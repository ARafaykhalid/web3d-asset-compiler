"""
Self-contained compact character animation export pipeline.

Animation values are sampled through a temporary Blender glTF export so bone
coordinates, constraints, drivers, and morph targets match the final GLB.
The sampled tracks are then reduced, quantized, validated, and written as
standalone T3AN files with a manifest and TypeScript runtime modules.
"""

import json
import math
import os
import re
import shutil
import struct
import tempfile
import traceback

import bpy

from .character_exporter import collect_export_objects, export_gltf
from .quarantine import (
    iter_action_fcurve_collections,
    quarantine_unsafe_fcurves,
    restore_quarantined_fcurves,
)
from .ts_generator import GENERATED_MARKER, safe_filename, write_typescript_modules


MAGIC = b"T3AN"
FORMAT_VERSION = 1
HEADER_SIZE = 32
MANIFEST_FILENAME = "animation-manifest.json"
PROPERTY_CODES = {"position": 0, "quaternion": 1, "scale": 2, "morph": 3}
INTERPOLATION_CODES = {"LINEAR": 0, "STEP": 1}
ENCODING_FLOAT32 = 0
ENCODING_QUAT_INT16 = 1
ENCODING_VECTOR_UINT16 = 2
ENCODING_SCALAR_UINT16 = 3
TIME_UNIFORM = 0
TIME_UINT16 = 1
TIME_UINT32 = 2
TIME_FLOAT32 = 3
COMPONENT_FORMATS = {
    5120: "b",
    5121: "B",
    5122: "h",
    5123: "H",
    5125: "I",
    5126: "f",
}
ACCESSOR_WIDTHS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def round_float(value, precision=5):
    rounded = round(float(value), precision)
    return 0.0 if abs(rounded) < 10.0 ** (-precision) else rounded


def _progress(callback, value, message):
    print(f"[Binary Animation Export] {value:3d}% {message}")
    if callback:
        callback(int(value), message)


def _human_size(byte_count):
    size = float(byte_count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024.0


# ---------------------------------------------------------------------------
# Export object and Action discovery
# ---------------------------------------------------------------------------


def _uses_armature(obj, armature):
    if obj.type != "MESH":
        return False
    if obj.parent == armature and obj.parent_type in {"ARMATURE", "BONE"}:
        return True
    return any(
        modifier.type == "ARMATURE" and modifier.object == armature
        for modifier in obj.modifiers
    )


def _skinned_meshes_for_armature(armature, allowed_objects=None):
    allowed = set(allowed_objects) if allowed_objects is not None else None
    meshes = [
        obj
        for obj in bpy.context.scene.objects
        if (allowed is None or obj in allowed) and _uses_armature(obj, armature)
    ]
    return sorted(meshes, key=lambda obj: obj.name)


def _find_primary_armature(export_objects):
    export_set = set(export_objects)
    candidates = set(obj for obj in export_objects if obj.type == "ARMATURE")
    for obj in export_objects:
        if obj.type != "MESH":
            continue
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE" and modifier.object:
                candidates.add(modifier.object)

    ranked = []
    for armature in candidates:
        meshes = _skinned_meshes_for_armature(armature, export_set)
        vertices = sum(len(mesh.data.vertices) for mesh in meshes)
        ranked.append((len(meshes), vertices, armature.name, armature, meshes))
    if not ranked:
        raise RuntimeError("No Armature exists in the requested export scope.")

    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    mesh_count, _vertices, _name, armature, meshes = ranked[0]
    if mesh_count == 0:
        raise RuntimeError(
            f"Found Armature '{armature.name}', but no scoped mesh is skinned to it."
        )
    return armature, meshes


def _collect_character_objects(armature, meshes):
    result = {armature, *meshes}
    pending = list(result)
    while pending:
        obj = pending.pop()
        if obj.parent and obj.parent not in result:
            result.add(obj.parent)
            pending.append(obj.parent)
    return sorted(result, key=lambda obj: obj.name)


def _pipeline_objects(context, props, force_character_scope):
    if force_character_scope:
        all_objects = list(context.scene.objects)
        armature, meshes = _find_primary_armature(all_objects)
        return armature, meshes, _collect_character_objects(armature, meshes)

    scope = getattr(props, "export_scope", "SELECTED")
    export_objects = collect_export_objects(context, scope)
    if not export_objects:
        raise RuntimeError("No objects are available in the requested export scope.")
    armature, meshes = _find_primary_armature(export_objects)
    return armature, meshes, export_objects


def _iter_action_fcurves(action):
    for collection in iter_action_fcurve_collections(action):
        yield from collection


def _action_has_keyframes(action):
    return any(
        len(fcurve.keyframe_points) > 0 or len(fcurve.sampled_points) > 0
        for fcurve in _iter_action_fcurves(action)
    )


def _animated_ids(objects, include_morphs=True):
    result = list(objects)
    if not include_morphs:
        return result
    for obj in objects:
        if obj.type == "MESH" and obj.data.shape_keys:
            result.append(obj.data.shape_keys)
    return result


def _active_actions(data_blocks):
    result = set()
    for data_block in data_blocks:
        animation_data = getattr(data_block, "animation_data", None)
        if animation_data and animation_data.action:
            result.add(animation_data.action)
    return result


def _used_actions(data_blocks, include_nla):
    result = _active_actions(data_blocks)
    if not include_nla:
        return result
    for data_block in data_blocks:
        animation_data = getattr(data_block, "animation_data", None)
        if not animation_data:
            continue
        for track in animation_data.nla_tracks:
            if getattr(track, "mute", False):
                continue
            for strip in track.strips:
                if not getattr(strip, "mute", False) and strip.action:
                    result.add(strip.action)
    return result


def _compatible_actions(armature, meshes, include_morphs):
    bone_names = set(armature.data.bones.keys())
    shape_key_names = set()
    if include_morphs:
        for mesh in meshes:
            shape_keys = mesh.data.shape_keys
            if shape_keys:
                shape_key_names.update(key.name for key in shape_keys.key_blocks)

    bone_pattern = re.compile(r'pose\.bones\["((?:\\.|[^"])*)"\]')
    shape_pattern = re.compile(r'key_blocks\["((?:\\.|[^"])*)"\]\.value')
    result = set()
    for action in bpy.data.actions:
        for fcurve in _iter_action_fcurves(action):
            bone_match = bone_pattern.search(fcurve.data_path)
            if bone_match and bone_match.group(1) in bone_names:
                result.add(action)
                break
            shape_match = shape_pattern.search(fcurve.data_path)
            if shape_match and shape_match.group(1) in shape_key_names:
                result.add(action)
                break
    return result


def _has_nla_content(data_blocks):
    for data_block in data_blocks:
        animation_data = getattr(data_block, "animation_data", None)
        if not animation_data:
            continue
        for track in animation_data.nla_tracks:
            if getattr(track, "mute", False):
                continue
            if any(
                strip.action and not getattr(strip, "mute", False)
                for strip in track.strips
            ):
                return True
    return False


def _action_selection(
    armature,
    meshes,
    export_objects,
    props,
    force_all_actions,
):
    mode = "ACTIONS" if force_all_actions else getattr(
        props, "animation_mode", "ACTIONS"
    )
    if mode not in {"ACTIONS", "ACTIVE_ACTIONS", "NLA_TRACKS", "SCENE"}:
        raise RuntimeError(f"Unsupported animation mode: {mode}")

    include_morphs = getattr(props, "include_morphs", True)
    data_blocks = _animated_ids(export_objects, include_morphs)
    excluded = set() if force_all_actions else {
        name.strip().casefold()
        for name in re.split(r"[,;\n]+", getattr(props, "exclude_actions", ""))
        if name.strip()
    }

    if mode == "NLA_TRACKS":
        if not getattr(props, "export_nla_strips", True):
            raise RuntimeError(
                "NLA Tracks mode requires 'Export NLA Track Strips' to be enabled."
            )
        if not _has_nla_content(data_blocks):
            raise RuntimeError("No non-muted NLA strips were found for this character.")
        return mode, None, excluded

    if mode == "SCENE":
        return mode, None, excluded

    if mode == "ACTIVE_ACTIONS":
        actions = _active_actions(data_blocks)
    else:
        include_nla = getattr(props, "export_nla_strips", True)
        actions = _used_actions(data_blocks, include_nla)
        if force_all_actions or getattr(props, "export_all_actions", True):
            actions.update(_compatible_actions(
                armature,
                meshes,
                include_morphs,
            ))

    actions = sorted(
        (
            action
            for action in actions
            if action.name.casefold() not in excluded and _action_has_keyframes(action)
        ),
        key=lambda action: action.name.casefold(),
    )
    if not actions:
        label = "active Actions" if mode == "ACTIVE_ACTIONS" else "compatible Actions"
        raise RuntimeError(f"No {label} with keyframes were found for this character.")
    return mode, {action.name for action in actions}, excluded


def _export_relevant_actions(export_objects, props, wanted_names):
    if wanted_names is not None:
        return sorted(
            (
                action
                for action in bpy.data.actions
                if action.name in wanted_names
            ),
            key=lambda action: action.name.casefold(),
        )

    data_blocks = _animated_ids(
        export_objects,
        getattr(props, "include_morphs", True),
    )
    return sorted(
        _used_actions(data_blocks, include_nla=True),
        key=lambda action: action.name.casefold(),
    )


def animation_export_readiness(
    context,
    props,
    *,
    force_all_actions=False,
    force_character_scope=False,
):
    """Return the same non-mutating animation readiness used by export."""
    try:
        armature, meshes, export_objects = _pipeline_objects(
            context,
            props,
            force_character_scope,
        )
        mode, wanted_names, _excluded = _action_selection(
            armature,
            meshes,
            export_objects,
            props,
            force_all_actions,
        )
    except Exception as exc:
        return False, str(exc)

    if mode == "NLA_TRACKS":
        detail = "non-muted NLA tracks"
    elif mode == "SCENE":
        detail = "scene timeline"
    else:
        detail = f"{len(wanted_names or ())} animation Action(s)"
    return True, f"{armature.name}, {len(meshes)} skinned mesh(es), {detail}"


# ---------------------------------------------------------------------------
# Reversible Blender state management
# ---------------------------------------------------------------------------


def _snapshot_scene_state(objects):
    context = bpy.context
    scene = context.scene
    active = context.view_layer.objects.active
    animation_state = []
    for data_block in _animated_ids(objects):
        animation_data = getattr(data_block, "animation_data", None)
        if not animation_data:
            continue
        nla_state = []
        for track in animation_data.nla_tracks:
            strips = [
                (strip, getattr(strip, "mute", None))
                for strip in track.strips
            ]
            nla_state.append((
                track,
                getattr(track, "mute", None),
                getattr(track, "is_solo", None),
                strips,
            ))
        animation_state.append((
            data_block,
            animation_data.action,
            getattr(animation_data, "action_slot", None),
            getattr(animation_data, "use_nla", None),
            getattr(animation_data, "action_blend_type", None),
            getattr(animation_data, "action_extrapolation", None),
            getattr(animation_data, "action_influence", None),
            nla_state,
        ))
    return {
        "active": active,
        "mode": active.mode if active else "OBJECT",
        "selected": list(context.selected_objects),
        "hidden": {
            obj: (obj.hide_get(), obj.hide_viewport)
            for obj in objects
        },
        "frame": scene.frame_current,
        "subframe": scene.frame_subframe,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "use_preview_range": scene.use_preview_range,
        "frame_preview_start": scene.frame_preview_start,
        "frame_preview_end": scene.frame_preview_end,
        "animation": animation_state,
    }


def _set_if_available(owner, attribute, value):
    if value is None or not hasattr(owner, attribute):
        return
    try:
        setattr(owner, attribute, value)
    except (AttributeError, RuntimeError, TypeError):
        pass


def _ensure_object_mode():
    active = bpy.context.view_layer.objects.active
    if active and active.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def _restore_scene_state(state):
    _ensure_object_mode()
    scene = bpy.context.scene
    for record in state["animation"]:
        (
            data_block,
            action,
            action_slot,
            use_nla,
            blend,
            extrapolation,
            influence,
            nla_state,
        ) = record
        animation_data = getattr(data_block, "animation_data", None)
        if not animation_data:
            continue
        try:
            animation_data.action = action
        except (AttributeError, RuntimeError, TypeError):
            pass
        _set_if_available(animation_data, "action_slot", action_slot)
        _set_if_available(animation_data, "use_nla", use_nla)
        _set_if_available(animation_data, "action_blend_type", blend)
        _set_if_available(animation_data, "action_extrapolation", extrapolation)
        _set_if_available(animation_data, "action_influence", influence)
        for track, mute, is_solo, strips in nla_state:
            _set_if_available(track, "mute", mute)
            _set_if_available(track, "is_solo", is_solo)
            for strip, strip_mute in strips:
                _set_if_available(strip, "mute", strip_mute)

    scene.frame_start = state["frame_start"]
    scene.frame_end = state["frame_end"]
    scene.use_preview_range = state["use_preview_range"]
    scene.frame_preview_start = state["frame_preview_start"]
    scene.frame_preview_end = state["frame_preview_end"]
    scene.frame_set(state["frame"], subframe=state["subframe"])

    for obj, (hidden, hide_viewport) in state["hidden"].items():
        if obj.name in bpy.data.objects:
            obj.hide_set(hidden)
            obj.hide_viewport = hide_viewport
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except RuntimeError:
        pass
    for obj in state["selected"]:
        if obj.name in bpy.data.objects:
            try:
                obj.select_set(True)
            except RuntimeError:
                pass
    active = state["active"]
    if active and active.name in bpy.data.objects:
        bpy.context.view_layer.objects.active = active
        if state["mode"] != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode=state["mode"])
            except RuntimeError:
                print(
                    f"[Binary Animation Export] Could not restore mode {state['mode']}."
                )


# ---------------------------------------------------------------------------
# GLB parsing and glTF animation extraction
# ---------------------------------------------------------------------------


def _read_glb(filepath):
    with open(filepath, "rb") as handle:
        header = handle.read(12)
        if len(header) != 12:
            raise ValueError(f"Invalid GLB header: {filepath}")
        magic, version, total_length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2:
            raise ValueError(f"Not a glTF 2.0 GLB: {filepath}")

        document = None
        binary = b""
        while handle.tell() < total_length:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                raise ValueError("Truncated GLB chunk header.")
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            chunk = handle.read(chunk_length)
            if len(chunk) != chunk_length:
                raise ValueError("Truncated GLB chunk.")
            if chunk_type == 0x4E4F534A:
                document = json.loads(
                    chunk.rstrip(b"\x00 \t\r\n").decode("utf-8")
                )
            elif chunk_type == 0x004E4942:
                binary = chunk

    if document is None:
        raise ValueError("GLB contains no JSON document.")
    return document, binary


def read_glb_animation_names(filepath):
    document, _binary = _read_glb(filepath)
    return [
        animation.get("name") or f"Animation_{index + 1}"
        for index, animation in enumerate(document.get("animations", []))
    ]


def _normalize_component(value, component_type):
    if component_type == 5120:
        return max(value / 127.0, -1.0)
    if component_type == 5121:
        return value / 255.0
    if component_type == 5122:
        return max(value / 32767.0, -1.0)
    if component_type == 5123:
        return value / 65535.0
    return value


def _unpack_rows(
    binary,
    *,
    offset,
    count,
    width,
    component_type,
    stride=None,
    normalized=False,
):
    component_format = COMPONENT_FORMATS[component_type]
    component_size = struct.calcsize("<" + component_format)
    element_size = component_size * width
    stride = stride or element_size
    unpack_format = "<" + component_format * width
    rows = []
    for index in range(count):
        row = list(struct.unpack_from(
            unpack_format,
            binary,
            offset + index * stride,
        ))
        if normalized:
            row = [_normalize_component(value, component_type) for value in row]
        rows.append(row)
    return rows


def _read_accessor(document, binary, accessor_index):
    accessor = document["accessors"][accessor_index]
    width = ACCESSOR_WIDTHS[accessor["type"]]
    count = accessor["count"]
    component_type = accessor["componentType"]
    normalized = accessor.get("normalized", False)
    rows = [[0.0] * width for _ in range(count)]

    if "bufferView" in accessor:
        view = document["bufferViews"][accessor["bufferView"]]
        component_size = struct.calcsize("<" + COMPONENT_FORMATS[component_type])
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        rows = _unpack_rows(
            binary,
            offset=offset,
            count=count,
            width=width,
            component_type=component_type,
            stride=view.get("byteStride", component_size * width),
            normalized=normalized,
        )

    sparse = accessor.get("sparse")
    if sparse:
        sparse_count = sparse["count"]
        index_spec = sparse["indices"]
        index_view = document["bufferViews"][index_spec["bufferView"]]
        index_type = index_spec["componentType"]
        indices = _unpack_rows(
            binary,
            offset=index_view.get("byteOffset", 0) + index_spec.get("byteOffset", 0),
            count=sparse_count,
            width=1,
            component_type=index_type,
        )
        value_spec = sparse["values"]
        value_view = document["bufferViews"][value_spec["bufferView"]]
        values = _unpack_rows(
            binary,
            offset=value_view.get("byteOffset", 0) + value_spec.get("byteOffset", 0),
            count=sparse_count,
            width=width,
            component_type=component_type,
            normalized=normalized,
        )
        for index_row, value_row in zip(indices, values):
            rows[index_row[0]] = value_row
    return rows


def _three_node_names(document):
    """Mirror Three.js PropertyBinding node-name sanitizing and uniqueness."""
    result = {}
    used = {}
    for index, node in enumerate(document.get("nodes", [])):
        original = node.get("name") or f"node_{index}"
        sanitized = re.sub(r"\s", "_", original)
        sanitized = re.sub(r"[\[\]\.:/]", "", sanitized) or f"node_{index}"
        duplicate_index = used.get(sanitized, 0)
        used[sanitized] = duplicate_index + 1
        result[index] = (
            sanitized
            if duplicate_index == 0
            else f"{sanitized}_{duplicate_index}"
        )
    return result


def _node_rest_value(document, node_index, path):
    node = document["nodes"][node_index]
    if path == "translation":
        return node.get("translation", [0.0, 0.0, 0.0])
    if path == "rotation":
        return node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    if path == "scale":
        return node.get("scale", [1.0, 1.0, 1.0])
    return None


def _extract_raw_standard_track(document, binary, channel, sampler, node_names):
    node_index = channel["target"]["node"]
    path = channel["target"]["path"]
    times = [
        row[0]
        for row in _read_accessor(document, binary, sampler["input"])
    ]
    values = _read_accessor(document, binary, sampler["output"])
    interpolation = sampler.get("interpolation", "LINEAR")
    in_tangents = None
    out_tangents = None
    if interpolation == "CUBICSPLINE":
        if len(values) != len(times) * 3:
            raise ValueError("Invalid glTF cubic spline sampler length.")
        in_tangents = values[0::3]
        sampled_values = values[1::3]
        out_tangents = values[2::3]
        values = sampled_values
    property_name = {
        "translation": "position",
        "rotation": "quaternion",
        "scale": "scale",
    }[path]
    if len(times) != len(values):
        raise ValueError("glTF animation sampler input/output lengths do not match.")
    track = {
        "target": node_names[node_index],
        "property": property_name,
        "morph_index": 0xFFFF,
        "interpolation": interpolation,
        "times": [float(value) for value in times],
        "values": [
            [float(component) for component in row]
            for row in values
        ],
        "rest": [
            float(value)
            for value in _node_rest_value(document, node_index, path)
        ],
    }
    if in_tangents is not None:
        track["in_tangents"] = [
            [float(component) for component in row]
            for row in in_tangents
        ]
        track["out_tangents"] = [
            [float(component) for component in row]
            for row in out_tangents
        ]
    return track


def _extract_raw_weight_tracks(document, binary, channel, sampler, node_names):
    node_index = channel["target"]["node"]
    node = document["nodes"][node_index]
    if "mesh" not in node:
        return []
    mesh = document["meshes"][node["mesh"]]
    times = [
        row[0]
        for row in _read_accessor(document, binary, sampler["input"])
    ]
    rows = _read_accessor(document, binary, sampler["output"])
    flat = [component for row in rows for component in row]
    interpolation = sampler.get("interpolation", "LINEAR")
    divisor = len(times) * (3 if interpolation == "CUBICSPLINE" else 1)
    target_count = len(flat) // divisor if divisor else 0
    if target_count == 0:
        return []
    if divisor == 0 or len(flat) % divisor:
        raise ValueError("Invalid glTF morph sampler length.")
    if target_count >= 0xFFFF:
        raise RuntimeError("T3AN supports at most 65,534 morph targets per mesh.")

    in_sampled = None
    out_sampled = None
    if interpolation == "CUBICSPLINE":
        in_sampled = []
        sampled = []
        out_sampled = []
        block = target_count * 3
        for index in range(len(times)):
            start = index * block
            in_sampled.append(flat[start:start + target_count])
            sampled.append(flat[
                start + target_count:start + target_count * 2
            ])
            out_sampled.append(flat[
                start + target_count * 2:start + target_count * 3
            ])
    else:
        sampled = [
            flat[index * target_count:(index + 1) * target_count]
            for index in range(len(times))
        ]

    rest_weights = node.get(
        "weights",
        mesh.get("weights", [0.0] * target_count),
    )
    tracks = []
    for target_index in range(target_count):
        track = {
            "target": node_names[node_index],
            "property": "morph",
            "morph_index": target_index,
            "interpolation": interpolation,
            "times": [float(value) for value in times],
            "values": [[float(row[target_index])] for row in sampled],
            "rest": [
                float(
                    rest_weights[target_index]
                    if target_index < len(rest_weights)
                    else 0.0
                )
            ],
        }
        if in_sampled is not None:
            track["in_tangents"] = [
                [float(row[target_index])]
                for row in in_sampled
            ]
            track["out_tangents"] = [
                [float(row[target_index])]
                for row in out_sampled
            ]
        tracks.append(track)
    return tracks


def _extract_raw_animation_data(
    document,
    binary,
    wanted_names,
    excluded_names,
    effective_fps,
):
    grouped = {}
    node_names = _three_node_names(document)
    for animation_index, animation in enumerate(document.get("animations", [])):
        name = animation.get("name") or f"Animation_{animation_index + 1}"
        if wanted_names is not None and name not in wanted_names:
            continue
        if name.casefold() in excluded_names:
            continue
        entry = grouped.setdefault(name, {
            "name": name,
            "tracks": [],
            "duration": 0.0,
            "fps": float(effective_fps),
        })
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            if "node" not in target:
                continue
            path = target.get("path")
            sampler = animation["samplers"][channel["sampler"]]
            if path in {"translation", "rotation", "scale"}:
                entry["tracks"].append(_extract_raw_standard_track(
                    document,
                    binary,
                    channel,
                    sampler,
                    node_names,
                ))
            elif path == "weights":
                entry["tracks"].extend(_extract_raw_weight_tracks(
                    document,
                    binary,
                    channel,
                    sampler,
                    node_names,
                ))

    result = []
    for name in sorted(grouped, key=str.casefold):
        entry = grouped[name]
        if not entry["tracks"]:
            continue
        entry["duration"] = max(
            (
                max(track["times"])
                for track in entry["tracks"]
                if track["times"]
            ),
            default=0.0,
        )
        entry["tracks"].sort(key=lambda track: (
            track["target"],
            track["property"],
            track["morph_index"],
        ))
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Exact-FPS resampling, key reduction, and quantization
# ---------------------------------------------------------------------------


def _quat_normalize(quaternion):
    length = math.sqrt(sum(value * value for value in quaternion))
    if length <= 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return [value / length for value in quaternion]


def _quat_slerp(first, second, factor):
    first = _quat_normalize(first)
    second = _quat_normalize(second)
    dot = sum(a * b for a, b in zip(first, second))
    if dot < 0.0:
        second = [-value for value in second]
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return _quat_normalize([
            a + factor * (b - a)
            for a, b in zip(first, second)
        ])
    angle = math.acos(dot)
    sine = math.sin(angle)
    left = math.sin((1.0 - factor) * angle) / sine
    right = math.sin(factor * angle) / sine
    return [left * a + right * b for a, b in zip(first, second)]


def _interpolated_value(first, second, factor, property_name):
    if property_name == "quaternion":
        return _quat_slerp(first, second, factor)
    return [a + factor * (b - a) for a, b in zip(first, second)]


def _sample_values(
    times,
    values,
    target_time,
    property_name,
    interpolation="LINEAR",
    in_tangents=None,
    out_tangents=None,
):
    if len(times) == 1 or target_time <= times[0]:
        return values[0]
    if target_time >= times[-1]:
        return values[-1]
    low, high = 0, len(times) - 1
    while low + 1 < high:
        middle = (low + high) // 2
        if times[middle] <= target_time:
            low = middle
        else:
            high = middle
    if interpolation == "STEP":
        return values[low]
    span = times[high] - times[low]
    factor = (
        0.0
        if abs(span) <= 1e-12
        else (target_time - times[low]) / span
    )
    if interpolation == "CUBICSPLINE":
        if in_tangents is None or out_tangents is None:
            raise ValueError("Cubic spline track is missing tangent data.")
        factor2 = factor * factor
        factor3 = factor2 * factor
        result = []
        for component in range(len(values[low])):
            result.append(
                (2.0 * factor3 - 3.0 * factor2 + 1.0)
                * values[low][component]
                + (factor3 - 2.0 * factor2 + factor)
                * span
                * out_tangents[low][component]
                + (-2.0 * factor3 + 3.0 * factor2)
                * values[high][component]
                + (factor3 - factor2)
                * span
                * in_tangents[high][component]
            )
        return _quat_normalize(result) if property_name == "quaternion" else result
    return _interpolated_value(values[low], values[high], factor, property_name)


def _exact_sample_times(duration, fps):
    duration = max(0.0, float(duration))
    fps = float(fps)
    if duration <= 1e-12:
        return [0.0]
    whole_steps = int(math.floor(duration * fps + 1e-9))
    times = [index / fps for index in range(whole_steps + 1)]
    endpoint_epsilon = max(1e-7, (1.0 / fps) * 1e-6)
    if abs(times[-1] - duration) <= endpoint_epsilon:
        times[-1] = duration
    elif times[-1] < duration:
        times.append(duration)
    return times


def _resample_raw_animations(animations, fps):
    requested_fps = float(fps)
    result = []
    for animation in animations:
        duration = float(animation.get("duration", 0.0))
        sample_times = _exact_sample_times(duration, requested_fps)
        tracks = []
        for track in animation["tracks"]:
            if not track["times"] or len(track["times"]) != len(track["values"]):
                continue
            resampled = {
                key: value
                for key, value in track.items()
                if key not in {"in_tangents", "out_tangents"}
            }
            resampled.update({
                "times": list(sample_times),
                "interpolation": (
                    "STEP" if track.get("interpolation") == "STEP" else "LINEAR"
                ),
                "values": [
                    list(_sample_values(
                        track["times"],
                        track["values"],
                        target_time,
                        track["property"],
                        track.get("interpolation", "LINEAR"),
                        track.get("in_tangents"),
                        track.get("out_tangents"),
                    ))
                    for target_time in sample_times
                ],
            })
            tracks.append(resampled)
        if tracks:
            result.append({
                **animation,
                "duration": duration,
                "fps": requested_fps,
                "tracks": tracks,
            })
    return result


def _track_tolerance(props, property_name):
    if property_name == "position":
        return getattr(props, "position_tolerance", 0.0001)
    if property_name == "quaternion":
        return math.radians(getattr(props, "rotation_tolerance", 0.05))
    if property_name == "scale":
        return getattr(props, "scale_tolerance", 0.0001)
    return getattr(props, "morph_tolerance", 0.0005)


def _property_error(property_name, first, second):
    if property_name == "quaternion":
        a = _quat_normalize(first)
        b = _quat_normalize(second)
        dot = min(1.0, max(-1.0, abs(sum(x * y for x, y in zip(a, b)))))
        return 2.0 * math.acos(dot)
    if property_name == "position":
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))
    return max((abs(a - b) for a, b in zip(first, second)), default=0.0)


def _reduce_linear_indices(times, values, property_name, tolerance):
    keep = {0, len(times) - 1}
    stack = [(0, len(times) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        span = times[end] - times[start]
        worst_error = -1.0
        worst_index = None
        for index in range(start + 1, end):
            factor = (
                0.0
                if abs(span) <= 1e-12
                else (times[index] - times[start]) / span
            )
            predicted = _interpolated_value(
                values[start],
                values[end],
                factor,
                property_name,
            )
            error = _property_error(property_name, values[index], predicted)
            if error > worst_error:
                worst_error = error
                worst_index = index
        if worst_index is not None and worst_error > tolerance:
            keep.add(worst_index)
            stack.append((start, worst_index))
            stack.append((worst_index, end))
    return sorted(keep)


def _validate_optimized_track(track, decoded_values):
    maximum = 0.0
    for time_value, source in zip(
        track["original_times"],
        track["original_values"],
    ):
        rebuilt = _sample_values(
            track["times"],
            decoded_values,
            time_value,
            track["property"],
            track["interpolation"],
        )
        maximum = max(
            maximum,
            _property_error(track["property"], source, rebuilt),
        )
    return maximum


def _optimize_binary_track(raw_track, props):
    times = raw_track["times"]
    original_values = raw_track["values"]
    if not times or len(times) != len(original_values):
        return None
    property_name = raw_track["property"]
    tolerance = _track_tolerance(props, property_name)
    precision = getattr(props, "decimal_precision", 5)
    source_values = []
    for source in original_values:
        value = [float(component) for component in source]
        if property_name == "quaternion":
            value = _quat_normalize(value)
            if source_values and sum(
                a * b for a, b in zip(source_values[-1], value)
            ) < 0.0:
                value = [-component for component in value]
        source_values.append(value)

    values = []
    for source in source_values:
        value = [round(component, precision) for component in source]
        if property_name == "quaternion":
            value = _quat_normalize(value)
            if values and sum(a * b for a, b in zip(values[-1], value)) < 0.0:
                value = [-component for component in value]
        values.append(value)

    is_constant = all(
        _property_error(property_name, source_values[0], value) <= tolerance
        for value in source_values[1:]
    )
    if is_constant:
        matches_rest = raw_track["rest"] is not None and all(
            _property_error(property_name, value, raw_track["rest"]) <= tolerance
            for value in source_values
        )
        if getattr(props, "remove_static_tracks", True) and matches_rest:
            return None
        indices = [0]
    elif raw_track["interpolation"] == "STEP":
        indices = [0]
        for index in range(1, len(times)):
            if _property_error(
                property_name,
                values[index - 1],
                values[index],
            ) > tolerance:
                indices.append(index)
        if indices[-1] != len(times) - 1:
            indices.append(len(times) - 1)
    elif getattr(props, "keyframe_reduction", True) and len(times) > 2:
        indices = _reduce_linear_indices(
            times,
            values,
            property_name,
            tolerance * 0.9,
        )
    else:
        indices = list(range(len(times)))

    def build(selected):
        return {
            "target": raw_track["target"],
            "property": property_name,
            "morph_index": raw_track["morph_index"],
            "interpolation": raw_track["interpolation"],
            "times": [times[index] for index in selected],
            "values": [values[index] for index in selected],
            "original_times": times,
            "original_values": original_values,
        }

    optimized = build(indices)
    if _validate_optimized_track(optimized, optimized["values"]) > tolerance:
        values = source_values
        optimized = build(list(range(len(times))))
    return optimized


def _quantization_allowed(props, property_name):
    if not getattr(props, "enable_animation_quantization", True):
        return False
    if property_name == "quaternion":
        return getattr(props, "quantize_quaternions", True)
    if property_name == "position":
        return getattr(props, "quantize_positions", True)
    if property_name == "scale":
        return getattr(props, "quantize_scales", True)
    return getattr(props, "quantize_morphs", True)


def _float32(value):
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _float32_rows(rows):
    return [[_float32(value) for value in row] for row in rows]


def _choose_value_encoding(track, props):
    values = track["values"]
    property_name = track["property"]
    tolerance = _track_tolerance(props, property_name)
    float_values = _float32_rows(values)
    if not _quantization_allowed(props, property_name):
        return ENCODING_FLOAT32, None, float_values

    if property_name == "quaternion":
        encoded = []
        decoded = []
        for value in values:
            row = [
                max(-32767, min(32767, int(round(component * 32767.0))))
                for component in value
            ]
            encoded.append(row)
            decoded.append(_quat_normalize([
                component / 32767.0
                for component in row
            ]))
        error = max(
            (
                _property_error(property_name, source, rebuilt)
                for source, rebuilt in zip(values, decoded)
            ),
            default=0.0,
        )
        if error <= tolerance:
            return ENCODING_QUAT_INT16, encoded, decoded
        return ENCODING_FLOAT32, None, float_values

    width = len(values[0])
    minimum = _float32_rows([[
        min(row[index] for row in values)
        for index in range(width)
    ]])[0]
    maximum = _float32_rows([[
        max(row[index] for row in values)
        for index in range(width)
    ]])[0]
    encoded = []
    decoded = []
    for value in values:
        encoded_row = []
        decoded_row = []
        for index, component in enumerate(value):
            span = maximum[index] - minimum[index]
            quantized = (
                0
                if abs(span) <= 1e-20
                else int(round((component - minimum[index]) * 65535.0 / span))
            )
            quantized = max(0, min(65535, quantized))
            encoded_row.append(quantized)
            decoded_row.append(
                minimum[index]
                if abs(span) <= 1e-20
                else minimum[index] + (quantized / 65535.0) * span
            )
        encoded.append(encoded_row)
        decoded.append(decoded_row)
    error = max(
        (
            _property_error(property_name, source, rebuilt)
            for source, rebuilt in zip(values, decoded)
        ),
        default=0.0,
    )
    if error <= tolerance:
        encoding = (
            ENCODING_SCALAR_UINT16
            if width == 1
            else ENCODING_VECTOR_UINT16
        )
        return encoding, {
            "minimum": minimum,
            "maximum": maximum,
            "values": encoded,
        }, decoded
    return ENCODING_FLOAT32, None, float_values


def _choose_time_encoding(times, fps):
    sample_indices = [int(round(value * fps)) for value in times]
    on_grid = all(
        abs(value - index / fps) <= 1e-5
        for value, index in zip(times, sample_indices)
    )
    nonnegative = not sample_indices or min(sample_indices) >= 0
    within_uint32 = not sample_indices or max(sample_indices) <= 0xFFFFFFFF
    if on_grid and nonnegative and within_uint32:
        if len(sample_indices) <= 1:
            return TIME_UNIFORM, (sample_indices[0] if sample_indices else 0, 0)
        step = sample_indices[1] - sample_indices[0]
        if 0 <= step <= 0xFFFFFFFF and all(
            sample_indices[index] - sample_indices[index - 1] == step
            for index in range(2, len(sample_indices))
        ):
            return TIME_UNIFORM, (sample_indices[0], step)
        if max(sample_indices) <= 0xFFFF:
            return TIME_UINT16, sample_indices
        return TIME_UINT32, sample_indices
    return TIME_FLOAT32, times


# ---------------------------------------------------------------------------
# T3AN binary writing and manifest construction
# ---------------------------------------------------------------------------


def _align(data, alignment=4):
    padding = (-len(data)) % alignment
    if padding:
        data.extend(b"\0" * padding)


def _append_values(data, fmt, rows):
    packer = struct.Struct("<" + fmt)
    for row in rows:
        for value in row:
            data.extend(packer.pack(value))


def _fnv1a32(data):
    value = 0x811C9DC5
    for byte in data:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _fnv1a32_file(filepath):
    value = 0x811C9DC5
    with open(filepath, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            for byte in chunk:
                value ^= byte
                value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def _encode_binary_animation(animation, target_ids, mapping_hash, props):
    if len(animation["tracks"]) > 0xFFFF:
        raise RuntimeError("T3AN supports at most 65,535 tracks per animation.")

    data = bytearray(b"\0" * HEADER_SIZE)
    total_keys = 0
    contains_quantization = False
    validation = dict(animation.get("removed_validation", {
        "position": 0.0,
        "quaternion": 0.0,
        "scale": 0.0,
        "morph": 0.0,
    }))
    for track in animation["tracks"]:
        target_id = target_ids[track["target"]]
        encoding, encoded, decoded_values = _choose_value_encoding(track, props)
        maximum_error = _validate_optimized_track(track, decoded_values)
        tolerance = _track_tolerance(props, track["property"])
        if encoding != ENCODING_FLOAT32 and maximum_error > tolerance:
            encoding = ENCODING_FLOAT32
            encoded = None
            decoded_values = _float32_rows(track["values"])
            maximum_error = _validate_optimized_track(track, decoded_values)
        if maximum_error > tolerance:
            if track["property"] == "quaternion":
                measured = math.degrees(maximum_error)
                allowed = math.degrees(tolerance)
                unit = " degrees"
            else:
                measured = maximum_error
                allowed = tolerance
                unit = ""
            raise RuntimeError(
                f"Animation track '{track['target']}.{track['property']}' "
                f"cannot meet its error tolerance after Float32 encoding "
                f"({measured:.9g}{unit} > {allowed:.9g}{unit})."
            )
        time_mode, time_payload = _choose_time_encoding(
            track["times"],
            animation["fps"],
        )
        validation[track["property"]] = max(
            validation[track["property"]],
            maximum_error,
        )
        key_count = len(track["times"])
        component_count = len(track["values"][0])
        if key_count > 0xFFFFFFFF or component_count > 0xFF:
            raise RuntimeError("A T3AN track exceeds binary format limits.")
        total_keys += key_count
        if total_keys > 0xFFFFFFFF:
            raise RuntimeError("T3AN supports at most 4,294,967,295 total keys.")
        contains_quantization = contains_quantization or encoding != ENCODING_FLOAT32

        data.extend(struct.pack(
            "<HBBBBBBHHI",
            target_id,
            PROPERTY_CODES[track["property"]],
            INTERPOLATION_CODES.get(track["interpolation"], 0),
            encoding,
            time_mode,
            component_count,
            0,
            track["morph_index"],
            0,
            key_count,
        ))
        if time_mode == TIME_UNIFORM:
            data.extend(struct.pack("<II", time_payload[0], time_payload[1]))
        elif time_mode == TIME_UINT16:
            data.extend(struct.pack("<" + "H" * key_count, *time_payload))
        elif time_mode == TIME_UINT32:
            data.extend(struct.pack("<" + "I" * key_count, *time_payload))
        else:
            data.extend(struct.pack("<" + "f" * key_count, *time_payload))
        _align(data)

        if encoding == ENCODING_FLOAT32:
            _append_values(data, "f", track["values"])
        elif encoding == ENCODING_QUAT_INT16:
            _append_values(data, "h", encoded)
        else:
            _append_values(data, "f", [encoded["minimum"], encoded["maximum"]])
            _append_values(data, "H", encoded["values"])
        _align(data)

    flags = 1 if contains_quantization else 0
    struct.pack_into(
        "<4sHHffHHIII",
        data,
        0,
        MAGIC,
        FORMAT_VERSION,
        HEADER_SIZE,
        float(animation["duration"]),
        float(animation["fps"]),
        len(animation["tracks"]),
        flags,
        total_keys,
        mapping_hash,
        len(data),
    )
    return bytes(data), {
        "tracks": len(animation["tracks"]),
        "keyframes": total_keys,
        "size": len(data),
        "contentHash": f"{_fnv1a32(data):08x}",
        "validation": validation,
    }


def _atomic_write_binary(filepath, content):
    temporary = filepath + ".tmp"
    with open(temporary, "wb") as handle:
        handle.write(content)
    os.replace(temporary, filepath)


def _atomic_write_text(filepath, content):
    temporary = filepath + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.replace(temporary, filepath)


def _build_target_mapping(base_document, raw_animations):
    node_names = _three_node_names(base_document)
    available_names = set(node_names.values())
    joint_indices = []
    for skin in base_document.get("skins", []):
        for node_index in skin.get("joints", []):
            if node_index not in joint_indices:
                joint_indices.append(node_index)
    bone_names = [node_names[index] for index in joint_indices]

    animated_names = sorted(
        {
            track["target"]
            for animation in raw_animations
            for track in animation["tracks"]
        },
        key=str.casefold,
    )
    missing = [name for name in animated_names if name not in available_names]
    if missing:
        raise RuntimeError(
            "Animation targets are missing from the base GLB: " + ", ".join(missing)
        )

    targets = []
    used = set()
    for name in bone_names:
        if name not in used:
            targets.append({"name": name, "kind": "bone"})
            used.add(name)
    for name in animated_names:
        if name not in used:
            targets.append({"name": name, "kind": "object"})
            used.add(name)
    if len(targets) > 0xFFFF:
        raise RuntimeError("T3AN supports at most 65,535 animation targets.")

    target_ids = {entry["name"]: index for index, entry in enumerate(targets)}
    mapping_bytes = bytearray()
    for entry in targets:
        mapping_bytes.extend(entry["kind"].encode("utf-8"))
        mapping_bytes.append(0)
        mapping_bytes.extend(entry["name"].encode("utf-8"))
        mapping_bytes.append(0)
    return bone_names, targets, target_ids, _fnv1a32(mapping_bytes)


def _safe_animation_filenames(animations):
    used = {"index"}
    result = []
    for animation in animations:
        base = safe_filename(animation["name"])
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        result.append((animation, candidate + ".anim"))
    return result


def _previous_animation_files(output_dir, animation_dir):
    manifest_path = os.path.join(output_dir, MANIFEST_FILENAME)
    if not os.path.isfile(manifest_path):
        return set()
    result = set()
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            old_manifest = json.load(handle)
        animation_root = os.path.normcase(os.path.abspath(animation_dir))
        for metadata in old_manifest.get("animations", {}).values():
            candidate = os.path.abspath(os.path.join(
                output_dir,
                metadata.get("file", ""),
            ))
            candidate_key = os.path.normcase(candidate)
            if (
                candidate.lower().endswith(".anim")
                and os.path.commonpath([candidate_key, animation_root]) == animation_root
            ):
                result.add(candidate)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print("[Binary Animation Export] Existing manifest cleanup skipped.")
    return result


def _generated_typescript_files(animation_dir):
    result = set()
    if not os.path.isdir(animation_dir):
        return result
    for filename in os.listdir(animation_dir):
        if not filename.lower().endswith(".ts"):
            continue
        filepath = os.path.join(animation_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as handle:
                if handle.readline().rstrip("\r\n") == GENERATED_MARKER:
                    result.add(filepath)
        except OSError:
            pass
    return result


def _publish_working_tree(work_dir, output_dir, relative_files):
    animation_dir = os.path.join(output_dir, "animations")
    previous_animations = _previous_animation_files(output_dir, animation_dir)
    previous_modules = _generated_typescript_files(animation_dir)
    manifest_relative = MANIFEST_FILENAME
    ordered = [path for path in relative_files if path != manifest_relative]
    if manifest_relative in relative_files:
        ordered.append(manifest_relative)

    backup_root = tempfile.mkdtemp(
        prefix=".web3d-publish-backup-",
        dir=output_dir,
    )
    replacements = []
    published = []
    try:
        for relative in ordered:
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
            published.append(destination)
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
                "Output publication failed and rollback was incomplete for: "
                f"{failed_paths}. Recovery copies remain in {backup_root}"
            ) from publish_error
        shutil.rmtree(backup_root, ignore_errors=True)
        raise

    shutil.rmtree(backup_root, ignore_errors=True)

    new_animation_paths = {
        os.path.normcase(os.path.abspath(os.path.join(output_dir, relative)))
        for relative in relative_files
        if relative.lower().endswith(".anim")
    }
    for filepath in previous_animations:
        if (
            os.path.normcase(os.path.abspath(filepath)) not in new_animation_paths
            and os.path.isfile(filepath)
        ):
            try:
                os.remove(filepath)
            except OSError:
                print(
                    "[Binary Animation Export] Could not remove stale clip: "
                    + filepath
                )

    new_module_paths = {
        os.path.normcase(os.path.abspath(os.path.join(output_dir, relative)))
        for relative in relative_files
        if relative.startswith("animations/") and relative.lower().endswith(".ts")
    }
    for filepath in previous_modules:
        if (
            os.path.normcase(os.path.abspath(filepath)) not in new_module_paths
            and os.path.isfile(filepath)
        ):
            try:
                os.remove(filepath)
            except OSError:
                print(
                    "[Binary Animation Export] Could not remove stale module: "
                    + filepath
                )
    return published


# ---------------------------------------------------------------------------
# Public export orchestration
# ---------------------------------------------------------------------------


def export_character_and_animations(
    output_dir,
    glb_filename,
    props,
    context,
    *,
    progress_callback=None,
    force_all_actions=False,
    force_character_scope=False,
):
    """Export character.glb, compact clips, manifest, and TypeScript modules."""
    if getattr(props, "export_format", "GLB") != "GLB" and not force_character_scope:
        raise RuntimeError(
            "Character + Binary Animations requires the GLB export format."
        )
    if not getattr(props, "export_skins", True):
        raise RuntimeError(
            "Character + Binary Animations requires Armatures & Skins. "
            "Use the standalone model exporter when skins are disabled."
        )
    if not glb_filename.lower().endswith(".glb"):
        raise ValueError("The binary animation pipeline requires a .glb filename.")
    if os.path.basename(glb_filename) != glb_filename:
        raise ValueError("The GLB filename cannot contain a directory path.")

    precision = getattr(props, "decimal_precision", 5)
    sampling_fps = getattr(props, "sampling_fps", 30)
    tolerances = (
        getattr(props, "position_tolerance", 0.0001),
        getattr(props, "rotation_tolerance", 0.05),
        getattr(props, "scale_tolerance", 0.0001),
        getattr(props, "morph_tolerance", 0.0005),
    )
    if not 3 <= precision <= 9:
        raise ValueError("Decimal precision must be between 3 and 9.")
    if sampling_fps <= 0:
        raise ValueError("Sampling FPS must be greater than zero.")
    if any(value < 0 for value in tolerances):
        raise ValueError("Animation tolerances cannot be negative.")

    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    armature, meshes, export_objects = _pipeline_objects(
        context,
        props,
        force_character_scope,
    )
    animation_mode, wanted_names, excluded_names = _action_selection(
        armature,
        meshes,
        export_objects,
        props,
        force_all_actions,
    )
    scene_fps = context.scene.render.fps / context.scene.render.fps_base
    frame_step = max(1, int(getattr(props, "export_frame_step", 1)))
    state = _snapshot_scene_state(export_objects)
    quarantine = None
    quarantine_records = []
    work_dir = tempfile.mkdtemp(prefix=".web3d-export-", dir=output_dir)
    work_animation_dir = os.path.join(work_dir, "animations")
    os.makedirs(work_animation_dir, exist_ok=True)
    base_path = os.path.join(work_dir, glb_filename)
    staging_path = os.path.join(work_dir, ".animation-staging.glb")
    export_succeeded = False

    try:
        _ensure_object_mode()
        _progress(
            progress_callback,
            2,
            f"Detected armature {armature.name} and {len(meshes)} skinned meshes",
        )
        export_gltf(
            base_path,
            props,
            context,
            objects=export_objects,
            animations=False,
            export_format="GLB",
            manage_state=False,
            quarantine_fcurves=False,
        )
        base_document, _base_binary = _read_glb(base_path)
        if base_document.get("animations"):
            raise RuntimeError("The base GLB unexpectedly contains animations.")
        if not base_document.get("skins"):
            raise RuntimeError(
                "The base GLB contains no skin. Check Armature modifiers and vertex groups."
            )
        _progress(progress_callback, 18, "Exported animation-free character GLB")

        quarantine, quarantine_records = quarantine_unsafe_fcurves(
            _export_relevant_actions(export_objects, props, wanted_names)
        )
        if quarantine_records:
            print(
                "[Binary Animation Export] Temporarily skipped "
                f"{len(quarantine_records)} unsupported F-curve(s)."
            )

        _progress(progress_callback, 22, "Sampling evaluated animation through Blender glTF")
        export_gltf(
            staging_path,
            props,
            context,
            objects=export_objects,
            animations=True,
            export_format="GLB",
            animation_mode=animation_mode,
            frame_step=frame_step,
            staging=True,
            manage_state=False,
            quarantine_fcurves=False,
        )
        animation_document, animation_binary = _read_glb(staging_path)
        raw_animations = _extract_raw_animation_data(
            animation_document,
            animation_binary,
            wanted_names,
            excluded_names,
            scene_fps,
        )
        raw_animations = _resample_raw_animations(raw_animations, sampling_fps)
        if wanted_names is not None:
            exported_names = {animation["name"] for animation in raw_animations}
            missing = sorted(wanted_names - exported_names, key=str.casefold)
            if missing:
                raise RuntimeError(
                    "Blender did not emit these character Actions: " + ", ".join(missing)
                )
        if not raw_animations:
            raise RuntimeError(
                "The selected animation mode emitted no supported transform or morph clips."
            )
        _progress(progress_callback, 60, "Extracted evaluated glTF-space tracks")

        bone_names, targets, target_ids, mapping_hash = _build_target_mapping(
            base_document,
            raw_animations,
        )
        optimized_animations = []
        for raw_animation in raw_animations:
            optimized_tracks = []
            removed_validation = {
                "position": 0.0,
                "quaternion": 0.0,
                "scale": 0.0,
                "morph": 0.0,
            }
            for raw_track in raw_animation["tracks"]:
                optimized = _optimize_binary_track(raw_track, props)
                if optimized:
                    optimized_tracks.append(optimized)
                elif raw_track["rest"] is not None:
                    removed_validation[raw_track["property"]] = max(
                        removed_validation[raw_track["property"]],
                        max(
                            (
                                _property_error(
                                    raw_track["property"],
                                    value,
                                    raw_track["rest"],
                                )
                                for value in raw_track["values"]
                            ),
                            default=0.0,
                        ),
                    )
            optimized_animations.append({
                "name": raw_animation["name"],
                "duration": raw_animation["duration"],
                "fps": raw_animation["fps"],
                "tracks": optimized_tracks,
                "original_tracks": len(raw_animation["tracks"]),
                "removed_validation": removed_validation,
                "original_keys": sum(
                    len(track["times"])
                    for track in raw_animation["tracks"]
                ),
            })

        manifest_animations = {}
        reports = []
        assets = []
        named = _safe_animation_filenames(optimized_animations)
        for index, (animation, filename) in enumerate(named):
            content, encoded = _encode_binary_animation(
                animation,
                target_ids,
                mapping_hash,
                props,
            )
            _atomic_write_binary(os.path.join(work_animation_dir, filename), content)
            original_keys = animation["original_keys"]
            optimized_keys = encoded["keyframes"]
            reduction = (
                0.0
                if original_keys == 0
                else (1.0 - optimized_keys / original_keys) * 100.0
            )
            errors = encoded["validation"]
            stem = os.path.splitext(filename)[0]
            metadata = {
                "file": "animations/" + filename,
                "module": "animations/" + stem + ".ts",
                "duration": round_float(animation["duration"], precision),
                "fps": round_float(animation["fps"], precision),
                "tracks": encoded["tracks"],
                "keyframes": optimized_keys,
                "size": encoded["size"],
                "contentHash": encoded["contentHash"],
                "validation": {
                    "originalSampledKeyframes": original_keys,
                    "optimizedKeyframes": optimized_keys,
                    "reductionPercent": round(reduction, 2),
                    "maxPositionError": errors["position"],
                    "maxRotationErrorDegrees": math.degrees(errors["quaternion"]),
                    "maxScaleError": errors["scale"],
                    "maxMorphError": errors["morph"],
                },
            }
            manifest_animations[animation["name"]] = metadata
            asset = {"name": animation["name"], **metadata}
            assets.append(asset)
            reports.append((animation["name"], metadata))
            _progress(
                progress_callback,
                64 + int(25 * (index + 1) / max(1, len(named))),
                f"Encoded {animation['name']}",
            )

        manifest = {
            "version": FORMAT_VERSION,
            "format": "T3AN",
            "generator": "Web3D Asset Compiler",
            "character": glb_filename,
            "characterHash": f"{_fnv1a32_file(base_path):08x}",
            "characterSize": os.path.getsize(base_path),
            "source": os.path.basename(bpy.data.filepath) if bpy.data.filepath else None,
            "mappingHash": mapping_hash,
            "targets": targets,
            "skeleton": bone_names,
            "objects": [
                entry["name"]
                for entry in targets
                if entry["kind"] == "object"
            ],
            "properties": {
                "position": 0,
                "quaternion": 1,
                "scale": 2,
                "morphTarget": 3,
            },
            "modules": {
                "controller": "model_controller.ts",
                "factory": "animationClipFactory.ts",
                "index": "animations/index.ts",
            },
            "animations": manifest_animations,
        }
        manifest_path = os.path.join(work_dir, MANIFEST_FILENAME)
        _atomic_write_text(
            manifest_path,
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            ) + "\n",
        )
        ts_files = write_typescript_modules(work_dir, glb_filename, assets)

        relative_files = [
            glb_filename,
            *("animations/" + filename for _animation, filename in named),
            *(
                os.path.relpath(filepath, work_dir).replace(os.sep, "/")
                for filepath in ts_files
            ),
            MANIFEST_FILENAME,
        ]
        published = _publish_working_tree(work_dir, output_dir, relative_files)
        _progress(progress_callback, 98, "Validated files and published manifest/modules")

        print("\n" + "=" * 84)
        print("THREE.JS BINARY CHARACTER EXPORT COMPLETE")
        print("=" * 84)
        print(f"Character GLB: {os.path.join(output_dir, glb_filename)}")
        print(f"Manifest: {os.path.join(output_dir, MANIFEST_FILENAME)}")
        print(
            f"Armature: {armature.name} | Skeleton: {len(bone_names)} | "
            f"Targets: {len(targets)} | Sampling: {sampling_fps:g} FPS"
        )
        for name, metadata in reports:
            validation = metadata["validation"]
            print(
                f"  {name}: {metadata['tracks']} tracks | "
                f"{validation['originalSampledKeyframes']} -> {metadata['keyframes']} keys "
                f"({validation['reductionPercent']:.2f}% reduction) | "
                f"{_human_size(metadata['size'])}"
            )
        print("=" * 84)
        _progress(progress_callback, 100, "Complete")
        export_succeeded = True
        return {
            "character_glb": os.path.join(output_dir, glb_filename),
            "manifest": os.path.join(output_dir, MANIFEST_FILENAME),
            "animations": manifest_animations,
            "tracks": sum(item["tracks"] for item in manifest_animations.values()),
            "keyframes": sum(
                item["keyframes"]
                for item in manifest_animations.values()
            ),
            "files": published,
        }
    finally:
        cleanup_error = None
        if quarantine:
            try:
                restore_quarantined_fcurves(quarantine, quarantine_records)
            except Exception as exc:
                cleanup_error = exc
                print(
                    "[Binary Animation Export] ERROR restoring quarantined F-curves; "
                    "copies remain in __TJS_EXPORT_QUARANTINE__."
                )
                traceback.print_exc()
        try:
            _restore_scene_state(state)
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc
            print("[Binary Animation Export] ERROR restoring Blender scene state.")
            traceback.print_exc()
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        if export_succeeded and cleanup_error is not None:
            raise cleanup_error


__all__ = [
    "FORMAT_VERSION",
    "MAGIC",
    "MANIFEST_FILENAME",
    "animation_export_readiness",
    "export_character_and_animations",
    "read_glb_animation_names",
    "round_float",
    "safe_filename",
]
