"""
Logging and UI redraw utilities.
"""

import bpy


def force_ui_redraw():
    """Ask Blender to repaint on the next event loop pass.

    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP') forces a synchronous window
    swap and measured 0.79s per call on this pipeline -- longer than most of
    the work it was announcing, and it defeats the point of the job-based bake.
    area.tag_redraw() is free and lands on the next pass.
    """
    try:
        area = bpy.context.area
        if area is not None:
            area.tag_redraw()
    except Exception:
        pass


def safe_filename(name):
    """Convert a Blender datablock name into a portable filename stem."""
    import re
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip(' .')
    return safe or "bake"
