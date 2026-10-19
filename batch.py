#!/usr/bin/env python3
"""
Batch Order Uploader - standalone fast-upload tool.

Flow:
  1. Enter an order number and click "Look Up".
  2. The order is found in Shopify and its customer/order Dropbox folder is
     created (same logic as the main Order Queue GUI).
  3. Pick one or more twin check folders from the list.
  4. Click "Upload Selected" - files are sent via Dropbox upload *sessions*
     and committed with a single finish_batch call per twin check, so a
     40-frame roll counts as ONE write operation instead of 40. This avoids
     the too_many_write_operations throttle that makes per-file uploads crawl.

Safety: every file still goes through router._read_complete_bytes (the
grey-file gate), and every committed file is verified against the size and
Dropbox content-hash of the bytes we sent. A failed or mismatched commit is
retried; if it still fails the scan is reported as a grey roll, never
silently skipped.
"""

import os
import re
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QListWidget, QListWidgetItem, QAbstractItemView, QMessageBox,
    QGroupBox, QFileDialog,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

import scanner_router_direct as router

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class LookupWorker(QThread):
    """Finds the order in Shopify and creates its customer/order folder."""

    resolved = Signal(str, str, str, str, str)  # order_no, customer_name, email, order_path, order_gid
    error = Signal(str)

    def __init__(self, order_input: str):
        super().__init__()
        self.order_input = order_input.strip()

    def run(self):
        try:
            order_num = self.order_input
            m = re.match(r"^#?(\d+)", self.order_input)
            if m:
                order_num = m.group(1)
            results = router.shopify_search_orders(f"name:{order_num}")
            if not results:
                self.error.emit(f"Order not found: {self.order_input}")
                return
            node = results[0]
            order_no = (node.get("name") or "").lstrip("#")
            order_gid = node.get("id") or ""
            customer = node.get("customer") or {}
            email = (customer.get("email") or node.get("email") or "unknown").strip().lower()
            name = (f"{customer.get('firstName') or ''} {customer.get('lastName') or ''}".strip()
                    or customer.get("displayName") or email)
            _, order_path = router.ensure_customer_order_folder(node)
            self.resolved.emit(order_no, name, email, order_path, order_gid)
        except Exception:
            self.error.emit(traceback.format_exc())


class BatchUploadWorker(QThread):
    """Uploads the selected twin check folders with batch sessions."""

    scan_started = Signal(str, str)             # scan_name, dest
    progress = Signal(str, int, int, str)       # scan_name, cur, total, msg
    scan_done = Signal(str, int)                # scan_name, files
    scan_failed = Signal(str, str)              # scan_name, reason
    tags_applied = Signal(list)                 # tags
    note_updated = Signal(str)                  # note text
    shopify_error = Signal(str)                 # upload OK but tags/note failed
    finished_all = Signal(int, int)             # total_files, failed_scans

    def __init__(self, order_path: str, scan_root: Path, scans: List[str],
                 order_gid: str = "", pending_tags: List[str] = None):
        super().__init__()
        self.order_path = order_path
        self.scan_root = scan_root
        self.scans = list(scans)
        self.order_gid = order_gid
        self.pending_tags = list(pending_tags or [])
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        total_files = 0
        failed = 0
        for scan_name in self.scans:
            if self._abort:
                return
            scan_dir = self.scan_root / scan_name
            ready, issues = router.folder_upload_ready(scan_dir)
            if not ready:
                failed += 1
                self.scan_failed.emit(
                    scan_name, f"not ready: {issues[0] if issues else 'incomplete'}")
                continue
            dest = f"{self.order_path}/{scan_name}"
            self.scan_started.emit(scan_name, dest)
            try:
                n = router.batch_upload_folder(
                    scan_dir, dest,
                    progress_callback=lambda c, t, m, sn=scan_name:
                        self.progress.emit(sn, c, t, m),
                    abort_check=lambda: self._abort)
                total_files += n
                self.scan_done.emit(scan_name, n)
            except router.UploadAborted:
                return
            except (router.IncompleteUploadError,
                    router.UploadVerificationError) as e:
                failed += 1
                self.scan_failed.emit(scan_name, f"GREY ROLL — {e}")
            except Exception:
                failed += 1
                self.scan_failed.emit(scan_name, traceback.format_exc())

        # Tags + twin-check note only when every scan committed clean —
        # same rule as the main Order Queue GUI: never tag a half-uploaded
        # order as done.
        if failed == 0 and self.order_gid:
            if self.pending_tags:
                try:
                    router.order_add_tags(self.order_gid, self.pending_tags)
                    self.tags_applied.emit(self.pending_tags)
                except Exception as tag_err:
                    self.shopify_error.emit(f"Upload done but tagging failed: {tag_err}")
            if self.scans:
                try:
                    try:
                        existing = router.get_existing_twin_checks_from_dropbox(self.order_path)
                    except Exception:
                        existing = []
                    all_twins = sorted(set(self.scans) | set(existing))
                    note_text = f"Twin Checks: {', '.join(all_twins)}"
                    if router.order_update_note(self.order_gid, note_text, append=True):
                        self.note_updated.emit(note_text)
                    else:
                        self.shopify_error.emit("Upload done but note update failed")
                except Exception as note_err:
                    self.shopify_error.emit(f"Upload done but note update failed: {note_err}")

        self.finished_all.emit(total_files, failed)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class BatchUploaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Batch Order Uploader")
        self.resize(760, 640)

        self.order_path: Optional[str] = None
        self.order_no: Optional[str] = None
        self.order_gid: str = ""
        self._lookup_worker: Optional[LookupWorker] = None
        self._upload_worker: Optional[BatchUploadWorker] = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- Step 1: order lookup ---
        order_box = QGroupBox("1 · Order")
        order_col = QVBoxLayout(order_box)
        order_row = QHBoxLayout()
        self.order_edit = QLineEdit()
        self.order_edit.setPlaceholderText("Order number (e.g. 1234 or 1234s — trailing letters become tags)")
        self.order_edit.returnPressed.connect(self._on_lookup)
        self.lookup_btn = QPushButton("Look Up && Create Folder")
        self.lookup_btn.clicked.connect(self._on_lookup)
        order_row.addWidget(self.order_edit, 1)
        order_row.addWidget(self.lookup_btn)
        order_col.addLayout(order_row)
        tags_row = QHBoxLayout()
        tags_row.addWidget(QLabel("Shopify tags:"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma-separated, applied after a clean upload")
        tags_row.addWidget(self.tags_edit, 1)
        order_col.addLayout(tags_row)
        layout.addWidget(order_box)

        self.order_info = QLabel("No order loaded.")
        self.order_info.setWordWrap(True)
        layout.addWidget(self.order_info)

        # --- Step 2: twin check selection ---
        scans_box = QGroupBox("2 · Twin checks")
        scans_layout = QVBoxLayout(scans_box)
        row = QHBoxLayout()
        self.root_label = QLabel(router.get_noritsu_root())
        self.root_label.setStyleSheet("color: gray;")
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_scans)
        change_btn = QPushButton("Change Folder…")
        change_btn.clicked.connect(self._change_root)
        row.addWidget(self.root_label, 1)
        row.addWidget(refresh_btn)
        row.addWidget(change_btn)
        scans_layout.addLayout(row)
        self.scan_list = QListWidget()
        self.scan_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        scans_layout.addWidget(self.scan_list)
        layout.addWidget(scans_box, 1)

        # --- Step 3: upload ---
        btn_row = QHBoxLayout()
        self.upload_btn = QPushButton("Upload Selected")
        self.upload_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._on_upload)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.upload_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo", 11))
        self.log_view.setMaximumHeight(180)
        layout.addWidget(self.log_view)

        self._refresh_scans()

    # -- helpers ----------------------------------------------------------

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {msg}")

    def _refresh_scans(self):
        root = Path(router.get_noritsu_root())
        self.root_label.setText(str(root))
        self.scan_list.clear()
        if not root.exists():
            self._log(f"Scan folder does not exist yet: {root}")
            return
        try:
            for d in sorted(p for p in root.iterdir() if p.is_dir()):
                QListWidgetItem(d.name, self.scan_list)
        except OSError as e:
            self._log(f"Cannot list scan folder: {e}")

    def _change_root(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select scan folder", router.get_noritsu_root() or str(Path.home()))
        if path and router.set_noritsu_root(path):
            self._refresh_scans()

    def _update_upload_enabled(self):
        self.upload_btn.setEnabled(
            self.order_path is not None and self._upload_worker is None)

    # -- lookup -----------------------------------------------------------

    def _on_lookup(self):
        text = self.order_edit.text().strip()
        if not text:
            return
        # "1234s" → order 1234, tags ["s"] (same syntax as the main GUI).
        # Only prefill; the tags field stays editable until upload starts.
        m = re.match(r"^#?(\d+)(.*)$", text)
        if m and m.group(2).strip():
            parsed = [t for t in re.split(r"[,\s]+", m.group(2).strip()) if t.strip()]
            if parsed:
                self.tags_edit.setText(", ".join(parsed))
        self.lookup_btn.setEnabled(False)
        self.order_info.setText("Looking up order…")
        self._log(f"Looking up order {text}…")
        self._lookup_worker = LookupWorker(text)
        self._lookup_worker.resolved.connect(self._on_resolved)
        self._lookup_worker.error.connect(self._on_lookup_error)
        self._lookup_worker.finished.connect(self._lookup_finished)
        self._lookup_worker.start()

    def _lookup_finished(self):
        self.lookup_btn.setEnabled(True)
        if self._lookup_worker is not None:
            self._lookup_worker.deleteLater()
            self._lookup_worker = None

    def _on_resolved(self, order_no, name, email, order_path, order_gid):
        self.order_no = order_no
        self.order_path = order_path
        self.order_gid = order_gid
        self.order_info.setText(
            f"✅ Order #{order_no} — {name} ({email})\nDropbox: {order_path}")
        self._log(f"Order #{order_no} ready → {order_path}")
        self._update_upload_enabled()

    def _on_lookup_error(self, msg):
        self.order_path = None
        self.order_no = None
        self.order_gid = ""
        self.order_info.setText("❌ Lookup failed.")
        self._log(f"Lookup failed: {msg.strip().splitlines()[-1]}")
        QMessageBox.warning(self, "Lookup failed", msg[:2000])
        self._update_upload_enabled()

    # -- upload -----------------------------------------------------------

    def _on_upload(self):
        if not self.order_path:
            return
        scans = [i.text() for i in self.scan_list.selectedItems()]
        if not scans:
            QMessageBox.information(self, "Nothing selected",
                                    "Select one or more twin check folders first.")
            return
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        tag_line = f"\n\nTags after upload: {', '.join(tags)}" if tags else ""
        reply = QMessageBox.question(
            self, "Confirm Upload",
            f"Upload {len(scans)} twin check(s) to order #{self.order_no}?\n\n"
            + "\n".join(f"  • {s}" for s in scans) + tag_line,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return

        self.upload_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.lookup_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self._log(f"Uploading {len(scans)} twin check(s) to #{self.order_no}…")

        self._upload_worker = BatchUploadWorker(
            self.order_path, Path(router.get_noritsu_root()), scans,
            order_gid=self.order_gid, pending_tags=tags)
        w = self._upload_worker
        w.scan_started.connect(lambda sn, dest: self._log(f"▶ {sn} → {dest}"))
        w.progress.connect(self._on_progress)
        w.scan_done.connect(lambda sn, n: self._log(f"✅ {sn}: {n} files committed"))
        w.scan_failed.connect(self._on_scan_failed)
        w.tags_applied.connect(
            lambda tags: self._log(f"🏷️ Tags applied: {', '.join(tags)}"))
        w.note_updated.connect(lambda note: self._log(f"📝 {note}"))
        w.shopify_error.connect(self._on_shopify_error)
        w.finished_all.connect(self._on_all_done)
        w.finished.connect(self._upload_finished)
        w.start()

    def _on_shopify_error(self, msg):
        self._log(f"⚠️ {msg}")
        QMessageBox.warning(self, "Shopify update failed",
                            f"{msg}\n\nFiles are uploaded — fix the tag/note "
                            "manually in Shopify if needed.")

    def _on_progress(self, scan_name, cur, total, msg):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(cur)
        self.progress_bar.setFormat(f"{scan_name} — {msg}")
        if msg.startswith(("⚠️", "❌")):
            self._log(f"{scan_name}: {msg}")

    def _on_scan_failed(self, scan_name, reason):
        first_line = reason.strip().splitlines()[-1]
        self._log(f"❌ {scan_name}: {first_line}")

    def _on_all_done(self, total_files, failed_scans):
        if failed_scans:
            self._log(f"⚠️ Finished with {failed_scans} failed scan(s) — "
                      f"{total_files} files uploaded. Rescan and retry the failures.")
            QMessageBox.warning(
                self, "Upload finished with errors",
                f"{failed_scans} twin check(s) failed — see the log. "
                "Rescan if needed, then upload them again.")
        else:
            self._log(f"🎉 All done — {total_files} files uploaded.")

    def _upload_finished(self):
        if self._upload_worker is not None:
            self._upload_worker.deleteLater()
            self._upload_worker = None
        self.cancel_btn.setEnabled(False)
        self.lookup_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_upload_enabled()

    def _on_cancel(self):
        if self._upload_worker is not None:
            self._upload_worker.abort()
            self._log("Cancelling after current file…")

    def closeEvent(self, event):
        if self._upload_worker is not None and self._upload_worker.isRunning():
            reply = QMessageBox.question(
                self, "Upload in progress",
                "An upload is running. Cancel it and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._upload_worker.abort()
            self._upload_worker.wait(15000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = BatchUploaderWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
