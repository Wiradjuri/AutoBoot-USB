# AutoBoot USB 0.3.0

Download operating-system installers, check their integrity and create installation USBs on Windows.

## Start on Windows

1. Extract the ZIP into a new folder on your internal disk.
2. Install Python 3.10 or newer if it is not already installed.
3. Double-click **START_WINDOWS.bat**. It sets up the local Python environment and opens the app.

Start normally. AutoBoot requests Windows administrator access automatically when USB creation needs it. USB detection uses a filtered, read-only Windows storage query with a 90-second limit; a first scan can take longer while Windows starts its storage provider. If Windows denies disk-information access, AutoBoot requests UAC for a read-only scan too. Approve the Windows UAC prompt and the final confirmation showing the USB model, capacity, serial and disk number.

## Download an operating system

Click an OS in the left column. Its default installer downloads automatically into:

```text
AutoBoot-USB/Images/<OS>/
```

Changing an edition also starts its download. **Open Images folder** opens the saved files. Selecting an image again checks and reuses the cached file. A damaged cache is preserved with an `.invalid-...` suffix and downloaded again. Images are retained after USB creation.

| OS | Automatic download | USB preparation on Windows |
| --- | --- | --- |
| Kali Linux | 2026.2 Installer | Hybrid ISO write and full read-back |
| Ubuntu | 26.04.1 LTS desktop | Hybrid ISO write and full read-back |
| Debian | 13.7.0 network installer | Hybrid ISO write and full read-back |
| Linux Mint | 22.3 Cinnamon, MATE or Xfce | Hybrid ISO write and full read-back |
| Fedora | 44 Workstation Live | Hybrid ISO write and full read-back |
| Arch Linux | 2026.10.01 installer | Hybrid ISO write and full read-back |
| Parrot OS | 7.4 Security or Home | Hybrid ISO write and full read-back |
| Windows 11 | 26H2, English (United States), x64 | FAT32 installer files, split WIM, file read-back |
| Windows 10 | 22H2, English International, x64 | FAT32 installer files, split WIM, file read-back |
| macOS | Full installer from Apple's current catalog | Save the package; create the USB on a compatible Mac |

Linux downloads must match both the bundled SHA-256 and the exact filename in the upstream checksum manifest. Windows uses Microsoft's public edition/language selection page to obtain its expiring ISO link, then checks the bundled official SHA-256. Windows 11 downloads the available English (United States) edition; its English International edition remains available by importing a matching ISO. Microsoft may refuse to issue a link on some networks; the app displays that failure and provides a retry. An existing matching ISO can also be imported.

Apple supplies SHA-256 integrity data for the full installer. AutoBoot checks every chunk against that data and records a SHA-256 of the saved package. On a compatible Mac, install the downloaded package and follow [Apple's bootable installer instructions](https://support.apple.com/en-au/101578). The Windows writer does not raw-write Apple installer packages.

Kali Live 2026.2 remains available by importing its matching official ISO because the direct HTTPS image is unavailable. **Other ISO** opens a local image picker; an arbitrary ISO has no known automatic download source or trusted writing handler.

## Create the USB

Select the intended USB and click **Create bootable USB**. Check its identity in both erasure confirmations. Keep it connected through **Check image → Write USB → Verify USB**. Successful completion is reported only after verification and a successful writer exit.

The **Enable Windows writer preview** checkbox has been removed. There is no separate writer opt-in.

The app refuses system disks, disk zero, read-only disks, devices without a serial number and changed target identities. It refuses to erase a USB containing the source image or application. Stop requests cancel the operation cooperatively; wait until it has stopped before unplugging.

Linux images are copied with sector alignment, flushed, reopened and read back in full. Windows ISOs use a separate installer workflow: mount the verified ISO read-only, prepare oversized `install.wim` files with Windows DISM, create a FAT32 boot partition, copy the installer files, apply boot code and verify every copied file. This can need additional temporary disk space for split WIM files.

## Fixes in 0.3.0

The GUI creates private status journals before elevation. The helper appends complete messages to those same files, preserving their owner, integrity label and permissions. Progress updates no longer replace elevated-owned status files. A fresh acknowledgement must succeed before any disk operation; persistent communication errors cancel the helper and keep the GUI busy until it exits.

Unsupported USB alignment and volume-extent queries have compatible fallbacks using native disk identity and logical geometry. Access-denied errors, changed drives and failed volume locks are still reported as failures. Error messages identify the Windows storage query that failed.

## Validation and remaining limits

**TEST_WINDOWS.bat** runs the software tests without erasing a USB. Windows CI also checks the real status exchange between high and medium integrity levels, captures/splits a small WIM with native DISM, and runs the raw writer and helper against temporary virtual disks. The test suite covers GUI selection, cache reuse, downloads, cancellation, corruption and read-back.

Live image and file-backed disk results are recorded in [docs/VALIDATION.md](docs/VALIDATION.md), [docs/LIVE_SOURCE_CHECK.json](docs/LIVE_SOURCE_CHECK.json) and [docs/FILE_BACKED_CHECK.json](docs/FILE_BACKED_CHECK.json).

Physical writing and firmware booting on the user's USB hardware have not been tested from this development environment. Read-back confirms bytes; boot compatibility depends on the target computer. Linux and macOS hosts currently support downloading, verification and discovery; physical writing is implemented on Windows. Packages are unsigned.

## Development

```bash
python -m pip install -e . ruff
ruff check .
python -m unittest discover -s tests -v
python -m autoboot_usb
```

For live source and full file-backed checks:

```bash
python -m tools.check_official_sources
python -m tools.check_official_sources --download --pipeline --discard --folder /path/to/test-images --only debian
```

Build Windows packages with `packaging/build_windows.ps1`. The writer helper is built separately and bundled with the GUI.
