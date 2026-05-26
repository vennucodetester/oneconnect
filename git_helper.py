"""
Git Helper v2 — Simple Git GUI with built-in instructions.
Pull, Stage All, Commit, Push — that's it for daily use.
Auto-sets up new repos when you paste a GitHub link.
"""
import sys
import os
import subprocess
import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QGroupBox, QPushButton, QLineEdit, QPlainTextEdit,
    QTreeWidget, QTreeWidgetItem, QLabel, QDialog, QFileDialog,
    QMessageBox, QInputDialog, QTextEdit,
)
from PyQt6.QtGui import QFont, QColor, QBrush, QPalette
from PyQt6.QtCore import Qt


# ============================================================================
# HOW TO USE — always-visible instructions
# ============================================================================

INSTRUCTIONS = """\
HOW TO USE THIS APP

DAILY WORKFLOW (do these in order):
  1. Click [Pull] — gets the latest files from GitHub
  2. Do your work (edit files, run your app, etc.)
  3. Click [Stage All] — prepares all your changes
  4. Type a short note in the commit box
     (e.g. "updated log file")
  5. Click [Commit] — saves your changes locally
  6. Click [Push] — sends everything to GitHub

FIRST TIME SETUP (new project folder):
  1. Put this app (GitHelper.exe) in your project folder
  2. Open the app — it will ask for your GitHub link
  3. Paste the link
     (e.g. https://github.com/you/project.git)
  4. Click OK — done. Everything is set up.

THAT'S IT. The buttons below the line are advanced
and you can ignore them.
"""


# ============================================================================
# GIT RUNNER — subprocess wrapper for git CLI
# ============================================================================

class GitRunner:
    """Wraps all git operations via subprocess. Returns (success, output) tuples."""

    def __init__(self, repo_path):
        self.repo_path = repo_path

    def _run(self, args, timeout=30):
        """Run a git command and return (success: bool, output: str)."""
        try:
            kwargs = dict(
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(["git"] + args, **kwargs)
            output = (result.stdout or "") + (result.stderr or "")
            return (result.returncode == 0, output.strip())
        except subprocess.TimeoutExpired:
            return (False, "Command timed out. If push/pull needs auth, run git from terminal first.")
        except FileNotFoundError:
            return (False, "git is not installed or not in PATH.")

    def is_git_repo(self):
        ok, _ = self._run(["rev-parse", "--is-inside-work-tree"])
        return ok

    # --- Daily operations ---

    def pull(self):
        return self._run(["pull"], timeout=60)

    def push(self):
        return self._run(["push"], timeout=60)

    def stage_all(self):
        return self._run(["add", "-A"])

    def stage_files(self, files):
        return self._run(["add", "--"] + files)

    def unstage_files(self, files):
        return self._run(["restore", "--staged", "--"] + files)

    def commit(self, message):
        return self._run(["commit", "-m", message])

    def status(self):
        return self._run(["status", "--porcelain=v1"])

    def current_branch(self):
        return self._run(["rev-parse", "--abbrev-ref", "HEAD"])

    def branch_info(self):
        return self._run(["branch", "-vv"])

    def log(self, count=20):
        return self._run(["log", "--oneline", f"-n{count}"])

    # --- First-time setup ---

    def init(self):
        return self._run(["init"])

    def add_remote(self, url):
        return self._run(["remote", "add", "origin", url])

    def set_branch_main(self):
        return self._run(["branch", "-M", "main"])

    def push_first(self):
        return self._run(["push", "-u", "origin", "main"], timeout=60)

    # --- Advanced (hidden by default) ---

    def diff_file(self, filepath):
        return self._run(["diff", "--", filepath])

    def diff_staged_file(self, filepath):
        return self._run(["diff", "--cached", "--", filepath])

    def stash_save(self, message=""):
        args = ["stash", "push"]
        if message:
            args += ["-m", message]
        return self._run(args)

    def stash_list(self):
        return self._run(["stash", "list"])

    def stash_pop(self):
        return self._run(["stash", "pop"])

    def create_tag(self, name, message=""):
        args = ["tag", "-a", name]
        if message:
            args += ["-m", message]
        else:
            args += ["-m", name]
        return self._run(args)

    def list_tags(self):
        return self._run(["tag", "--sort=-creatordate"])

    def push_tags(self):
        return self._run(["push", "--tags"], timeout=60)


# ============================================================================
# DIFF DIALOG — modal file diff viewer
# ============================================================================

class DiffDialog(QDialog):
    def __init__(self, filename, diff_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Diff: {filename}")
        self.resize(750, 520)
        layout = QVBoxLayout(self)
        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 10))
        text_edit.setPlainText(diff_text)
        layout.addWidget(text_edit)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)


# ============================================================================
# MAIN WINDOW — Simplified Git Helper
# ============================================================================

class GitHelperWindow(QMainWindow):
    def __init__(self, repo_path):
        super().__init__()
        self.repo_path = repo_path
        self.git = GitRunner(repo_path)
        self.setWindowTitle(f"Git Helper — {os.path.basename(repo_path)}")
        self.resize(1000, 680)
        self._build_ui()
        self.refresh_status()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        # --- Top bar: branch info + refresh ---
        top_bar = QHBoxLayout()
        self.branch_label = QLabel("Branch: ...")
        self.branch_label.setFont(QFont("Consolas", 10))
        top_bar.addWidget(self.branch_label)
        top_bar.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_status)
        top_bar.addWidget(btn_refresh)
        main_layout.addLayout(top_bar)

        # --- Splitter: left (actions) | right (instructions + output) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # ============ LEFT PANEL ============
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)

        # --- Main buttons (the daily workflow) ---
        grp_main = QGroupBox("Daily Workflow")
        main_btns_lay = QVBoxLayout(grp_main)

        row_remote = QHBoxLayout()
        self.btn_pull = QPushButton("1. Pull")
        self.btn_push = QPushButton("6. Push")
        self.btn_pull.clicked.connect(self.do_pull)
        self.btn_push.clicked.connect(self.do_push)
        self.btn_pull.setMinimumHeight(32)
        self.btn_push.setMinimumHeight(32)
        row_remote.addWidget(self.btn_pull)
        row_remote.addWidget(self.btn_push)
        main_btns_lay.addLayout(row_remote)

        # File list
        self.file_list = QTreeWidget()
        self.file_list.setHeaderLabels(["Status", "File"])
        self.file_list.setColumnWidth(0, 50)
        self.file_list.setFont(QFont("Consolas", 9))
        self.file_list.setRootIsDecorated(False)
        self.file_list.setMaximumHeight(180)
        main_btns_lay.addWidget(self.file_list)

        # Stage All button (the main one)
        self.btn_stage_all = QPushButton("3. Stage All")
        self.btn_stage_all.setMinimumHeight(32)
        self.btn_stage_all.clicked.connect(self.do_stage_all)
        main_btns_lay.addWidget(self.btn_stage_all)

        # Commit row
        self.commit_msg = QLineEdit()
        self.commit_msg.setPlaceholderText("4. Type a short note here...")
        main_btns_lay.addWidget(self.commit_msg)
        self.btn_commit = QPushButton("5. Commit")
        self.btn_commit.setMinimumHeight(32)
        self.btn_commit.clicked.connect(self.do_commit)
        self.commit_msg.textChanged.connect(
            lambda txt: self.btn_commit.setEnabled(bool(txt.strip()))
        )
        self.btn_commit.setEnabled(False)
        main_btns_lay.addWidget(self.btn_commit)

        left_layout.addWidget(grp_main)

        # --- Advanced section (collapsed by default) ---
        self.btn_show_advanced = QPushButton("Show Advanced Options...")
        self.btn_show_advanced.setStyleSheet("color: #666; font-size: 9pt;")
        self.btn_show_advanced.clicked.connect(self._toggle_advanced)
        left_layout.addWidget(self.btn_show_advanced)

        self.grp_advanced = QGroupBox("Advanced")
        adv_lay = QVBoxLayout(self.grp_advanced)

        row_adv1 = QHBoxLayout()
        btn_stage_sel = QPushButton("Stage Selected")
        btn_unstage = QPushButton("Unstage")
        btn_log = QPushButton("View Log")
        btn_stage_sel.clicked.connect(self.do_stage_selected)
        btn_unstage.clicked.connect(self.do_unstage_selected)
        btn_log.clicked.connect(self.do_view_log)
        row_adv1.addWidget(btn_stage_sel)
        row_adv1.addWidget(btn_unstage)
        row_adv1.addWidget(btn_log)
        adv_lay.addLayout(row_adv1)

        row_adv2 = QHBoxLayout()
        btn_diff = QPushButton("Diff")
        btn_stash = QPushButton("Stash")
        btn_stash_pop = QPushButton("Pop Stash")
        btn_diff.clicked.connect(self.do_diff_selected)
        btn_stash.clicked.connect(self.do_stash_save)
        btn_stash_pop.clicked.connect(self.do_stash_pop)
        row_adv2.addWidget(btn_diff)
        row_adv2.addWidget(btn_stash)
        row_adv2.addWidget(btn_stash_pop)
        adv_lay.addLayout(row_adv2)

        row_adv3 = QHBoxLayout()
        btn_tag = QPushButton("Create Tag")
        btn_push_tags = QPushButton("Push Tags")
        btn_tag.clicked.connect(self.do_create_tag)
        btn_push_tags.clicked.connect(self.do_push_tags)
        row_adv3.addWidget(btn_tag)
        row_adv3.addWidget(btn_push_tags)
        adv_lay.addLayout(row_adv3)

        self.grp_advanced.setVisible(False)
        left_layout.addWidget(self.grp_advanced)

        left_layout.addStretch()

        # ============ RIGHT PANEL — instructions (top) + output (bottom) ============
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)

        # Instructions (always visible)
        instructions_box = QTextEdit()
        instructions_box.setReadOnly(True)
        instructions_box.setFont(QFont("Consolas", 9))
        instructions_box.setPlainText(INSTRUCTIONS)
        instructions_box.setMaximumHeight(320)
        instructions_box.setStyleSheet(
            "background-color: #fffff0; border: 1px solid #ddd; padding: 8px;"
        )
        right_layout.addWidget(instructions_box)

        # Output pane (below instructions)
        out_label = QLabel("Output:")
        out_label.setFont(QFont("Consolas", 9, weight=QFont.Weight.Bold))
        right_layout.addWidget(out_label)
        self.output_pane = QPlainTextEdit()
        self.output_pane.setReadOnly(True)
        self.output_pane.setFont(QFont("Consolas", 9))
        right_layout.addWidget(self.output_pane)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([380, 580])

        # Status bar
        self.statusBar().showMessage(f"Repo: {self.repo_path}")

        # Stylesheet
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold; border: 1px solid #ccc; border-radius: 4px;
                margin-top: 8px; padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }
            QPushButton {
                padding: 5px 12px; border: 1px solid #ccc; border-radius: 3px;
                background: #f5f5f5;
            }
            QPushButton:hover { background: #e0e0e0; }
            QPushButton:pressed { background: #d0d0d0; }
            QPushButton:disabled { color: #aaa; }
        """)

    # ------------------------------------------------------------------
    # Toggle advanced section
    # ------------------------------------------------------------------

    def _toggle_advanced(self):
        visible = not self.grp_advanced.isVisible()
        self.grp_advanced.setVisible(visible)
        self.btn_show_advanced.setText(
            "Hide Advanced Options" if visible else "Show Advanced Options..."
        )

    # ------------------------------------------------------------------
    # Helper: run a git op, display output, refresh
    # ------------------------------------------------------------------

    def _run_and_display(self, cmd_label, runner_fn):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.output_pane.appendPlainText(f"\n>>> git {cmd_label}  [{ts}]")
        success, output = runner_fn()
        self.output_pane.appendPlainText(output if output else "(no output)")
        if success:
            self.statusBar().showMessage(f"OK: git {cmd_label}", 5000)
        else:
            self.statusBar().showMessage(f"FAILED: git {cmd_label}", 5000)
            if "CONFLICT" in output:
                QMessageBox.warning(
                    self, "Merge Conflict",
                    "Merge conflicts detected. Resolve them manually, then stage and commit."
                )
        self.refresh_status()

    def _get_checked_files(self):
        files = []
        for i in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                files.append(item.text(1))
        return files

    # ------------------------------------------------------------------
    # Refresh file list + branch info
    # ------------------------------------------------------------------

    def refresh_status(self):
        self.file_list.clear()
        ok, branch = self.git.current_branch()
        if ok:
            ok2, detail = self.git.branch_info()
            tracking = ""
            if ok2:
                for line in detail.splitlines():
                    if line.startswith("*"):
                        tracking = line[2:].strip()
                        break
            self.branch_label.setText(f"Branch: {tracking if tracking else branch.strip()}")

        ok, raw = self.git.status()
        if not ok:
            return
        for line in raw.splitlines():
            if len(line) < 4:
                continue
            idx_st = line[0]
            wrk_st = line[1]
            filepath = line[3:]
            display = f"{idx_st}{wrk_st}".strip()
            item = QTreeWidgetItem([display, filepath])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            color_map = {
                "M": QColor(200, 120, 0),
                "A": QColor(0, 150, 0),
                "D": QColor(200, 0, 0),
                "?": QColor(128, 128, 128),
                "R": QColor(0, 100, 200),
                "U": QColor(180, 0, 180),
            }
            dominant = idx_st if idx_st != " " else wrk_st
            c = color_map.get(dominant)
            if c:
                item.setForeground(0, QBrush(c))
                item.setForeground(1, QBrush(c))
            self.file_list.addTopLevelItem(item)

    # ------------------------------------------------------------------
    # Slot methods — daily workflow
    # ------------------------------------------------------------------

    def do_pull(self):
        self._run_and_display("pull", self.git.pull)

    def do_push(self):
        self._run_and_display("push", self.git.push)

    def do_stage_all(self):
        self._run_and_display("add -A", self.git.stage_all)

    def do_commit(self):
        msg = self.commit_msg.text().strip()
        if not msg:
            QMessageBox.warning(self, "Commit", "Please enter a commit message.")
            return
        self._run_and_display(f'commit -m "{msg}"', lambda: self.git.commit(msg))
        self.commit_msg.clear()

    # ------------------------------------------------------------------
    # Slot methods — advanced
    # ------------------------------------------------------------------

    def do_stage_selected(self):
        files = self._get_checked_files()
        if not files:
            self.statusBar().showMessage("No files selected", 3000)
            return
        self._run_and_display(
            f"add -- {' '.join(files)}",
            lambda: self.git.stage_files(files),
        )

    def do_unstage_selected(self):
        files = self._get_checked_files()
        if not files:
            self.statusBar().showMessage("No files selected", 3000)
            return
        self._run_and_display(
            f"restore --staged -- {' '.join(files)}",
            lambda: self.git.unstage_files(files),
        )

    def do_view_log(self):
        self._run_and_display("log --oneline -20", lambda: self.git.log(20))

    def do_diff_selected(self):
        files = self._get_checked_files()
        if not files:
            QMessageBox.information(self, "Diff", "Select a file first (checkbox).")
            return
        for f in files:
            ok, diff_text = self.git.diff_file(f)
            if not diff_text.strip():
                ok, diff_text = self.git.diff_staged_file(f)
            if not diff_text.strip():
                diff_text = "(No changes or file is untracked)"
            dlg = DiffDialog(f, diff_text, self)
            dlg.exec()

    def do_stash_save(self):
        msg, ok = QInputDialog.getText(self, "Stash", "Stash message (optional):")
        if ok:
            self._run_and_display(
                f'stash push -m "{msg}"',
                lambda: self.git.stash_save(msg),
            )

    def do_stash_pop(self):
        self._run_and_display("stash pop", self.git.stash_pop)

    def do_create_tag(self):
        tag_name, ok = QInputDialog.getText(self, "Create Tag", "Tag name (e.g. v3.2.7):")
        if not ok or not tag_name.strip():
            return
        tag_msg, ok2 = QInputDialog.getText(self, "Tag Message", "Tag message:")
        if ok2:
            self._run_and_display(
                f'tag -a {tag_name.strip()} -m "{tag_msg}"',
                lambda: self.git.create_tag(tag_name.strip(), tag_msg),
            )

    def do_push_tags(self):
        self._run_and_display("push --tags", self.git.push_tags)


# ============================================================================
# REPO DETECTION + AUTO-SETUP
# ============================================================================

def detect_or_setup_repo():
    """
    Find a git repo in script dir or cwd.
    If none found: ask for GitHub URL and set everything up automatically.
    """
    runner = GitRunner("")
    candidates = [
        os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, 'frozen', False) else __file__)),
        os.getcwd(),
    ]
    for path in candidates:
        runner.repo_path = path
        if runner.is_git_repo():
            return path

    # No git repo found — offer to set one up
    # Use the first candidate (the folder the exe/script is in)
    target_folder = candidates[0] if os.path.isdir(candidates[0]) else candidates[1]

    url, ok = QInputDialog.getText(
        None,
        "Set Up New Project",
        f"No git repo found in:\n{target_folder}\n\n"
        "Paste your GitHub link below to set it up:\n"
        "(e.g. https://github.com/yourname/project.git)",
    )
    if not ok or not url.strip():
        return ""

    url = url.strip()
    runner.repo_path = target_folder
    results = []

    # Step 1: git init
    ok1, out1 = runner.init()
    results.append(f"git init: {'OK' if ok1 else 'FAILED'}\n{out1}")

    # Step 2: git remote add origin <url>
    ok2, out2 = runner.add_remote(url)
    results.append(f"git remote add origin: {'OK' if ok2 else 'FAILED'}\n{out2}")

    # Step 3: git add -A
    ok3, out3 = runner.stage_all()
    results.append(f"git add -A: {'OK' if ok3 else 'FAILED'}\n{out3}")

    # Step 4: git commit -m "Initial commit"
    ok4, out4 = runner.commit("Initial commit")
    results.append(f"git commit: {'OK' if ok4 else 'FAILED'}\n{out4}")

    # Step 5: git branch -M main
    ok5, out5 = runner.set_branch_main()
    results.append(f"git branch -M main: {'OK' if ok5 else 'FAILED'}\n{out5}")

    # Step 6: git push -u origin main
    ok6, out6 = runner.push_first()
    results.append(f"git push: {'OK' if ok6 else 'FAILED'}\n{out6}")

    all_ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6
    summary = "\n\n".join(results)
    if all_ok:
        QMessageBox.information(
            None, "Setup Complete",
            f"Your project is set up and pushed to GitHub!\n\n{summary}"
        )
    else:
        QMessageBox.warning(
            None, "Setup Partially Complete",
            f"Some steps had issues. You may need to push manually later.\n\n{summary}"
        )

    return target_folder


# ============================================================================
# MAIN
# ============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Light palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(245, 245, 245))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.Text, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    repo_path = detect_or_setup_repo()
    if not repo_path:
        QMessageBox.critical(
            None, "Git Helper",
            "No repository set up. Exiting."
        )
        sys.exit(1)

    win = GitHelperWindow(repo_path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
