"""
Operator for applying Web3D compilation presets.
"""

from bpy.types import Operator
from bpy.props import EnumProperty
from .preset_data import PRESETS


def _set_properties(target, values, prefix, skipped):
    for prop_name, value in values.items():
        if not hasattr(target, prop_name):
            skipped.append(f"{prefix}.{prop_name}")
            continue
        try:
            setattr(target, prop_name, value)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            skipped.append(f"{prefix}.{prop_name}")


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
        skipped = []
        _set_properties(ahb, preset.get('ahb', {}), "Bake", skipped)

        tjs = context.scene.tjs_props
        _set_properties(tjs, preset.get('tjs', {}), "Export", skipped)

        msg = f"Applied preset: {preset['label']}"
        context.scene.web3d_status = "Ready"
        if skipped:
            preview = ", ".join(skipped[:3])
            if len(skipped) > 3:
                preview += f", +{len(skipped) - 3} more"
            self.report({'WARNING'}, f"{msg}. Skipped unavailable setting(s): {preview}")
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}
