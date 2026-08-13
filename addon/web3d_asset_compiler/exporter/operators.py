"""
Operator classes for Three.js Exporter within Web3D Asset Compiler.
"""

import os
import re
import json
import importlib.util
import bpy
from bpy.types import Operator

from .character_exporter import export_character_glb
from .ts_generator import typescript_module


def resolve_output_dir(props):
    raw_path = props.output_dir.strip() or "//web3d_output"
    abs_path = bpy.path.abspath(raw_path)
    if not abs_path:
        abs_path = os.path.join(os.path.expanduser("~"), "web3d_output")
    return abs_path


class TJS_OT_ExportCharacterGLB(Operator):
    """Export animation-free character GLB model"""
    bl_idname = "tjs.export_character_glb"
    bl_label = "Export Base Character GLB"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.tjs_props
        output_dir = resolve_output_dir(props)
        os.makedirs(output_dir, exist_ok=True)
        filename = props.base_name.strip() or "character.glb"
        if not filename.lower().endswith(".glb"):
            filename += ".glb"

        filepath = os.path.join(output_dir, filename)
        try:
            export_character_glb(filepath, props, context)
            msg = f"Exported character GLB to {filepath}"
            props.status = msg
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        except Exception as exc:
            props.status = f"Character export failed: {exc}"
            self.report({"ERROR"}, props.status)
            return {"CANCELLED"}


class TJS_OT_ExportAnimations(Operator):
    """Create animation-free character.glb, animations/*.anim, and animation-manifest.json"""
    bl_idname = "tjs.export_animations"
    bl_label = "Export Character + Binary Animations"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.tjs_props
        wm = context.window_manager
        wm.progress_begin(0, 100)
        props.status = "Loading binary animation exporter…"

        try:
            # Delegate execution to full export runner if threejs_binary_animation_export script exists
            addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            module_path = os.path.join(
                os.path.dirname(addon_dir),
                "threejs_binary_animation_export.py",
            )

            output_directory = resolve_output_dir(props)
            os.makedirs(output_directory, exist_ok=True)
            filename = props.base_name.strip() or "character.glb"
            if not filename.lower().endswith(".glb"):
                filename += ".glb"

            if not os.path.isfile(module_path):
                filepath = os.path.join(output_directory, filename)
                export_character_glb(filepath, props, context)

                ts_filepath = os.path.join(output_directory, "model_controller.ts")
                with open(ts_filepath, "w", encoding="utf-8") as f:
                    f.write(typescript_module(filename, []))

                props.status = f"Exported {filename} and TS helper module."
                self.report({"INFO"}, props.status)
                return {"FINISHED"}

            spec = importlib.util.spec_from_file_location(
                "threejs_binary_animation_export_runtime", module_path
            )
            exporter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(exporter)

            excluded = {
                name.strip()
                for name in re.split(r"[,;\n]+", props.exclude_actions)
                if name.strip()
            }

            exporter.OUTPUT_DIRECTORY = output_directory
            exporter.GLB_FILENAME = filename
            exporter.EXPORT_ALL_ACTIONS = props.export_all_actions
            exporter.ACTIONS_TO_EXCLUDE = excluded
            exporter.DECIMAL_PRECISION = props.decimal_precision
            exporter.SAMPLING_FPS = props.sampling_fps
            exporter.SAMPLE_BAKED_ANIMATION = props.sample_baked
            exporter.APPLY_MODIFIERS = props.apply_modifiers
            exporter.EXPORT_MATERIALS = props.include_materials
            exporter.EXPORT_TEXTURES = props.include_textures
            exporter.TEXTURE_IMAGE_FORMAT = props.image_format
            exporter.TEXTURE_QUALITY = props.texture_quality
            exporter.EXPORT_MORPH_TARGETS = props.include_morphs
            exporter.EXPORT_VERTEX_COLORS = props.include_vertex_colors
            exporter.EXPORT_TANGENTS = props.export_tangents
            exporter.EXPORT_CUSTOM_PROPERTIES = props.custom_properties
            exporter.MESH_COMPRESSION = props.compression
            exporter.DRACO_COMPRESSION_LEVEL = props.draco_level
            exporter.DRACO_POSITION_QUANTIZATION = props.draco_position
            exporter.DRACO_NORMAL_QUANTIZATION = props.draco_normal
            exporter.DRACO_TEXCOORD_QUANTIZATION = props.draco_texcoord
            exporter.DRACO_COLOR_QUANTIZATION = props.draco_color
            exporter.DRACO_GENERIC_QUANTIZATION = props.draco_generic
            exporter.POSITION_TOLERANCE = props.position_tolerance
            exporter.ROTATION_TOLERANCE_DEGREES = props.rotation_tolerance
            exporter.SCALE_TOLERANCE = props.scale_tolerance
            exporter.MORPH_TOLERANCE = props.morph_tolerance
            exporter.ENABLE_KEYFRAME_REDUCTION = props.keyframe_reduction
            exporter.REMOVE_STATIC_TRACKS = props.remove_static_tracks
            exporter.ENABLE_QUANTIZATION = props.enable_animation_quantization
            exporter.QUANTIZE_QUATERNIONS = props.quantize_quaternions
            exporter.QUANTIZE_POSITIONS = props.quantize_vectors
            exporter.QUANTIZE_SCALES = props.quantize_vectors
            exporter.QUANTIZE_MORPHS = props.quantize_morphs

            wm.progress_update(1)
            props.status = "Exporting character and binary Actions…"
            result = exporter.export_character_and_animations()
            wm.progress_update(100)

            animation_count = len(result["animations"])
            props.status = f"Done: {animation_count} animation(s), {result['tracks']} tracks"
            self.report(
                {"INFO"},
                f"Exported character.glb and {animation_count} binary animation file(s).",
            )
            return {"FINISHED"}
        except Exception as exc:
            props.status = f"Export failed: {exc}"
            self.report({"ERROR"}, props.status)
            return {"CANCELLED"}
        finally:
            wm.progress_end()
