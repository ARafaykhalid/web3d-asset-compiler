# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Auto HDR Baker v3.0 — Blender 5.1+
#  Fully automated HDR/EXR light-bake pipeline with material detection,
#  UV setup, node injection, batch baking, and auto-save.
#  v3.0: Atlas / Auto-Tiles / Per-Material image modes, multi-object UV packing.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bl_info = {
    "name": "Auto HDR Baker",
    "author": "Rocky",
    "version": (3, 1, 1),
    "blender": (5, 1, 0),
    "location": "N-Panel > HDR Baker  |  Properties > Render > Auto HDR Baker",
    "description": "Fully automated HDR/EXR light bake pipeline: material detection, UV setup, node injection, batch baking",
    "category": "Render",
}

import bpy
import os
import json
from bpy_extras.io_utils import ExportHelper, ImportHelper
import math
import traceback
import bmesh
import re
from mathutils import Vector
from bpy.props import (
    BoolProperty,
    IntProperty,
    FloatProperty,
    StringProperty,
    EnumProperty,
    CollectionProperty,
)
from bpy.types import Panel, Operator, PropertyGroup


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NODE_TAG = "AHB_BakeTarget"
IMAGE_UV_BACKUP_LAYER = "AHB_ImageUV_Backup"
IMAGE_UV_SOURCE_PROP = "ahb_image_uv_source"
IMAGE_UV_BACKUP_PROP = "ahb_image_uv_backup"

# Bake types that support pass filters (direct / indirect / color)
_PASS_FILTER_TYPES = {'COMBINED', 'DIFFUSE', 'GLOSSY', 'TRANSMISSION'}

# Valid colorspace candidates ordered by preference
_LINEAR_COLORSPACES = ['Linear Rec.709', 'Linear', 'scene_linear',
                        'Non-Color', 'Raw']
_DATA_COLORSPACES = ['Non-Color', 'Raw', 'Linear Rec.709', 'Linear']
_SRGB_COLORSPACES = ['sRGB', 'Filmic sRGB', 'AgX Base sRGB']

# Extension map
_EXT_MAP = {
    'OPEN_EXR': '.exr',
    'OPEN_EXR_MULTILAYER': '.exr',
    'HDR': '.hdr',
    'PNG': '.png',
    'JPEG': '.jpg',
    'TIFF': '.tiff',
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Properties
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AHB_Properties(PropertyGroup):

    # ── Scope ──────────────────────────────────────────────────────────────
    bake_scope: EnumProperty(
        name="Scope",
        items=[
            ('SELECTED', 'All Selected',  'Bake every selected mesh'),
            ('ACTIVE',   'Active Only',   'Bake only the active object'),
            ('VISIBLE',  'All Visible',   'Bake all visible meshes in scene'),
        ],
        default='SELECTED',
    )

    # ── Bake Type ──────────────────────────────────────────────────────────
    bake_type: EnumProperty(
        name="Bake Type",
        items=[
            ('COMBINED',     'Combined',           'All passes combined'),
            ('AO',           'Ambient Occlusion',  'AO pass'),
            ('DIFFUSE',      'Diffuse',            'Diffuse color + lighting'),
            ('GLOSSY',       'Glossy',             'Glossy reflections'),
            ('TRANSMISSION', 'Transmission',       'Transmission'),
            ('ROUGHNESS',    'Roughness',          'Roughness map'),
            ('NORMAL',       'Normal',             'Normal map'),
            ('EMIT',         'Emit',               'Emission'),
            ('ENVIRONMENT',  'Environment',        'Environment lighting'),
            ('SHADOW',       'Shadow',             'Shadow map'),
            ('UV',           'UV',                 'UV layout visualization'),
        ],
        default='COMBINED',
    )

    # ── Passes (for types that support them) ───────────────────────────────
    use_pass_direct:   BoolProperty(name="Direct",   default=True)
    use_pass_indirect: BoolProperty(name="Indirect", default=True)
    use_pass_color:    BoolProperty(name="Color",    default=True)

    # ── Resolution ─────────────────────────────────────────────────────────
    resolution: EnumProperty(
        name="Resolution",
        items=[
            ('512',    '512 × 512',  ''),
            ('1024',   '1K (1024)',  ''),
            ('2048',   '2K (2048)',  ''),
            ('4096',   '4K (4096)',  ''),
            ('8192',   '8K (8192)',  ''),
            ('CUSTOM', 'Custom',     'Set a custom resolution'),
        ],
        default='2048',
    )
    custom_res_x: IntProperty(name="Width",  default=2048, min=64, max=16384)
    custom_res_y: IntProperty(name="Height", default=2048, min=64, max=16384)

    # ── Image Format ───────────────────────────────────────────────────────
    image_format: EnumProperty(
        name="Format",
        items=[
            ('PNG',                 'PNG (LDR)',             'Standard 8/16-bit PNG — small files'),
            ('JPEG',                'JPEG',                 'Lossy, smallest files'),
            ('OPEN_EXR',            'EXR (HDR, 32-bit)',    'OpenEXR full-float HDR — large files'),
            ('OPEN_EXR_MULTILAYER', 'EXR Multi-Layer',      'Multi-layer OpenEXR'),
            ('HDR',                 'Radiance HDR (.hdr)',   'Radiance RGBE format'),
            ('TIFF',                'TIFF',                 'Tagged Image Format'),
        ],
        default='PNG',
    )
    exr_codec: EnumProperty(
        name="EXR Codec",
        items=[
            ('NONE',  'None (Uncompressed)', ''),
            ('ZIP',   'ZIP',                 ''),
            ('ZIPS',  'ZIPS',                ''),
            ('RLE',   'RLE',                 ''),
            ('PIZ',   'PIZ',                 ''),
            ('PXR24', 'PXR24 (lossy)',       ''),
            ('B44',   'B44',                 ''),
            ('B44A',  'B44A',                ''),
            ('DWAA',  'DWAA',                ''),
        ],
        default='ZIP',
    )
    color_mode: EnumProperty(
        name="Color Mode",
        items=[
            ('RGB',  'RGB',  'No alpha'),
            ('RGBA', 'RGBA', 'Include alpha'),
        ],
        default='RGB',
    )
    use_hdr_float: BoolProperty(
        name="32-bit Float (HDR)",
        default=False,
        description="True HDR float buffer — large files. Only needed for HDR lighting workflows",
    )

    # ── Image Mode (replaces per_material_image) ──────────────────────────
    image_mode: EnumProperty(
        name="Image Mode",
        items=[
            ('ATLAS',        'Single Atlas',   'All objects share one UV-packed image'),
            ('AUTO_TILES',   'Auto Tiles',     'Auto-split across multiple images for quality'),
            ('COLLECTION_ATLASES', 'Collection Atlases', 'One numbered atlas per collection (Texture_Pack_1, Texture_Pack_2, …)'),
            ('PER_MATERIAL', 'Per Material',   'One image per material name'),
        ],
        default='ATLAS',
        description="How bake images are organized",
    )
    collection_atlas_count: IntProperty(
        name="Atlas Count",
        default=4, min=1, max=64,
        description="Number of numbered collections to atlas (Prefix + 1 … Prefix + N)",
    )
    collection_atlas_prefix: StringProperty(
        name="Collection Prefix",
        default="Texture_Pack_",
        description="Numbered collection prefix, for example Texture_Pack_ creates Texture_Pack_1, Texture_Pack_2, …",
    )
    tile_count: IntProperty(
        name="Tile Count",
        default=4, min=2, max=16,
        description="Number of texture tiles for Auto Tiles mode",
    )

    # ── UV ─────────────────────────────────────────────────────────────────
    auto_create_uv: BoolProperty(name="Auto Create UV Layer", default=True)
    uv_layer_name:  StringProperty(name="UV Layer Name", default="BakeLightmap")
    remove_old_uvs: BoolProperty(
        name="Remove Old UV Maps",
        default=False,
        description="Remove all other UV layers after creating the bake UV layer"
    )
    uv_mode: EnumProperty(
        name="UV Mode",
        items=[
            ('SMART',     'Smart UV Project', '(RECOMMENDED) Angle-based automatic unwrap. Guarantees NO overlaps.'),
            ('AUTO_SEAM', 'Auto Seam Unwrap', 'Auto-marks seams at sharp edges, keeps smooth curves together.'),
            ('CUBE',      'Cube Project',     'Good for simple architecture, but can cause overlapping on complex connected geometry.'),
            ('UNWRAP',    'Standard Unwrap',  'Uses your existing seams. Will overlap if you have no seams!'),
            ('LIGHTMAP',  'Lightmap Pack',    'Every face gets its own island. Good density, but tiny faces for curves.'),
            ('EXISTING',  'Use Existing',     'Keep current UVs, just set active layer'),
        ],
        default='SMART',
        description="How to generate UVs for the bake layer",
    )
    smart_uv_angle:         FloatProperty(name="Angle Limit",   default=66.0, min=0, max=89)
    smart_uv_island_margin: FloatProperty(name="Island Margin", default=0.03, min=0.0, max=1.0)

    cube_size: FloatProperty(
        name="Cube Size",
        default=1.0, min=0.001,
        description="Size of the cube projection mapping"
    )

    renew_auto_seams_on_preview: BoolProperty(
        name="Renew Auto Seams on Preview",
        default=True,
        description="Clear old seams and recalculate fresh auto seams when previewing or setting up UVs",
    )
    auto_seam_angle: FloatProperty(
        name="Seam Angle",
        default=30.0, min=1.0, max=89.0,
        description="Edges sharper than this angle become seams. Lower = more seams (more islands). Higher = fewer seams (bigger islands)",
    )

    lightmap_quality: IntProperty(name="Quality", default=12, min=1, max=48,
                                        description="Quality of lightmap pack (higher = slower but better)")
    lightmap_margin:        FloatProperty(name="Lightmap Margin", default=0.1, min=0, max=1)

    # ── UV Island Packing (UVPackmaster-style) ─────────────────────────────
    pack_enabled: BoolProperty(
        name="Pack Islands After Unwrap",
        default=True,
        description="Run optimized island packing after UV unwrap for maximum density",
    )
    pack_engine: EnumProperty(
        name="Pack Engine",
        items=[
            ('BLENDER', 'Blender Native', 'Use built-in Blender packing'),
            ('UVP3',    'UVPackmaster 3', 'Use UVPackmaster 3 (Must be installed)'),
            ('UVP2',    'UVPackmaster 2', 'Use UVPackmaster 2 (Must be installed)'),
        ],
        default='BLENDER',
        description="Which algorithm to use for packing islands",
    )
    pack_rotate: BoolProperty(
        name="Allow Rotation",
        default=True,
        description="Allow islands to rotate during packing for better fit",
    )
    pack_rotation_step: EnumProperty(
        name="Rotation Step",
        items=[
            ('ANY',  'Any Angle',  'Fully free rotation for maximum density'),
            ('90',   '90°',        'Only 0°/90°/180°/270° rotations'),
            ('45',   '45°',        '45° step rotations'),
            ('NONE', 'No Rotation', 'Keep original orientation'),
        ],
        default='ANY',
        description="Constrain rotation angles during packing",
    )
    pack_margin_px: IntProperty(
        name="Pack Margin (px)",
        default=16, min=0, max=64,
        description="Pixel-precise margin between UV islands (calculated from texture resolution)",
    )
    pack_world_scale: BoolProperty(
        name="World-Space Proportional",
        default=True,
        description="Size UV islands proportional to their real 3D surface area. "
                    "A 5m wall gets 50× more UV pixels than a 10cm trim piece — "
                    "consistent texel density (pixels per meter) everywhere",
    )
    pack_image_boost: FloatProperty(
        name="Image Texture Boost",
        default=1.0, min=1.0, max=4.0,
        description="Extra UV space multiplier for materials with image textures. "
                    "Applied ON TOP of world-space sizing. 1.0 = no boost",
    )
    pack_iterations: IntProperty(
        name="Pack Quality Iterations",
        default=3, min=1, max=32,
        description="Number of packing attempts — higher = better density but slower",
    )
    pack_nest_holes: BoolProperty(
        name="Nest Inside Hollow Centers",
        default=False,
        description="Use concave packing for hollow shapes. Disabled by default because concave packing can overlap islands.",
    )
    pack_shape_method: EnumProperty(
        name="Shape Method",
        items=[
            ('CONVEX',  'Convex',   'Convex hull (tight packing, NO overlaps)'),
            ('CONCAVE', 'Concave',  'Accurate concave hull (tighter, but Blender can sometimes overlap)'),
            ('AABB',    'Bounding Box', 'Axis-aligned bounding box (fastest but wastes space)'),
        ],
        default='CONVEX',
        description="How island shapes are approximated during packing",
    )
    pack_scale_islands: BoolProperty(
        name="Scale Islands to Fit Gaps",
        default=False,
        description="Allow UV islands to scale dynamically. Disabled by default to preserve texel density and sharpness.",
    )
    pack_stack_identical: BoolProperty(
        name="Stack Identical Islands",
        default=False,
        description="Preserve intentional overlapping islands while packing. Disabled by default so separate faces receive separate baked pixels.",
    )

    # ── Node tag ───────────────────────────────────────────────────────────
    image_node_name: StringProperty(
        name="Node Tag",
        default=NODE_TAG,
        description="Prefix used to identify auto-inserted Image Texture nodes",
    )

    # ── Bake Quality ───────────────────────────────────────────────────────
    samples:  IntProperty(name="Samples",    default=128, min=1, max=4096)
    margin:   IntProperty(name="Margin (px)", default=16, min=0, max=64)
    margin_type: EnumProperty(
        name="Margin Type",
        items=[
            ('ADJACENT_FACES', 'Adjacent Faces', ''),
            ('EXTEND',         'Extend',         ''),
        ],
        default='ADJACENT_FACES',
    )
    clear_bake: BoolProperty(name="Clear Before Bake", default=True)
    unlink_materials: BoolProperty(
        name="Make Materials Unique",
        default=True,
        description="Make shared materials unique per-object before baking to prevent UV and bake overlapping issues"
    )

    # ── Engine / GPU ───────────────────────────────────────────────────────
    auto_switch_cycles: BoolProperty(name="Auto Switch to Cycles", default=True)
    compute_device: EnumProperty(
        name="Compute Device",
        items=[
            ('GPU', 'GPU', 'Bake with a supported graphics card'),
            ('CPU', 'CPU', 'Bake with the processor'),
        ],
        default='GPU',
        description="Choose whether Cycles baking uses the GPU or CPU",
    )
    gpu_backend: EnumProperty(
        name="GPU Backend",
        items=[
            ('AUTO',   'Auto',   'Use the configured backend, or try OptiX, CUDA, HIP, oneAPI, and Metal'),
            ('OPTIX',  'OptiX',  'NVIDIA RTX/GTX; recommended for an RTX 4060'),
            ('CUDA',   'CUDA',   'NVIDIA CUDA'),
            ('HIP',    'HIP',    'AMD GPU'),
            ('ONEAPI', 'oneAPI', 'Intel GPU'),
            ('METAL',  'Metal',  'Apple GPU'),
        ],
        default='AUTO',
        description="Cycles GPU compute backend",
    )
    device_status: StringProperty(
        name="Active Device",
        default="Configured when baking starts",
        description="Compute device used by the most recent bake",
    )

    # ── Selected-to-Active ─────────────────────────────────────────────────
    use_selected_to_active: BoolProperty(name="Selected to Active", default=False)
    cage_extrusion:         FloatProperty(name="Cage Extrusion",     default=0.02, min=0, max=1)
    max_ray_distance:       FloatProperty(name="Max Ray Distance",   default=0.0,  min=0, max=10)

    # ── Output ─────────────────────────────────────────────────────────────
    output_dir:    StringProperty(name="Output Directory", subtype='DIR_PATH', default="//baked/")
    output_prefix: StringProperty(name="Filename Prefix",  default="bake_")
    auto_save:     BoolProperty(name="Auto Save After Bake", default=True)

    # ── Cleanup ────────────────────────────────────────────────────────────
    auto_cleanup_nodes: BoolProperty(
        name="Auto Clean Bake Nodes",
        default=True,
        description="Remove injected bake-target nodes after baking completes",
    )
    baked_view: BoolProperty(
        name="Baked View",
        default=False,
        description="Tracks whether the scoped materials currently show baked textures",
    )

    # ── Apply Baked Textures ───────────────────────────────────────────────
    apply_mode: EnumProperty(
        name="Apply Mode",
        items=[
            ('AUTO',      'Auto (by Bake Type)', 'Emission for Combined/Diffuse, Base Color for others'),
            ('EMISSION',  'Emission',            'Use baked texture as emission (pre-lit, no re-lighting)'),
            ('BASE_COLOR','Base Color',           'Replace base color (allows re-lighting by scene)'),
        ],
        default='AUTO',
        description="How the baked texture connects into the shader",
    )
    apply_keep_original_nodes: BoolProperty(
        name="Keep Original Nodes",
        default=True,
        description="Preserve original shader nodes (muted) so you can restore later",
    )

    # ── Status (read-only display) ─────────────────────────────────────────
    status_text: StringProperty(default="Ready")

    # ── Auto Features ──────────────────────────────────────────────────────
    auto_rename_objects: BoolProperty(
        name="Auto-Rename Objects",
        default=False,
        description="After baking, automatically append the generated texture name to the object name"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers (crash-safe, no stale references)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _force_ui_redraw():
    """Force Blender to redraw the UI so the user can see progress during long operations.
    Without this, Blender freezes the entire UI until the script finishes."""
    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except Exception:
        pass


def _normalize_core_names(props):
    """Keep user-editable identifiers safe and non-empty."""
    if not props.output_prefix.strip():
        props.output_prefix = "bake_"
    if not props.image_node_name.strip():
        props.image_node_name = NODE_TAG
    if not props.uv_layer_name.strip():
        props.uv_layer_name = "BakeLightmap"
    if not props.collection_atlas_prefix.strip():
        props.collection_atlas_prefix = "Texture_Pack_"


def _bake_node_tag(tag):
    """Return a safe tag; an empty tag would match and remove every node."""
    tag = str(tag).strip() if tag is not None else ""
    return tag or NODE_TAG


def _safe_filename(name):
    """Convert a Blender datablock name into a portable filename stem."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip(' .')
    return safe or "bake"


def _prepare_mesh_data(obj_names, make_unique=False):
    """Make mesh data unique when objects need independent atlas UVs."""
    seen_meshes = set()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if make_unique and (mesh.users > 1 or mesh.as_pointer() in seen_meshes):
            obj.data = mesh.copy()
            mesh = obj.data
        seen_meshes.add(mesh.as_pointer())


def _prepare_material_slots(obj_names):
    """Ensure every material slot used by a face has a node material."""
    created = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            continue

        used_indices = {poly.material_index for poly in obj.data.polygons}
        if not obj.material_slots:
            mat = bpy.data.materials.new(f"AHB_Default_{obj.name}")
            mat.use_nodes = True
            obj.data.materials.append(mat)
            created += 1
            continue

        for slot_index in used_indices:
            if slot_index >= len(obj.material_slots):
                continue
            slot = obj.material_slots[slot_index]
            if not slot.material:
                mat = bpy.data.materials.new(
                    f"AHB_Default_{obj.name}_{slot_index + 1:02d}")
                mat.use_nodes = True
                slot.material = mat
                created += 1
            elif not slot.material.use_nodes:
                slot.material.use_nodes = True
    return created


def _get_render_uv_name(mesh, excluded_name=""):
    """Return the UV map used by existing implicit texture coordinates."""
    if isinstance(excluded_name, str):
        excluded_names = {excluded_name} if excluded_name else set()
    else:
        excluded_names = set(excluded_name)
    for uv_layer in mesh.uv_layers:
        if (uv_layer.name not in excluded_names
                and getattr(uv_layer, 'active_render', False)):
            return uv_layer.name
    active = mesh.uv_layers.active
    if active and active.name not in excluded_names:
        return active.name
    for uv_layer in mesh.uv_layers:
        if uv_layer.name not in excluded_names:
            return uv_layer.name
    return ""


def _copy_image_uv_backup(obj, source_name):
    """Copy an image material's source UVs to a stable per-mesh layer."""
    mesh = obj.data
    source = mesh.uv_layers.get(source_name)
    if not source:
        return ""

    backup_name = mesh.get(IMAGE_UV_BACKUP_PROP, "")
    backup = mesh.uv_layers.get(backup_name) if backup_name else None
    if not backup:
        backup = mesh.uv_layers.new(name=IMAGE_UV_BACKUP_LAYER)
        backup_name = backup.name

    if source != backup and len(source.data) == len(backup.data):
        for source_loop, backup_loop in zip(source.data, backup.data):
            backup_loop.uv = source_loop.uv

    obj[IMAGE_UV_SOURCE_PROP] = source_name
    obj[IMAGE_UV_BACKUP_PROP] = backup_name
    mesh[IMAGE_UV_BACKUP_PROP] = backup_name
    return backup.name


def _preserve_implicit_texture_uvs(obj_names, props):
    """Pin unconnected source Image Texture nodes to their original UV map.

    Image Texture nodes with an unconnected Vector socket implicitly use the
    render-active UV map. The bake pipeline changes that map to the lightmap,
    which otherwise makes original textures bake stretched or scrambled.
    """
    tag = _bake_node_tag(props.image_node_name)
    material_uv = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        backup_layer_name = mesh.get(
            IMAGE_UV_BACKUP_PROP,
            obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
        uv_name = _get_render_uv_name(
            mesh, {props.uv_layer_name, backup_layer_name})
        if not uv_name:
            remembered = obj.get(IMAGE_UV_SOURCE_PROP)
            if remembered in mesh.uv_layers:
                uv_name = remembered
            elif backup_layer_name in mesh.uv_layers:
                uv_name = backup_layer_name
        if not uv_name:
            continue

        image_materials = {
            slot.material.name for slot in obj.material_slots
            if slot.material
            and _has_real_image_textures(slot.material, tag)
        }
        backup_uv_name = ""
        if image_materials:
            backup_uv_name = _copy_image_uv_backup(obj, uv_name)

        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            pinned_uv_name = (backup_uv_name
                              if mat.name in image_materials
                              else uv_name)
            material_uv.setdefault(mat.name, pinned_uv_name)

    for mat_name, uv_name in material_uv.items():
        mat = bpy.data.materials.get(mat_name)
        if not mat or not mat.use_nodes:
            continue
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        uv_node_name = f"AHB_SourceUV_{uv_name}"
        uv_node = nodes.get(uv_node_name)

        def ensure_uv_node(anchor):
            nonlocal uv_node
            if not uv_node:
                uv_node = nodes.new('ShaderNodeUVMap')
                uv_node.name = uv_node_name
                uv_node.label = f"Preserved Source UV: {uv_name}"
                uv_node.uv_map = uv_name
                uv_node.location = (anchor.location.x - 220,
                                    anchor.location.y)
            return uv_node

        # Explicit Texture Coordinate UV links also follow active_render.
        # Reroute them through a named UV Map node before changing that layer.
        for node in list(nodes):
            if node.type != 'TEX_COORD' or not node.outputs.get('UV'):
                continue
            for link in list(node.outputs['UV'].links):
                target_socket = link.to_socket
                links.remove(link)
                links.new(ensure_uv_node(node).outputs['UV'], target_socket)

        for node in list(nodes):
            if (node.type != 'TEX_IMAGE' or node.name.startswith(tag)
                    or not node.inputs.get('Vector')
                    or node.inputs['Vector'].is_linked):
                continue
            links.new(ensure_uv_node(node).outputs['UV'], node.inputs['Vector'])


def _get_resolution(props):
    """Return (width, height) based on current resolution setting."""
    if props.resolution == 'CUSTOM':
        return props.custom_res_x, props.custom_res_y
    v = int(props.resolution)
    return v, v


def _get_collection_atlas_name(props, atlas_number):
    """Return the exact numbered collection/image stem for an atlas slot."""
    return f"{props.collection_atlas_prefix}{atlas_number}"


def _get_collection_atlas_image(props, atlas_number):
    """Get or create the image belonging to one numbered collection atlas."""
    return _get_or_create_image(props, _get_collection_atlas_name(props, atlas_number))


def _get_atlas_groups(obj_names, props, report=None):
    """Resolve numbered collection atlases within the current bake scope.
    Every requested atlas number is retained in the result, including missing
    or empty collections. Objects from child collections are included through
    Collection.all_objects. If an object belongs to more than one numbered
    collection, the lowest atlas number wins so atlas membership stays unique.
    """
    scope_names = set(obj_names)
    assigned = {}
    duplicate_memberships = []
    missing_collections = []
    groups = []

    for atlas_number in range(1, props.collection_atlas_count + 1):
        collection_name = _get_collection_atlas_name(props, atlas_number)
        collection = bpy.data.collections.get(collection_name)
        if not collection:
            missing_collections.append(collection_name)
            groups.append((atlas_number, []))
            continue

        candidates = sorted({
            obj.name for obj in collection.all_objects
            if obj.type == 'MESH' and obj.name in scope_names
        })
        group_names = []
        for obj_name in candidates:
            previous_number = assigned.get(obj_name)
            if previous_number is not None:
                duplicate_memberships.append(
                    (obj_name, previous_number, atlas_number))
                continue
            assigned[obj_name] = atlas_number
            group_names.append(obj_name)
        groups.append((atlas_number, group_names))

    if report and missing_collections:
        preview = ", ".join(missing_collections[:4])
        if len(missing_collections) > 4:
            preview += f", +{len(missing_collections) - 4} more"
        report({'WARNING'}, f"Missing collection(s): {preview}")

    if report and duplicate_memberships:
        preview = ", ".join(
            f"{name} ({first}/{duplicate})"
            for name, first, duplicate in duplicate_memberships[:4])
        if len(duplicate_memberships) > 4:
            preview += f", +{len(duplicate_memberships) - 4} more"
        report({'WARNING'},
               f"Objects in multiple atlas collections; lowest number used: {preview}")

    unassigned = sorted(scope_names.difference(assigned))
    if report and unassigned:
        preview = ", ".join(unassigned[:4])
        if len(unassigned) > 4:
            preview += f", +{len(unassigned) - 4} more"
        report({'WARNING'}, f"Objects not assigned to an atlas collection: {preview}")

    return groups


def _flatten_atlas_groups(groups):
    """Return a flat list of object names across all collection atlas groups."""
    result = []
    for _atlas_num, obj_names in groups:
        result.extend(obj_names)
    return result


def _isolate_materials_between_atlas_groups(groups):
    """Duplicate materials shared across different collection atlas groups so that
    materials targeted by one atlas don't conflict with another atlas."""
    duplicated = 0
    mat_atlas_map = {}
    for atlas_number, obj_names in groups:
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj: continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat: continue
                prev = mat_atlas_map.get(mat.name)
                if prev is not None and prev != atlas_number:
                    new_mat = mat.copy()
                    new_mat.name = f"{mat.name}_atlas{atlas_number:02d}"
                    slot.material = new_mat
                    mat = new_mat
                    duplicated += 1
                mat_atlas_map[mat.name] = atlas_number
    return duplicated


def _get_object_names(props, context):
    """Return a list of object *names* matching the current scope.
    Sorted alphabetically so that distribution into tiles is 100% deterministic."""
    if props.bake_scope == 'ACTIVE':
        obj = context.active_object
        return [obj.name] if (obj and obj.type == 'MESH' and obj.data.polygons) else []
    if props.bake_scope == 'SELECTED':
        return sorted([o.name for o in context.selected_objects
                       if o.type == 'MESH' and o.data.polygons])
    # VISIBLE
    return sorted([o.name for o in context.scene.objects
            if o.type == 'MESH' and o.data.polygons and not o.hide_viewport
            and not o.hide_get(view_layer=context.view_layer)])


def _resolve_pipeline_targets(context, props, obj_names, report):
    """Return bake targets and preserved sources for Selected-to-Active."""
    if not props.use_selected_to_active:
        return obj_names, None

    active = context.view_layer.objects.active
    if not active or active.type != 'MESH' or not active.data.polygons:
        report({'ERROR'}, "Selected to Active needs an active mesh target.")
        return [], None
    source_names = sorted(
        obj.name for obj in context.selected_objects
        if obj.type == 'MESH' and obj.data.polygons and obj != active)
    if not source_names:
        report({'ERROR'},
               "Selected to Active needs at least one other selected source mesh.")
        return [], None
    return [active.name], source_names


def _collect_material_names(obj_names):
    """Return {material_name: [obj_name, …]} for all objects by name."""
    mat_map = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            if not mat.use_nodes:
                mat.use_nodes = True
            mat_map.setdefault(mat.name, []).append(obj_name)
    return mat_map


def _safe_set_colorspace(image, is_hdr, is_data=False):
    """Try setting an appropriate colorspace without crashing on missing names."""
    if is_data:
        candidates = _DATA_COLORSPACES
    else:
        candidates = _LINEAR_COLORSPACES if is_hdr else _SRGB_COLORSPACES
    for cs in candidates:
        try:
            image.colorspace_settings.name = cs
            return
        except (TypeError, RuntimeError):
            continue


def _get_or_create_image(props, name):
    """Get or create a bake-target image with the correct resolution and format."""
    _normalize_core_names(props)
    rx, ry = _get_resolution(props)
    # EXR/HDR targets must use a float buffer even when the output EXR is
    # half-float; otherwise lighting values are clamped before saving.
    is_hdr = props.image_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR')
    is_data = props.bake_type in ('AO', 'NORMAL', 'ROUGHNESS', 'SHADOW', 'UV')
    img_name = f"{props.output_prefix}{name}"

    img = bpy.data.images.get(img_name)
    if img:
        if img.size[0] != rx or img.size[1] != ry:
            # Scaling an existing bake target interpolates old pixels and is a
            # common source of blur on repeated quick bakes. Recreate it at
            # the requested resolution instead.
            bpy.data.images.remove(img)
            img = None
        # Ensure float buffer matches
        if img and img.is_float != is_hdr:
            bpy.data.images.remove(img)
            img = None

    if not img:
        img = bpy.data.images.new(
            img_name,
            width=rx,
            height=ry,
            float_buffer=is_hdr,
            alpha=(props.color_mode == 'RGBA'),
        )

    _safe_set_colorspace(img, is_hdr, is_data)
    return img


def _get_tile_image(props, tile_idx):
    """Get or create the image for a specific tile index (0-based).
    Tile names are like 'tile_01', 'tile_02', etc."""
    tile_name = f"tile_{tile_idx + 1:02d}"
    return _get_or_create_image(props, tile_name)


def _activate_bake_uv(obj_name, props):
    """Ensure the bake UV layer is the active + active_render layer on the mesh.
    Called right before baking to guarantee the baker uses the correct UVs."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    mesh = obj.data
    layer_name = props.uv_layer_name
    if layer_name in mesh.uv_layers:
        uv_layer = mesh.uv_layers[layer_name]
        mesh.uv_layers.active = uv_layer
        try:
            mesh.uv_layers.active_render = uv_layer
        except (AttributeError, RuntimeError):
            pass


def _pixel_margin_to_uv(px, props):
    """Convert pixel margin to UV-space margin based on current texture resolution."""
    rx, ry = _get_resolution(props)
    return px / min(rx, ry)


def _has_real_image_textures(mat, bake_tag):
    """Check if a material uses actual image textures (photos, maps)
    rather than procedural/solid colors. Ignores our bake-target nodes."""
    bake_tag = _bake_node_tag(bake_tag)
    if not mat or not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            # Skip our own injected bake target nodes
            if node.name.startswith(bake_tag):
                continue
            # Skip placeholder images with no real data
            if node.image.size[0] == 0 or node.image.size[1] == 0:
                continue
            return True
    return False


def _apply_image_boost(obj_name, props):
    """Scale UV islands of materials with image textures UP by the boost factor.
    Materials without image textures are left untouched.

    This runs AFTER average_islands_scale() has already established proportional
    sizing — we only add a mild boost for materials that need extra detail.

    IMPORTANT: Manages its OWN edit-mode session.
    The object MUST be in OBJECT mode when this is called."""
    boost = props.pack_image_boost
    if boost <= 1.01:
        return  # No boost configured

    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    if not obj.material_slots:
        return
    if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
        return

    bake_tag   = props.image_node_name
    layer_name = props.uv_layer_name

    # Classify materials — find which have real image textures
    image_mat_indices = set()
    for idx, slot in enumerate(obj.material_slots):
        if slot.material and _has_real_image_textures(slot.material, bake_tag):
            image_mat_indices.add(idx)

    if not image_mat_indices:
        return  # No image materials to boost

    # Enter edit mode
    _force_object_mode()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    if layer_name in obj.data.uv_layers:
        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

    try:
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if not uv_layer or len(bm.faces) == 0:
            return

        # sqrt(boost) because UV dimensions scale quadratically to area
        scale_factor = math.sqrt(boost)

        # Only scale UP islands belonging to image-textured materials
        for mat_idx in image_mat_indices:
            faces = [f for f in bm.faces if f.material_index == mat_idx]
            if not faces:
                continue

            # Collect all loops and compute centroid
            all_loops = []
            centroid = Vector((0.0, 0.0))
            count = 0
            for face in faces:
                for loop in face.loops:
                    all_loops.append(loop)
                    centroid += loop[uv_layer].uv
                    count += 1
            if count == 0:
                continue
            centroid /= count

            # Scale from centroid
            for loop in all_loops:
                uv = loop[uv_layer].uv
                loop[uv_layer].uv = centroid + (uv - centroid) * scale_factor

        bmesh.update_edit_mesh(obj.data)

    finally:
        _force_object_mode()


def _world_surface_area(obj):
    """Return mesh surface area after applying the object's world transform."""
    if not obj or obj.type != 'MESH':
        return 0.0
    transform = obj.matrix_world.to_3x3()
    verts = obj.data.vertices
    obj.data.calc_loop_triangles()
    area = 0.0
    for tri in obj.data.loop_triangles:
        origin = transform @ verts[tri.vertices[0]].co
        a = transform @ verts[tri.vertices[1]].co - origin
        b = transform @ verts[tri.vertices[2]].co - origin
        area += a.cross(b).length * 0.5
    return area


def _apply_world_area_scale(obj_name, layer_name):
    """Correct UV scale for unapplied object transforms.

    Blender's multi-object average-islands-scale works in mesh-local space.
    Scaling UVs by sqrt(world area / local area) restores consistent pixels per
    metre when objects have different or unapplied scale values.
    """
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
        return
    local_area = sum(poly.area for poly in obj.data.polygons)
    world_area = _world_surface_area(obj)
    if local_area <= 1e-12 or world_area <= 1e-12:
        return
    factor = math.sqrt(world_area / local_area)
    if abs(factor - 1.0) < 1e-6:
        return

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.get(layer_name)
    if not uv_layer:
        bm.free()
        return
    loops = [loop for face in bm.faces for loop in face.loops]
    if not loops:
        bm.free()
        return
    centroid = Vector((0.0, 0.0))
    for loop in loops:
        centroid += loop[uv_layer].uv
    centroid /= len(loops)
    for loop in loops:
        uv = loop[uv_layer].uv
        loop[uv_layer].uv = centroid + (uv - centroid) * factor
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


# ── Low-level mode helpers ────────────────────────────────────────────────

def _force_object_mode():
    """Safely return to OBJECT mode regardless of current state."""
    try:
        if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def _enter_edit_select_all(obj, layer_name):
    """Enter EDIT mode on *obj*, select all geometry, and activate the UV layer.
    The caller MUST call _force_object_mode() when done."""
    _force_object_mode()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if layer_name in obj.data.uv_layers:
        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]


def _subdivide_large_uv_islands(obj_name, layer_name, max_island_ratio=0.15):
    """Split large UV islands by marking extra seams on long interior edges.

    After auto-seam + unwrap, some islands may be huge concave shapes (like
    an L-shaped room wall) that waste packing space. This function:
    1. Identifies UV islands
    2. For any island whose bounding-box covers > max_island_ratio of UV space,
       marks seams on the longest non-boundary edges to split it
    3. The caller must re-unwrap after this

    Works in object mode using bmesh.
    """
    import bmesh

    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return False
    mesh = obj.data
    if layer_name not in mesh.uv_layers:
        return False

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.get(layer_name)
    if not uv_layer:
        bm.free()
        return False

    # Build UV islands: group faces by connected UV coordinates
    face_island = {}  # face.index -> island_id
    island_id = 0
    visited = set()

    for face in bm.faces:
        if face.index in visited:
            continue
        # BFS to find all faces connected via shared UV edges
        stack = [face]
        island_faces = []
        while stack:
            f = stack.pop()
            if f.index in visited:
                continue
            visited.add(f.index)
            face_island[f.index] = island_id
            island_faces.append(f)
            for edge in f.edges:
                if edge.seam:
                    continue  # Seams break island connectivity
                for linked_face in edge.link_faces:
                    if linked_face.index not in visited:
                        stack.append(linked_face)
        island_id += 1

    # For each island, compute UV bounding box area
    island_uv_bounds = {}  # island_id -> (min_x, min_y, max_x, max_y)
    island_faces_map = {}  # island_id -> [face indices]

    for face in bm.faces:
        iid = face_island.get(face.index)
        if iid is None:
            continue
        if iid not in island_faces_map:
            island_faces_map[iid] = []
        island_faces_map[iid].append(face.index)

        for loop in face.loops:
            uv = loop[uv_layer].uv
            if iid not in island_uv_bounds:
                island_uv_bounds[iid] = [uv.x, uv.y, uv.x, uv.y]
            else:
                b = island_uv_bounds[iid]
                b[0] = min(b[0], uv.x)
                b[1] = min(b[1], uv.y)
                b[2] = max(b[2], uv.x)
                b[3] = max(b[3], uv.y)

    # Find islands that are too large OR have too much wasted empty space (rings/L-shapes)
    large_islands = set()
    for iid, bounds in island_uv_bounds.items():
        bb_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])

        # Calculate actual UV polygon area (shoelace formula)
        actual_area = 0.0
        for face_idx in island_faces_map.get(iid, []):
            f = bm.faces[face_idx]
            pts = [l[uv_layer].uv for l in f.loops]
            area = 0.0
            n = len(pts)
            for i in range(n):
                j = (i + 1) % n
                area += pts[i].x * pts[j].y - pts[j].x * pts[i].y
            actual_area += abs(area) * 0.5

        # Split if it's huge, OR if it's >2% of UV space and wastes >40% of its bounding box (e.g. rings)
        if bb_area > max_island_ratio or (bb_area > 0.02 and actual_area / max(bb_area, 0.000001) < 0.6):
            large_islands.add(iid)

    if not large_islands:
        bm.free()
        return False

    # For large islands, mark seams on the longest interior edges
    marked_any = False
    for iid in large_islands:
        face_indices = set(island_faces_map.get(iid, []))
        if len(face_indices) < 4:
            continue  # Too few faces to split

        # Collect interior edges (both connected faces are in this island)
        interior_edges = []
        for face in bm.faces:
            if face.index not in face_indices:
                continue
            for edge in face.edges:
                if edge.seam:
                    continue
                linked_in_island = sum(
                    1 for lf in edge.link_faces if lf.index in face_indices
                )
                if linked_in_island == 2:
                    interior_edges.append(edge)

        # Deduplicate
        seen_edges = set()
        unique_edges = []
        for e in interior_edges:
            if e.index not in seen_edges:
                seen_edges.add(e.index)
                unique_edges.append(e)

        if not unique_edges:
            continue

        # Sort by edge length (longest first) and mark seams on ~30% of them
        unique_edges.sort(key=lambda e: e.calc_length(), reverse=True)
        num_to_mark = max(1, len(unique_edges) // 3)

        for edge in unique_edges[:num_to_mark]:
            edge.seam = True
            marked_any = True

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return marked_any



# ── Stack Similar UV Islands ──────────────────────────────────────────────

def _stack_similar_islands(obj_names, layer_name, area_tol=0.01, perim_tol=0.01):
    """Detect identical UV islands (e.g., duplicated geometry, cylinder caps)
    and perfectly overlap their UVs so they share the exact same texture space.
    Uses 3D surface area and perimeter to match identical topology groups.
    """
    import bmesh
    from collections import defaultdict

    stacked_total = 0

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if layer_name not in mesh.uv_layers:
            continue

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.get(layer_name)
        if not uv_layer:
            bm.free()
            continue

        # 1. Identify Islands
        face_island = {}
        island_faces = defaultdict(list)
        island_id = 0
        visited = set()

        for face in bm.faces:
            if face.index in visited:
                continue
            stack = [face]
            while stack:
                f = stack.pop()
                if f.index in visited:
                    continue
                visited.add(f.index)
                face_island[f.index] = island_id
                island_faces[island_id].append(f)
                for edge in f.edges:
                    if not edge.seam:
                        for lf in edge.link_faces:
                            if lf.index not in visited:
                                stack.append(lf)
            island_id += 1

        # 2. Compute metrics for each island
        island_metrics = {}
        for iid, faces in island_faces.items():
            area_3d = sum(f.calc_area() for f in faces)
            perim_3d = sum(f.calc_perimeter() for f in faces)

            # Calculate UV bounding box center
            min_x = min_y = float('inf')
            max_x = max_y = float('-inf')
            for f in faces:
                for l in f.loops:
                    uv = l[uv_layer].uv
                    min_x = min(min_x, uv.x)
                    max_x = max(max_x, uv.x)
                    min_y = min(min_y, uv.y)
                    max_y = max(max_y, uv.y)

            center_x = (min_x + max_x) * 0.5
            center_y = (min_y + max_y) * 0.5

            island_metrics[iid] = {
                'area': area_3d,
                'perim': perim_3d,
                'center_x': center_x,
                'center_y': center_y,
                'faces': faces
            }

        # 3. Group and Stack
        groups = []
        for iid, data in island_metrics.items():
            placed = False
            for g in groups:
                master = g[0]
                # Match by 3D area, perimeter, and exact face count
                if (abs(data['area'] - master['area']) <= area_tol * master['area'] and
                    abs(data['perim'] - master['perim']) <= perim_tol * master['perim'] and
                    len(data['faces']) == len(master['faces'])):
                    g.append(data)
                    placed = True
                    break
            if not placed:
                groups.append([data])

        for g in groups:
            if len(g) > 1:
                master = g[0]
                for target in g[1:]:
                    dx = master['center_x'] - target['center_x']
                    dy = master['center_y'] - target['center_y']
                    # Translate target UVs to perfectly overlap the master
                    for f in target['faces']:
                        for l in f.loops:
                            l[uv_layer].uv.x += dx
                            l[uv_layer].uv.y += dy
                    stacked_total += 1

        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

    if stacked_total > 0:
        print(f"Auto HDR Baker: Stacked {stacked_total} identical UV islands.")

# ── Scale UVs to fill 0-1 space ──────────────────────────────────────────

def _scale_uvs_to_bounds(obj_names, layer_name, margin=0.005):
    """Scale all UV islands uniformly so they fill the 0→1 space maximally.
    Uses bmesh to find the bounding box of all packed UVs, then scales
    and centers them. This eliminates the wasted black space that
    Blender's native packer often leaves behind.

    Args:
        obj_names: list of object name strings
        layer_name: UV layer name to process
        margin: small margin from 0-1 edges to prevent bleeding
    """
    import bmesh

    # Step 1: Collect the bounding box of all UVs across all objects
    uv_min_x = uv_min_y = float('inf')
    uv_max_x = uv_max_y = float('-inf')
    has_uvs = False

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if layer_name not in mesh.uv_layers:
            continue

        bm = bmesh.new()
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.get(layer_name)
        if not uv_layer:
            bm.free()
            continue

        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                uv_min_x = min(uv_min_x, uv.x)
                uv_min_y = min(uv_min_y, uv.y)
                uv_max_x = max(uv_max_x, uv.x)
                uv_max_y = max(uv_max_y, uv.y)
                has_uvs = True
        bm.free()

    if not has_uvs:
        return

    # Step 2: Calculate the uniform scale factor
    uv_width  = uv_max_x - uv_min_x
    uv_height = uv_max_y - uv_min_y

    if uv_width < 1e-6 or uv_height < 1e-6:
        return  # Degenerate UVs

    target = 1.0 - 2.0 * margin  # available space inside margins
    scale = min(target / uv_width, target / uv_height)

    if abs(scale - 1.0) < 0.01:
        return  # Already filling the space well enough

    # Step 3: Scale and center all UVs
    center_x = (uv_min_x + uv_max_x) * 0.5
    center_y = (uv_min_y + uv_max_y) * 0.5

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if layer_name not in mesh.uv_layers:
            continue

        bm = bmesh.new()
        bm.from_mesh(mesh)
        uv_layer = bm.loops.layers.uv.get(layer_name)
        if not uv_layer:
            bm.free()
            continue

        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                # Scale from center of bounding box
                uv.x = (uv.x - center_x) * scale + 0.5
                uv.y = (uv.y - center_y) * scale + 0.5

        bm.to_mesh(mesh)
        bm.free()
        mesh.update()


# ── Phased packing pipeline (per-object, for PER_MATERIAL mode) ──────────

def _run_uv_normalize(obj_name, props):
    """Phase 1: Normalize island scale via Blender's built-in operator.
    This makes UV area proportional to 3D surface area (consistent texel density).
    Own edit-mode session — enter and exit cleanly."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    layer_name = props.uv_layer_name
    if layer_name not in obj.data.uv_layers:
        return

    _enter_edit_select_all(obj, layer_name)
    try:
        bpy.ops.uv.average_islands_scale()
    except Exception:
        pass
    finally:
        _force_object_mode()
    _apply_world_area_scale(obj_name, layer_name)


def _run_uv_pack(obj_name, props):
    """Phase 3: Pack UV islands via Blender's built-in operator.
    Own edit-mode session."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    layer_name = props.uv_layer_name
    if layer_name not in obj.data.uv_layers:
        return

    uv_margin = _pixel_margin_to_uv(props.pack_margin_px, props)
    rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'
    shape_method = props.pack_shape_method

    _enter_edit_select_all(obj, layer_name)
    try:
        # ── Third Party Packers ──
        if props.pack_engine == 'UVP3' and hasattr(bpy.ops, 'uvpackmaster3'):
            try:
                bpy.context.scene.uvp3_props.margin = uv_margin
                bpy.ops.uvpackmaster3.pack()
                return
            except Exception:
                pass  # Fallback to Blender if fails

        elif props.pack_engine == 'UVP2' and hasattr(bpy.ops, 'uvpackmaster2'):
            try:
                bpy.context.scene.uvp2_props.margin = uv_margin
                bpy.ops.uvpackmaster2.uv_pack()
                return
            except Exception:
                pass  # Fallback to Blender if fails

        # ── Blender Native Packer ──
        for _i in range(props.pack_iterations):
            _run_blender_pack(props, uv_margin)

        # Handle specific rotation steps
        if rotate and props.pack_rotation_step in ('90', '45'):
            try:
                bpy.ops.uv.pack_islands(
                    margin=uv_margin,
                    rotate=True,
                    rotate_method='AXIS_ALIGNED' if props.pack_rotation_step == '90' else 'ANY',
                )
            except (TypeError, RuntimeError):
                pass
    finally:
        _force_object_mode()


def _run_blender_pack(props, uv_margin):
    """Execute conservative, non-overlapping UV island packing."""
    rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'
    rot_method = 'ANY' if props.pack_rotation_step == 'ANY' else 'AXIS_ALIGNED'
    # Concave hulls and overlap merging can produce valid-looking but
    # overlapping UVs, which causes blurred/mixed bake pixels. Uniform packing
    # scale remains enabled so every island stays inside the 0-1 image bounds.
    shape_method = 'CONVEX' if props.pack_shape_method == 'CONCAVE' else props.pack_shape_method
    scale = True

    try:
        bpy.ops.uv.pack_islands(
            margin_method='FRACTION',
            margin=uv_margin,
            rotate=rotate,
            rotate_method=rot_method,
            scale=scale,
            shape_method=shape_method,
            merge_overlap=False
        )
        return
    except Exception:
        pass

    try:
        bpy.ops.uv.pack_islands(
            margin=uv_margin,
            rotate=rotate,
            scale=scale,
            shape_method=shape_method,
            merge_overlap=False
        )
        return
    except Exception:
        pass

    try:
        bpy.ops.uv.pack_islands(margin=uv_margin, rotate=rotate)
    except Exception:
        pass


def _pack_uv_islands_phased(obj_name, props):
    """Run the full packing pipeline as separate edit-mode phases.
    Each phase enters and exits edit mode independently so Blender
    can flush its dependency graph between operations.

    Phase 1: average_islands_scale  (Blender op — proportional to 3D area)
    Phase 2: _apply_image_boost     (bmesh — boost image-textured materials only)
    Phase 3: pack_islands           (Blender op — pack tightly)

    The object MUST be in OBJECT mode when this is called."""

    # Phase 1 — Normalize (makes UV area ∝ 3D surface area)
    if props.pack_world_scale:
        _run_uv_normalize(obj_name, props)

    # Phase 2 — Boost image-textured materials (only scales UP, doesn't shrink others)
    if props.pack_image_boost > 1.01:
        _apply_image_boost(obj_name, props)

    # Phase 3 — Pack
    _run_uv_pack(obj_name, props)

    # Do not rescale after packing. Blender's packer already fits islands to
    # the requested margin; a second global scale can change texel density and
    # make baked details appear soft or distorted.


def _remove_old_uvs(obj_names, keep_layer_name):
    """Remove unused UV layers while retaining maps referenced by materials."""
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj and obj.type == 'MESH':
            mesh = obj.data
            protected = {keep_layer_name}
            backup_name = mesh.get(
                IMAGE_UV_BACKUP_PROP,
                obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
            if backup_name in mesh.uv_layers:
                protected.add(backup_name)
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or not mat.use_nodes:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type == 'UVMAP' and node.uv_map:
                        protected.add(node.uv_map)
            to_remove = [uv.name for uv in mesh.uv_layers
                         if uv.name not in protected]
            for name in to_remove:
                mesh.uv_layers.remove(mesh.uv_layers[name])


def _ensure_uv(obj_name, props):
    """Create (or activate) the bake UV layer on an object by name.
    Re-fetches the object each time to avoid stale references.

    UV operations are split into separate edit-mode phases to prevent
    Blender segfaults from mixing bmesh + UV operators in one session.

    IMPORTANT: smart_project() expects angle_limit in RADIANS."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
        return

    mesh = obj.data
    layer_name = props.uv_layer_name

    # Create the layer if it doesn't exist
    if layer_name not in mesh.uv_layers:
        mesh.uv_layers.new(name=layer_name)

    uv_layer = mesh.uv_layers[layer_name]
    mesh.uv_layers.active = uv_layer

    if props.uv_mode == 'EXISTING':
        if props.pack_enabled:
            _pack_uv_islands_phased(obj_name, props)
        return

    # ── Phase A: Unwrap ────────────────────────────────────────────────
    _force_object_mode()
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        mesh.uv_layers.active = mesh.uv_layers[layer_name]

        if props.uv_mode == 'SMART':
            angle_rad = math.radians(props.smart_uv_angle)
            bpy.ops.uv.smart_project(
                angle_limit=angle_rad,
                island_margin=props.smart_uv_island_margin,
            )
        elif props.uv_mode == 'CUBE':
            bpy.ops.uv.cube_project(cube_size=props.cube_size)
            # Cube project creates overlapping islands — must normalize + repack
            try:
                bpy.ops.uv.average_islands_scale()
            except Exception:
                pass
            try:
                uv_margin = _pixel_margin_to_uv(props.pack_margin_px, props)
                try:
                    bpy.ops.uv.pack_islands(margin=uv_margin, rotate=True, shape_method='AABB')
                except TypeError:
                    bpy.ops.uv.pack_islands(margin=uv_margin, rotate=True)
            except Exception:
                pass
        elif props.uv_mode == 'UNWRAP':
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=props.smart_uv_island_margin)
        elif props.uv_mode == 'AUTO_SEAM':
            # Pass 1: Smart Project to perfectly cut overlaps (like cylinders)
            seam_rad = math.radians(props.auto_seam_angle)
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
            # Pass 2: Convert those perfect cuts into actual mesh seams
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
            # Pass 3: Relax the geometry using Angle-Based unwrap
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            # Pass 4: Split large concave islands or rings for tighter packing
            _force_object_mode()
            did_split = _subdivide_large_uv_islands(obj_name, layer_name)
            if did_split:
                obj = bpy.data.objects.get(obj_name)
                if obj:
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='SELECT')
                    mesh = obj.data
                    mesh.uv_layers.active = mesh.uv_layers[layer_name]
                    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
                    _force_object_mode()
            # Re-enter edit mode for the finally block
            obj = bpy.data.objects.get(obj_name)
            if obj:
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
        elif props.uv_mode == 'LIGHTMAP':
            try:
                bpy.ops.uv.lightmap_pack(
                    PREF_CONTEXT='ALL_FACES',
                    PREF_PACK_IN_ONE=True,
                    PREF_NEW_UVLAYER=False,
                    PREF_BOX_DIV=props.lightmap_quality,
                    PREF_MARGIN_DIV=props.lightmap_margin,
                )
            except Exception:
                angle_rad = math.radians(66.0)
                bpy.ops.uv.smart_project(
                    angle_limit=angle_rad,
                    island_margin=0.03,
                )
    finally:
        _force_object_mode()

    # ── Phase B: Pack (separate edit-mode sessions) ────────────────────
    if props.pack_enabled:
        _pack_uv_islands_phased(obj_name, props)

    if props.remove_old_uvs:
        _remove_old_uvs([obj_name], props.uv_layer_name)


def _offset_tile_uvs(obj_names, tile_idx, layer_name):
    """Shift all UV coordinates for the given objects by (tile_idx, 0).
    Creates UDIM-style tile layout for visualization:
      Tile 0 → (0,0)-(1,1), Tile 1 → (1,0)-(2,1), etc.
    Uses bmesh per-object for safety. Only used in PreviewUV."""
    if tile_idx == 0:
        return  # Tile 0 stays at origin, no offset needed

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue
        if layer_name not in obj.data.uv_layers:
            continue

        _force_object_mode()
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer:
                offset_x = float(tile_idx)
                for face in bm.faces:
                    for loop in face.loops:
                        loop[uv_layer].uv.x += offset_x
                bmesh.update_edit_mesh(obj.data)
        finally:
            _force_object_mode()


def _remove_tile_uv_offsets(obj_names, layer_name):
    """Remove any UDIM-style tile offsets — move all UVs back into 0-1 range.
    Called before baking to ensure UVs are in the correct space."""
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue
        if layer_name not in obj.data.uv_layers:
            continue

        _force_object_mode()
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer:
                loops = [loop for face in bm.faces for loop in face.loops]
                if loops:
                    # Preview moves an entire object's UVs by one common
                    # integer tile. Subtract that common offset only. Applying
                    # modulo per coordinate would incorrectly turn valid
                    # boundary coordinates such as 1.0 into 0.0.
                    min_x = min(loop[uv_layer].uv.x for loop in loops)
                    tile_offset = math.floor(min_x + 1e-6)
                    if tile_offset > 0:
                        for loop in loops:
                            loop[uv_layer].uv.x -= tile_offset
                        bmesh.update_edit_mesh(obj.data)
        finally:
            _force_object_mode()


# ── Multi-object atlas UV packing ─────────────────────────────────────────

def _setup_atlas_uvs(obj_names, props):
    """UV-unwrap and pack multiple objects together into one non-overlapping UV atlas.
    Uses Blender's multi-object edit mode so pack_islands works across all objects."""
    layer_name = props.uv_layer_name

    # Phase 0: Prepare all objects
    _force_object_mode()

    valid_objs = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue

        # Create UV layer if needed
        mesh = obj.data
        if layer_name not in mesh.uv_layers:
            mesh.uv_layers.new(name=layer_name)
        uv = mesh.uv_layers[layer_name]
        mesh.uv_layers.active = uv

        valid_objs.append(obj_name)

    if not valid_objs:
        return

    if props.uv_mode == 'EXISTING':
        # Just pack existing UVs together
        if props.pack_enabled:
            _atlas_pack_phase(valid_objs, props)
        return

    # Phase A: Unwrap all objects together
    _force_object_mode()

    # Deselect all, then select our objects
    bpy.ops.object.select_all(action='DESELECT')
    first_obj = None
    for obj_name in valid_objs:
        obj = bpy.data.objects.get(obj_name)
        if obj:
            obj.select_set(True)
            if first_obj is None:
                first_obj = obj

    if not first_obj:
        return
    bpy.context.view_layer.objects.active = first_obj

    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')

        # Set active UV layer on all objects
        for obj_name in valid_objs:
            obj = bpy.data.objects.get(obj_name)
            if obj and layer_name in obj.data.uv_layers:
                obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

        if props.uv_mode == 'SMART':
            angle_rad = math.radians(props.smart_uv_angle)
            bpy.ops.uv.smart_project(
                angle_limit=angle_rad,
                island_margin=props.smart_uv_island_margin,
            )
        elif props.uv_mode == 'CUBE':
            bpy.ops.uv.cube_project(cube_size=props.cube_size)
            # Cube project creates overlapping islands — must normalize
            try:
                bpy.ops.uv.average_islands_scale()
            except Exception:
                pass
        elif props.uv_mode == 'UNWRAP':
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=props.smart_uv_island_margin)
        elif props.uv_mode == 'AUTO_SEAM':
            # Pass 1: Smart Project to perfectly cut overlaps (like cylinders)
            seam_rad = math.radians(props.auto_seam_angle)
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
            # Pass 2: Convert those perfect cuts into actual mesh seams
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
            # Pass 3: Relax the geometry using Angle-Based unwrap
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            # Pass 2: Split large concave islands for tighter packing
            _force_object_mode()
            any_split = False
            for obj_name in valid_objs:
                if _subdivide_large_uv_islands(obj_name, layer_name):
                    any_split = True
            if any_split:
                # Re-enter multi-object edit mode and re-unwrap
                bpy.ops.object.select_all(action='DESELECT')
                first_obj = None
                for obj_name in valid_objs:
                    obj = bpy.data.objects.get(obj_name)
                    if obj:
                        obj.select_set(True)
                        if first_obj is None:
                            first_obj = obj
                if first_obj:
                    bpy.context.view_layer.objects.active = first_obj
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='SELECT')
                    for obj_name in valid_objs:
                        obj = bpy.data.objects.get(obj_name)
                        if obj and layer_name in obj.data.uv_layers:
                            obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
                    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
        elif props.uv_mode == 'LIGHTMAP':
            try:
                bpy.ops.uv.lightmap_pack(
                    PREF_CONTEXT='ALL_FACES',
                    PREF_PACK_IN_ONE=True,
                    PREF_NEW_UVLAYER=False,
                    PREF_BOX_DIV=props.lightmap_quality,
                    PREF_MARGIN_DIV=props.lightmap_margin,
                )
            except Exception:
                angle_rad = math.radians(66.0)
                bpy.ops.uv.smart_project(angle_limit=angle_rad, island_margin=0.03)
    finally:
        _force_object_mode()

    # Phase B: Pack all objects' islands together
    if props.pack_enabled:
        _atlas_pack_phase(valid_objs, props)

    if props.remove_old_uvs:
        _remove_old_uvs(valid_objs, props.uv_layer_name)


def _atlas_pack_phase(obj_names, props):
    """Pack UV islands across multiple objects simultaneously.
    Each sub-phase (normalize, boost, pack) gets its own edit-mode session."""
    layer_name = props.uv_layer_name

    # Sub-phase 1: Normalize island scale
    if props.pack_world_scale:
        _force_object_mode()
        bpy.ops.object.select_all(action='DESELECT')
        first_obj = None
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if obj:
                obj.select_set(True)
                if first_obj is None:
                    first_obj = obj
        if first_obj:
            bpy.context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                for obj_name in obj_names:
                    obj = bpy.data.objects.get(obj_name)
                    if obj and layer_name in obj.data.uv_layers:
                        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
                try:
                    bpy.ops.uv.average_islands_scale()
                except Exception:
                    pass
            finally:
                _force_object_mode()
        for obj_name in obj_names:
            _apply_world_area_scale(obj_name, layer_name)

    # NOTE: Image boost is intentionally SKIPPED in atlas mode.
    # Boosting individual materials' UVs after atlas packing causes overlaps
    # and UVs going outside 0-1 tile. Image boost only works in PER_MATERIAL mode.

    # Sub-phase 3: Pack islands across all objects
    _force_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    first_obj = None
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj:
            obj.select_set(True)
            if first_obj is None:
                first_obj = obj
    if first_obj:
        bpy.context.view_layer.objects.active = first_obj
        uv_margin = _pixel_margin_to_uv(props.pack_margin_px, props)
        rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'
        shape_method = props.pack_shape_method
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if obj and layer_name in obj.data.uv_layers:
                    obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

            # ── Third Party Packers ──
            if props.pack_engine == 'UVP3' and hasattr(bpy.ops, 'uvpackmaster3'):
                try:
                    bpy.context.scene.uvp3_props.margin = uv_margin
                    bpy.ops.uvpackmaster3.pack()
                    return
                except Exception:
                    pass

            elif props.pack_engine == 'UVP2' and hasattr(bpy.ops, 'uvpackmaster2'):
                try:
                    bpy.context.scene.uvp2_props.margin = uv_margin
                    bpy.ops.uvpackmaster2.uv_pack()
                    return
                except Exception:
                    pass

            # ── Blender Native Packer ──
            for _i in range(props.pack_iterations):
                _run_blender_pack(props, uv_margin)
        finally:
            _force_object_mode()

    # Preserve the packer's texel density and exact island margins.


# ── Tile distribution ─────────────────────────────────────────────────────

def _distribute_tiles(obj_names, tile_count):
    """Distribute objects across N tiles, balanced by 3D surface area.
    Returns a list of lists: tiles[tile_idx] = [obj_name, ...]"""
    # Calculate surface area per object
    obj_areas = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        area = sum(p.area for p in obj.data.polygons) if obj.data.polygons else 0.0
        obj_areas.append((obj_name, area))

    # Sort by area descending (largest first for balanced distribution)
    obj_areas.sort(key=lambda x: x[1], reverse=True)

    # Greedy assignment: put each object in the tile with least total area
    tiles = [[] for _ in range(min(tile_count, len(obj_areas)))]
    tile_areas = [0.0] * len(tiles)

    for obj_name, area in obj_areas:
        # Find tile with smallest total area
        min_idx = tile_areas.index(min(tile_areas))
        tiles[min_idx].append(obj_name)
        tile_areas[min_idx] += area

    # Remove empty tiles
    tiles = [t for t in tiles if t]
    return tiles


# ── Node injection / removal ─────────────────────────────────────────────

def _inject_bake_node(mat_name, image, tag):
    """Insert (or replace) the bake-target Image Texture node in a material."""
    tag = _bake_node_tag(tag)
    mat = bpy.data.materials.get(mat_name)
    if not mat or not mat.use_nodes:
        return None

    nodes = mat.node_tree.nodes

    # Remove any previous AHB node
    for n in list(nodes):
        if n.name.startswith(tag):
            nodes.remove(n)

    node = nodes.new('ShaderNodeTexImage')
    node.name  = tag
    node.label = "AHB Bake Target (do not connect)"
    node.image = image

    # Position to the left of existing nodes
    xs = [n.location.x for n in nodes if n is not node]
    node.location = ((min(xs) - 300 if xs else -400), 200)
    for existing in nodes:
        existing.select = False
    node.select = True
    nodes.active = node  # REQUIRED: Blender bakes into the active Image Texture node
    return node


def _remove_bake_nodes(mat_names, tag):
    """Strip all AHB-injected nodes from the given materials."""
    tag = _bake_node_tag(tag)
    for mat_name in mat_names:
        mat = bpy.data.materials.get(mat_name)
        if mat and mat.use_nodes:
            for n in list(mat.node_tree.nodes):
                if n.name.startswith(tag):
                    mat.node_tree.nodes.remove(n)


def _cycles_devices(cycles_prefs):
    """Refresh and return Cycles devices across Blender API variants."""
    refreshed = cycles_prefs.get_devices()
    devices = list(getattr(cycles_prefs, "devices", []))
    if devices:
        return devices
    if refreshed:
        for group in refreshed:
            if isinstance(group, (list, tuple)):
                devices.extend(group)
    return devices


def _configure_cycles_device(scene, props):
    """Select CPU or a real GPU backend without silently falling back."""
    if props.compute_device == 'CPU':
        scene.cycles.device = 'CPU'
        props.device_status = "CPU"
        print("[AHB] Cycles compute device: CPU")
        return

    cycles_addon = bpy.context.preferences.addons.get('cycles')
    cycles_prefs = cycles_addon.preferences if cycles_addon else None
    if cycles_prefs is None:
        props.device_status = "GPU unavailable (Cycles preferences not found)"
        raise RuntimeError("Cycles preferences were not found; cannot enable GPU baking")

    requested = props.gpu_backend
    configured = getattr(cycles_prefs, "compute_device_type", 'NONE')
    candidates = []
    if requested == 'AUTO':
        if configured and configured != 'NONE':
            candidates.append(configured)
        for backend in ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'):
            if backend not in candidates:
                candidates.append(backend)
    else:
        candidates.append(requested)

    attempted = []
    for backend in candidates:
        attempted.append(backend)
        try:
            cycles_prefs.compute_device_type = backend
            devices = _cycles_devices(cycles_prefs)
        except (TypeError, ValueError, RuntimeError):
            continue

        gpu_devices = [
            device for device in devices
            if getattr(device, "type", 'CPU') != 'CPU'
        ]
        if not gpu_devices:
            continue

        # GPU mode is GPU-only. Leaving CPU enabled creates hybrid rendering
        # and can keep the CPU near 100%, which is confusing during a bake.
        for device in devices:
            device.use = device in gpu_devices

        scene.cycles.device = 'GPU'
        names = ", ".join(device.name for device in gpu_devices)
        props.device_status = f"GPU / {backend}: {names}"
        print(f"[AHB] Cycles compute device: {props.device_status}")
        return

    props.device_status = "GPU unavailable"
    tried = ", ".join(attempted)
    raise RuntimeError(
        "No compatible Cycles GPU was found "
        f"(tried {tried}). For an RTX 4060, update the NVIDIA driver and "
        "use OptiX or CUDA in Edit > Preferences > System > Cycles Render Devices"
    )


def _configure_bake_settings(props, context):
    """Apply all bake settings to the scene. This is the safe approach for
    Blender 5.1 — settings live on scene.render.bake, not as operator args."""
    scene = context.scene

    # Engine
    if props.auto_switch_cycles:
        scene.render.engine = 'CYCLES'

    if scene.render.engine != 'CYCLES':
        raise RuntimeError("Texture baking requires the Cycles render engine")

    # Samples
    scene.cycles.samples = props.samples

    # Compute device. GPU setup errors are shown to the user instead of
    # silently falling back to CPU.
    _configure_cycles_device(scene, props)

    # Bake settings
    bake = scene.render.bake
    bake.use_clear   = props.clear_bake

    # Dilation must fit inside the packed gap. Two neighboring islands each
    # expand into that gap, so cap bake margin to half the UV pack margin.
    if props.pack_enabled:
        bake.margin = min(props.margin, max(0, props.pack_margin_px // 2))
    else:
        bake.margin = props.margin

    try:
        bake.margin_type = props.margin_type
    except Exception:
        pass

    # Pass filters
    bake.use_pass_direct   = props.use_pass_direct
    bake.use_pass_indirect = props.use_pass_indirect
    bake.use_pass_color    = props.use_pass_color

    # Selected to active
    bake.use_selected_to_active = props.use_selected_to_active
    if props.use_selected_to_active:
        bake.cage_extrusion   = props.cage_extrusion
        bake.max_ray_distance = props.max_ray_distance


def _save_image(image, props, context):
    """Save baked pixels directly without applying the display view transform."""
    out_dir = bpy.path.abspath(props.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    ext  = _EXT_MAP.get(props.image_format, '.exr')
    path = os.path.join(out_dir, _safe_filename(image.name) + ext)

    # Configure render image settings on the scene BEFORE calling save_render
    scene = context.scene
    img_settings = scene.render.image_settings
    # A bake target is a single image layer; Blender cannot write it as a
    # multilayer render result. Keep the EXR extension but use OPEN_EXR.
    save_format = ('OPEN_EXR' if props.image_format == 'OPEN_EXR_MULTILAYER'
                   else props.image_format)
    img_settings.file_format = save_format
    img_settings.color_mode = ('RGB' if save_format in ('JPEG', 'HDR')
                               else props.color_mode)

    if save_format == 'OPEN_EXR':
        img_settings.color_depth = '32' if props.use_hdr_float else '16'
        img_settings.exr_codec   = props.exr_codec
    elif save_format == 'JPEG':
        try:
            img_settings.quality = 100
        except (AttributeError, TypeError):
            pass

    # Save the image datablock directly. save_render applies the scene's view
    # transform (AgX/Filmic), which can make baked textures look washed out or
    # otherwise unlike the pixels used by the material.
    try:
        image.filepath_raw = path
        image.file_format = save_format
        image.save()
    except TypeError:
        image.save(filepath=path)

    return path


def _do_bake_batch(obj_names, props, context, source_obj_names=None):
    """Execute the bake operator for a batch of objects at once.
    This is drastically faster than baking one by one because Cycles
    only evaluates the scene once per batch. Returns True on success."""
    objs_to_bake = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj and obj.type == 'MESH':
            objs_to_bake.append(obj)

    if not objs_to_bake:
        return False

    if not _validate_bake_ready([obj.name for obj in objs_to_bake], props,
                                 lambda _level, _message: None):
        raise RuntimeError("Bake target is missing a UV layer or active image node")

    source_objs = []
    if props.use_selected_to_active:
        if len(objs_to_bake) != 1:
            raise RuntimeError("Selected to Active requires exactly one bake target")
        for obj_name in source_obj_names or []:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type == 'MESH' and obj not in objs_to_bake:
                source_objs.append(obj)
        if not source_objs:
            raise RuntimeError(
                "Selected to Active requires at least one selected source mesh")

    # Deselect everything first
    for o in context.selected_objects:
        o.select_set(False)

    # Prepare all objects
    for obj in objs_to_bake:
        obj.select_set(True)
        # Ensure we are in Object Mode
        if obj.mode != 'OBJECT':
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='OBJECT')

        # Re-activate the bake UV layer RIGHT BEFORE baking
        _activate_bake_uv(obj.name, props)

        # Re-activate the bake image node in EVERY material slot
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.use_nodes:
                tag = _bake_node_tag(props.image_node_name)
                for node in mat.node_tree.nodes:
                    if node.name.startswith(tag):
                        for existing in mat.node_tree.nodes:
                            existing.select = False
                        node.select = True
                        mat.node_tree.nodes.active = node
                        break

    # Set the first object as active
    active_obj = objs_to_bake[0]
    context.view_layer.objects.active = active_obj
    for obj in source_objs:
        obj.select_set(True)

    selected_for_bake = source_objs + objs_to_bake

    # Execute bake
    try:
        with bpy.context.temp_override(
            active_object=active_obj,
            selected_objects=selected_for_bake,
            selected_editable_objects=selected_for_bake,
        ):
            bpy.ops.object.bake(type=props.bake_type)
    except RuntimeError as e:
        # Fallback: try without temp_override (some Blender builds)
        try:
            bpy.ops.object.bake(type=props.bake_type)
        except Exception:
            raise e  # re-raise original if fallback also fails

    return True


def _validate_bake_ready(obj_names, props, report):
    """Validate UV and active-image prerequisites before invoking Cycles."""
    failures = []
    tag = _bake_node_tag(props.image_node_name)
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            failures.append(f"{obj_name}: no bakeable mesh faces")
            continue
        if props.uv_layer_name not in obj.data.uv_layers:
            failures.append(
                f"{obj_name}: missing UV layer '{props.uv_layer_name}'")
            continue
        for slot_index in {poly.material_index for poly in obj.data.polygons}:
            if slot_index >= len(obj.material_slots):
                failures.append(f"{obj_name}: invalid material slot {slot_index + 1}")
                continue
            mat = obj.material_slots[slot_index].material
            if not mat or not mat.use_nodes:
                failures.append(f"{obj_name}: empty material slot {slot_index + 1}")
                continue
            node = next((n for n in mat.node_tree.nodes
                         if n.name.startswith(tag) and n.type == 'TEX_IMAGE'
                         and n.image), None)
            if not node:
                failures.append(f"{obj_name}: no bake target in '{mat.name}'")

    if failures:
        preview = "; ".join(failures[:4])
        if len(failures) > 4:
            preview += f"; +{len(failures) - 4} more"
        report({'ERROR'}, f"Bake setup incomplete: {preview}")
        return False
    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Pipeline functions  (called by operators — accept obj_names directly)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run_setup(obj_names, props, report):
    """Set up UVs and inject bake-target image nodes.
    Works on the given obj_names list — never re-queries selection."""

    _normalize_core_names(props)
    _prepare_mesh_data(obj_names, make_unique=props.auto_create_uv)
    created_materials = _prepare_material_slots(obj_names)
    if created_materials:
        report({'INFO'}, f"Created {created_materials} missing bake material(s).")

    if props.unlink_materials:
        props.status_text = "Unlinking shared materials..."
        _force_ui_redraw()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                if slot.material and slot.material.users > 1:
                    slot.material = slot.material.copy()

    _preserve_implicit_texture_uvs(obj_names, props)

    image_mode = props.image_mode
    props.status_text = f"Setting up {image_mode} mode…"

    collection_groups = None
    if image_mode == 'COLLECTION_ATLASES':
        collection_groups = _get_atlas_groups(obj_names, props, report)
        if not _flatten_atlas_groups(collection_groups):
            report({'ERROR'}, "No scoped objects belong to an atlas collection.")
            return False
        _isolate_materials_between_atlas_groups(collection_groups)

    mat_map = _collect_material_names(obj_names)
    if not mat_map:
        report({'WARNING'}, "No materials found on objects.")
        return False

    if image_mode == 'ATLAS':
        if props.auto_create_uv:
            _remove_tile_uv_offsets(obj_names, props.uv_layer_name)
            try:
                _setup_atlas_uvs(obj_names, props)
            except Exception as e:
                report({'ERROR'}, f"Atlas UV setup failed: {e}")
                print(f"[AHB] Atlas UV error:\n{traceback.format_exc()}")
                _force_object_mode()
                return False

        shared_img = _get_or_create_image(props, "shared")
        for mat_name in mat_map:
            _inject_bake_node(mat_name, shared_img, props.image_node_name)

    elif image_mode == 'AUTO_TILES':
        _remove_tile_uv_offsets(obj_names, props.uv_layer_name)
        tiles = _distribute_tiles(obj_names, props.tile_count)

        for tile_idx, tile_obj_names in enumerate(tiles):
            props.status_text = f"Setting up tile {tile_idx + 1}/{len(tiles)}…"
            if props.auto_create_uv:
                try:
                    _setup_atlas_uvs(tile_obj_names, props)
                except Exception as e:
                    report({'ERROR'},
                           f"Tile {tile_idx + 1} UV setup failed: {e}")
                    print(f"[AHB] Tile {tile_idx + 1} UV error:\n"
                          f"{traceback.format_exc()}")
                    _force_object_mode()
                    return False

            tile_img = _get_tile_image(props, tile_idx)
            for obj_name in tile_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                for slot in obj.material_slots:
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        _inject_bake_node(slot.material.name, tile_img,
                                          props.image_node_name)

    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_obj_names in collection_groups:
            if not atlas_obj_names:
                continue
            _remove_tile_uv_offsets(atlas_obj_names, props.uv_layer_name)
            if props.auto_create_uv:
                try:
                    _setup_atlas_uvs(atlas_obj_names, props)
                except Exception as e:
                    report({'ERROR'},
                           f"Atlas {atlas_number} UV setup failed: {e}")
                    print(f"[AHB] Collection atlas {atlas_number} UV error:\n"
                          f"{traceback.format_exc()}")
                    _force_object_mode()
                    return False

            atlas_img = _get_collection_atlas_image(props, atlas_number)
            atlas_mats = _collect_material_names(atlas_obj_names)
            for mat_name in atlas_mats:
                _inject_bake_node(
                    mat_name, atlas_img, props.image_node_name)

    elif image_mode == 'PER_MATERIAL':
        for obj_name in obj_names:
            if props.auto_create_uv:
                try:
                    _ensure_uv(obj_name, props)
                except Exception as e:
                    report({'ERROR'}, f"UV setup failed [{obj_name}]: {e}")
                    _force_object_mode()
                    return False

        for mat_name in mat_map:
            img = _get_or_create_image(props, mat_name)
            _inject_bake_node(mat_name, img, props.image_node_name)

    ready_names = (_flatten_atlas_groups(collection_groups)
                   if collection_groups is not None else obj_names)
    if not _validate_bake_ready(ready_names, props, report):
        return False

    msg = f"Setup done: {len(mat_map)} material(s) across {len(obj_names)} object(s)"
    props.status_text = msg
    report({'INFO'}, msg)
    return True


def _rename_objects_by_texture(obj_names, props, report):
    """Rename objects by appending their baked texture name as a suffix.
    First tries to read the actual baked node (if done after bake),
    otherwise predicts the name based on the deterministic settings."""
    image_mode = props.image_mode
    renamed = 0

    if image_mode == 'PER_MATERIAL':
        report({'WARNING'},
               "Object renaming is ambiguous in Per Material mode and was skipped.")
        return 0

    # Pre-calculate predicted suffixes in case they haven't baked yet
    predicted_suffixes = {}
    if image_mode == 'AUTO_TILES':
        tiles = _distribute_tiles(obj_names, props.tile_count)
        for tile_idx, tile_objs in enumerate(tiles):
            suffix = f"_{props.output_prefix}tile_{tile_idx + 1:02d}"
            for o in tile_objs: predicted_suffixes[o] = suffix
    elif image_mode == 'ATLAS':
        for o in obj_names: predicted_suffixes[o] = f"_{props.output_prefix}shared"
    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_objs in _get_atlas_groups(obj_names, props):
            suffix = (f"_{props.output_prefix}"
                      f"{_get_collection_atlas_name(props, atlas_number)}")
            for o in atlas_objs:
                predicted_suffixes[o] = suffix

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj: continue

        # 1. Look for actual baked texture from custom property (100% reliable)
        actual_texture = obj.get("ahb_baked_texture")

        # 2. Look for actual baked texture from material (legacy/fallback)
        if not actual_texture:
            for slot in obj.material_slots:
                if slot.material and slot.material.use_nodes:
                    node = slot.material.node_tree.nodes.get(props.image_node_name)
                    if node and node.type == 'TEX_IMAGE' and node.image:
                        actual_texture = node.image.name
                        break

        if actual_texture:
            # Enforce lowercase 'tile' for suffixes to match user expectations if it contains Tile
            suffix = f"_{actual_texture}".replace("Tile", "tile").replace("TILE", "tile")
        else:
            # 3. Fallback to prediction (if done BEFORE bake)
            suffix = predicted_suffixes.get(obj.name)

        if not suffix:
            continue

        # Prevent double-renaming
        if obj.name.endswith(suffix):
            continue

        obj.name = f"{obj.name}{suffix}"
        renamed += 1

    return renamed


def _run_bake(obj_names, props, context, report, source_obj_names=None):
    """Bake all objects in obj_names. Never re-queries selection.
    Returns (baked_count, error_count)."""
    _normalize_core_names(props)
    if (props.bake_type in _PASS_FILTER_TYPES
            and not (props.use_pass_direct or props.use_pass_indirect
                     or props.use_pass_color)):
        report({'ERROR'}, "Enable at least one Direct, Indirect, or Color pass.")
        return 0, 1

    try:
        _configure_bake_settings(props, context)
    except Exception as e:
        report({'ERROR'}, f"Cannot configure bake: {e}")
        return 0, 1

    image_mode = props.image_mode
    all_mat_names = set()
    baked_names = set()
    errors = 0
    total = len(obj_names)

    if image_mode == 'AUTO_TILES':
        tiles = _distribute_tiles(obj_names, props.tile_count)
        for tile_idx, tile_obj_names in enumerate(tiles):
            tile_img = _get_tile_image(props, tile_idx)
            valid_objs = []
            for obj_name in tile_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                obj["ahb_baked_texture"] = tile_img.name
                has_mat = False
                for slot in obj.material_slots:
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        all_mat_names.add(slot.material.name)
                        _inject_bake_node(slot.material.name, tile_img, props.image_node_name)
                        has_mat = True
                if has_mat:
                    valid_objs.append(obj_name)

            if not valid_objs:
                continue
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking tile {tile_idx + 1}/{len(tiles)} ({len(valid_objs)} objects)"
            _force_ui_redraw()

            try:
                success = _do_bake_batch(valid_objs, props, context,
                                         source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"[Tile {tile_idx + 1}] Baked {len(valid_objs)} objects")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED [Tile {tile_idx + 1}]: {e}")

    elif image_mode == 'COLLECTION_ATLASES':
        groups = _get_atlas_groups(obj_names, props, report)
        total = len(_flatten_atlas_groups(groups))
        _isolate_materials_between_atlas_groups(groups)
        for atlas_number, atlas_obj_names in groups:
            if not atlas_obj_names:
                continue
            col_name = _get_collection_atlas_name(props, atlas_number)
            atlas_img = _get_collection_atlas_image(props, atlas_number)
            valid_objs = []
            for obj_name in atlas_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                obj["ahb_baked_texture"] = atlas_img.name
                has_mat = False
                for slot in obj.material_slots:
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        all_mat_names.add(slot.material.name)
                        _inject_bake_node(slot.material.name, atlas_img, props.image_node_name)
                        has_mat = True
                if has_mat:
                    valid_objs.append(obj_name)

            if not valid_objs:
                continue
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking {col_name} ({len(valid_objs)} objects)"
            _force_ui_redraw()

            try:
                success = _do_bake_batch(valid_objs, props, context,
                                         source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"[{col_name}] Baked {len(valid_objs)} objects")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED [{col_name}]: {e}")

    elif image_mode == 'ATLAS':
        atlas_img = _get_or_create_image(props, "shared")
        valid_objs = []
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            obj["ahb_baked_texture"] = atlas_img.name
            has_mat = False
            for slot in obj.material_slots:
                if slot.material:
                    if not slot.material.use_nodes:
                        slot.material.use_nodes = True
                    all_mat_names.add(slot.material.name)
                    _inject_bake_node(slot.material.name, atlas_img, props.image_node_name)
                    has_mat = True
            if has_mat:
                valid_objs.append(obj_name)

        if valid_objs:
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking Single Atlas ({len(valid_objs)} objects)"
            _force_ui_redraw()

            try:
                success = _do_bake_batch(valid_objs, props, context,
                                         source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"Baked Single Atlas ({len(valid_objs)} objects)")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED: {e}")

    elif image_mode == 'PER_MATERIAL':
        mat_map = _collect_material_names(obj_names)
        valid_objs = set()
        object_material_images = {}
        for mat_name, mat_obj_names in mat_map.items():
            mat_img = _get_or_create_image(props, mat_name)
            _inject_bake_node(mat_name, mat_img, props.image_node_name)
            all_mat_names.add(mat_name)
            for obj_name in mat_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                valid_objs.add(obj_name)
                object_material_images.setdefault(obj_name, set()).add(mat_img.name)

        for obj_name, image_names in object_material_images.items():
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            if len(image_names) == 1:
                obj["ahb_baked_texture"] = next(iter(image_names))
            elif "ahb_baked_texture" in obj:
                del obj["ahb_baked_texture"]

        if valid_objs:
            valid_objs = sorted(valid_objs)
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = (
                f"Baking {len(mat_map)} material image(s) "
                f"across {len(valid_objs)} object(s)")
            _force_ui_redraw()

            try:
                success = _do_bake_batch(valid_objs, props, context,
                                         source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'},
                           f"Baked {len(mat_map)} per-material image(s)")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Per-material bake FAILED: {e}")

    # ── Save ALL images AFTER all baking ───────────────────────────────
    if props.auto_save:
        props.status_text = "Saving images…"
        _force_ui_redraw()
        saved = 0
        for img in bpy.data.images:
            if img.name.startswith(props.output_prefix) and img.has_data:
                try:
                    path = _save_image(img, props, context)
                    saved += 1
                except Exception as e:
                    report({'WARNING'}, f"Save failed [{img.name}]: {e}")

    if props.auto_cleanup_nodes and baked_names and all_mat_names:
        _remove_bake_nodes(all_mat_names, props.image_node_name)

    baked = len(baked_names)
    msg = f"Baked {baked}/{total} object(s). Errors: {errors}"
    report({'INFO'}, msg)
    return baked, errors


def _get_object_bake_image(obj, props, material=None):
    """Resolve the image already assigned to an object's texture pack."""
    if not obj:
        return None

    remembered = obj.get("ahb_baked_texture")
    if remembered:
        image = bpy.data.images.get(str(remembered))
        if image:
            return image

    if props.image_mode == 'ATLAS':
        return bpy.data.images.get(f"{props.output_prefix}shared")

    if props.image_mode == 'PER_MATERIAL' and material:
        return bpy.data.images.get(f"{props.output_prefix}{material.name}")

    if props.image_mode == 'COLLECTION_ATLASES':
        groups = _get_atlas_groups([obj.name], props)
        for atlas_number, names in groups:
            if obj.name in names:
                return _get_collection_atlas_image(props, atlas_number)

    tag = _bake_node_tag(props.image_node_name)
    if obj.material_slots:
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.name.startswith(tag) and node.type == 'TEX_IMAGE' and node.image:
                    return node.image
                if (node.name == 'AHB_Applied_Texture'
                        and node.type == 'TEX_IMAGE' and node.image):
                    return node.image
    return None


def _apply_single_object_bake(obj, props, mode, report):
    """Apply already-baked images to one object without redistributing tiles."""
    applied = 0
    processed = set()
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or mat.name in processed:
            continue
        processed.add(mat.name)
        image = _get_object_bake_image(
            obj, props, mat if props.image_mode == 'PER_MATERIAL' else None)
        if not image:
            report({'WARNING'}, f"No existing bake image found for '{mat.name}'.")
            continue
        backup_name = f"{mat.name}_AHB_backup"
        if not bpy.data.materials.get(backup_name):
            backup = mat.copy()
            backup.name = backup_name
        if _apply_baked_to_material(
                mat.name, image, mode, props.apply_keep_original_nodes,
                props.uv_layer_name):
            applied += 1
    return applied


def _scope_has_material_backups(obj_names):
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            if slot.material and bpy.data.materials.get(
                    f"{slot.material.name}_AHB_backup"):
                return True
    return False


def _restore_image_uv_backups(obj_names):
    """Make the saved source-image UV map active after restoring materials."""
    restored = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        backup_name = obj.data.get(
            IMAGE_UV_BACKUP_PROP,
            obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
        if backup_name not in obj.data.uv_layers:
            continue
        has_image_material = any(
            slot.material and _has_real_image_textures(
                slot.material, NODE_TAG)
            for slot in obj.material_slots
        )
        if not has_image_material:
            continue
        uv_layer = obj.data.uv_layers[backup_name]
        obj.data.uv_layers.active = uv_layer
        try:
            obj.data.uv_layers.active_render = uv_layer
        except (AttributeError, RuntimeError):
            pass
        restored += 1
    return restored


def _get_existing_tile_groups(obj_names, props):
    """Group objects by their recorded tile image, preserving prior packing."""
    groups = {}
    fallback = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        image = _get_object_bake_image(obj, props)
        if image and image.name.startswith(props.output_prefix + 'tile_'):
            match = re.search(r'tile_(\d+)', image.name, re.IGNORECASE)
            if match:
                tile_idx = int(match.group(1)) - 1
                groups.setdefault(tile_idx, []).append(obj_name)
                continue
        fallback.append(obj_name)

    if fallback:
        predicted = _distribute_tiles(fallback, props.tile_count)
        used = set(groups)
        for tile_idx, tile_objs in enumerate(predicted):
            while tile_idx in used:
                tile_idx += 1
            groups.setdefault(tile_idx, []).extend(tile_objs)
            used.add(tile_idx)
    return [(idx, groups[idx]) for idx in sorted(groups)]


def _run_apply(obj_names, props, report):
    """Apply baked textures to materials. Works on given obj_names list."""
    _normalize_core_names(props)
    mode = _resolve_apply_mode(props)
    applied = 0
    skipped = 0

    if props.image_mode == 'AUTO_TILES':
        # ── AUTO_TILES: process tile-by-tile ──────────────────────────────
        # Materials shared across tiles MUST be duplicated so each tile's
        # objects get a material copy pointing to the correct tile image.
        tiles = _get_existing_tile_groups(obj_names, props)

        # Track: mat_name → tile_idx it was first applied for
        mat_applied_tile = {}

        for tile_idx, tile_obj_names in tiles:
            tile_name = f"tile_{tile_idx + 1:02d}"
            img = bpy.data.images.get(f"{props.output_prefix}{tile_name}")
            if not img:
                report({'WARNING'}, f"No image for tile {tile_idx + 1}")
                continue

            # Track materials already handled within THIS tile
            done_in_tile = set()

            for obj_name in tile_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue

                for slot_idx, slot in enumerate(obj.material_slots):
                    mat = slot.material
                    if not mat:
                        continue

                    # Check if this material was already applied for a
                    # DIFFERENT tile — if so, we must duplicate it.
                    prev_tile = mat_applied_tile.get(mat.name)

                    if prev_tile is not None and prev_tile != tile_idx:
                        # Shared across tiles → duplicate for this tile
                        new_mat = mat.copy()
                        new_mat.name = f"{mat.name}_tile{tile_idx + 1:02d}"
                        obj.material_slots[slot_idx].material = new_mat
                        mat = new_mat

                    if mat.name in done_in_tile:
                        continue
                    done_in_tile.add(mat.name)

                    # Backup the original material
                    backup_name = f"{mat.name}_AHB_backup"
                    if not bpy.data.materials.get(backup_name):
                        backup = mat.copy()
                        backup.name = backup_name

                    success = _apply_baked_to_material(
                        mat.name, img, mode,
                        props.apply_keep_original_nodes,
                        props.uv_layer_name)
                    if success:
                        applied += 1
                        mat_applied_tile[mat.name] = tile_idx
                    else:
                        skipped += 1

    elif props.image_mode == 'COLLECTION_ATLASES':
        # Each numbered collection has its own atlas image. Materials shared
        # across groups were isolated during setup/bake.
        processed_mats = set()
        groups = _get_atlas_groups(obj_names, props, report)
        for atlas_number, atlas_obj_names in groups:
            if not atlas_obj_names:
                continue
            img = bpy.data.images.get(
                f"{props.output_prefix}{_get_collection_atlas_name(props, atlas_number)}")
            if not img:
                report({'WARNING'},
                       f"No baked image for {_get_collection_atlas_name(props, atlas_number)}")
                continue
            for obj_name in atlas_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                for slot in obj.material_slots:
                    mat = slot.material
                    if not mat or mat.name in processed_mats:
                        continue
                    processed_mats.add(mat.name)
                    backup_name = f"{mat.name}_AHB_backup"
                    if not bpy.data.materials.get(backup_name):
                        backup = mat.copy()
                        backup.name = backup_name
                    if _apply_baked_to_material(
                            mat.name, img, mode,
                            props.apply_keep_original_nodes,
                            props.uv_layer_name):
                        applied += 1
                    else:
                        skipped += 1

    else:
        # ── ATLAS / PER_MATERIAL ──────────────────────────────────────────
        processed_mats = set()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or mat.name in processed_mats:
                    continue
                processed_mats.add(mat.name)

                # Find the baked image
                img = None
                if props.image_mode == 'ATLAS':
                    img = bpy.data.images.get(f"{props.output_prefix}shared")
                elif props.image_mode == 'PER_MATERIAL':
                    img = bpy.data.images.get(
                        f"{props.output_prefix}{mat.name}")

                # Fallbacks
                if not img:
                    img = bpy.data.images.get(f"{props.output_prefix}shared")
                if not img:
                    img = bpy.data.images.get(
                        f"{props.output_prefix}{mat.name}")

                if not img:
                    skipped += 1
                    continue

                # Backup
                backup_name = f"{mat.name}_AHB_backup"
                if not bpy.data.materials.get(backup_name):
                    backup = mat.copy()
                    backup.name = backup_name

                success = _apply_baked_to_material(
                    mat.name, img, mode,
                    props.apply_keep_original_nodes,
                    props.uv_layer_name)
                if success:
                    applied += 1
                else:
                    skipped += 1

    mode_label = 'Emission' if mode == 'EMISSION' else 'Base Color'
    msg = f"Applied {applied} material(s) as {mode_label}. Skipped: {skipped}"
    props.status_text = msg
    report({'INFO'}, msg)
    return applied, skipped


def _save_and_restore_selection(context, func):
    """Run func() while preserving the current object selection."""
    active_obj = context.view_layer.objects.active
    selected_objects = list(context.selected_objects)

    try:
        result = func()
    finally:
        _force_object_mode()
        try:
            bpy.ops.object.select_all(action='DESELECT')
        except Exception:
            pass
        for obj in selected_objects:
            if obj:
                try:
                    obj.select_set(True)
                except Exception:
                    pass
        if active_obj:
            try:
                context.view_layer.objects.active = active_obj
            except Exception:
                pass

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Operators
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── JSON EXPORT/IMPORT ──────────────────────────────────────────────────

def _get_principled_bsdf(mat):
    if not mat or not mat.use_nodes: return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None

def _extract_socket_value(socket):
    if socket.is_linked:
        link = socket.links[0]
        node = link.from_node
        if node.type == 'TEX_IMAGE' and node.image:
            import bpy
            return {'type': 'texture', 'path': bpy.path.abspath(node.image.filepath)}
        elif node.type == 'NORMAL_MAP':
            return _extract_socket_value(node.inputs['Color'])
    else:
        val = socket.default_value
        if hasattr(val, '__len__'):
            return {'type': 'vector', 'value': list(val)}
        elif isinstance(val, (int, float)):
            return {'type': 'float', 'value': val}
    return None

def _load_texture(path):
    import os
    import bpy
    if os.path.exists(path):
        for img in bpy.data.images:
            if img.filepath == path or bpy.path.abspath(img.filepath) == path:
                return img
        try:
            return bpy.data.images.load(path)
        except:
            return None
    return None

class AHB_OT_ExportMaterialsJSON(Operator, ExportHelper):
    """Export material assignments and PBR properties to JSON"""
    bl_idname = "ahb.export_materials_json"
    bl_label = "Export Materials to JSON"
    bl_options = {'REGISTER'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        import bpy
        import json
        props = context.scene.ahb_props
        obj_names = _get_object_names(props, context)

        data = {'objects': {}, 'materials': {}}

        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH': continue

            mats = [slot.material.name for slot in obj.material_slots if slot.material]
            data['objects'][obj_name] = mats

            for mat_name in mats:
                if mat_name in data['materials']: continue
                mat = bpy.data.materials.get(mat_name)

                mat_data = {'name': mat_name}
                bsdf = _get_principled_bsdf(mat)
                if bsdf:
                    mat_data['pbr'] = {}
                    for key in ['Base Color', 'Metallic', 'Roughness', 'Specular IOR Level', 'Emission Color', 'Normal', 'Alpha']:
                        if key in bsdf.inputs:
                            val = _extract_socket_value(bsdf.inputs[key])
                            if val:
                                mat_data['pbr'][key] = val

                data['materials'][mat_name] = mat_data

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=4)

        self.report({'INFO'}, f"Exported {len(data['materials'])} materials to JSON.")
        return {'FINISHED'}

class AHB_OT_ImportMaterialsJSON(Operator, ImportHelper):
    """Import material assignments and PBR properties from JSON"""
    bl_idname = "ahb.import_materials_json"
    bl_label = "Import Materials from JSON"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        import bpy
        import json
        with open(self.filepath, 'r') as f:
            try:
                data = json.load(f)
            except Exception as e:
                self.report({'ERROR'}, f"Failed to parse JSON: {e}")
                return {'CANCELLED'}

        # Recreate materials
        for mat_name, mat_data in data.get('materials', {}).items():
            mat = bpy.data.materials.get(mat_name)
            if not mat:
                mat = bpy.data.materials.new(mat_name)
                mat.use_nodes = True

            bsdf = _get_principled_bsdf(mat)
            if not bsdf and mat.use_nodes:
                bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')

            if bsdf and 'pbr' in mat_data:
                for key, val in mat_data['pbr'].items():
                    if key in bsdf.inputs:
                        socket = bsdf.inputs[key]
                        if val['type'] == 'texture':
                            img = _load_texture(val['path'])
                            if img:
                                tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                                tex_node.image = img
                                if key == 'Normal':
                                    norm_node = mat.node_tree.nodes.new('ShaderNodeNormalMap')
                                    mat.node_tree.links.new(tex_node.outputs['Color'], norm_node.inputs['Color'])
                                    mat.node_tree.links.new(norm_node.outputs['Normal'], socket)
                                else:
                                    mat.node_tree.links.new(tex_node.outputs['Color'], socket)
                        elif val['type'] == 'vector':
                            socket.default_value = val['value']
                        elif val['type'] == 'float':
                            socket.default_value = val['value']

        # Assign to objects
        for obj_name, mats in data.get('objects', {}).items():
            obj = bpy.data.objects.get(obj_name)
            if not obj: continue

            obj.data.materials.clear()
            for mat_name in mats:
                mat = bpy.data.materials.get(mat_name)
                if mat:
                    obj.data.materials.append(mat)

        self.report({'INFO'}, "Imported materials from JSON successfully.")
        return {'FINISHED'}

class AHB_OT_SetupMaterials(Operator):
    """Scan objects, create bake UV layers, and inject Image Texture nodes"""
    bl_idname  = "ahb.setup_materials"
    bl_label   = "Setup Material Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, _source_names = _resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        def work():
            return _run_setup(obj_names, props, self.report)

        ok = _save_and_restore_selection(context, work)
        return {'FINISHED'} if ok else {'CANCELLED'}


class AHB_OT_BakeAll(Operator):
    """Run the automated HDR bake pipeline on all objects in scope"""
    bl_idname  = "ahb.bake_all"
    bl_label   = "Bake All"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, source_names = _resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        def work():
            return _run_bake(obj_names, props, context, self.report, source_names)

        baked, _errors = _save_and_restore_selection(context, work)
        return {'FINISHED'} if baked else {'CANCELLED'}


class AHB_OT_RebakeSelected(Operator):
    """Rebake the active object into its existing atlas/tile/material image."""
    bl_idname = "ahb.rebake_selected"
    bl_label = "Rebake Selected Object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data.polygons)

    def execute(self, context):
        props = context.scene.ahb_props
        _normalize_core_names(props)
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            self.report({'WARNING'}, "Select an active mesh object to rebake.")
            return {'CANCELLED'}

        obj_names = [obj.name]
        source_names = [o.name for o in context.selected_objects
                        if o.type == 'MESH' and o != obj and o.data.polygons]

        def work():
            # Never use the previous baked shader as the source for a rebake.
            _restore_material_backups(obj_names)
            _prepare_mesh_data(obj_names, make_unique=props.auto_create_uv)
            _prepare_material_slots(obj_names)
            _preserve_implicit_texture_uvs(obj_names, props)

            if props.uv_layer_name not in obj.data.uv_layers:
                if props.image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
                    _setup_atlas_uvs(obj_names, props)
                else:
                    _ensure_uv(obj.name, props)

            target_images = {}
            had_recorded_assignment = bool(obj.get("ahb_baked_texture"))
            for slot in obj.material_slots:
                mat = slot.material
                if not mat:
                    continue
                image = _get_object_bake_image(
                    obj, props, mat if props.image_mode == 'PER_MATERIAL' else None)
                if image:
                    target_images[mat.name] = image
                    _inject_bake_node(mat.name, image, props.image_node_name)

            if not target_images:
                raise RuntimeError(
                    "The object has no existing bake image/texture-pack assignment")
            if props.image_mode in ('AUTO_TILES', 'COLLECTION_ATLASES') \
                    and not had_recorded_assignment:
                raise RuntimeError(
                    "This object has no recorded texture-pack assignment; run a full bake first")
            if not _validate_bake_ready(obj_names, props, self.report):
                return False

            _configure_bake_settings(props, context)
            context.scene.render.bake.use_clear = False
            props.status_text = f"Rebaking {obj.name} in its existing texture pack…"
            _force_ui_redraw()
            _do_bake_batch(obj_names, props, context, source_names)

            if props.auto_save:
                for image in {image.name: image for image in target_images.values()}.values():
                    if image.has_data:
                        _save_image(image, props, context)

            if props.auto_cleanup_nodes:
                _remove_bake_nodes(set(target_images), props.image_node_name)
            _apply_single_object_bake(
                obj, props, _resolve_apply_mode(props), self.report)
            props.baked_view = True
            props.status_text = f"Rebaked {obj.name} in its existing texture pack."
            return True

        try:
            ok = _save_and_restore_selection(context, work)
        except Exception as e:
            print(f"[AHB] Selected rebake error:\n{traceback.format_exc()}")
            props.status_text = f"Selected rebake failed: {e}"
            self.report({'ERROR'}, f"Selected rebake failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'} if ok else {'CANCELLED'}


class AHB_OT_QuickBake(Operator):
    """One-click: detect materials → setup nodes → bake → save"""
    bl_idname  = "ahb.quick_bake"
    bl_label   = "Quick Bake (Full Pipeline)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props

        # Capture object list ONCE — used for the ENTIRE pipeline
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, source_names = _resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        self.report({'INFO'},
                    f"Starting full pipeline for {len(obj_names)} object(s)…")
        props.status_text = f"Pipeline: {len(obj_names)} object(s)…"
        _force_ui_redraw()

        def work():
            restored = _restore_material_backups(obj_names)
            if restored:
                self.report({'INFO'},
                            f"Restored {restored} original material(s) before rebaking.")

            # Step 1: Setup (uses captured obj_names, NOT current selection)
            ok = _run_setup(obj_names, props, self.report)
            if not ok:
                props.status_text = "Setup failed!"
                _force_ui_redraw()
                return False

            # Step 2: Bake (uses SAME captured obj_names)
            baked, errs = _run_bake(
                obj_names, props, context, self.report, source_names)
            if baked == 0:
                props.status_text = "Bake failed — 0 objects baked!"
                _force_ui_redraw()
                return False
            if errs:
                props.status_text = (
                    f"Bake incomplete — {baked} object(s), {errs} error(s)")
                _force_ui_redraw()
                return False

            # Step 3: Apply baked textures to materials
            props.status_text = "Applying baked textures…"
            _force_ui_redraw()
            _run_apply(obj_names, props, self.report)

            if props.auto_rename_objects:
                _rename_objects_by_texture(obj_names, props, self.report)

            props.status_text = (
                f"Pipeline complete ✓  ({baked} baked, {errs} errors)")
            props.baked_view = True
            _force_ui_redraw()
            return True

        try:
            ok = _save_and_restore_selection(context, work)
        except Exception as e:
            print(f"[AHB] Quick bake error:\n{traceback.format_exc()}")
            props.status_text = f"Pipeline failed: {e}"
            self.report({'ERROR'}, f"Pipeline failed: {e}")
            _force_ui_redraw()
            return {'CANCELLED'}
        return {'FINISHED'} if ok else {'CANCELLED'}


def _remove_all_uvs(obj_names):
    """Remove ALL UV layers from all specified mesh objects."""
    removed_count = 0
    obj_count = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if not mesh.uv_layers:
            continue

        if mesh.users > 1:
            mesh = mesh.copy()
            obj.data = mesh

        uv_names = [uv.name for uv in mesh.uv_layers]
        for name in uv_names:
            uv_layer = mesh.uv_layers.get(name)
            if uv_layer:
                mesh.uv_layers.remove(uv_layer)
                removed_count += 1
        obj_count += 1

    return removed_count, obj_count


class AHB_OT_RemoveAllUVMaps(Operator):
    """Remove ALL UV maps from objects in scope, leaving meshes completely clean"""
    bl_idname  = "ahb.remove_all_uv_maps"
    bl_label   = "Remove All UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        removed_count, obj_count = _remove_all_uvs(obj_names)
        msg = f"Removed {removed_count} UV map(s) across {obj_count} object(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AHB_OT_DeleteOldUVMaps(Operator):
    """Delete old UV maps while retaining bake and image-material backups."""
    bl_idname = "ahb.delete_old_uv_maps"
    bl_label = "Delete Old UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        props = context.scene.ahb_props
        keep_name = props.uv_layer_name

        count = 0
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                mesh = obj.data
                keep_names = {keep_name}
                backup_name = mesh.get(
                    IMAGE_UV_BACKUP_PROP,
                    obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
                if backup_name in mesh.uv_layers:
                    keep_names.add(backup_name)
                to_remove = [uv.name for uv in mesh.uv_layers
                             if uv.name not in keep_names]
                for name in to_remove:
                    mesh.uv_layers.remove(mesh.uv_layers[name])
                    count += 1

        self.report({'INFO'}, f"Deleted {count} old UV maps")
        return {'FINISHED'}


class AHB_OT_ClearBakeNodes(Operator):
    """Remove all auto-added AHB Image Texture nodes from materials"""
    bl_idname  = "ahb.clear_bake_nodes"
    bl_label   = "Remove Bake Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        mat_names = set()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                if slot.material:
                    mat_names.add(slot.material.name)

        _remove_bake_nodes(mat_names, props.image_node_name)
        self.report({'INFO'}, f"Removed bake nodes from {len(mat_names)} material(s).")
        return {'FINISHED'}


class AHB_OT_SaveImages(Operator):
    """Save all baked images to the output directory"""
    bl_idname  = "ahb.save_images"
    bl_label   = "Save Baked Images"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.ahb_props
        _normalize_core_names(props)
        saved = 0

        for img in bpy.data.images:
            if img.name.startswith(props.output_prefix) and img.has_data:
                try:
                    path = _save_image(img, props, context)
                    saved += 1
                    self.report({'INFO'}, f"Saved: {path}")
                except Exception as e:
                    self.report({'WARNING'}, f"Save failed [{img.name}]: {e}")

        self.report({'INFO'}, f"Saved {saved} image(s).")
        return {'FINISHED'}


def _clear_and_renew_auto_seams(obj_names, props):
    """Clear all existing UV seams and re-apply fresh auto seams at auto_seam_angle."""
    seam_rad = math.radians(props.auto_seam_angle)
    count = 0
    _force_object_mode()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data

        # Clear existing seams
        for edge in mesh.edges:
            edge.use_seam = False

        # Mark seams from smart project islands and unwrap
        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            count += 1
        except Exception:
            pass
        finally:
            _force_object_mode()
    return count


class AHB_OT_RenewAutoSeams(Operator):
    """Clear all existing seams and recalculate fresh auto seams based on Seam Angle"""
    bl_idname  = "ahb.renew_auto_seams"
    bl_label   = "Renew Auto Seams"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        count = _clear_and_renew_auto_seams(obj_names, props)
        # Re-pack UVs after renewing seams
        _setup_atlas_uvs(obj_names, props)

        # Enter edit mode for visual feedback
        _force_object_mode()
        first_obj = None
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type == 'MESH':
                obj.select_set(True)
                if first_obj is None:
                    first_obj = obj
        if first_obj:
            bpy.context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
            except Exception:
                pass

        msg = f"Renewed auto seams & re-unwrapped {count} object(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AHB_OT_PreviewUV(Operator):
    """Preview the UV layout that will be used for baking"""
    bl_idname  = "ahb.preview_uv"
    bl_label   = "Preview Bake UVs"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        # Make sure we start in object mode
        _force_object_mode()

        image_mode = props.image_mode
        layer_name = props.uv_layer_name

        if image_mode == 'ATLAS':
            try:
                _setup_atlas_uvs(obj_names, props)
                self.report({'INFO'},
                            f"Atlas UV layout created for {len(obj_names)} object(s).")
            except Exception as e:
                self.report({'WARNING'}, f"Atlas UV preview failed: {e}")
                print(f"[AHB] Atlas UV preview error: {traceback.format_exc()}")
                _force_object_mode()

        elif image_mode == 'AUTO_TILES':
            _remove_tile_uv_offsets(obj_names, layer_name)
            tiles = _distribute_tiles(obj_names, props.tile_count)
            succeeded = 0
            tile_info = []
            for tile_idx, tile_obj_names in enumerate(tiles):
                try:
                    _setup_atlas_uvs(tile_obj_names, props)
                    _offset_tile_uvs(tile_obj_names, tile_idx, layer_name)
                    succeeded += 1
                    tile_info.append(f"Tile {tile_idx + 1}: {len(tile_obj_names)} obj(s)")
                except Exception as e:
                    self.report({'WARNING'}, f"Tile {tile_idx + 1} UV preview failed: {e}")
                    print(f"[AHB] Tile {tile_idx + 1} UV preview error: {traceback.format_exc()}")
                    _force_object_mode()

            info = " | ".join(tile_info)
            self.report({'INFO'}, f"UV preview: {succeeded} tile(s) side-by-side. {info}.")

        elif image_mode == 'COLLECTION_ATLASES':
            _remove_tile_uv_offsets(obj_names, layer_name)
            groups = _get_atlas_groups(obj_names, props, self.report)
            succeeded = 0
            atlas_info = []
            for atlas_idx, (atlas_number, atlas_obj_names) in enumerate(groups):
                if not atlas_obj_names:
                    continue
                try:
                    _setup_atlas_uvs(atlas_obj_names, props)
                    _offset_tile_uvs(atlas_obj_names, atlas_idx, layer_name)
                    succeeded += 1
                    col_name = _get_collection_atlas_name(props, atlas_number)
                    atlas_info.append(f"{col_name}: {len(atlas_obj_names)} obj(s)")
                except Exception as e:
                    self.report({'WARNING'}, f"Atlas {atlas_number} UV preview failed: {e}")
                    print(f"[AHB] Collection atlas {atlas_number} UV preview error: {traceback.format_exc()}")
                    _force_object_mode()

            info = " | ".join(atlas_info)
            self.report({'INFO'}, f"Collection atlas UV preview: {succeeded} atlas group(s). {info}.")

        elif image_mode == 'PER_MATERIAL':
            succeeded = 0
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj or obj.type != 'MESH':
                    continue
                if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
                    continue

                if props.auto_create_uv:
                    try:
                        _ensure_uv(obj_name, props)
                        succeeded += 1
                    except Exception as e:
                        self.report({'WARNING'}, f"UV setup failed [{obj_name}]: {e}")
                        _force_object_mode()

            self.report({'INFO'}, f"UV layers created/updated for {succeeded}/{len(obj_names)} object(s).")

        # ── Enter Edit Mode and Select All Mesh UVs so UV Editor displays them ──
        _force_object_mode()
        bpy.ops.object.select_all(action='DESELECT')
        first_obj = None

        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type == 'MESH':
                obj.select_set(True)
                if first_obj is None:
                    first_obj = obj
                if layer_name in obj.data.uv_layers:
                    obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

        if first_obj:
            bpy.context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
            except Exception:
                pass

        return {'FINISHED'}


class AHB_OT_PackIslands(Operator):
    """Run optimized UV island packing on selected objects (UVPackmaster-style)"""
    bl_idname  = "ahb.pack_islands"
    bl_label   = "Pack UV Islands"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        _force_object_mode()

        image_mode = props.image_mode

        if image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
            # Multi-object packing
            valid = []
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj or obj.type != 'MESH':
                    continue
                if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
                    continue
                layer_name = props.uv_layer_name
                if layer_name not in obj.data.uv_layers:
                    self.report({'WARNING'}, f"{obj_name}: No UV layer '{layer_name}'.")
                    continue
                valid.append(obj_name)

            if not valid:
                self.report({'WARNING'}, "No objects with valid UV layers.")
                return {'CANCELLED'}

            if image_mode == 'ATLAS':
                try:
                    _atlas_pack_phase(valid, props)
                except Exception as e:
                    self.report({'WARNING'}, f"Atlas pack failed: {e}")
                    print(f"[AHB] Atlas pack error:\n{traceback.format_exc()}")
                    _force_object_mode()
            elif image_mode == 'AUTO_TILES':
                tiles = _distribute_tiles(valid, props.tile_count)
                for tile_idx, tile_objs in enumerate(tiles):
                    try:
                        _atlas_pack_phase(tile_objs, props)
                    except Exception as e:
                        self.report({'WARNING'},
                                    f"Tile {tile_idx + 1} pack failed: {e}")
                        _force_object_mode()
            else:
                groups = _get_atlas_groups(valid, props, self.report)
                for atlas_number, atlas_objs in groups:
                    if not atlas_objs:
                        continue
                    try:
                        _atlas_pack_phase(atlas_objs, props)
                    except Exception as e:
                        self.report({'WARNING'},
                                    f"Atlas {atlas_number} pack failed: {e}")
                        _force_object_mode()

            margin_info = f"{props.pack_margin_px}px margin"
            msg = (f"Packed islands across {len(valid)} object(s) "
                   f"({margin_info}, {props.pack_iterations} iterations)")
            props.status_text = msg
            self.report({'INFO'}, msg)

        else:
            # PER_MATERIAL: per-object packing
            packed = 0
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj or obj.type != 'MESH':
                    continue
                if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
                    continue

                layer_name = props.uv_layer_name
                if layer_name not in obj.data.uv_layers:
                    self.report({'WARNING'}, f"{obj_name}: No UV layer '{layer_name}'.")
                    continue

                try:
                    _pack_uv_islands_phased(obj_name, props)
                    packed += 1
                except Exception as e:
                    self.report({'WARNING'}, f"Pack failed [{obj_name}]: {e}")
                    print(f"[AHB] Pack error for {obj_name}:\n{traceback.format_exc()}")
                    _force_object_mode()

            margin_info = f"{props.pack_margin_px}px margin"
            msg = (f"Packed islands on {packed} object(s) "
                   f"({margin_info}, {props.pack_iterations} iterations)")
            props.status_text = msg
            self.report({'INFO'}, msg)

        return {'FINISHED'}


class AHB_OT_CleanupImages(Operator):
    """Remove all AHB-generated images from the blend file"""
    bl_idname  = "ahb.cleanup_images"
    bl_label   = "Remove Bake Images"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props   = context.scene.ahb_props
        _normalize_core_names(props)
        prefix  = props.output_prefix
        removed = 0

        for img in list(bpy.data.images):
            if img.name.startswith(prefix):
                bpy.data.images.remove(img)
                removed += 1

        self.report({'INFO'}, f"Removed {removed} bake image(s) from blend data.")
        return {'FINISHED'}


class AHB_OT_RemoveUnusedMaterials(Operator):
    """Remove material slots that are assigned to an object but not used by any face"""
    bl_idname  = "ahb.remove_unused_materials"
    bl_label   = "Remove Unused Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        total_removed = 0

        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH':
                continue

            mesh = obj.data
            if len(mesh.polygons) == 0:
                # No faces - remove ALL material slots
                while obj.material_slots:
                    obj.active_material_index = 0
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.material_slot_remove()
                    total_removed += 1
                continue

            # Find which material indices are actually used by faces
            used_indices = set()
            for poly in mesh.polygons:
                used_indices.add(poly.material_index)

            # Remove unused slots from the END first (to not shift indices)
            # Collect slots to remove
            slots_to_remove = []
            for idx in range(len(obj.material_slots)):
                if idx not in used_indices:
                    slots_to_remove.append(idx)

            if not slots_to_remove:
                continue

            # Set active object f
            bpy.context.view_layer.objects.active = obj

            # Remove from highest index first to avoid index shifting
            for idx in reversed(slots_to_remove):
                obj.active_material_index = idx
                bpy.ops.object.material_slot_remove()
                total_removed += 1

        msg = f"Removed {total_removed} unused material slot(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def _resolve_apply_mode(props):
    """Determine whether to wire as emission or base color."""
    if props.apply_mode != 'AUTO':
        return props.apply_mode
    # Auto: Combined/Diffuse/AO bakes contain lighting → use Emission
    if props.bake_type in ('COMBINED', 'DIFFUSE', 'AO', 'ENVIRONMENT', 'SHADOW'):
        return 'EMISSION'
    return 'BASE_COLOR'


def _find_output_node(nodes):
    """Find the Material Output node."""
    for n in nodes:
        if n.type == 'OUTPUT_MATERIAL' and n.is_active_output:
            return n
    # Fallback: any output node
    for n in nodes:
        if n.type == 'OUTPUT_MATERIAL':
            return n
    return None


def _find_principled_bsdf(nodes):
    """Find the first Principled BSDF node."""
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def _apply_baked_to_material(mat_name, baked_img, mode, keep_original, uv_layer_name):
    """Wire a baked image into a material's shader graph.

    If keep_original is True:
        Mutes all existing nodes (except Material Output), then adds
        the baked texture setup alongside them.
    If keep_original is False:
        Clears all old nodes and creates a fresh shader.

    mode='EMISSION':  UV Map → Image Texture → Emission → Material Output
    mode='BASE_COLOR': UV Map → Image Texture → Principled BSDF → Material Output

    A backup should already exist before calling this.
    Returns True on success."""
    mat = bpy.data.materials.get(mat_name)
    if not mat or not mat.use_nodes:
        return False

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Re-applying should replace the previous generated shader rather than
    # accumulating duplicate AHB nodes and links on every click.
    generated_names = {
        'AHB_UV_Map', 'AHB_Applied_Texture',
        'AHB_Emission', 'AHB_Principled',
    }
    for node in list(nodes):
        if node.name in generated_names:
            nodes.remove(node)

    if keep_original:
        # KEEP ORIGINAL: mute existing nodes, disconnect output
        output_node = _find_output_node(nodes)

        # Mute all non-output nodes and disconnect surface input
        for n in nodes:
            if n.type != 'OUTPUT_MATERIAL':
                n.mute = True

        # Remove links going into the output's Surface input
        if output_node:
            for link in list(links):
                if link.to_node == output_node and link.to_socket.name == 'Surface':
                    links.remove(link)
        else:
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.name     = 'Material Output'
            output_node.location = (300, 0)

        # Create UV Map node pointing to the bake UV layer
        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.name      = 'AHB_UV_Map'
        uv_node.label     = 'Bake UV'
        uv_node.uv_map    = uv_layer_name
        uv_node.location  = (output_node.location.x - 900, output_node.location.y)

        # Create Image Texture with the baked image
        tex_node = nodes.new('ShaderNodeTexImage')
        tex_node.name     = 'AHB_Applied_Texture'
        tex_node.label    = 'Baked Texture'
        tex_node.image    = baked_img
        tex_node.interpolation = 'Closest' if mode == 'BASE_COLOR' else 'Linear'
        tex_node.extension = 'CLIP'
        tex_node.location = (output_node.location.x - 700, output_node.location.y)

        # Connect UV Map → Image Texture
        links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])

        if mode == 'EMISSION':
            emit_node = nodes.new('ShaderNodeEmission')
            emit_node.name     = 'AHB_Emission'
            emit_node.label    = 'Baked Emission'
            emit_node.location = (output_node.location.x - 350, output_node.location.y)
            emit_node.inputs['Strength'].default_value = 1.0

            links.new(tex_node.outputs['Color'], emit_node.inputs['Color'])
            links.new(emit_node.outputs['Emission'], output_node.inputs['Surface'])

        elif mode == 'BASE_COLOR':
            principled = nodes.new('ShaderNodeBsdfPrincipled')
            principled.name     = 'AHB_Principled'
            principled.location = (output_node.location.x - 350, output_node.location.y)

            links.new(tex_node.outputs['Color'],
                      principled.inputs['Base Color'])
            links.new(principled.outputs['BSDF'],
                      output_node.inputs['Surface'])

    else:
        # ── CLEAR everything — fresh clean shader ──────────────────────
        nodes.clear()

        # Create Material Output
        output = nodes.new('ShaderNodeOutputMaterial')
        output.name     = 'Material Output'
        output.location = (300, 0)

        # Create UV Map node pointing to the bake UV layer
        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.name     = 'AHB_UV_Map'
        uv_node.label    = 'Bake UV'
        uv_node.uv_map   = uv_layer_name
        uv_node.location = (-600, 0)

        # Create Image Texture with the baked image
        tex_node = nodes.new('ShaderNodeTexImage')
        tex_node.name     = 'AHB_Applied_Texture'
        tex_node.label    = 'Baked Texture'
        tex_node.image    = baked_img
        tex_node.interpolation = 'Closest' if mode == 'BASE_COLOR' else 'Linear'
        tex_node.extension = 'CLIP'
        tex_node.location = (-400, 0)

        # Connect UV Map → Image Texture
        links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])

        if mode == 'EMISSION':
            emit_node = nodes.new('ShaderNodeEmission')
            emit_node.name     = 'AHB_Emission'
            emit_node.label    = 'Baked Emission'
            emit_node.location = (-50, 0)
            emit_node.inputs['Strength'].default_value = 1.0

            links.new(tex_node.outputs['Color'], emit_node.inputs['Color'])
            links.new(emit_node.outputs['Emission'], output.inputs['Surface'])

        elif mode == 'BASE_COLOR':
            principled = nodes.new('ShaderNodeBsdfPrincipled')
            principled.name     = 'AHB_Principled'
            principled.location = (-50, 0)

            links.new(tex_node.outputs['Color'],
                      principled.inputs['Base Color'])
            links.new(principled.outputs['BSDF'],
                      output.inputs['Surface'])

    return True


class AHB_OT_ApplyBakedTextures(Operator):
    """Wire baked images into material shader graphs so they appear in viewport/render"""
    bl_idname  = "ahb.apply_baked"
    bl_label   = "Apply Baked Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        _run_apply(obj_names, props, self.report)
        props.baked_view = True
        return {'FINISHED'}


class AHB_OT_ToggleBakedView(Operator):
    """Toggle scoped materials between their original and baked shaders."""
    bl_idname = "ahb.toggle_baked_view"
    bl_label = "Toggle Baked / Original"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        if _scope_has_material_backups(obj_names):
            restored = _restore_material_backups(obj_names)
            props.baked_view = False
            props.status_text = f"Showing original materials ({restored} restored)."
            self.report({'INFO'}, props.status_text)
        else:
            applied, skipped = _run_apply(obj_names, props, self.report)
            props.baked_view = True
            if applied == 0 and skipped:
                return {'CANCELLED'}
        return {'FINISHED'}


def _group_objects_by_tile(obj_names, props, report):
    """Move objects into collections named by their tile assignment."""
    if props.image_mode != 'AUTO_TILES':
        report({'WARNING'}, "Group to Collections only works in Auto Tiles mode.")
        return 0

    predicted_tiles = _distribute_tiles(obj_names, props.tile_count)
    predicted_col_names = {}
    for tile_idx, tile_objs in enumerate(predicted_tiles):
        col_name = f"{props.output_prefix}Tile_{tile_idx + 1:02d}"
        for o in tile_objs:
            predicted_col_names[o] = col_name

    moved = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj: continue

        col_name = None
        actual_texture = obj.get("ahb_baked_texture")
        if actual_texture:
            import re as _re
            match = _re.search(r"Tile_(\d+)", str(actual_texture), _re.IGNORECASE)
            if match:
                idx = int(match.group(1))
                col_name = f"{props.output_prefix}Tile_{idx:02d}"

        if not col_name:
            col_name = predicted_col_names.get(obj.name)

        if not col_name: continue

        # Ensure object remembers this if previewing before bake
        if not actual_texture:
            obj["ahb_baked_texture"] = col_name

        col = bpy.data.collections.get(col_name)
        if not col:
            col = bpy.data.collections.new(col_name)
            bpy.context.scene.collection.children.link(col)

        # Link to tile collection if not already there
        if obj.name not in col.objects:
            col.objects.link(obj)
        # Unlink from all other collections
        for old_col in list(obj.users_collection):
            if old_col != col:
                old_col.objects.unlink(obj)
        moved += 1

    return moved


class AHB_OT_CreateAtlasCollections(Operator):
    """Create missing collection atlas containers in Scene Collection"""
    bl_idname = "ahb.create_atlas_collections"
    bl_label = "Create Atlas Collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        _normalize_core_names(props)
        created = 0
        for atlas_number in range(1, props.collection_atlas_count + 1):
            col_name = _get_collection_atlas_name(props, atlas_number)
            col = bpy.data.collections.get(col_name)
            if not col:
                col = bpy.data.collections.new(col_name)
                context.scene.collection.children.link(col)
                created += 1
        if created:
            self.report({'INFO'}, f"Created {created} atlas collection container(s).")
        else:
            self.report({'INFO'}, "All atlas collection containers already exist.")
        return {'FINISHED'}


class AHB_OT_GroupToCollections(Operator):
    """Move objects into tile-based collections (e.g. Tile_01, Tile_02)"""
    bl_idname  = "ahb.group_to_collections"
    bl_label   = "Group to Tile Collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        moved = _group_objects_by_tile(obj_names, props, self.report)
        if moved:
            msg = f"Grouped {moved} object(s) into {props.tile_count} tile collection(s)."
            props.status_text = msg
            self.report({'INFO'}, msg)
        return {'FINISHED'} if moved else {'CANCELLED'}


class AHB_OT_RenameObjects(Operator):
    """Rename objects by appending their baked texture name (e.g. Cube -> Cube_bake_tile_01)"""
    bl_idname  = "ahb.rename_objects"
    bl_label   = "Rename Objects by Texture"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        renamed = _rename_objects_by_texture(obj_names, props, self.report)
        if renamed:
            msg = f"Renamed {renamed} object(s) with texture suffixes."
            props.status_text = msg
            self.report({'INFO'}, msg)
            return {'FINISHED'}
        else:
            self.report({'INFO'}, "No objects needed renaming.")
            return {'CANCELLED'}


def _restore_material_backups(obj_names):
    """Swap scoped materials back to their pre-apply backup datablocks."""
    replacements = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            backup = bpy.data.materials.get(f"{mat.name}_AHB_backup")
            if backup:
                replacements[mat] = backup

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            if slot.material in replacements:
                slot.material = replacements[slot.material]

    restored = 0
    for modified, backup in replacements.items():
        original_name = modified.name
        modified.name = f"{original_name}_AHB_modified"
        backup.name = original_name
        if modified.users == 0:
            bpy.data.materials.remove(modified)
        restored += 1
    _restore_image_uv_backups(obj_names)
    return restored


class AHB_OT_RestoreOriginalMaterials(Operator):
    """Restore materials to their pre-bake state from backups"""
    bl_idname  = "ahb.restore_materials"
    bl_label   = "Restore Original Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = _get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        restored = _restore_material_backups(obj_names)

        msg = f"Restored {restored} material(s) to original."
        props.baked_view = False
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  UI Drawing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _draw_ui(layout, props):
    """Shared draw function for both the N-panel and the Properties panel."""

    # ── 1. Status Strip ────────────────────────────────────────────────────
    status_box = layout.box()
    row = status_box.row()
    icon = 'CHECKMARK' if '✓' in props.status_text else 'INFO'
    if 'fail' in props.status_text.lower() or 'error' in props.status_text.lower():
        icon = 'ERROR'
    row.label(text=f"  {props.status_text}", icon=icon)

    layout.separator()

    # ── 2. Quick Bake Button ───────────────────────────────────────────────
    r = layout.row()
    r.scale_y = 1.8
    r.operator("ahb.quick_bake", text="⚡ Quick Bake (Full Pipeline)",
               icon='RENDER_STILL')
    row = layout.row()
    row.operator("ahb.rebake_selected",
                 text="Rebake Active in Existing Pack", icon='REC')

    layout.separator()

    # ── 3. Target & Type ───────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Target & Type", icon='OBJECT_DATA')
    box.prop(props, "bake_scope", text="Scope")
    box.prop(props, "bake_type", text="Type")
    if props.bake_type in _PASS_FILTER_TYPES:
        row = box.row(align=True)
        row.prop(props, "use_pass_direct",   toggle=True)
        row.prop(props, "use_pass_indirect", toggle=True)
        row.prop(props, "use_pass_color",    toggle=True)

    # ── 4. Image Settings ─────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Image Settings", icon='IMAGE_DATA')
    box.prop(props, "image_mode", text="Mode")
    if props.image_mode == 'AUTO_TILES':
        box.prop(props, "tile_count")
    elif props.image_mode == 'COLLECTION_ATLASES':
        box.prop(props, "collection_atlas_prefix", text="Collection Prefix")
        box.prop(props, "collection_atlas_count", text="Atlas Count")
        box.operator("ahb.create_atlas_collections",
                     text="Create Missing Atlas Collections",
                     icon='OUTLINER_COLLECTION')
    box.prop(props, "resolution", text="Resolution")
    if props.resolution == 'CUSTOM':
        row = box.row(align=True)
        row.prop(props, "custom_res_x", text="W")
        row.prop(props, "custom_res_y", text="H")
    box.prop(props, "image_format", text="Format")
    if props.image_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER'):
        box.prop(props, "exr_codec", text="EXR Codec")
    box.prop(props, "color_mode",     text="Channels")
    box.prop(props, "use_hdr_float")

    # ── 5. UV Settings ─────────────────────────────────────────────────────
    box = layout.box()
    box.label(text="UV Settings", icon='UV')
    box.prop(props, "auto_create_uv")
    if props.auto_create_uv:
        box.prop(props, "uv_layer_name", text="Layer Name")
        row = box.row()
        row.prop(props, "remove_old_uvs")
        sub_row = box.row(align=True)
        sub_row.operator("ahb.delete_old_uv_maps", text="Delete Old UV Maps", icon='TRASH')
        sub_row.operator("ahb.remove_all_uv_maps", text="Remove All UVs", icon='CANCEL')
        box.prop(props, "uv_mode", text="UV Mode")
        if props.uv_mode == 'SMART':
            col = box.column(align=True)
            col.prop(props, "smart_uv_angle", text="Angle Limit")
        elif props.uv_mode == 'AUTO_SEAM':
            col = box.column(align=True)
            col.prop(props, "auto_seam_angle", text="Seam Angle")
            sub = box.column()
            sub.scale_y = 0.8
            sub.label(text="Lower = more cuts. Higher = bigger islands.")
        elif props.uv_mode == 'LIGHTMAP':
            col = box.column(align=True)
            col.prop(props, "lightmap_quality", text="Quality")

        # Pack settings
        box.separator()
        box.prop(props, "pack_enabled")
        if props.pack_enabled:
            box.prop(props, "pack_engine")

            box.separator()
            box.prop(props, "pack_world_scale")
            if props.pack_world_scale:
                sub = box.column()
                sub.scale_y = 0.8
                sub.label(text="Proportional: bigger faces → more UV space.")
            box.separator()
            box.prop(props, "pack_image_boost", text="Image Tex Boost")
            if props.pack_image_boost > 1.01:
                sub = box.column()
                sub.scale_y = 0.8
                sub.label(text=f"Image-textured materials scaled {props.pack_image_boost:.1f}× up.")
            box.separator()
            col = box.column(align=True)
            col.prop(props, "pack_margin_px", text="Margin (pixels)")

            if props.pack_engine == 'BLENDER':
                col.prop(props, "pack_iterations", text="Quality Iterations")
                box.separator()
                box.prop(props, "pack_rotate")
                if props.pack_rotate:
                    box.prop(props, "pack_rotation_step", text="Rotation Step")
                box.prop(props, "pack_shape_method", text="Shape Method")
    # Preview UV and Renew Auto Seams buttons (Always Available)
    box.separator()
    if props.uv_mode == 'AUTO_SEAM':
        box.prop(props, "renew_auto_seams_on_preview")
    row = box.row(align=True)
    row.operator("ahb.preview_uv", text="Preview UV Layout", icon='UV')
    row.operator("ahb.renew_auto_seams", text="Renew Auto Seams", icon='EDGE_SEAM')

    # ── 6. Bake Settings & Quality ──────────────────────────────────────────
    box = layout.box()
    box.label(text="Bake Settings & Quality", icon='RENDER_RESULT')
    col = box.column(align=True)
    col.prop(props, "samples")
    col.prop(props, "margin")
    col.prop(props, "margin_type", text="Margin Type")
    col.prop(props, "clear_bake")
    col.prop(props, "unlink_materials")
    box.separator()
    box.prop(props, "auto_switch_cycles")
    box.prop(props, "compute_device", expand=True)
    if props.compute_device == 'GPU':
        box.prop(props, "gpu_backend", text="Backend")
    status = box.column()
    status.scale_y = 0.8
    status.label(text=f"Active: {props.device_status}", icon='INFO')

    # ── Selected to Active ─────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Selected to Active", icon='CON_FOLLOWPATH')
    box.prop(props, "use_selected_to_active")
    if props.use_selected_to_active:
        col = box.column(align=True)
        col.prop(props, "cage_extrusion")
        col.prop(props, "max_ray_distance")

    # ── 7. Output ──────────────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Output", icon='FILE_FOLDER')
    box.prop(props, "output_dir",    text="Directory")
    box.prop(props, "output_prefix", text="Prefix")
    box.prop(props, "auto_save")

    # ── 8. Apply Textures ──────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Apply Baked Textures", icon='NODE_MATERIAL')
    row = box.row(align=True)
    row.operator("ahb.toggle_baked_view",
                 text=("Show Original Materials" if props.baked_view
                       else "Show Baked Textures"),
                 icon=('LOOP_BACK' if props.baked_view else 'CHECKMARK'))
    box.prop(props, "apply_mode", text="Mode")
    box.prop(props, "apply_keep_original_nodes")
    row = box.row(align=True)
    row.operator("ahb.apply_baked",
                 text="Apply to Materials", icon='CHECKMARK')
    row = box.row(align=True)
    row.operator("ahb.rebake_selected",
                 text="Rebake Active in Existing Pack", icon='REC')
    row = box.row(align=True)
    row.operator("ahb.restore_materials",
                 text="Restore Originals", icon='LOOP_BACK')
    if props.image_mode == 'AUTO_TILES':
        row = box.row(align=True)
        row.operator("ahb.group_to_collections",
                     text="Group to Tile Collections", icon='OUTLINER_COLLECTION')

    # Utility: Rename objects
    row = box.row(align=True)
    row.operator("ahb.rename_objects",
                 text="Rename Objects by Texture", icon='SORTALPHA')

    layout.separator()

    # ── 9. Cleanup ─────────────────────────────────────────────────────────
    box = layout.box()
    box.label(text="Cleanup", icon='TRASH')
    row = box.row(align=True)
    row.operator("ahb.clear_bake_nodes", text="Remove Bake Nodes",  icon='TRASH')
    row.operator("ahb.cleanup_images",   text="Remove Bake Images", icon='IMAGE_DATA')
    row = box.row(align=True)
    row.operator("ahb.remove_unused_materials",
                 text="Remove Unused Materials", icon='MATERIAL')
    box.separator()
    box.prop(props, "image_node_name", text="Node Tag")
    box.prop(props, "auto_rename_objects")
    box.prop(props, "auto_cleanup_nodes")

    layout.separator()

    # ── 10. Manual Steps ───────────────────────────────────────────────────

    # ── 10. JSON Export/Import ─────────────────────────────────────────────
    box = layout.box()
    box.label(text="Export/Import JSON", icon='FILE_SCRIPT')
    row = box.row(align=True)
    row.operator("ahb.export_materials_json", text="Export JSON", icon='EXPORT')
    row.operator("ahb.import_materials_json", text="Import JSON", icon='IMPORT')

    layout.separator()

    # ── 11. Manual Steps ───────────────────────────────────────────────────
    col = layout.column(align=True)
    col.label(text="Manual Steps:", icon='TOOL_SETTINGS')
    col.operator("ahb.setup_materials", text="1 · Setup Nodes",   icon='NODETREE')
    col.operator("ahb.bake_all",        text="2 · Bake",          icon='RENDER_STILL')
    col.operator("ahb.save_images",     text="3 · Save Images",   icon='FILE_TICK')

    layout.separator()

    # ── Re-Pack button ─────────────────────────────────────────────────────
    row = layout.row()
    row.scale_y = 1.3
    row.operator("ahb.pack_islands",
                 text="Re-Pack Islands Now", icon='PACKAGE')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Panels
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AHB_PT_Sidebar(Panel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'HDR Baker'
    bl_label       = 'Auto HDR Baker'
    bl_idname      = 'AHB_PT_Sidebar'

    def draw(self, context):
        _draw_ui(self.layout, context.scene.ahb_props)


class AHB_PT_RenderProps(Panel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'render'
    bl_label       = 'Auto HDR Baker'
    bl_idname      = 'AHB_PT_RenderProps'

    def draw(self, context):
        _draw_ui(self.layout, context.scene.ahb_props)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Registration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_classes = [
    AHB_Properties,
    AHB_OT_ExportMaterialsJSON,
    AHB_OT_ImportMaterialsJSON,
    AHB_OT_SetupMaterials,
    AHB_OT_BakeAll,
    AHB_OT_RebakeSelected,
    AHB_OT_ClearBakeNodes,
    AHB_OT_DeleteOldUVMaps,
    AHB_OT_RemoveAllUVMaps,
    AHB_OT_SaveImages,
    AHB_OT_QuickBake,
    AHB_OT_RenewAutoSeams,
    AHB_OT_PreviewUV,
    AHB_OT_PackIslands,
    AHB_OT_CleanupImages,
    AHB_OT_RemoveUnusedMaterials,
    AHB_OT_ApplyBakedTextures,
    AHB_OT_ToggleBakedView,
    AHB_OT_CreateAtlasCollections,
    AHB_OT_GroupToCollections,
    AHB_OT_RenameObjects,
    AHB_OT_RestoreOriginalMaterials,
    AHB_PT_Sidebar,
    AHB_PT_RenderProps,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ahb_props = bpy.props.PointerProperty(type=AHB_Properties)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'ahb_props'):
        del bpy.types.Scene.ahb_props


if __name__ == "__main__":
    register()
