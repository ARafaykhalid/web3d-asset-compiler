"""Blender operators for model-only and decoupled character export."""

import os
import re

import bpy
from bpy.types import Operator

from .binary_exporter import export_character_and_animations
from .character_exporter import export_character_glb
from .ts_generator import WINDOWS_RESERVED


PORTFOLIO_OUTPUT_ENV = "WEB3D_PORTFOLIO_MODELS_DIR"


def _portfolio_output_dir():
    override = os.environ.get(PORTFOLIO_OUTPUT_ENV, "").strip()
    if override:
        return bpy.path.abspath(override)
    return os.path.join(
        os.path.expanduser("~"),
        "OneDrive",
        "Documents",
        "GitHub",
        "portfolio",
        "public",
        "models",
    )


def resolve_output_dir(props):
    if getattr(props, "portfolio_one_click", False):
        return os.path.abspath(_portfolio_output_dir())
    raw_path = props.output_dir.strip() or "//web3d_output"
    if raw_path.startswith("//") and not bpy.data.filepath:
        raise RuntimeError(
            "Save the .blend file before using a blend-relative output directory."
        )
    abs_path = bpy.path.abspath(raw_path)
    if not abs_path:
        abs_path = os.path.join(os.path.expanduser("~"), "web3d_output")
    return os.path.abspath(abs_path)


def _output_filename(props, *, force_glb=False):
    if getattr(props, "portfolio_one_click", False):
        return "character.glb"
    filename = props.base_name.strip() or "character.glb"
    if re.search(r'[<>:"/\\|?*#%\x00-\x1F]', filename):
        raise ValueError(
            "The export filename contains a reserved path or URL character."
        )
    if filename != filename.rstrip(" ."):
        raise ValueError("The export filename cannot end with a space or period.")

    export_format = "GLB" if force_glb else getattr(props, "export_format", "GLB")
    extension = ".glb" if export_format == "GLB" else ".gltf"
    stem, current_extension = os.path.splitext(filename)
    if current_extension.lower() not in {".glb", ".gltf"}:
        stem = filename
    stem = stem or "character"
    if stem.upper() in WINDOWS_RESERVED:
        raise ValueError(f"'{stem}' is a reserved filename on Windows.")
    return stem + extension


class TJS_OT_ExportCharacterGLB(Operator):
    """Export an animation-free model"""

    bl_idname = "tjs.export_character_glb"
    bl_label = "Export Model Only"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.tjs_props
        try:
            output_dir = resolve_output_dir(props)
            os.makedirs(output_dir, exist_ok=True)
            export_format = (
                "GLB"
                if props.portfolio_one_click
                else getattr(props, "export_format", "GLB")
            )
            filename = _output_filename(props)
            filepath = os.path.join(output_dir, filename)
            export_character_glb(
                filepath,
                props,
                context,
                export_format=export_format,
                scope="SCENE" if props.portfolio_one_click else None,
            )
            props.status = f"Exported animation-free model to {filepath}"
            self.report({"INFO"}, props.status)
            return {"FINISHED"}
        except Exception as exc:
            props.status = f"Model export failed: {exc}"
            self.report({"ERROR"}, props.status)
            return {"CANCELLED"}


class TJS_OT_ExportAnimations(Operator):
    """Create character.glb, compact animations, manifest, and TS modules"""

    bl_idname = "tjs.export_animations"
    bl_label = "Export Character + Binary Animations"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.tjs_props
        wm = context.window_manager
        wm.progress_begin(0, 100)
        props.status = "Preparing binary character export..."

        try:
            portfolio = getattr(props, "portfolio_one_click", False)
            if not portfolio and getattr(props, "export_format", "GLB") != "GLB":
                raise RuntimeError(
                    "Character + Binary Animations requires '.glb (Binary)'. "
                    "Use the model-only exporter for .gltf output."
                )

            output_directory = resolve_output_dir(props)
            os.makedirs(output_directory, exist_ok=True)
            filename = _output_filename(props, force_glb=True)

            def update_export_progress(value, message):
                wm.progress_update(value)
                props.status = message
                if context.area:
                    context.area.tag_redraw()

            result = export_character_and_animations(
                output_directory,
                filename,
                props,
                context,
                progress_callback=update_export_progress,
                force_all_actions=portfolio,
                force_character_scope=portfolio,
            )
            animation_count = len(result["animations"])
            if animation_count == 0:
                raise RuntimeError("No animation clips were exported.")

            props.status = (
                f"Done: {animation_count} animation(s), "
                f"{result['tracks']} tracks, {result['keyframes']} keys"
            )
            self.report(
                {"INFO"},
                f"Exported {filename} and {animation_count} binary animation file(s).",
            )
            return {"FINISHED"}
        except Exception as exc:
            props.status = f"Export failed: {exc}"
            self.report({"ERROR"}, props.status)
            return {"CANCELLED"}
        finally:
            wm.progress_end()
