"""Give a CI virtual disk the USB identity the writer requires (test-only).

A VHD reports BusTypeFileBackedVirtual and no serial number. Rewrite only those
two fields of the real STORAGE_DEVICE_DESCRIPTOR returned by Windows; every
other IOCTL and all disk reads, writes, locks and flushes stay real.
"""

import struct

CI_SERIAL = "AUTOBOOT-CI-VHD"
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400


def usb_identity_ioctl(real):
    def ioctl(self, handle, code, input_bytes=b"", output_size=65536):
        result = real(self, handle, code, input_bytes, output_size)
        if (
            code == IOCTL_STORAGE_QUERY_PROPERTY
            and input_bytes[:4] == struct.pack("<I", 0)  # StorageDeviceProperty
            and len(result) >= 36
        ):
            data = bytearray(result)
            offset = len(data)
            data += CI_SERIAL.encode("ascii") + b"\0"
            struct.pack_into("<I", data, 4, len(data))  # Size
            struct.pack_into("<I", data, 24, offset)  # SerialNumberOffset
            struct.pack_into("<I", data, 28, 7)  # BusTypeUsb
            return bytes(data)
        return result

    return ioctl
