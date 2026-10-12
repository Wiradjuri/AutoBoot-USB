"""Headless Qt smoke checks, in a separate process from QCoreApplication tests."""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from autoboot_usb.catalogue import BY_KEY
from autoboot_usb.models import ImageSelection, UsbDevice
from autoboot_usb.ui.window import AutoBootWindow


class Adapter:
    def list_usb_disks(self):
        return []

    def firmware_mode(self):
        return "UEFI"

    def boot_guidance(self):
        return "Use the one-time boot menu."


app = QApplication([])
with patch("autoboot_usb.ui.window.get_adapter", return_value=Adapter()):
    window = AutoBootWindow(auto_download=False)
    window.show()
    deadline = time.monotonic() + 5
    while window.scanner is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert window.scanner is None
    assert window.system.key == "kali"
    assert window._release().edition == "Installer"
    assert not window.create_button.isEnabled()
    assert window.download_button.isEnabled()
    window._set_busy(True, download=True)
    assert not window.release_box.isEnabled()
    assert window._release() == BY_KEY["kali"].releases[0], "Busy control lost its release"
    window._download_progress(5_368_709_120, 6_442_450_944, 1_048_576.0)
    assert window.progress.value() == 833
    assert "5.4 GB" in window.progress_label.text()
    window._verify_progress(4_802_531_328, 4_802_531_328)
    assert window.progress.value() == 1000
    # Completion must retain the release while its combo box is disabled.
    release = window._release()
    window._download_completed("/tmp/" + release.filename, release.sha256)
    assert window.image and window.image.verified
    window._set_busy(False)
    window.release_box.setCurrentIndex(1)
    assert window._release().edition == "Live"
    assert not window.download_button.isEnabled(), "Dead Live URL must not be offered"
    assert window.image is None
    # A local file named differently is still recognised by its trusted digest.
    release = BY_KEY["kali"].releases[0]
    window._import_completed(Path("/tmp/renamed.iso"), release.sha256)
    assert window._release().edition == "Installer"
    assert window.image.verified
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "mock.iso"
        path.write_bytes(b"fake GUI fixture; no writer is launched")
        device = UsbDevice(
            "PhysicalDrive2", "Test USB", 16_000_000_000, "usb", True, False, serial="TEST-2"
        )
        window.image = ImageSelection(path, "official", release.sha256, release.sha256)
        window.selected_device = device
        window.devices = [device]
        with patch("autoboot_usb.ui.window.sys.platform", "win32"):
            window._update_actions()
            assert window.create_button.isEnabled()
            with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No):
                window._create_usb()
            assert window.worker is None, "Rejecting erase must not launch a worker"
            window.selected_device = UsbDevice(
                "PhysicalDrive2", "USB with no serial", 16_000_000_000, "usb", True, False
            )
            window._update_actions()
            assert not window.create_button.isEnabled()
            window.selected_device = device
            window.image = ImageSelection(path, "imported", "0" * 64, release.sha256)
            window._update_actions()
            assert not window.create_button.isEnabled()
        # Mock UI transitions only; no physical writer process is launched.
        window._write_phase("awaiting_admin", 0, 0)
        assert "administrator" in window.write_stage_detail.text().lower()
        assert window.write_stage_progress.maximum() == 1000
        assert window.write_stage_progress.value() == 0
        window._write_progress("admin_approved", 0, 0)
        assert "approved" in window.write_stage_detail.text().lower()
        window._write_progress("confirming_usb", 0, 0)
        assert "final erasure" in window.write_stage_detail.text().lower()
        window._write_progress("writing", 5_368_709_120, 6_442_450_944)
        assert window.write_stage.isVisible()
        assert window.write_stage_progress.value() == 833
        assert window.write_percent.text() == "83.3%"
        assert window.write_phase_labels["writing"].property("stepStatus") == "active"
        assert "5.4 GB" in window.write_stage_detail.text()
        with patch.object(QMessageBox, "warning"):
            window._write_failed("Simulated failed write")
        assert window.write_stage_progress.maximum() == 1000
        assert window.write_stage_progress.value() == 833
        assert window.write_percent.text() == "83.3% · Failed"
        assert window.completed_target is None
        # A new attempt begins at zero. Verification has its own byte progress.
        window._reset_write_stage()
        window._write_progress("starting_helper", 0, 0)
        assert window.write_stage_progress.value() == 0
        window._write_progress("writing", 0, 6_442_450_944)
        assert window.write_percent.text() == "0.0%"
        window._write_progress("writing", 6_442_450_944, 6_442_450_944)
        window._write_progress("verifying", 0, 6_442_450_944)
        assert window.write_stage_progress.value() == 0
        assert window.write_percent.text() == "0.0%"
        window._write_progress("verifying", 6_442_450_944, 6_442_450_944)
        assert window.write_phase_labels["writing"].property("stepStatus") == "done"
        window._write_phase("complete", 0, 0)
        assert window.write_stage_progress.value() == 1000
        assert "Verified" in window.write_percent.text()
        window._reset_write_stage()
        window._write_phase("awaiting_admin", 0, 0)
        window._write_cancelled("Cancelled before writing")
        assert window.write_stage_progress.maximum() == 1000
        assert window.write_stage_progress.value() == 0
        assert "Stopped" in window.write_percent.text()
        window._reset_write_stage()
        window._write_phase("awaiting_admin", 0, 0)
        with patch.object(QMessageBox, "warning"):
            window._write_failed("Helper exited before writing")
        assert window.write_stage_progress.maximum() == 1000
        assert window.write_stage_progress.value() == 0
        assert window.write_percent.text() == "0.0% · Failed"
        window._reset_write_stage()
        assert not window.write_stage.isVisible()
    for key in ["ubuntu", "win11", "macos", "other", "kali"]:
        window._choose_system(key)
        app.processEvents()
        assert window.system.key == key
        assert not window.create_button.isEnabled()
    # Clicking any named OS queues its own automatic download; opening the
    # app alone does not force a multi-GB download before the user chooses.
    window.auto_download = True
    with patch.object(window, "_download") as download:
        for key in [
            "kali",
            "ubuntu",
            "debian",
            "mint",
            "fedora",
            "arch",
            "parrot",
            "win10",
            "win11",
            "macos",
        ]:
            window.system_buttons[key].click()
            app.processEvents()
            assert window.system.key == key
            assert download.call_count
            download.reset_mock()
    # Exercise the real downloader QThread and UI completion using a checked
    # local cache. The fixed small fixture never connects to a vendor.
    from test_download_pipeline import HASH, PAYLOAD, RELEASE

    with tempfile.TemporaryDirectory() as folder:
        from autoboot_usb.services.state import image_directory

        with patch("autoboot_usb.services.state.application_directory", return_value=Path(folder)):
            window.auto_download = False
            window._choose_system("ubuntu")
            window.release_box.setItemData(0, RELEASE)
            saved = image_directory("ubuntu") / RELEASE.filename
            saved.write_bytes(PAYLOAD)
            window._download()
            deadline = time.monotonic() + 5
            while window.worker is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()
            assert window.worker is None
            assert window.image.verified and window.image.path == saved
            assert window.image.computed_sha256 == HASH
            assert saved.read_bytes() == PAYLOAD
    window._choose_system("kali")
    window._toggle_theme()
    window.resize(885, 635)
    app.processEvents()
    window.close()
    app.processEvents()
print(
    "Qt GUI smoke: PASS (large progress, busy release, import, confirmation denial, catalogue, themes)"
)
