"""
F-curve quarantine and safety utilities for Blender 5.1 slotted Actions.
Prevents Blender's glTF exporter from crashing on invalid array index F-curves.
"""

import re

import bpy

TARGET_LENGTHS = {
    "delta_location": 3,
    "delta_rotation_euler": 3,
    "delta_rotation_quaternion": 4,
    "delta_scale": 3,
    "location": 3,
    "rotation_axis_angle": 4,
    "rotation_euler": 3,
    "rotation_quaternion": 4,
    "scale": 3,
    "value": 1,
}

_BONE_TRANSFORM_RE = re.compile(
    r'^pose\.bones\["(?:\\.|[^"])*"\]\.(?P<target>[A-Za-z_]+)$'
)
_SHAPE_KEY_VALUE_RE = re.compile(
    r'^key_blocks\["(?:\\.|[^"])*"\]\.value$'
)


def iter_action_fcurve_collections(action):
    """Yield each F-curve collection for legacy and Blender 5 slotted Actions."""
    found_slotted_curves = False
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for channelbag in getattr(strip, "channelbags", []):
                found_slotted_curves = True
                yield channelbag.fcurves

    if found_slotted_curves:
        return
    try:
        yield action.fcurves
    except (AttributeError, RuntimeError):
        pass


def _target_length(data_path):
    if data_path in TARGET_LENGTHS and data_path != "value":
        return TARGET_LENGTHS[data_path]

    bone_match = _BONE_TRANSFORM_RE.fullmatch(data_path)
    if bone_match:
        return TARGET_LENGTHS.get(bone_match.group("target"))

    if _SHAPE_KEY_VALUE_RE.fullmatch(data_path):
        return TARGET_LENGTHS["value"]
    return None


def is_unsafe_fcurve(fcurve):
    """Return true only for malformed transform or shape-key component curves."""
    target_length = _target_length(fcurve.data_path)
    if target_length is None:
        return False
    return fcurve.array_index < 0 or fcurve.array_index >= target_length


def quarantine_unsafe_fcurves(actions):
    """Temporarily move unsafe curves from the Actions used by this export."""
    quarantine = None
    records = []
    try:
        quarantine = bpy.data.actions.new("__TJS_EXPORT_QUARANTINE__")
        slot = quarantine.slots.new("SCENE", "TJS Export Quarantine")
        layer = quarantine.layers.new("Quarantine")
        strip = layer.strips.new(type="KEYFRAME")
        destination = strip.channelbags.new(slot).fcurves

        for action in list(dict.fromkeys(actions)):
            if action == quarantine:
                continue
            unsafe = [
                (collection, fcurve)
                for collection in iter_action_fcurve_collections(action)
                for fcurve in list(collection)
                if is_unsafe_fcurve(fcurve)
            ]
            if unsafe and not getattr(action, "is_editable", True):
                raise RuntimeError(
                    f"Linked Action '{action.name}' contains an invalid transform "
                    "F-curve and cannot be repaired temporarily. Make it local first."
                )
            for collection, fcurve in unsafe:
                copy = destination.new_from_fcurve(fcurve)
                record = (
                    collection,
                    copy,
                    action.name,
                    fcurve.data_path,
                    fcurve.array_index,
                )
                collection.remove(fcurve)
                records.append(record)
        return quarantine, records
    except Exception:
        rollback_failed = False
        for (
            collection,
            copy,
            _action_name,
            _data_path,
            _array_index,
        ) in reversed(records):
            try:
                collection.new_from_fcurve(copy)
            except Exception:
                rollback_failed = True
        if quarantine and not rollback_failed:
            try:
                bpy.data.actions.remove(quarantine)
            except Exception:
                pass
        raise


def restore_quarantined_fcurves(quarantine, records):
    restore_error = None
    for collection, copy, _action_name, _data_path, _array_index in records:
        try:
            collection.new_from_fcurve(copy)
        except Exception as exc:
            if restore_error is None:
                restore_error = exc
    if quarantine and restore_error is None:
        bpy.data.actions.remove(quarantine)
    if restore_error is not None:
        raise restore_error
