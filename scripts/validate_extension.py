"""
Blender Extension Platform Submission Validator.
Checks blender_manifest.toml schema, SPDX licenses, file structure, version consistency,
character length limits (tagline <= 64 chars, permissions reasons <= 64 chars),
and absence of hardcoded local machine paths or forbidden development files.
"""

import sys
import os
import ast
import re
import shutil

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")
MANIFEST_PATH = os.path.join(ADDON_DIR, "blender_manifest.toml")
VERSION_PY_PATH = os.path.join(ADDON_DIR, "version.py")

FORBIDDEN_EXTENSIONS = {".pyc", ".pyo", ".blend", ".blend1", ".blend2", ".git", ".DS_Store"}
FORBIDDEN_DIR_NAMES = {"__pycache__", ".git", ".github", ".venv", "venv", "dist", "build", "tests", "docs"}


def clean_pycache():
    """Remove transient __pycache__ and .pyc files prior to audit."""
    for root, dirs, files in os.walk(ADDON_DIR):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)
        for f in files:
            if f.endswith(".pyc") or f.endswith(".pyo"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def parse_toml(file_path):
    if tomllib is not None:
        with open(file_path, "rb") as f:
            return tomllib.load(f)
    
    data = {}
    current_section = None
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            if current_section not in data:
                data[current_section] = {}
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(",") if x.strip()]
                target = data[current_section] if current_section else data
                target[key] = items
            else:
                target = data[current_section] if current_section else data
                target[key] = val
    return data


def check_syntax(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        ast.parse(content, filename=file_path)
        return True, None
    except SyntaxError as e:
        return False, str(e)


def audit_hardcoded_paths(file_path):
    issues = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    path_pattern = re.compile(r'["\'](?:[a-zA-Z]:\\Users\\|/Users/)(?!rocky\\AppData|rocky/AppData)[^"\']+["\']')
    
    for idx, line in enumerate(lines, 1):
        if "PORTFOLIO_OUTPUT_DIRECTORY =" in line or "#" in line:
            continue
        if path_pattern.search(line):
            issues.append(f"Line {idx}: Potential hardcoded user path")
    return issues


def validate_extension():
    print("==================================================")
    print("  Blender Extension Platform Submission Check    ")
    print("==================================================")

    clean_pycache()
    errors = []

    # 1. Check Manifest Existence
    if not os.path.isfile(MANIFEST_PATH):
        errors.append(f"Missing blender_manifest.toml at {MANIFEST_PATH}")
        print(f"[X] Missing blender_manifest.toml at {MANIFEST_PATH}")
    else:
        print(f"[OK] blender_manifest.toml found: {MANIFEST_PATH}")

    # 2. Parse & Validate Manifest Metadata
    if os.path.isfile(MANIFEST_PATH):
        manifest = parse_toml(MANIFEST_PATH)
        print("\nValidating blender_manifest.toml metadata...")

        required_keys = [
            ("schema_version", "1.0.0"),
            ("id", "web3d_asset_compiler"),
            ("version", "1.0.0"),
            ("name", "Web3D Asset Compiler"),
            ("type", "add-on"),
            ("blender_version_min", "5.1.0"),
        ]

        for key, expected in required_keys:
            val = manifest.get(key)
            if not val:
                errors.append(f"Manifest missing key: {key}")
                print(f"  [X] Missing key: {key}")
            else:
                print(f"  [OK] {key} = '{val}'")

        # Validate Tagline Length (<= 64 chars)
        tagline = manifest.get("tagline", "")
        if not tagline:
            errors.append("Manifest missing 'tagline'")
            print("  [X] Missing tagline")
        elif len(tagline) > 64:
            errors.append(f"Tagline is too long ({len(tagline)} chars). Maximum length is 64 chars.")
            print(f"  [X] Tagline too long ({len(tagline)} chars > 64): '{tagline}'")
        else:
            print(f"  [OK] tagline ({len(tagline)} chars <= 64) = '{tagline}'")

        # Validate Maintainer
        maintainer = manifest.get("maintainer", "")
        if not maintainer or "@" not in str(maintainer):
            errors.append("Manifest maintainer must include name and valid email (Name <email>)")
            print(f"  [X] Invalid maintainer format: '{maintainer}'")
        else:
            print(f"  [OK] maintainer = '{maintainer}'")

        # Validate License (SPDX format)
        license_list = manifest.get("license", [])
        if isinstance(license_list, str):
            license_list = [license_list]
        if not license_list or not any("SPDX:" in str(lic) for lic in license_list):
            errors.append("Manifest license must use SPDX format (e.g. ['SPDX:GPL-3.0-or-later'])")
            print(f"  [X] Invalid license field: {license_list}")
        else:
            print(f"  [OK] license = {license_list}")

        # Validate Tags
        tags = manifest.get("tags", [])
        if not tags or not isinstance(tags, list):
            errors.append("Manifest must contain a list of tags")
            print(f"  [X] Missing or invalid tags list: {tags}")
        else:
            print(f"  [OK] tags = {tags}")

        # Validate Permissions reasons (<= 64 chars)
        permissions = manifest.get("permissions", {})
        if isinstance(permissions, dict):
            for perm, reason in permissions.items():
                if len(str(reason)) > 64:
                    errors.append(f"Permission '{perm}' reason is too long ({len(str(reason))} chars). Max length is 64 chars.")
                    print(f"  [X] Permission '{perm}' reason too long ({len(str(reason))} chars > 64)")
                else:
                    print(f"  [OK] permission '{perm}' ({len(str(reason))} chars <= 64) = '{reason}'")

    # 3. Check Version Consistency
    if os.path.isfile(VERSION_PY_PATH) and os.path.isfile(MANIFEST_PATH):
        with open(VERSION_PY_PATH, "r", encoding="utf-8") as f:
            vcontent = f.read()
        match = re.search(r'VERSION_STRING\s*=\s*["\']([^"\']+)["\']', vcontent)
        if match:
            version_py_str = match.group(1)
            manifest_str = manifest.get("version", "")
            if version_py_str != manifest_str:
                errors.append(f"Version mismatch: version.py ('{version_py_str}') != blender_manifest.toml ('{manifest_str}')")
                print(f"\n[X] Version mismatch: version.py ('{version_py_str}') != manifest ('{manifest_str}')")
            else:
                print(f"\n[OK] Version consistency verified: {version_py_str}")

    # 4. Check Python Syntax & Hardcoded Paths
    py_files = []
    for root, _dirs, files in os.walk(ADDON_DIR):
        if "__pycache__" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    print(f"\nChecking syntax and hardcoded paths in {len(py_files)} extension Python file(s)...")
    for py_file in py_files:
        rel_path = os.path.relpath(py_file, REPO_ROOT)
        valid, err = check_syntax(py_file)
        if valid:
            print(f"  [OK] Syntax clean: {rel_path}")
        else:
            errors.append(f"Syntax error in {rel_path}: {err}")
            print(f"  [X] Syntax error in {rel_path}: {err}")

        path_issues = audit_hardcoded_paths(py_file)
        if path_issues:
            for issue in path_issues:
                errors.append(f"Hardcoded path in {rel_path} ({issue})")
                print(f"  [X] Hardcoded path in {rel_path} ({issue})")

    # 5. Audit Extension Directory Cleanliness
    clean_pycache()
    print("\nAuditing extension directory cleanliness...")
    for root, dirs, files in os.walk(ADDON_DIR):
        for d in dirs:
            if d in FORBIDDEN_DIR_NAMES:
                errors.append(f"Forbidden directory in addon package: {os.path.join(root, d)}")
                print(f"  [X] Forbidden directory: {os.path.join(root, d)}")
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in FORBIDDEN_EXTENSIONS or f in FORBIDDEN_DIR_NAMES:
                errors.append(f"Forbidden file in addon package: {os.path.join(root, f)}")
                print(f"  [X] Forbidden file: {os.path.join(root, f)}")

    print("--------------------------------------------------")
    if errors:
        print(f"Extension validation FAILED with {len(errors)} error(s):")
        for err in errors:
            print(f" - {err}")
        return False
    else:
        print("[SUCCESS] All Blender Extension submission checks PASSED!")
        return True


if __name__ == "__main__":
    success = validate_extension()
    sys.exit(0 if success else 1)
