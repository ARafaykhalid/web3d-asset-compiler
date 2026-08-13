"""
JSON export and import helpers for materials and bake parameters.
"""

import bpy
import json
import os


def get_principled_bsdf(mat):
    if not mat or not mat.use_nodes:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def extract_socket_value(socket):
    if socket.is_linked:
        link = socket.links[0]
        node = link.from_node
        if node.type == 'TEX_IMAGE' and node.image:
            return {'type': 'texture', 'path': bpy.path.abspath(node.image.filepath)}
        elif node.type == 'NORMAL_MAP':
            return extract_socket_value(node.inputs['Color'])
    else:
        val = socket.default_value
        if hasattr(val, '__len__'):
            return {'type': 'vector', 'value': list(val)}
        elif isinstance(val, (int, float)):
            return {'type': 'float', 'value': val}
    return None


def load_texture(path):
    if os.path.exists(path):
        for img in bpy.data.images:
            if img.filepath == path or bpy.path.abspath(img.filepath) == path:
                return img
        try:
            return bpy.data.images.load(path)
        except Exception:
            return None
    return None
