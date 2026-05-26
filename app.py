#!/usr/bin/env python3
"""
OneConnect Data Download App - Phase 1
Desktop GUI application to download graph data from StoreConnect Pulse
For Microblock-Cases on Dollar General profile
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QLabel,
    QProgressBar, QMessageBox, QInputDialog, QSpinBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QFont


# ---------------------------------------------------------------------------
#  API Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://api.us.oneconnect.net/oneconnect-api"
PROJECT_ID = 136        # Hussmann OneConnect
TENANT_ID = 37          # Dollar General
DATA_DIR = Path("C:\\Users\\silam\\OneC\\downloads")
TOKEN_FILE = Path("C:\\Users\\silam\\OneC\\token.txt")
RTA_FILE = Path("C:\\Users\\silam\\OneC\\rta.txt")

# Microblock telemetry attributes
# sensor-data = numeric (temperatures), sensor-event = boolean (status flags)
CASE_STATUS_ATTRS = [
    {"attribute": "Control Temperature", "serviceType": "Microblock-sensor-data"},
    {"attribute": "Defrost Terminate",   "serviceType": "Microblock-sensor-data"},
    {"attribute": "Defrost Status",      "serviceType": "Microblock-sensor-data"},
    {"attribute": "Compressor Discharge Temp", "serviceType": "Microblock-sensor-data"},
    {"attribute": "Alarm",               "serviceType": "Microblock-sensor-event"},
    {"attribute": "Refrigeration DO",    "serviceType": "Microblock-sensor-event"},
    {"attribute": "Setpoint",            "serviceType": "Microblock-sensor-event"},
    {"attribute": "Evap Fan DO",         "serviceType": "Microblock-sensor-event"},
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
                return auth[7:]  # strip "Bearer " prefix
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
#  Download Worker (runs in background thread)
# ---------------------------------------------------------------------------

class DownloadWorker(QObject):
    """Worker thread for downloading data"""
    progress = pyqtSignal(str)          # Status message
    case_progress = pyqtSignal(int, int)  # current, total
    step_progress = pyqtSignal(str)     # Current step description
    finished = pyqtSignal(bool, str)    # success, summary

    def __init__(self, case_ids: List[str], token: str, rta: str, hours: int = 24):
        super().__init__()
        self.case_ids = case_ids
        self.token = token
        self.rta = rta
        self.hours = hours

    # ---- token refresh ----
    def _ensure_token(self):
        """Auto-refresh the token if needed."""
        new = refresh_token(self.rta)
        if new:
            self.token = new
            # persist for next run
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(new)

    # ---- asset lookup ----
    def _find_asset(self, serial: str) -> Optional[dict]:
        """Find asset by serial number. Returns {id, serialNumber, ...}."""
        url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets"
        params = {"pageNumber": 0, "pageSize": 5, "search": serial}
        resp = requests.get(url, headers=get_headers(self.token), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for asset in data:
            if asset.get("serialNumber") == serial:
                return asset
        return None

    # ---- module discovery ----
    def _get_modules(self, asset_id: int) -> List[dict]:
        """Get module placeholders for an asset."""
        url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets/{asset_id}"
        resp = requests.get(url, headers=get_headers(self.token), timeout=15)
        resp.raise_for_status()
        return resp.json().get("modulePlaceholders", [])

    # ---- telemetry query ----
    def _query_telemetry(self, external_id: str, service_type: str,
                         attrs: List[dict], hours: int) -> pd.DataFrame:
        """
        Query telemetry data for one module, one service type.
        attrs: list of {"attribute": "Control Temperature", ...}
        Returns DataFrame with timestamp + value columns.
        """
        url = f"{BASE_URL}/projects/{PROJECT_ID}/telemetry/v1/telemetry-attributes:query"

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)

        agg_attrs = []
        for a in attrs:
            agg_attrs.append({
                "attribute": a["attribute"],
                "id": 0,
                "name": a["attribute"],
                "legend": f"{a['attribute']} - LTTB",
                "aggregation": "LTTB",
            })

        payload = {
            "serviceType": service_type,
            "aggregatedAttributes": agg_attrs,
            "searchSpan": {
                "from": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "to":   now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
            "timeSeriesId": {"assetExternalId": external_id},
            "withStep": True,
            "pageSize": 2000,
        }

        # event service type needs extra fields
        if "event" in service_type:
            payload["lookupBeforeStart"] = True
            payload["sortDirection"] = "ASC"

        resp = requests.post(url, json=payload,
                             headers=get_headers(self.token), timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("data", [])

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df = df.sort_values("timestamp")
        return df

    # ---- main loop ----
    def run(self):
        try:
            # Refresh token at start
            self.step_progress.emit("Refreshing auth token...")
            self._ensure_token()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            successful = []
            failed = []

            for idx, case_id in enumerate(self.case_ids, 1):
                self.case_progress.emit(idx, len(self.case_ids))
                self.progress.emit(f"\nProcessing case {idx}/{len(self.case_ids)}: {case_id}")

                try:
                    # Step 1: Find asset
                    self.step_progress.emit(f"Looking up {case_id}...")
                    asset = self._find_asset(case_id)
                    if not asset:
                        self.progress.emit(f"  X Asset {case_id} not found")
                        failed.append(case_id)
                        continue

                    asset_id = asset["id"]
                    self.progress.emit(f"  Found asset (internal ID: {asset_id})")

                    # Step 2: Get modules
                    self.step_progress.emit(f"Getting modules for {case_id}...")
                    modules = self._get_modules(asset_id)
                    if not modules:
                        self.progress.emit(f"  X No modules found for {case_id}")
                        failed.append(case_id)
                        continue

                    self.progress.emit(f"  Found {len(modules)} module(s)")

                    all_sheets = {}

                    # Step 3: For each module, download telemetry
                    for m_idx, module in enumerate(modules, 1):
                        mod_name = module.get("name", f"Module_{m_idx}")
                        ext_id = module.get("externalId")
                        if not ext_id:
                            self.progress.emit(f"  X Module {mod_name} has no externalId, skipping")
                            continue

                        self.progress.emit(f"  Module {m_idx}/{len(modules)}: {mod_name}")

                        # Group attributes by service type
                        by_service = {}
                        for attr_def in CASE_STATUS_ATTRS:
                            st = attr_def["serviceType"]
                            by_service.setdefault(st, []).append(attr_def)

                        for svc_type, attrs in by_service.items():
                            svc_label = "sensor-data" if "data" in svc_type else "sensor-event"
                            self.step_progress.emit(
                                f"{mod_name}: Downloading {svc_label}..."
                            )
                            self.progress.emit(f"    Querying {svc_label} ({len(attrs)} attributes)...")

                            df = self._query_telemetry(ext_id, svc_type, attrs, self.hours)

                            if len(df) > 0:
                                sheet = f"{mod_name}_{svc_label}"
                                # Excel sheet names max 31 chars
                                if len(sheet) > 31:
                                    sheet = sheet[:31]
                                all_sheets[sheet] = df
                                self.progress.emit(f"    OK  {len(df)} rows for {svc_label}")
                            else:
                                self.progress.emit(f"    --  No {svc_label} data")

                    # Step 4: Save to Excel
                    if all_sheets:
                        self.step_progress.emit(f"Saving {case_id} to Excel...")
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        out_file = DATA_DIR / f"{case_id}_{ts}.xlsx"
                        with pd.ExcelWriter(out_file, engine="openpyxl") as writer:
                            for sheet_name, df in all_sheets.items():
                                df.to_excel(writer, sheet_name=sheet_name, index=False)
                        self.progress.emit(f"  OK  Saved: {out_file.name}")
                        successful.append(case_id)
                    else:
                        self.progress.emit(f"  X No data to save for {case_id}")
                        failed.append(case_id)

                except Exception as e:
                    self.progress.emit(f"  X ERROR on {case_id}: {e}")
                    failed.append(case_id)

            # Summary
            sep = "=" * 50
            summary = f"\n{sep}\nDownload Complete!\n{sep}\n"
            summary += f"Successful: {len(successful)}\n"
            for c in successful:
                summary += f"  OK  {c}\n"
            if failed:
                summary += f"\nFailed: {len(failed)}\n"
                for c in failed:
                    summary += f"  X  {c}\n"
            summary += f"\nFiles saved to: {DATA_DIR}\n"

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
        self.setGeometry(100, 100, 900, 750)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Title
        title = QLabel("OneConnect Graph Data Download")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        subtitle = QLabel("Microblock-Cases  |  Dollar General")
        subtitle.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(subtitle)

        # Input area
        layout.addWidget(QLabel("Enter Case IDs (one per line or comma-separated):"))
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("MY26C019878\nMY25L086318\nMY26B013808")
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

        btn_row.addWidget(QLabel("Hours of data:"))
        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(1, 168)   # 1 hour to 7 days
        self.hours_spin.setValue(24)
        btn_row.addWidget(self.hours_spin)

        layout.addLayout(btn_row)

        # Case table
        layout.addWidget(QLabel("Cases to Download:"))
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Case ID", "Action"])
        self.table.setColumnWidth(0, 400)
        self.table.setMaximumHeight(160)
        layout.addWidget(self.table)

        # Download button
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

    # ---- credentials ----
    def _load_credentials(self):
        """Load saved RTA and token."""
        if RTA_FILE.exists():
            self.rta = RTA_FILE.read_text().strip()

        if TOKEN_FILE.exists():
            self.token = TOKEN_FILE.read_text().strip()

        if self.rta and len(self.rta) > 10:
            # Try to get a fresh token
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
        """Ask user for their Refresh Token (RTA)."""
        msg = (
            "To get your Refresh Token (one-time setup):\n\n"
            "1. Open https://mc.us.oneconnect.net in your browser\n"
            "2. Press F12 (open DevTools)\n"
            "3. Click the Console tab\n"
            "4. Type this and press Enter:\n"
            "       localStorage.getItem('RTA')\n"
            "5. Copy the value shown (looks like: 5c227fcf-369b-...)\n\n"
            "This only needs to be done once. The app will auto-refresh\n"
            "your session token using this."
        )
        rta, ok = QInputDialog.getText(self, "Enter Refresh Token (RTA)", msg)

        if ok and rta and len(rta.strip()) > 10:
            self.rta = rta.strip()
            RTA_FILE.parent.mkdir(parents=True, exist_ok=True)
            RTA_FILE.write_text(self.rta)

            # Immediately get a fresh token
            self._log("Testing RTA...")
            new_token = refresh_token(self.rta)
            if new_token:
                self.token = new_token
                TOKEN_FILE.write_text(new_token)
                self._log("OK  RTA is valid! Token saved.")
            else:
                self._log("X  RTA did not work. Please try again.")
                QMessageBox.warning(self, "Error", "Could not get token with that RTA. Please try again.")
        else:
            if not self.token:
                QMessageBox.warning(self, "Warning",
                    "No RTA entered. You will need to set it before downloading.")

    # ---- case ID management ----
    def _add_case_ids(self):
        text = self.input_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Error", "Please enter at least one case ID")
            return

        ids = [c.strip().upper() for c in text.replace(",", "\n").split("\n") if c.strip()]
        if not ids:
            return

        # Check for duplicates
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
                rm_btn.clicked.connect(lambda _, r=row: self._remove_case(r))
                self.table.setCellWidget(row, 1, rm_btn)
                existing.add(cid)
                added += 1

        self.input_text.clear()
        self._log(f"OK  Added {added} case ID(s)  (total: {self.table.rowCount()})")

    def _remove_case(self, row: int):
        if 0 <= row < self.table.rowCount():
            self.table.removeRow(row)
            # Reconnect remaining remove buttons
            for r in range(self.table.rowCount()):
                btn = self.table.cellWidget(r, 1)
                if btn:
                    btn.clicked.disconnect()
                    btn.clicked.connect(lambda _, rr=r: self._remove_case(rr))

    def _clear_cases(self):
        self.table.setRowCount(0)
        self._log("Cleared all cases")

    # ---- download ----
    def _start_download(self):
        if self.table.rowCount() == 0:
            QMessageBox.warning(self, "Error", "Please add at least one case ID")
            return

        if not self.rta:
            QMessageBox.warning(self, "Error",
                "No Refresh Token set. Click 'Update Token' first.")
            return

        # Collect case IDs
        case_ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                case_ids.append(item.text())

        # Disable controls
        self.download_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

        self.status_text.clear()
        self.progress_bar.setValue(0)

        # Create worker thread
        hours = self.hours_spin.value()
        self.worker = DownloadWorker(case_ids, self.token, self.rta, hours)
        self.download_thread = QThread()
        self.worker.moveToThread(self.download_thread)

        self.worker.progress.connect(self._log)
        self.worker.case_progress.connect(self._update_progress)
        self.worker.step_progress.connect(self._update_step)
        self.worker.finished.connect(self._download_finished)
        self.download_thread.started.connect(self.worker.run)

        self._log(f"Starting download for {len(case_ids)} case(s), last {hours} hours...")
        self.download_thread.start()

    def _download_finished(self, success: bool, message: str):
        self.download_thread.quit()
        self.download_thread.wait()

        self.download_btn.setEnabled(True)
        self.add_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)
        self.step_label.setText("Done")

        if success:
            QMessageBox.information(self, "Success", "All cases downloaded successfully!")
        else:
            QMessageBox.warning(self, "Completed with Errors", "Some cases failed. Check the log.")

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
