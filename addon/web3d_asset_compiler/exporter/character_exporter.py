"""
Character and staging glTF export helpers.

The binary animation pipeline uses the same object selection and glTF options
as the standalone model exporter so the animation bindings match the final
asset exactly.
"""

import os
import shutil
import tempfile

import bpy

from .quarantine import quarantine_unsafe_fcurves, restore_quarantined_fcurves


def _uses_armature(obj, armature):
    if obj.type != "MESH":
        return False
    if obj.parent == armature and obj.parent_type in {"ARMATURE", "BONE"}:
        return True
    return any(
        modifier.type == "ARMATURE" and modifier.object == armature
        for modifier in obj.modifiers
    )


def collect_export_objects(context, scope=None):
    """Collect the requested objects plus parents and required rig objects."""
    scope = scope or getattr(context.scene.tjs_props, "export_scope", "SELECTED")
    if scope == "SCENE":
        return sorted(context.scene.objects, key=lambda obj: obj.name)

    result = set(context.selected_objects)
    explicitly_selected_armatures = {
        obj for obj in context.selected_objects if obj.type == "ARMATURE"
    }
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

        if obj in explicitly_selected_armatures:
            for candidate in context.scene.objects:
                if _uses_armature(candidate, obj) and candidate not in result:
                    result.add(candidate)
                    pending.append(candidate)

    return sorted(result, key=lambda obj: obj.name)


def _actions_for_export_objects(objects, include_morphs):
    data_blocks = []
    for obj in objects:
        data_blocks.append(obj)
        data = getattr(obj, "data", None)
        if data is not None:
            data_blocks.append(data)
        if include_morphs and obj.type == "MESH":
            shape_keys = getattr(data, "shape_keys", None)
            if shape_keys is not None:
                data_blocks.append(shape_keys)

    actions = set()
    for data_block in data_blocks:
        animation_data = getattr(data_block, "animation_data", None)
        if not animation_data:
            continue
        if animation_data.action:
            actions.add(animation_data.action)
        for track in animation_data.nla_tracks:
            if getattr(track, "mute", False):
                continue
            for strip in track.strips:
                if strip.action and not getattr(strip, "mute", False):
                    actions.add(strip.action)
    return sorted(actions, key=lambda action: action.name.casefold())


def _operator_properties():
    try:
        return {
            prop.identifier
            for prop in bpy.ops.export_scene.gltf.get_rna_type().properties
        }
    except (AttributeError, RuntimeError):
        return None


def _filtered_options(options, required=()):
    supported = _operator_properties()
    if supported is None:
        return options

    missing = [name for name in required if name not in supported]
    if missing:
        raise RuntimeError(
            "This Blender glTF exporter does not support: " + ", ".join(missing)
        )

    ignored = sorted(set(options) - supported)
    if ignored:
        print(
            "[Web3D Export] Ignoring unavailable glTF option(s): "
            + ", ".join(ignored)
        )
    return {key: value for key, value in options.items() if key in supported}


def _material_mode(props, staging):
    if staging or not getattr(props, "include_materials", True):
        return "NONE"
    return getattr(props, "export_materials_mode", "EXPORT")


def _image_format(props, staging):
    if (
        staging
        or not getattr(props, "include_materials", True)
        or not getattr(props, "include_textures", True)
    ):
        return "NONE"
    return getattr(props, "image_format", "AUTO")


def build_gltf_options(
    filepath,
    props,
    *,
    animations=False,
    export_format=None,
    animation_mode=None,
    frame_step=None,
    staging=False,
):
    """Build Blender glTF operator options from every applicable UI property."""
    export_format = export_format or getattr(props, "export_format", "GLB")
    animation_mode = animation_mode or getattr(props, "animation_mode", "ACTIONS")
    frame_step = frame_step or getattr(props, "export_frame_step", 1)
    use_draco = getattr(props, "compression", "NONE") == "DRACO" and not animations

    options = dict(
        filepath=filepath,
        export_format=export_format,
        use_selection=True,
        export_yup=getattr(props, "export_yup", True),
        export_apply=getattr(props, "apply_modifiers", False),
        export_materials=_material_mode(props, staging),
        export_image_format=_image_format(props, staging),
        export_jpeg_quality=getattr(props, "texture_quality", 85),
        export_image_quality=getattr(props, "texture_quality", 85),
        export_vertex_color=(
            "MATERIAL" if getattr(props, "include_vertex_colors", True) else "NONE"
        ),
        export_all_vertex_colors=getattr(props, "include_vertex_colors", True),
        export_tangents=getattr(props, "export_tangents", False),
        export_extras=getattr(props, "custom_properties", False),
        export_cameras=getattr(props, "export_cameras", False),
        export_lights=getattr(props, "export_lights", False),
        export_attributes=getattr(props, "export_attributes", False),
        export_draco_mesh_compression_enable=use_draco,
        export_draco_mesh_compression_level=getattr(props, "draco_level", 6),
        export_draco_position_quantization=getattr(props, "draco_position", 14),
        export_draco_normal_quantization=getattr(props, "draco_normal", 10),
        export_draco_texcoord_quantization=getattr(props, "draco_texcoord", 12),
        export_draco_color_quantization=getattr(props, "draco_color", 10),
        export_draco_generic_quantization=getattr(props, "draco_generic", 12),
        export_skins=getattr(props, "export_skins", True),
        export_all_influences=getattr(props, "export_all_influences", False),
        export_def_bones=False,
        export_rest_position_armature=True,
        export_leaf_bone=False,
        export_morph=getattr(props, "include_morphs", True),
        export_animations=animations,
        export_morph_animation=(
            animations and getattr(props, "include_morphs", True)
        ),
    )
    required = ["export_format"]

    if getattr(props, "export_attributes", False):
        required.append("export_attributes")

    if animations:
        options.update(
            export_animation_mode=animation_mode,
            export_force_sampling=getattr(props, "sample_baked", True),
            export_frame_step=max(1, int(frame_step)),
            export_frame_range=getattr(props, "export_frame_range", False),
            export_reset_pose_bones=getattr(
                props, "export_reset_pose_at_frame_zero", False
            ),
            export_nla_strips=getattr(props, "export_nla_strips", True),
            export_extra_animations=(
                animation_mode == "ACTIONS"
                and getattr(props, "export_nla_strips", True)
            ),
            export_optimize_animation_size=True,
            export_optimize_animation_keep_anim_armature=True,
            export_anim_single_armature=True,
            export_anim_slide_to_zero=True,
        )
        if animation_mode in {"ACTIONS", "ACTIVE_ACTIONS"}:
            options["export_merge_animation"] = "ACTION"

        required.append("export_animation_mode")
        if getattr(props, "export_frame_range", False):
            required.append("export_frame_range")
        if max(1, int(frame_step)) != 1:
            required.append("export_frame_step")
        if getattr(props, "export_reset_pose_at_frame_zero", False):
            required.append("export_reset_pose_bones")

    return _filtered_options(options, required)


def _snapshot_object_state(context, objects):
    active = context.view_layer.objects.active
    return {
        "active": active,
        "mode": active.mode if active else "OBJECT",
        "selected": list(context.selected_objects),
        "hidden": {
            obj: (obj.hide_get(), obj.hide_viewport)
            for obj in objects
        },
    }


def _ensure_object_mode(context):
    active = context.view_layer.objects.active
    if active and active.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def _restore_object_state(context, state):
    _ensure_object_mode(context)
    for obj, (hidden, hide_viewport) in state["hidden"].items():
        if obj.name not in bpy.data.objects:
            continue
        obj.hide_set(hidden)
        obj.hide_viewport = hide_viewport

    try:
        bpy.ops.object.select_all(action="DESELECT")
    except RuntimeError:
        pass
    for obj in state["selected"]:
        if obj.name not in bpy.data.objects:
            continue
        try:
            obj.select_set(True)
        except RuntimeError:
            pass

    active = state["active"]
    if active and active.name in bpy.data.objects:
        context.view_layer.objects.active = active
        if state["mode"] != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode=state["mode"])
            except RuntimeError:
                print(
                    f"[Web3D Export] Could not restore object mode {state['mode']}."
                )


def export_gltf(
    filepath,
    props,
    context,
    *,
    objects=None,
    animations=False,
    export_format=None,
    animation_mode=None,
    frame_step=None,
    staging=False,
    manage_state=True,
    quarantine_fcurves=False,
):
    """Export an explicit object set through Blender's glTF operator."""
    export_objects = list(objects or collect_export_objects(context))
    if not export_objects:
        raise RuntimeError("No objects are available in the requested export scope.")

    state = _snapshot_object_state(context, export_objects) if manage_state else None
    quarantine = None
    records = []
    try:
        if quarantine_fcurves:
            quarantine, records = quarantine_unsafe_fcurves(
                _actions_for_export_objects(
                    export_objects,
                    getattr(props, "include_morphs", True),
                )
            )

        _ensure_object_mode(context)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in export_objects:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.select_set(True)
        context.view_layer.objects.active = export_objects[0]

        options = build_gltf_options(
            filepath,
            props,
            animations=animations,
            export_format=export_format,
            animation_mode=animation_mode,
            frame_step=frame_step,
            staging=staging,
        )
        result = bpy.ops.export_scene.gltf(**options)
        if "FINISHED" not in result:
            raise RuntimeError(f"Blender cancelled glTF export: {filepath}")
    finally:
        restore_error = None
        if quarantine:
            try:
                restore_quarantined_fcurves(quarantine, records)
            except Exception as exc:
                restore_error = exc
        if state:
            try:
                _restore_object_state(context, state)
            except Exception as exc:
                if restore_error is None:
                    restore_error = exc
        if restore_error is not None:
            raise restore_error

    return filepath


def _publish_staged_export(work_dir, output_dir, primary_filename):
    relative_files = []
    for root, _directories, filenames in os.walk(work_dir):
        for filename in filenames:
            relative_files.append(
                os.path.relpath(os.path.join(root, filename), work_dir)
            )

    primary_relative = os.path.normpath(primary_filename)
    if primary_relative not in relative_files:
        raise RuntimeError(
            f"Blender did not create the expected model file: {primary_filename}"
        )
    relative_files.sort(
        key=lambda relative: (
            os.path.normcase(relative) == os.path.normcase(primary_relative),
            relative.casefold(),
        )
    )

    backup_root = tempfile.mkdtemp(
        prefix=".web3d-model-backup-",
        dir=output_dir,
    )
    replacements = []
    try:
        for relative in relative_files:
            source = os.path.join(work_dir, relative)
            destination = os.path.join(output_dir, relative)
            os.makedirs(os.path.dirname(destination) or output_dir, exist_ok=True)
            backup = None
            if os.path.isfile(destination):
                backup = os.path.join(backup_root, relative)
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                os.replace(destination, backup)
            replacements.append((destination, backup))
            os.replace(source, destination)
    except Exception as publish_error:
        rollback_errors = []
        for destination, backup in reversed(replacements):
            try:
                if os.path.isfile(destination):
                    os.remove(destination)
                if backup and os.path.isfile(backup):
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    os.replace(backup, destination)
            except OSError as rollback_error:
                rollback_errors.append((destination, rollback_error))
        if rollback_errors:
            failed_paths = ", ".join(path for path, _error in rollback_errors)
            raise RuntimeError(
                "Model publication failed and rollback was incomplete for: "
                f"{failed_paths}. Recovery copies remain in {backup_root}"
            ) from publish_error
        shutil.rmtree(backup_root, ignore_errors=True)
        raise

    shutil.rmtree(backup_root, ignore_errors=True)


def export_character_glb(
    filepath,
    props,
    context,
    *,
    export_format=None,
    scope=None,
):
    """Export an animation-free model in the selected glTF container format."""
    filepath = os.path.abspath(filepath)
    output_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    export_format = export_format or getattr(props, "export_format", "GLB")
    os.makedirs(output_dir, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix=".web3d-model-", dir=output_dir)
    staged_filepath = os.path.join(work_dir, filename)
    try:
        export_gltf(
            staged_filepath,
            props,
            context,
            objects=collect_export_objects(context, scope),
            animations=False,
            export_format=export_format,
        )
        _publish_staged_export(work_dir, output_dir, filename)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return filepath
