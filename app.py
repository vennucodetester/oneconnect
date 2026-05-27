#!/usr/bin/env python3
"""
OneConnect Data Downloader
Desktop GUI: download + diagnostics + data cruncher (coming soon)
For Microblock-Cases on Dollar General profile
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QLabel,
    QProgressBar, QMessageBox, QInputDialog, QSpinBox, QTabWidget,
    QDialog, QLineEdit, QDialogButtonBox, QTextBrowser, QCheckBox, QComboBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QFont

from diagnostics import DiagnosticsWidget
from data_cruncher import DataCruncherWidget


# ---------------------------------------------------------------------------
#  API Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://api.us.oneconnect.net/oneconnect-api"
PROJECT_ID = 136        # Hussmann OneConnect
TENANT_ID = 37          # Dollar General

# Paths — works on any laptop/username
_APP_DIR = Path.home() / "OneC"
DATA_DIR = _APP_DIR / "downloads"
TOKEN_FILE = _APP_DIR / "token.txt"
RTA_FILE = _APP_DIR / "rta.txt"
CATALOG_FILE = DATA_DIR / "_catalog.json"

# Microblock telemetry attributes
# sensor-data = numeric (3 temp sensors + defrost status)
# sensor-event = digital outputs + setpoint
CASE_ATTRS = [
    {"attribute": "Control Temperature",      "serviceType": "Microblock-sensor-data"},
    {"attribute": "Defrost Terminate",         "serviceType": "Microblock-sensor-data"},
    {"attribute": "Defrost Status",            "serviceType": "Microblock-sensor-data"},
    {"attribute": "Compressor Discharge Temp", "serviceType": "Microblock-sensor-data"},
    {"attribute": "Refrigeration DO",          "serviceType": "Microblock-sensor-event"},
    {"attribute": "Setpoint",                  "serviceType": "Microblock-sensor-event"},
    {"attribute": "Evap Fan DO",               "serviceType": "Microblock-sensor-event"},
    {"attribute": "Cond Fan DO",               "serviceType": "Microblock-sensor-event"},
    {"attribute": "Defrost DO",                "serviceType": "Microblock-sensor-event"},
]

# Store ambient sensor attributes
STORE_ATTRS = [
    {"attribute": "Temperature", "serviceType": "RHsensor-sensor-data"},
    {"attribute": "Humidity",    "serviceType": "RHsensor-sensor-data"},
    {"attribute": "Dewpoint",    "serviceType": "RHsensor-sensor-data"},
]


# ---------------------------------------------------------------------------
#  API helper functions
# ---------------------------------------------------------------------------

def refresh_token(rta: str) -> Optional[str]:
    """Get a fresh Bearer token using the Refresh Token (RTA)."""
    try:
        resp = requests.post(
            f"{BASE_URL}/auth",
            headers={"Content-Type": "application/json", "x-refresh-token": rta},
            timeout=10,
        )
        if resp.status_code == 200:
            auth = resp.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:]
            return auth
    except Exception:
        pass
    return None


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
#  Catalog  (tracks all downloaded cases + metadata)
# ---------------------------------------------------------------------------

def load_catalog() -> dict:
    if CATALOG_FILE.exists():
        try:
            return json.loads(CATALOG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_catalog(catalog: dict):
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(catalog, indent=2, default=str))


# ---------------------------------------------------------------------------
#  Custom token input dialog
# ---------------------------------------------------------------------------

class TokenInputDialog(QDialog):
    """Dialog to get RTA token from user with clear instructions and copy button."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter Refresh Token")
        self.setFixedWidth(500)
        self.token = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        instr = QLabel(
            "Your RTA token has expired. Get a fresh one:\n\n"
            "1. Go to: mc.us.oneconnect.net\n"
            "2. Press F12 to open DevTools\n"
            "3. Click the Console tab\n"
            "4. Copy and paste this command:"
        )
        instr.setStyleSheet("font-size:11px; line-height:1.6;")
        layout.addWidget(instr)

        cmd_box = QTextEdit()
        cmd_box.setPlainText("localStorage.getItem('RTA')")
        cmd_box.setReadOnly(True)
        cmd_box.setMaximumHeight(50)
        cmd_box.setStyleSheet(
            "QTextEdit { background:#2a2a4e; color:#42A5F5; font-family:Consolas,monospace; "
            "font-size:13px; padding:8px; border:1px solid #42A5F5; border-radius:4px; }"
        )
        layout.addWidget(cmd_box)

        btn_copy = QPushButton("Copy command to clipboard")
        btn_copy.setFixedHeight(32)
        btn_copy.setStyleSheet(
            "QPushButton { background:#42A5F5; color:#fff; font-weight:bold; "
            "border:none; border-radius:4px; }"
            "QPushButton:hover { background:#64B5F6; }"
        )
        btn_copy.clicked.connect(lambda: self._copy_to_clipboard("localStorage.getItem('RTA')"))
        layout.addWidget(btn_copy)

        layout.addSpacing(10)

        paste_label = QLabel("5. Paste the result here:")
        paste_label.setStyleSheet("font-size:11px; font-weight:bold;")
        layout.addWidget(paste_label)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Paste your RTA token here (looks like 5c227fcf-369b-...)")
        self.input.setEchoMode(QLineEdit.Password)
        self.input.setMinimumHeight(40)
        layout.addWidget(self.input)

        help_text = QLabel(
            "The token looks like:  5c227fcf-369b-451d-b5ad-537c4d745a32\n"
            "This only needs to be done once."
        )
        help_text.setStyleSheet("font-size:10px; color:#888;")
        layout.addWidget(help_text)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _copy_to_clipboard(self, text: str):
        try:
            app = QApplication.instance()
            if app:
                app.clipboard().setText(text)
                self.input.setPlaceholderText("Command copied! Paste it in the browser console")
        except Exception:
            pass

    def _accept(self):
        token = self.input.text().strip()
        if len(token) < 10:
            QMessageBox.warning(self, "Invalid", "Please paste the token from the browser console.")
            return
        self.token = token
        self.accept()


# ---------------------------------------------------------------------------
#  Model Fetcher Worker
# ---------------------------------------------------------------------------

class FetchModelsWorker(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, token: str, rta: str):
        super().__init__()
        self.token = token
        self.rta = rta

    def run(self):
        try:
            url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets?pageNumber=0&pageSize=2000"
            resp = requests.get(url, headers=get_headers(self.token), timeout=20)
            resp.raise_for_status()
            
            result = resp.json()
            assets = result.get("data", []) if isinstance(result, dict) else result
            if not assets and isinstance(result, dict) and "content" in result:
                assets = result.get("content", [])

            models = {}
            for a in assets:
                m_name = a.get("model", {}).get("name", "Unknown")
                serial = a.get("serialNumber", str(a.get("id")))
                models.setdefault(m_name, []).append(serial)

            self.finished.emit(models)
        except Exception as e:
            self.error.emit(str(e))

# ---------------------------------------------------------------------------
#  Download Worker (runs in background thread)
# ---------------------------------------------------------------------------

class DownloadWorker(QObject):
    """Worker thread for downloading data with pagination and incremental updates."""
    progress = pyqtSignal(str)
    case_progress = pyqtSignal(int, int)
    step_progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, case_ids: List[str], token: str, rta: str, days: int = 90):
        super().__init__()
        self.case_ids = case_ids
        self.token = token
        self.rta = rta
        self.days = days

    # ---- token refresh ----
    def _ensure_token(self):
        new = refresh_token(self.rta)
        if new:
            self.token = new
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(new)
            self.progress.emit("  OK  Token refreshed")
        else:
            raise RuntimeError(
                "RTA token has expired.\n\n"
                "Get a new one from the browser:\n"
                "  1. Open mc.us.oneconnect.net\n"
                "  2. F12 > Console\n"
                "  3. Run: localStorage.getItem('RTA')\n"
                "  4. Click 'Update Token' in the app and paste it"
            )

    # ---- token refresh mid-run (every ~12 minutes) ----
    def _maybe_refresh_token(self):
        """Re-refresh token to avoid expiry during long downloads."""
        new = refresh_token(self.rta)
        if new:
            self.token = new
            TOKEN_FILE.write_text(new)

    # ---- asset lookup ----
    def _find_asset(self, serial: str) -> Optional[dict]:
        url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets"
        params = {"pageNumber": 0, "pageSize": 5, "search": serial}
        resp = requests.get(url, headers=get_headers(self.token), params=params, timeout=15)
        resp.raise_for_status()
        for asset in resp.json().get("data", []):
            if asset.get("serialNumber") == serial:
                return asset
        return None

    # ---- UUID detection ----
    _UUID_RE = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )

    @classmethod
    def _is_uuid(cls, s: str) -> bool:
        return bool(s and cls._UUID_RE.match(s))

    @staticmethod
    def _extract_uuids(obj, found=None) -> List[str]:
        """Recursively extract all UUID-format strings from any JSON structure."""
        if found is None:
            found = []
        uuid_re = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            re.IGNORECASE
        )
        if isinstance(obj, str):
            if uuid_re.match(obj):
                found.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                DownloadWorker._extract_uuids(v, found)
        elif isinstance(obj, list):
            for item in obj:
                DownloadWorker._extract_uuids(item, found)
        return found

    # ---- module discovery ----
    def _get_modules(self, asset_id: int) -> List[dict]:
        url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets/{asset_id}"
        resp = requests.get(url, headers=get_headers(self.token), timeout=15)
        resp.raise_for_status()
        return resp.json().get("modulePlaceholders", [])

    # ---- dashboard config lookup (finds UUID externalIds the portal uses) ----
    def _get_dashboard_uuids(self, asset_id: int) -> List[str]:
        """
        Fetch the portal dashboard config for this asset and extract all
        UUID-format strings. These are the externalIds the portal uses for
        telemetry queries — the ground truth when module placeholders don't
        have a UUID externalId.
        """
        try:
            url = f"{BASE_URL}/tenants/{TENANT_ID}/asset-management/v1/dashboards"
            params = {"assetId": asset_id, "location": "ASSET", "target": "TENANT"}
            resp = requests.get(url, headers=get_headers(self.token),
                                params=params, timeout=15)
            if resp.status_code == 200:
                uuids = self._extract_uuids(resp.json())
                # Deduplicate while preserving order
                seen = set()
                return [u for u in uuids if not (u in seen or seen.add(u))]
        except Exception:
            pass
        return []

    # ---- extract metadata from asset response ----
    @staticmethod
    def _extract_metadata(asset: dict) -> dict:
        model = asset.get("model", {})
        inv = asset.get("inventoryType", {})
        subgroup = asset.get("subgroup", {})
        group = asset.get("group", {})

        sg_name = subgroup.get("name", "") if isinstance(subgroup, dict) else ""
        parts = sg_name.split(" - ", 1)
        store_num = parts[0].strip() if parts else ""
        store_name = parts[1].strip() if len(parts) > 1 else ""

        return {
            "model": model.get("name", "") if isinstance(model, dict) else "",
            "inventory_type": inv.get("name", "") if isinstance(inv, dict) else "",
            "alt_name": asset.get("altName", ""),
            "store": store_num,
            "store_name": store_name,
            "state": group.get("name", "") if isinstance(group, dict) else "",
            "num_modules": asset.get("numberOfModules", 0),
        }

    # ---- paginated telemetry query ----
    def _query_telemetry(self, external_id: str, service_type: str,
                         attrs: List[dict],
                         start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        """
        Query telemetry with automatic pagination.
        Pages through 2000-row chunks until all data is fetched.
        """
        url = f"{BASE_URL}/projects/{PROJECT_ID}/telemetry/v1/telemetry-attributes:query"

        agg_attrs = []
        for i, a in enumerate(attrs):
            agg_attrs.append({
                "attribute": a["attribute"],
                "id": i,
                "name": a["attribute"],
                "legend": f"{a['attribute']} - LTTB",
                "aggregation": "LTTB",
            })

        all_rows = []
        page_num = 0

        # Chunk the total time range into 1-day windows so LTTB provides high density (1-min frequency)
        chunk_days = 1
        current_from = start_dt
        
        while current_from < end_dt:
            current_to = min(current_from + timedelta(days=chunk_days), end_dt)
            
            page_from_str = current_from.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            # Pagination within the chunk (rarely needed for 7 days, but safe)
            while True:
                payload = {
                    "serviceType": service_type,
                    "aggregatedAttributes": agg_attrs,
                    "searchSpan": {
                        "from": page_from_str,
                        "to":   current_to.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    },
                    "timeSeriesId": {"assetExternalId": external_id},
                    "withStep": True,
                    "pageSize": 2000,
                }

                if "event" in service_type:
                    payload["lookupBeforeStart"] = True
                    payload["sortDirection"] = "ASC"

                resp = requests.post(url, json=payload,
                                     headers=get_headers(self.token), timeout=30)
                resp.raise_for_status()
                result = resp.json()
                rows = result.get("data", [])
                meta = result.get("meta", {})

                all_rows.extend(rows)
                page_num += 1

                if meta.get("last", True) or not rows:
                    break

                next_token = meta.get("nextPageToken")
                if not next_token:
                    break
                page_from_str = next_token
                self.progress.emit(f"      ... page {page_num + 1} ({len(all_rows)} rows so far)")
            
            current_from = current_to

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df = df.sort_values("timestamp").drop_duplicates(
                subset=["timestamp"]).reset_index(drop=True)
        return df

    # ---- store ambient download ----
    def _download_store_ambient(self, store_num: str,
                                start_dt: datetime,
                                end_dt: datetime) -> Optional[pd.DataFrame]:
        """Find the DG{store} rhsensor and download ambient data."""
        serial = f"DG{store_num}"
        self.progress.emit(f"    Looking up store sensor {serial}...")

        asset = self._find_asset(serial)
        if not asset:
            self.progress.emit(f"    -- No store ambient sensor found for {serial}")
            return None

        asset_id = asset["id"]

        # Get module external IDs (rhsensor might have modules or not)
        modules = self._get_modules(asset_id)

        # Determine the external ID for telemetry query
        ext_id = None
        if modules:
            ext_id = modules[0].get("externalId")
        if not ext_id:
            # Try using the asset serial as external ID
            ext_id = serial

        attrs = STORE_ATTRS
        try:
            df = self._query_telemetry(
                ext_id, "RHsensor-sensor-data", attrs, start_dt, end_dt
            )
            if not df.empty:
                self.progress.emit(f"    OK  {len(df)} rows of store ambient data")
            else:
                self.progress.emit(f"    -- No store ambient data returned")
            return df if not df.empty else None
        except Exception as e:
            self.progress.emit(f"    -- Store ambient error: {e}")
            return None

    # ---- merge with existing data ----
    @staticmethod
    def _merge_sheets(existing_file: Path, new_sheets: dict) -> dict:
        """Merge new data with existing Excel file, deduplicate by timestamp."""
        try:
            # Use 'with' so the file handle is released before we rename over it
            with pd.ExcelFile(existing_file) as xl:
                sheet_names = xl.sheet_names
                old_data = {}
                for sheet_name in sheet_names:
                    df = pd.read_excel(xl, sheet_name=sheet_name)
                    if "timestamp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                    old_data[sheet_name] = df
            # File handle is now closed — safe to merge and overwrite
            for sheet_name, old_df in old_data.items():
                if sheet_name in new_sheets:
                    merged = pd.concat([old_df, new_sheets[sheet_name]],
                                       ignore_index=True)
                    if "timestamp" in merged.columns:
                        merged = (merged.drop_duplicates(subset=["timestamp"])
                                        .sort_values("timestamp")
                                        .reset_index(drop=True))
                    new_sheets[sheet_name] = merged
                else:
                    # Keep existing sheet that we didn't re-download
                    new_sheets[sheet_name] = old_df
        except Exception:
            pass  # If existing file is corrupt, just use new data
        return new_sheets

    # ---- safe write ----
    @staticmethod
    def _safe_write_excel(file_path: Path, sheets: dict):
        """Write to temp file, then rename. Protects against mid-write corruption."""
        tmp = file_path.with_suffix(".tmp.xlsx")
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                # Clean sheet name for Excel
                clean = re.sub(r'[\\/*\[\]:?]', '-', sheet_name)[:31]
                df.to_excel(writer, sheet_name=clean, index=False)

        # Atomic-ish replace
        if file_path.exists():
            file_path.unlink()
        tmp.rename(file_path)

    # ---- main download loop ----
    def run(self):
        try:
            self.step_progress.emit("Refreshing auth token...")
            self._ensure_token()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            catalog = load_catalog()
            successful = []
            failed = []
            now = datetime.now(timezone.utc)

            for idx, case_id in enumerate(self.case_ids, 1):
                self.case_progress.emit(idx, len(self.case_ids))
                self.progress.emit(
                    f"\nProcessing case {idx}/{len(self.case_ids)}: {case_id}")

                try:
                    # Refresh token periodically (every 5 cases)
                    if idx > 1 and idx % 5 == 0:
                        self._maybe_refresh_token()

                    # Determine date range
                    existing = catalog.get(case_id, {})
                    existing_file = DATA_DIR / f"{case_id}.xlsx"
                    last_data = existing.get("last_data")

                    if last_data and existing_file.exists():
                        # Incremental: from last_data minus 1 day overlap
                        start_dt = datetime.fromisoformat(last_data).replace(
                            tzinfo=timezone.utc) - timedelta(days=1)
                        self.progress.emit(
                            f"  Incremental update from "
                            f"{start_dt.strftime('%Y-%m-%d')}")
                    else:
                        # Full download
                        start_dt = now - timedelta(days=self.days)
                        self.progress.emit(
                            f"  Full download, last {self.days} days")

                    end_dt = now

                    # Step 1: Find asset
                    self.step_progress.emit(f"Looking up {case_id}...")
                    asset = self._find_asset(case_id)
                    if not asset:
                        self.progress.emit(f"  X Asset {case_id} not found")
                        failed.append(case_id)
                        continue

                    asset_id = asset["id"]
                    meta = self._extract_metadata(asset)
                    self.progress.emit(
                        f"  Found: {meta['model']}  |  "
                        f"{meta['alt_name']}  |  "
                        f"Store {meta['store']} {meta['store_name']}, "
                        f"{meta['state']}")

                    # Step 2: Get modules
                    self.step_progress.emit(f"Getting modules for {case_id}...")
                    modules = self._get_modules(asset_id)
                    if not modules:
                        # Fallback for single-module cases where the asset IS the module
                        modules = [{
                            "name": "Main",
                            "externalId": asset.get("externalId") or asset.get("serialNumber") or case_id
                        }]

                    mod_names = [m.get("name", f"Module_{i}")
                                 for i, m in enumerate(modules, 1)]
                    self.progress.emit(
                        f"  {len(modules)} module(s): {', '.join(mod_names)}")

                    all_sheets = {}

                    # Step 3: Download telemetry for each module
                    #
                    # The telemetry API key is a UUID stored in the module's externalId.
                    # Multi-module cases (Left / Center/Right) always have UUID externalIds.
                    # Single "Main" module cases often have the serialNumber instead of a
                    # UUID — in that case we look up the dashboard config to find the real
                    # UUID that the portal uses.
                    asset_ext_id = asset.get("externalId", "")
                    asset_serial  = asset.get("serialNumber", case_id)
                    _dash_uuids_cache: Optional[List[str]] = None  # fetched lazily once

                    for m_idx, module in enumerate(modules, 1):
                        mod_name   = module.get("name", f"Module_{m_idx}")
                        mod_ext_id = module.get("externalId") or ""

                        # Start with the module's own externalId
                        candidates = []
                        for cid in [mod_ext_id, asset_ext_id, asset_serial, case_id]:
                            if cid and cid not in candidates:
                                candidates.append(cid)

                        # If the lead candidate is NOT a UUID, the telemetry store won't
                        # match it. Look up the dashboard config to find the real UUIDs.
                        if not self._is_uuid(candidates[0] if candidates else ""):
                            if _dash_uuids_cache is None:
                                self.progress.emit(
                                    f"    Looking up dashboard config for UUIDs…")
                                _dash_uuids_cache = self._get_dashboard_uuids(asset_id)
                            # Prepend dashboard UUIDs — they're the most likely match
                            for uid in reversed(_dash_uuids_cache):
                                if uid not in candidates:
                                    candidates.insert(0, uid)

                        self.progress.emit(
                            f"  Module {m_idx}/{len(modules)}: {mod_name}"
                            f"  [id: {candidates[0]}]")

                        # Group attrs by service type
                        by_service: Dict[str, list] = {}
                        for attr_def in CASE_ATTRS:
                            st = attr_def["serviceType"]
                            by_service.setdefault(st, []).append(attr_def)

                        for svc_type, attrs in by_service.items():
                            svc_label = ("sensor-data" if "data" in svc_type
                                         else "sensor-event")
                            self.step_progress.emit(
                                f"{mod_name}: Downloading {svc_label}...")
                            self.progress.emit(
                                f"    Querying {svc_label} "
                                f"({len(attrs)} attributes)...")

                            df = pd.DataFrame()
                            used_id = candidates[0]
                            for cid in candidates:
                                df = self._query_telemetry(
                                    cid, svc_type, attrs, start_dt, end_dt)
                                if len(df) > 0:
                                    used_id = cid
                                    break

                            if len(df) > 0:
                                sheet = f"{mod_name}_{svc_label}"
                                all_sheets[sheet] = df
                                note = (f"  (via {used_id})"
                                        if used_id != candidates[0] else "")
                                self.progress.emit(
                                    f"    OK  {len(df)} rows{note}")
                            else:
                                self.progress.emit(
                                    f"    --  No {svc_label} data"
                                    f"  [tried: {', '.join(candidates)}]")

                    # Step 4: Download store ambient
                    if meta["store"]:
                        self.step_progress.emit(
                            f"Downloading store ambient for {meta['store']}...")
                        ambient_df = self._download_store_ambient(
                            meta["store"], start_dt, end_dt)
                        if ambient_df is not None:
                            all_sheets["Store_ambient"] = ambient_df

                    # Step 5: Merge + save
                    if all_sheets:
                        if existing_file.exists():
                            self.step_progress.emit(
                                f"Merging with existing data...")
                            all_sheets = self._merge_sheets(
                                existing_file, all_sheets)
                            self.progress.emit(
                                "  Merged with existing data")

                        self.step_progress.emit(
                            f"Saving {case_id}.xlsx...")
                        self._safe_write_excel(existing_file, all_sheets)

                        # Count total rows
                        total_rows = sum(len(df) for df in all_sheets.values())
                        self.progress.emit(
                            f"  OK  Saved: {case_id}.xlsx "
                            f"({total_rows} total rows)")
                        successful.append(case_id)

                        # Update catalog
                        catalog[case_id] = {
                            **meta,
                            "modules": mod_names,
                            "first_data": (
                                existing.get("first_data")
                                or start_dt.isoformat()),
                            "last_data": end_dt.isoformat(),
                            "last_updated": datetime.now().isoformat(),
                            "total_rows": total_rows,
                        }
                        save_catalog(catalog)
                    else:
                        self.progress.emit(
                            f"  X No data to save for {case_id}")
                        failed.append(case_id)

                except Exception as e:
                    self.progress.emit(f"  X ERROR on {case_id}: {e}")
                    failed.append(case_id)

            # Summary
            sep = "=" * 50
            summary = f"\n{sep}\nDownload Complete!\n{sep}\n"
            summary += f"Successful: {len(successful)}\n"
            for c in successful:
                cat = catalog.get(c, {})
                summary += (f"  OK  {c}  ({cat.get('model', '?')}  "
                            f"Store {cat.get('store', '?')})\n")
            if failed:
                summary += f"\nFailed: {len(failed)}\n"
                for c in failed:
                    summary += f"  X  {c}\n"
            summary += f"\nFiles saved to: {DATA_DIR}\n"
            summary += f"Catalog: {CATALOG_FILE}\n"

            self.progress.emit(summary)
            self.finished.emit(len(failed) == 0, summary)

        except Exception as e:
            self.finished.emit(False, f"Fatal error: {e}")


# ---------------------------------------------------------------------------
#  Main Application Window
# ---------------------------------------------------------------------------

class OneConnectApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.token = None
        self.rta = None
        self.download_thread = None
        self.worker = None
        self._init_ui()
        self._load_credentials()

    # ---- UI setup ----
    def _init_ui(self):
        self.setWindowTitle("OneConnect Data Downloader")
        self.setGeometry(100, 100, 1100, 800)

        # Tab container
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # == Tab 1: Download ==================================================
        download_tab = QWidget()
        self.tabs.addTab(download_tab, "Download")
        layout = QVBoxLayout(download_tab)

        title = QLabel("OneConnect Graph Data Download")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        subtitle = QLabel("Microblock-Cases  |  Dollar General  |  "
                          "One file per case, incremental updates")
        subtitle.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(subtitle)

        # Input area
        layout.addWidget(QLabel(
            "Enter Case IDs (one per line or comma-separated):"))
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText(
            "MY26C019878\nMY25L086318\nMY26B013808")
        self.input_text.setMaximumHeight(90)
        layout.addWidget(self.input_text)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Case IDs")
        self.add_btn.clicked.connect(self._add_case_ids)
        btn_row.addWidget(self.add_btn)

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._clear_cases)
        btn_row.addWidget(self.clear_btn)

        btn_row.addStretch()

        btn_row.addWidget(QLabel("Days of data:"))
        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 90)
        self.days_spin.setValue(90)
        self.days_spin.setToolTip(
            "How far back to download for NEW cases.\n"
            "Existing cases auto-update from last download.")
        btn_row.addWidget(self.days_spin)

        layout.addLayout(btn_row)
        
        # Bulk add by Nomenclature
        model_row = QHBoxLayout()
        self.fetch_models_btn = QPushButton("Fetch Available Models")
        self.fetch_models_btn.clicked.connect(self._fetch_models)
        model_row.addWidget(self.fetch_models_btn)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        model_row.addWidget(self.model_combo)

        self.add_model_btn = QPushButton("Add All Cases")
        self.add_model_btn.clicked.connect(self._add_model_cases)
        self.add_model_btn.setEnabled(False)
        model_row.addWidget(self.add_model_btn)
        
        model_row.addStretch()
        layout.addLayout(model_row)

        # Info label
        info = QLabel(
            "New cases: downloads last N days.  "
            "Existing cases: auto-updates from last download (incremental).  "
            "Store ambient (temp/RH/dewpoint) downloaded automatically.")
        info.setStyleSheet("color:#666; font-size:10px; font-style:italic;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Case table
        layout.addWidget(QLabel("Cases to Download:"))
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Case ID", "Action"])
        self.table.setColumnWidth(0, 400)
        self.table.setMaximumHeight(160)
        layout.addWidget(self.table)

        # Download button row
        dl_row = QHBoxLayout()
        self.download_btn = QPushButton("  Download Data  ")
        self.download_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; "
            "padding: 8px 20px; font-size: 13px;"
        )
        self.download_btn.clicked.connect(self._start_download)
        dl_row.addWidget(self.download_btn)
        dl_row.addStretch()

        self.token_btn = QPushButton("Update Token")
        self.token_btn.setStyleSheet("font-size: 11px;")
        self.token_btn.clicked.connect(self._ask_rta)
        dl_row.addWidget(self.token_btn)

        self.ref_btn = QPushButton("? Quick Ref")
        self.ref_btn.setStyleSheet("font-size: 10px;")
        self.ref_btn.clicked.connect(self._show_quick_ref)
        dl_row.addWidget(self.ref_btn)

        layout.addLayout(dl_row)

        # Progress
        layout.addWidget(QLabel("Progress:"))
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.step_label = QLabel("Ready")
        self.step_label.setStyleSheet("color: #666;")
        layout.addWidget(self.step_label)

        # Status log
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        layout.addWidget(self.status_text)

        # == Tab 2: Diagnostics ================================================
        self.diag_widget = DiagnosticsWidget()
        self.tabs.addTab(self.diag_widget, "Diagnostics")

        # == Tab 3: Data Cruncher =============================================
        self.cruncher_widget = DataCruncherWidget()
        self.tabs.addTab(self.cruncher_widget, "Data Cruncher")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        if index == 1:
            self.diag_widget.refresh_cases()

    # ---- credentials ----
    def _load_credentials(self):
        if RTA_FILE.exists():
            self.rta = RTA_FILE.read_text().strip()
        if TOKEN_FILE.exists():
            self.token = TOKEN_FILE.read_text().strip()

        if self.rta and len(self.rta) > 10:
            self._log("Refreshing auth token...")
            new_token = refresh_token(self.rta)
            if new_token:
                self.token = new_token
                TOKEN_FILE.write_text(new_token)
                self._log("OK  Token refreshed successfully")
                return
            else:
                self._log("Could not refresh token - may need new RTA")

        if not self.rta or len(self.rta) < 10:
            self._ask_rta()

    def _ask_rta(self):
        dlg = TokenInputDialog(self)
        if dlg.exec_() == QDialog.Accepted and dlg.token:
            self.rta = dlg.token
            RTA_FILE.parent.mkdir(parents=True, exist_ok=True)
            RTA_FILE.write_text(self.rta)

            self._log("Testing RTA...")
            new_token = refresh_token(self.rta)
            if new_token:
                self.token = new_token
                TOKEN_FILE.write_text(new_token)
                self._log("OK  RTA is valid! Token saved.")
            else:
                self._log("X  RTA did not work. Please try again.")
                QMessageBox.warning(self, "Error",
                    "Could not get token with that RTA. Please try again.")
        else:
            if not self.token:
                QMessageBox.warning(self, "Warning",
                    "No RTA entered. You will need to set it before downloading.")

    # ---- case ID management ----
    def _add_case_ids(self):
        text = self.input_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Error",
                "Please enter at least one case ID")
            return

        ids = [c.strip().upper()
               for c in text.replace(",", "\n").split("\n") if c.strip()]
        if not ids:
            return

        existing = set()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                existing.add(item.text())

        added = 0
        for cid in ids:
            if cid not in existing:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(cid))
                rm_btn = QPushButton("Remove")
                rm_btn.clicked.connect(
                    lambda _, r=row: self._remove_case(r))
                self.table.setCellWidget(row, 1, rm_btn)
                existing.add(cid)
                added += 1

        self.input_text.clear()
        self._log(f"OK  Added {added} case ID(s)  "
                  f"(total: {self.table.rowCount()})")

    def _remove_case(self, row: int):
        if 0 <= row < self.table.rowCount():
            self.table.removeRow(row)
            for r in range(self.table.rowCount()):
                btn = self.table.cellWidget(r, 1)
                if btn:
                    btn.clicked.disconnect()
                    btn.clicked.connect(
                        lambda _, rr=r: self._remove_case(rr))

    def _clear_cases(self):
        self.table.setRowCount(0)
        self._log("Cleared all cases")

    # ---- bulk model selection ----
    def _fetch_models(self):
        if not self.token:
            self._ask_rta()
            if not self.token:
                return

        self.fetch_models_btn.setEnabled(False)
        self.fetch_models_btn.setText("Fetching...")
        
        self.fetch_thread = QThread()
        self.fetch_worker = FetchModelsWorker(self.token, self.rta)
        self.fetch_worker.moveToThread(self.fetch_thread)
        self.fetch_thread.started.connect(self.fetch_worker.run)
        self.fetch_worker.finished.connect(self._on_models_fetched)
        self.fetch_worker.error.connect(self._on_models_error)
        self.fetch_worker.finished.connect(self.fetch_thread.quit)
        self.fetch_worker.error.connect(self.fetch_thread.quit)
        self.fetch_worker.finished.connect(self.fetch_worker.deleteLater)
        self.fetch_thread.finished.connect(self.fetch_thread.deleteLater)
        self.fetch_thread.start()

    def _on_models_error(self, err: str):
        self.fetch_models_btn.setEnabled(True)
        self.fetch_models_btn.setText("Fetch Available Models")
        QMessageBox.warning(self, "Error", f"Could not fetch models: {err}")

    def _on_models_fetched(self, models: dict):
        self.fetch_models_btn.setEnabled(True)
        self.fetch_models_btn.setText("Fetch Available Models")
        self._models_cache = models
        
        self.model_combo.clear()
        for m_name, serials in sorted(models.items()):
            self.model_combo.addItem(f"{m_name} ({len(serials)} units)", m_name)
            
        if self.model_combo.count() > 0:
            self.add_model_btn.setEnabled(True)
            self._log(f"OK  Fetched {len(models)} models from API.")
        else:
            self._log("Warning: No models found.")

    def _add_model_cases(self):
        if not hasattr(self, '_models_cache') or not self._models_cache:
            return
            
        m_name = self.model_combo.currentData()
        if m_name and m_name in self._models_cache:
            serials = self._models_cache[m_name]
            self.input_text.setPlainText("\n".join(serials))
            self._add_case_ids()

    # ---- quick reference ----
    def _show_quick_ref(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Quick Reference - Browser Console Commands")
        dlg.setFixedSize(600, 400)
        layout = QVBoxLayout(dlg)

        text_area = QTextBrowser()
        text_area.setHtml("""
<html><body style="background:#1a1a2e; color:#e0e0e0;
font-family:Consolas,monospace; font-size:11px; padding:10px;">
<h2 style="color:#42A5F5;">Browser Console Commands</h2>
<p>Copy &amp; paste into browser <b>Console tab</b> (F12).</p>

<h3 style="color:#FFA726;">Get RTA Token</h3>
<p style="background:#16213e; padding:8px; border-radius:4px;
border-left:3px solid #42A5F5;">
<code>localStorage.getItem('RTA')</code></p>

<h3 style="color:#FFA726;">Check Bearer Token</h3>
<p style="background:#16213e; padding:8px; border-radius:4px;
border-left:3px solid #42A5F5;">
<code>localStorage.getItem('TOKEN')</code></p>

<hr style="border-color:#333;"/>
<p style="color:#999; font-size:10px;">
Copy command, paste into Console, press Enter.</p>
</body></html>
""")
        text_area.setStyleSheet(
            "QTextBrowser { background:#1a1a2e; color:#e0e0e0; "
            "font-family:Consolas,monospace; font-size:11px; }"
        )
        layout.addWidget(text_area)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec_()

    # ---- download ----
    def _start_download(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "Error",
                "Please add at least one case ID")
            return

        if not self.rta:
            QMessageBox.warning(self, "Error",
                "No Refresh Token set. Click 'Update Token' first.")
            return

        case_ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                case_ids.append(item.text())

        self.download_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.status_text.clear()
        self.progress_bar.setValue(0)

        days = self.days_spin.value()
        self.worker = DownloadWorker(case_ids, self.token, self.rta, days)
        self.download_thread = QThread()
        self.worker.moveToThread(self.download_thread)

        self.worker.progress.connect(self._log)
        self.worker.case_progress.connect(self._update_progress)
        self.worker.step_progress.connect(self._update_step)
        self.worker.finished.connect(self._download_finished)
        self.download_thread.started.connect(self.worker.run)

        self._log(f"Starting download for {len(case_ids)} case(s), "
                  f"last {days} days (existing cases update incrementally)...")
        self.download_thread.start()

    def _download_finished(self, success: bool, message: str):
        self.download_thread.quit()
        self.download_thread.wait()
        self.download_btn.setEnabled(True)
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.step_label.setText("Done")

        if success:
            QMessageBox.information(self, "Success",
                "All cases downloaded successfully!")
        else:
            QMessageBox.warning(self, "Completed with Errors",
                "Some cases failed. Check the log.")

    # ---- UI helpers ----
    def _log(self, msg: str):
        self.status_text.append(msg)
        sb = self.status_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_progress(self, current: int, total: int):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def _update_step(self, step: str):
        self.step_label.setText(step)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    window = OneConnectApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
