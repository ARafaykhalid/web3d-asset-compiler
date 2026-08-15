"""
Property definitions for Three.js Exporter within Web3D Asset Compiler.
"""

import os
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


def _get_quantize_positions(props):
    return bool(
        props.get(
            "quantize_positions",
            props.get("quantize_vectors", True),
        )
    )


def _set_quantize_positions(props, value):
    props["quantize_positions"] = bool(value)


def _get_quantize_scales(props):
    return bool(
        props.get(
            "quantize_scales",
            props.get("quantize_vectors", True),
        )
    )


def _set_quantize_scales(props, value):
    props["quantize_scales"] = bool(value)


class TJS_Properties(PropertyGroup):
    portfolio_one_click: BoolProperty(
        name="Portfolio One-Click Export",
        description="Export character.glb, every current Action, manifest, and binary files directly into output folder",
        default=False,
    )
    output_dir: StringProperty(
        name="Output Directory",
        subtype="DIR_PATH",
        default="//web3d_output",
    )
    base_name: StringProperty(
        name="Model Filename",
        default="character.glb",
    )
    export_scope: EnumProperty(
        name="Scope",
        items=[
            ("SELECTED", "Selected", "Export selected objects and required rigs"),
            ("SCENE", "Entire Scene", "Export the complete scene"),
        ],
        default="SELECTED",
    )
    animation_mode: EnumProperty(
        name="Animations",
        items=[
            ("ACTIONS", "All Actions", "Export compatible actions as separate clips"),
            ("ACTIVE_ACTIONS", "Active Actions", "Export active actions only"),
            ("NLA_TRACKS", "NLA Tracks", "Export non-muted NLA tracks as clips"),
            ("SCENE", "Scene Timeline", "Export the scene timeline as one animation"),
        ],
        default="ACTIONS",
    )
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Export evaluated mesh with visible modifiers applied; keep disabled when editable shape keys must be preserved",
        default=False,
    )
    include_materials: BoolProperty(name="Include Materials", default=True)
    include_morphs: BoolProperty(name="Include Shape Keys", default=True)
    include_textures: BoolProperty(name="Include Textures", default=True)
    image_format: EnumProperty(
        name="Image Format",
        items=[
            ("AUTO", "Automatic", "Keep compatible source formats"),
            ("JPEG", "JPEG", "Convert exported images to JPEG"),
            ("WEBP", "WebP", "Convert exported images to WebP"),
            ("NONE", "No Images", "Do not export image data"),
        ],
        default="AUTO",
    )
    texture_quality: IntProperty(
        name="Image Quality", default=85, min=0, max=100,
    )
    include_vertex_colors: BoolProperty(name="Include Vertex Colors", default=True)
    export_tangents: BoolProperty(name="Export Tangents", default=False)
    custom_properties: BoolProperty(name="Custom Properties", default=False)
    compression: EnumProperty(
        name="Mesh Compression",
        items=[
            ("NONE", "None", "Export uncompressed mesh data"),
            ("DRACO", "Draco", "Compress final GLB mesh data with Draco"),
        ],
        default="NONE",
    )
    draco_level: IntProperty(
        name="Compression Level", default=6, min=0, max=10,
    )
    draco_position: IntProperty(
        name="Position Bits", default=14, min=0, max=30,
    )
    draco_normal: IntProperty(
        name="Normal Bits", default=10, min=0, max=30,
    )
    draco_texcoord: IntProperty(
        name="UV Bits", default=12, min=0, max=30,
    )
    draco_color: IntProperty(
        name="Color Bits", default=10, min=0, max=30,
    )
    draco_generic: IntProperty(
        name="Generic Bits", default=12, min=0, max=30,
    )
    position_tolerance: FloatProperty(
        name="Position Error", default=0.0001, min=0.0, precision=6,
    )
    rotation_tolerance: FloatProperty(
        name="Rotation Error (deg)", default=0.05, min=0.0, precision=4,
    )
    scale_tolerance: FloatProperty(
        name="Scale Error", default=0.0001, min=0.0, precision=6,
    )
    morph_tolerance: FloatProperty(
        name="Morph Error", default=0.0005, min=0.0, precision=6,
    )
    keyframe_reduction: BoolProperty(name="Reduce Keyframes", default=True)
    remove_static_tracks: BoolProperty(name="Remove Rest-Pose Tracks", default=True)
    enable_animation_quantization: BoolProperty(name="Quantize Animation Data", default=True)
    quantize_quaternions: BoolProperty(name="Int16 Quaternions", default=True)
    quantize_positions: BoolProperty(
        name="Uint16 Positions",
        get=_get_quantize_positions,
        set=_set_quantize_positions,
    )
    quantize_scales: BoolProperty(
        name="Uint16 Scales",
        get=_get_quantize_scales,
        set=_set_quantize_scales,
    )
    quantize_morphs: BoolProperty(name="Uint16 Morph Values", default=True)
    export_all_actions: BoolProperty(name="Export All Actions", default=True)
    exclude_actions: StringProperty(
        name="Exclude Actions",
        description="Comma-separated Action names to skip",
        default="",
    )
    decimal_precision: IntProperty(
        name="Decimal Precision", default=5, min=3, max=9,
    )
    export_format: EnumProperty(
        name="Export Format",
        items=[
            ("GLB", ".glb (Binary)", "Single self-contained binary file"),
            ("GLTF_SEPARATE", ".gltf (Separate)", "gltf + .bin + textures"),
            ("GLTF_EMBEDDED", ".gltf (Embedded)", "gltf with inline base64 data"),
        ],
        default="GLB",
    )
    export_materials_mode: EnumProperty(
        name="Materials Mode",
        items=[
            ("EXPORT", "Export Materials", "Export PBR materials and textures"),
            ("PLACEHOLDER", "Placeholder", "Export material slots only"),
            ("NONE", "No Materials", "Do not export materials"),
        ],
        default="EXPORT",
    )
    export_cameras: BoolProperty(name="Include Cameras", default=False, description="Export perspective/orthographic cameras")
    export_lights: BoolProperty(name="Include Lights", default=False, description="Export KHR_lights_punctual (Point, Sun, Spot)")
    export_skins: BoolProperty(name="Include Armatures & Skins", default=True, description="Export bone armatures and skin weights")
    export_all_influences: BoolProperty(name="Allow >4 Bone Influences", default=False, description="Allow more than 4 bone weights per vertex")
    export_yup: BoolProperty(name="Y-Up Orientation", default=True, description="Export using WebGL standard Y-Up coordinate space")
    export_attributes: BoolProperty(name="Export Mesh Attributes", default=False, description="Export custom geometry mesh attributes")
    export_frame_range: BoolProperty(name="Use Scene Frame Range", default=False, description="Limit export to scene frame start/end")
    export_frame_step: IntProperty(name="Frame Step", default=1, min=1, max=100, description="Step interval between keyframe samples")
    export_reset_pose_at_frame_zero: BoolProperty(name="Reset Rest Pose at Frame 0", default=False, description="Reset armature to rest pose at frame 0")
    export_nla_strips: BoolProperty(name="Export NLA Track Strips", default=True, description="Export NLA tracks and combined strips")
    sampling_fps: IntProperty(
        name="Sampling FPS", default=30, min=1, max=240,
    )
    sample_baked: BoolProperty(
        name="Sample Evaluated Pose",
        description="Bake constraints and final dependency-graph pose",
        default=True,
    )
    status: StringProperty(default="Ready")
