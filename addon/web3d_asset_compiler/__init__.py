"""
Web3D Asset Compiler — Official Blender Addon Package
Unified pipeline for texture baking, mesh optimization, and Three.js animation export.
"""

import bpy
from .version import VERSION, ADDON_NAME, AUTHOR, BLENDER_VERSION
from .baking import BAKING_CLASSES, AHB_Properties
from .exporter import EXPORTER_CLASSES, TJS_Properties
from .presets import PRESET_CLASSES
from .ui import UI_CLASSES

bl_info = {
    "name": ADDON_NAME,
    "author": AUTHOR,
    "version": VERSION,
    "blender": BLENDER_VERSION,
    "location": "3D Viewport > Sidebar > Web3D  |  Properties > Render > Web3D Asset Compiler",
    "description": "Unified Web 3D Asset Pipeline: Automated Lightmap & Texture Baking, Optimization, and Three.js Animation Export",
    "category": "Import-Export",
}

ALL_CLASSES = BAKING_CLASSES + EXPORTER_CLASSES + PRESET_CLASSES + UI_CLASSES


def register():
    # Safely unregister any existing registrations to ensure idempotent registration
    for cls in reversed(ALL_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (AttributeError, RuntimeError, ValueError):
            pass

    for cls in ALL_CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            try:
                bpy.utils.unregister_class(cls)
                bpy.utils.register_class(cls)
            except Exception:
                pass

    bpy.types.Scene.ahb_props = bpy.props.PointerProperty(type=AHB_Properties)
    bpy.types.Scene.tjs_props = bpy.props.PointerProperty(type=TJS_Properties)
    bpy.types.Scene.web3d_status = bpy.props.StringProperty(
        name="Web3D Compiler Status",
        default="Ready",
    )


def unregister():
    if hasattr(bpy.types.Scene, 'ahb_props'):
        try:
            del bpy.types.Scene.ahb_props
        except (AttributeError, RuntimeError):
            pass
    if hasattr(bpy.types.Scene, 'tjs_props'):
        try:
            del bpy.types.Scene.tjs_props
        except (AttributeError, RuntimeError):
            pass
    if hasattr(bpy.types.Scene, 'web3d_status'):
        try:
            del bpy.types.Scene.web3d_status
        except (AttributeError, RuntimeError):
            pass

    for cls in reversed(ALL_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (AttributeError, RuntimeError, ValueError):
            pass


if __name__ == "__main__":
    register()
