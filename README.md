# AutoBoot USB 0.2.3 — Windows USB creation preview

A compact PySide6 desktop app for downloading, checking and preparing operating-system USB media.

**This is source code for a development preview, not a hardware-certified release.** The Windows writer is implemented and explicitly opt-in. Its shared copy engine is tested using simulated disks. Windows UAC, volume locking, physical USB writing and actual booting still need testing on a spare USB and real Windows hardware. macOS and Linux retain the shared GUI, downloading, verification and read-only discovery; their physical writers are disabled.

## Start on Windows

1. Extract this ZIP into a **new folder on your internal disk**. Do not launch the app from the USB you intend to erase.
2. Install [Python 3.10 or newer](https://www.python.org/downloads/windows/) with the Python Launcher if it is not already installed.
3. Double-click **START_WINDOWS.bat**. First launch installs the GUI dependency into a local virtual environment.

No `.env` file, API key or configuration editing is required.

## Use the app

1. Select **Kali Linux** or **Ubuntu**.
2. Click **Download official image**, or import an ISO you already have. Creation requires a match to one of this build's trusted release hashes.
3. Insert a **spare USB with nothing important on it**, select **Refresh drives**, then select the intended drive. An 8 GB or larger USB is recommended.
4. On Windows, tick **Enable Windows writer preview for a spare test USB**.
5. Click **Create bootable USB**. Carefully check the model, capacity, serial number and physical disk identifier in the erasure dialog.
6. Approve Windows UAC. The main GUI will say **Administrator approved**, then a **separate foreground erasure confirmation** shows the USB model, serial and disk ID. Check the details and select **Yes** to continue (default is No). If you cannot see it, press **Alt + Tab**.
7. Keep the USB connected until the complete write, flush and read-back SHA-256 check finish. A failed or cancelled operation never counts as verified.
8. **Boot instructions** explains how to open the firmware boot menu after successful read-back. This preview does not restart the computer or automatically select a firmware boot target.

Kali **Installer** installs Kali to a computer; it is different from Kali **Live**, which runs from a USB. The Kali Live 2026.2 direct HTTPS link currently returns 404. Its edition remains available for importing a checksum-verified ISO obtained through the official website's BitTorrent download. The app never silently substitutes Installer for Live.

## Curated images

| Image | Download | Verification |
| --- | --- | --- |
| Kali 2026.2 Installer, x86-64 | Direct HTTPS via Kali's official mirror service | Bundled SHA-256 plus upstream manifest comparison |
| Kali 2026.2 Live, x86-64 | Import from the official website / BitTorrent | Bundled SHA-256 |
| Ubuntu 26.04.1 Desktop LTS, x86-64 | Direct HTTPS from Ubuntu releases | Bundled SHA-256 plus upstream manifest comparison |

Other catalogue entries link to their official websites. Windows installers, macOS installers and arbitrary/custom ISOs cannot be raw-written in this preview. They need separate image handlers. These x86-64 images cannot boot an Apple Silicon Mac or ARM Windows PC, although media can be prepared for a different compatible computer.

## What changed since 0.1.1

- Replaced the dead Kali Live automatic download with an explicit Installer download and an import-only Live edition.
- Checked pinned digests against exact filenames in the upstream checksum manifests. A changed, missing or duplicate manifest entry blocks a download rather than replacing the trusted pin.
- Retained Python integer Qt byte counters, including USB write progress, so multi-gigabyte sizes cannot overflow signed 32-bit signals.
- Fixed completion losing the selected release while its selector was disabled during a download.
- Added cancellable cache and local ISO hashing, stable-file checks, length and HTML response rejection, and atomic completion that never overwrites a file appearing during a download.
- Implemented a separate UAC-elevated Windows helper with a hash-bound request, native target confirmation, fresh disk discovery and native-handle identity checks.
- Added aligned writes, removal of stale backup partition metadata, flushing, reopening without OS caching, and full read-back verification.
- Added fault injection and headless Qt tests. See [docs/VALIDATION.md](docs/VALIDATION.md) for what was actually tested.

## Windows USB creation progress (0.2.3)

The Create section now has a separate three-stage progress display: **Check image → Write USB → Verify USB**. Byte counts remain Python integers so an image larger than 2 GB cannot overflow the Qt signal. The GUI asks for erasure consent before a separate UAC-elevated helper confirms the same disk. The helper checks the official SHA-256, protects the source ISO, validates the native USB identity, attempts to lock/dismount target volumes, writes the image, flushes it, and reads **every ISO byte** back for a final digest match.

### Windows administrator approval not advancing (fixed in 0.2.2)

Previously the elevated helper was started with `nShow=0` (hidden) and opened an unowned confirmation MessageBox without foreground/topmost flags. After UAC approval, the second erasure confirmation could be obscured behind other windows while the main GUI still said it was waiting for administrator approval. The helper now uses an explicit foreground/topmost, default-No erasure dialog and publishes `admin_approved`, `confirming_usb` and `checking` state transitions. The GUI distinguishes these steps. Cancelling in the main GUI while the final dialog is open blocks writing even if Yes is subsequently clicked. A helper process that exits without a status gives an actionable error, not silent success.

Always use the newly extracted **0.2.3** folder to test the fix rather than continuing a stuck 0.2.1 process; leave the spare USB connected until you have confirmed no write is in progress. Unprivileged application operation and the second elevated confirmation remain mandatory.

The copy engine now respects **physical sector alignment** when it is larger than the logical sector size (for example, a USB exposing 512-byte logical sectors but 4096-byte physical sectors). It pads the final *I/O transfer*, not the downloaded ISO or the verification hash. Devices with unrecognised geometry fail without starting a write. This change has passed synthetic disk tests; it has **not** been exercised on a real Windows USB device here.

A USB is marked successful only after the elevated helper exits with a matching SHA-256, physical disk ID and byte counts. Cancellation or read-back errors mark the media unverified. This does not certify the USB as bootable on a particular computer.

## Safety and limitations

- Only external, non-system, writable USB disks are eligible. PhysicalDrive0 is always blocked.
- The writer requires a serial number and an exact match to the confirmed model, capacity, serial, disk ID, unique ID and sector size. Unidentifiable devices are refused.
- A native disk handle must confirm USB bus, serial, number and length before writes. Each mounted volume must belong to that target and be successfully locked/dismounted.
- A source ISO, helper, interpreter or status directory located on the selected target disk is refused.
- The source ISO is opened with modification and deletion denied until copying ends, then rehashed before any disk bytes are changed.
- Cancellation is cooperative. Wait for the operation to stop; never unplug during a write. A cancelled or failed write must be recreated from the beginning. Raw writes are not resumed.
- Replacing partitions makes old files inaccessible but is **not secure data erasure** of the whole device.
- Windows 512-byte and 4096-byte logical sectors are accepted; unfamiliar geometry, inaccessible volumes or serial mismatches fail closed.
- Same-account Windows UAC elevation is assumed. Elevating under a different account, Windows on ARM, unusual USB bridges and code-signed packaging require further validation.
- SHA-256 pins were compared to official HTTPS release metadata. Runtime GPG signature verification and production code signing are not implemented.
- Read-back proves image bytes match. It does not prove BIOS/UEFI, Secure Boot or hardware boot compatibility.

## Developer commands

Double-click **TEST_WINDOWS.bat** on Windows to install missing GUI dependencies into the local virtual environment and run the complete test suite without opening or erasing a USB drive.


**Windows PowerShell**, in the extracted AutoBoot-USB folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m autoboot_usb
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**Linux or macOS terminal**, in the extracted folder:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m autoboot_usb
.venv/bin/python -m unittest discover -s tests -v
```

Tests use only fake or file-backed disks and never elevate or erase a physical device.

Optional live checks, on any host with the package installed:

```bash
python -m tools.check_official_sources
python -m tools.check_official_sources --download --folder /path/to/test-images
python -m tools.check_image_pipeline /path/to/test-images
```

The first command checks official manifests and HTTPS headers. The second downloads complete ISOs and compares their hashes. The third hashes those ISOs through a real Qt worker, copies them to temporary file-backed disks, flushes, reopens and reads the entire image back. Allow space for the ISOs plus one additional image-size temporary disk. These are development checks; normal users use the GUI.

## Packaging

Build on each target host. Windows PowerShell: `./packaging/build_windows.ps1`. It builds the narrow helper first and bundles it with the GUI. macOS/Linux scripts build the shared read-only preview. The resulting packages are unsigned development builds. Production installers, Windows signing, Apple notarisation and AppImage/native package validation remain release tasks.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/VALIDATION.md](docs/VALIDATION.md) and [docs/TEST_MATRIX.md](docs/TEST_MATRIX.md).
