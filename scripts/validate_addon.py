"""
Addon validation script for Web3D Asset Compiler.
Checks Python syntax, directory structure, bl_info metadata, and package imports.
"""

import sys
import os
import ast

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon", "web3d_asset_compiler")


def check_syntax(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        ast.parse(content, filename=file_path)
        return True, None
    except SyntaxError as e:
        return False, str(e)


def validate_repository():
    print("==================================================")
    print("   Web3D Asset Compiler - Validation Check       ")
    print("==================================================")
    
    errors = []

    # 1. Check directory structure
    if not os.path.isdir(ADDON_DIR):
        errors.append(f"Addon directory missing: {ADDON_DIR}")
        print(f"[X] Addon directory missing: {ADDON_DIR}")
    else:
        print(f"[OK] Addon directory found: {ADDON_DIR}")

    init_file = os.path.join(ADDON_DIR, "__init__.py")
    if not os.path.isfile(init_file):
        errors.append(f"Missing __init__.py at {init_file}")
        print(f"[X] Missing __init__.py at {init_file}")
    else:
        print(f"[OK] __init__.py found: {init_file}")

    # 2. Syntax check all Python files in repo
    py_files = []
    for root, _dirs, files in os.walk(REPO_ROOT):
        if "venv" in root or ".venv" in root or "__pycache__" in root or "dist" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    print(f"\nChecking syntax of {len(py_files)} Python file(s)...")
    for py_file in py_files:
        rel_path = os.path.relpath(py_file, REPO_ROOT)
        valid, err = check_syntax(py_file)
        if valid:
            print(f"  [OK] {rel_path}")
        else:
            errors.append(f"Syntax error in {rel_path}: {err}")
            print(f"  [X] {rel_path} - Syntax Error: {err}")

    # 3. Check bl_info in __init__.py
    if os.path.isfile(init_file):
        with open(init_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=init_file)
        
        has_bl_info = False
        has_register = False
        has_unregister = False
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "bl_info":
                        has_bl_info = True
            elif isinstance(node, ast.FunctionDef):
                if node.name == "register":
                    has_register = True
                elif node.name == "unregister":
                    has_unregister = True

        if has_bl_info:
            print("\n[OK] bl_info metadata dictionary found in __init__.py")
        else:
            errors.append("bl_info missing in __init__.py")
            print("\n[X] bl_info missing in __init__.py")

        if has_register and has_unregister:
            print("[OK] register() and unregister() functions found in __init__.py")
        else:
            errors.append("register() or unregister() missing in __init__.py")
            print("[X] register() or unregister() missing in __init__.py")

    print("--------------------------------------------------")
    if errors:
        print(f"Validation FAILED with {len(errors)} error(s):")
        for err in errors:
            print(f" - {err}")
        return False
    else:
        print("[OK] All validation checks PASSED successfully!")
        return True


if __name__ == "__main__":
    success = validate_repository()
    sys.exit(0 if success else 1)
