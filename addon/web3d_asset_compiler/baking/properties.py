"""
Property definitions for Auto HDR Baker within Web3D Asset Compiler.
"""

import bpy
from bpy.props import (
    BoolProperty,
    IntProperty,
    FloatProperty,
    StringProperty,
    EnumProperty,
    PointerProperty,
)
from bpy.types import PropertyGroup

NODE_TAG = "AHB_BakeTarget"
IMAGE_UV_BACKUP_LAYER = "AHB_ImageUV_Backup"
IMAGE_UV_SOURCE_PROP = "ahb_image_uv_source"
IMAGE_UV_BACKUP_PROP = "ahb_image_uv_backup"

PASS_FILTER_TYPES = {'COMBINED', 'DIFFUSE', 'GLOSSY', 'TRANSMISSION'}

LINEAR_COLORSPACES = ['Linear Rec.709', 'Linear', 'scene_linear', 'Non-Color', 'Raw']
DATA_COLORSPACES = ['Non-Color', 'Raw', 'Linear Rec.709', 'Linear']
SRGB_COLORSPACES = ['sRGB', 'Filmic sRGB', 'AgX Base sRGB']

EXT_MAP = {
    'OPEN_EXR': '.exr',
    'OPEN_EXR_MULTILAYER': '.exr',
    'HDR': '.hdr',
    'PNG': '.png',
    'JPEG': '.jpg',
    'TIFF': '.tiff',
}


class AHB_Properties(PropertyGroup):

    # Scope
    bake_scope: EnumProperty(
        name="Scope",
        items=[
            ('SELECTED', 'All Selected',  'Bake every selected mesh'),
            ('ACTIVE',   'Active Only',   'Bake only the active object'),
            ('VISIBLE',  'All Visible',   'Bake all visible meshes in scene'),
        ],
        default='SELECTED',
    )

    # Bake Type
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

    # Passes
    use_pass_direct:   BoolProperty(name="Direct",   default=True)
    use_pass_indirect: BoolProperty(name="Indirect", default=True)
    use_pass_color:    BoolProperty(name="Color",    default=True)

    # Resolution
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

    # Image Format
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

    # Image Mode
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

    # UV Settings
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
    cube_size: FloatProperty(name="Cube Size", default=1.0, min=0.001)

    renew_auto_seams_on_preview: BoolProperty(
        name="Renew Auto Seams on Preview",
        default=True,
        description="Clear old seams and recalculate fresh auto seams when previewing or setting up UVs",
    )
    auto_seam_angle: FloatProperty(
        name="Seam Angle",
        default=30.0, min=1.0, max=89.0,
        description="Edges sharper than this angle become seams.",
    )
    lightmap_quality: IntProperty(name="Quality", default=12, min=1, max=48)
    lightmap_margin:        FloatProperty(name="Lightmap Margin", default=0.1, min=0, max=1)

    # Island Packing
    pack_enabled: BoolProperty(name="Pack Islands After Unwrap", default=True)
    pack_engine: EnumProperty(
        name="Pack Engine",
        items=[
            ('BLENDER', 'Blender Native', 'Use built-in Blender packing'),
            ('UVP3',    'UVPackmaster 3', 'Use UVPackmaster 3 (Must be installed)'),
            ('UVP2',    'UVPackmaster 2', 'Use UVPackmaster 2 (Must be installed)'),
        ],
        default='BLENDER',
    )
    pack_rotate: BoolProperty(name="Allow Rotation", default=True)
    pack_rotation_step: EnumProperty(
        name="Rotation Mode",
        items=[
            ('ANY',  'Any Angle',  'Fully free rotation for maximum density'),
            ('AXIS_ALIGNED', 'Axis Aligned', 'Align islands to their principal axes'),
            ('NONE', 'No Rotation', 'Keep original orientation'),
        ],
        default='ANY',
    )
    pack_margin_px: IntProperty(name="Pack Margin (px)", default=16, min=0, max=64)
    pack_world_scale: BoolProperty(name="World-Space Proportional", default=True)
    pack_image_boost: FloatProperty(name="Image Texture Boost", default=1.0, min=1.0, max=4.0)
    pack_iterations: IntProperty(name="Pack Quality Iterations", default=3, min=1, max=32)
    pack_nest_holes: BoolProperty(name="Nest Inside Hollow Centers", default=False)
    pack_shape_method: EnumProperty(
        name="Shape Method",
        items=[
            ('CONVEX',  'Convex',   'Convex hull (tight packing, NO overlaps)'),
            ('CONCAVE', 'Concave',  'Accurate concave hull'),
            ('AABB',    'Bounding Box', 'Axis-aligned bounding box'),
        ],
        default='CONVEX',
    )
    pack_scale_islands: BoolProperty(name="Scale Islands to Fit Gaps", default=False)
    pack_stack_identical: BoolProperty(name="Stack Identical Islands", default=False)

    # Node tag
    image_node_name: StringProperty(name="Node Tag", default=NODE_TAG)

    # Quality
    samples:  IntProperty(name="Samples", default=128, min=1, max=4096)
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
    unlink_materials: BoolProperty(name="Make Materials Unique", default=True)

    # Engine / GPU
    auto_switch_cycles: BoolProperty(name="Auto Switch to Cycles", default=True)
    compute_device: EnumProperty(
        name="Compute Device",
        items=[
            ('GPU', 'GPU', 'Bake with a supported graphics card'),
            ('CPU', 'CPU', 'Bake with the processor'),
        ],
        default='GPU',
    )
    gpu_backend: EnumProperty(
        name="GPU Backend",
        items=[
            ('AUTO',   'Auto',   'Use configured backend, or try OptiX, CUDA, HIP, oneAPI, Metal'),
            ('OPTIX',  'OptiX',  'NVIDIA RTX/GTX'),
            ('CUDA',   'CUDA',   'NVIDIA CUDA'),
            ('HIP',    'HIP',    'AMD GPU'),
            ('ONEAPI', 'oneAPI', 'Intel GPU'),
            ('METAL',  'Metal',  'Apple GPU'),
        ],
        default='AUTO',
    )
    device_status: StringProperty(name="Active Device", default="Configured when baking starts")

    # Selected-to-Active & Cage
    use_selected_to_active: BoolProperty(name="Selected to Active", default=False)
    cage_extrusion:         FloatProperty(name="Cage Extrusion", default=0.02, min=0, max=1)
    max_ray_distance:       FloatProperty(name="Max Ray Distance", default=0.0, min=0, max=10)
    use_cage:               BoolProperty(name="Use Custom Cage", default=False, description="Use a custom cage object for distance casting")
    cage_object:            PointerProperty(type=bpy.types.Object, name="Cage Object", description="Custom cage mesh object")

    # Normal Map Controls
    denoise_bake: BoolProperty(
        name="Denoise Baked Textures",
        default=False,
        description="Reserved for compatibility; Cycles does not denoise bake targets",
        options={'HIDDEN'},
    )
    normal_space: EnumProperty(
        name="Normal Space",
        items=[
            ('TANGENT', 'Tangent Space', 'Standard tangent space normal map for PBR shaders'),
            ('OBJECT',  'Object Space',  'Object space normal map'),
        ],
        default='TANGENT',
    )
    normal_r: EnumProperty(name="Axis X", items=[('POS_X', '+X', ''), ('NEG_X', '-X', '')], default='POS_X')
    normal_g: EnumProperty(name="Axis Y", items=[('POS_Y', '+Y', ''), ('NEG_Y', '-Y', '')], default='POS_Y')
    normal_b: EnumProperty(name="Axis Z", items=[('POS_Z', '+Z', ''), ('NEG_Z', '-Z', '')], default='POS_Z')
    multires_bake: BoolProperty(
        name="Bake from Multires",
        default=False,
        description="Bake a Normal map from an object's Multiresolution modifier",
    )
    bake_target: EnumProperty(
        name="Target Output",
        items=[
            ('IMAGE_TEXTURES', 'Image Textures', 'Bake to image textures'),
            ('VERTEX_COLORS', 'Color Attributes', 'Bake directly to mesh vertex colors'),
        ],
        default='IMAGE_TEXTURES',
    )
    save_mode: EnumProperty(
        name="Save Mode",
        items=[
            ('EXTERNAL', 'Save Files to Disk', 'Save baked images directly to output directory'),
            ('INTERNAL', 'Keep in Blend File', 'Keep images internal to .blend file'),
        ],
        default='EXTERNAL',
    )

    # Output
    output_dir:    StringProperty(name="Output Directory", subtype='DIR_PATH', default="//baked/")
    output_prefix: StringProperty(name="Filename Prefix", default="bake_")
    auto_save:     BoolProperty(name="Auto Save After Bake", default=True)

    # Cleanup & Apply
    auto_cleanup_nodes: BoolProperty(name="Auto Clean Bake Nodes", default=True)
    baked_view: BoolProperty(name="Baked View", default=False)
    apply_mode: EnumProperty(
        name="Apply Mode",
        items=[
            ('AUTO',      'Auto (by Bake Type)', 'Emission for Combined/Diffuse, Base Color for others'),
            ('EMISSION',  'Emission',            'Use baked texture as emission'),
            ('BASE_COLOR','Base Color',           'Replace base color'),
        ],
        default='AUTO',
    )
    apply_keep_original_nodes: BoolProperty(name="Keep Original Nodes", default=True)

    status_text: StringProperty(default="Ready")
    auto_rename_objects: BoolProperty(name="Auto-Rename Objects", default=False)
