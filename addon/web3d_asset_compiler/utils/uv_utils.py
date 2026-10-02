"""
UV layer preservation and implicit texture map handling.
"""

import bpy

IMAGE_UV_BACKUP_LAYER = "AHB_ImageUV_Backup"
IMAGE_UV_SOURCE_PROP = "ahb_image_uv_source"
IMAGE_UV_BACKUP_PROP = "ahb_image_uv_backup"
DEFAULT_BAKE_NODE_TAG = "AHB_BakeTarget"


def _bake_node_tag(tag):
    tag = str(tag or "").strip()
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


def _uv_island_bounds(obj_names, layer_name):
    """Axis-aligned bounds per UV island.

    Islands are found by *UV connectivity*, not by `edge.seam`. In Blender 5.2
    `bpy.ops.uv.smart_project` marks no seams at all (measured: zero seams and
    zero sharp edges on a plain cube); it splits islands purely by separating
    coincident UVs. Walking seams therefore merges every face of an object into
    one island, which reported a zero gutter for a correctly packed atlas.

    Two faces are in the same island when they share a mesh edge and the UVs on
    both sides of that edge are identical. `BMEdge` exposes no loop accessor in
    5.2, so the pairs are collected from the face loops directly.
    """
    import bmesh

    EPS = 1e-6
    bounds = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
            continue
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.get(layer_name)
            if not uv_layer:
                continue

            edge_faces = {}
            edge_uvs = {}
            for face in bm.faces:
                loops = face.loops
                count = len(loops)
                for index in range(count):
                    here = loops[index]
                    nxt = loops[(index + 1) % count]
                    a, b = here.vert.index, nxt.vert.index
                    key = (a, b) if a <= b else (b, a)
                    edge_faces.setdefault(key, []).append(face.index)
                    edge_uvs.setdefault(key, []).append(
                        (here[uv_layer].uv, nxt[uv_layer].uv))

            neighbours = {face.index: [] for face in bm.faces}
            for key, faces in edge_faces.items():
                if len(faces) != 2:
                    continue  # boundary or non-manifold: an island edge
                pairs = edge_uvs.get(key, [])
                if len(pairs) != 2:
                    continue
                (u1, v1), (u2, v2) = pairs
                if (abs(u1.x - u2.x) <= EPS and abs(u1.y - u2.y) <= EPS
                        and abs(v1.x - v2.x) <= EPS and abs(v1.y - v2.y) <= EPS):
                    neighbours[faces[0]].append(faces[1])
                    neighbours[faces[1]].append(faces[0])

            visited = set()
            for face in bm.faces:
                if face.index in visited:
                    continue
                stack = [face.index]
                xs, ys = [], []
                while stack:
                    index = stack.pop()
                    if index in visited:
                        continue
                    visited.add(index)
                    for loop in bm.faces[index].loops:
                        xs.append(loop[uv_layer].uv.x)
                        ys.append(loop[uv_layer].uv.y)
                    stack.extend(neighbours[index])
                if xs and ys:
                    bounds.append((min(xs), max(xs), min(ys), max(ys)))
        finally:
            bm.free()
    return bounds


def _min_box_gap(boxes):
    """Smallest distance between any two boxes. None when there is only one."""
    best = float('inf')
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            du = max(b[0] - a[1], a[0] - b[1], 0.0)
            dv = max(b[2] - a[3], a[2] - b[3], 0.0)
            distance = (du * du + dv * dv) ** 0.5
            if distance < best:
                best = distance
    return None if best == float('inf') else best


def island_bounds_and_gap(obj_names, layer_name, target_gap=0.0):
    """Return (gap_uv, uv_min, uv_max, island_count) for a group.

    gap_uv is the smallest distance between the bounds of two different UV
    islands, clamped to `target_gap`: it answers "is the gutter at least this
    wide?", which is the question a lightmap atlas has to get right. Pass the
    target as `target_gap` in UV units for the fast and exact result.

    `target_gap` also sizes the spatial hash, which is what keeps this cheap:
    any pair closer than the target must share or neighbour a cell, so a 3x3
    scan is exhaustive for the distances that matter. A global minimum would
    need an unbounded neighbourhood and an O(n^2) scan.

    Blender's own overlap test cannot measure this. `bpy.ops.uv.select_overlap`
    applies a *relative* along-the-edge bias to avoid shared-vertex false
    positives, so islands 0.005 UV apart still read as "not overlapping".
    """
    boxes = _uv_island_bounds(obj_names, layer_name)
    if not boxes:
        return None, 0.0, 0.0, 0

    xs = [v for a in boxes for v in (a[0], a[1])]
    uv_min, uv_max = min(xs), max(xs)
    if len(boxes) == 1:
        return target_gap, uv_min, uv_max, 1

    span = max(uv_max - uv_min, 1e-9)
    if target_gap <= 0.0:
        # No target means the caller wants the true global minimum, which the
        # bounded scan below cannot give. Fall back to brute force rather than
        # returning a "close enough" zero that reads as overlapping.
        best = _min_box_gap(boxes)
        return best, uv_min, uv_max, len(boxes)

    cell = max(target_gap, span / 512.0, 1e-9)
    grid = {}
    for index, box in enumerate(boxes):
        for cu in range(int((box[0] - uv_min) / cell),
                        int((box[1] - uv_min) / cell) + 1):
            for cv in range(int((box[2] - uv_min) / cell),
                            int((box[3] - uv_min) / cell) + 1):
                grid.setdefault((cu, cv), []).append(index)

    best = float('inf')
    for (cu, cv), members in grid.items():
        neighbours = []
        for ou in (-1, 0, 1):
            for ov in (-1, 0, 1):
                neighbours.extend(grid.get((cu + ou, cv + ov), ()))
        for i in members:
            a = boxes[i]
            for j in neighbours:
                if j <= i:
                    continue
                b = boxes[j]
                du = max(b[0] - a[1], a[0] - b[1], 0.0)
                dv = max(b[2] - a[3], a[2] - b[3], 0.0)
                d = (du * du + dv * dv) ** 0.5
                if d < best:
                    best = d

    # Nothing closer than one cell: the gutter is at least the target.
    if best == float('inf'):
        return target_gap, uv_min, uv_max, len(boxes)
    return best, uv_min, uv_max, len(boxes)
