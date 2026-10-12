# Changelog

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
