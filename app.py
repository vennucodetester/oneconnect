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
    QProgressBar, QMessageBox, QInputDialog, QSpinBox, QTabWidget,
    QDialog, QLineEdit, QDialogButtonBox, QTextBrowser
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread
from PyQt5.QtGui import QFont
try:
    from PyQt5.QtWidgets import QApplication
    _clipboard = QApplication.clipboard
except:
    _clipboard = None

from diagnostics import DiagnosticsWidget


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

        # Instructions
        instr = QLabel(
            "Your RTA token has expired. Get a fresh one:\n\n"
            "1. Go to: mc.us.oneconnect.net\n"
            "2. Press F12 to open DevTools\n"
            "3. Click the Console tab\n"
            "4. Copy and paste this command:"
        )
        instr.setStyleSheet("font-size:11px; line-height:1.6;")
        layout.addWidget(instr)

        # Command box (highlighted, selectable, easy to copy)
        cmd_box = QTextEdit()
        cmd_box.setPlainText("localStorage.getItem('RTA')")
        cmd_box.setReadOnly(True)
        cmd_box.setMaximumHeight(50)
        cmd_box.setStyleSheet(
            "QTextEdit { background:#2a2a4e; color:#42A5F5; font-family:Consolas,monospace; "
            "font-size:13px; padding:8px; border:1px solid #42A5F5; border-radius:4px; }"
        )
        layout.addWidget(cmd_box)

        # Copy button
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

        # Paste result
        paste_label = QLabel("5. Paste the result here:")
        paste_label.setStyleSheet("font-size:11px; font-weight:bold;")
        layout.addWidget(paste_label)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Paste your RTA token here (long string starting with 5c227...)")
        self.input.setEchoMode(QLineEdit.Password)  # Hide for privacy
        self.input.setMinimumHeight(40)
        layout.addWidget(self.input)

        # Help text
        help_text = QLabel(
            "The token looks like:  5c227fcf-369b-451d-b5ad-537c4d745a32\n"
            "This only needs to be done once."
        )
        help_text.setStyleSheet("font-size:10px; color:#888;")
        layout.addWidget(help_text)

        # Dialog buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _copy_to_clipboard(self, text: str):
        """Copy text to system clipboard."""
        try:
            # Get clipboard from QApplication
            app = QApplication.instance()
            if app:
                cb = app.clipboard()
                cb.setText(text)
                # Visual feedback
                self.input.setPlaceholderText("✓ Command copied to clipboard — paste it in the browser console")
        except Exception:
            pass

    def _accept(self):
        """Validate and accept."""
        token = self.input.text().strip()
        if len(token) < 10:
            QMessageBox.warning(self, "Invalid", "Please paste the token from the browser console.")
            return
        self.token = token
        self.accept()


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
        """Auto-refresh the token. Raises if RTA is expired."""
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
                "  2. F12 → Console\n"
                "  3. Run: localStorage.getItem('RTA')\n"
                "  4. Click 'Update Token' in the app and paste it"
            )

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
                                # Excel sheet names cannot contain \ / * [ ] : ?
                                import re
                                sheet = re.sub(r'[\\\\/*\[\]:?]', '-', sheet)
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
        self.setGeometry(100, 100, 1100, 800)

        # Tab container
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # ── Tab 1: Download ───────────────────────────────────────────
        download_tab = QWidget()
        self.tabs.addTab(download_tab, "📥  Download")
        layout = QVBoxLayout(download_tab)

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

        # ── Tab 2: Diagnostics ────────────────────────────────────────
        self.diag_widget = DiagnosticsWidget()
        self.tabs.addTab(self.diag_widget, "🔍  Diagnostics")

        # Refresh diagnostics case list after a download completes
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index):
        """Refresh the case list when switching to Diagnostics tab."""
        if index == 1:
            self.diag_widget.refresh_cases()

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
        """Ask user for their Refresh Token (RTA) with custom dialog."""
        dlg = TokenInputDialog(self)
        if dlg.exec_() == QDialog.Accepted and dlg.token:
            self.rta = dlg.token
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

    # ---- quick reference ----
    def _show_quick_ref(self):
        """Show all browser console commands user might need."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Quick Reference - Browser Console Commands")
        dlg.setFixedSize(600, 400)

        layout = QVBoxLayout(dlg)

        text_area = QTextBrowser()
        text_area.setHtml("""
<html><body style="background:#1a1a2e; color:#e0e0e0; font-family:Consolas,monospace; font-size:11px; padding:10px;">
<h2 style="color:#42A5F5;">Browser Console Commands</h2>
<p>Copy &amp; paste these into your browser's <b>Console tab</b> (F12 → Console).</p>

<h3 style="color:#FFA726;">Get RTA Token (One-Time Setup)</h3>
<p style="background:#16213e; padding:8px; border-radius:4px; border-left:3px solid #42A5F5;">
<code>localStorage.getItem('RTA')</code>
</p>
<p><b>When:</b> First time using the app, or when "Update Token" asks for it<br/>
<b>Result:</b> Token like <code>5c227fcf-369b-...</code></p>

<h3 style="color:#FFA726;">Check Current Bearer Token</h3>
<p style="background:#16213e; padding:8px; border-radius:4px; border-left:3px solid #42A5F5;">
<code>localStorage.getItem('TOKEN')</code>
</p>
<p><b>When:</b> If you need to verify the app's current session token</p>

<h3 style="color:#FFA726;">Check All Auth Values</h3>
<p style="background:#16213e; padding:8px; border-radius:4px; border-left:3px solid #42A5F5;">
<code>console.table(Object.keys(localStorage).filter(k => k.includes('TOKEN') || k.includes('RTA')))</code>
</p>
<p><b>When:</b> To see all auth data in storage</p>

<hr style="border-color:#333;"/>
<p style="color:#999; font-size:10px;">Copy each command, paste into Console, press Enter.</p>
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
