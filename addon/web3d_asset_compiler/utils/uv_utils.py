"""
UV layer preservation and implicit texture map handling.
"""

import bpy

IMAGE_UV_BACKUP_LAYER = "AHB_ImageUV_Backup"
IMAGE_UV_SOURCE_PROP = "ahb_image_uv_source"
IMAGE_UV_BACKUP_PROP = "ahb_image_uv_backup"


def get_render_uv_name(mesh, excluded_name=""):
    """Return the UV map used by existing implicit texture coordinates."""
    if isinstance(excluded_name, str):
        excluded_names = {excluded_name} if excluded_name else set()
    else:
        excluded_names = set(excluded_name)
    for uv_layer in mesh.uv_layers:
        if (uv_layer.name not in excluded_names
                and getattr(uv_layer, 'active_render', False)):
            return uv_layer.name
    active = mesh.uv_layers.active
    if active and active.name not in excluded_names:
        return active.name
    for uv_layer in mesh.uv_layers:
        if uv_layer.name not in excluded_names:
            return uv_layer.name
    return ""


def copy_image_uv_backup(obj, source_name):
    """Copy an image material's source UVs to a stable per-mesh layer."""
    mesh = obj.data
    source = mesh.uv_layers.get(source_name)
    if not source:
        return ""

    backup_name = mesh.get(IMAGE_UV_BACKUP_PROP, "")
    backup = mesh.uv_layers.get(backup_name) if backup_name else None
    if not backup:
        backup = mesh.uv_layers.new(name=IMAGE_UV_BACKUP_LAYER)
        backup_name = backup.name

    if source != backup and len(source.data) == len(backup.data):
        for source_loop, backup_loop in zip(source.data, backup.data):
            backup_loop.uv = source_loop.uv

    obj[IMAGE_UV_SOURCE_PROP] = source_name
    obj[IMAGE_UV_BACKUP_PROP] = backup_name
    mesh[IMAGE_UV_BACKUP_PROP] = backup_name
    return backup.name


def preserve_implicit_texture_uvs(obj_names, props):
    """Pin unconnected source Image Texture nodes to their original UV map."""
    bake_uv_name = props.uv_layer_name.strip() or "BakeLightmap"
    count = 0

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if not mesh.uv_layers:
            continue

        render_uv_name = get_render_uv_name(mesh, excluded_name=bake_uv_name)
        if not render_uv_name:
            continue

        backup_uv_name = copy_image_uv_backup(obj, render_uv_name)

        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue

            nodes = mat.node_tree.nodes
            links = mat.node_tree.links

            for node in nodes:
                if node.type != 'TEX_IMAGE' or node.label.startswith(props.image_node_name):
                    continue

                vector_socket = node.inputs.get('Vector')
                if not vector_socket or vector_socket.is_linked:
                    continue

                uv_node = nodes.new(type='ShaderNodeUVMap')
                uv_node.location = (node.location.x - 220, node.location.y)
                uv_node.uv_map = backup_uv_name or render_uv_name
                uv_node.label = f"AHB_SourceUV_{obj.name}"
                links.new(uv_node.outputs['UV'], vector_socket)
                count += 1

    return count
