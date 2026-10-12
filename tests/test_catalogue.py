import unittest
from urllib.parse import urlparse

from autoboot_usb.catalogue import BY_KEY, CATALOGUE


class CatalogueTests(unittest.TestCase):
    def test_unique_keys(self):
        self.assertEqual(len({system.key for system in CATALOGUE}), len(CATALOGUE))

    def test_only_curated_downloads(self):
        self.assertEqual({s.key for s in CATALOGUE if s.releases}, {"kali", "ubuntu"})
        for system in CATALOGUE:
            for release in system.releases:
                if not release.url:
                    continue
                self.assertIn(
                    urlparse(release.url).netloc, ("cdimage.kali.org", "releases.ubuntu.com")
                )

    def test_macos_has_honest_limitations(self):
        self.assertFalse(BY_KEY["macos"].releases)
        self.assertIn("compatible Mac", BY_KEY["macos"].support_message)


if __name__ == "__main__":
    unittest.main()
