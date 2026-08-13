"""
Unit test for blender_manifest.toml schema validation.
"""

import unittest
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")
MANIFEST_PATH = os.path.join(ADDON_DIR, "blender_manifest.toml")


class TestExtensionManifest(unittest.TestCase):

    def test_manifest_file_exists(self):
        self.assertTrue(os.path.isfile(MANIFEST_PATH), "blender_manifest.toml must exist.")

    def test_manifest_contents(self):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('schema_version = "1.0.0"', content)
        self.assertIn('id = "web3d_asset_compiler"', content)
        self.assertIn('type = "add-on"', content)
        self.assertIn('blender_version_min = "5.1.0"', content)
        self.assertIn("SPDX:GPL-3.0-or-later", content)
        self.assertIn("[permissions]", content)


if __name__ == "__main__":
    unittest.main()
