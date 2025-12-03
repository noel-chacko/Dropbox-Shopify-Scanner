#!/usr/bin/env python3
"""
GUI for Scanner Router Direct - Runs alongside the CLI
"""

import sys
import os
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QGroupBox, QMessageBox,
    QSplitter, QFrame, QMenuBar, QToolBar, QMenu, QDateEdit, QDialog,
    QDialogButtonBox
)
import re
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QObject, QDate
from PySide6.QtGui import QFont, QColor, QFontDatabase, QIcon, QAction

# Import the scanner router module
import scanner_router_direct as router

class ScannerWorker(QThread):
    """Worker thread that runs the scanner loop"""
    status_update = Signal(str)
    error_occurred = Signal(str, str)
    path_changed = Signal(str)  # Emit when path changes
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.current_root = None
        self.existing_folders = set()
        
    def update_path(self, new_path: str):
        """Update the scan path and reset existing folders"""
        self.current_root = Path(new_path)
        self.existing_folders = set()
        if self.current_root.exists():
            self.existing_folders = {d.name for d in self.current_root.iterdir() if d.is_dir()}
        self.path_changed.emit(new_path)
        
    def run(self):
        """Run the scanner loop"""
        # Initialize with current path
        current_path = router.get_noritsu_root()
        self.current_root = Path(current_path)
        last_scan = time.time()
        
        if self.current_root.exists():
            self.existing_folders = {d.name for d in self.current_root.iterdir() if d.is_dir()}
        
        while self.running:
            try:
                # Check if path has changed
                new_path = router.get_noritsu_root()
                if str(self.current_root) != new_path:
                    self.update_path(new_path)
                
                if time.time() - last_scan < router.SCAN_INTERVAL:
                    time.sleep(0.1)
                    continue
                
                last_scan = time.time()
                
                if not self.current_root.exists():
                    self.status_update.emit(f"⚠️ Cannot access: {self.current_root}")
                    time.sleep(5)
                    continue
                
                # Scan for new directories
                for scan_dir in self.current_root.iterdir():
                    if not scan_dir.is_dir():
                        continue
                    if scan_dir.name in self.existing_folders:
                        continue
                    
                    # Process scan (this will trigger callbacks)
                    router.process_scan(scan_dir)
                    
            except Exception as e:
                self.error_occurred.emit("Scanner Loop", str(e))
                time.sleep(1)
    
    def stop(self):
        self.running = False

class OrderWorker(QObject):
    """Worker object for order operations that can emit signals"""
    order_found = Signal(dict)  # Emits order info for confirmation
    order_not_found = Signal(str)  # Emits order input that wasn't found
    order_set_result = Signal(bool, str)  # Emits (success, order_input)
    
    def search_and_confirm(self, order_input: str):
        """Search for order in background thread"""
        order_num = order_input
        m = re.match(r"^#?(\d+)(.*)$", order_input)
        if m:
            order_num = m.group(1)
        
        results = router.shopify_search_orders(f"name:{order_num}")
        if not results:
            self.order_not_found.emit(order_input)
            return
        
        order = results[0]
        order_info = {
            "order_input": order_input,
            "order_no": order.get("name", "Unknown"),
            "email": (order.get("customer") or {}).get("email") or order.get("email") or "unknown"
        }
        self.order_found.emit(order_info)
    
    def set_order(self, order_input: str):
        """Set the order in background thread"""
        success = router.set_order_gui(order_input)
        self.order_set_result.emit(success, order_input)

class ScannerRouterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scanner Router - Direct")
        self.setGeometry(100, 100, 1080, 720)  # 10% smaller than original (1200x800)
        
        # Set industrial grey color scheme with better readability
        self.setStyleSheet("""
            QMainWindow {
                background-color: #d3d3d3;
                color: #000000;
            }
            QWidget {
                background-color: #d3d3d3;
                color: #000000;
            }
            QLabel {
                color: #000000;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 11pt;
                color: #000000;
                border: 2px solid #808080;
                border-radius: 3px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #e8e8e8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #000000;
            }
            QLineEdit {
                background-color: white;
                color: #000000;
                border: 1px solid #808080;
                padding: 5px;
                font-size: 11pt;
            }
            QPushButton {
                background-color: #c0c0c0;
                color: #000000;
                border: 1px solid #808080;
                padding: 5px 15px;
                min-height: 25px;
                font-size: 11pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #a0a0a0;
            }
            QTableWidget {
                background-color: white;
                color: #000000;
                border: 1px solid #808080;
                gridline-color: #d0d0d0;
                font-size: 10pt;
            }
            QTableWidget::item {
                color: #000000;
            }
            QHeaderView::section {
                background-color: #c0c0c0;
                color: #000000;
                font-weight: bold;
                padding: 4px;
            }
            QTextEdit {
                background-color: white;
                color: #000000;
                border: 1px solid #808080;
                font-size: 10pt;
            }
            QProgressBar {
                border: 1px solid #808080;
                background-color: #e8e8e8;
                color: #000000;
                text-align: center;
                font-size: 10pt;
            }
            QProgressBar::chunk {
                background-color: #4a9eff;
            }
            QMenuBar {
                background-color: #c0c0c0;
                color: #000000;
                font-size: 11pt;
            }
            QMenuBar::item:selected {
                background-color: #b0b0b0;
            }
            QMenu {
                background-color: #e8e8e8;
                color: #000000;
                border: 1px solid #808080;
            }
            QMenu::item:selected {
                background-color: #c0c0c0;
            }
            QToolBar {
                background-color: #c0c0c0;
                border: 1px solid #808080;
            }
        """)
        
        # Setup GUI callbacks
        self.setup_callbacks()
        
        # Create UI first (so all widgets exist)
        self.init_ui()
        
        # Create menu bar and toolbar after UI is created
        self.create_menu_bar()
        self.create_toolbar()
        
        # Create order worker thread
        self.order_worker_thread = QThread()
        self.order_worker = OrderWorker()
        self.order_worker.moveToThread(self.order_worker_thread)
        self.order_worker.order_found.connect(self.on_order_found)
        self.order_worker.order_not_found.connect(self.on_order_not_found)
        self.order_worker.order_set_result.connect(self.on_order_set_result)
        self.order_worker_thread.start()
        
        # Start scanner worker thread
        self.worker = ScannerWorker()
        self.worker.status_update.connect(self.update_status)
        self.worker.error_occurred.connect(self.show_error)
        self.worker.path_changed.connect(self.on_scan_path_changed)
        self.worker.start()
        
        # Update timer for refreshing order info
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.refresh_order_info)
        self.update_timer.start(1000)  # Update every second
        
        # Initial order info refresh
        self.refresh_order_info()
        
        # Update scan path display (will show auto-set date path)
        self.update_scan_path_display()
        
        # Log that path was auto-set to today's date
        current_path = router.get_noritsu_root()
        today_str = datetime.now().strftime("%Y-%m-%d")
        if today_str.replace("-", "") in current_path.replace("\\", "/"):
            self.log_message(f"Scanner path auto-set to today's date: {current_path}", "INFO")
    
    def update_scan_path_display(self):
        """Update the scan path label"""
        current_path = router.get_noritsu_root()
        self.scan_path_label.setText(f"Watching: {current_path}")
    
    def on_scan_path_changed(self, new_path: str):
        """Handle scan path change"""
        self.scan_path_label.setText(f"Watching: {new_path}")
        self.log_message(f"Scanner path changed to: {new_path}", "INFO")
    
    def change_scan_date(self):
        """Open dialog to change scan date"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Change Scanner Date")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # Instructions
        info_label = QLabel("Select a date to scan. The path will be:\nBASE_PATH\\YYYYMMDD")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Date picker
        date_label = QLabel("Date:")
        layout.addWidget(date_label)
        
        date_edit = QDateEdit()
        date_edit.setDate(QDate.currentDate())
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(date_edit)
        
        # Current path display
        base_path = router.get_noritsu_base()
        current_path = router.get_noritsu_root()
        current_label = QLabel(f"Current: {current_path}")
        current_label.setWordWrap(True)
        layout.addWidget(current_label)
        
        # Preview new path
        preview_label = QLabel("")
        preview_label.setWordWrap(True)
        preview_label.setStyleSheet("color: #0066cc; font-weight: bold;")
        layout.addWidget(preview_label)
        
        def update_preview():
            selected_date = date_edit.date()
            date_str = selected_date.toString("yyyyMMdd")
            # Use os.path.join to handle path separators correctly (Windows uses \)
            import os
            if base_path:
                # Preserve UNC path format if it starts with \\
                if base_path.startswith("\\\\"):
                    new_path = f"{base_path}\\{date_str}"
                else:
                    new_path = os.path.join(base_path, date_str)
            else:
                new_path = date_str
            preview_label.setText(f"New path: {new_path}")
        
        date_edit.dateChanged.connect(update_preview)
        update_preview()  # Initial preview
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.Accepted:
            selected_date = date_edit.date()
            date_str = selected_date.toString("yyyyMMdd")
            # Use os.path.join to handle path separators correctly
            import os
            if base_path:
                # Preserve UNC path format if it starts with \\
                if base_path.startswith("\\\\"):
                    new_path = f"{base_path}\\{date_str}"
                else:
                    new_path = os.path.join(base_path, date_str)
            else:
                new_path = date_str
            
            # Try to set the new path
            if router.set_noritsu_root(new_path):
                self.log_message(f"Changed scanner path to: {new_path}", "SUCCESS")
                # Worker will pick up the change automatically
            else:
                QMessageBox.warning(self, "Invalid Path", 
                                  f"Cannot access path:\n{new_path}\n\nPlease check the path exists.")
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Order menu
        order_menu = menubar.addMenu("Order")
        set_order_action = QAction("Set Order", self)
        set_order_action.triggered.connect(lambda: self.order_input.setFocus())
        order_menu.addAction(set_order_action)
        
        # View menu
        view_menu = menubar.addMenu("View")
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_order_info)
        view_menu.addAction(refresh_action)
        
        # Status menu
        status_menu = menubar.addMenu("Status")
        clear_log_action = QAction("Clear Log", self)
        clear_log_action.triggered.connect(self.log_text.clear)
        status_menu.addAction(clear_log_action)
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def create_toolbar(self):
        """Create the toolbar with icons"""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Set Order action
        set_order_action = QAction("Set Order", self)
        set_order_action.triggered.connect(lambda: self.order_input.setFocus())
        toolbar.addAction(set_order_action)
        
        toolbar.addSeparator()
        
        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_order_info)
        toolbar.addAction(refresh_action)
        
        toolbar.addSeparator()
        
        # Clear log action
        clear_log_action = QAction("Clear Log", self)
        clear_log_action.triggered.connect(self.log_text.clear)
        toolbar.addAction(clear_log_action)
    
    def show_about(self):
        """Show about dialog"""
        QMessageBox.about(self, "About Scanner Router", 
                         "Scanner Router - Direct\n\n"
                         "Automatically routes scanner output to customer Dropbox folders.")
    
    def setup_callbacks(self):
        """Setup callbacks for the router module"""
        router.gui_callbacks['order_changed'] = self.on_order_changed
        router.gui_callbacks['scan_detected'] = self.on_scan_detected
        router.gui_callbacks['upload_started'] = self.on_upload_started
        router.gui_callbacks['upload_progress'] = self.on_upload_progress
        router.gui_callbacks['upload_completed'] = self.on_upload_completed
        router.gui_callbacks['error'] = self.on_error
        router.gui_callbacks['status'] = self.on_status
    
    def init_ui(self):
        """Initialize the UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)
        
        # Left panel - Order and Controls
        left_panel = self.create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel - Logs and Status
        right_panel = self.create_right_panel()
        splitter.addWidget(right_panel)
        
        splitter.setStretchFactor(0, 2)  # Give more space to left panel
        splitter.setStretchFactor(1, 1)   # Less space to right panel
        
        main_layout.addWidget(splitter)
    
    def create_left_panel(self):
        """Create the left panel with order info and controls"""
        panel = QWidget()
        layout = QVBoxLayout()
        panel.setLayout(layout)
        
        # Current Order Group
        order_group = QGroupBox("Current Order")
        order_layout = QVBoxLayout()
        
        self.order_number_label = QLabel("No order set")
        self.order_number_label.setFont(QFont("Arial", 36, QFont.Bold))
        self.order_number_label.setStyleSheet("""
            QLabel {
                background-color: white;
                color: #000000;
                padding: 15px;
                border: 2px solid #808080;
            }
        """)
        self.order_number_label.setAlignment(Qt.AlignCenter)
        order_layout.addWidget(self.order_number_label)
        
        self.order_email_label = QLabel("")
        self.order_email_label.setFont(QFont("Arial", 11, QFont.Bold))
        self.order_email_label.setStyleSheet("color: #000000;")
        order_layout.addWidget(self.order_email_label)
        
        self.order_status_label = QLabel("")
        self.order_status_label.setFont(QFont("Arial", 10))
        self.order_status_label.setStyleSheet("color: #000000;")
        order_layout.addWidget(self.order_status_label)
        
        # Pending tags display (bigger and more prominent)
        self.pending_tags_label = QLabel("")
        self.pending_tags_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.pending_tags_label.setStyleSheet("""
            color: #0066cc;
            background-color: #e8f4f8;
            padding: 8px;
            border: 2px solid #0066cc;
            border-radius: 3px;
        """)
        self.pending_tags_label.setAlignment(Qt.AlignCenter)
        self.pending_tags_label.setWordWrap(True)
        order_layout.addWidget(self.pending_tags_label)
        
        # Tags buttons layout
        tags_buttons_layout = QHBoxLayout()
        
        # Change tags button
        self.change_tags_btn = QPushButton("Change Tags")
        self.change_tags_btn.clicked.connect(self.change_pending_tags)
        self.change_tags_btn.setFont(QFont("Arial", 11, QFont.Bold))
        self.change_tags_btn.setStyleSheet("""
            QPushButton {
                background-color: #c0c0c0;
                color: #000000;
                border: 2px solid #808080;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #a0a0a0;
            }
            QPushButton:disabled {
                background-color: #e0e0e0;
                color: #808080;
            }
        """)
        self.change_tags_btn.setEnabled(False)  # Disabled by default
        tags_buttons_layout.addWidget(self.change_tags_btn)
        
        # Apply tags button
        self.apply_tags_btn = QPushButton("Apply Pending Tags")
        self.apply_tags_btn.clicked.connect(self.apply_pending_tags)
        self.apply_tags_btn.setFont(QFont("Arial", 11, QFont.Bold))
        self.apply_tags_btn.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: 2px solid #0055aa;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0055aa;
            }
            QPushButton:pressed {
                background-color: #004499;
            }
            QPushButton:disabled {
                background-color: #c0c0c0;
                color: #808080;
            }
        """)
        self.apply_tags_btn.setEnabled(False)  # Disabled by default
        tags_buttons_layout.addWidget(self.apply_tags_btn)
        
        order_layout.addLayout(tags_buttons_layout)
        self.apply_tags_btn.hide()  # Hide initially (only show when tags exist)
        # Change Tags button is always visible but disabled until order is set
        
        self.order_dropbox_label = QLabel("")
        self.order_dropbox_label.setWordWrap(True)
        self.order_dropbox_label.setFont(QFont("Arial", 9))
        self.order_dropbox_label.setStyleSheet("color: #000000;")
        order_layout.addWidget(self.order_dropbox_label)
        
        order_group.setLayout(order_layout)
        layout.addWidget(order_group)
        
        # Set Order Controls
        controls_group = QGroupBox("Set Order")
        controls_layout = QVBoxLayout()
        controls_layout.setSpacing(15)  # More spacing
        
        self.order_input = QLineEdit()
        self.order_input.setPlaceholderText("Enter order number (e.g., 12345 or 12345s)")
        self.order_input.returnPressed.connect(self.set_order)
        
        # Track pending order confirmation
        self.pending_order_info = None
        # Make input field bigger
        input_font = QFont("Arial", 14)
        self.order_input.setFont(input_font)
        self.order_input.setMinimumHeight(50)
        self.order_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 2px solid #808080;
                background-color: white;
            }
        """)
        controls_layout.addWidget(self.order_input)
        
        self.set_order_btn = QPushButton("Set Order")
        self.set_order_btn.clicked.connect(self.set_order)
        # Make button bigger
        btn_font = QFont("Arial", 12, QFont.Bold)
        self.set_order_btn.setFont(btn_font)
        self.set_order_btn.setMinimumHeight(50)
        self.set_order_btn.setStyleSheet("""
            QPushButton {
                background-color: #c0c0c0;
                color: black;
                border: 2px solid #808080;
                padding: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #a0a0a0;
            }
        """)
        controls_layout.addWidget(self.set_order_btn)
        
        controls_group.setLayout(controls_layout)
        layout.addWidget(controls_group)
        
        # Scanner Path Group
        path_group = QGroupBox("Scanner Path")
        path_layout = QVBoxLayout()
        
        self.scan_path_label = QLabel("")
        self.scan_path_label.setFont(QFont("Arial", 9))
        self.scan_path_label.setWordWrap(True)
        self.scan_path_label.setStyleSheet("color: #000000;")
        path_layout.addWidget(self.scan_path_label)
        
        change_path_btn = QPushButton("Change Date")
        change_path_btn.clicked.connect(self.change_scan_date)
        path_layout.addWidget(change_path_btn)
        
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)
        
        # Recent Scans Group
        scans_group = QGroupBox("Recent Scans")
        scans_layout = QVBoxLayout()
        
        self.scans_table = QTableWidget()
        self.scans_table.setColumnCount(4)
        self.scans_table.setHorizontalHeaderLabels(["Scan", "Status", "Files", "Time"])
        self.scans_table.horizontalHeader().setStretchLastSection(True)
        scans_layout.addWidget(self.scans_table)
        
        scans_group.setLayout(scans_layout)
        layout.addWidget(scans_group)
        
        layout.addStretch()
        
        return panel
    
    def create_right_panel(self):
        """Create the right panel with logs and progress"""
        panel = QWidget()
        layout = QVBoxLayout()
        panel.setLayout(layout)
        
        # Upload Progress Group
        progress_group = QGroupBox("Upload Progress")
        progress_layout = QVBoxLayout()
        
        self.current_upload_label = QLabel("No active upload")
        self.current_upload_label.setFont(QFont("Arial", 10, QFont.Bold))
        self.current_upload_label.setStyleSheet("color: #000000;")
        progress_layout.addWidget(self.current_upload_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        self.progress_status_label = QLabel("")
        self.progress_status_label.setFont(QFont("Arial", 9))
        self.progress_status_label.setStyleSheet("color: #000000;")
        progress_layout.addWidget(self.progress_status_label)
        
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)
        
        # Status Log
        log_group = QGroupBox("Status Log")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # Use system monospace font (cross-platform)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(8)
        self.log_text.setFont(font)
        self.log_text.setMaximumHeight(200)  # Limit log height
        log_layout.addWidget(self.log_text)
        
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.log_text.clear)
        log_layout.addWidget(clear_btn)
        
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
        
        return panel
    
    def log_message(self, message: str, level: str = "INFO"):
        """Add a message to the log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "black",
            "SUCCESS": "green",
            "WARNING": "orange",
            "ERROR": "red"
        }
        color = color_map.get(level, "black")
        formatted = f'<span style="color: {color}">[{timestamp}] {message}</span>'
        self.log_text.append(formatted)
        # Auto-scroll to bottom
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
    
    def refresh_order_info(self):
        """Refresh the current order information display"""
        with router.order_lock:
            order = router.current_order_data
        
        if not order:
            self.order_number_label.setText("No order set")
            self.order_email_label.setText("")
            self.order_status_label.setText("")
            self.order_dropbox_label.setText("")
            self.pending_tags_label.setText("")
            self.pending_tags_label.setVisible(False)
            self.change_tags_btn.setEnabled(False)
            self.apply_tags_btn.setEnabled(False)
            return
        
        if order.get("mode") == "stage":
            self.order_number_label.setText("STAGING MODE")
            self.order_email_label.setText("")
            self.order_status_label.setText("All scans will be uploaded to staging")
            self.order_dropbox_label.setText(f"Dropbox: {router.DROPBOX_ROOT}/_staging/")
            self.pending_tags_label.setText("")
            self.pending_tags_label.setVisible(False)
            self.change_tags_btn.setEnabled(False)
            self.apply_tags_btn.setEnabled(False)
        else:
            order_no = order.get("order_no", "Unknown")
            # Remove # if it's already in the order number
            if order_no.startswith("#"):
                order_no = order_no[1:]
            email = order.get("email", "unknown")
            self.order_number_label.setText(f"Order #{order_no}")
            self.order_email_label.setText(f"Email: {email}")
            
            pending_tags = order.get("pending_tags", [])
            if pending_tags:
                self.order_status_label.setText("Ready")
                tags_display = f"Pending Tags: {', '.join(pending_tags)}"
                self.pending_tags_label.setText(tags_display)
                self.pending_tags_label.setVisible(True)
                self.change_tags_btn.setEnabled(True)
                self.apply_tags_btn.setEnabled(True)
                self.apply_tags_btn.setVisible(True)
            else:
                self.order_status_label.setText("Ready")
                self.pending_tags_label.setText("")
                self.pending_tags_label.setVisible(False)
                # Change Tags button is always enabled when order is set (even without tags)
                self.change_tags_btn.setEnabled(True)
                self.apply_tags_btn.setEnabled(False)
                self.apply_tags_btn.setVisible(False)
            
            dropbox_path = order.get("dropbox_order_path", "")
            if dropbox_path:
                self.order_dropbox_label.setText(f"Dropbox: {dropbox_path}")
            else:
                self.order_dropbox_label.setText("Dropbox path not set")
    
    def set_order(self):
        """Set the order from the input field"""
        # If we have a pending order confirmation, confirm it immediately (no delay)
        if self.pending_order_info:
            order_input = self.pending_order_info["order_input"]
            self.pending_order_info = None
            self.order_input.clear()
            self.order_input.setPlaceholderText("Enter order number (e.g., 12345 or 12345s)")
            self.order_input.setEnabled(False)  # Disable input while processing
            self.log_message(f"Setting order: {order_input}...", "INFO")
            # Set the order immediately (no timer delay for faster response)
            self.order_worker.set_order(order_input)
            return
        
        # Otherwise, get input and search for the order
        order_input = self.order_input.text().strip()
        if not order_input:
            return
        
        # Search for the order
        self.order_input.setEnabled(False)  # Disable input while searching
        self.log_message(f"Searching for order: {order_input}...", "INFO")
        
        # Search for order in background thread
        QTimer.singleShot(0, lambda: self.order_worker.search_and_confirm(order_input))
    
    def on_order_found(self, order_info: dict):
        """Handle when order is found - show info and wait for confirmation"""
        order_input = order_info["order_input"]
        order_no = order_info["order_no"]
        email = order_info["email"]
        
        # Strip any leading '#' from order_no to avoid double #
        if order_no.startswith("#"):
            order_no = order_no[1:]
        
        # Re-enable input immediately
        self.order_input.setEnabled(True)
        
        # Store pending order info
        self.pending_order_info = order_info
        
        # Show order info in status and log (no popup)
        self.log_message(f"Found order: #{order_no} ({email}) - Press Enter again to confirm", "SUCCESS")
        
        # Update placeholder to show confirmation needed
        self.order_input.setPlaceholderText(f"Found: #{order_no} ({email}) - Press Enter to confirm")
        self.order_input.clear()  # Clear the input so they can just press Enter
        self.order_input.setFocus()  # Keep focus so Enter works immediately
    
    def on_order_not_found(self, order_input: str):
        """Handle when order is not found - show warning on main thread"""
        self.order_input.setEnabled(True)
        self.order_input.setPlaceholderText("Enter order number (e.g., 12345 or 12345s)")
        self.log_message(f"❌ No order found for: {order_input}", "ERROR")
        QMessageBox.warning(self, "Order Not Found", f"No order found matching: {order_input}")
    
    def change_pending_tags(self):
        """Change the pending tags for the current order"""
        with router.order_lock:
            order = router.current_order_data
        
        if not order or not isinstance(order, dict):
            QMessageBox.warning(self, "No Order", "No order is currently set.")
            return
        
        current_tags = order.get("pending_tags", [])
        order_no = order.get("order_no", "Unknown")
        
        # Create dialog to edit tags
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Change Tags for Order #{order_no}")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # Instructions
        info_label = QLabel("Enter tags separated by commas (e.g., s, bs, sp):")
        layout.addWidget(info_label)
        
        # Tags input
        tags_input = QLineEdit(", ".join(current_tags))
        tags_input.setPlaceholderText("e.g., s, bs, sp")
        layout.addWidget(tags_input)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.Accepted:
            tags_text = tags_input.text().strip()
            if tags_text:
                # Parse tags (split by comma, strip whitespace)
                new_tags = [t.strip() for t in tags_text.split(",") if t.strip()]
            else:
                new_tags = []
            
            # Update pending tags
            with router.order_lock:
                if router.current_order_data and isinstance(router.current_order_data, dict):
                    if new_tags:
                        router.current_order_data["pending_tags"] = new_tags
                    else:
                        router.current_order_data.pop("pending_tags", None)
            
            if new_tags:
                self.log_message(f"Changed pending tags to: {', '.join(new_tags)}", "INFO")
            else:
                self.log_message("Cleared pending tags", "INFO")
            
            self.refresh_order_info()  # Update display
    
    def apply_pending_tags(self):
        """Apply pending tags to the current order"""
        with router.order_lock:
            order = router.current_order_data
        
        if not order or not isinstance(order, dict):
            QMessageBox.warning(self, "No Order", "No order is currently set.")
            return
        
        pending_tags = order.get("pending_tags", [])
        order_gid = order.get("order_gid")
        order_no = order.get("order_no", "Unknown")
        
        if not pending_tags:
            QMessageBox.information(self, "No Pending Tags", "There are no pending tags to apply.")
            return
        
        if not order_gid:
            QMessageBox.warning(self, "No Order ID", "Cannot apply tags - order ID is missing.")
            return
        
        # Apply tags
        self.apply_tags_btn.setEnabled(False)
        self.change_tags_btn.setEnabled(False)
        self.log_message(f"Applying tags to order #{order_no}: {', '.join(pending_tags)}", "INFO")
        
        try:
            success = router.order_add_tags(order_gid, pending_tags)
            if success:
                # Remove pending tags from current order
                with router.order_lock:
                    if router.current_order_data and isinstance(router.current_order_data, dict):
                        router.current_order_data.pop("pending_tags", None)
                
                self.log_message(f"✅ Tags applied successfully: {', '.join(pending_tags)}", "SUCCESS")
                self.refresh_order_info()  # Update display
            else:
                self.log_message(f"❌ Failed to apply tags", "ERROR")
                self.apply_tags_btn.setEnabled(True)
                self.change_tags_btn.setEnabled(True)
        except Exception as e:
            self.log_message(f"❌ Error applying tags: {e}", "ERROR")
            self.apply_tags_btn.setEnabled(True)
            self.change_tags_btn.setEnabled(True)
    
    def on_order_set_result(self, success: bool, order_input: str):
        """Handle order set result - show message on main thread"""
        # Re-enable input
        self.order_input.setEnabled(True)
        self.order_input.setPlaceholderText("Enter order number (e.g., 12345 or 12345s)")
        
        if not success:
            self.log_message(f"❌ Failed to set order: {order_input}", "ERROR")
            QMessageBox.warning(self, "Error", f"Failed to set order: {order_input}")
        else:
            self.log_message(f"✅ Order set successfully: {order_input}", "SUCCESS")
    
    def set_staging(self):
        """Set to staging mode"""
        reply = QMessageBox.question(
            self,
            "Confirm Staging Mode",
            "Set to STAGING mode?\n\nAll scans will be uploaded to the staging folder.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        with router.order_lock:
            router.current_order_data = {"mode": "stage"}
        self.log_message("Set to STAGING mode", "INFO")
        self.refresh_order_info()
        if router.gui_callbacks['order_changed']:
            router.gui_callbacks['order_changed'](router.current_order_data)
    
    def on_order_changed(self, order_data: Dict[str, Any]):
        """Callback when order changes"""
        self.refresh_order_info()
        if order_data.get("mode") == "stage":
            self.log_message("Order changed: STAGING MODE", "INFO")
        else:
            order_no = order_data.get("order_no", "Unknown")
            # Remove # if it's already in the order number
            if order_no.startswith("#"):
                order_no = order_no[1:]
            dropbox_path = order_data.get("dropbox_order_path", "")
            if "/pending" in dropbox_path:
                QMessageBox.warning(
                    self,
                    "Using Pending Folder",
                    f"Order #{order_no} is using the /pending folder.\n\n"
                    "This usually means there was an error creating the customer's Dropbox folder.\n"
                    "Check the logs for details."
                )
            self.log_message(f"Order changed: #{order_no}", "SUCCESS")
    
    def on_scan_detected(self, scan_name: str, order: Dict[str, Any]):
        """Callback when a scan is detected"""
        self.log_message(f"📷 Scan detected: {scan_name}", "INFO")
        self.add_scan_to_table(scan_name, "Detected", 0, datetime.now())
    
    def on_upload_started(self, scan_name: str, dest: str):
        """Callback when upload starts"""
        self.log_message(f"📤 Starting upload: {scan_name} → {dest}", "INFO")
        self.current_upload_label.setText(f"Uploading: {scan_name}")
        self.progress_bar.setValue(0)
        self.progress_status_label.setText("Initializing...")
        self.update_scan_status(scan_name, "Uploading", 0)
    
    def on_upload_progress(self, scan_name: str, current: int, total: int, message: str):
        """Callback for upload progress"""
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar.setValue(percent)
            self.progress_status_label.setText(f"{current}/{total} files - {message}")
        else:
            self.progress_status_label.setText(message)
    
    def on_upload_completed(self, scan_name: str, file_count: int, dest: str):
        """Callback when upload completes"""
        self.log_message(f"✅ Upload completed: {scan_name} ({file_count} files)", "SUCCESS")
        self.current_upload_label.setText("No active upload")
        self.progress_bar.setValue(100)
        self.progress_status_label.setText(f"Completed: {file_count} files uploaded")
        self.update_scan_status(scan_name, "Completed", file_count)
        # Reset progress bar after a delay
        QTimer.singleShot(3000, lambda: self.progress_bar.setValue(0))
    
    def on_error(self, scan_name: str, error_msg: str):
        """Callback for errors"""
        self.log_message(f"❌ Error: {scan_name} - {error_msg}", "ERROR")
        self.update_scan_status(scan_name, "Error", 0)
    
    def add_scan_to_table(self, scan_name: str, status: str, file_count: int, timestamp: datetime):
        """Add or update a scan in the table"""
        # Check if scan already exists
        for row in range(self.scans_table.rowCount()):
            if self.scans_table.item(row, 0).text() == scan_name:
                # Update existing row
                self.scans_table.item(row, 1).setText(status)
                self.scans_table.item(row, 2).setText(str(file_count))
                self.scans_table.item(row, 3).setText(timestamp.strftime("%H:%M:%S"))
                return
        
        # Add new row
        row = self.scans_table.rowCount()
        self.scans_table.insertRow(row)
        self.scans_table.setItem(row, 0, QTableWidgetItem(scan_name))
        self.scans_table.setItem(row, 1, QTableWidgetItem(status))
        self.scans_table.setItem(row, 2, QTableWidgetItem(str(file_count)))
        self.scans_table.setItem(row, 3, QTableWidgetItem(timestamp.strftime("%H:%M:%S")))
        # Scroll to bottom
        self.scans_table.scrollToBottom()
    
    def update_scan_status(self, scan_name: str, status: str, file_count: int):
        """Update the status of a scan in the table"""
        for row in range(self.scans_table.rowCount()):
            if self.scans_table.item(row, 0).text() == scan_name:
                self.scans_table.item(row, 1).setText(status)
                self.scans_table.item(row, 2).setText(str(file_count))
                self.scans_table.item(row, 3).setText(datetime.now().strftime("%H:%M:%S"))
                break
    
    def update_status(self, message: str):
        """Update status message"""
        self.log_message(message, "INFO")
    
    def show_error(self, source: str, error: str):
        """Show an error message"""
        self.log_message(f"Error in {source}: {error}", "ERROR")
    
    def on_status(self, message: str):
        """Callback for status messages"""
        self.log_message(message, "WARNING")
    
    def closeEvent(self, event):
        """Handle window close"""
        if self.worker:
            self.worker.stop()
            self.worker.wait()
        if self.order_worker_thread:
            self.order_worker_thread.quit()
            self.order_worker_thread.wait()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = ScannerRouterGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

