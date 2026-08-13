"""
Properties Editor render panel integration for Web3D Asset Compiler.
"""

from bpy.types import Panel
from .sidebar import draw_unified_ui


class WEB3D_PT_RenderProps(Panel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'render'
    bl_label       = 'Web3D Asset Compiler'
    bl_idname      = 'WEB3D_PT_RenderProps'

    def draw(self, context):
        draw_unified_ui(self.layout, context)
