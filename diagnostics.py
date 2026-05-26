"""
Diagnostics tab - interactive charts and automated anomaly detection
"""

import json
import re
import tempfile
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QDialog, QFormLayout,
    QDoubleSpinBox, QSpinBox, QComboBox, QDialogButtonBox, QTextEdit
)
from PyQt5.QtCore import Qt

DATA_DIR = Path("C:\\Users\\silam\\OneC\\downloads")
CONFIG_FILE = Path("C:\\Users\\silam\\OneC\\case_config.json")

DEFAULT_CONFIGS = {
    "LT": {
        "case_type": "LT",
        "setpoint": -10.0,
        "defrost_terminate_threshold": 35.0,
        "defrost_frequency": 4,
        "sync_tolerance_min": 30,
        "door_rise_per_15min": 5.0,
    },
    "MT": {
        "case_type": "MT",
        "setpoint": 28.0,
        "defrost_terminate_threshold": 40.0,
        "defrost_frequency": 2,
        "sync_tolerance_min": 30,
        "door_rise_per_15min": 8.0,
    },
}


# ---------------------------------------------------------------------------
#  Config storage
# ---------------------------------------------------------------------------

class CaseConfig:
    def __init__(self):
        self.data: dict = {"cases": {}}
        self._load()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                self.data = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass

    def save(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.data, indent=2))

    def get(self, case_id: str) -> dict:
        return self.data.get("cases", {}).get(case_id, DEFAULT_CONFIGS["LT"]).copy()

    def set(self, case_id: str, cfg: dict):
        self.data.setdefault("cases", {})[case_id] = cfg
        self.save()


# ---------------------------------------------------------------------------
#  Data loading
# ---------------------------------------------------------------------------

def get_downloaded_cases() -> List[str]:
    if not DATA_DIR.exists():
        return []
    cases = set()
    for f in DATA_DIR.glob("*.xlsx"):
        if not f.name.startswith("~"):
            m = re.match(r"^([A-Z0-9]+)_\d{8}_\d{6}\.xlsx$", f.name)
            if m:
                cases.add(m.group(1))
    return sorted(cases)


def load_case_data(case_id: str) -> Optional[Dict]:
    """Returns {module_name: {sensor_data: df, sensor_event: df}}"""
    files = sorted(DATA_DIR.glob(f"{case_id}_*.xlsx"), reverse=True)
    if not files:
        return None
    try:
        xl = pd.ExcelFile(files[0])
        modules: Dict = {}
        for sheet in xl.sheet_names:
            df = pd.read_excel(files[0], sheet_name=sheet)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp").reset_index(drop=True)
            if "_sensor-data" in sheet:
                mod = sheet.split("_sensor-data")[0]
                modules.setdefault(mod, {})["sensor_data"] = df
            elif "_sensor-event" in sheet:
                mod = sheet.split("_sensor-event")[0]
                modules.setdefault(mod, {})["sensor_event"] = df
        return modules or None
    except Exception as e:
        print(f"Error loading {case_id}: {e}")
        return None


# ---------------------------------------------------------------------------
#  Detection helpers
# ---------------------------------------------------------------------------

def detect_defrost_periods(df: pd.DataFrame, threshold: float) -> List[dict]:
    if df is None or df.empty or "Defrost Status" not in df.columns:
        return []
    periods, in_def, start = [], False, None
    for _, row in df.iterrows():
        val = row.get("Defrost Status", 0)
        is_def = pd.notna(val) and float(val) > 0
        if is_def and not in_def:
            in_def, start = True, row["timestamp"]
        elif not is_def and in_def:
            end = row["timestamp"]
            in_def = False
            mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
            seg = df[mask]
            max_t = seg["Defrost Terminate"].max() if "Defrost Terminate" in seg.columns else None
            max_t_f = float(max_t) if max_t is not None and pd.notna(max_t) else None
            periods.append({
                "start": start, "end": end,
                "max_terminate": max_t_f,
                "terminated_ok": (max_t_f is not None and max_t_f >= threshold),
                "duration_min": (end - start).total_seconds() / 60,
            })
    return periods


def detect_compressor_runs(df_event: pd.DataFrame) -> List[dict]:
    if df_event is None or df_event.empty or "Refrigeration DO" not in df_event.columns:
        return []
    df = df_event.dropna(subset=["Refrigeration DO"]).copy()
    periods, state, start = [], None, None
    for _, row in df.iterrows():
        on = bool(row["Refrigeration DO"])
        if state is None:
            state, start = on, row["timestamp"]
        elif on != state:
            periods.append({"start": start, "end": row["timestamp"], "on": state})
            state, start = on, row["timestamp"]
    if state is not None and len(df):
        periods.append({"start": start, "end": df["timestamp"].iloc[-1], "on": state})
    return periods


def get_alarm_times(df_event: pd.DataFrame) -> list:
    if df_event is None or df_event.empty or "Alarm" not in df_event.columns:
        return []
    return list(df_event.loc[df_event["Alarm"] == True, "timestamp"])


# ---------------------------------------------------------------------------
#  Diagnostics engine
# ---------------------------------------------------------------------------

def run_diagnostics(modules: Dict, config: dict) -> List[dict]:
    findings = []
    threshold = config.get("defrost_terminate_threshold", 35.0)
    sync_tol  = config.get("sync_tolerance_min", 30)
    setpoint  = config.get("setpoint", -10.0)
    door_rate = config.get("door_rise_per_15min", 5.0)
    mod_names = list(modules.keys())

    for mod_name, data in modules.items():
        df_d = data.get("sensor_data", pd.DataFrame())
        df_e = data.get("sensor_event", pd.DataFrame())

        # ── Defrost analysis ──────────────────────────────────────────
        periods = detect_defrost_periods(df_d, threshold)
        if periods:
            bad = [p for p in periods if not p["terminated_ok"]]
            if bad:
                findings.append({
                    "level": "critical", "type": "icing", "module": mod_name,
                    "msg": (f"🧊 [{mod_name}] {len(bad)}/{len(periods)} defrosts "
                            f"did NOT reach {threshold}°F — likely icing in coil"),
                })

            # Post-defrost temperature floor trend
            floors = []
            for p in periods:
                w_end = p["end"] + pd.Timedelta(minutes=45)
                seg = df_d[(df_d["timestamp"] >= p["end"]) & (df_d["timestamp"] <= w_end)]
                if not seg.empty and "Control Temperature" in seg.columns:
                    floors.append(seg["Control Temperature"].min())
            if len(floors) >= 2:
                drift = floors[-1] - floors[0]
                if drift > 3.0:
                    findings.append({
                        "level": "warning", "type": "icing_early", "module": mod_name,
                        "msg": (f"📈 [{mod_name}] Post-defrost temp floor rising "
                                f"+{drift:.1f}°F — early icing sign"),
                    })

        # ── Door open / air leak ─────────────────────────────────────
        if not df_d.empty and "Control Temperature" in df_d.columns:
            df_s = df_d.sort_values("timestamp").copy()
            df_s["block"] = ((df_s["timestamp"] - df_s["timestamp"].iloc[0])
                             .dt.total_seconds() // 900).astype(int)
            block_avg  = df_s.groupby("block")["Control Temperature"].mean()
            max_rise   = block_avg.diff().max()

            if pd.notna(max_rise) and max_rise >= door_rate:
                findings.append({
                    "level": "critical", "type": "door_open", "module": mod_name,
                    "msg": (f"🚪 [{mod_name}] Temp rose {max_rise:.1f}°F in 15 min "
                            f"— possible door left open"),
                })
            else:
                high = df_s[df_s["Control Temperature"] > setpoint + 8]
                if len(high) > 3:
                    hrs = (high["timestamp"].max() - high["timestamp"].min()).total_seconds() / 3600
                    if hrs >= 0.5:
                        findings.append({
                            "level": "warning", "type": "air_leak", "module": mod_name,
                            "msg": (f"💨 [{mod_name}] Temp above setpoint+8°F for "
                                    f"{hrs:.1f}h — possible air leak"),
                        })

        # ── Alarms ───────────────────────────────────────────────────
        alarms = get_alarm_times(df_e)
        if alarms:
            findings.append({
                "level": "critical", "type": "alarm", "module": mod_name,
                "msg": f"🚨 [{mod_name}] {len(alarms)} alarm event(s) in this period",
            })

    # ── Defrost sync (only with 2 modules) ───────────────────────────
    if len(mod_names) == 2:
        a, b = mod_names
        pa = detect_defrost_periods(modules[a].get("sensor_data", pd.DataFrame()), threshold)
        pb = detect_defrost_periods(modules[b].get("sensor_data", pd.DataFrame()), threshold)
        if pa and pb:
            starts_b = [p["start"] for p in pb]
            max_gap = 0.0
            for sa in [p["start"] for p in pa]:
                closest = min(abs((sa - sb).total_seconds() / 60) for sb in starts_b)
                max_gap = max(max_gap, closest)
            if max_gap > sync_tol:
                findings.append({
                    "level": "critical", "type": "sync", "module": "both",
                    "msg": (f"⏱ [{a} vs {b}] Defrosts OUT OF SYNC — "
                            f"up to {max_gap:.0f} min apart (limit: {sync_tol} min)"),
                })

    if not findings:
        findings.append({"level": "ok", "type": "ok", "module": "all",
                         "msg": "✅ No anomalies detected in this time window"})
    return findings


# ---------------------------------------------------------------------------
#  Chart builder
# ---------------------------------------------------------------------------

def build_chart_html(case_id: str, modules: Dict, config: dict) -> str:
    setpoint  = config.get("setpoint", -10.0)
    threshold = config.get("defrost_terminate_threshold", 35.0)
    case_type = config.get("case_type", "LT")
    mod_names = list(modules.keys())
    n = len(mod_names)
    if n == 0:
        return "<html><body style='background:#1a1a2e;color:#aaa'><p>No data</p></body></html>"

    specs = [[{"secondary_y": True}]] * n
    fig = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=mod_names,
        specs=specs,
    )

    sp_shown = thr_shown = False

    for ri, mod_name in enumerate(mod_names, 1):
        data  = modules[mod_name]
        df_d  = data.get("sensor_data",  pd.DataFrame())
        df_e  = data.get("sensor_event", pd.DataFrame())

        # Compressor background
        for cp in detect_compressor_runs(df_e):
            if cp["on"]:
                fig.add_vrect(x0=cp["start"], x1=cp["end"],
                              fillcolor="rgba(100,200,100,0.07)",
                              line_width=0, row=ri, col=1)

        # Defrost shading
        for p in detect_defrost_periods(df_d, threshold):
            color = "rgba(255,210,50,0.20)" if p["terminated_ok"] else "rgba(255,70,50,0.25)"
            fig.add_vrect(x0=p["start"], x1=p["end"],
                          fillcolor=color, line_width=0, row=ri, col=1)

        if not df_d.empty:
            ts = df_d["timestamp"]

            # Reference lines as scatter traces
            fig.add_trace(go.Scatter(
                x=[ts.min(), ts.max()], y=[setpoint, setpoint],
                name="Setpoint", mode="lines",
                line=dict(color="rgba(180,180,180,0.55)", dash="dash", width=1.5),
                showlegend=not sp_shown, legendgroup="sp", hoverinfo="skip",
            ), row=ri, col=1, secondary_y=False)
            sp_shown = True

            fig.add_trace(go.Scatter(
                x=[ts.min(), ts.max()], y=[threshold, threshold],
                name=f"Defrost threshold ({threshold}°F)", mode="lines",
                line=dict(color="rgba(255,160,0,0.55)", dash="dot", width=1.5),
                showlegend=not thr_shown, legendgroup="thr", hoverinfo="skip",
            ), row=ri, col=1, secondary_y=False)
            thr_shown = True

            # Control Temperature
            if "Control Temperature" in df_d.columns:
                fig.add_trace(go.Scatter(
                    x=ts, y=df_d["Control Temperature"],
                    name=f"{mod_name} — Control Temp", mode="lines",
                    line=dict(color="#42A5F5", width=2.5),
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Control Temp: %{y:.1f}°F<extra></extra>",
                ), row=ri, col=1, secondary_y=False)

            # Defrost Terminate
            if "Defrost Terminate" in df_d.columns:
                fig.add_trace(go.Scatter(
                    x=ts, y=df_d["Defrost Terminate"],
                    name=f"{mod_name} — Defrost Term.", mode="lines",
                    line=dict(color="#FFA726", width=1.5, dash="dot"), opacity=0.85,
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Defrost Term.: %{y:.1f}°F<extra></extra>",
                ), row=ri, col=1, secondary_y=False)

            # Compressor Discharge (secondary axis, hidden by default)
            if "Compressor Discharge Temp" in df_d.columns:
                fig.add_trace(go.Scatter(
                    x=ts, y=df_d["Compressor Discharge Temp"],
                    name=f"{mod_name} — Comp. Discharge", mode="lines",
                    line=dict(color="#EF5350", width=1.5, dash="longdash"),
                    opacity=0.75, visible="legendonly",
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Comp. Discharge: %{y:.1f}°F<extra></extra>",
                ), row=ri, col=1, secondary_y=True)

        # Alarm lines
        for alarm_ts in get_alarm_times(df_e):
            fig.add_vline(x=str(alarm_ts), line_color="red",
                          line_width=2, row=ri, col=1)

        fig.update_yaxes(title_text="Temp (°F)", row=ri, col=1, secondary_y=False,
                         showgrid=True, gridcolor="rgba(255,255,255,0.07)")
        fig.update_yaxes(title_text="Comp. Disch. (°F)", row=ri, col=1, secondary_y=True,
                         showgrid=False, range=[50, 250])

    fig.update_layout(
        title=dict(
            text=f"<b>{case_id}</b>  ·  {case_type}  ·  Setpoint: {setpoint}°F",
            font=dict(size=15, color="#e0e0e0"),
        ),
        height=max(440, n * 440),
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0", family="Segoe UI, Arial"),
        legend=dict(bgcolor="rgba(0,0,0,0.35)", bordercolor="rgba(255,255,255,0.15)",
                    borderwidth=1, font=dict(size=11)),
        margin=dict(l=70, r=90, t=70, b=50),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.07)",
                     tickformat="%m/%d\n%H:%M")

    return fig.to_html(include_plotlyjs="cdn", full_html=True)


# ---------------------------------------------------------------------------
#  Config dialog
# ---------------------------------------------------------------------------

class CaseConfigDialog(QDialog):
    def __init__(self, case_id: str, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configure  —  {case_id}")
        self.setFixedWidth(360)
        self.result_config = config.copy()
        self._build(config)

    def _build(self, cfg):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Case type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["LT", "MT"])
        self.type_combo.setCurrentText(cfg.get("case_type", "LT"))
        top.addWidget(self.type_combo)
        btn_def = QPushButton("Load defaults")
        btn_def.clicked.connect(lambda: self._apply_defaults(self.type_combo.currentText()))
        top.addWidget(btn_def)
        layout.addLayout(top)

        form = QFormLayout()

        def ds(val, lo, hi, suffix="°F"):
            w = QDoubleSpinBox()
            w.setRange(lo, hi); w.setDecimals(1)
            w.setSuffix(f" {suffix}"); w.setValue(float(val))
            return w

        def si(val, lo, hi, suffix=""):
            w = QSpinBox()
            w.setRange(lo, hi)
            if suffix: w.setSuffix(f" {suffix}")
            w.setValue(int(val))
            return w

        self.w_setpoint   = ds(cfg.get("setpoint", -10),                  -60,  60)
        self.w_threshold  = ds(cfg.get("defrost_terminate_threshold", 35),  20,  80)
        self.w_freq       = si(cfg.get("defrost_frequency", 4),              1,  24, "x/day")
        self.w_sync       = si(cfg.get("sync_tolerance_min", 30),            5, 120, "min")
        self.w_door       = ds(cfg.get("door_rise_per_15min", 5),            1,  30, "°F/15min")

        form.addRow("Setpoint:",                    self.w_setpoint)
        form.addRow("Defrost terminate ≥:",         self.w_threshold)
        form.addRow("Expected defrost frequency:",  self.w_freq)
        form.addRow("Sync tolerance:",              self.w_sync)
        form.addRow("Door open threshold:",         self.w_door)
        layout.addLayout(form)

        note = QLabel("One size doesn't fit all — adjust per store as needed.")
        note.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply_defaults(self, t: str):
        c = DEFAULT_CONFIGS.get(t, DEFAULT_CONFIGS["LT"])
        self.w_setpoint.setValue(c["setpoint"])
        self.w_threshold.setValue(c["defrost_terminate_threshold"])
        self.w_freq.setValue(c["defrost_frequency"])
        self.w_sync.setValue(c["sync_tolerance_min"])
        self.w_door.setValue(c["door_rise_per_15min"])

    def _save(self):
        self.result_config = {
            "case_type":                    self.type_combo.currentText(),
            "setpoint":                     self.w_setpoint.value(),
            "defrost_terminate_threshold":  self.w_threshold.value(),
            "defrost_frequency":            self.w_freq.value(),
            "sync_tolerance_min":           self.w_sync.value(),
            "door_rise_per_15min":          self.w_door.value(),
        }
        self.accept()


# ---------------------------------------------------------------------------
#  Main diagnostics widget
# ---------------------------------------------------------------------------

class DiagnosticsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.case_config = CaseConfig()
        self.all_cases: List[str] = []
        self._init_ui()
        self.refresh_cases()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Left panel ────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(210)
        left.setStyleSheet("background:#1a1a2e;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 8, 8)

        lbl = QLabel("Downloaded Cases")
        lbl.setStyleSheet("font-weight:bold; color:#42A5F5; font-size:12px;")
        ll.addWidget(lbl)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._filter)
        ll.addWidget(self.search)

        self.case_list = QListWidget()
        self.case_list.setStyleSheet("""
            QListWidget { background:#16213e; color:#e0e0e0; border:none; }
            QListWidget::item:selected { background:#42A5F5; color:#000; }
            QListWidget::item:hover    { background:#2a2a4e; }
        """)
        self.case_list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.case_list)

        self.cfg_btn = QPushButton("⚙  Configure Case")
        self.cfg_btn.clicked.connect(self._open_config)
        self.cfg_btn.setEnabled(False)
        ll.addWidget(self.cfg_btn)

        QPushButton_refresh = QPushButton("↻  Refresh List")
        QPushButton_refresh.clicked.connect(self.refresh_cases)
        ll.addWidget(QPushButton_refresh)
        layout.addWidget(left)

        # ── Right panel ───────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)

        self.findings_box = QTextEdit()
        self.findings_box.setReadOnly(True)
        self.findings_box.setMaximumHeight(95)
        self.findings_box.setPlaceholderText("Select a case to see auto-diagnostics…")
        self.findings_box.setStyleSheet(
            "QTextEdit { background:#0d0d1a; color:#e0e0e0; "
            "border:1px solid #333; font-size:12px; font-family:'Segoe UI',Arial; }"
        )
        rl.addWidget(self.findings_box)

        if HAS_WEBENGINE:
            self.chart_view = QWebEngineView()
            self.chart_view.setHtml(
                "<html><body style='background:#1a1a2e;color:#555;"
                "display:flex;align-items:center;justify-content:center;height:100vh'>"
                "<p style='font-size:16px'>Select a case from the list</p></body></html>"
            )
            rl.addWidget(self.chart_view)
        else:
            self.chart_view = None
            rl.addWidget(QLabel(
                "PyQtWebEngine not installed — charts will open in your browser.\n"
                "pip install PyQtWebEngine"
            ))

        layout.addWidget(right)

    # ── Case list ─────────────────────────────────────────────────────

    def refresh_cases(self):
        self.all_cases = get_downloaded_cases()
        self._populate(self.all_cases)

    def _populate(self, cases):
        self.case_list.clear()
        for c in cases:
            self.case_list.addItem(QListWidgetItem(c))

    def _filter(self, text):
        self._populate([c for c in self.all_cases if text.upper() in c])

    # ── Selection ─────────────────────────────────────────────────────

    def _on_select(self, item):
        if item is None:
            self.cfg_btn.setEnabled(False)
            return
        self.cfg_btn.setEnabled(True)
        self._show(item.text())

    def _show(self, case_id: str):
        self.findings_box.setPlainText(f"Loading {case_id}…")

        modules = load_case_data(case_id)
        if not modules:
            self.findings_box.setPlainText(f"No downloaded data found for {case_id}.")
            return

        config   = self.case_config.get(case_id)
        findings = run_diagnostics(modules, config)

        # Findings text + border colour
        self.findings_box.setPlainText("\n".join(f["msg"] for f in findings))
        border = ("#EF5350" if any(f["level"] == "critical" for f in findings)
                  else "#FFA726" if any(f["level"] == "warning" for f in findings)
                  else "#66BB6A")
        self.findings_box.setStyleSheet(
            f"QTextEdit {{ background:#0d0d1a; color:#e0e0e0; "
            f"border:2px solid {border}; font-size:12px; font-family:'Segoe UI',Arial; }}"
        )

        html = build_chart_html(case_id, modules, config)
        if self.chart_view:
            self.chart_view.setHtml(html)
        else:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html",
                                              mode="w", encoding="utf-8")
            tmp.write(html)
            tmp.close()
            webbrowser.open(f"file:///{tmp.name}")

    # ── Config ────────────────────────────────────────────────────────

    def _open_config(self):
        item = self.case_list.currentItem()
        if not item:
            return
        case_id = item.text()
        dlg = CaseConfigDialog(case_id, self.case_config.get(case_id), self)
        if dlg.exec_() == QDialog.Accepted:
            self.case_config.set(case_id, dlg.result_config)
            self._show(case_id)
