"""
Web3D Asset Compiler — Official Blender Extension Package
Unified pipeline for texture baking, mesh optimization, and Three.js animation export.
"""

import bpy
from .baking import BAKING_CLASSES, AHB_Properties
from .exporter import EXPORTER_CLASSES, TJS_Properties
from .presets import PRESET_CLASSES
from .ui import UI_CLASSES

# Metadata lives in blender_manifest.toml; bl_info is the legacy-addon path and
# is ignored for extensions (Blender 4.2+).

ALL_CLASSES = BAKING_CLASSES + EXPORTER_CLASSES + PRESET_CLASSES + UI_CLASSES


def register():
    for cls in ALL_CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ahb_props = bpy.props.PointerProperty(type=AHB_Properties)
    bpy.types.Scene.tjs_props = bpy.props.PointerProperty(type=TJS_Properties)
    bpy.types.Scene.web3d_status = bpy.props.StringProperty(
        name="Web3D Compiler Status",
        default="Ready",
    )


def unregister():
    for prop in ('ahb_props', 'tjs_props', 'web3d_status'):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

    for cls in reversed(ALL_CLASSES):
        bpy.utils.unregister_class(cls)
