"""
Character base GLB exporter without embedded animation tracks.
"""

import os
import bpy

from .quarantine import quarantine_unsafe_fcurves, restore_quarantined_fcurves


def collect_export_objects(context):
    """Include selected objects plus parents and armatures required by meshes."""
    result = set(context.selected_objects)
    pending = list(result)

    while pending:
        obj = pending.pop()
        if obj.parent and obj.parent not in result:
            result.add(obj.parent)
            pending.append(obj.parent)
        if obj.type == "MESH":
            for modifier in obj.modifiers:
                armature = modifier.object if modifier.type == "ARMATURE" else None
                if armature and armature not in result:
                    result.add(armature)
                    pending.append(armature)

    return sorted(result, key=lambda obj: obj.name)


def export_character_glb(filepath, props, context):
    """Export animation-free character GLB with optional Draco compression."""
    if props.export_scope == "SELECTED":
        export_objects = collect_export_objects(context)
        if not export_objects:
            raise RuntimeError("No objects selected to export.")
    else:
        export_objects = list(context.scene.objects)

    quarantine, records = quarantine_unsafe_fcurves()
    try:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        bpy.ops.object.select_all(action="DESELECT")
        for obj in export_objects:
            obj.hide_set(False)
            obj.select_set(True)

        if export_objects:
            context.view_layer.objects.active = export_objects[0]

        use_draco = props.compression == "DRACO"
        options = dict(
            filepath=filepath,
            export_format="GLB",
            use_selection=(props.export_scope == "SELECTED"),
            export_yup=True,
            export_apply=props.apply_modifiers,
            export_materials="EXPORT" if props.include_materials else "NONE",
            export_image_format=props.image_format,
            export_jpeg_quality=props.texture_quality,
            export_image_quality=props.texture_quality,
            export_vertex_color="MATERIAL" if props.include_vertex_colors else "NONE",
            export_all_vertex_colors=props.include_vertex_colors,
            export_tangents=props.export_tangents,
            export_extras=props.custom_properties,
            export_draco_mesh_compression_enable=use_draco,
            export_draco_mesh_compression_level=props.draco_level,
            export_draco_position_quantization=props.draco_position,
            export_draco_normal_quantization=props.draco_normal,
            export_draco_texcoord_quantization=props.draco_texcoord,
            export_draco_color_quantization=props.draco_color,
            export_draco_generic_quantization=props.draco_generic,
            export_skins=True,
            export_def_bones=False,
            export_rest_position_armature=True,
            export_leaf_bone=False,
            export_morph=props.include_morphs,
            export_animations=False,
            export_morph_animation=False,
        )

        result = bpy.ops.export_scene.gltf(**options)
        if "FINISHED" not in result:
            raise RuntimeError(f"GLB export failed: {filepath}")
    finally:
        restore_quarantined_fcurves(quarantine, records)

    return filepath
