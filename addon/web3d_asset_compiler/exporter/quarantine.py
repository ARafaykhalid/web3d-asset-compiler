"""
F-curve quarantine and safety utilities for Blender 5.1 slotted Actions.
Prevents Blender's glTF exporter from crashing on invalid array index F-curves.
"""

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


def is_unsafe_fcurve(fcurve):
    """Match the component lengths used internally by Blender's glTF exporter."""
    target = fcurve.data_path.rsplit(".", 1)[-1]
    if target.startswith("["):
        target = ""
    target_length = TARGET_LENGTHS.get(target, 1)
    return fcurve.array_index < 0 or fcurve.array_index >= target_length


def quarantine_unsafe_fcurves():
    """Temporarily move glTF-crashing curves to an incompatible Action slot."""
    quarantine = bpy.data.actions.new("__TJS_EXPORT_QUARANTINE__")
    slot = quarantine.slots.new("SCENE", "TJS Export Quarantine")
    layer = quarantine.layers.new("Quarantine")
    strip = layer.strips.new(type="KEYFRAME")
    destination = strip.channelbags.new(slot).fcurves
    records = []

    for action in list(bpy.data.actions):
        if action == quarantine:
            continue
        for collection in iter_action_fcurve_collections(action):
            for fcurve in list(collection):
                if not is_unsafe_fcurve(fcurve):
                    continue
                copy = destination.new_from_fcurve(fcurve)
                records.append((collection, copy, action.name, fcurve.data_path,
                                fcurve.array_index))
                collection.remove(fcurve)
    return quarantine, records


def restore_quarantined_fcurves(quarantine, records):
    for collection, copy, _action_name, _data_path, _array_index in records:
        collection.new_from_fcurve(copy)
    if quarantine:
        bpy.data.actions.remove(quarantine)
