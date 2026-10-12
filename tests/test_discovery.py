import unittest

from autoboot_usb.adapters.linux import parse_lsblk
from autoboot_usb.adapters.macos import parse_diskutil
from autoboot_usb.adapters.windows import parse_windows_disks


class DiscoveryTests(unittest.TestCase):
    def test_windows_json_disks(self):
        payload = [
            {
                "Id": "PhysicalDrive1",
                "Name": "USB Thumb",
                "SizeBytes": 16_000_000_000,
                "Bus": "USB",
                "IsBoot": False,
                "IsSystem": False,
                "ReadOnly": False,
            },
            {
                "Id": "PhysicalDrive2",
                "Name": "External system drive",
                "SizeBytes": 32_000_000_000,
                "Bus": "USB",
                "IsBoot": True,
                "IsSystem": True,
                "ReadOnly": False,
            },
            {
                "Id": "PhysicalDrive0",
                "Name": "Internal",
                "SizeBytes": 1_000_000_000_000,
                "Bus": "NVMe",
                "IsBoot": False,
                "IsSystem": False,
                "ReadOnly": False,
            },
        ]
        devices = parse_windows_disks(payload)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].device_id, "PhysicalDrive1")

    def test_linux_usb_and_root_protection(self):
        payload = {
            "blockdevices": [
                {"name": "sda", "path": "/dev/sda", "tran": "sata", "type": "disk", "size": 100},
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "tran": "usb",
                    "type": "disk",
                    "size": 12345,
                    "model": "External",
                    "children": [{"type": "part", "mountpoints": ["/media/a"]}],
                },
                {
                    "name": "sdc",
                    "path": "/dev/sdc",
                    "tran": "usb",
                    "type": "disk",
                    "size": 12345,
                    "model": "System USB",
                    "children": [{"type": "part", "mountpoints": ["/"]}],
                },
                {
                    "name": "sdd",
                    "path": "/dev/sdd",
                    "tran": "usb",
                    "type": "disk",
                    "size": 12345,
                    "model": "Read-only",
                    "ro": 1,
                },
            ]
        }
        devices = parse_lsblk(payload)
        self.assertEqual([device.device_id for device in devices], ["/dev/sdb"])

    def test_macos_nonusb_blocked(self):
        mac_usb = parse_diskutil(
            {
                "Internal": False,
                "BusProtocol": "USB",
                "TotalSize": 32000,
                "DeviceNode": "/dev/disk4",
            },
            "disk4",
        )
        mac_internal = parse_diskutil(
            {"Internal": True, "BusProtocol": "PCI-Express", "TotalSize": 32000}, "disk0"
        )
        self.assertTrue(mac_usb.safe_to_offer)
        self.assertFalse(mac_internal.safe_to_offer)


if __name__ == "__main__":
    unittest.main()
