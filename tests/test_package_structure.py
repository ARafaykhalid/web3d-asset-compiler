"""
Pure Python package structure and metadata test.
"""

import unittest
import os
import ast

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")


class TestPackageStructure(unittest.TestCase):

    def test_addon_directory_exists(self):
        self.assertTrue(os.path.isdir(ADDON_DIR), "Addon directory must exist.")

    def test_init_file_exists(self):
        init_path = os.path.join(ADDON_DIR, "__init__.py")
        self.assertTrue(os.path.isfile(init_path), "__init__.py must exist.")

    def test_subpackages_exist(self):
        subpackages = ["baking", "exporter", "presets", "ui", "utils"]
        for pkg in subpackages:
            pkg_dir = os.path.join(ADDON_DIR, pkg)
            init_file = os.path.join(pkg_dir, "__init__.py")
            self.assertTrue(os.path.isdir(pkg_dir), f"Subpackage directory '{pkg}' must exist.")
            self.assertTrue(os.path.isfile(init_file), f"Subpackage '{pkg}' must have __init__.py.")

    def test_version_py(self):
        version_file = os.path.join(ADDON_DIR, "version.py")
        self.assertTrue(os.path.isfile(version_file), "version.py must exist.")
        with open(version_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("VERSION", content)
        self.assertIn("ADDON_NAME", content)


if __name__ == "__main__":
    unittest.main()
