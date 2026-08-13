"""
UI package initialization.
"""

from .operators import WEB3D_OT_BuildWebAsset
from .sidebar import WEB3D_PT_Sidebar
from .render_panel import WEB3D_PT_RenderProps

UI_CLASSES = [
    WEB3D_OT_BuildWebAsset,
    WEB3D_PT_Sidebar,
    WEB3D_PT_RenderProps,
]
