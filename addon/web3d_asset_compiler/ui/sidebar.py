"""
Unified 3D Viewport N-panel interface for Web3D Asset Compiler.
"""

import bpy
from bpy.types import Panel
from ..baking.properties import PASS_FILTER_TYPES
from ..presets.preset_data import PRESETS


def draw_unified_ui(layout, context):
    ahb = context.scene.ahb_props
    tjs = context.scene.tjs_props

    # ── 1. Status Strip & Primary Action ─────────────────────────────────
    status_box = layout.box()
    row = status_box.row()
    status_text = getattr(context.scene, "web3d_status", "Ready")
    icon = 'CHECKMARK' if ('✓' in status_text or 'complete' in status_text.lower()) else 'INFO'
    if 'fail' in status_text.lower() or 'error' in status_text.lower():
        icon = 'ERROR'
    row.label(text=f"  {status_text}", icon=icon)

    layout.separator()

    # Primary Action Button
    r = layout.row()
    r.scale_y = 1.8
    r.operator("web3d.build_web_asset", text="⚡ BUILD WEB ASSET", icon='PACKAGE')

    layout.separator()

    # ── 2. Preset Selector ───────────────────────────────────────────────
    preset_box = layout.box()
    preset_box.label(text="Optimization Presets", icon='PRESET')
    row = preset_box.row(align=True)
    row.operator_enum("web3d.apply_preset", "preset_key")

    layout.separator()

    # ── 3. Texture & Lightmap Baker Section ──────────────────────────────
    baker_box = layout.box()
    baker_box.label(text="Texture & Lightmap Baker", icon='RENDER_RESULT')

    # Quick bake & rebake
    row = baker_box.row(align=True)
    row.operator("ahb.quick_bake", text="⚡ Quick Bake", icon='RENDER_STILL')
    row.operator("ahb.rebake_selected", text="Rebake Active", icon='REC')

    # Scope & Type
    sub = baker_box.box()
    sub.label(text="Scope & Pass Type", icon='OBJECT_DATA')
    sub.prop(ahb, "bake_scope", text="Scope")
    sub.prop(ahb, "bake_type", text="Type")
    if ahb.bake_type in PASS_FILTER_TYPES:
        r = sub.row(align=True)
        r.prop(ahb, "use_pass_direct", toggle=True)
        r.prop(ahb, "use_pass_indirect", toggle=True)
        r.prop(ahb, "use_pass_color", toggle=True)

    # Image Settings
    sub = baker_box.box()
    sub.label(text="Image & Atlasing Mode", icon='IMAGE_DATA')
    sub.prop(ahb, "image_mode", text="Mode")
    if ahb.image_mode == 'AUTO_TILES':
        sub.prop(ahb, "tile_count")
    elif ahb.image_mode == 'COLLECTION_ATLASES':
        sub.prop(ahb, "collection_atlas_prefix", text="Prefix")
        sub.prop(ahb, "collection_atlas_count", text="Count")
        sub.operator("ahb.create_atlas_collections", text="Create Atlas Collections", icon='OUTLINER_COLLECTION')
    sub.prop(ahb, "resolution", text="Resolution")
    if ahb.resolution == 'CUSTOM':
        r = sub.row(align=True)
        r.prop(ahb, "custom_res_x", text="W")
        r.prop(ahb, "custom_res_y", text="H")
    sub.prop(ahb, "image_format", text="Format")
    if ahb.image_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
        sub.prop(ahb, "exr_codec", text="Codec")
    sub.prop(ahb, "color_mode", text="Channels")
    sub.prop(ahb, "use_hdr_float")

    # UV & Island Packing
    sub = baker_box.box()
    sub.label(text="UV Generation & Packing", icon='UV')
    sub.prop(ahb, "auto_create_uv")
    if ahb.auto_create_uv:
        sub.prop(ahb, "uv_layer_name", text="Layer Name")
        sub.prop(ahb, "uv_mode", text="Mode")
        if ahb.uv_mode == 'SMART':
            sub.prop(ahb, "smart_uv_angle", text="Angle Limit")
        elif ahb.uv_mode == 'AUTO_SEAM':
            sub.prop(ahb, "auto_seam_angle", text="Seam Angle")

    sub.separator()
    sub.prop(ahb, "pack_enabled")
    if ahb.pack_enabled:
        sub.prop(ahb, "pack_engine")
        sub.prop(ahb, "pack_world_scale")
        sub.prop(ahb, "pack_image_boost", text="Tex Boost")
        sub.prop(ahb, "pack_margin_px", text="Margin (px)")

    r = sub.row(align=True)
    r.operator("ahb.preview_uv", text="Preview UV Layout", icon='UV')
    r.operator("ahb.renew_auto_seams", text="Renew Auto Seams", icon='EDGE_SEAM')
    r.operator("ahb.pack_islands", text="Re-Pack Islands", icon='PACKAGE')

    # Cycles & Compute
    sub = baker_box.box()
    sub.label(text="Render Engine & Hardware", icon='MONITOR')
    sub.prop(ahb, "samples")
    sub.prop(ahb, "margin")
    sub.prop(ahb, "auto_switch_cycles")
    sub.prop(ahb, "compute_device", expand=True)
    if ahb.compute_device == 'GPU':
        sub.prop(ahb, "gpu_backend", text="Backend")
    status = sub.column()
    status.scale_y = 0.8
    status.label(text=f"Active: {ahb.device_status}", icon='INFO')

    # Material View & Toggle
    sub = baker_box.box()
    sub.label(text="Material Application", icon='NODE_MATERIAL')
    r = sub.row(align=True)
    r.operator("ahb.toggle_baked_view",
               text=("Show Original Materials" if ahb.baked_view else "Show Baked Textures"),
               icon=('LOOP_BACK' if ahb.baked_view else 'CHECKMARK'))
    sub.prop(ahb, "apply_mode", text="Mode")
    r = sub.row(align=True)
    r.operator("ahb.apply_baked", text="Apply to Shader", icon='CHECKMARK')
    r.operator("ahb.restore_materials", text="Restore Originals", icon='LOOP_BACK')

    layout.separator()

    # ── 4. Web & Animation Exporter Section ──────────────────────────────
    exporter_box = layout.box()
    exporter_box.label(text="Web Animation & Model Exporter", icon='EXPORT')

    # Quick export button
    r = exporter_box.row()
    r.scale_y = 1.3
    r.operator("tjs.export_animations", text="Export Character & Animations", icon='EXPORT')

    # Output Directory
    sub = exporter_box.box()
    sub.label(text="Output Target", icon='FILE_FOLDER')
    sub.prop(tjs, "output_dir")
    sub.prop(tjs, "base_name")

    # Geometry & Materials
    sub = exporter_box.box()
    sub.label(text="Geometry & Texture Compression", icon='MESH_DATA')
    sub.prop(tjs, "apply_modifiers")
    sub.prop(tjs, "include_materials")
    sub.prop(tjs, "include_textures")
    sub.prop(tjs, "image_format")
    if tjs.image_format != "NONE":
        sub.prop(tjs, "texture_quality")
    sub.prop(tjs, "include_morphs")

    # Mesh Compression (Draco)
    sub.prop(tjs, "compression")
    if tjs.compression == "DRACO":
        sub.prop(tjs, "draco_level")
        r = sub.row(align=True)
        r.prop(tjs, "draco_position")
        r.prop(tjs, "draco_normal")

    # Animation Optimization & Quantization
    sub = exporter_box.box()
    sub.label(text="Animation Optimization", icon='MOD_SIMPLIFY')
    sub.prop(tjs, "export_all_actions")
    r = sub.row(align=True)
    r.prop(tjs, "position_tolerance")
    r.prop(tjs, "rotation_tolerance")
    sub.prop(tjs, "keyframe_reduction")
    sub.prop(tjs, "remove_static_tracks")
    sub.prop(tjs, "enable_animation_quantization")
    if tjs.enable_animation_quantization:
        r = sub.row(align=True)
        r.prop(tjs, "quantize_quaternions")
        r.prop(tjs, "quantize_vectors")
        r.prop(tjs, "quantize_morphs")

    layout.separator()

    # ── 5. Cleanup & Utilities Section ───────────────────────────────────
    cleanup_box = layout.box()
    cleanup_box.label(text="Cleanup & Utilities", icon='TRASH')
    r = cleanup_box.row(align=True)
    r.operator("ahb.clear_bake_nodes", text="Remove Bake Nodes", icon='TRASH')
    r.operator("ahb.cleanup_images", text="Remove Bake Images", icon='IMAGE_DATA')
    r = cleanup_box.row(align=True)
    r.operator("ahb.remove_unused_materials", text="Remove Unused Slots", icon='MATERIAL')
    r.operator("ahb.rename_objects", text="Rename by Texture", icon='SORTALPHA')

    layout.separator()

    # ── 6. JSON Export/Import Section ─────────────────────────────────────
    json_box = layout.box()
    json_box.label(text="Export/Import Settings JSON", icon='FILE_SCRIPT')
    r = json_box.row(align=True)
    r.operator("ahb.export_materials_json", text="Export JSON", icon='EXPORT')
    r.operator("ahb.import_materials_json", text="Import JSON", icon='IMPORT')


class WEB3D_PT_Sidebar(Panel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'Web3D'
    bl_label       = 'Web3D Asset Compiler'
    bl_idname      = 'WEB3D_PT_Sidebar'

    def draw(self, context):
        draw_unified_ui(self.layout, context)
