"""
Unified action operators for Web3D Asset Compiler UI.
"""

import bpy
from bpy.types import Operator

from ..exporter.binary_exporter import animation_export_readiness


def _build_export_mode(context, props):
    portfolio = getattr(props, "portfolio_one_click", False)
    animation_allowed = (
        (portfolio or getattr(props, "export_format", "GLB") == "GLB")
        and getattr(props, "export_skins", True)
    )
    if portfolio and not animation_allowed:
        return None, "portfolio character export requires skins enabled"
    if animation_allowed:
        animation_ready, detail = animation_export_readiness(
            context,
            props,
            force_all_actions=portfolio,
            force_character_scope=portfolio,
        )
        if animation_ready:
            return "ANIMATIONS", None
        if portfolio:
            return None, detail

    scene_scope = getattr(props, "export_scope", "SELECTED") == "SCENE"
    export_objects = context.scene.objects if scene_scope else context.selected_objects
    if not export_objects:
        detail = "the scene is empty" if scene_scope else "no export objects are selected"
        return None, detail
    return "MODEL", None


class WEB3D_OT_BuildWebAsset(Operator):
    """Run full Web3D compilation pipeline: auto setup -> bake -> apply -> export"""
    bl_idname = "web3d.build_web_asset"
    bl_label = "Build Web Asset"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return bool(
            scene
            and hasattr(scene, "ahb_props")
            and hasattr(scene, "tjs_props")
        )

    def execute(self, context):
        export_mode, export_error = _build_export_mode(
            context,
            context.scene.tjs_props,
        )
        if export_error:
            msg = f"Build cannot start: {export_error}."
            context.scene.web3d_status = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        export_label = (
            "character and animations"
            if export_mode == "ANIMATIONS"
            else "model only"
        )

        self.report({'INFO'}, "Starting Web3D Asset Compiler full pipeline.")
        context.scene.web3d_status = "Build started: baking textures"

        # Step 1: Run Quick Bake
        try:
            res = bpy.ops.ahb.quick_bake()
            if 'FINISHED' not in res:
                msg = "Build stopped: bake failed or was cancelled; export skipped."
                context.scene.web3d_status = msg
                self.report({'ERROR'}, msg)
                return {'CANCELLED'}
        except Exception as e:
            msg = f"Build stopped: bake failed ({e}); export skipped."
            context.scene.web3d_status = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        # Step 2: Run Exporter
        context.scene.web3d_status = f"Bake complete; exporting {export_label}"
        try:
            res = (
                bpy.ops.tjs.export_animations()
                if export_mode == "ANIMATIONS"
                else bpy.ops.tjs.export_character_glb()
            )
            if 'FINISHED' in res:
                context.scene.web3d_status = (
                    f"Build complete: baked and exported {export_label}"
                )
                self.report(
                    {'INFO'},
                    f"Web3D asset baked and exported as {export_label} successfully.",
                )
                return {'FINISHED'}
            else:
                context.scene.web3d_status = "Build failed during export"
                self.report({'ERROR'}, "Bake completed, but export failed or was cancelled.")
                return {'CANCELLED'}
        except Exception as e:
            msg = f"Export step failed: {e}"
            context.scene.web3d_status = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
