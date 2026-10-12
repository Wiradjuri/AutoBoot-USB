# Architecture at 0.3.0

The Python/PySide6 GUI shares a release catalogue, download manager, integrity checks and copy/read-back engine. Host adapters discover USB devices. Physical writing is implemented on Windows.

## Downloads and trust

Sidebar choices and edition changes queue an installer download into the application's `Images/<OS>/` folder. Cache files are checked before reuse; a damaged file is preserved with an `.invalid-...` suffix before downloading again. Cancellation removes only the operation's partial file. Final publication never overwrites another file.

Linux releases carry version-specific filenames, HTTPS sources, SHA-256 pins and official checksum manifests. The exact manifest entry must agree with the bundled pin. Responses must have valid lengths and image content rather than text or HTML. Imported ISOs are recognised by digest, not filename.

Windows uses a private Qt WebEngine profile to select the public Microsoft ISO edition and the release's pinned language: English (United States) for Windows 11 and English International for Windows 10. The resulting expiring HTTPS ISO URL must belong to an approved Microsoft download host. The application downloader verifies the bundled official SHA-256. This flow does not sign in, submit account data or bypass vendor challenges. A blocked vendor page offers retry or verified import.

Apple's current software-update catalogue resolves a full `InstallAssistant.pkg` and its integrity data. Every streamed SHA-256 chunk must match Apple's chunklist; the saved package also receives a computed SHA-256. Creating macOS USB media requires Apple's installer tools on a compatible Mac.

## Windows privilege boundary

The GUI starts normally. USB discovery queries only `MSFT_Disk` records with USB bus type through the Windows storage CIM provider, allowing up to 90 seconds for the process. The query is read-only and cancellable. An access denial invokes a separate read-only UAC helper; it accepts only the exact discovery request and cannot write an image.

For USB creation, `UsbWriteWorker` serialises a bounded snapshot containing the image path, size, trusted digest and target identity. A separate SHA-256 in the helper arguments binds this request. The helper validates every field and its administrator token before disk work. A normal caller uses `runas`; an already-elevated caller uses `open`. Source helpers use the base Python interpreter with `-I -S` and load only the application source root.

`writer_session.py` derives the GUI's actual `TokenUser` SID and creates a private inheritable DACL for that user, SYSTEM and Administrators. The GUI creates the status, ready and acknowledgement journals before elevation. Helpers append newline-committed messages to those same files, preserving ownership, integrity labels and permissions. Readers ignore unfinished final records and retain the previous committed message. Brief access-denied reads are retried, including plain CRT `EACCES`; persistent communication failure cancels the helper and leaves the worker busy until it exits.

A fresh nonce acknowledgement must succeed before any disk operation. The nonce checks communication; it does not replace target validation or user consent. The elevated helper displays a foreground, default-No erasure confirmation with the exact device identity.

## Writing and verification

The helper rejects system/boot disks, disk zero, missing serials, changed identities and a target containing the image, application or session. The source is locked against mutation and hashed again. Native handles must match the confirmed bus, serial, capacity and disk number. Target volumes must map to the intended disk and be locked before dismounting. Busy locks are retried for a bounded interval; failed locks are never bypassed.

Hybrid Linux ISOs use sector-aligned raw copying, tail cleanup, flushing and a reopened complete read-back. When a USB driver does not implement alignment reporting, validated native logical geometry permits buffered write-through I/O. An unsupported volume-extent query may use the native single-disk device-number mapping; ambiguous mappings remain errors.

Windows ISOs use a separate installer path: mount the verified ISO read-only, inspect installer and boot files, split an oversized `install.wim` with DISM, recheck the target before destructive operations, create a FAT32 boot partition, copy files, apply boot code and read back every copied file against its hash. Cleanup unmounts only images mounted by this operation. Oversized ESD files are refused before formatting.

Completion requires a successful helper exit and a matching final target, digest and byte count. Cancellation is cooperative throughout scanning, checking, writing and read-back. A partially written USB is never reported as verified.

## Interface and acceptance

The interface has separate download and Check image / Write USB / Verify USB progress. Byte counters remain Python integers across Qt signals, including images larger than 2 GiB. Controls are locked during operations and closing waits for workers to stop. There is no writer preview checkbox or separate opt-in.

Windows CI exercises native discovery, high-to-medium integrity status communication, DISM, production raw/helper writes and FAT32 file copying on test-owned VHDs. Physical USB controllers, interactive UAC and firmware booting still require the acceptance checks in `TEST_MATRIX.md`. The app does not change persistent firmware boot order or install an operating system onto the host.
