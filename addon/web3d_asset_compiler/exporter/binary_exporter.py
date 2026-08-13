"""
Compact binary animation exporter (.anim) and animation-manifest.json builder.
"""

import json
import math
import os
import re
import struct
import bpy

from .quarantine import quarantine_unsafe_fcurves, restore_quarantined_fcurves

MAGIC = b"T3AN"
FORMAT_VERSION = 1
HEADER_SIZE = 32
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

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def read_glb_animation_names(filepath):
    """Read exact animation names from the JSON chunk of a GLB file."""
    with open(filepath, "rb") as handle:
        magic, version, total_length = struct.unpack("<4sII", handle.read(12))
        if magic != b"glTF" or version != 2:
            raise ValueError("The exported file is not a glTF 2.0 binary.")

        while handle.tell() < total_length:
            chunk_length, chunk_type = struct.unpack("<II", handle.read(8))
            chunk_data = handle.read(chunk_length)
            if chunk_type != 0x4E4F534A:
                continue
            document = json.loads(chunk_data.rstrip(b"\x00 \t\r\n").decode("utf-8"))
            return [
                animation.get("name") or f"Animation_{index + 1}"
                for index, animation in enumerate(document.get("animations", []))
            ]
    return []


def safe_filename(name):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name).strip(" .")
    if not filename:
        filename = "Animation"
    if filename.upper() in WINDOWS_RESERVED:
        filename = f"Animation_{filename}"
    return filename


def round_float(value, precision=5):
    rounded = round(float(value), precision)
    return 0.0 if abs(rounded) < 10.0 ** (-precision) else rounded


def encode_binary_animation(animation_data):
    """Pack animation data into compact binary format (.anim)."""
    name_bytes = animation_data["name"].encode("utf-8")
    tracks = animation_data.get("tracks", [])
    
    header = struct.pack(
        "<4sHHffII8s",
        MAGIC,
        FORMAT_VERSION,
        len(name_bytes),
        float(animation_data.get("duration", 0.0)),
        float(animation_data.get("fps", 30.0)),
        len(tracks),
        0,
        b"\x00" * 8
    )
    
    body = bytearray(name_bytes)
    if len(body) % 4 != 0:
        body.extend(b"\x00" * (4 - (len(body) % 4)))
        
    for track in tracks:
        target_bytes = track["target"].encode("utf-8")
        prop_code = PROPERTY_CODES.get(track.get("property", "position"), 0)
        interp_code = INTERPOLATION_CODES.get(track.get("interpolation", "LINEAR"), 0)
        
        times = track.get("times", [])
        values = track.get("values", [])
        
        track_header = struct.pack(
            "<HHBBBBII",
            len(target_bytes),
            0,
            prop_code,
            interp_code,
            ENCODING_FLOAT32,
            TIME_FLOAT32,
            len(times),
            len(values)
        )
        body.extend(track_header)
        body.extend(target_bytes)
        if len(body) % 4 != 0:
            body.extend(b"\x00" * (4 - (len(body) % 4)))
            
        for t in times:
            body.extend(struct.pack("<f", float(t)))
        for v in values:
            body.extend(struct.pack("<f", float(v)))

    return header + bytes(body)
