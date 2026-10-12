# Architecture at 0.2.2

One Python/PySide6 GUI shares a catalogue, download manager, checksum engine and copy/verify engine. Host adapters handle read-only device discovery. Only the Windows adapter has a physical writer in this preview.

## Download and trust

`catalogue.py` contains version-specific filenames, expected lengths where known, SHA-256 pins, official ISO URLs and official checksum URLs. A release without a verified direct URL has import-only controls.

The downloader validates the initial official origin, follows HTTPS-only mirror redirects, reads a bounded checksum manifest, selects an exact filename and requires it to agree with the bundled pin. It never trusts a replacement hash supplied by the live manifest. Image bytes are hashed while streaming to a unique partial file. HTTP errors, invalid lengths, text/HTML, incomplete responses, checksum mismatch and cancellation discard that partial. Final publication must not overwrite an existing destination.

Cached images are hashed again before use. Imported ISOs are matched by digest, not by their filenames; calculating an untrusted digest does not authenticate an image. Runtime GPG signature verification is not claimed.

## Windows privilege boundary

The GUI runs without elevation. `UsbWriteWorker` writes a bounded snapshot and launches the independent `run_writer.py`/`AutoBootUSBWriter.exe` using Windows `runas`. The snapshot contains the absolute image path, image size, curated digest and full target identity. Its SHA-256 is passed separately in the process arguments. The helper verifies that binding and validates all fields before accepting a request.

The helper can only write a curated hybrid Kali/Ubuntu image. It does not accept shell commands, format recipes, arbitrary firmware operations, Windows installers or macOS installers. Source runs use isolated Python (`-I`) and restore only the application source root to import paths. The helper itself displays the final target/erasure confirmation.

Before writing, it checks that the image/application/status files are on another physical disk, locks the source file against writes/deletion, checks image structure and hash, and re-enumerates the target. It then opens a Win32 physical-disk handle and checks native serial, USB bus, capacity, disk number and storage alignment. Get-Disk properties must still equal the user-approved snapshot. Mounted target volumes are checked by physical disk extents, locked and dismounted. Any failed check aborts.

The serial requirement is deliberate: devices without a reliable serial, disk zero, unfamiliar logical sectors and ambiguous volume/source mappings are refused. Device identifiers alone are not trusted.

The privileged helper is launched with `SW_SHOW`. In source mode, it uses `pythonw.exe` to avoid a console where present; packaged versions are windowed. The helper's independent final erasure `MessageBoxW` uses `MB_SETFOREGROUND | MB_TOPMOST`, and **No** remains the default. It reports `admin_approved` and `confirming_usb` states so the unprivileged UI can distinguish UAC from final erase consent. Cancellation is checked again immediately after this prompt and before the write engine is called. The app never auto-accepts confirmation.

## Copy and read-back

`copy_and_verify` does not open disks. It works on file-like objects, so the same engine is exercised by tests against temporary files.

It prehashes the source, validates capacity/alignment, clears stale backup partition-table metadata beyond the new image, copies physical-sector-aligned chunks (including the final partial block), checks the copied source digest, flushes and reopens the physical handle for uncached reads. Every image byte is read back and hashed against the curated digest. Padding is excluded from the image hash. Success is emitted only after the helper exits successfully and its final digest, target and byte count match the GUI request.

Qt byte-count signals use `object`, not 32-bit C++ `int`. Widgets receive bounded progress values from 0 to 1000. The creation section displays Check image / Write USB / Verify USB independently of download progress. Cancellation is cooperative across checking, writing and read-back; a partial USB is never reported as complete. Bounded request/status files and rotating logs allow diagnosis. Recovery means recreating the USB from byte zero, not resuming an interrupted raw write.

## Capability reporting

| Capability | Windows | Linux | macOS |
| --- | --- | --- | --- |
| GUI, curated HTTPS downloads, local hashing | Implemented | Implemented | Implemented |
| Read-only USB discovery | Get-Disk | lsblk | diskutil |
| Physical Linux hybrid ISO writer | Opt-in preview, hardware unvalidated | Disabled | Disabled |
| Windows/macOS installer writing | Disabled | Disabled | Disabled |
| Firmware guidance | Implemented | Implemented | Intel / Apple Silicon guidance |
| Automatic USB boot / reboot / OS provisioning | Disabled | Disabled | Disabled |

Do not promote the Windows writer from preview until native volume locking, disconnection/cancellation recovery and real booting have been tested on physical hardware. Unimplemented functionality must remain disabled or explicitly explained.

## Desktop interface

The existing compact Qt Widgets layout, custom iconography, restrained amber actions, light/dark themes and system fonts are retained. The preview acknowledgement is adjacent to the creation controls. Destructive confirmation shows device model, capacity, identifier and serial. Image and drive controls are locked during work; the selected release data remains readable even when its combo box is disabled. No command-line configuration is required in normal operation.
