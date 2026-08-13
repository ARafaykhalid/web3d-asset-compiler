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
        name="GLB Filename",
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
    quantize_vectors: BoolProperty(name="Uint16 Position / Scale", default=True)
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
    sampling_fps: IntProperty(
        name="Sampling FPS", default=30, min=1, max=240,
    )
    sample_baked: BoolProperty(
        name="Sample Evaluated Pose",
        description="Bake constraints and final dependency-graph pose",
        default=True,
    )
    status: StringProperty(default="Ready")
