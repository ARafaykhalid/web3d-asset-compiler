"""
Build script to package Web3D Asset Compiler into an installable Blender addon ZIP.
Generates dist/web3d_asset_compiler.zip with root directory web3d_asset_compiler/.
"""

import os
import sys
import shutil
import zipfile
from validate_addon import validate_repository

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_SOURCE_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")
DIST_DIR = os.path.join(REPO_ROOT, "dist")
ZIP_PATH = os.path.join(DIST_DIR, "web3d_asset_compiler.zip")

EXCLUDED_EXTENSIONS = {".pyc", ".pyo", ".blend", ".blend1", ".blend2", ".git", ".DS_Store"}
EXCLUDED_NAMES = {"__pycache__", ".git", ".github", ".venv", "venv", "dist", "build"}


def build_zip():
    print("==================================================")
    print("    Building web3d_asset_compiler.zip             ")
    print("==================================================")

    # 1. Run validation
    if not validate_repository():
        print("\n[X] Build aborted due to validation failure.")
        sys.exit(1)

    # 2. Clean previous build
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
        print(f"\n[OK] Cleaned output directory: {DIST_DIR}")
    os.makedirs(DIST_DIR, exist_ok=True)

    # 3. Create ZIP
    print(f"\nCreating ZIP archive: {ZIP_PATH}...")
    archived_files = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(ADDON_SOURCE_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_NAMES]
            
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDED_EXTENSIONS or file in EXCLUDED_NAMES:
                    continue

                abs_file_path = os.path.join(root, file)
                rel_path_in_addon = os.path.relpath(abs_file_path, ADDON_SOURCE_DIR)
                arc_name = os.path.join("web3d_asset_compiler", rel_path_in_addon)

                zipf.write(abs_file_path, arc_name)
                archived_files += 1
                print(f"  + {arc_name}")

    # 4. Verify ZIP
    print("\n--------------------------------------------------")
    print("Verifying ZIP archive integrity...")
    with zipfile.ZipFile(ZIP_PATH, "r") as zipf:
        test_fail = zipf.testzip()
        if test_fail:
            print(f"[X] Corrupt file detected in ZIP: {test_fail}")
            sys.exit(1)

        names = zipf.namelist()
        has_init = "web3d_asset_compiler/__init__.py" in names
        if not has_init:
            print("[X] ZIP does not contain web3d_asset_compiler/__init__.py")
            sys.exit(1)

        zip_size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
        print(f"[OK] ZIP verified successfully ({archived_files} files, {zip_size_mb:.2f} MB)")
        print(f"\n[SUCCESS] Output artifact ready: {ZIP_PATH}")


if __name__ == "__main__":
    build_zip()
