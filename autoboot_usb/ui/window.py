"""AutoBoot USB compact desktop interface (Qt Widgets, not a browser UI)."""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..adapters import get_adapter
from ..catalogue import BY_KEY, CATALOGUE
from ..models import ImageSelection, UsbDevice, enough_capacity, format_size
from ..services.boot import inspect_boot
from ..services.download import ImageDownloader, ImageVerifier
from ..services.state import image_directory
from ..services.write_worker import UsbWriteWorker
from ..services.writer import trusted_release, validate_target
from .icons import os_mark, product_icon
from .styles import apply_theme

LOG = logging.getLogger("autoboot_usb.ui")


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def line() -> QFrame:
    item = QFrame()
    item.setObjectName("divider")
    item.setFixedHeight(1)
    return item


class DiskScanner(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, adapter, parent=None):
        super().__init__(parent)
        self.adapter = adapter

    def run(self) -> None:
        try:
            from ..adapters.windows import DiscoveryPermissionRequired, WindowsAdapter

            if isinstance(self.adapter, WindowsAdapter):
                try:
                    devices = self.adapter.list_usb_disks(self.isInterruptionRequested)
                except DiscoveryPermissionRequired:
                    from ..services.admin_discovery import discover_with_admin

                    devices = discover_with_admin(self.isInterruptionRequested)
            else:
                devices = self.adapter.list_usb_disks()
            if not self.isInterruptionRequested():
                self.completed.emit(devices)
        except Exception as exc:
            if not self.isInterruptionRequested():
                LOG.warning("USB discovery failed: %s", exc)
                self.failed.emit(str(exc))


class AutoBootWindow(QMainWindow):
    def __init__(self, *, auto_download: bool = True) -> None:
        super().__init__()
        self.adapter = get_adapter()
        self.boot_info = inspect_boot(self.adapter)
        self.settings = QSettings("AutoBoot USB", "AutoBoot USB")
        self.mode = "light" if self.settings.value("theme", "dark") == "light" else "dark"
        self.advanced_enabled = self.settings.value("advanced", False, type=bool)
        self.system = BY_KEY["kali"]
        self.image: ImageSelection | None = None
        self.devices: list[UsbDevice] = []
        self.selected_device: UsbDevice | None = None
        self.worker: ImageDownloader | ImageVerifier | UsbWriteWorker | None = None
        self.completed_target: UsbDevice | None = None
        self.scanner: DiskScanner | None = None
        self.auto_download = auto_download
        self.download_dialog = None
        self.pending_vendor_link = False

        self.setWindowTitle("AutoBoot USB  ·  Operating System Media Creator")
        self.setWindowIcon(product_icon())
        self.setMinimumSize(885, 635)
        self.resize(1120, 785)
        self._build()
        apply_theme(QApplication.instance(), self.mode)
        self._choose_system("kali")
        self._refresh_drives()
        LOG.info("Application opened on %s (%s)", platform.system(), platform.machine())

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(70)
        top = QHBoxLayout(header)
        top.setContentsMargins(24, 11, 25, 11)
        top.setSpacing(11)
        brand_icon = label("")
        brand_icon.setPixmap(product_icon(36).pixmap(36, 36))
        brand_icon.setFixedSize(40, 40)
        top.addWidget(brand_icon)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_text.addWidget(label("AutoBoot USB", "productName"))
        brand_text.addWidget(label("OPERATING SYSTEM MEDIA CREATOR", "brandLine"))
        top.addLayout(brand_text)
        top.addStretch(1)
        top.addWidget(label(f"v{__version__}", "eyebrow"))
        self.advanced_button = QToolButton()
        self.advanced_button.setText("Advanced  ON" if self.advanced_enabled else "Advanced  OFF")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setChecked(self.advanced_enabled)
        self.advanced_button.setToolTip("Show technical details and image checksums")
        self.advanced_button.clicked.connect(self._toggle_advanced)
        top.addWidget(self.advanced_button)
        self.theme_button = QToolButton()
        self.theme_button.setText("Light mode" if self.mode == "dark" else "Dark mode")
        self.theme_button.clicked.connect(self._toggle_theme)
        top.addWidget(self.theme_button)
        outer.addWidget(header)
        outer.addWidget(line())

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)
        outer.addLayout(main_row, 1)
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(245)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(13, 22, 0, 17)
        side_layout.setSpacing(0)
        cap = label("OPERATING SYSTEM", "eyebrow")
        cap.setContentsMargins(11, 0, 0, 11)
        side_layout.addWidget(cap)
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_root = QWidget()
        sidebar_list = QVBoxLayout(sidebar_root)
        sidebar_list.setContentsMargins(0, 0, 0, 0)
        sidebar_list.setSpacing(1)
        self.system_buttons: dict[str, QPushButton] = {}
        for system in CATALOGUE:
            item = QPushButton(f"{system.name}\n{system.subtitle}")
            item.setObjectName("osRow")
            item.setIcon(os_mark(system.mark, system.brand_color, 30))
            item.setIconSize(QSize(30, 30))
            item.setProperty("active", False)
            item.setToolTip(system.subtitle)
            item.setFixedHeight(51)
            item.clicked.connect(
                lambda _checked=False, key=system.key: self._choose_system(key, download=True)
            )
            sidebar_list.addWidget(item)
            self.system_buttons[system.key] = item
        sidebar_list.addStretch(1)
        sidebar_scroll.setWidget(sidebar_root)
        side_layout.addWidget(sidebar_scroll, 1)
        side_layout.addWidget(line())
        txt = label(
            "Select a system to download its image.\nSaved in this app's Images folder.",
            "helper",
            True,
        )
        txt.setContentsMargins(11, 13, 10, 0)
        side_layout.addWidget(txt)
        images_button = QPushButton("Open Images folder  ↗")
        images_button.clicked.connect(self._open_images_folder)
        side_layout.addWidget(images_button)
        main_row.addWidget(side)

        scroll = QScrollArea()
        self.content_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setObjectName("contentScroll")
        content = QFrame()
        content.setObjectName("content")
        pane = QVBoxLayout(content)
        pane.setContentsMargins(30, 24, 36, 22)
        pane.setSpacing(0)
        intro = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        self.overline = label("SELECTED SYSTEM  /  LINUX", "eyebrow")
        self.page_title = label("Kali Linux", "pageTitle")
        self.page_subtitle = label("Security testing distribution", "pageSubtitle")
        heading.addWidget(self.overline)
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        intro.addLayout(heading, 1)
        self.site_button = QPushButton("Official website  ↗")
        self.site_button.clicked.connect(self._open_website)
        intro.addWidget(self.site_button, alignment=Qt.AlignmentFlag.AlignTop)
        pane.addLayout(intro)
        pane.addSpacing(20)
        pane.addWidget(line())
        pane.addSpacing(17)

        pane.addLayout(self._section_header("01", "Choose an operating system image"))
        pane.addSpacing(13)
        options = QHBoxLayout()
        options.setSpacing(13)
        release_col = QVBoxLayout()
        release_col.setSpacing(6)
        release_col.addWidget(label("Version / edition", "fieldName"))
        self.release_box = QComboBox()
        self.release_box.currentIndexChanged.connect(self._version_changed)
        release_col.addWidget(self.release_box)
        options.addLayout(release_col, 2)
        arch_col = QVBoxLayout()
        arch_col.setSpacing(6)
        arch_col.addWidget(label("Target architecture", "fieldName"))
        self.arch_box = QComboBox()
        self.arch_box.addItems(["x86-64 (Intel / AMD)"])
        self.arch_box.setEnabled(False)
        arch_col.addWidget(self.arch_box)
        options.addLayout(arch_col, 1)
        pane.addLayout(options)
        pane.addSpacing(11)
        source_row = QHBoxLayout()
        source_row.setSpacing(9)
        self.download_button = QPushButton("Download official image")
        self.download_button.setObjectName("primaryButton")
        self.download_button.clicked.connect(self._download)
        source_row.addWidget(self.download_button)
        self.import_button = QPushButton("Use local ISO…")
        self.import_button.clicked.connect(self._import_iso)
        source_row.addWidget(self.import_button)
        self.cancel_button = QPushButton("Cancel download")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_download)
        source_row.addWidget(self.cancel_button)
        source_row.addStretch(1)
        pane.addLayout(source_row)
        pane.addSpacing(10)

        image_well = QFrame()
        image_well.setObjectName("imageWell")
        image_box = QVBoxLayout(image_well)
        image_box.setContentsMargins(14, 11, 14, 11)
        image_box.setSpacing(5)
        self.image_status = label("No image selected", "fieldName")
        self.image_hint = label(
            "Choose a trusted download or browse for an ISO file.", "helper", True
        )
        self.image_path = label("", "helper", True)
        image_box.addWidget(self.image_status)
        image_box.addWidget(self.image_hint)
        image_box.addWidget(self.image_path)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(9)
        self.progress.setVisible(False)
        image_box.addWidget(self.progress)
        self.progress_label = label("", "helper")
        self.progress_label.setVisible(False)
        image_box.addWidget(self.progress_label)
        pane.addWidget(image_well)
        pane.addSpacing(22)
        pane.addWidget(line())
        pane.addSpacing(17)

        device_title = self._section_header("02", "Select a USB drive")
        device_title.addStretch(1)
        self.scan_button = QPushButton("↻  Refresh drives")
        self.scan_button.clicked.connect(self._scan_clicked)
        device_title.addWidget(self.scan_button)
        pane.addLayout(device_title)
        pane.addSpacing(7)
        pane.addWidget(
            label(
                "Only external USB disks are shown. Internal and system drives are excluded.",
                "helper",
            )
        )
        pane.addSpacing(10)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["USB DEVICE", "CAPACITY", "DISK IDENTIFIER"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setColumnWidth(1, 92)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setFixedHeight(121)
        self.table.itemSelectionChanged.connect(self._drive_changed)
        pane.addWidget(self.table)
        pane.addSpacing(6)
        self.disk_message = label("Searching for USB storage devices…", "helper", True)
        pane.addWidget(self.disk_message)
        pane.addSpacing(22)
        pane.addWidget(line())
        pane.addSpacing(17)

        pane.addLayout(self._section_header("03", "Create bootable media"))
        pane.addSpacing(8)
        self.compat_message = label("Choose a verified image and a USB drive.", "helper", True)
        pane.addWidget(self.compat_message)
        pane.addSpacing(11)
        strip = QFrame()
        strip.setObjectName("infoStrip")
        st_layout = QHBoxLayout(strip)
        st_layout.setContentsMargins(12, 10, 12, 10)
        warning = label("!", "number")
        st_layout.addWidget(warning, 0, Qt.AlignmentFlag.AlignTop)
        st_layout.addWidget(
            label(
                "Writing an image removes every partition and file on the selected USB. "
                "Check the drive name and capacity before continuing. "
                "Keep the USB connected until writing and verification finish.",
                "helper",
                True,
            ),
            1,
        )
        pane.addWidget(strip)
        pane.addSpacing(12)
        action_row = QHBoxLayout()
        self.create_button = QPushButton("Create bootable USB")
        self.create_button.setObjectName("primaryButton")
        self.create_button.setEnabled(False)
        self.create_button.setToolTip("Choose a verified image and the USB drive you want to use")
        self.create_button.clicked.connect(self._create_usb)
        action_row.addWidget(self.create_button)
        self.restart_button = QPushButton("Boot instructions")
        self.restart_button.setEnabled(False)
        self.restart_button.setToolTip("Available only after a USB write has been verified")
        self.restart_button.clicked.connect(self._restart_guidance)
        action_row.addWidget(self.restart_button)
        self.stop_write_button = QPushButton("Stop USB creation")
        self.stop_write_button.setToolTip("Stop writing. The USB will need to be created again")
        self.stop_write_button.setVisible(False)
        self.stop_write_button.clicked.connect(self._cancel_download)
        action_row.addWidget(self.stop_write_button)
        action_row.addStretch(1)
        pane.addLayout(action_row)
        pane.addSpacing(11)

        # USB writing has its own status area, close to the Create button.
        # Download/ISO-check progress above remains independent.
        self.write_stage = QFrame()
        self.write_stage.setObjectName("writeStage")
        write_layout = QVBoxLayout(self.write_stage)
        write_layout.setContentsMargins(13, 12, 13, 12)
        write_layout.setSpacing(9)
        progress_heading = QHBoxLayout()
        progress_heading.addWidget(label("USB CREATION", "eyebrow"))
        progress_heading.addStretch()
        self.write_percent = label("0.0%", "writePercent")
        progress_heading.addWidget(self.write_percent)
        write_layout.addLayout(progress_heading)
        phase_row = QHBoxLayout()
        phase_row.setSpacing(7)
        self.write_phase_labels: dict[str, QLabel] = {}
        for phase, title in (
            ("checking", "1  CHECK IMAGE"),
            ("writing", "2  WRITE USB"),
            ("verifying", "3  VERIFY USB"),
        ):
            step = label(title, "writeStep")
            step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step.setProperty("stepStatus", "pending")
            phase_row.addWidget(step, 1)
            self.write_phase_labels[phase] = step
        write_layout.addLayout(phase_row)
        self.write_stage_progress = QProgressBar()
        self.write_stage_progress.setAccessibleName("USB creation progress")
        self.write_stage_progress.setTextVisible(False)
        self.write_stage_progress.setRange(0, 1000)
        self.write_stage_progress.setValue(0)
        self.write_stage_progress.setFixedHeight(18)
        write_layout.addWidget(self.write_stage_progress)
        self.write_stage_detail = label("", "helper", True)
        write_layout.addWidget(self.write_stage_detail)
        retry_row = QHBoxLayout()
        self.start_over_button = QPushButton("↻  Clear and refresh drives")
        self.start_over_button.setToolTip(
            "Clear this result and scan USB drives again, e.g. after reconnecting the USB"
        )
        self.start_over_button.setVisible(False)
        self.start_over_button.clicked.connect(self._start_over)
        retry_row.addWidget(self.start_over_button)
        retry_row.addStretch(1)
        write_layout.addLayout(retry_row)
        self.write_stage.setVisible(False)
        self.last_write_phase = ""
        pane.addWidget(self.write_stage)
        pane.addSpacing(8)
        boot_help_button = QPushButton("How to open the boot menu  ↗")
        boot_help_button.setFlat(True)
        boot_help_button.setToolTip("Read platform-specific startup instructions")
        boot_help_button.clicked.connect(self._boot_help)
        pane.addWidget(boot_help_button, 0, Qt.AlignmentFlag.AlignLeft)
        pane.addSpacing(16)

        self.advanced_area = QFrame()
        adv = QVBoxLayout(self.advanced_area)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setSpacing(9)
        adv.addWidget(line())
        adv.addSpacing(6)
        adv.addLayout(self._section_header("A", "Technical details"))
        self.advanced_firmware = label("Host firmware: " + self.boot_info.firmware, "helper")
        self.advanced_host = label(
            "Host: " + platform.system() + "  ·  " + platform.machine(), "helper"
        )
        self.advanced_hash = label("Expected SHA-256: –", "helper", True)
        self.advanced_image = label("Selected image: –", "helper", True)
        adv.addWidget(self.advanced_firmware)
        adv.addWidget(self.advanced_host)
        adv.addWidget(self.advanced_hash)
        adv.addWidget(self.advanced_image)
        self.advanced_area.setVisible(self.advanced_enabled)
        pane.addWidget(self.advanced_area)
        pane.addStretch(1)
        scroll.setWidget(content)
        main_row.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(37)
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(20, 0, 23, 0)
        foot.setSpacing(12)
        self.status_dot = label("●", "statusOK")
        foot.addWidget(self.status_dot)
        self.status_text = label("Ready to prepare an operating-system image", "helper")
        foot.addWidget(self.status_text)
        foot.addStretch(1)
        foot.addWidget(label(f"AUTOBOOT USB  ·  v{__version__}", "eyebrow"))
        outer.addWidget(line())
        outer.addWidget(footer)

    @staticmethod
    def _section_header(number: str, heading: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label(number, "number"))
        row.addWidget(label(heading, "sectionName"))
        return row

    def _choose_system(self, key: str, *, download: bool = False) -> None:
        if self.worker is not None or self.download_dialog is not None:
            return
        self.completed_target = None
        self._reset_write_stage()
        self.system = BY_KEY[key]
        os_info = self.system
        for item_key, button in self.system_buttons.items():
            active = item_key == key
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self.page_title.setText(os_info.name)
        self.page_subtitle.setText(os_info.subtitle)
        self.overline.setText(
            "SELECTED SYSTEM  /  "
            + (
                "LINUX"
                if key in {"kali", "ubuntu", "debian", "mint", "fedora", "arch", "parrot"}
                else ("WINDOWS" if key.startswith("win") else "OTHER")
            )
        )
        self.site_button.setEnabled(bool(os_info.official_page))
        self.release_box.blockSignals(True)
        self.release_box.clear()
        for release in os_info.releases:
            self.release_box.addItem(f"{release.version}   ·   {release.edition}", userData=release)
        if not os_info.releases:
            self.release_box.addItem("Import an ISO to continue")
            self.release_box.setEnabled(False)
        else:
            self.release_box.setEnabled(True)
        self.release_box.blockSignals(False)
        self.download_button.setEnabled(
            bool(self._release() and self._release().download_available) and self.worker is None
        )
        self.arch_box.setItemText(
            0, (os_info.releases[0].target_architecture if os_info.releases else "Imported image")
        )
        self.arch_box.setToolTip("Architecture of the target computer for this installer")
        self.image = None
        self._set_image_empty()
        self._update_release_details()
        self._show_status(f"Selected {os_info.name}")
        if download and self.auto_download:
            if key == "other":
                QTimer.singleShot(0, self._import_iso)
            else:
                self._queue_download()

    def _version_changed(self, _index: int) -> None:
        self.completed_target = None
        self._reset_write_stage()
        self.image = None
        self._set_image_empty()
        self._update_release_details()
        if self.auto_download:
            self._queue_download()

    def _queue_download(self) -> None:
        key, release = self.system.key, self._release()

        def start():
            if self.system.key == key and self._release() == release and self.worker is None:
                self._download()

        QTimer.singleShot(0, start)

    def _open_images_folder(self) -> None:
        try:
            folder = image_directory(self.system.key).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except OSError as exc:
            QMessageBox.warning(self, "Images folder unavailable", str(exc))

    def _release(self):
        # Disabled controls retain their selected value while a worker is running.
        return self.release_box.currentData()

    def _update_release_details(self) -> None:
        release = self._release()
        self.advanced_hash.setText(
            ("Apple package integrity: " + (release.verification_digest or "From Apple catalog"))
            if release and release.image_type == "apple-installer"
            else "Expected SHA-256: " + (release.sha256 if release else "No pinned digest")
        )
        self.compat_message.setText(self.system.support_message)
        self.download_button.setEnabled(
            bool(release and release.download_available) and self.worker is None
        )
        self._update_disk_capacity_message()

    def _set_image_empty(self) -> None:
        self.image_status.setText("No image selected")
        self.image_status.setObjectName("fieldName")
        self.image_hint.setText("Download an official image or browse for an ISO file.")
        self.image_path.clear()
        self.advanced_image.setText("Selected image: –")
        self._set_progress(False)

    def _open_website(self) -> None:
        if self.system.official_page:
            QDesktopServices.openUrl(QUrl(self.system.official_page))

    def _download(self) -> None:
        release = self._release()
        if (
            not release
            or not release.download_available
            or self.worker is not None
            or self.download_dialog
        ):
            return
        try:
            folder = image_directory(self.system.key)
        except OSError as exc:
            QMessageBox.warning(self, "Downloads unavailable", str(exc))
            return
        if (
            release.provider == "microsoft"
            and not release.url
            and not (folder / release.filename).is_file()
        ):
            from .microsoft_download import MicrosoftDownloadDialog

            dialog = MicrosoftDownloadDialog(self.system.key, release, self)
            self.download_dialog = dialog
            dialog.resolved.connect(self._microsoft_resolved)
            dialog.finished.connect(lambda _: self._download_dialog_finished(dialog))
            dialog.open()
            return
        self._start_download(release, folder)

    def _download_dialog_finished(self, dialog):
        if self.download_dialog is dialog:
            self.download_dialog = None
        dialog.deleteLater()

    def _microsoft_resolved(self, release):
        self.release_box.setItemData(self.release_box.currentIndex(), release)
        self.download_dialog = None
        self._start_download(release, image_directory(self.system.key))

    def _start_download(self, release, folder):
        self.worker = ImageDownloader(release, folder, self)
        self.worker.resolved.connect(self._release_resolved)
        self.worker.link_required.connect(self._vendor_link_required)
        self.image = None
        self.completed_target = None
        self.worker.progress.connect(self._download_progress)
        self.worker.completed.connect(self._download_completed)
        self.worker.failed.connect(self._image_failed)
        self.worker.cancelled.connect(self._download_cancelled)
        self.worker.finished.connect(self._worker_finished)
        self.image_status.setText("Downloading " + release.name)
        self.image_hint.setText(
            "The installer is checked against the integrity digest in Apple's catalog."
            if release.provider == "apple"
            else "The download is checked against a trusted, pinned SHA-256 checksum."
        )
        self.image_path.setText(str(folder / release.filename))
        self._set_progress(True)
        self._set_busy(True, download=True)
        self._show_status("Downloading official image…")
        LOG.info("Download started: %s", release.filename)
        self.worker.start()

    def _release_resolved(self, release):
        self.release_box.setItemData(self.release_box.currentIndex(), release)
        self.release_box.setItemText(
            self.release_box.currentIndex(), f"{release.version} · {release.edition}"
        )
        self.image_path.setText(str(image_directory(self.system.key) / release.filename))
        self._update_release_details()

    def _vendor_link_required(self):
        self.pending_vendor_link = True
        self._show_status("Getting a fresh Windows download link from Microsoft…")

    def _import_iso(self) -> None:
        if self.worker is not None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an existing installation image",
            str(Path.home()),
            "Installation images (*.iso *.img);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        if not path.is_file():
            QMessageBox.warning(self, "Image not found", "The selected file no longer exists.")
            return
        if path.stat().st_size < 1024 * 1024:
            QMessageBox.warning(self, "Invalid image", "That file is too small to be an OS image.")
            return
        self.image_status.setText("Checking image SHA-256…")
        self.image_hint.setText("Comparing with a trusted digest when one is available.")
        self.image_path.setText(str(path))
        self._set_progress(True)
        self.image = None
        self.completed_target = None
        self._set_busy(True, download=True)
        self.cancel_button.setText("Cancel verification")
        self.worker = ImageVerifier(path, self)
        self.worker.progress.connect(self._verify_progress)
        self.worker.completed.connect(
            lambda checksum, selected=path: self._import_completed(selected, checksum)
        )
        self.worker.failed.connect(self._image_failed)
        self.worker.cancelled.connect(self._download_cancelled)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()
        LOG.info("Imported image hashing started: %s", path.name)

    def _import_completed(self, path: Path, checksum: str) -> None:
        release = self._release()
        # Known imported editions are recognised by digest, never by filename.
        for index in range(self.release_box.count()):
            candidate = self.release_box.itemData(index)
            if candidate and candidate.sha256 == checksum:
                self.release_box.blockSignals(True)
                self.release_box.setCurrentIndex(index)
                self.release_box.blockSignals(False)
                release = candidate
                self.advanced_hash.setText("Expected SHA-256: " + candidate.sha256)
                break
        expected = release.sha256 if release else None
        self.image = ImageSelection(path, "imported", checksum, expected)
        if self.image.verified:
            self.image_status.setText("Image verified  ·  SHA-256 matched")
            self.image_hint.setText("Matches the selected official release.")
        elif expected:
            self.image_status.setText("Imported ISO  ·  checksum does not match selected release")
            self.image_hint.setText(
                "This may be a different release or a modified image. Authenticity not verified."
            )
        else:
            self.image_status.setText("Local ISO  ·  hash calculated, origin unverified")
            self.image_hint.setText(
                "Without a trusted expected checksum, a hash alone cannot confirm authenticity."
            )
        self.image_path.setText(str(path))
        self.advanced_image.setText("Computed SHA-256: " + checksum)
        self._set_progress(False)
        self._show_status("Local ISO image checked")
        self._update_disk_capacity_message()
        LOG.info("Imported image hash calculated. Trusted match=%s", self.image.verified)

    def _download_completed(self, path_str: str, checksum: str) -> None:
        path = Path(path_str)
        release = self._release()
        if release and release.filename == path.name and release.image_type == "apple-installer":
            self.image = ImageSelection(path, "apple", checksum, integrity_verified=True)
            self.image_status.setText("Apple installer downloaded and integrity checked")
            self.image_hint.setText(
                "Saved in Images/macos. Use a compatible Mac to create its installer USB."
            )
            self.image_path.setText(str(path))
            self.advanced_image.setText("Computed SHA-256: " + checksum)
        elif release and release.filename == path.name and checksum == release.sha256:
            self.image = ImageSelection(path, "official", checksum, release.sha256)
            self.image_status.setText("Official image verified  ·  SHA-256 matched")
            self.image_hint.setText(
                "Download completed successfully. Ready for the USB-writing stage."
            )
            self.advanced_image.setText("Computed SHA-256: " + checksum)
            self.image_path.setText(str(path))
            LOG.info("Official image verified: %s", path.name)
        else:
            self.image = None
            self.image_status.setText("Image verification could not be confirmed")
            self.image_hint.setText("The selected operating system changed during the download.")
        self._set_progress(False)
        self._show_status("Download and verification completed")
        self._update_disk_capacity_message()

    def _download_progress(self, downloaded: int, total: int, speed: float) -> None:
        self._update_progress(downloaded, total)
        transfer = f"{format_size(downloaded)} downloaded"
        if total:
            transfer += f" of {format_size(total)}"
        if speed > 0:
            transfer += f"  ·  {format_size(int(speed))}/s"
            if total and downloaded < total:
                transfer += f"  ·  about {int((total - downloaded) / speed / 60) + 1} min left"
        self.progress_label.setText(transfer)

    def _verify_progress(self, count: int, total: int) -> None:
        self._update_progress(count, total)
        self.progress_label.setText(f"Checking {format_size(count)} of {format_size(total)}")

    def _update_progress(self, count: int, total: int) -> None:
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(min(1000, int(count / total * 1000)))
        else:
            self.progress.setRange(0, 0)

    def _image_failed(self, message: str) -> None:
        LOG.warning("Image task failed: %s", message)
        self.image = None
        release = self._release()
        if release and release.provider == "microsoft" and release.url:
            from dataclasses import replace

            self.release_box.setItemData(self.release_box.currentIndex(), replace(release, url=""))
        self.image_status.setText("Image operation failed")
        self.image_hint.setText(message)
        self._set_progress(False)
        self._show_status("Image operation failed", error=True)
        QMessageBox.warning(self, "Image operation failed", message)

    def _cancel_download(self) -> None:
        if isinstance(self.worker, (ImageDownloader, ImageVerifier, UsbWriteWorker)):
            if isinstance(self.worker, UsbWriteWorker):
                response = QMessageBox.question(
                    self,
                    "Stop USB creation?",
                    "Stopping can leave the USB incomplete. You will need to create it again. Stop now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if response != QMessageBox.StandardButton.Yes:
                    return
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.stop_write_button.setEnabled(False)
            self._show_status("Stopping the current operation safely…")

    def _download_cancelled(self) -> None:
        self._set_image_empty()
        self.image = None
        self._update_actions()
        self._show_status("Image operation cancelled; partial download discarded")

    def _worker_finished(self) -> None:
        worker = self.worker
        if worker:
            worker.deleteLater()
        self.worker = None
        self._set_busy(False)
        if isinstance(worker, UsbWriteWorker) and self.completed_target is None:
            self._refresh_drives()
        if self.pending_vendor_link:
            self.pending_vendor_link = False
            QTimer.singleShot(0, self._download)

    def _set_progress(self, show: bool) -> None:
        self.progress.setVisible(show)
        self.progress_label.setVisible(show)
        if show:
            self.progress.setRange(0, 0)
            self.progress_label.setText("Starting…")

    def _set_busy(self, busy: bool, download: bool = False) -> None:
        release = self._release()
        self.download_button.setEnabled(not busy and bool(release and release.download_available))
        self.import_button.setEnabled(not busy)
        self.release_box.setEnabled(not busy and bool(self.system.releases))
        self.cancel_button.setVisible(busy and download)
        self.cancel_button.setEnabled(busy and download)
        if isinstance(self.worker, ImageDownloader):
            self.cancel_button.setText("Cancel download")
        writing = busy and isinstance(self.worker, UsbWriteWorker)
        self.stop_write_button.setVisible(writing)
        self.stop_write_button.setEnabled(writing)
        self.table.setEnabled(not busy)
        self._update_scan_button()
        for button in self.system_buttons.values():
            button.setEnabled(not busy)
        self._update_actions()

    def _update_scan_button(self) -> None:
        scanning = self.scanner is not None
        if scanning:
            stopping = self.scanner.isInterruptionRequested()
            self.scan_button.setText("Cancelling scan…" if stopping else "Cancel scan")
            self.scan_button.setEnabled(not stopping)
        else:
            self.scan_button.setText("↻  Refresh drives")
            self.scan_button.setEnabled(self.worker is None)

    def _scan_clicked(self) -> None:
        if self.scanner is not None:
            self.scanner.requestInterruption()
            self.disk_message.setText("USB scan cancelled. Select Refresh drives to scan again.")
            self._update_scan_button()
        else:
            self._refresh_drives()

    def _start_over(self) -> None:
        if self.worker is not None:
            return
        self.completed_target = None
        self._reset_write_stage()
        self._show_status("Ready. Reconnect the USB if needed, then create it again")
        self._refresh_drives()

    def _refresh_drives(self) -> None:
        if self.worker is not None:
            return
        if self.scanner and self.scanner.isRunning():
            return
        self.disk_message.setText("Scanning externally connected USB disks…")
        self.scanner = DiskScanner(self.adapter, self)
        self.scanner.completed.connect(self._disks_found)
        self.scanner.failed.connect(self._disks_failed)
        self.scanner.finished.connect(self._scanner_finished)
        self.scanner.start()
        self._update_scan_button()

    def _scanner_finished(self) -> None:
        if self.scanner:
            self.scanner.deleteLater()
        self.scanner = None
        self._update_scan_button()
        self._update_actions()

    def _disks_found(self, devices: list[UsbDevice]) -> None:
        previous = self.selected_device.device_id if self.selected_device else ""
        self.devices = devices
        self.selected_device = None
        self.table.blockSignals(True)
        self.table.setRowCount(len(devices))
        new_selection = -1
        for row, device in enumerate(devices):
            cells = (device.name, format_size(device.size_bytes), device.device_id)
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setToolTip(device.serial or device.device_id)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 34)
            if device.device_id == previous:
                new_selection = row
        self.table.clearSelection()
        if new_selection >= 0:
            self.table.selectRow(new_selection)
            self.selected_device = devices[new_selection]
        self.table.blockSignals(False)
        self._update_disk_capacity_message()
        LOG.info("Read-only USB discovery completed: %d selectable drives", len(devices))

    def _disks_failed(self, message: str) -> None:
        self.devices = []
        self.selected_device = None
        self.table.setRowCount(0)
        self.completed_target = None
        self._update_actions()
        self.disk_message.setText("USB detection failed: " + message)
        self._show_status("Unable to detect USB drives", error=True)

    def _drive_changed(self) -> None:
        self._reset_write_stage()
        index = self.table.currentRow()
        self.selected_device = self.devices[index] if 0 <= index < len(self.devices) else None
        self._update_disk_capacity_message()

    def _update_disk_capacity_message(self) -> None:
        self._update_actions()
        if not self.devices:
            self.disk_message.setText(
                "No eligible USB drives detected. Insert a USB drive and select Refresh drives."
            )
        elif not self.selected_device:
            self.disk_message.setText(
                f"{len(self.devices)} USB drive(s) found. Select the intended device above."
            )
        elif sys.platform == "win32" and not self.selected_device.serial:
            self.disk_message.setText(
                "This USB has no serial number. AutoBoot cannot identify it. Try a different USB."
            )
        elif self.image:
            try:
                if enough_capacity(self.selected_device, self.image.path):
                    self.disk_message.setText(
                        f"Selected: {self.selected_device.device_id}. Capacity is sufficient for this image."
                    )
                else:
                    self.disk_message.setText(
                        "Selected USB is too small for the image. Choose a larger USB drive."
                    )
            except OSError:
                self.disk_message.setText("The selected ISO is no longer accessible.")
        else:
            release = self._release()
            minimum = (
                f"  ·  Recommended {format_size(release.min_size_bytes)} minimum."
                if release
                else ""
            )
            self.disk_message.setText(
                f"Selected: {self.selected_device.device_id} ({format_size(self.selected_device.size_bytes)})."
                + minimum
            )

    def _boot_help(self) -> None:
        QMessageBox.information(
            self,
            "Booting from an external USB drive",
            f"Firmware detected: {self.boot_info.firmware}\n\n{self.boot_info.guidance}\n\n"
            "AutoBoot USB does not automatically select a firmware USB target or change "
            "persistent boot order. The verified-write flow provides guided boot instructions.",
        )

    def _update_actions(self, *_args) -> None:
        if not hasattr(self, "create_button"):
            return
        ready = False
        reason = "Choose a verified image and a USB drive"
        try:
            ready = bool(
                sys.platform == "win32"
                and self.worker is None
                and self.scanner is None
                and self.image
                and self.image.verified
                and self.selected_device
                and self._release()
                and self._release().image_type in {"hybrid-iso", "windows-iso"}
            )
            if ready:
                trusted_release(self.image.trusted_sha256)
                validate_target(
                    self.selected_device, self.selected_device, self.image.path.stat().st_size
                )
        except (OSError, ValueError) as exc:
            ready = False
            reason = str(exc)
        self.create_button.setEnabled(ready)
        self.create_button.setToolTip("Create and verify this USB image" if ready else reason)
        boot_ready = (
            self.worker is None
            and self.scanner is None
            and self.completed_target is not None
            and self.selected_device == self.completed_target
        )
        self.restart_button.setEnabled(boot_ready)

    def _create_usb(self) -> None:
        self._update_actions()
        if not self.create_button.isEnabled() or not self.image or not self.selected_device:
            return
        target = self.selected_device
        answer = QMessageBox.warning(
            self,
            "Confirm USB erasure",
            f"Erase ALL files and partitions on this USB?\n\n{target.name}\n"
            f"Disk: {target.device_id}\nCapacity: {format_size(target.size_bytes)}\n"
            f"Serial: {target.serial}\n\nImage: {self.image.path.name}\n\n"
            "Windows will request administrator approval and the helper will confirm the target.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.completed_target = None
        self.last_write_phase = "checking"
        self._write_phase("starting_helper", 0, 0)
        self.worker = UsbWriteWorker(self.image, target, self)
        self.worker.progress.connect(self._write_progress)
        self.worker.completed.connect(
            lambda digest, confirmed=target: self._write_completed(confirmed, digest)
        )
        self.worker.failed.connect(self._write_failed)
        self.worker.cancelled.connect(self._write_cancelled)
        self.worker.finished.connect(self._worker_finished)
        self._set_progress(False)
        self._set_busy(True)
        self._show_status("Checking the protected USB writer…")
        self.worker.start()

    def _reset_write_stage(self) -> None:
        if not hasattr(self, "write_stage") or isinstance(self.worker, UsbWriteWorker):
            return
        self.write_stage.setVisible(False)
        self.start_over_button.setVisible(False)
        self.last_write_phase = ""
        self.write_stage_progress.setRange(0, 1000)
        self.write_stage_progress.setValue(0)
        self.write_percent.setText("0.0%")
        for step in self.write_phase_labels.values():
            step.setProperty("stepStatus", "pending")
            step.style().unpolish(step)
            step.style().polish(step)

    def _write_phase(self, phase: str, count: int, total: int, message: str = "") -> None:
        """Drive an explicit, readable Check → Write → Verify stage UI."""
        newly_shown = self.write_stage.isHidden()
        self.write_stage.setVisible(True)
        if newly_shown:
            # Reveal the progress panel after Qt has laid out the taller content.
            QTimer.singleShot(0, lambda: self.content_scroll.ensureWidgetVisible(self.write_stage))
        stages = ("checking", "writing", "verifying")
        if phase in stages:
            self.last_write_phase = phase
        current = "writing" if phase == "flushing" else self.last_write_phase
        current_index = stages.index(current) if current in stages else 0
        for index, key in enumerate(stages):
            if phase == "complete":
                state = "done"
            elif phase in {"failed", "cancelled"} and index == current_index:
                state = "failed" if phase == "failed" else "stopped"
            elif index < current_index or (phase == "flushing" and index == 1):
                state = "done"
            elif index == current_index and phase not in {"failed", "cancelled", "flushing"}:
                state = "active"
            else:
                state = "pending"
            step = self.write_phase_labels[key]
            step.setProperty("stepStatus", state)
            step.style().unpolish(step)
            step.style().polish(step)
        # Always a measured progress bar. Waiting/failure must never leave an
        # indeterminate animation running as if data were being written.
        self.write_stage_progress.setRange(0, 1000)
        if phase == "complete":
            self.write_stage_progress.setValue(1000)
            self.write_percent.setText("100.0% · Verified")
        elif phase in {"failed", "cancelled"}:
            stopped = "Failed" if phase == "failed" else "Stopped"
            self.write_percent.setText(f"{self.write_stage_progress.value() / 10:.1f}% · {stopped}")
        elif phase not in {"failed", "cancelled"}:
            if total:
                measured = max(0, min(1000, count * 1000 // total))
                self.write_stage_progress.setValue(measured)
                self.write_percent.setText(f"{measured / 10:.1f}%")
            else:
                self.write_stage_progress.setValue(0)
                self.write_percent.setText("Waiting")
        descriptions = {
            "starting_helper": "Checking that the protected USB writer can start",
            "awaiting_admin": "Waiting for the Windows administrator (UAC) prompt…",
            "admin_approved": "Administrator approved. Starting the protected USB writer…",
            "confirming_usb": (
                "Administrator approved. Confirm the USB model and serial in the final "
                "erasure window. If you cannot see it, press Alt + Tab."
            ),
            "checking": "Checking the ISO checksum before erasing the USB",
            "preparing": "Preparing the selected USB and locking its volumes",
            "writing": "Copying the image to the USB drive — do not unplug",
            "flushing": "Saving pending writes to the USB drive",
            "verifying": "Reading the USB back and checking its SHA-256",
            "complete": "Complete — the USB read-back checksum matched",
            "failed": "Writing or verification failed — recreate the USB before using it",
            "cancelled": "Creation stopped — an incomplete USB must be recreated",
        }
        self.start_over_button.setVisible(phase in {"failed", "cancelled"})
        detail = message or descriptions.get(phase, phase)
        if total and phase not in {"failed", "cancelled"}:
            detail += f"  ·  {format_size(count)} / {format_size(total)}"
        self.write_stage_detail.setText(detail)
        self.write_stage_progress.setAccessibleDescription(detail)

    def _write_progress(self, phase: str, count: int, total: int) -> None:
        titles = {
            "starting_helper": "Checking the protected USB writer",
            "awaiting_admin": "Waiting for Windows administrator approval…",
            "admin_approved": "Administrator approved — launching USB writer",
            "confirming_usb": "Confirm USB erasure in the foreground dialog",
            "checking": "Checking source image",
            "preparing": "Preparing USB volumes",
            "writing": "Writing USB",
            "flushing": "Saving USB writes",
            "verifying": "Reading USB back",
            "complete": "Read-back check complete",
        }
        self._write_phase(phase, count, total)
        self._show_status(titles.get(phase, phase))

    def _write_completed(self, target: UsbDevice, checksum: str) -> None:
        if not self.image or checksum != self.image.trusted_sha256:
            self._write_failed("Writer completion did not match the selected image")
            return
        self.completed_target = target
        self._write_phase("complete", 0, 0)
        windows = self._release() and self._release().image_type == "windows-iso"
        self._show_status(
            "Windows installer created and every copied file verified"
            if windows
            else "USB written and full read-back SHA-256 verified"
        )
        QMessageBox.information(
            self,
            "USB image verified",
            (
                "Windows installation files were copied and each file's read-back checksum matched. "
                if windows
                else "The complete image was written and its read-back checksum matched. "
            )
            + "Boot compatibility still depends on the target computer. "
            "Select Boot instructions for the next steps.",
        )

    def _write_failed(self, message: str) -> None:
        self.completed_target = None
        self._write_phase("failed", 0, 0)
        self._show_status("USB creation failed; do not use it until recreated", error=True)
        QMessageBox.warning(self, "USB creation failed", message)

    def _write_cancelled(self, message: str) -> None:
        self.completed_target = None
        self._write_phase("cancelled", 0, 0, message)
        self._show_status(message)

    def _restart_guidance(self) -> None:
        self._update_actions()
        if not self.restart_button.isEnabled():
            return
        QMessageBox.information(
            self,
            "Boot from USB — guided steps",
            "The USB image passed read-back verification. Save your work first.\n\n"
            + self.boot_info.guidance
            + "\n\n"
            "Automatic USB selection is not available in this preview. Restart using your "
            "operating system's power menu and select the USB in the boot menu.",
        )

    def _toggle_theme(self) -> None:
        self.mode = "light" if self.mode == "dark" else "dark"
        self.settings.setValue("theme", self.mode)
        apply_theme(QApplication.instance(), self.mode)
        self.theme_button.setText("Light mode" if self.mode == "dark" else "Dark mode")

    def _toggle_advanced(self, enabled: bool) -> None:
        self.advanced_enabled = enabled
        self.advanced_area.setVisible(enabled)
        self.advanced_button.setText("Advanced  ON" if enabled else "Advanced  OFF")
        self.settings.setValue("advanced", enabled)

    def _show_status(self, text: str, error: bool = False) -> None:
        self.status_text.setText(text)
        self.status_dot.setObjectName("statusError" if error else "statusOK")
        self.status_dot.style().unpolish(self.status_dot)
        self.status_dot.style().polish(self.status_dot)

    def closeEvent(self, event) -> None:
        # Never destroy a running QThread. A user may cancel a download, then exit.
        waiting = []
        if self.worker and self.worker.isRunning():
            waiting.append(self.worker)
        if self.scanner and self.scanner.isRunning():
            waiting.append(self.scanner)
        if waiting:
            if isinstance(self.worker, (ImageDownloader, ImageVerifier, UsbWriteWorker)):
                response = QMessageBox.question(
                    self,
                    "Stop operation and exit?",
                    "An operation is still running. Stop it and close AutoBoot USB? "
                    "An interrupted USB write must be recreated before use.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if response != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self.worker.cancel()
            event.ignore()
            if self.scanner and self.scanner.isRunning():
                self.scanner.requestInterruption()
            for thread in waiting:
                thread.finished.connect(self.close)
            return
        LOG.info("Application closed")
        super().closeEvent(event)
