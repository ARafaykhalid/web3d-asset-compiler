"""
Operator for applying Web3D compilation presets.
"""

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty
from .preset_data import PRESETS


class WEB3D_OT_ApplyPreset(Operator):
    """Apply a target optimization preset to Bake and Export settings"""
    bl_idname = "web3d.apply_preset"
    bl_label = "Apply Web3D Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset_key: EnumProperty(
        name="Preset",
        items=[
            (key, data['label'], data['description'])
            for key, data in PRESETS.items()
        ],
        default='R3F_OPTIMIZED',
    )

    def execute(self, context):
        preset = PRESETS.get(self.preset_key)
        if not preset:
            self.report({'ERROR'}, f"Preset '{self.preset_key}' not found.")
            return {'CANCELLED'}

        ahb = context.scene.ahb_props
        for prop_name, val in preset.get('ahb', {}).items():
            if hasattr(ahb, prop_name):
                setattr(ahb, prop_name, val)

        tjs = context.scene.tjs_props
        for prop_name, val in preset.get('tjs', {}).items():
            if hasattr(tjs, prop_name):
                setattr(tjs, prop_name, val)

        msg = f"Applied preset: {preset['label']}"
        context.scene.web3d_status = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}
