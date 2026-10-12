# Physical hardware acceptance tests — still pending

Automated/file-backed evidence is listed in VALIDATION.md. Those checks do not substitute for the native Windows tests below. Use a dedicated spare USB with no valuable files.

| Host / medium | Image | Required result |
| --- | --- | --- |
| Windows 11 x64, UEFI, USB 3 flash | Kali Installer and Ubuntu Desktop | Discover, confirm, UAC, lock/dismount, write, flush, complete read-back; boot successfully |
| Windows 10 x64, Legacy BIOS | Kali Installer | Same checks; verify legacy boot separately |
| Windows 11 x64, 4096-byte logical sector USB bridge | Curated compatible ISO | Aligned write, reopened read-back; otherwise clear refusal |
| Windows on ARM | Curated x86-64 image | Creation for a different x86 PC is distinct from boot compatibility; no claim of native boot |
| Linux/macOS host | Any image | GUI/download/import/discovery work; physical writer remains disabled |
| Intel Mac / Apple Silicon boot target | x86-64 Linux image | Intel compatibility varies; Apple Silicon x86 boot explicitly unsupported |

## Before the first Windows test

Use an expendable USB device. Before enabling the writer, open **Windows PowerShell** and inspect the connected USB disks with:

```powershell
Get-Disk | Where-Object BusType -eq 'USB' |
  Select-Object Number, FriendlyName, Size, SerialNumber, IsBoot, IsSystem, IsReadOnly, LogicalSectorSize
```

Confirm the disk **number**, size and serial agree with the GUI and the erasure confirmation. This command is read-only. Do not test with a drive containing valuable data. On a physical 4 KB sector device, the post-write full SHA-256 read-back must pass. If the writer refuses a device, retain the diagnostic message; do not bypass its safety checks.

## First Windows test

1. Extract the project and keep the ISO on the internal drive.
2. Use a spare 8 GB or larger USB. Confirm its model, capacity and serial in the GUI.
3. Select Kali Installer, download/import it and wait for its trusted SHA-256 match.
4. Enable the preview, select Create and check BOTH erasure confirmations. Cancel the first trial; verify no write starts.
5. Repeat and reject UAC; verify the app reports cancellation and leaves files intact.
6. Repeat with approval. Verify progress reaches source checking, writing, flushing and full read-back before success.
7. Use the manual boot menu to test the resulting media. Choosing a Kali installer does not authorise installing over the computer's internal disk.
8. Repeat with Ubuntu. Record Windows version, hardware, firmware/Secure Boot state, USB model, serial/sector geometry, release digest and outcome.

## Fault and safety tests

- Connect multiple USBs; swap the selected device before approval. Changed identity must abort.
- Try a serial-less USB, PhysicalDrive0, read-only USB and internal disk. Creation must be refused.
- Place the ISO or application on the target USB. Creation must refuse before dismount/write.
- Boot Windows from an external USB; that system/boot disk must be excluded.
- Keep files open on the USB or use it for pagefile/system functions. Failed volume locking must abort; no forceful bypass.
- Disconnect during source checking, write, flush or read-back. No success result; recreate before booting.
- Stop during source checking and again during writing/read-back. Wait for the helper; no false completion.
- Repeated read-back mismatch: try a different USB or port; never offer reboot on failed verification.
- Close during an active worker. Cancellation must complete before Qt destroys any running thread.
- Test source launch and packaged helper separately, including UAC visibility and paths containing spaces.
- Test 100%, 125%, 150% and 200% Windows scaling, keyboard flow, dark/light modes and minimum window size.

Only after these tests pass should a signed installer and supported-hardware claims be released. Do not automatically modify persistent firmware boot order.
