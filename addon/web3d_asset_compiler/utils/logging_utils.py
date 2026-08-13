"""
Logging and UI redraw utilities.
"""

import bpy


def force_ui_redraw():
    """Force Blender to redraw the UI so the user can see progress during long operations."""
    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except Exception:
        pass


def safe_filename(name):
    """Convert a Blender datablock name into a portable filename stem."""
    import re
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip(' .')
    return safe or "bake"
