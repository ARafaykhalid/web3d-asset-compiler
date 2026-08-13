"""
Blender Extension Packaging Script for extensions.blender.org.
Generates dist/web3d_asset_compiler-1.0.0.zip ready for submission.
"""

import os
import sys
import shutil
import zipfile
from validate_extension import validate_extension, MANIFEST_PATH

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_SOURCE_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")
DIST_DIR = os.path.join(REPO_ROOT, "dist")
ZIP_PATH = os.path.join(DIST_DIR, "web3d_asset_compiler-1.0.0.zip")

EXCLUDED_EXTENSIONS = {".pyc", ".pyo", ".blend", ".blend1", ".blend2", ".git", ".DS_Store", ".tmp"}
EXCLUDED_NAMES = {"__pycache__", ".git", ".github", ".venv", "venv", "dist", "build", "tests", "docs", "scripts", "imported_source"}


def build_extension():
    print("==================================================")
    print("   Building Blender Extension Submission Artifact ")
    print("==================================================")

    # 1. Run validation
    if not validate_extension():
        print("\n[X] Extension packaging aborted due to validation failures.")
        sys.exit(1)

    # 2. Clean previous build
    if os.path.exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
        print(f"\n[OK] Cleaned output directory: {DIST_DIR}")
    os.makedirs(DIST_DIR, exist_ok=True)

    # 3. Package extension ZIP
    print(f"\nPackaging extension ZIP: {ZIP_PATH}...")
    archived_files = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(ADDON_SOURCE_DIR):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDED_NAMES]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in EXCLUDED_EXTENSIONS or file in EXCLUDED_NAMES:
                    continue

                abs_file_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_file_path, ADDON_SOURCE_DIR)
                
                # Blender extensions package layout: manifest and code directly at root of ZIP
                arc_name = rel_path.replace("\\", "/")

                zipf.write(abs_file_path, arc_name)
                archived_files += 1
                print(f"  + {arc_name}")

    # 4. Verify ZIP structure and integrity
    print("\n--------------------------------------------------")
    print("Verifying extension ZIP archive integrity...")
    with zipfile.ZipFile(ZIP_PATH, "r") as zipf:
        test_fail = zipf.testzip()
        if test_fail:
            print(f"[X] Corrupt file detected in extension ZIP: {test_fail}")
            sys.exit(1)

        names = zipf.namelist()
        has_manifest = "blender_manifest.toml" in names
        has_init = "__init__.py" in names

        if not has_manifest:
            print("[X] Submission ZIP missing 'blender_manifest.toml' at archive root!")
            sys.exit(1)
        if not has_init:
            print("[X] Submission ZIP missing '__init__.py' at archive root!")
            sys.exit(1)

        zip_size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
        print(f"[OK] Extension ZIP verified ({archived_files} files, {zip_size_mb:.2f} MB)")
        print(f"\n[SUCCESS] Distributable Extension Artifact Ready:")
        print(f"         {ZIP_PATH}")


if __name__ == "__main__":
    build_extension()
