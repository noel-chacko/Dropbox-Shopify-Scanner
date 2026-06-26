#!/usr/bin/env python3
"""
Order Queue GUI - Batch scanner with manual upload confirmation.

Scans are detected and settled automatically, then queued under their order.
No uploads happen until the user clicks "Confirm Order" on an order group.
"""

import sys
import os
import re
import time
import threading
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QGroupBox, QMessageBox, QSplitter, QScrollArea, QFrame,
    QFileDialog, QDialog, QDialogButtonBox, QDateEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QDate, QPoint, QMimeData
from PySide6.QtGui import QFont, QFontDatabase, QPainter, QBrush, QColor, QRegion, QPolygon, QDrag

import scanner_router_direct as router

SETTLE_SECONDS = float(os.getenv("SETTLE_SECONDS", "5.0"))
SCAN_INTERVAL = router.SCAN_INTERVAL
UNASSIGNED = "__UNASSIGNED__"


# ---------------------------------------------------------------------------
# Decorative stripe bar (matches the Noritsu scanner tape)
# ---------------------------------------------------------------------------

class StripeWidget(QWidget):
    """
    Diagonal stripe bar shaped like a parallelogram — left edge vertical,
    right edge cut at 45° matching the stripe angle, like the tape on the scanner.
    """

    # (horizontal width in px, color — None means maroon background, just skip)
    _PATTERN = [
        (20, None),
        (9,  QColor(211, 211, 211)),   # grey (matches background)
        (5,  None),
        (9,  QColor(168, 138, 210)),   # lavender
    ]
    _BG = QColor(108, 4, 22)           # dark maroon

    def __init__(self, height: int = 22, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Clip the widget to a parallelogram — narrow at top, wider at bottom (\-slant)
        w, h = self.width(), self.height()
        self.setMask(QRegion(QPolygon([
            QPoint(0,     0),
            QPoint(w - h, 0),
            QPoint(w,     h),
            QPoint(0,     h),
        ])))

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), self._BG)
        painter.setPen(Qt.NoPen)

        x = -h  # start left enough that stripes cover the bottom-left corner
        while x < w + h:
            for stripe_w, color in self._PATTERN:
                if color is not None:
                    painter.setBrush(QBrush(color))
                    painter.drawPolygon([
                        QPoint(x,                0),
                        QPoint(x + stripe_w,     0),
                        QPoint(x + stripe_w + h, h),
                        QPoint(x + h,            h),
                    ])
                x += stripe_w


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class OrderBatch:
    """One order and its associated twin check folder names."""

    def __init__(self, order_input: str):
        self.order_input: str = order_input          # exactly what the user typed

        # Parse "343432s" → number "343432", tags ["s"]
        m = re.match(r"^#?(\d+)(.*)$", order_input)
        if m:
            self.order_number: str = m.group(1)
            trailing = (m.group(2) or "").strip().lstrip(" ,")
            self.pending_tags: List[str] = [t for t in re.split(r"[,\s]+", trailing) if t.strip()]
        else:
            self.order_number = order_input
            self.pending_tags = []

        self.twin_checks: List[str] = []             # settled scan folder names
        self.status: str = "pending"                 # pending | uploading | completed | error
        self.error_msg: str = ""
        self.error_detail: str = ""
        # Populated at confirm time
        self.order_no: Optional[str] = None          # resolved Shopify order number
        self.order_gid: Optional[str] = None         # Shopify GID — needed for tagging
        self.email: Optional[str] = None
        self.customer_name: Optional[str] = None
        # Per-scan upload progress: {scan_name: (current, total, msg)}
        self.progress: Dict[str, tuple] = {}

    @property
    def display_name(self) -> str:
        if self.order_input == UNASSIGNED:
            return "Unassigned"
        num = self.order_no or self.order_number
        return f"Order #{num}"


# ---------------------------------------------------------------------------
# Scanner worker
# ---------------------------------------------------------------------------

class ScanQueueWorker(QThread):
    """Polls the scanner root, waits for folders to settle, then emits scan_settled."""

    scan_settled = Signal(str)              # scan_name
    settling_update = Signal(str, int, float)  # scan_name, file_count, size_mb
    status_update = Signal(str)
    path_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.current_root: Optional[Path] = None
        self.existing_folders: set = set()
        self.processed: set = set()                  # names already emitted
        self.pending_settles: Dict[str, float] = {}  # name -> first_seen_time
        self._lock = threading.Lock()

    def add_to_processed(self, scan_name: str):
        with self._lock:
            self.processed.add(scan_name)

    def update_path(self, new_path: str):
        try:
            new_norm = os.path.normcase(os.path.normpath(new_path))
        except Exception:
            new_norm = new_path
        cur_norm = None
        if self.current_root is not None:
            try:
                cur_norm = os.path.normcase(os.path.normpath(str(self.current_root)))
            except Exception:
                cur_norm = str(self.current_root)
        if cur_norm == new_norm:
            return
        self.current_root = Path(new_path)
        self.existing_folders = set()
        self.pending_settles = {}
        if self.current_root.exists():
            self.existing_folders = {d.name for d in self.current_root.iterdir() if d.is_dir()}
        self.path_changed.emit(new_path)

    @staticmethod
    def _folder_stats(scan_dir: Path):
        """Return (file_count, size_mb, latest_mtime) for a scan folder."""
        try:
            files = [f for f in scan_dir.rglob("*") if f.is_file()]
            if not files:
                return 0, 0.0, 0.0
            size = sum(f.stat().st_size for f in files)
            mtime = max(f.stat().st_mtime for f in files)
            return len(files), size / 1_048_576, mtime
        except Exception:
            return 0, 0.0, 0.0

    @staticmethod
    def _is_settled(scan_dir: Path) -> bool:
        try:
            if not scan_dir.exists():
                return False
            file_files = [f for f in scan_dir.rglob("*") if f.is_file()]
            if not file_files:
                return False
            mtime = max(f.stat().st_mtime for f in file_files)
            return (time.time() - mtime) > SETTLE_SECONDS
        except Exception:
            return False

    def run(self):
        self.current_root = Path(router.get_noritsu_root())
        if self.current_root.exists():
            self.existing_folders = {d.name for d in self.current_root.iterdir() if d.is_dir()}

        last_scan = time.time()

        while self.running:
            try:
                new_path = router.get_noritsu_root()
                try:
                    new_norm = os.path.normcase(os.path.normpath(new_path))
                except Exception:
                    new_norm = new_path
                cur_norm = None
                if self.current_root is not None:
                    try:
                        cur_norm = os.path.normcase(os.path.normpath(str(self.current_root)))
                    except Exception:
                        cur_norm = str(self.current_root)
                if cur_norm != new_norm:
                    self.update_path(new_path)

                if time.time() - last_scan < SCAN_INTERVAL:
                    time.sleep(0.1)
                    continue
                last_scan = time.time()

                if not self.current_root.exists():
                    self.status_update.emit(f"⚠️ Cannot access: {self.current_root}")
                    time.sleep(5)
                    continue

                with self._lock:
                    _processed_snap = set(self.processed)

                # Discover new directories
                for scan_dir in self.current_root.iterdir():
                    if not scan_dir.is_dir():
                        continue
                    name = scan_dir.name
                    if name in self.existing_folders:
                        continue
                    if name in _processed_snap:
                        self.existing_folders.add(name)
                        continue
                    if name not in self.pending_settles:
                        self.pending_settles[name] = time.time()
                        self.status_update.emit(f"🔍 New scan: {name} — waiting to settle…")

                # Check pending settles — emit live stats each loop
                for name in list(self.pending_settles.keys()):
                    with self._lock:
                        already = name in self.processed
                    if already:
                        del self.pending_settles[name]
                        continue
                    scan_dir = self.current_root / name
                    if not scan_dir.exists():
                        del self.pending_settles[name]
                        continue
                    count, size_mb, _ = self._folder_stats(scan_dir)
                    self.settling_update.emit(name, count, size_mb)
                    if self._is_settled(scan_dir):
                        self.scan_settled.emit(name)
                        del self.pending_settles[name]
                        self.existing_folders.add(name)

            except Exception as e:
                self.status_update.emit(f"Scanner error: {e}")
                time.sleep(1)

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# Upload worker
# ---------------------------------------------------------------------------

class UploadOrderWorker(QThread):
    """Looks up an order in Shopify and uploads all its twin check folders."""

    order_resolved = Signal(str, str, str, str, str)  # order_input, order_no, email, customer_name, order_gid
    scan_upload_started = Signal(str, str, str)       # order_input, scan_name, dest
    scan_upload_progress = Signal(str, str, int, int, str)  # order_input, scan_name, curr, total, msg
    upload_completed = Signal(str, int)               # order_input, total_files
    tags_applied = Signal(str, list)                  # order_input, tags
    upload_error = Signal(str, str)                   # order_input, error_msg

    def __init__(self, order_input: str, twin_checks: List[str], scan_root: Path,
                 pending_tags: List[str] = None):
        super().__init__()
        self.order_input = order_input
        self.twin_checks = list(twin_checks)
        self.scan_root = scan_root
        self.pending_tags = list(pending_tags or [])

    def run(self):
        try:
            # --- Shopify lookup ---
            order_num = self.order_input
            m = re.match(r"^#?(\d+)(.*)$", self.order_input)
            if m:
                order_num = m.group(1)

            results = router.shopify_search_orders(f"name:{order_num}")
            if not results:
                self.upload_error.emit(self.order_input, f"Order not found: {self.order_input}")
                return

            order_node = results[0]
            order_no = (order_node.get("name") or "").lstrip("#")
            order_gid = order_node.get("id") or order_node.get("admin_graphql_api_id") or ""
            customer = order_node.get("customer") or {}
            email = (customer.get("email") or order_node.get("email") or "unknown").strip().lower()
            first = customer.get("firstName") or customer.get("first_name") or ""
            last  = customer.get("lastName")  or customer.get("last_name")  or ""
            customer_name = (f"{first} {last}".strip()
                             or customer.get("displayName") or "")
            self.order_resolved.emit(self.order_input, order_no, email, customer_name, order_gid)

            # --- Dropbox folder ---
            _, order_path = router.ensure_customer_order_folder(order_node)

            # --- Upload twin checks ---
            total_uploaded = 0
            for scan_name in self.twin_checks:
                scan_dir = self.scan_root / scan_name
                if not scan_dir.exists():
                    self.scan_upload_progress.emit(
                        self.order_input, scan_name, 0, 0,
                        f"⚠️ Folder missing: {scan_name}"
                    )
                    continue

                # Defense in depth: re-verify the folder is fully written and
                # every JPEG is complete right before uploading it.
                ready, issues = router.folder_upload_ready(scan_dir)
                if not ready:
                    self.scan_upload_progress.emit(
                        self.order_input, scan_name, 0, 0,
                        f"⚠️ Skipped {scan_name}: not ready ({issues[0] if issues else 'incomplete'})"
                    )
                    continue

                dest = f"{order_path}/{scan_name}"
                self.scan_upload_started.emit(self.order_input, scan_name, dest)

                def _make_cb(sn):
                    def cb(cur, tot, msg):
                        self.scan_upload_progress.emit(self.order_input, sn, cur, tot, msg)
                    return cb

                try:
                    uploaded = router.upload_folder(scan_dir, dest, _make_cb(scan_name), upload_delay=2.0, exclude_files={"thumbs.db"})
                    total_uploaded += uploaded
                    self.scan_upload_progress.emit(
                        self.order_input, scan_name, uploaded, uploaded,
                        f"✅ {uploaded} files uploaded"
                    )
                except Exception as e:
                    self.scan_upload_progress.emit(
                        self.order_input, scan_name, 0, 0, f"❌ {e}"
                    )

            # --- Apply Shopify tags if any were specified ---
            if self.pending_tags and order_gid:
                try:
                    router.order_add_tags(order_gid, self.pending_tags)
                    self.tags_applied.emit(self.order_input, self.pending_tags)
                except Exception as tag_err:
                    self.upload_error.emit(self.order_input,
                                           f"Upload done but tagging failed: {tag_err}")
                    return

            # --- Append this order's twin check numbers to the Shopify note ---
            if order_gid and self.twin_checks:
                try:
                    existing = []
                    try:
                        existing = router.get_existing_twin_checks_from_dropbox(order_path)
                    except Exception:
                        existing = []
                    all_twins = sorted(set(self.twin_checks) | set(existing))
                    note_text = f"Twin Checks: {', '.join(all_twins)}"
                    if router.order_update_note(order_gid, note_text, append=True):
                        self.scan_upload_progress.emit(
                            self.order_input, self.twin_checks[0], 0, 0,
                            f"📝 Twin checks added to note: {', '.join(all_twins)}")
                except Exception as note_err:
                    print(f"⚠️  Failed to add twin checks to order note: {note_err}")

            self.upload_completed.emit(self.order_input, total_uploaded)

        except Exception as e:
            self.upload_error.emit(self.order_input, traceback.format_exc())


# ---------------------------------------------------------------------------
# Draggable scan-name label (for drag-and-drop between order groups)
# ---------------------------------------------------------------------------

class DraggableScanLabel(QLabel):
    """QLabel that initiates a drag carrying its order_input + scan_name."""

    MIME_TYPE = "application/x-scancheck"
    _DRAG_THRESHOLD = 8  # Manhattan distance before drag starts

    def __init__(self, scan_name: str, order_input: str, parent=None):
        super().__init__(scan_name, parent)
        self.scan_name = scan_name
        self.order_input = order_input
        self.setFont(QFont("Courier", 11))
        self.setCursor(Qt.OpenHandCursor)
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < self._DRAG_THRESHOLD:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.MIME_TYPE,
                     f"{self.order_input}\n{self.scan_name}".encode())
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        self._press_pos = None
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Order group widget (right panel card)
# ---------------------------------------------------------------------------

class OrderGroupWidget(QFrame):
    """
    Displays one order group: header, twin check rows, and action buttons.
    Progress labels are created once and updated in-place via update_scan_progress()
    to avoid expensive full-panel rebuilds on every upload tick.
    """

    confirm_requested = Signal(str)              # order_input
    move_up_requested = Signal(str, str)         # order_input, scan_name
    move_down_requested = Signal(str, str)       # order_input, scan_name
    retry_requested = Signal(str)                # order_input
    drop_scan_requested = Signal(str, str, str)  # src_order_input, scan_name, dst_order_input
    change_tags_requested = Signal(str)          # order_input

    def __init__(self, batch: OrderBatch, is_active: bool,
                 has_prev: bool, has_next: bool, parent=None):
        super().__init__(parent)
        self.batch = batch
        # Keyed by scan_name — updated in-place by update_scan_progress()
        self._progress_labels: Dict[str, QLabel] = {}
        self._build(is_active, has_prev, has_next)
        self.setAcceptDrops(True)

    def _build(self, is_active: bool, has_prev: bool, has_next: bool):
        self.setFrameShape(QFrame.Box)
        self.setLineWidth(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4)

        # --- Header row ---
        header_row = QHBoxLayout()
        count = len(self.batch.twin_checks)
        count_str = f"  ({count} scan{'s' if count != 1 else ''})" if count else ""
        active_str = "  [ACTIVE]" if (is_active and self.batch.status == "pending") else ""
        title = QLabel(self.batch.display_name + count_str + active_str)
        title.setFont(QFont("Arial", 12, QFont.Bold))
        header_row.addWidget(title)

        status_lbl = QLabel(f"[{self.batch.status.upper()}]")
        status_lbl.setFont(QFont("Arial", 9))
        header_row.addWidget(status_lbl)

        if self.batch.pending_tags or self.batch.status == "pending":
            tags_text = f"tags: {', '.join(self.batch.pending_tags)}" if self.batch.pending_tags else "tags: —"
            tags_lbl = QLabel(f"[{tags_text}]")
            tags_lbl.setFont(QFont("Arial", 9))
            header_row.addWidget(tags_lbl)

            if self.batch.status == "pending" and self.batch.order_input != UNASSIGNED:
                edit_btn = QPushButton("✎")
                edit_btn.setFixedSize(22, 22)
                edit_btn.setFont(QFont("Arial", 9))
                edit_btn.setToolTip("Edit tags")
                edit_btn.clicked.connect(
                    lambda: self.change_tags_requested.emit(self.batch.order_input)
                )
                header_row.addWidget(edit_btn)

        header_row.addStretch()
        root.addLayout(header_row)

        if self.batch.email or self.batch.customer_name:
            parts = []
            if self.batch.customer_name:
                parts.append(self.batch.customer_name)
            if self.batch.email:
                parts.append(self.batch.email)
            info_lbl = QLabel("  |  ".join(parts))
            info_lbl.setFont(QFont("Arial", 9))
            root.addWidget(info_lbl)

        if self.batch.error_msg:
            err_lbl = QLabel(f"Error: {self.batch.error_msg}")
            err_lbl.setFont(QFont("Arial", 9))
            err_lbl.setWordWrap(True)
            if self.batch.error_detail:
                err_lbl.setToolTip(self.batch.error_detail)
            root.addWidget(err_lbl)

        # --- Separator ---
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("")
        root.addWidget(sep)

        # --- Twin check rows ---
        can_move_up = has_prev and self.batch.status == "pending"
        can_move_down = has_next and self.batch.status == "pending"
        if self.batch.twin_checks:
            for scan_name in self.batch.twin_checks:
                row = QHBoxLayout()

                name_lbl = DraggableScanLabel(scan_name, self.batch.order_input)
                row.addWidget(name_lbl, stretch=1)

                # Progress label — always created so update_scan_progress() can find it
                prog = self.batch.progress.get(scan_name)
                prog_lbl = QLabel(prog[2] if prog and prog[2] else "")
                prog_lbl.setFont(QFont("Arial", 9))
                self._progress_labels[scan_name] = prog_lbl
                row.addWidget(prog_lbl)

                up_btn = QPushButton("↑")
                up_btn.setFixedSize(28, 26)
                up_btn.setFont(QFont("Arial", 9))
                up_btn.setEnabled(can_move_up)
                up_btn.setToolTip("Move to previous order")
                up_btn.clicked.connect(
                    (lambda sn: lambda: self.move_up_requested.emit(self.batch.order_input, sn))(scan_name)
                )
                row.addWidget(up_btn)

                down_btn = QPushButton("↓")
                down_btn.setFixedSize(28, 26)
                down_btn.setFont(QFont("Arial", 9))
                down_btn.setEnabled(can_move_down)
                down_btn.setToolTip("Move to next order")
                down_btn.clicked.connect(
                    (lambda sn: lambda: self.move_down_requested.emit(self.batch.order_input, sn))(scan_name)
                )
                row.addWidget(down_btn)

                root.addLayout(row)
        else:
            empty_lbl = QLabel("(no twin checks yet)")
            empty_lbl.setFont(QFont("Arial", 9))
            root.addWidget(empty_lbl)

        # --- Separator before action buttons ---
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #ccc;")
        root.addWidget(sep2)

        # --- Action buttons (only for real orders, not Unassigned) ---
        if self.batch.order_input != UNASSIGNED:
            if self.batch.status == "pending":
                confirm_btn = QPushButton("Confirm Order")
                confirm_btn.setMinimumHeight(36)
                confirm_btn.setEnabled(bool(self.batch.twin_checks))
                confirm_btn.clicked.connect(
                    lambda: self.confirm_requested.emit(self.batch.order_input)
                )
                root.addWidget(confirm_btn)

            elif self.batch.status == "uploading":
                pb = QProgressBar()
                pb.setRange(0, 0)  # indeterminate spinner
                pb.setFixedHeight(22)
                root.addWidget(pb)

            elif self.batch.status == "error":
                retry_btn = QPushButton("Retry Upload")
                retry_btn.setMinimumHeight(36)
                retry_btn.clicked.connect(
                    lambda: self.retry_requested.emit(self.batch.order_input)
                )
                root.addWidget(retry_btn)

    def update_scan_progress(self, scan_name: str, cur: int, tot: int, msg: str):
        """Update a single scan's progress label in-place — no widget rebuild needed."""
        lbl = self._progress_labels.get(scan_name)
        if lbl is not None:
            lbl.setText(msg if msg else f"{cur}/{tot}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(DraggableScanLabel.MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(DraggableScanLabel.MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(DraggableScanLabel.MIME_TYPE):
            return
        payload = event.mimeData().data(DraggableScanLabel.MIME_TYPE).data().decode()
        src_order, scan_name = payload.split("\n", 1)
        if src_order != self.batch.order_input:
            self.drop_scan_requested.emit(src_order, scan_name, self.batch.order_input)
        event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ScannerOrderQueueGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scanner — Order Queue")
        self.setGeometry(100, 100, 1200, 680)

        # Queue: first item is always the Unassigned bucket
        self._unassigned = OrderBatch(UNASSIGNED)
        self.order_queue: List[OrderBatch] = [self._unassigned]

        # Active batch = last pending non-unassigned batch, or unassigned if none
        self._active_order_input: str = UNASSIGNED

        # Running upload workers keyed by order_input
        self._upload_workers: Dict[str, UploadOrderWorker] = {}

        # Live card references so progress can be updated in-place
        self._order_cards: Dict[str, "OrderGroupWidget"] = {}

        # Debounce flag: prevents multiple rapid structural rebuilds in one event-loop cycle
        self._rebuild_queued = False

        # Currently settling scans: {scan_name: (file_count, size_mb)}
        self._settling: Dict[str, tuple] = {}

        self._build_ui()

        # Scanner worker
        self.scanner = ScanQueueWorker()
        self.scanner.scan_settled.connect(self._on_scan_settled)
        self.scanner.settling_update.connect(self._on_settling_update)
        self.scanner.status_update.connect(lambda m: self._log(m, "INFO"))
        self.scanner.path_changed.connect(self._on_path_changed)
        self.scanner.start()

        self._update_scan_path_label()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        content_layout.addWidget(splitter)
        root.addWidget(content)

        # Floating stripe — width = 1/8 of window, sits just above the bottom edge
        self._stripe = StripeWidget(height=22, parent=central)
        self._stripe.raise_()
        QTimer.singleShot(0, self._update_stripe_geometry)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- Add Order ---
        order_group = QGroupBox("Add Order")
        og = QVBoxLayout(order_group)

        self._order_input = QLineEdit()
        self._order_input.setPlaceholderText("Order number (e.g. 12345)")
        self._order_input.setFont(QFont("Arial", 14))
        self._order_input.setMinimumHeight(48)
        self._order_input.returnPressed.connect(self._add_order)
        og.addWidget(self._order_input)

        add_btn = QPushButton("Add Order")
        add_btn.setFont(QFont("Arial", 12, QFont.Bold))
        add_btn.setMinimumHeight(46)
        add_btn.clicked.connect(self._add_order)
        og.addWidget(add_btn)

        layout.addWidget(order_group)

        # --- Settling scans ---
        self._settling_group = QGroupBox("Settling")
        sg = QVBoxLayout(self._settling_group)
        self._settling_label = QLabel("(none)")
        self._settling_label.setFont(QFont("Courier", 9))
        self._settling_label.setWordWrap(True)
        sg.addWidget(self._settling_label)
        self._settling_group.setVisible(False)
        layout.addWidget(self._settling_group)

        # --- Scanner path ---
        path_group = QGroupBox("Scanner Path")
        pg = QVBoxLayout(path_group)

        self._path_label = QLabel("")
        self._path_label.setWordWrap(True)
        self._path_label.setFont(QFont("Arial", 9))
        pg.addWidget(self._path_label)

        btns = QHBoxLayout()
        change_btn = QPushButton("Change Folder")
        change_btn.clicked.connect(self._change_folder)
        btns.addWidget(change_btn)

        create_btn = QPushButton("Create Date Folder")
        create_btn.clicked.connect(self._create_date_folder)
        btns.addWidget(create_btn)
        pg.addLayout(btns)

        layout.addWidget(path_group)

        # --- Log ---
        log_group = QGroupBox("Log")
        lg = QVBoxLayout(log_group)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setAcceptRichText(True)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(8)
        self._log_text.setFont(mono)
        lg.addWidget(self._log_text)

        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self._log_text.clear)
        lg.addWidget(clear_btn)

        layout.addWidget(log_group)
        layout.addStretch()
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("Order Queue")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._queue_container = QWidget()
        self._queue_layout = QVBoxLayout(self._queue_container)
        self._queue_layout.setContentsMargins(4, 4, 4, 4)
        self._queue_layout.setSpacing(10)
        self._queue_layout.addStretch()

        self._scroll_area.setWidget(self._queue_container)
        layout.addWidget(self._scroll_area)

        self._rebuild_right_panel()
        return panel

    # ------------------------------------------------------------------
    # Right panel rebuild
    # ------------------------------------------------------------------

    def _rebuild_right_panel(self):
        """Schedule a debounced rebuild — prevents double-rebuilds within one event cycle."""
        if not self._rebuild_queued:
            self._rebuild_queued = True
            QTimer.singleShot(0, self._do_rebuild)

    def _do_rebuild(self):
        """Clear and recreate all order group widgets, updating _order_cards."""
        self._rebuild_queued = False
        self._order_cards.clear()

        # Remove all widgets except the trailing stretch
        while self._queue_layout.count() > 1:
            item = self._queue_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        last_idx = len(self.order_queue) - 1
        for i, batch in enumerate(self.order_queue):
            is_active = (batch.order_input == self._active_order_input)
            has_prev = (i > 0)
            has_next = (i < last_idx)
            card = OrderGroupWidget(batch, is_active=is_active, has_prev=has_prev, has_next=has_next)
            card.confirm_requested.connect(self._on_confirm_order)
            card.move_up_requested.connect(self._on_move_up)
            card.move_down_requested.connect(self._on_move_down)
            card.retry_requested.connect(self._on_retry_order)
            card.drop_scan_requested.connect(self._on_drop_scan)
            card.change_tags_requested.connect(self._on_change_tags)
            self._order_cards[batch.order_input] = card
            self._queue_layout.insertWidget(i, card)

    # ------------------------------------------------------------------
    # Slot: scan settled
    # ------------------------------------------------------------------

    def _on_scan_settled(self, scan_name: str):
        self.scanner.add_to_processed(scan_name)
        self._settling.pop(scan_name, None)
        self._update_settling_display()

        active = self._get_active_batch()
        if scan_name not in active.twin_checks:
            active.twin_checks.append(scan_name)

        self._warn_if_duplicate(scan_name)
        self._log(f"📷 Settled: {scan_name} → {active.display_name}", "SUCCESS")
        self._rebuild_right_panel()

    # ------------------------------------------------------------------
    # Slot: add order
    # ------------------------------------------------------------------

    def _add_order(self):
        text = self._order_input.text().strip()
        if not text:
            return

        # Don't add duplicate pending orders
        for b in self.order_queue:
            if b.order_input == text and b.status == "pending":
                QMessageBox.information(self, "Already exists",
                                        f"Order {text} is already in the queue.")
                self._order_input.clear()
                return

        batch = OrderBatch(text)
        self.order_queue.append(batch)
        self._active_order_input = text
        self._order_input.clear()
        self._log(f"Added order {text} to queue (now active)", "INFO")
        self._rebuild_right_panel()
        # Scroll to bottom so new order is visible
        QTimer.singleShot(50, lambda: self._scroll_area.verticalScrollBar().setValue(
            self._scroll_area.verticalScrollBar().maximum()
        ))

    # ------------------------------------------------------------------
    # Slot: confirm order
    # ------------------------------------------------------------------

    def _on_confirm_order(self, order_input: str):
        batch = self._find_batch(order_input)
        if not batch:
            return
        if not batch.twin_checks:
            QMessageBox.information(self, "No scans",
                                    f"No twin checks assigned to order {order_input} yet.")
            return

        # Gate: refuse to upload until every file has settled and every JPEG
        # is a complete image. Better to wait than upload a half-grey scan.
        scan_root = Path(router.get_noritsu_root())
        not_ready = []
        for sn in batch.twin_checks:
            ok, issues = router.folder_upload_ready(scan_root / sn)
            if not ok:
                not_ready.extend(f"{sn} → {i}" for i in issues)
        if not_ready:
            shown = not_ready[:20]
            extra = "" if len(not_ready) <= 20 else f"\n  …and {len(not_ready) - 20} more"
            QMessageBox.warning(
                self, "Not ready to upload",
                f"Order {order_input} has files that are still being written "
                "or incomplete:\n\n"
                + "\n".join(f"  • {x}" for x in shown) + extra
                + "\n\nWait for scanning to finish, then try again.",
            )
            self._log(f"Upload blocked for {order_input}: {len(not_ready)} file(s) not ready",
                      "ERROR")
            return

        reply = QMessageBox.question(
            self, "Confirm Upload",
            f"Upload {len(batch.twin_checks)} twin check(s) for order {order_input}?\n\n"
            + "\n".join(f"  • {t}" for t in batch.twin_checks),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        batch.status = "uploading"
        self._rebuild_right_panel()
        self._log(f"Starting upload for {order_input}…", "INFO")

        worker = UploadOrderWorker(order_input, batch.twin_checks, scan_root,
                                   pending_tags=batch.pending_tags)
        worker.order_resolved.connect(self._on_order_resolved)
        worker.scan_upload_started.connect(self._on_scan_upload_started)
        worker.scan_upload_progress.connect(self._on_scan_upload_progress)
        worker.upload_completed.connect(self._on_upload_completed)
        worker.tags_applied.connect(self._on_tags_applied)
        worker.upload_error.connect(self._on_upload_error)
        self._upload_workers[order_input] = worker
        worker.start()

    # ------------------------------------------------------------------
    # Slot: move twin check down to next order
    # ------------------------------------------------------------------

    def _on_move_up(self, order_input: str, scan_name: str):
        src_idx = self._find_batch_index(order_input)
        if src_idx is None or src_idx == 0:
            return
        dst_idx = src_idx - 1

        src = self.order_queue[src_idx]
        dst = self.order_queue[dst_idx]

        if scan_name in src.twin_checks:
            src.twin_checks.remove(scan_name)
        if scan_name not in dst.twin_checks:
            dst.twin_checks.append(scan_name)

        self._log(f"Moved {scan_name}: {src.display_name} → {dst.display_name}", "INFO")
        self._rebuild_right_panel()

    def _on_move_down(self, order_input: str, scan_name: str):
        src_idx = self._find_batch_index(order_input)
        if src_idx is None:
            return
        dst_idx = src_idx + 1
        if dst_idx >= len(self.order_queue):
            return

        src = self.order_queue[src_idx]
        dst = self.order_queue[dst_idx]

        if scan_name in src.twin_checks:
            src.twin_checks.remove(scan_name)
        if scan_name not in dst.twin_checks:
            dst.twin_checks.append(scan_name)

        self._log(
            f"Moved {scan_name}: {src.display_name} → {dst.display_name}", "INFO"
        )
        self._rebuild_right_panel()

    # ------------------------------------------------------------------
    # Upload callbacks
    # ------------------------------------------------------------------

    def _on_order_resolved(self, order_input: str, order_no: str, email: str,
                           customer_name: str, order_gid: str):
        batch = self._find_batch(order_input)
        if batch:
            batch.order_no = order_no
            batch.order_gid = order_gid
            batch.email = email
            batch.customer_name = customer_name
        name_str = f" ({customer_name})" if customer_name else ""
        self._log(f"Resolved {order_input} → #{order_no}{name_str} — {email}", "INFO")
        self._rebuild_right_panel()

    def _on_tags_applied(self, order_input: str, tags: list):
        self._log(f"✅ Tags applied to {order_input}: {', '.join(tags)}", "SUCCESS")

    def _on_change_tags(self, order_input: str):
        batch = self._find_batch(order_input)
        if not batch:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit Tags — {batch.display_name}")
        dlg.setMinimumWidth(320)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Tags (comma-separated, e.g. s, bs):"))
        inp = QLineEdit(", ".join(batch.pending_tags))
        inp.setPlaceholderText("e.g. s, bs, sp")
        lay.addWidget(inp)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        text = inp.text().strip()
        batch.pending_tags = [t.strip() for t in text.split(",") if t.strip()]
        self._log(f"Tags updated for {order_input}: {', '.join(batch.pending_tags) or '(none)'}", "INFO")
        self._rebuild_right_panel()

    def _on_scan_upload_started(self, order_input: str, scan_name: str, dest: str):
        self._log(f"  📤 Uploading {scan_name}…", "INFO")

    def _on_scan_upload_progress(self, order_input: str, scan_name: str,
                                  cur: int, tot: int, msg: str):
        batch = self._find_batch(order_input)
        if batch:
            batch.progress[scan_name] = (cur, tot, msg)
        # Update the progress label in-place — no panel rebuild needed
        card = self._order_cards.get(order_input)
        if card is not None:
            card.update_scan_progress(scan_name, cur, tot, msg)
        if msg:
            level = "ERROR" if "❌" in msg else ("WARNING" if "⚠️" in msg else "INFO")
            self._log(f"  {scan_name}: {msg}", level)

    def _on_upload_completed(self, order_input: str, total_files: int):
        batch = self._find_batch(order_input)
        if batch:
            batch.status = "completed"
            batch.progress = {}
        self._upload_workers.pop(order_input, None)
        self._log(f"✅ Upload complete for {order_input}: {total_files} files total", "SUCCESS")
        self._rebuild_right_panel()

    def _on_upload_error(self, order_input: str, error_msg: str):
        batch = self._find_batch(order_input)
        if batch:
            batch.status = "error"
            lines = error_msg.strip().splitlines()
            batch.error_msg = lines[-1] if lines else "Unknown error"
            batch.error_detail = error_msg  # full traceback available via tooltip
        self._upload_workers.pop(order_input, None)
        short = batch.error_msg if batch else error_msg
        self._log(f"❌ Upload error for {order_input}: {short}", "ERROR")
        self._rebuild_right_panel()

    def _on_retry_order(self, order_input: str):
        """Reset a failed order back to pending so it can be confirmed again."""
        batch = self._find_batch(order_input)
        if batch:
            batch.status = "pending"
            batch.error_msg = ""
            batch.error_detail = ""
            batch.progress = {}
        self._log(f"Retrying order {order_input}…", "INFO")
        self._rebuild_right_panel()

    # ------------------------------------------------------------------
    # Scanner path helpers
    # ------------------------------------------------------------------

    def _update_scan_path_label(self):
        self._path_label.setText(f"Watching: {router.get_noritsu_root()}")

    def _on_path_changed(self, new_path: str):
        self._path_label.setText(f"Watching: {new_path}")
        self._log(f"Scanner path changed: {new_path}", "INFO")

    def _change_folder(self):
        current = router.get_noritsu_root()
        base = router.get_noritsu_base()
        start = current if current and Path(current).exists() else base
        folder = QFileDialog.getExistingDirectory(
            self, "Select Scanner Folder", start,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            if router.set_noritsu_root(folder):
                self._log(f"Changed scanner folder: {folder}", "SUCCESS")
                self._update_scan_path_label()
            else:
                QMessageBox.warning(self, "Invalid Path", f"Cannot access:\n{folder}")

    def _create_date_folder(self):
        base_path = router.get_noritsu_base()
        if not base_path:
            QMessageBox.warning(self, "No Base Path", "No base path configured in .env.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Create Date Folder")
        dlg.setMinimumWidth(360)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel("Select date:"))
        de = QDateEdit(QDate.currentDate())
        de.setCalendarPopup(True)
        de.setDisplayFormat("yyyy-MM-dd")
        lay.addWidget(de)

        preview = QLabel("")
        preview.setStyleSheet("color: #0066cc; font-weight: bold;")
        lay.addWidget(preview)

        def _upd():
            ds = de.date().toString("yyyyMMdd")
            fp = f"{base_path}\\{ds}" if base_path.startswith("\\\\") else os.path.join(base_path, ds)
            preview.setText(f"Will create: {fp}")
        de.dateChanged.connect(_upd)
        _upd()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        ds = de.date().toString("yyyyMMdd")
        fp = f"{base_path}\\{ds}" if base_path.startswith("\\\\") else os.path.join(base_path, ds)
        try:
            Path(fp).mkdir(parents=True, exist_ok=True)
            self._log(f"Created: {fp}", "SUCCESS")
            reply = QMessageBox.question(
                self, "Switch?", f"Created {fp}\n\nSwitch scanner here?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes and router.set_noritsu_root(fp):
                self._update_scan_path_label()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_active_batch(self) -> OrderBatch:
        """Return the batch that incoming scans should go into."""
        # Try to find by stored active key
        for b in self.order_queue:
            if b.order_input == self._active_order_input and b.status == "pending":
                return b
        # Fall back: last pending non-unassigned, else unassigned
        for b in reversed(self.order_queue):
            if b.order_input != UNASSIGNED and b.status == "pending":
                self._active_order_input = b.order_input
                return b
        self._active_order_input = UNASSIGNED
        return self._unassigned

    def _find_batch(self, order_input: str) -> Optional[OrderBatch]:
        for b in self.order_queue:
            if b.order_input == order_input:
                return b
        return None

    def _find_batch_index(self, order_input: str) -> Optional[int]:
        for i, b in enumerate(self.order_queue):
            if b.order_input == order_input:
                return i
        return None

    def _log(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"INFO": "black", "SUCCESS": "green", "WARNING": "orange", "ERROR": "red"}
        color = colors.get(level, "black")
        self._log_text.append(f'<span style="color:{color}">[{ts}] {message}</span>')
        self._log_text.verticalScrollBar().setValue(
            self._log_text.verticalScrollBar().maximum()
        )

    def _on_settling_update(self, scan_name: str, file_count: int, size_mb: float):
        self._settling[scan_name] = (file_count, size_mb)
        self._update_settling_display()

    def _update_settling_display(self):
        if not self._settling:
            self._settling_group.setVisible(False)
            return
        lines = [f"{n}  —  {c} files, {s:.1f} MB"
                 for n, (c, s) in self._settling.items()]
        self._settling_label.setText("\n".join(lines))
        self._settling_group.setVisible(True)

    def _warn_if_duplicate(self, scan_name: str):
        owners = [b.display_name for b in self.order_queue
                  if scan_name in b.twin_checks]
        if len(owners) > 1:
            QMessageBox.warning(
                self, "Duplicate Twin Check",
                f"{scan_name} appears in multiple orders:\n"
                + "\n".join(f"  • {o}" for o in owners)
            )

    def _on_drop_scan(self, src_order: str, scan_name: str, dst_order: str):
        src = self._find_batch(src_order)
        dst = self._find_batch(dst_order)
        if not src or not dst:
            return
        if scan_name in src.twin_checks:
            src.twin_checks.remove(scan_name)
        if scan_name not in dst.twin_checks:
            dst.twin_checks.append(scan_name)
        self._warn_if_duplicate(scan_name)
        self._log(f"Dropped {scan_name}: {src.display_name} → {dst.display_name}", "INFO")
        self._rebuild_right_panel()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_stripe_geometry()

    def _update_stripe_geometry(self):
        if not hasattr(self, '_stripe'):
            return
        cw = self.centralWidget()
        if not cw:
            return
        stripe_w = cw.width() // 8
        stripe_h = self._stripe.height()
        y = cw.height() - stripe_h - 50   # 50px gap from bottom edge
        self._stripe.setGeometry(0, y, stripe_w, stripe_h)

    def closeEvent(self, event):
        self.scanner.stop()
        self.scanner.wait(2000)
        for w in self._upload_workers.values():
            w.wait(1000)
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

LIGHT_THEME = """
    QMainWindow { background-color: #d3d3d3; color: #000000; }
    QWidget     { background-color: #d3d3d3; color: #000000; }
    QLabel      { color: #000000; }
    QGroupBox {
        font-weight: bold; font-size: 11pt; color: #000000;
        border: 2px solid #808080; border-radius: 3px;
        margin-top: 10px; padding-top: 10px;
        background-color: #e8e8e8;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #000000; }
    QLineEdit  { background-color: white; color: #000000; border: 1px solid #808080; padding: 5px; font-size: 11pt; }
    QPushButton {
        background-color: #c0c0c0; color: #000000;
        border: 1px solid #808080; padding: 5px 15px;
        min-height: 25px; font-size: 11pt; font-weight: bold;
    }
    QPushButton:hover    { background-color: #b0b0b0; }
    QPushButton:pressed  { background-color: #a0a0a0; }
    QPushButton:disabled { background-color: #e0e0e0; color: #808080; }
    QTextEdit  { background-color: white; color: #000000; border: 1px solid #808080; font-size: 10pt; }
    QScrollArea { background-color: #d3d3d3; border: none; }
    QProgressBar {
        border: 1px solid #808080; background-color: #e8e8e8;
        color: #000000; text-align: center; font-size: 10pt;
    }
    QProgressBar::chunk { background-color: #4a9eff; }
"""


def main():
    import signal

    app = QApplication(sys.argv)

    def _sigint(sig, frame):
        app.quit()
        sys.exit(0)
    signal.signal(signal.SIGINT, _sigint)

    app.setStyleSheet(LIGHT_THEME)
    win = ScannerOrderQueueGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
