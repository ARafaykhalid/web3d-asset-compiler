"""
Package Web3D Asset Compiler as an installable Blender extension ZIP.

Manifest validation is Blender's job -- run it with:
    blender --command extension validate addon/web3d_asset_compiler
CI does that before calling this script.
"""

import ast
import os
import sys
import tomllib
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")
DIST_DIR = os.path.join(REPO_ROOT, "dist")

SKIP_DIRS = {"__pycache__", ".git", ".github", ".venv", "venv",
             "dist", "build"}
SKIP_EXTS = {".pyc", ".pyo", ".blend", ".blend1", ".blend2", ".DS_Store"}


def check_syntax():
    """Parse every module. ast.parse, not compileall -- compileall writes
    __pycache__ next to the source, which fails on synced/network folders."""
    errors = []
    for root, dirs, files in os.walk(ADDON_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as handle:
                try:
                    ast.parse(handle.read(), filename=path)
                except SyntaxError as exc:
                    errors.append(f"{os.path.relpath(path, REPO_ROOT)}: {exc}")
    if errors:
        sys.exit("Syntax errors:\n  " + "\n  ".join(errors))


def build_extension():
    if not os.path.isfile(os.path.join(ADDON_DIR, "blender_manifest.toml")):
        sys.exit(f"blender_manifest.toml missing in {ADDON_DIR}")

    check_syntax()

    with open(os.path.join(ADDON_DIR, "blender_manifest.toml"), "rb") as handle:
        version = tomllib.load(handle)["version"]
    zip_path = os.path.join(DIST_DIR, f"web3d_asset_compiler-{version}.zip")

    os.makedirs(DIST_DIR, exist_ok=True)
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if os.path.splitext(name)[1].lower() in SKIP_EXTS:
                    continue
                source = os.path.join(root, name)
                # Blender expects blender_manifest.toml at the archive root.
                arc = os.path.relpath(source, ADDON_DIR).replace(os.sep, "/")
                archive.write(source, arc)
                count += 1

    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip():
            sys.exit(f"Corrupt archive: {zip_path}")
        names = archive.namelist()
        for required in ("blender_manifest.toml", "__init__.py"):
            if required not in names:
                sys.exit(f"{zip_path} is missing {required} at the archive root")

    size = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"[OK] {zip_path} ({count} files, {size:.2f} MB)")


if __name__ == "__main__":
    build_extension()
