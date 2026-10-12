# Changelog

## 0.2.5 — Automatic admin access and private writer communication

- Request UAC automatically when the caller lacks administrator access; use existing elevation when already available. No administrator terminal is required.
- Grant private writer sessions to the actual GUI token user SID, SYSTEM and Administrators, with file/directory inheritance. Copy those grants onto every helper status replacement before publication.
- Require a fresh status acknowledgement from the GUI before opening any disk or showing the final erasure prompt. Stop before disk work if communication is unreadable, cancelled or unacknowledged.
- Report status permission failures explicitly instead of hiding them as missing status. Signal cancellation and wait for the child to exit before re-enabling creation after communication faults.
- Retain checksum, target identity, final erasure consent and full read-back requirements. Do not modify old diagnostic files or the user's broader Temp permissions.
- Display the package's actual version in both GUI labels and the download user agent.
- Add native Windows ACL regressions, communication timeout/cancellation tests, denied-access checks and already-elevated/UAC-rejected launch tests. Extend Windows/Linux CI to Python 3.14.

## 0.2.4 — Measured USB progress and helper startup diagnostics

- Show a larger, determinate USB progress bar with a percentage and current/total bytes. Bring the progress panel into view when creation starts, reset it for each attempt/stage, and stop it on failure or cancellation.
- In source mode, launch the actual base Python interpreter directly on Windows with `-I -S`, bypassing virtual-environment redirectors and site startup hooks. Keep the GUI unprivileged.
- Run a bounded helper startup check before requesting UAC. The check validates imports, request binding and Win32 API availability without opening the ISO or any disk.
- Capture helper import/request failures in bounded diagnostic status files, including a traceback, and show the diagnostic folder in failure messages.
- Use synchronous Shell activation for the worker thread. Retain the foreground, default-No final erasure confirmation and report USB volume preparation separately.
- Mark a USB verified only after successful process exit and matching hash, device ID and byte counts; intermediate `complete` status cannot mark the GUI successful.
- Add isolated-process startup tests, real subprocess/file write–flush–reopen–read-back tests with queued Qt progress, corruption/cancellation checks, and Windows/Linux CI for Python 3.12/3.13.
- Keep the existing Windows temporary-file test fix. Interactive UAC and physical USB/boot acceptance still require a spare device on Windows.

## 0.2.3 — Windows Python 3.12 test fixture compatibility

- Fixed `test_file_backed_disk_is_flushed_and_verified` using explicit `FileDisk` method delegation, preventing a Windows temporary-file method-binding error (`FileDisk` used as a numeric `seek()` offset).
- Run the file-backed writer regression against both `TemporaryFile` and `NamedTemporaryFile` on every platform, so the Windows-only failure is also caught in Linux CI.
- No changes to physical disk-writing logic or administrator-elevation handling. Actual Windows UAC/USB failure requires separate diagnosis.

## 0.2.2 — UAC/erasure confirmation visibility and status recovery

- Show the elevated final erasure confirmation in the foreground/topmost with **No** as the default, rather than starting a hidden helper that can wait behind the main GUI.
- Report three distinct stages in the GUI: Windows UAC approved, confirm the final USB-erasure dialog, and checking source ISO.
- Use the windowless Python interpreter for source-mode helper launching on Windows when available; package the independent helper as a windowed process.
- Check cancellation again after the elevated dialog to prevent a late **Yes** from beginning a write after a user clicked Stop.
- Report when the helper exits without any status, and include mock regression tests for topmost confirmation, stage transitions and cancellation.
- Native Windows physical writing and interactive UAC behaviour still require acceptance testing.

## 0.2.1 — write-stage progress, alignment and model tests

- Replaced the obsolete model-test assumption that USB writing is unavailable with explicit consent and Windows-only guard checks.
- Excluded Windows PhysicalDrive0 from the selectable USB list, matching the raw writer safety rule.
- Added physical-sector-aligned transfers for 512e/4Kn USB devices, including the final partial ISO block, tail cleanup and read-back.
- Refuse misaligned physical disk capacities and non-zero/native unsupported sector offsets before volume locking/dismounting or raw writes.
- Added a dedicated Check / Write / Verify stage beside the creation action; download progress remains separate.
- Require a matching final helper `total` as well as hash, disk ID and byte count before reporting success.
- Added disk alignment, size, Qt signal declaration and native-boundary regression tests. Hardware acceptance remains pending.

## 0.2.0 — verified image pipeline and Windows writer preview

- Corrected Kali download availability: Installer via HTTPS, Live by verified import.
- Added bounded exact-filename upstream manifest checks, expected image lengths, stable/cancellable hashing and no-clobber final image publication.
- Fixed the selected-release lookup while the version combo box is disabled during downloads.
- Added a separate hash-bound UAC helper, explicit erasure confirmation, source protection, target rechecks and native physical-handle identity validation.
- Added locked/dismounted volume handling, aligned writes, stale backup GPT cleanup, flushing and full reopened/uncached read-back verification.
- Added opt-in Windows preview controls, safe cancellation, diagnostic status files and guided boot instructions.
- Added pipeline, GUI and fault-injection tests. Physical Windows/macOS/Linux hardware acceptance is still pending; see docs/VALIDATION.md.

## 0.1.1 — large ISO progress overflow fix

Changed download and verifier byte-counter signals from 32-bit Qt int to Python objects. Disk writing remained disabled.

## 0.1.0 — foundation

Cross-platform Qt GUI, catalogue, download/import verification, read-only USB discovery, themes and boot guidance. Disk writing and restart controls were disabled.
