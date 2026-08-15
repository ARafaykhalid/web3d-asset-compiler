"""
UV layer preservation and implicit texture map handling.
"""

import bpy

IMAGE_UV_BACKUP_LAYER = "AHB_ImageUV_Backup"
IMAGE_UV_SOURCE_PROP = "ahb_image_uv_source"
IMAGE_UV_BACKUP_PROP = "ahb_image_uv_backup"
DEFAULT_BAKE_NODE_TAG = "AHB_BakeTarget"


def _bake_node_tag(tag):
    tag = str(tag).strip() if tag is not None else ""
    return tag or DEFAULT_BAKE_NODE_TAG


def _has_real_image_textures(mat, bake_tag):
    if not mat or not mat.use_nodes or not mat.node_tree:
        return False

    for node in mat.node_tree.nodes:
        if node.type != 'TEX_IMAGE' or not node.image:
            continue
        if node.name.startswith(bake_tag):
            continue
        if node.image.size[0] <= 0 or node.image.size[1] <= 0:
            continue
        return True
    return False


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
    """Pin implicit texture coordinates to the pre-bake render UV map."""
    bake_uv_name = props.uv_layer_name.strip() or "BakeLightmap"
    bake_tag = _bake_node_tag(props.image_node_name)
    material_uv = {}

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if not mesh.uv_layers:
            continue

        backup_layer_name = mesh.get(
            IMAGE_UV_BACKUP_PROP,
            obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
        uv_name = get_render_uv_name(
            mesh, excluded_name={bake_uv_name, backup_layer_name})
        if not uv_name:
            remembered = obj.get(IMAGE_UV_SOURCE_PROP)
            if remembered in mesh.uv_layers:
                uv_name = remembered
            elif backup_layer_name in mesh.uv_layers:
                uv_name = backup_layer_name
        if not uv_name:
            continue

        image_materials = {
            slot.material.name for slot in obj.material_slots
            if slot.material
            and _has_real_image_textures(slot.material, bake_tag)
        }
        backup_uv_name = ""
        if image_materials:
            backup_uv_name = copy_image_uv_backup(obj, uv_name)

        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            pinned_uv_name = (backup_uv_name
                              if mat.name in image_materials
                              else uv_name)
            material_uv.setdefault(mat.name, pinned_uv_name)

    linked = 0
    for mat_name, uv_name in material_uv.items():
        mat = bpy.data.materials.get(mat_name)
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        uv_node_name = f"AHB_SourceUV_{uv_name}"
        uv_node = nodes.get(uv_node_name)

        def ensure_uv_node(anchor):
            nonlocal uv_node
            if not uv_node or uv_node.type != 'UVMAP':
                uv_node = nodes.new('ShaderNodeUVMap')
                uv_node.name = uv_node_name
            uv_node.label = f"Preserved Source UV: {uv_name}"
            uv_node.uv_map = uv_name
            uv_node.location = (anchor.location.x - 220, anchor.location.y)
            return uv_node

        for node in list(nodes):
            if node.type != 'TEX_COORD' or not node.outputs.get('UV'):
                continue
            for link in list(node.outputs['UV'].links):
                target_socket = link.to_socket
                links.remove(link)
                links.new(ensure_uv_node(node).outputs['UV'], target_socket)
                linked += 1

        for node in list(nodes):
            vector_socket = node.inputs.get('Vector') if node.type == 'TEX_IMAGE' else None
            if (not vector_socket or node.name.startswith(bake_tag)
                    or vector_socket.is_linked):
                continue
            links.new(ensure_uv_node(node).outputs['UV'], vector_socket)
            linked += 1

    return linked


def restore_image_uv_backups(obj_names, bake_tag=DEFAULT_BAKE_NODE_TAG):
    """Restore saved image UV layers as the active render coordinates."""
    bake_tag = _bake_node_tag(bake_tag)
    restored = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue

        mesh = obj.data
        backup_name = mesh.get(
            IMAGE_UV_BACKUP_PROP,
            obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
        uv_layer = mesh.uv_layers.get(backup_name)
        if not uv_layer:
            continue
        if not any(
                slot.material
                and _has_real_image_textures(slot.material, bake_tag)
                for slot in obj.material_slots):
            continue

        mesh.uv_layers.active = uv_layer
        try:
            uv_layer.active_render = True
        except (AttributeError, RuntimeError):
            pass
        restored += 1

    return restored
