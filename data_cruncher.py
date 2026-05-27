"""
Data Cruncher tab — fleet-wide histogram analysis and outlier detection.

Architecture:
  - A background CacheBuilder thread reads every downloaded Excel file once,
    computes per-module metrics, and saves them to _metrics_cache.csv.
  - The UI reads only that small CSV — no Excel files touched at crunch time.
  - Cache invalidation: each row stores an mtime+config hash; unchanged
    files are skipped on subsequent rebuilds.
  - Date filtering is based on data timestamps inside the files (data_end
    column), not the file modification date.
  - Outlier detection uses a user-chosen percentile threshold rather than
    standard deviation, which handles skewed / zero-inflated distributions
    (e.g. defrost failure rate = 0% for 90% of cases).
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor


# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------
_APP_DIR     = Path.home() / "OneC"
DATA_DIR     = _APP_DIR / "downloads"
CACHE_FILE   = DATA_DIR / "_metrics_cache.csv"
CATALOG_FILE = DATA_DIR / "_catalog.json"
CONFIG_FILE  = _APP_DIR / "case_config.json"

_DEFAULT_CFG = {
    "setpoint": -10.0,
    "defrost_terminate_threshold": 35.0,
}


# ---------------------------------------------------------------------------
#  Metric registry  { column: (display_label, unit) }
# ---------------------------------------------------------------------------
METRICS = {
    "avg_defrost_min":            ("Avg Defrost Duration",          "min"),
    "max_defrost_min":            ("Max Defrost Duration",          "min"),
    "defrost_failed_pct":         ("Defrost Failure Rate",          "%"),
    "avg_hours_between_defrosts": ("Avg Time Between Defrosts",     "hrs"),
    "setpoint_deviation":         ("Setpoint Deviation (Temp - SP)", "°F"),
    "temp_stability_std":         ("Temp Stability (Std Dev)",      "°F"),
    "high_temp_hours":            ("Hours Above Setpoint + 8°F",    "hrs"),
    "post_defrost_floor_drift":   ("Post-Defrost Floor Drift",      "°F"),
}


# ---------------------------------------------------------------------------
#  Shared widget styles
# ---------------------------------------------------------------------------
_BTN = (
    "QPushButton{background:#2a2a4e;color:#e0e0e0;"
    "border:1px solid #42A5F5;border-radius:4px;padding:5px 12px;}"
    "QPushButton:hover{background:#3a3a6e;}"
    "QPushButton:disabled{background:#1a1a2e;color:#555;border-color:#333;}"
)
_COMBO = (
    "QComboBox{background:#16213e;color:#e0e0e0;"
    "border:1px solid #555;border-radius:3px;padding:3px 6px;}"
    "QComboBox QAbstractItemView{background:#16213e;color:#e0e0e0;"
    "selection-background-color:#42A5F5;}"
)
_SPIN = (
    "QSpinBox{background:#16213e;color:#e0e0e0;"
    "border:1px solid #555;border-radius:3px;padding:3px;}"
)


# ---------------------------------------------------------------------------
#  Per-module metrics computation
# ---------------------------------------------------------------------------
def _compute_module_metrics(
        case_id: str, module_name: str,
        df_d: pd.DataFrame, df_e: pd.DataFrame,
        cfg: dict, cat: dict) -> dict:
    """
    Compute all crunchable metrics for one module.
    Imports detect_defrost_periods from diagnostics (lazy, avoids Qt init).
    """
    # Lazy import — diagnostics.py does not import Qt at module level
    from diagnostics import detect_defrost_periods

    threshold    = cfg.get("defrost_terminate_threshold", 35.0)
    setpoint_cfg = cfg.get("setpoint", -10.0)

    row = {
        "case_id":      case_id,
        "module_name":  module_name,
        "model":        cat.get("model", ""),
        "inventory_type": cat.get("inventory_type", ""),
        "store":        cat.get("store", ""),
        "store_name":   cat.get("store_name", ""),
        "state":        cat.get("state", ""),
        "data_start":   None,
        "data_end":     None,
        # Defrost
        "defrost_count":          0,
        "defrost_failed_count":   0,
        "defrost_failed_pct":     0.0,
        "avg_defrost_min":        None,
        "max_defrost_min":        None,
        "avg_hours_between_defrosts": None,
        # Temperature
        "setpoint_deviation":       None,
        "temp_stability_std":       None,
        "high_temp_hours":          0.0,
        "post_defrost_floor_drift": 0.0,
        "max_temp":    None,
        "min_temp":    None,
        "mean_temp":   None,
        # Internal
        "cache_key":   "",
    }

    # ── Defrost periods ───────────────────────────────────────────────
    periods = detect_defrost_periods(df_d, df_e, threshold)
    row["defrost_count"] = len(periods)

    if periods:
        bad = [p for p in periods if not p["terminated_ok"]]
        row["defrost_failed_count"] = len(bad)
        row["defrost_failed_pct"]   = len(bad) / len(periods) * 100

        durations = [p["duration_min"] for p in periods]
        row["avg_defrost_min"] = sum(durations) / len(durations)
        row["max_defrost_min"] = max(durations)

        # Average time between defrost starts
        if len(periods) >= 2:
            starts = [p["start"] for p in periods]
            gaps = [
                (starts[i + 1] - starts[i]).total_seconds() / 3600
                for i in range(len(starts) - 1)
            ]
            row["avg_hours_between_defrosts"] = sum(gaps) / len(gaps)

    # ── Temperature metrics ───────────────────────────────────────────
    if df_d is not None and not df_d.empty and "Control Temperature" in df_d.columns:
        df_s = (df_d.sort_values("timestamp")
                    .dropna(subset=["Control Temperature"])
                    .copy())
        if not df_s.empty:
            row["data_start"] = df_s["timestamp"].min().isoformat()
            row["data_end"]   = df_s["timestamp"].max().isoformat()
            row["max_temp"]   = float(df_s["Control Temperature"].max())
            row["min_temp"]   = float(df_s["Control Temperature"].min())
            row["mean_temp"]  = float(df_s["Control Temperature"].mean())
            row["temp_stability_std"] = float(df_s["Control Temperature"].std())

            # Use actual setpoint from data when available (beats config)
            setpoint_use = setpoint_cfg
            if df_e is not None and not df_e.empty and "Setpoint" in df_e.columns:
                sp = df_e["Setpoint"].dropna()
                if not sp.empty:
                    setpoint_use = float(sp.median())

            row["setpoint_deviation"] = row["mean_temp"] - setpoint_use

            # Hours temp was above setpoint + 8°F
            high = df_s[df_s["Control Temperature"] > setpoint_use + 8]
            if len(high) > 1:
                row["high_temp_hours"] = float(
                    (high["timestamp"].max() - high["timestamp"].min())
                    .total_seconds() / 3600
                )

            # Post-defrost floor drift (first vs last post-defrost low)
            floors = []
            for p in periods:
                w_end = p["end"] + pd.Timedelta(minutes=45)
                seg = df_s[
                    (df_s["timestamp"] >= p["end"]) &
                    (df_s["timestamp"] <= w_end)
                ]
                if not seg.empty:
                    floors.append(float(seg["Control Temperature"].min()))
            if len(floors) >= 2:
                row["post_defrost_floor_drift"] = floors[-1] - floors[0]

    elif df_d is not None and not df_d.empty and "timestamp" in df_d.columns:
        # No Control Temp column but we can still record date range
        row["data_start"] = df_d["timestamp"].min().isoformat()
        row["data_end"]   = df_d["timestamp"].max().isoformat()

    return row


# ---------------------------------------------------------------------------
#  Cache builder — runs in a background QThread
# ---------------------------------------------------------------------------
class CacheBuilder(QThread):
    msg      = pyqtSignal(str)       # status line for the progress label
    progress = pyqtSignal(int, int)  # (current_file, total_files)
    done     = pyqtSignal(int)       # total module rows saved

    def run(self):
        try:
            self._build()
        except Exception as e:
            self.msg.emit(f"Cache build error: {e}")
            self.done.emit(0)

    def _build(self):
        # Load catalog (model / store metadata)
        catalog: dict = {}
        if CATALOG_FILE.exists():
            try:
                catalog = json.loads(CATALOG_FILE.read_text())
            except Exception:
                pass

        # Load per-case configs (setpoint / threshold)
        case_cfgs: dict = {}
        if CONFIG_FILE.exists():
            try:
                case_cfgs = json.loads(CONFIG_FILE.read_text()).get("cases", {})
            except Exception:
                pass

        # Load existing cache so we can skip unchanged files
        existing: dict = {}   # (case_id, module_name) → row dict
        if CACHE_FILE.exists():
            try:
                df_old = pd.read_csv(CACHE_FILE)
                for _, r in df_old.iterrows():
                    existing[(r["case_id"], r["module_name"])] = r.to_dict()
            except Exception:
                pass

        # Discover Excel files
        xlsx_files = sorted(
            f for f in DATA_DIR.glob("*.xlsx")
            if not f.name.startswith("~") and not f.name.startswith("_")
        )
        total = len(xlsx_files)
        all_rows: list = []

        for idx, xlsx_path in enumerate(xlsx_files):
            case_id = xlsx_path.stem
            self.progress.emit(idx + 1, total)

            cfg      = case_cfgs.get(case_id, _DEFAULT_CFG.copy())
            mtime    = xlsx_path.stat().st_mtime
            cfg_hash = hashlib.md5(
                json.dumps(cfg, sort_keys=True).encode()
            ).hexdigest()[:8]
            cache_key = f"{mtime:.0f}_{cfg_hash}"
            cat       = catalog.get(case_id, {})

            try:
                xl = pd.ExcelFile(xlsx_path)

                # Parse sheets into {module: {sensor_data, sensor_event}}
                module_data: dict = {}
                for sheet in xl.sheet_names:
                    if sheet in ("Store_ambient",) or sheet.startswith("_"):
                        continue
                    df = pd.read_excel(xlsx_path, sheet_name=sheet)
                    if "timestamp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                        df = df.sort_values("timestamp").reset_index(drop=True)
                    if "_sensor-data" in sheet:
                        mod = sheet.split("_sensor-data")[0]
                        module_data.setdefault(mod, {})["sensor_data"] = df
                    elif "_sensor-event" in sheet:
                        mod = sheet.split("_sensor-event")[0]
                        module_data.setdefault(mod, {})["sensor_event"] = df

                for mod_name, data in module_data.items():
                    key    = (case_id, mod_name)
                    cached = existing.get(key)

                    # Re-use the cached row if nothing changed
                    if cached and str(cached.get("cache_key", "")) == cache_key:
                        all_rows.append(cached)
                        continue

                    self.msg.emit(f"  {case_id} / {mod_name}")
                    df_d = data.get("sensor_data",  pd.DataFrame())
                    df_e = data.get("sensor_event", pd.DataFrame())
                    row  = _compute_module_metrics(
                        case_id, mod_name, df_d, df_e, cfg, cat)
                    row["cache_key"] = cache_key
                    all_rows.append(row)

            except Exception as e:
                self.msg.emit(f"  Skip {case_id}: {e}")

        if all_rows:
            pd.DataFrame(all_rows).to_csv(CACHE_FILE, index=False)
            self.msg.emit(f"Done — {len(all_rows)} module entries cached.")
        else:
            self.msg.emit("No Excel files found in downloads folder.")
        self.done.emit(len(all_rows))


# ---------------------------------------------------------------------------
#  Histogram chart builder
# ---------------------------------------------------------------------------
def build_histogram_html(
        df: pd.DataFrame, metric_col: str,
        metric_label: str, metric_unit: str,
        outlier_pct: float) -> str:

    vals = df[metric_col].dropna().astype(float)
    if len(vals) < 2:
        return (
            "<html><body style='background:#1a1a2e;color:#888;"
            "display:flex;align-items:center;justify-content:center;height:80vh'>"
            f"<p style='text-align:center'>Not enough data for "
            f"<b>{metric_label}</b><br>"
            f"({len(vals)} non-null value(s) — need at least 2)</p>"
            "</body></html>"
        )

    median   = float(vals.median())
    q1       = float(vals.quantile(0.25))
    q3       = float(vals.quantile(0.75))
    iqr      = q3 - q1
    fence_lo = q1 - 1.5 * iqr
    fence_hi = q3 + 1.5 * iqr
    pct_thr  = float(vals.quantile(1 - outlier_pct / 100))

    # Split into normal vs outlier series so we can colour them differently
    normal_vals  = vals[vals <= pct_thr]
    outlier_vals = vals[vals >  pct_thr]

    val_range  = float(vals.max() - vals.min()) or 1.0
    bin_size   = val_range / 40

    fig = go.Figure()

    if not normal_vals.empty:
        fig.add_trace(go.Histogram(
            x=normal_vals, name="Normal range",
            marker_color="#42A5F5", opacity=0.80,
            xbins=dict(size=bin_size),
            hovertemplate="Value: %{x:.2f}<br>Count: %{y}<extra>Normal</extra>",
        ))

    if not outlier_vals.empty:
        fig.add_trace(go.Histogram(
            x=outlier_vals, name=f"Top {outlier_pct:.0f}% (outliers)",
            marker_color="#EF5350", opacity=0.85,
            xbins=dict(size=bin_size),
            hovertemplate="Value: %{x:.2f}<br>Count: %{y}<extra>Outlier</extra>",
        ))

    fig.update_layout(barmode="overlay")

    # Median line
    fig.add_vline(
        x=median, line_width=2, line_color="#66BB6A", line_dash="solid",
        annotation_text=f"Median  {median:.2f}",
        annotation_font_color="#66BB6A",
        annotation_position="top right",
    )

    # IQR fence (upper)
    fig.add_vline(
        x=fence_hi, line_width=1.5, line_color="#FFA726", line_dash="dash",
        annotation_text=f"IQR fence  {fence_hi:.2f}",
        annotation_font_color="#FFA726",
        annotation_position="top left",
    )

    # IQR fence (lower) — only if meaningful
    if fence_lo > float(vals.min()):
        fig.add_vline(
            x=fence_lo, line_width=1.5,
            line_color="#FFA726", line_dash="dash",
        )

    n_outliers = len(outlier_vals)
    fig.update_layout(
        title=dict(
            text=(
                f"<b>{metric_label}</b>   "
                f"n={len(vals)} modules   "
                f"{n_outliers} outlier(s) in top {outlier_pct:.0f}%"
            ),
            font=dict(size=14, color="#e0e0e0"),
        ),
        xaxis_title=f"{metric_label} ({metric_unit})",
        yaxis_title="Modules",
        height=360,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0", family="Segoe UI, Arial"),
        legend=dict(
            bgcolor="rgba(0,0,0,0.35)",
            bordercolor="rgba(255,255,255,0.12)",
            borderwidth=1,
            orientation="h", yanchor="bottom", y=1.02, x=0,
        ),
        margin=dict(l=65, r=40, t=75, b=55),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.07)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.07)")

    html = fig.to_html(include_plotlyjs="cdn", full_html=True)

    # Polyfill for PyQtWebEngine CSS insertRule crash
    polyfill = (
        "<script>\n"
        "var _o=CSSStyleSheet.prototype.insertRule;\n"
        "CSSStyleSheet.prototype.insertRule=function(r,i){"
        "try{return _o.call(this,r,i||0);}catch(e){return -1;}};\n"
        "</script>"
    )
    return html.replace("<head>", f"<head>\n{polyfill}")


# ---------------------------------------------------------------------------
#  Main widget
# ---------------------------------------------------------------------------
class DataCruncherWidget(QWidget):

    def __init__(self):
        super().__init__()
        self._cache_df: Optional[pd.DataFrame] = None
        self._builder: Optional[CacheBuilder]  = None
        self._init_ui()
        self._load_cache(silent=True)

    # ── UI construction ───────────────────────────────────────────────

    def _init_ui(self):
        self.setStyleSheet("background:#1a1a2e; color:#e0e0e0;")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(7)

        # ── Top bar (status + rebuild button) ─────────────────────────
        top = QHBoxLayout()
        self.status_lbl = QLabel("No cache loaded — click Rebuild Cache to start")
        self.status_lbl.setStyleSheet("color:#aaa; font-size:11px;")
        top.addWidget(self.status_lbl, 1)

        self.rebuild_btn = QPushButton("↻  Rebuild Cache")
        self.rebuild_btn.setStyleSheet(_BTN)
        self.rebuild_btn.clicked.connect(self._rebuild)
        top.addWidget(self.rebuild_btn)
        root.addLayout(top)

        # Progress bar + label (hidden when idle)
        self.prog_bar = QProgressBar()
        self.prog_bar.setMaximumHeight(5)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setVisible(False)
        self.prog_bar.setStyleSheet(
            "QProgressBar{background:#16213e;border:none;}"
            "QProgressBar::chunk{background:#42A5F5;}"
        )
        root.addWidget(self.prog_bar)

        self.prog_lbl = QLabel("")
        self.prog_lbl.setStyleSheet("color:#666; font-size:10px;")
        self.prog_lbl.setVisible(False)
        root.addWidget(self.prog_lbl)

        # Divider
        div = QLabel()
        div.setFixedHeight(1)
        div.setStyleSheet("background:#333;")
        root.addWidget(div)

        # ── Filter row ────────────────────────────────────────────────
        fil = QHBoxLayout()
        fil.setSpacing(10)

        fil.addWidget(self._lbl("Model:"))
        self.model_cb = QComboBox()
        self.model_cb.setMinimumWidth(115)
        self.model_cb.setStyleSheet(_COMBO)
        self.model_cb.addItem("All models")
        fil.addWidget(self.model_cb)

        fil.addWidget(self._lbl("State:"))
        self.state_cb = QComboBox()
        self.state_cb.setMinimumWidth(85)
        self.state_cb.setStyleSheet(_COMBO)
        self.state_cb.addItem("All states")
        fil.addWidget(self.state_cb)

        fil.addWidget(self._lbl("Data newer than:"))
        self.days_cb = QComboBox()
        self.days_cb.addItems(["30 days", "60 days", "90 days", "All time"])
        self.days_cb.setCurrentIndex(2)
        self.days_cb.setStyleSheet(_COMBO)
        fil.addWidget(self.days_cb)

        fil.addStretch()
        root.addLayout(fil)

        # ── Metric + crunch row ───────────────────────────────────────
        met = QHBoxLayout()
        met.setSpacing(10)

        met.addWidget(self._lbl("Metric:"))
        self.metric_cb = QComboBox()
        self.metric_cb.setMinimumWidth(280)
        self.metric_cb.setStyleSheet(_COMBO)
        for col, (label, unit) in METRICS.items():
            self.metric_cb.addItem(f"{label}  ({unit})", userData=col)
        met.addWidget(self.metric_cb)

        met.addWidget(self._lbl("Show worst:"))
        self.pct_spin = QSpinBox()
        self.pct_spin.setRange(1, 50)
        self.pct_spin.setValue(10)
        self.pct_spin.setSuffix(" %")
        self.pct_spin.setStyleSheet(_SPIN)
        met.addWidget(self.pct_spin)

        crunch_btn = QPushButton("⚡  Crunch!")
        crunch_btn.setStyleSheet(
            "QPushButton{background:#1a3a1a;color:#e0e0e0;"
            "border:1px solid #66BB6A;border-radius:4px;"
            "padding:5px 18px;font-weight:bold;}"
            "QPushButton:hover{background:#2a5a2a;}"
        )
        crunch_btn.clicked.connect(self._crunch)
        met.addWidget(crunch_btn)
        met.addStretch()
        root.addLayout(met)

        # ── Histogram chart ───────────────────────────────────────────
        if HAS_WEBENGINE:
            self.chart_view = QWebEngineView()
            self.chart_view.setMinimumHeight(330)
            self._chart_placeholder()
            root.addWidget(self.chart_view)
        else:
            self.chart_view = None
            root.addWidget(QLabel(
                "PyQtWebEngine not installed — pip install PyQtWebEngine"
            ))

        # ── Outlier table ─────────────────────────────────────────────
        otitle = QLabel("Outliers")
        otitle.setStyleSheet(
            "font-weight:bold; color:#42A5F5; font-size:11px; margin-top:4px;"
        )
        root.addWidget(otitle)

        self.out_table = QTableWidget()
        self.out_table.setColumnCount(7)
        self.out_table.setHorizontalHeaderLabels(
            ["Case ID", "Module", "Model", "Store", "State", "Value", "vs Median"]
        )
        self.out_table.setMaximumHeight(185)
        self.out_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.out_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.out_table.setAlternatingRowColors(True)
        self.out_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.out_table.setStyleSheet(
            "QTableWidget{background:#16213e;alternate-background-color:#1e2640;"
            "color:#e0e0e0;gridline-color:#333;border:1px solid #333;}"
            "QHeaderView::section{background:#1a1a2e;color:#42A5F5;"
            "border:1px solid #333;padding:4px;}"
            "QTableWidget::item:selected{background:#42A5F5;color:#000;}"
        )
        root.addWidget(self.out_table)

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#ccc;")
        return lbl

    def _chart_placeholder(self):
        if self.chart_view:
            self.chart_view.setHtml(
                "<html><body style='background:#1a1a2e;color:#555;"
                "display:flex;align-items:center;justify-content:center;height:100vh'>"
                "<p style='font-size:16px;text-align:center'>"
                "Build the cache, then click ⚡ Crunch<br>"
                "<span style='font-size:12px;color:#444'>"
                "Use ↻ Rebuild Cache to scan your downloads folder</span>"
                "</p></body></html>"
            )

    # ── Cache management ──────────────────────────────────────────────

    def _load_cache(self, silent=False):
        """Load (or reload) the metrics CSV into self._cache_df."""
        if CACHE_FILE.exists():
            try:
                self._cache_df = pd.read_csv(CACHE_FILE)
                n      = len(self._cache_df)
                cases  = self._cache_df["case_id"].nunique()
                age    = _file_age(CACHE_FILE)
                self.status_lbl.setText(
                    f"Cache ready — {cases} cases, {n} modules  |  Updated: {age}"
                )
                self._populate_filters()
            except Exception as e:
                if not silent:
                    QMessageBox.warning(self, "Cache load error", str(e))
                self.status_lbl.setText("Cache could not be loaded")
        else:
            self._cache_df = None
            self.status_lbl.setText(
                "No cache yet — download some cases, then click ↻ Rebuild Cache"
            )

    def _populate_filters(self):
        """Fill model and state dropdowns from the cache."""
        if self._cache_df is None:
            return
        df = self._cache_df

        prev_model = self.model_cb.currentText()
        prev_state = self.state_cb.currentText()

        self.model_cb.clear()
        self.model_cb.addItem("All models")
        for m in sorted(df["model"].dropna().unique()):
            if str(m).strip():
                self.model_cb.addItem(str(m))

        self.state_cb.clear()
        self.state_cb.addItem("All states")
        for s in sorted(df["state"].dropna().unique()):
            if str(s).strip():
                self.state_cb.addItem(str(s))

        # Restore previous selection if still valid
        if self.model_cb.findText(prev_model) >= 0:
            self.model_cb.setCurrentText(prev_model)
        if self.state_cb.findText(prev_state) >= 0:
            self.state_cb.setCurrentText(prev_state)

    def _rebuild(self):
        """Start the background cache builder."""
        if self._builder and self._builder.isRunning():
            return
        self.rebuild_btn.setEnabled(False)
        self.prog_bar.setRange(0, 0)   # indeterminate spinner until first progress signal
        self.prog_bar.setVisible(True)
        self.prog_lbl.setText("Starting…")
        self.prog_lbl.setVisible(True)
        self.status_lbl.setText("Building cache…")

        self._builder = CacheBuilder()
        self._builder.msg.connect(self.prog_lbl.setText)
        self._builder.progress.connect(self._on_progress)
        self._builder.done.connect(self._on_done)
        self._builder.start()

    def _on_progress(self, cur: int, total: int):
        if self.prog_bar.maximum() == 0:
            self.prog_bar.setRange(0, total)
        self.prog_bar.setValue(cur)
        self.status_lbl.setText(f"Building cache — {cur} / {total} files…")

    def _on_done(self, n: int):
        self.prog_bar.setVisible(False)
        self.prog_lbl.setVisible(False)
        self.rebuild_btn.setEnabled(True)
        self._load_cache()

    # ── Crunch ────────────────────────────────────────────────────────

    def _crunch(self):
        """Apply filters, build histogram, populate outlier table."""
        if self._cache_df is None or self._cache_df.empty:
            QMessageBox.information(
                self, "No cache",
                "Click ↻ Rebuild Cache first to scan your downloaded files."
            )
            return

        df = self._cache_df.copy()

        # ── Date filter (based on data timestamps, not file mtime) ────
        days_txt = self.days_cb.currentText()
        if days_txt != "All time":
            n_days = int(days_txt.split()[0])
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=n_days)
            df["_data_end_ts"] = pd.to_datetime(df["data_end"], errors="coerce")
            df = df[df["_data_end_ts"] >= cutoff]

        # ── Model filter ──────────────────────────────────────────────
        if self.model_cb.currentText() != "All models":
            df = df[df["model"] == self.model_cb.currentText()]

        # ── State filter ──────────────────────────────────────────────
        if self.state_cb.currentText() != "All states":
            df = df[df["state"] == self.state_cb.currentText()]

        if df.empty:
            QMessageBox.information(
                self, "No data", "No modules match the current filters."
            )
            return

        # ── Metric ────────────────────────────────────────────────────
        col   = self.metric_cb.currentData()
        label, unit = METRICS[col]
        outlier_pct = float(self.pct_spin.value())

        # ── Chart ─────────────────────────────────────────────────────
        html = build_histogram_html(df, col, label, unit, outlier_pct)
        if self.chart_view:
            self.chart_view.setHtml(html)

        # ── Outlier table ─────────────────────────────────────────────
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if vals.empty:
            self.out_table.setRowCount(0)
            return

        median_val = float(vals.median())
        pct_thr    = float(vals.quantile(1 - outlier_pct / 100))

        outliers = df[pd.to_numeric(df[col], errors="coerce") > pct_thr].copy()
        outliers = outliers.sort_values(col, ascending=False)

        self.out_table.setRowCount(len(outliers))
        for i, (_, r) in enumerate(outliers.iterrows()):
            raw  = pd.to_numeric(r.get(col, None), errors="coerce")
            val  = float(raw) if pd.notna(raw) else 0.0
            diff = val - median_val

            sign = "+" if diff >= 0 else ""
            cells = [
                str(r.get("case_id",     "")),
                str(r.get("module_name", "")),
                str(r.get("model",       "")),
                str(r.get("store",       "")),
                str(r.get("state",       "")),
                f"{val:.2f} {unit}",
                f"{sign}{diff:.2f}",
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                # Colour the "vs Median" column red to draw the eye
                if j == 6:
                    item.setForeground(QColor("#EF5350"))
                self.out_table.setItem(i, j, item)


# ---------------------------------------------------------------------------
#  Utility
# ---------------------------------------------------------------------------
def _file_age(path: Path) -> str:
    """Return a human-readable age string for a file."""
    secs = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
    if secs < 120:    return "just now"
    if secs < 3600:   return f"{int(secs / 60)} min ago"
    if secs < 86400:  return f"{int(secs / 3600)} hr ago"
    return f"{int(secs / 86400)} days ago"
