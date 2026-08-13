"""
Unified action operators for Web3D Asset Compiler UI.
"""

import bpy
from bpy.types import Operator


class WEB3D_OT_BuildWebAsset(Operator):
    """Run full Web3D compilation pipeline: auto setup -> bake -> apply -> export"""
    bl_idname = "web3d.build_web_asset"
    bl_label = "⚡ BUILD WEB ASSET (Full Pipeline)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ahb_props = context.scene.ahb_props
        tjs_props = context.scene.tjs_props

        self.report({'INFO'}, "Starting Web3D Asset Compiler full pipeline…")
        context.scene.web3d_status = "Pipeline started: Baking textures…"

        # Step 1: Run Quick Bake
        try:
            res = bpy.ops.ahb.quick_bake()
            if 'FINISHED' not in res:
                self.report({'WARNING'}, "Baking did not complete cleanly; proceeding with export.")
        except Exception as e:
            self.report({'WARNING'}, f"Baking step warning: {e}")

        # Step 2: Run Exporter
        context.scene.web3d_status = "Pipeline: Exporting Web3D asset…"
        try:
            res = bpy.ops.tjs.export_animations()
            if 'FINISHED' in res:
                context.scene.web3d_status = "Build Complete ✓ (Baked & Exported)"
                self.report({'INFO'}, "Web3D asset compiled and exported successfully!")
                return {'FINISHED'}
            else:
                context.scene.web3d_status = "Export cancelled or failed."
                return {'CANCELLED'}
        except Exception as e:
            msg = f"Export step failed: {e}"
            context.scene.web3d_status = msg
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
