# AutoBoot USB 0.3.0 validation — 12 October 2026

Current local software result: 143 tests, 133 passed and 10 native-Windows checks skipped on the Linux development host. Ruff passed. Tests include a real Qt WebEngine form fixture, automatic sidebar downloads, local cache reuse, corruption/cancellation, and the real child-process status journal. Physical USB devices are never opened by this test suite.

The initial six Windows/Linux jobs passed on Python 3.12, 3.13 and 3.14. The expanded native virtual-disk and discovery coverage is running in the final CI revision. The additional checks exercise high-to-medium integrity status exchange, native DISM capture/splitting, filtered CIM enumeration, and production raw/helper and FAT32 installer operations on test-owned VHDs. These checks do not use a USB device.

Live source manifests and HTTPS headers passed for every direct Linux image and Apple's resolved full installer. Debian, Mint Cinnamon, Fedora, Arch and Parrot Security passed complete downloads and full file-backed disk read-back. Apple macOS 27.0.1 passed the complete 18,386,308,006-byte download with every SHA-256 integrity chunk checked; its computed SHA-256 is `2b59907ae1097af9b8be6399457395397543aef3514847dea0e45032a849a30d`. Results are recorded in LIVE_SOURCE_CHECK.json and FILE_BACKED_CHECK.json. The unchanged Kali and Ubuntu pins retain their previous complete-image results. Microsoft browser-form automation passed against the local fixture; the live Microsoft page returned net::ERR_EMPTY_RESPONSE from this development network, so a real Microsoft download has not yet been verified here.

The user's Windows remote connection is offline. The Lexar physical write, volume locking on that USB and firmware booting remain untested. Same-account real UAC interaction also remains a physical-host acceptance check. No successful physical write or boot claim is made.

The historical reports below describe earlier builds and do not override the current results above.

---

# AutoBoot USB validation reports

## v0.2.5 administrator access and writer status — 12 October 2026

The local Linux/Python 3.12 suite ran **97 tests: 95 passed, 2 native Windows ACL
checks skipped, no failures**. Ruff and Git whitespace checks passed. New checks
exercise denied status access, automatic `runas`, reusing an existing administrator
token, cancelled UAC, fresh-nonce acknowledgements, timeout before any disk work,
cooperative cancellation, and repeated atomic status replacement. The real Qt worker
and test child exchange the production nonce before creating their file-backed disk;
copy, flush, reopened read-back, corruption and cancellation regressions pass.

The private ACL implementation binds Windows token/security APIs directly and derives
the caller grant from `TokenUser`. Native Windows CI checks session inheritance and
original-user grants on ready/status/ack files. CI now covers Python 3.12, 3.13 and
3.14 on Windows and Linux. Interactive desktop UAC and physical USB boot acceptance
are not exercised by this suite. The underlying exception in the user's unreadable
v0.2.2 diagnostic remains unknown; this patch fixes the known reporting fault and
communication access handling rather than claiming to have recovered that exception.

## v0.2.4 writing progress and helper startup — 12 October 2026

**85 automated tests passed, with no failures or skips,** on the Linux development host with Python 3.12 and PySide6 6.12.0. Ruff and Git whitespace checks passed. The progress panel was rendered and inspected in both light and dark themes.

New checks launch the actual source helper in a separate interpreter with `-I -S` from an unrelated working directory, and execute the exact interpreter/helper command chosen by the GUI (`pythonw.exe` on Windows). Its startup check succeeds without Qt in the helper, without an ISO file and without opening any device. An absent application package reproduces an import failure and now produces a bounded `status.json` traceback rather than an unexplained windowless exit. Invalid request schemas, tampering and symlinked sessions are refused.

The real Qt writer worker also runs through a separate **test-only process** using the production copy engine and an ordinary temporary disk file. Tests exercise measured write progress, flush, close/reopen, full read-back, matching process completion, injected corruption and cancellation during writing. These checks use real file I/O, real status polling and queued Qt signals, while replacing UAC/Win32 process APIs. A helper's intermediate `complete` state cannot mark the GUI verified before its process exit and final payload are checked.

The GUI smoke test checks multi-gigabyte counters, percentage display, verification reset, failure/cancellation stopping the bar, failure before any write and starting another attempt at zero. The existing Windows `TemporaryFile`/`NamedTemporaryFile` regression is retained.

The repository now runs this suite on Windows and Linux for Python 3.12 and 3.13 through GitHub Actions. Those jobs exercise native Win32 function binding during the safe helper startup check, but **do not request UAC, open a physical disk or prove a USB boots**. Interactive UAC, actual volume locks and physical USB/boot acceptance remain pending. The prior full Kali/Ubuntu ISO download checks were not repeated for this UI/startup change.

The source-mode launch change bypasses Windows virtual-environment redirectors and site hooks. The screenshots supplied only establish an elevated process exit with code 1 and no status; they do not contain the original traceback. The new diagnostics make the next failure, if any, specific. Do not claim this change proves the exact root cause on the user's Windows machine.

Platform references used for the launch change:

- [Python 3.12 Windows venv redirectors](https://docs.python.org/3.12/library/venv.html)
- [ShellExecuteEx worker-thread activation flags](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow)

## v0.2.0 prior validation

Checked: 2026-10-11T16:42:17.754934+00:00 (UTC), 12 October 2026 in Australia/Sydney.

**59 automated tests passed.** Lint, formatting and Python compilation passed.
Test host: Linux, Python 3.12.14, PySide6 6.12.0.

The complete Kali and Ubuntu ISOs were downloaded over HTTPS, checked against the official checksum manifests and pinned SHA-256 values, hashed through a running Qt verifier with byte counts exceeding 2 GiB, then written to file-backed simulated disks. Each simulated disk was flushed, closed/reopened, and read back over the complete image length. Both resulting hashes matched. No physical disk was opened, erased or formatted.

| Check | Kali Installer 2026.2 | Ubuntu Desktop 26.04.1 LTS |
| --- | --- | --- |
| HTTPS source response | 200 | 200 |
| Complete image length | 4,802,531,328 bytes | 6,482,409,472 bytes |
| Official manifest matches pinned hash | Pass | Pass |
| Complete download SHA-256 | Pass | Pass |
| Real Qt worker hash / progress beyond 2 GiB | Pass | Pass |
| Hybrid boot header check | Pass | Pass |
| Full simulated disk write, flush and reopened read-back | Pass | Pass |
| Physical USB write and real boot | Not tested | Not tested |

## Image digests

Kali Installer: `6dbefacc95e3b556c19c48e8bae39b8b505e2d3a1aba0bfb7ab62b036c3d2ba3`

Ubuntu Desktop: `601e30fbf5d97759367c632e2c33630665039b7e2158fd068403da3ccf1bda1f`

Kali Live import pin: `49e90e694d1b3dedd47f94afbe99dfdd5afb41c8462b638bbd332929769c773a`. The pin matches official metadata; the Live ISO itself was not downloaded or written in this test. Its direct HTTPS URL returned 404, so automatic download is disabled for that edition. The official directory lists its BitTorrent download.

## Regression and failure coverage

- Progress signals preserve 5 GiB / 6 GiB Python integers, including the worker/helper boundary.
- GUI completion retains the release when its version selector is disabled. Known renamed imports are recognised by digest. Unverified imports and serial-less targets cannot enable creation; rejecting erasure does not launch a worker.
- Bad/missing/duplicate manifest entries, insecure URLs/redirects, HTML responses, wrong length, truncated downloads, bad hashes and insufficient storage do not publish a final ISO.
- Cached downloads are rehashed; cancellation and file mutation are detected. Files appearing during a download are never overwritten.
- Wrong checksum, insufficient/alignment-invalid capacity, wrong native serial/bus/disk number/length, changed snapshots and volumes on another disk are rejected.
- Short writes are completed, zero/failed writes and flush/short-read/corrupt-read-back failures never report success. Cancellation before writing preserves the test disk; cancellation during verification prevents completion.
- Mocked Windows volume locking/dismount order and handle cleanup pass; failed locks are never bypassed.
- Helper requests reject tampering. Mocked process success requires exit code zero and matching target, digest and byte count; rejection/cancellation/failure cannot appear as success.

## Practical limitations

The native Win32 helper, UAC prompts, disk locks, actual USB controller caching and firmware boot compatibility were **not tested on Windows hardware**. The connected PCs were offline, and this environment is Linux. Windows writing therefore remains an explicit development preview. Linux/macOS physical writers, Windows/macOS image handlers, automatic firmware target selection, restart and relaunch/provisioning are not implemented. Signing, native installers and hardware architecture/scaling acceptance remain outstanding.

An initial Ubuntu test ran out of scratch disk space due to temporary test copies. It failed without publishing the ISO. After removing only those temporary validation copies, the complete download succeeded and the full read-back check passed. That failed attempt was not counted as a verified download.

The included JSON files retain detailed live-source and file-backed results. They record test paths, not bundled ISO files. Do not treat a simulated disk result as evidence of physical USB booting.

## Official references

- [Kali release directory](https://cdimage.kali.org/kali-2026.2/)
- [Kali release checksums](https://cdimage.kali.org/kali-2026.2/SHA256SUMS)
- [Kali image verification](https://www.kali.org/docs/introduction/download-official-kali-linux-images/)
- [Ubuntu release checksums](https://releases.ubuntu.com/26.04/SHA256SUMS)
- [Win32 CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
- [Volume locking](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_lock_volume)
- [Volume physical disk extents](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_volume_get_volume_disk_extents)

Physical acceptance instructions are in TEST_MATRIX.md. Only promote the writer from preview after a spare USB has been created, read-back verified and booted on the intended hardware.


## v0.2.1 writing-stage regression check — 12 October 2026

The previous v0.2.0 official ISO downloads/read-backs described above **were not repeated** for this change. This development host is Linux running Python 3.13 and cannot install PySide6 because package-network access is unavailable. It is also not a Windows host and cannot perform a physical Win32 USB write.

Locally exercised test groups: model validation, writer pipeline and cancellation/fault injection, Win32 API boundary fakes, OS catalogue, disk discovery, and large-progress signal declarations. **41 tests executed: 40 passed, 1 skipped (Qt-only), 0 failures and 0 errors.** PySide6 was not installed, so the Qt runtime signal test skipped. Qt GUI smoke, Qt writer-worker and Qt-dependent download pipeline tests must be rerun in the Windows development environment; they were successfully exercised in v0.2.0 before the UI/core changes.

New specific checks include: logical-512/physical-4096 unbuffered transfer padding, aligned USB read-back, rejecting insufficient/misaligned physical capacity *before volume locking*, and unsupported alignment offsets, Windows disk-zero model filtering, explicit write consent/host gating, and worker completion requiring a matching `total` field (included in tests pending PySide6). The Qt write-stage UI has been checked for Python syntax; visual/runtime behaviour still requires native Qt testing.

This is **not** evidence that Windows UAC, USB disk locking, actual flash media writes or firmware booting have passed. A spare USB acceptance test remains mandatory before wider use.

## v0.2.2 Windows approval/erasure confirmation fix — 12 October 2026

User symptom: the GUI continued to report "Awaiting administrator approval" after Windows UAC had been approved. Source inspection found that the UAC helper was launched with `nShow=0` and subsequently opened a second, unowned erasure dialog without foreground/topmost flags. That can leave the second confirmation hidden behind the GUI. The patch uses `SW_SHOW` for the helper and `MB_SETFOREGROUND | MB_TOPMOST` with a default-No button for the destructive confirmation. A return from `ShellExecuteExW` now notifies the GUI that administrator approval has finished. The helper reports `admin_approved`, `confirming_usb`, then `checking`, so its waiting state is observable.

The final destructive confirmation was **not removed**. The main GUI's cancel flag is rechecked immediately after the dialog returns. This prevents a delayed Yes from beginning a write when cancellation occurred while that dialog was open. An elevated helper that exits without status now generates a more diagnostic error, including its Windows exit code.

Validation limitations: The current development environment is Linux, with no PySide6 wheel available through the disabled package network. The elevation tests use a mocked `MessageBoxW`, file-backed status exchange, and a mocked `create_bootable_usb`: no administrator token, raw disk, physical USB or Windows display was touched. GUI smoke and native UAC focus behaviour **must** be retested on actual Windows hardware. No claim of verified Windows media writing or bootability is made.

Steps to accept v0.2.2 on Windows: Extract into a clean internal-disk directory; run `TEST_WINDOWS.bat`; start the GUI; download and verify a curated Linux ISO; select a disposable identifiable USB; opt in to the experimental writer; select Create; confirm initial erase prompt; approve Windows UAC; observe a foreground final confirmation with exact device model/serial; choose No and confirm no write; repeat and choose Yes only for a disposable test USB; check progress and post-write full read-back; test boot compatibility independently. Test cancellation while the final dialog is open and confirm no write begins after a late Yes.
