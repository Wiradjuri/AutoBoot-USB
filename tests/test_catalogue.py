import unittest
from urllib.parse import urlparse

from autoboot_usb.catalogue import BY_KEY, CATALOGUE


class CatalogueTests(unittest.TestCase):
    def test_unique_keys(self):
        self.assertEqual(len({system.key for system in CATALOGUE}), len(CATALOGUE))

    def test_only_curated_downloads(self):
        self.assertEqual(
            {s.key for s in CATALOGUE if s.releases},
            {
                "kali",
                "ubuntu",
                "debian",
                "mint",
                "fedora",
                "arch",
                "parrot",
                "win10",
                "win11",
                "macos",
            },
        )
        from autoboot_usb.services.download import APPROVED_ORIGINS

        for system in CATALOGUE:
            for release in system.releases:
                if not release.url:
                    continue
                self.assertIn(urlparse(release.url).netloc, APPROVED_ORIGINS)

    def test_macos_has_honest_limitations(self):
        self.assertEqual(BY_KEY["macos"].releases[0].provider, "apple")
        self.assertIn("compatible Mac", BY_KEY["macos"].support_message)


if __name__ == "__main__":
    unittest.main()
