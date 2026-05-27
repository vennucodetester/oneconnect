"""
Diagnostics tab - interactive charts and automated anomaly detection
Rules engine: user-editable expressions (like Excel IF statements)
"""

import ast
import json
import operator
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
    QDoubleSpinBox, QSpinBox, QComboBox, QDialogButtonBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QAbstractItemView,
    QMessageBox, QScrollArea, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

# Paths — works on any laptop/username
_APP_DIR = Path.home() / "OneC"
DATA_DIR = _APP_DIR / "downloads"
CONFIG_FILE = _APP_DIR / "case_config.json"

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
#  Default rules  (user can edit these per-case)
#  Variables available in expressions:
#    setpoint, threshold, door_rate
#    defrost_count, defrost_failed_count, defrost_failed_pct
#    avg_defrost_min, max_defrost_min
#    alarm_count  (if Alarm column present)
#    temp_rise_15min   (max °F rise in any 15-min window)
#    post_defrost_floor_drift  (+ means getting warmer after each defrost)
#    high_temp_hours   (hours temp was above setpoint + 8°F)
#    max_temp, min_temp
#  Operators: + - * /  AND OR NOT  >= <= > < == !=
# ---------------------------------------------------------------------------

DEFAULT_RULES = [
    {
        "name": "Icing in coil",
        "expression": "defrost_failed_pct >= 50",
        "level": "critical",
        "enabled": True,
        "icon": "🧊",
        "detail": "defrost_failed_pct:.0f% of defrosts did not reach threshold",
    },
    {
        "name": "Early icing (floor drift)",
        "expression": "post_defrost_floor_drift >= 3.0",
        "level": "warning",
        "enabled": True,
        "icon": "📈",
        "detail": "Post-defrost temp floor rising +post_defrost_floor_drift:.1f°F",
    },
    {
        "name": "Door left open",
        "expression": "temp_rise_15min >= door_rate",
        "level": "critical",
        "enabled": True,
        "icon": "🚪",
        "detail": "Temp rose temp_rise_15min:.1f°F in 15 min",
    },
    {
        "name": "Air leak / warm load",
        "expression": "high_temp_hours >= 0.5 AND temp_rise_15min < door_rate",
        "level": "warning",
        "enabled": True,
        "icon": "💨",
        "detail": "Temp above setpoint+8°F for high_temp_hours:.1f hours",
    },
    {
        "name": "Long defrost cycles",
        "expression": "avg_defrost_min >= 45",
        "level": "warning",
        "enabled": True,
        "icon": "⏱",
        "detail": "Avg defrost duration avg_defrost_min:.0f min (max max_defrost_min:.0f min)",
    },
]


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

    def get_rules(self, case_id: str) -> List[dict]:
        """Return per-case rules, or fall back to global rules, or defaults."""
        case_cfg = self.data.get("cases", {}).get(case_id, {})
        if "rules" in case_cfg:
            return [r.copy() for r in case_cfg["rules"]]
        global_rules = self.data.get("global_rules")
        if global_rules:
            return [r.copy() for r in global_rules]
        return [r.copy() for r in DEFAULT_RULES]

    def set_rules(self, case_id: str, rules: List[dict]):
        """Save rules for a specific case."""
        self.data.setdefault("cases", {}).setdefault(case_id, {})["rules"] = rules
        self.save()

    def set_global_rules(self, rules: List[dict]):
        """Save rules that apply to all cases (unless case has its own)."""
        self.data["global_rules"] = rules
        self.save()


# ---------------------------------------------------------------------------
#  Data loading
# ---------------------------------------------------------------------------

def get_downloaded_cases() -> List[str]:
    if not DATA_DIR.exists():
        return []
    cases = set()
    for f in DATA_DIR.glob("*.xlsx"):
        if f.name.startswith("~") or f.name.startswith("_"):
            continue
        # New format: MY26C019878.xlsx  (no timestamp)
        stem = f.stem
        if re.match(r'^[A-Z0-9]+$', stem):
            cases.add(stem)
        else:
            # Legacy format: MY26C019878_20260526_143000.xlsx
            m = re.match(r"^([A-Z0-9]+)_\d{8}_\d{6}$", stem)
            if m:
                cases.add(m.group(1))
    return sorted(cases)


def load_case_data(case_id: str) -> Optional[Dict]:
    """
    Returns {
        "_meta": {"file": filename, "downloaded": mtime},
        module_name: {"sensor_data": df, "sensor_event": df},
        "Store_ambient": {"sensor_data": df},   (if present)
        ...
    }
    Looks for {case_id}.xlsx first (new format), falls back to legacy timestamped files.
    """
    # Try new format first
    target = DATA_DIR / f"{case_id}.xlsx"
    if not target.exists():
        # Fall back to legacy timestamped format
        legacy = sorted(DATA_DIR.glob(f"{case_id}_*.xlsx"), reverse=True)
        if legacy:
            target = legacy[0]
        else:
            return None

    try:
        xl = pd.ExcelFile(target)
        modules: Dict = {
            "_meta": {
                "file": target.name,
                "downloaded": target.stat().st_mtime,
            }
        }
        for sheet in xl.sheet_names:
            df = pd.read_excel(target, sheet_name=sheet)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp").reset_index(drop=True)

            if sheet == "Store_ambient":
                modules["Store_ambient"] = {"sensor_data": df}
            elif "_sensor-data" in sheet:
                mod = sheet.split("_sensor-data")[0]
                modules.setdefault(mod, {})["sensor_data"] = df
            elif "_sensor-event" in sheet:
                mod = sheet.split("_sensor-event")[0]
                modules.setdefault(mod, {})["sensor_event"] = df

        data_keys = [k for k in modules if k not in ("_meta", "Store_ambient")]
        return modules if data_keys else None
    except Exception as e:
        print(f"Error loading {case_id}: {e}")
        return None


# ---------------------------------------------------------------------------
#  Detection helpers
# ---------------------------------------------------------------------------

def detect_defrost_periods(df_d: pd.DataFrame, df_e: pd.DataFrame,
                           threshold: float) -> List[dict]:
    """
    Detect defrost periods using Defrost DO (sensor-event boolean) if available,
    otherwise fall back to Defrost Status (sensor-data numeric > 0).
    Cross-references sensor-data for max Defrost Terminate temp during each period.
    """
    # Determine which source has defrost on/off info
    use_do = (df_e is not None and not df_e.empty and "Defrost DO" in df_e.columns)
    use_status = (df_d is not None and not df_d.empty and "Defrost Status" in df_d.columns)

    if not use_do and not use_status:
        return []

    if use_do:
        # Use Defrost DO (boolean, on-change events — more accurate)
        src = df_e.dropna(subset=["Defrost DO"]).sort_values("timestamp").copy()
        periods, in_def, start = [], False, None
        for _, row in src.iterrows():
            is_def = bool(row["Defrost DO"])
            if is_def and not in_def:
                in_def, start = True, row["timestamp"]
            elif not is_def and in_def:
                end = row["timestamp"]
                in_def = False
                # Look up max Defrost Terminate from sensor-data during this window
                max_t_f = None
                if df_d is not None and not df_d.empty and "Defrost Terminate" in df_d.columns:
                    mask = (df_d["timestamp"] >= start) & (df_d["timestamp"] <= end)
                    seg = df_d[mask]
                    if not seg.empty:
                        max_t = seg["Defrost Terminate"].max()
                        max_t_f = float(max_t) if pd.notna(max_t) else None
                periods.append({
                    "start": start, "end": end,
                    "max_terminate": max_t_f,
                    "terminated_ok": (max_t_f is not None and max_t_f >= threshold),
                    "duration_min": (end - start).total_seconds() / 60,
                })
        return periods
    else:
        # Fall back to Defrost Status (numeric, sampled every 60s)
        periods, in_def, start = [], False, None
        for _, row in df_d.iterrows():
            val = row.get("Defrost Status", 0)
            is_def = pd.notna(val) and float(val) > 0
            if is_def and not in_def:
                in_def, start = True, row["timestamp"]
            elif not is_def and in_def:
                end = row["timestamp"]
                in_def = False
                mask = (df_d["timestamp"] >= start) & (df_d["timestamp"] <= end)
                seg = df_d[mask]
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
    """Get alarm timestamps. Returns empty list if Alarm column not present (removed from downloads)."""
    if df_event is None or df_event.empty or "Alarm" not in df_event.columns:
        return []
    return list(df_event.loc[df_event["Alarm"] == True, "timestamp"])


# ---------------------------------------------------------------------------
#  Metrics computation  (feeds the rules engine)
# ---------------------------------------------------------------------------

def compute_metrics(df_d: pd.DataFrame, df_e: pd.DataFrame, config: dict) -> dict:
    """
    Compute all numeric variables that can be referenced in rule expressions.
    Returns a flat dict of floats/ints.
    """
    threshold = config.get("defrost_terminate_threshold", 35.0)
    setpoint  = config.get("setpoint", -10.0)
    door_rate = config.get("door_rise_per_15min", 5.0)

    m = {
        # Config values (also available in expressions)
        "setpoint":   setpoint,
        "threshold":  threshold,
        "door_rate":  door_rate,
        # Defrost
        "defrost_count":        0,
        "defrost_failed_count": 0,
        "defrost_failed_pct":   0.0,
        # Alarms
        "alarm_count":          0,
        # Temperature behavior
        "temp_rise_15min":            0.0,
        "post_defrost_floor_drift":   0.0,
        "high_temp_hours":            0.0,
        "max_temp":                   0.0,
        "min_temp":                   0.0,
    }

    # Defrost stats (now uses Defrost DO from sensor-event if available)
    periods = detect_defrost_periods(df_d, df_e, threshold)
    m["defrost_count"] = len(periods)
    bad = [p for p in periods if not p["terminated_ok"]]
    m["defrost_failed_count"] = len(bad)
    m["defrost_failed_pct"] = (len(bad) / len(periods) * 100) if periods else 0.0

    # Average defrost duration (minutes)
    if periods:
        m["avg_defrost_min"] = sum(p["duration_min"] for p in periods) / len(periods)
        m["max_defrost_min"] = max(p["duration_min"] for p in periods)
    else:
        m["avg_defrost_min"] = 0.0
        m["max_defrost_min"] = 0.0

    # Alarms (graceful — Alarm column may not exist in new downloads)
    m["alarm_count"] = len(get_alarm_times(df_e))

    if not df_d.empty and "Control Temperature" in df_d.columns:
        df_s = df_d.sort_values("timestamp").copy()
        m["max_temp"] = float(df_s["Control Temperature"].max())
        m["min_temp"] = float(df_s["Control Temperature"].min())

        # Max temp rise in any 15-min block
        df_s["_block"] = ((df_s["timestamp"] - df_s["timestamp"].iloc[0])
                          .dt.total_seconds() // 900).astype(int)
        block_avg = df_s.groupby("_block")["Control Temperature"].mean()
        rises = block_avg.diff()
        m["temp_rise_15min"] = float(rises.max()) if not rises.empty and pd.notna(rises.max()) else 0.0

        # Hours above setpoint + 8
        high = df_s[df_s["Control Temperature"] > setpoint + 8]
        if len(high) > 1:
            m["high_temp_hours"] = float(
                (high["timestamp"].max() - high["timestamp"].min()).total_seconds() / 3600
            )

        # Post-defrost floor drift (last post-defrost min vs first)
        floors = []
        for p in periods:
            w_end = p["end"] + pd.Timedelta(minutes=45)
            seg = df_d[(df_d["timestamp"] >= p["end"]) & (df_d["timestamp"] <= w_end)]
            if not seg.empty and "Control Temperature" in seg.columns:
                floors.append(float(seg["Control Temperature"].min()))
        if len(floors) >= 2:
            m["post_defrost_floor_drift"] = floors[-1] - floors[0]

    return m


# ---------------------------------------------------------------------------
#  Rules engine  (safe expression evaluator)
# ---------------------------------------------------------------------------

class RulesEngine:
    """
    Evaluates user-written expressions like:
      "defrost_failed_pct >= 50"
      "temp_rise_15min >= door_rate AND alarm_count > 0"
      "(max_temp - setpoint) >= 15"

    Supported:  + - * /  parentheses  AND OR NOT  >= <= > < == !=
    No Python builtins are exposed — only the metrics dict values.
    """

    # Safe AST node types we allow
    _ALLOWED = {
        ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
        ast.And, ast.Or, ast.Not,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.USub, ast.UAdd,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.Constant, ast.Name, ast.IfExp, ast.Load,
    }

    @classmethod
    def _check_node(cls, node):
        if type(node) not in cls._ALLOWED:
            raise ValueError(f"Unsupported expression element: {type(node).__name__}")
        for child in ast.iter_child_nodes(node):
            cls._check_node(child)

    @classmethod
    def evaluate(cls, expression: str, variables: dict) -> bool:
        """Returns True/False. Returns False on any error."""
        try:
            # Normalise Excel-style keywords to Python
            expr = re.sub(r'\bAND\b', 'and', expression)
            expr = re.sub(r'\bOR\b',  'or',  expr)
            expr = re.sub(r'\bNOT\b', 'not', expr)
            expr = re.sub(r'\bTRUE\b',  'True',  expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bFALSE\b', 'False', expr, flags=re.IGNORECASE)

            tree = ast.parse(expr, mode='eval')
            cls._check_node(tree)

            safe_ns = {"__builtins__": {}, "True": True, "False": False}
            safe_ns.update({k: v for k, v in variables.items() if v is not None})
            return bool(eval(compile(tree, "<rule>", "eval"), safe_ns))
        except Exception:
            return False

    @classmethod
    def test_expression(cls, expression: str, variables: dict) -> tuple:
        """Returns (ok: bool, result_or_error: str)"""
        try:
            expr = re.sub(r'\bAND\b', 'and', expression)
            expr = re.sub(r'\bOR\b',  'or',  expr)
            expr = re.sub(r'\bNOT\b', 'not', expr)
            tree = ast.parse(expr, mode='eval')
            cls._check_node(tree)
            safe_ns = {"__builtins__": {}, "True": True, "False": False}
            safe_ns.update({k: v for k, v in variables.items() if v is not None})
            result = eval(compile(tree, "<rule>", "eval"), safe_ns)
            return True, f"= {result}"
        except Exception as e:
            return False, str(e)


def _format_detail(template: str, metrics: dict) -> str:
    """
    Render a detail string like "defrost_failed_pct:.0f% of defrosts failed"
    by substituting variable:format patterns with actual values.
    """
    def replacer(m):
        varname = m.group(1)
        fmt = m.group(2) or ""
        val = metrics.get(varname)
        if val is None:
            return varname
        try:
            return format(val, fmt) if fmt else str(round(val, 2))
        except Exception:
            return str(val)
    return re.sub(r'(\w+)(:[^,\s]+)?', replacer, template)


# ---------------------------------------------------------------------------
#  Diagnostics engine  (uses rules)
# ---------------------------------------------------------------------------

def run_diagnostics(modules: Dict, config: dict, rules: List[dict] = None) -> List[dict]:
    if rules is None:
        rules = DEFAULT_RULES

    findings = []
    mod_names = [k for k in modules.keys() if k != "_meta"]
    threshold = config.get("defrost_terminate_threshold", 35.0)

    for mod_name in mod_names:
        data = modules[mod_name]
        df_d = data.get("sensor_data",  pd.DataFrame())
        df_e = data.get("sensor_event", pd.DataFrame())

        metrics = compute_metrics(df_d, df_e, config)

        for rule in rules:
            if not rule.get("enabled", True):
                continue
            if RulesEngine.evaluate(rule["expression"], metrics):
                icon   = rule.get("icon", "⚠")
                detail = _format_detail(rule.get("detail", ""), metrics)
                findings.append({
                    "level":  rule.get("level", "warning"),
                    "type":   rule["name"],
                    "module": mod_name,
                    "msg":    f"{icon} [{mod_name}] {rule['name']}: {detail}",
                })

    # Defrost sync check (only when 2+ modules present)
    sync_tol = config.get("sync_tolerance_min", 30)
    if len(mod_names) == 2:
        a, b = mod_names
        pa = detect_defrost_periods(
            modules[a].get("sensor_data", pd.DataFrame()),
            modules[a].get("sensor_event", pd.DataFrame()), threshold)
        pb = detect_defrost_periods(
            modules[b].get("sensor_data", pd.DataFrame()),
            modules[b].get("sensor_event", pd.DataFrame()), threshold)
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
    mod_names = [k for k in modules.keys() if k not in ("_meta", "Store_ambient")]
    has_ambient = "Store_ambient" in modules
    n = len(mod_names) + (1 if has_ambient else 0)
    if len(mod_names) == 0:
        return "<html><body style='background:#1a1a2e;color:#aaa'><p>No data</p></body></html>"

    titles = mod_names + (["Store Ambient"] if has_ambient else [])
    specs = [[{"secondary_y": True}]] * n
    fig = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=titles,
        specs=specs,
    )

    sp_shown = thr_shown = False

    for ri, mod_name in enumerate(mod_names, 1):
        data  = modules[mod_name]
        df_d  = data.get("sensor_data",  pd.DataFrame())
        df_e  = data.get("sensor_event", pd.DataFrame())

        if not df_d.empty:
            ts = df_d["timestamp"]

            # Setpoint reference
            fig.add_trace(go.Scatter(
                x=[ts.min(), ts.max()], y=[setpoint, setpoint],
                name="Setpoint", mode="lines",
                line=dict(color="rgba(180,180,180,0.55)", dash="dash", width=1.5),
                showlegend=not sp_shown, legendgroup="sp", hoverinfo="skip",
            ), row=ri, col=1, secondary_y=False)
            sp_shown = True

            # Defrost threshold reference
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

        # Digital output traces (from sensor-event) — shown on secondary axis as 0/1
        if not df_e.empty:
            ts_e = df_e["timestamp"]

            # Defrost DO
            if "Defrost DO" in df_e.columns:
                fig.add_trace(go.Scatter(
                    x=ts_e, y=df_e["Defrost DO"].astype(float),
                    name=f"{mod_name} — Defrost DO", mode="lines",
                    line=dict(color="#FFD54F", width=1.5),
                    opacity=0.6, visible="legendonly",
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Defrost DO: %{y}<extra></extra>",
                ), row=ri, col=1, secondary_y=True)

            # Cond Fan DO
            if "Cond Fan DO" in df_e.columns:
                fig.add_trace(go.Scatter(
                    x=ts_e, y=df_e["Cond Fan DO"].astype(float),
                    name=f"{mod_name} — Cond Fan DO", mode="lines",
                    line=dict(color="#AB47BC", width=1.5),
                    opacity=0.6, visible="legendonly",
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Cond Fan DO: %{y}<extra></extra>",
                ), row=ri, col=1, secondary_y=True)

        # Defrost shading
        for p in detect_defrost_periods(df_d, df_e, threshold):
            color = "rgba(255,210,50,0.30)" if p["terminated_ok"] else "rgba(255,70,50,0.35)"
            fig.add_vrect(x0=p["start"].strftime("%Y-%m-%d %H:%M:%S"), 
                          x1=p["end"].strftime("%Y-%m-%d %H:%M:%S"),
                          fillcolor=color, line_width=0, layer="below", row=ri, col=1)

        # Alarm markers
        alarm_times = get_alarm_times(df_e)
        if alarm_times:
            fig.add_trace(go.Scatter(
                x=alarm_times,
                y=[None] * len(alarm_times),
                mode="markers",
                marker=dict(symbol="line-ns", size=30, color="red",
                            line=dict(color="red", width=2)),
                name="Alarm",
                legendgroup="alarm",
                showlegend=(ri == 1),
                hovertemplate="<b>ALARM</b><br>%{x|%m/%d %H:%M}<extra></extra>",
            ), row=ri, col=1, secondary_y=False)

        fig.update_yaxes(title_text="Temp (°F)", row=ri, col=1, secondary_y=False,
                         showgrid=True, gridcolor="rgba(255,255,255,0.07)")
        fig.update_yaxes(title_text="DO / Disch.", row=ri, col=1, secondary_y=True,
                         showgrid=False)

    # ── Store Ambient subplot ──
    if has_ambient:
        amb_row = len(mod_names) + 1
        amb_df = modules["Store_ambient"].get("sensor_data", pd.DataFrame())
        if not amb_df.empty and "timestamp" in amb_df.columns:
            ts_a = amb_df["timestamp"]
            if "Temperature" in amb_df.columns:
                fig.add_trace(go.Scatter(
                    x=ts_a, y=amb_df["Temperature"],
                    name="Store Temp", mode="lines",
                    line=dict(color="#66BB6A", width=2),
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Store Temp: %{y:.1f}°F<extra></extra>",
                ), row=amb_row, col=1, secondary_y=False)
            if "Humidity" in amb_df.columns:
                fig.add_trace(go.Scatter(
                    x=ts_a, y=amb_df["Humidity"],
                    name="Store RH%", mode="lines",
                    line=dict(color="#29B6F6", width=1.5, dash="dot"),
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>RH: %{y:.1f}%<extra></extra>",
                ), row=amb_row, col=1, secondary_y=True)
            if "Dewpoint" in amb_df.columns:
                fig.add_trace(go.Scatter(
                    x=ts_a, y=amb_df["Dewpoint"],
                    name="Dewpoint", mode="lines",
                    line=dict(color="#78909C", width=1.5, dash="dash"),
                    hovertemplate="<b>%{x|%m/%d %H:%M}</b><br>Dewpoint: %{y:.1f}°F<extra></extra>",
                ), row=amb_row, col=1, secondary_y=False)
            fig.update_yaxes(title_text="Temp (°F)", row=amb_row, col=1, secondary_y=False,
                             showgrid=True, gridcolor="rgba(255,255,255,0.07)")
            fig.update_yaxes(title_text="RH %", row=amb_row, col=1, secondary_y=True,
                             showgrid=False)

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

    html = fig.to_html(include_plotlyjs="cdn", full_html=True)

    # Inject a polyfill for insertRule to avoid crashes on old PyQtWebEngine Chromium
    polyfill = """
    <script>
    var originalInsertRule = CSSStyleSheet.prototype.insertRule;
    CSSStyleSheet.prototype.insertRule = function(rule, index) {
        try {
            return originalInsertRule.call(this, rule, index || 0);
        } catch (e) {
            return -1;
        }
    };
    </script>
    """
    return html.replace("<head>", f"<head>\\n{polyfill}")


# ---------------------------------------------------------------------------
#  Case config dialog
# ---------------------------------------------------------------------------

class CaseConfigDialog(QDialog):
    def __init__(self, case_id: str, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Configure  —  {case_id}")
        self.setFixedWidth(360)
        self.result_config = config.copy()
        self._build(config)

    def _build(self, cfg):
        self.setStyleSheet("""
            QDialog { background: #1a1a2e; color: #e0e0e0; }
            QLabel { color: #e0e0e0; }
            QPushButton { background: #2a2a4e; color: #e0e0e0; border: 1px solid #42A5F5; border-radius: 4px; padding: 4px 12px; }
            QPushButton:hover { background: #3a3a6e; }
            QComboBox { background: #16213e; color: #e0e0e0; border: 1px solid #555; padding: 2px; }
            QDoubleSpinBox, QSpinBox { background: #16213e; color: #e0e0e0; border: 1px solid #555; padding: 2px; }
        """)
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

        self.w_setpoint  = ds(cfg.get("setpoint", -10),                  -60,  60)
        self.w_threshold = ds(cfg.get("defrost_terminate_threshold", 35),  20,  80)
        self.w_freq      = si(cfg.get("defrost_frequency", 4),              1,  24, "x/day")
        self.w_sync      = si(cfg.get("sync_tolerance_min", 30),            5, 120, "min")
        self.w_door      = ds(cfg.get("door_rise_per_15min", 5),            1,  30, "°F/15min")

        form.addRow("Setpoint:",                   self.w_setpoint)
        form.addRow("Defrost terminate ≥:",        self.w_threshold)
        form.addRow("Expected defrost frequency:", self.w_freq)
        form.addRow("Sync tolerance:",             self.w_sync)
        form.addRow("Door open threshold:",        self.w_door)
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
            "case_type":                   self.type_combo.currentText(),
            "setpoint":                    self.w_setpoint.value(),
            "defrost_terminate_threshold": self.w_threshold.value(),
            "defrost_frequency":           self.w_freq.value(),
            "sync_tolerance_min":          self.w_sync.value(),
            "door_rise_per_15min":         self.w_door.value(),
        }
        self.accept()


# ---------------------------------------------------------------------------
#  Rules editor dialog
# ---------------------------------------------------------------------------

_VARS_HELP = """\
Available variables you can use in expressions:

  setpoint          Configured setpoint temp (°F)
  threshold         Defrost terminate threshold (°F)
  door_rate         Door-open temp rise threshold (°F/15min)

  defrost_count        Total defrosts detected
  defrost_failed_count Defrosts that didn't reach threshold
  defrost_failed_pct   % that failed (0-100)
  avg_defrost_min      Average defrost duration (minutes)
  max_defrost_min      Longest single defrost (minutes)

  alarm_count          Number of alarm events (if available)

  temp_rise_15min      Max temp rise in any 15-min window (°F)
  high_temp_hours      Hours temp was above setpoint + 8°F
  post_defrost_floor_drift  Drift in post-defrost low temp (°F)
                            Positive = getting warmer over time

  max_temp / min_temp  Highest / lowest Control Temp seen

Operators:   +  -  *  /  ( )
Comparisons: >= <= > < == !=
Logic:       AND  OR  NOT

Examples:
  defrost_failed_pct >= 50
  (max_temp - setpoint) >= 15
  temp_rise_15min >= door_rate AND alarm_count > 0
  defrost_failed_pct >= 30 OR post_defrost_floor_drift >= 5
"""


class RulesDialog(QDialog):
    """Edit diagnostic rules for a case (or globally)."""

    def __init__(self, case_id: str, rules: List[dict],
                 sample_metrics: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Diagnostic Rules  —  {case_id}")
        self.resize(780, 500)
        self.rules: List[dict] = [r.copy() for r in rules]
        self.sample_metrics = sample_metrics or {}
        self._build()

    def _build(self):
        self.setStyleSheet("""
            QDialog { background: #1a1a2e; color: #e0e0e0; }
            QLabel { color: #e0e0e0; }
            QCheckBox { color: #e0e0e0; }
            QPushButton { background: #2a2a4e; color: #e0e0e0; border: 1px solid #42A5F5; border-radius: 4px; padding: 4px 12px; }
            QPushButton:hover { background: #3a3a6e; }
            QLineEdit { background: #16213e; color: #e0e0e0; border: 1px solid #555; padding: 4px; }
            QTextEdit { background: #0d0d1a; color: #aaa; border: 1px solid #333; }
        """)
        layout = QVBoxLayout(self)

        # Header
        hdr = QLabel(
            "Write rules as expressions — like Excel formulas.\n"
            "Each rule fires a finding when its expression is TRUE."
        )
        hdr.setStyleSheet("color:#aaa; font-size:11px; padding:4px;")
        layout.addWidget(hdr)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["✓", "Rule name", "Expression", "Level"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().resizeSection(0, 30)
        self.table.horizontalHeader().resizeSection(1, 160)
        self.table.horizontalHeader().resizeSection(3, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#16213e; alternate-background-color:#1e2640; color:#e0e0e0; gridline-color:#333; border: 1px solid #333; }"
            "QHeaderView::section { background:#1a1a2e; color:#42A5F5; border:1px solid #333; padding:4px; }"
            "QTableWidget::item:selected { background:#42A5F5; color:#000; }"
        )
        layout.addWidget(self.table)

        # Populate
        self._populate()

        # Row action buttons
        row_btns = QHBoxLayout()
        btn_add = QPushButton("＋  Add rule")
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("✕  Delete selected")
        btn_del.clicked.connect(self._del_row)
        btn_reset = QPushButton("↺  Reset to defaults")
        btn_reset.clicked.connect(self._reset)
        row_btns.addWidget(btn_add)
        row_btns.addWidget(btn_del)
        row_btns.addStretch()
        row_btns.addWidget(btn_reset)
        layout.addLayout(row_btns)

        # Test expression area
        test_row = QHBoxLayout()
        self.test_expr = QLineEdit()
        self.test_expr.setPlaceholderText("Test an expression here, e.g.  defrost_failed_pct >= 50")
        btn_test = QPushButton("Test")
        btn_test.setFixedWidth(60)
        btn_test.clicked.connect(self._test_expr)
        self.test_result = QLabel("")
        self.test_result.setMinimumWidth(80)
        test_row.addWidget(QLabel("Try:"))
        test_row.addWidget(self.test_expr)
        test_row.addWidget(btn_test)
        test_row.addWidget(self.test_result)
        layout.addLayout(test_row)

        # Variables help (collapsible toggle)
        self.help_visible = False
        btn_help = QPushButton("📋  Show available variables")
        btn_help.setCheckable(True)
        btn_help.toggled.connect(self._toggle_help)
        layout.addWidget(btn_help)

        self.help_box = QTextEdit()
        self.help_box.setReadOnly(True)
        self.help_box.setPlainText(_VARS_HELP)
        self.help_box.setMaximumHeight(160)
        self.help_box.setStyleSheet(
            "QTextEdit { background:#0d0d1a; color:#aaa; font-family:Consolas,monospace; font-size:11px; }"
        )
        self.help_box.setVisible(False)
        layout.addWidget(self.help_box)

        # Dialog buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _populate(self):
        self.table.setRowCount(0)
        for rule in self.rules:
            self._append_row(rule)

    def _append_row(self, rule: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Enabled checkbox
        chk = QCheckBox()
        chk.setChecked(rule.get("enabled", True))
        chk.setStyleSheet("margin-left:6px;")
        self.table.setCellWidget(row, 0, chk)

        # Name
        self.table.setItem(row, 1, QTableWidgetItem(rule.get("name", "")))

        # Expression
        self.table.setItem(row, 2, QTableWidgetItem(rule.get("expression", "")))

        # Level combo
        combo = QComboBox()
        combo.addItems(["critical", "warning", "info"])
        combo.setCurrentText(rule.get("level", "warning"))
        combo.setStyleSheet("background:#16213e; color:#e0e0e0;")
        self.table.setCellWidget(row, 3, combo)

    def _add_row(self):
        self.table.insertRow(self.table.rowCount())
        row = self.table.rowCount() - 1
        chk = QCheckBox(); chk.setChecked(True); chk.setStyleSheet("margin-left:6px;")
        self.table.setCellWidget(row, 0, chk)
        self.table.setItem(row, 1, QTableWidgetItem("New rule"))
        self.table.setItem(row, 2, QTableWidgetItem(""))
        combo = QComboBox(); combo.addItems(["critical", "warning", "info"])
        combo.setStyleSheet("background:#16213e; color:#e0e0e0;")
        self.table.setCellWidget(row, 3, combo)
        self.table.editItem(self.table.item(row, 2))

    def _del_row(self):
        rows = sorted({i.row() for i in self.table.selectedItems()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _reset(self):
        if QMessageBox.question(
            self, "Reset rules",
            "Replace all rules with the built-in defaults?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.rules = [r.copy() for r in DEFAULT_RULES]
            self._populate()

    def _toggle_help(self, checked):
        self.help_box.setVisible(checked)

    def _test_expr(self):
        expr = self.test_expr.text().strip()
        if not expr:
            return
        ok, result = RulesEngine.test_expression(expr, self.sample_metrics)
        if ok:
            is_true = "True" in result
            color = "#66BB6A" if is_true else "#EF5350"
            self.test_result.setText(result)
            self.test_result.setStyleSheet(f"color:{color}; font-weight:bold;")
        else:
            self.test_result.setText(f"Error: {result}")
            self.test_result.setStyleSheet("color:#FF7043;")

    def _save(self):
        self.rules = []
        for row in range(self.table.rowCount()):
            chk   = self.table.cellWidget(row, 0)
            name  = (self.table.item(row, 1) or QTableWidgetItem("")).text().strip()
            expr  = (self.table.item(row, 2) or QTableWidgetItem("")).text().strip()
            combo = self.table.cellWidget(row, 3)
            if not expr:
                continue
            self.rules.append({
                "name":       name or "Rule",
                "expression": expr,
                "level":      combo.currentText() if combo else "warning",
                "enabled":    chk.isChecked() if chk else True,
                "icon":       "⚠",
                "detail":     "",
            })
        self.accept()


# ---------------------------------------------------------------------------
#  Main diagnostics widget
# ---------------------------------------------------------------------------

class DiagnosticsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.case_config = CaseConfig()
        self.all_cases: List[str] = []
        self._current_case: Optional[str] = None
        self._current_metrics: dict = {}
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

        btn_style = """
            QPushButton {
                background: #2a2a4e; 
                color: #e0e0e0; 
                border: 1px solid #42A5F5; 
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover { background: #3a3a6e; }
            QPushButton:disabled { background: #1a1a2e; color: #555; border-color: #333; }
        """

        self.cfg_btn = QPushButton("⚙  Configure Case")
        self.cfg_btn.setStyleSheet(btn_style)
        self.cfg_btn.clicked.connect(self._open_config)
        self.cfg_btn.setEnabled(False)
        ll.addWidget(self.cfg_btn)

        self.rules_btn = QPushButton("📋  Edit Rules")
        self.rules_btn.setStyleSheet(btn_style)
        self.rules_btn.clicked.connect(self._open_rules)
        self.rules_btn.setEnabled(False)
        ll.addWidget(self.rules_btn)

        btn_refresh = QPushButton("↻  Refresh List")
        btn_refresh.setStyleSheet(btn_style)
        btn_refresh.clicked.connect(self.refresh_cases)
        ll.addWidget(btn_refresh)
        layout.addWidget(left)

        # ── Right panel ───────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)

        # File info — shows exactly which file is loaded
        self.file_label = QLabel("No case selected")
        self.file_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.file_label.setStyleSheet(
            "color:#666; font-size:10px; padding:2px 4px; "
            "background:#0d0d1a; border-bottom:1px solid #222;"
        )
        rl.addWidget(self.file_label)

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
            self.rules_btn.setEnabled(False)
            return
        self.cfg_btn.setEnabled(True)
        self.rules_btn.setEnabled(True)
        self._show(item.text())

    def _show(self, case_id: str):
        self._current_case = case_id
        self.findings_box.setPlainText(f"Loading {case_id}…")
        self.file_label.setText(f"Loading {case_id}…")

        modules = load_case_data(case_id)
        if not modules:
            self.findings_box.setPlainText(f"No downloaded data found for {case_id}.")
            self.file_label.setText(f"No data found for {case_id}")
            return

        # Show which file is loaded
        meta = modules.get("_meta", {})
        n_files = meta.get("all_files", 1)
        fname   = meta.get("file", "unknown")
        self.file_label.setText(
            f"📄  {fname}"
            + (f"   ({n_files} file(s) in folder — showing latest)" if n_files > 1 else "")
        )

        config = self.case_config.get(case_id)
        rules  = self.case_config.get_rules(case_id)

        # Compute sample metrics from first module (for rules testing)
        mod_names = [k for k in modules if k != "_meta"]
        if mod_names:
            first = modules[mod_names[0]]
            self._current_metrics = compute_metrics(
                first.get("sensor_data", pd.DataFrame()),
                first.get("sensor_event", pd.DataFrame()),
                config,
            )
        else:
            self._current_metrics = {}

        findings = run_diagnostics(modules, config, rules)

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

    # ── Rules editor ─────────────────────────────────────────────────

    def _open_rules(self):
        if not self._current_case:
            return
        case_id = self._current_case
        rules   = self.case_config.get_rules(case_id)
        dlg = RulesDialog(case_id, rules, self._current_metrics, self)
        if dlg.exec_() == QDialog.Accepted:
            self.case_config.set_rules(case_id, dlg.rules)
            self._show(case_id)
