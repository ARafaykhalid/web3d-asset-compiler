"""
Headless Blender addon registration test.
Executed with: blender.exe --background --python tests/test_blender_addon_register.py
"""

import sys
import os
import bpy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PARENT_DIR = os.path.join(REPO_ROOT, "addon")

if ADDON_PARENT_DIR not in sys.path:
    sys.path.insert(0, ADDON_PARENT_DIR)

print("==================================================")
print("  Headless Blender Addon Registration Test       ")
print("==================================================")

try:
    import web3d_asset_compiler
    print(f"[OK] Successfully imported web3d_asset_compiler")
    print(f"  Addon Name: {web3d_asset_compiler.bl_info['name']}")
    print(f"  Version:    {web3d_asset_compiler.bl_info['version']}")
    
    # Test register()
    web3d_asset_compiler.register()
    print("[OK] Successfully executed register()")

    # Verify operators in Blender
    expected_operators = [
        "web3d.build_web_asset",
        "ahb.quick_bake",
        "ahb.bake_all",
        "ahb.rebake_selected",
        "ahb.setup_materials",
        "ahb.apply_baked",
        "ahb.toggle_baked_view",
        "ahb.restore_materials",
        "ahb.preview_uv",
        "ahb.renew_auto_seams",
        "ahb.pack_islands",
        "ahb.clear_bake_nodes",
        "ahb.cleanup_images",
        "ahb.export_materials_json",
        "ahb.import_materials_json",
        "tjs.export_character_glb",
        "tjs.export_animations",
        "web3d.apply_preset",
    ]

    missing = []
    for op in expected_operators:
        module, op_name = op.split(".")
        op_group = getattr(bpy.ops, module, None)
        if op_group is None or not hasattr(op_group, op_name):
            missing.append(op)

    if missing:
        print(f"[X] Missing registered operators: {missing}")
        sys.exit(1)
    else:
        print(f"[OK] Verified registration of {len(expected_operators)} operators!")

    # Verify scene properties
    scene = bpy.context.scene
    has_ahb = hasattr(scene, "ahb_props")
    has_tjs = hasattr(scene, "tjs_props")
    has_status = hasattr(scene, "web3d_status")

    if has_ahb and has_tjs and has_status:
        print("[OK] Verified scene property pointers (ahb_props, tjs_props, web3d_status)")
    else:
        print("[X] Missing scene property pointers!")
        sys.exit(1)

    # Test unregister()
    web3d_asset_compiler.unregister()
    print("[OK] Successfully executed unregister()")

    print("--------------------------------------------------")
    print("[SUCCESS] All Blender addon registration tests PASSED!")
except Exception as e:
    print(f"\n[X] Error during Blender addon test: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
