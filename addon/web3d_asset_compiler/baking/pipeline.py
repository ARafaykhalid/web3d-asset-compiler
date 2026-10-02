"""
Core lightmap and texture baking pipeline for Web3D Asset Compiler.
"""

import bpy
import bmesh
import math
import os
import re
from mathutils import Vector

from ..utils import (
    force_ui_redraw,
    island_bounds_and_gap,
    safe_filename,
    prepare_mesh_data,
    prepare_material_slots,
    preserve_implicit_texture_uvs,
    restore_image_uv_backups,
)
from .properties import (
    NODE_TAG,
    IMAGE_UV_BACKUP_LAYER,
    IMAGE_UV_BACKUP_PROP,
    PASS_FILTER_TYPES,
    LINEAR_COLORSPACES,
    DATA_COLORSPACES,
    SRGB_COLORSPACES,
    EXT_MAP,
)

BAKE_COLOR_ATTRIBUTE = "AHB_BakedColor"
BAKE_COLOR_ATTRIBUTE_PROP = "ahb_baked_color_attribute"
OWNED_IMAGE_PROP = "ahb_owned_bake_image"
OWNED_MATERIAL_PROP = "ahb_owned_backup"
BACKUP_SOURCE_PROP = "ahb_backup_source"
RENAME_BASE_PROP = "ahb_rename_base"


def normalize_core_names(props):
    """Keep user-editable identifiers safe and non-empty."""
    if not props.output_prefix.strip():
        props.output_prefix = "bake_"
    if not props.image_node_name.strip():
        props.image_node_name = NODE_TAG
    if not props.uv_layer_name.strip():
        props.uv_layer_name = "BakeLightmap"
    if not props.collection_atlas_prefix.strip():
        props.collection_atlas_prefix = "Texture_Pack_"


def bake_node_tag(tag):
    tag = str(tag).strip() if tag is not None else ""
    return tag or NODE_TAG


def get_resolution(props):
    if props.resolution == 'CUSTOM':
        return props.custom_res_x, props.custom_res_y
    v = int(props.resolution)
    return v, v


def get_collection_atlas_name(props, atlas_number):
    return f"{props.collection_atlas_prefix}{atlas_number}"


def safe_set_colorspace(image, is_hdr, is_data=False):
    """Set the image colorspace, raising if no candidate applies.

    Returning silently on failure left data bakes (AO, Normal, Roughness,
    Shadow, UV) on the default sRGB curve. That gamma-shifts every stored
    value and produces a lightmap that still looks plausible in the viewport
    and is wrong in the render, so it must be loud instead.
    """
    if is_data:
        candidates = DATA_COLORSPACES
    else:
        candidates = LINEAR_COLORSPACES if is_hdr else SRGB_COLORSPACES
    for cs in candidates:
        try:
            image.colorspace_settings.name = cs
        except (TypeError, RuntimeError):
            continue
        if image.colorspace_settings.name == cs:
            return cs
    raise RuntimeError(
        f"No usable colorspace for '{image.name}' "
        f"(tried {', '.join(candidates)}). Either the bake passes are "
        f"non-color data that needs a linear/non-color curve, or this OCIO "
        f"config is missing them; set the curve by hand and re-bake."
    )


def owned_bake_images(props):
    """Yield only the bake images this addon created.

    Never match on `output_prefix`: that prefix is user-editable and collides
    with ordinary asset names, which would let the addon overwrite or delete
    textures the user owns. Ownership is tracked per datablock instead.
    """
    prefix = props.output_prefix
    for img in bpy.data.images:
        if img.get(OWNED_IMAGE_PROP):
            yield img
        elif img.source == 'GENERATED' and img.name.startswith(prefix):
            # Pre-existing images from older builds have no ownership tag.
            # GENERATED + never saved to disk is safe to adopt.
            if not img.filepath:
                img[OWNED_IMAGE_PROP] = True
                yield img


def get_or_create_image(props, name):
    normalize_core_names(props)
    rx, ry = get_resolution(props)
    is_hdr = props.image_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR')
    is_data = props.bake_type in ('AO', 'NORMAL', 'ROUGHNESS', 'SHADOW', 'UV')
    img_name = f"{props.output_prefix}{name}"

    img = bpy.data.images.get(img_name)
    if img and not img.get(OWNED_IMAGE_PROP):
        # Name belongs to a user texture. Never remove it; suffix until free.
        suffix = 1
        while bpy.data.images.get(f"{img_name}.{suffix}"):
            suffix += 1
        img_name = f"{img_name}.{suffix}"
        img = None
    if img:
        if img.size[0] != rx or img.size[1] != ry:
            bpy.data.images.remove(img)
            img = None
        if img and img.is_float != is_hdr:
            bpy.data.images.remove(img)
            img = None

    if not img:
        img = bpy.data.images.new(
            img_name,
            width=rx,
            height=ry,
            float_buffer=is_hdr,
            alpha=(props.color_mode == 'RGBA'),
        )
    img[OWNED_IMAGE_PROP] = True

    safe_set_colorspace(img, is_hdr, is_data)
    return img


def get_collection_atlas_image(props, atlas_number):
    return get_or_create_image(props, get_collection_atlas_name(props, atlas_number))


def get_tile_image(props, tile_idx):
    tile_name = f"tile_{tile_idx + 1:02d}"
    return get_or_create_image(props, tile_name)


def get_atlas_groups(obj_names, props, report=None):
    scope_names = set(obj_names)
    assigned = {}
    duplicate_memberships = []
    missing_collections = []
    groups = []

    for atlas_number in range(1, props.collection_atlas_count + 1):
        collection_name = get_collection_atlas_name(props, atlas_number)
        collection = bpy.data.collections.get(collection_name)
        if not collection:
            missing_collections.append(collection_name)
            groups.append((atlas_number, []))
            continue

        candidates = sorted({
            obj.name for obj in collection.all_objects
            if obj.type == 'MESH' and obj.name in scope_names
        })
        group_names = []
        for obj_name in candidates:
            previous_number = assigned.get(obj_name)
            if previous_number is not None:
                duplicate_memberships.append((obj_name, previous_number, atlas_number))
                continue
            assigned[obj_name] = atlas_number
            group_names.append(obj_name)
        groups.append((atlas_number, group_names))

    if report and missing_collections:
        preview = ", ".join(missing_collections[:4])
        if len(missing_collections) > 4:
            preview += f", +{len(missing_collections) - 4} more"
        report({'WARNING'}, f"Missing collection(s): {preview}")

    if duplicate_memberships:
        # Each object can only live in one atlas. Silently dropping it from the
        # second made the reported object count lower than the scene without
        # any hint that a texture pack was missing an object.
        preview = ", ".join(
            f"{name} (in atlas {first} and {second})"
            for name, first, second in duplicate_memberships[:4]
        )
        if len(duplicate_memberships) > 4:
            preview += f", +{len(duplicate_memberships) - 4} more"
        report({'WARNING'},
               f"Object(s) in more than one atlas collection, kept only in the "
               f"first: {preview}")

    return groups


def flatten_atlas_groups(groups):
    result = []
    for _atlas_num, obj_names in groups:
        result.extend(obj_names)
    return result


def isolate_materials_between_atlas_groups(groups):
    duplicated = 0
    mat_atlas_map = {}
    for atlas_number, obj_names in groups:
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj: continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat: continue
                prev = mat_atlas_map.get(mat.name)
                if prev is not None and prev != atlas_number:
                    original_name = mat.name
                    new_mat = mat.copy()
                    new_mat.name = f"{original_name}_atlas{atlas_number:02d}"
                    slot.material = new_mat
                    # Both names now belong to this atlas. Without the second
                    # write the next object sharing `original_name` copies
                    # again, producing N-1 duplicate materials per material.
                    mat_atlas_map[original_name] = atlas_number
                    mat = new_mat
                    duplicated += 1
                mat_atlas_map[mat.name] = atlas_number
    return duplicated


def get_object_names(props, context, include_empty=False):
    def is_bake_mesh(obj):
        return bool(
            obj and obj.type == 'MESH'
            and (include_empty or obj.data.polygons))

    if props.bake_scope == 'ACTIVE':
        obj = context.active_object
        return [obj.name] if is_bake_mesh(obj) else []
    if props.bake_scope == 'SELECTED':
        return sorted([o.name for o in context.selected_objects
                       if is_bake_mesh(o)])
    return sorted([o.name for o in context.scene.objects
            if is_bake_mesh(o) and not o.hide_viewport
            and not o.hide_get(view_layer=context.view_layer)])


def resolve_pipeline_targets(context, props, obj_names, report):
    if not props.use_selected_to_active:
        return obj_names, None

    active = context.view_layer.objects.active
    if not active or active.type != 'MESH' or not active.data.polygons:
        report({'ERROR'}, "Selected to Active needs an active mesh target.")
        return [], None
    use_cage = getattr(props, 'use_cage', False)
    cage_object = getattr(props, 'cage_object', None) if use_cage else None
    if use_cage and (
            not cage_object or cage_object.type != 'MESH'
            or not cage_object.data.polygons):
        report({'ERROR'}, "The custom cage must be a non-empty mesh object.")
        return [], None
    if cage_object is active:
        report({'ERROR'}, "The custom cage must be separate from the active bake target.")
        return [], None
    source_names = sorted(
        obj.name for obj in context.selected_objects
        if (obj.type == 'MESH' and obj.data.polygons and obj != active
            and obj != cage_object))
    if not source_names:
        report({'ERROR'}, "Selected to Active needs at least one other selected source mesh.")
        return [], None
    return [active.name], source_names


def collect_material_names(obj_names):
    mat_map = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            if not mat.use_nodes:
                mat.use_nodes = True
            mat_map.setdefault(mat.name, []).append(obj_name)
    return mat_map


def activate_bake_uv(obj_name, props):
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    mesh = obj.data
    layer_name = props.uv_layer_name
    if layer_name in mesh.uv_layers:
        uv_layer = mesh.uv_layers[layer_name]
        mesh.uv_layers.active = uv_layer
        try:
            mesh.uv_layers.active_render = uv_layer
        except (AttributeError, RuntimeError):
            pass


def ensure_bake_color_attribute(obj):
    """Create and activate the color attribute used by vertex-color bakes."""
    if not obj or obj.type != 'MESH':
        return None
    color_attributes = getattr(obj.data, 'color_attributes', None)
    if color_attributes is None:
        return None

    attr_name = str(obj.get(
        BAKE_COLOR_ATTRIBUTE_PROP, BAKE_COLOR_ATTRIBUTE)).strip()
    attr_name = attr_name or BAKE_COLOR_ATTRIBUTE
    attribute = color_attributes.get(attr_name)
    if not attribute:
        attribute = color_attributes.new(
            name=attr_name, type='FLOAT_COLOR', domain='CORNER')

    index = next(
        (index for index, item in enumerate(color_attributes)
         if item == attribute), -1)
    if index >= 0:
        color_attributes.active_color_index = index
        color_attributes.render_color_index = index
    obj[BAKE_COLOR_ATTRIBUTE_PROP] = attribute.name
    return attribute


def get_bake_color_attribute(obj):
    if not obj or obj.type != 'MESH':
        return None
    color_attributes = getattr(obj.data, 'color_attributes', None)
    if color_attributes is None:
        return None
    attr_name = obj.get(BAKE_COLOR_ATTRIBUTE_PROP, BAKE_COLOR_ATTRIBUTE)
    return color_attributes.get(str(attr_name))


def uses_image_bake_target(props):
    return getattr(props, 'bake_target', 'IMAGE_TEXTURES') == 'IMAGE_TEXTURES'


def bake_type_needs_uv(props):
    return (uses_image_bake_target(props)
            or props.bake_type == 'UV'
            or (props.bake_type == 'NORMAL'
                and getattr(props, 'normal_space', 'TANGENT') == 'TANGENT'))


def pixel_margin_to_uv(px, props):
    """Convert a requested inter-island gutter in pixels to a Blender pack margin.

    Blender applies `margin_method='FRACTION'` *per island side*, so the gap
    that ends up between two adjacent islands is twice the value passed in.
    `pack_margin_px` is the total gutter the user asked for (and the bake
    margin is already clamped to half of it in `configure_bake_settings`), so
    halve here. Without this the atlas wastes ~half its area on padding.
    """
    rx, ry = get_resolution(props)
    return (px * 0.5) / min(rx, ry)


def has_real_image_textures(mat, bake_tag):
    bake_tag = bake_node_tag(bake_tag)
    if not mat or not mat.use_nodes:
        return False
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            if node.name.startswith(bake_tag):
                continue
            if node.image.size[0] == 0 or node.image.size[1] == 0:
                continue
            return True
    return False


def force_object_mode():
    try:
        if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def enter_edit_select_all(obj, layer_name):
    """Enter single-object edit mode with all faces and `layer_name` active.

    Two things must happen *before* `mode_set`: deselect everything else, or
    Blender enters multi-object edit mode and every `bpy.ops.uv.*` call that
    follows silently operates on the whole selection instead of this mesh.
    """
    force_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if layer_name in obj.data.uv_layers:
        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')


def subdivide_large_uv_islands(obj_name, layer_name, max_island_ratio=0.15):
    """Mark seams through large or sparse UV islands before re-unwrapping."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
        return False

    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.get(layer_name)
        if not uv_layer:
            return False

        face_island = {}
        island_faces = {}
        visited = set()
        island_id = 0
        for face in bm.faces:
            if face.index in visited:
                continue
            stack = [face]
            faces = []
            while stack:
                current = stack.pop()
                if current.index in visited:
                    continue
                visited.add(current.index)
                face_island[current.index] = island_id
                faces.append(current)
                for edge in current.edges:
                    if edge.seam:
                        continue
                    for linked_face in edge.link_faces:
                        if linked_face.index not in visited:
                            stack.append(linked_face)
            island_faces[island_id] = faces
            island_id += 1

        large_islands = []
        for island_id, faces in island_faces.items():
            min_x = min_y = float('inf')
            max_x = max_y = float('-inf')
            actual_area = 0.0
            for face in faces:
                points = [loop[uv_layer].uv for loop in face.loops]
                for uv in points:
                    min_x = min(min_x, uv.x)
                    min_y = min(min_y, uv.y)
                    max_x = max(max_x, uv.x)
                    max_y = max(max_y, uv.y)
                signed_area = 0.0
                for index, point in enumerate(points):
                    next_point = points[(index + 1) % len(points)]
                    signed_area += point.x * next_point.y - next_point.x * point.y
                actual_area += abs(signed_area) * 0.5

            bounds_area = (max_x - min_x) * (max_y - min_y)
            fill_ratio = actual_area / max(bounds_area, 1e-12)
            if (bounds_area > max_island_ratio
                    or (bounds_area > 0.02 and fill_ratio < 0.6)):
                large_islands.append(island_id)

        marked_any = False
        for island_id in large_islands:
            faces = island_faces[island_id]
            face_indices = {face.index for face in faces}
            if len(face_indices) < 4:
                continue

            interior_edges = {
                edge for face in faces for edge in face.edges
                if not edge.seam
                and sum(linked.index in face_indices
                        for linked in edge.link_faces) == 2
            }
            if not interior_edges:
                continue

            ordered_edges = sorted(
                interior_edges, key=lambda edge: edge.calc_length(), reverse=True)
            for edge in ordered_edges[:max(1, len(ordered_edges) // 3)]:
                edge.seam = True
                marked_any = True

        if marked_any:
            bm.to_mesh(mesh)
            mesh.update()
        return marked_any
    finally:
        bm.free()


def _uv_area(face, uv_layer):
    """Signed-free polygon area in UV space (shoelace)."""
    loops = [loop[uv_layer].uv for loop in face.loops]
    total = 0.0
    for index in range(len(loops)):
        a = loops[index]
        b = loops[(index + 1) % len(loops)]
        total += a.x * b.y - b.x * a.y
    return abs(total) * 0.5


def _uv_perimeter(face, uv_layer):
    """Closed polygon edge length in UV space."""
    loops = [loop[uv_layer].uv for loop in face.loops]
    return sum(
        (loops[(index + 1) % len(loops)] - loops[index]).length
        for index in range(len(loops))
    )


def stack_similar_islands(obj_names, layer_name, area_tol=0.01, perim_tol=0.01):
    """Stack translation-identical islands while rejecting unsafe lookalikes."""
    stacked_total = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
            continue

        mesh = obj.data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.faces.ensure_lookup_table()
            uv_layer = bm.loops.layers.uv.get(layer_name)
            if not uv_layer:
                continue

            islands = []
            visited = set()
            for face in bm.faces:
                if face.index in visited:
                    continue
                stack = [face]
                faces = []
                while stack:
                    current = stack.pop()
                    if current.index in visited:
                        continue
                    visited.add(current.index)
                    faces.append(current)
                    for edge in current.edges:
                        if edge.seam:
                            continue
                        for linked_face in edge.link_faces:
                            if linked_face.index not in visited:
                                stack.append(linked_face)
                islands.append(faces)

            groups = []
            for faces in islands:
                loops = [loop for face in faces for loop in face.loops]
                if not loops:
                    continue
                center = Vector((0.0, 0.0))
                for loop in loops:
                    center += loop[uv_layer].uv
                center /= len(loops)

                uv_shape = tuple(sorted(
                    (round(loop[uv_layer].uv.x - center.x, 6),
                     round(loop[uv_layer].uv.y - center.y, 6))
                    for loop in loops
                ))
                # UV-space area and perimeter, not the 3D ones. The question
                # here is purely "do these islands occupy the same shape in UV
                # space", and bmesh's calc_area()/calc_perimeter() measure the
                # mesh, so identical UV islands on differently scaled meshes
                # failed the tolerance and nothing ever stacked.
                data = {
                    'area': sum(_uv_area(face, uv_layer) for face in faces),
                    'perimeter': sum(
                        _uv_perimeter(face, uv_layer) for face in faces),
                    'center': center,
                    'faces': faces,
                    'signature': (
                        tuple(sorted(len(face.loops) for face in faces)),
                        tuple(sorted(face.material_index for face in faces)),
                        uv_shape,
                    ),
                }

                placed = False
                for group in groups:
                    master = group[0]
                    area_limit = area_tol * max(abs(master['area']), 1e-12)
                    perim_limit = perim_tol * max(abs(master['perimeter']), 1e-12)
                    if (data['signature'] == master['signature']
                            and abs(data['area'] - master['area']) <= area_limit
                            and abs(data['perimeter'] - master['perimeter']) <= perim_limit):
                        group.append(data)
                        placed = True
                        break
                if not placed:
                    groups.append([data])

            for group in groups:
                master = group[0]
                for target in group[1:]:
                    offset = master['center'] - target['center']
                    for face in target['faces']:
                        for loop in face.loops:
                            loop[uv_layer].uv += offset
                    stacked_total += 1

            if any(len(group) > 1 for group in groups):
                bm.to_mesh(mesh)
                mesh.update()
        finally:
            bm.free()

    return stacked_total


def scale_uvs_to_bounds(obj_names, layer_name, margin=0.005):
    """Uniformly scale packed UVs to the available zero-to-one bounds."""
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    has_uvs = False

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
            continue
        uv_layer = obj.data.uv_layers[layer_name]
        for loop in uv_layer.data:
            min_x = min(min_x, loop.uv.x)
            min_y = min(min_y, loop.uv.y)
            max_x = max(max_x, loop.uv.x)
            max_y = max(max_y, loop.uv.y)
            has_uvs = True

    width = max_x - min_x
    height = max_y - min_y
    if not has_uvs or width < 1e-6 or height < 1e-6:
        return False

    margin = max(0.0, min(float(margin), 0.49))
    target = 1.0 - 2.0 * margin
    scale = min(target / width, target / height)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
            continue
        uv_layer = obj.data.uv_layers[layer_name]
        for loop in uv_layer.data:
            loop.uv.x = (loop.uv.x - center_x) * scale + 0.5
            loop.uv.y = (loop.uv.y - center_y) * scale + 0.5
        obj.data.update()
    return True


def world_surface_area(obj):
    if not obj or obj.type != 'MESH':
        return 0.0
    transform = obj.matrix_world.to_3x3()
    verts = obj.data.vertices
    obj.data.calc_loop_triangles()
    area = 0.0
    for tri in obj.data.loop_triangles:
        origin = transform @ verts[tri.vertices[0]].co
        a = transform @ verts[tri.vertices[1]].co - origin
        b = transform @ verts[tri.vertices[2]].co - origin
        area += a.cross(b).length * 0.5
    return area


def apply_world_area_scale(obj_name, layer_name):
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH' or layer_name not in obj.data.uv_layers:
        return
    local_area = sum(poly.area for poly in obj.data.polygons)
    w_area = world_surface_area(obj)
    if local_area <= 1e-12 or w_area <= 1e-12:
        return
    factor = math.sqrt(w_area / local_area)
    if abs(factor - 1.0) < 1e-6:
        return

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uv_layer = bm.loops.layers.uv.get(layer_name)
    if not uv_layer:
        bm.free()
        return
    loops = [loop for face in bm.faces for loop in face.loops]
    if not loops:
        bm.free()
        return
    centroid = Vector((0.0, 0.0))
    for loop in loops:
        centroid += loop[uv_layer].uv
    centroid /= len(loops)
    for loop in loops:
        uv = loop[uv_layer].uv
        loop[uv_layer].uv = centroid + (uv - centroid) * factor
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def apply_image_boost(obj_name, props):
    boost = props.pack_image_boost
    if boost <= 1.01:
        return

    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH' or not obj.material_slots:
        return
    if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
        return

    bake_tag   = props.image_node_name
    layer_name = props.uv_layer_name

    image_mat_indices = set()
    for idx, slot in enumerate(obj.material_slots):
        if slot.material and has_real_image_textures(slot.material, bake_tag):
            image_mat_indices.add(idx)

    if not image_mat_indices:
        return

    enter_edit_select_all(obj, layer_name)

    try:
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if not uv_layer or len(bm.faces) == 0:
            return

        scale_factor = math.sqrt(boost)
        for mat_idx in image_mat_indices:
            faces = [f for f in bm.faces if f.material_index == mat_idx]
            if not faces:
                continue

            all_loops = []
            centroid = Vector((0.0, 0.0))
            count = 0
            for face in faces:
                for loop in face.loops:
                    all_loops.append(loop)
                    centroid += loop[uv_layer].uv
                    count += 1
            if count == 0:
                continue
            centroid /= count

            for loop in all_loops:
                uv = loop[uv_layer].uv
                loop[uv_layer].uv = centroid + (uv - centroid) * scale_factor

        bmesh.update_edit_mesh(obj.data)
    finally:
        force_object_mode()


def run_blender_pack(props, uv_margin):
    """One pack pass over the current selection. Returns True if it ran.

    `scale=True` is not a density preference, it is a correctness requirement.
    Blender only runs the margin line search when at least one island is
    scalable (`uv_pack.cc`: `can_scale_count > 0`); with `scale=False` the
    packer lays islands out to fill and then adds the margin on top, pushing
    the result past 1.0. Measured at 512px, a two-cube tile, 16px requested:

        FRACTION scale=False  ->  gutter 16px but UVs reach 1.042  (overflow)
        FRACTION scale=True   ->  gutter 16px,  UVs reach 0.984  (correct)

    The overflow is silent, and it paints into the neighbouring tile.
    """
    rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'
    rot_method = 'ANY' if props.pack_rotation_step == 'ANY' else 'AXIS_ALIGNED'
    shape_method = 'CONCAVE' if props.pack_nest_holes else props.pack_shape_method

    shared = dict(
        rotate=rotate,
        rotate_method=rot_method,
        scale=True,
        shape_method=shape_method,
        merge_overlap=props.pack_stack_identical,
    )
    # Progressively drop arguments so a future rename of one keyword degrades
    # to a coarser pack rather than to no pack at all.
    attempts = (
        dict(shared, margin_method='FRACTION', margin=uv_margin),
        dict(shared, margin=uv_margin),
        dict(rotate=rotate, scale=True),
    )
    for extra in attempts:
        try:
            bpy.ops.uv.pack_islands(**extra)
            return True
        except (TypeError, RuntimeError, ValueError):
            continue
    return False


def run_uv_normalize(obj_name, props):
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    layer_name = props.uv_layer_name
    if layer_name not in obj.data.uv_layers:
        return

    enter_edit_select_all(obj, layer_name)
    try:
        bpy.ops.uv.average_islands_scale()
    except Exception:
        pass
    finally:
        force_object_mode()
    apply_world_area_scale(obj_name, layer_name)


def verify_uv_pack(obj_names, props, report, label=""):
    """Check the packed atlas and report what is actually wrong.

    Packing can fail in ways that look like success: the margin line search is
    skipped when no island is scalable, which silently pushes UVs past 1.0, and
    a too-tight gutter produces lightmap bleed that no operator flags. Report
    both instead of baking a corrupt atlas.
    """
    layer_name = props.uv_layer_name
    rx, ry = get_resolution(props)
    resolution = min(rx, ry)
    target_gap = props.pack_margin_px / resolution if props.pack_enabled else 0.0
    gap, u_min, u_max, islands = island_bounds_and_gap(
        obj_names, layer_name, target_gap)
    if not islands:
        return False
    prefix = f"[{label}] " if label else ""
    problems = []

    if u_max > 1.0001 or u_min < -0.0001:
        problems.append(
            f"UVs reach {u_max:.3f}/{u_min:.3f}, outside the 0-1 tile "
            f"(~{(max(u_max - 1.0, -u_min) * resolution):.0f}px of bleed into "
            f"the neighbouring tile)")

    # The bake margin is global to the scene, so it has to fit the *worst*
    # group in the build, not whichever group verified last.
    measured = gap * resolution if gap is not None else 0.0
    known = props.uv_gutter_px >= 0.0
    props.uv_gutter_px = min(props.uv_gutter_px, measured) \
        if known else measured

    if props.pack_enabled and props.pack_margin_px > 0 and gap is not None:
        gap_px = gap * resolution
        # A strictly positive gap between island AABBs proves no two islands
        # intersect, so this also covers overlap detection. Blender's own
        # `bpy.ops.uv.select_overlap` cannot serve as the oracle: in 5.2 the UV
        # selection moved to bmesh and is not readable from Python, and
        # `tool_settings.use_uv_select_sync` does not carry the result back, so
        # it silently reports zero even for fully coincident islands.
        if gap_px < props.pack_margin_px * 0.9:
            # Handled: the bake inset is clamped to this value below. Still
            # worth surfacing, because the fix is a bigger atlas, not a
            # different setting.
            report(
                {'WARNING'},
                f"{prefix}requested a {props.pack_margin_px}px gutter but the "
                f"pack achieved {gap_px:.1f}px. The bake margin has been "
                f"reduced to fit. Raise the resolution or lower Pack Margin "
                f"if you need the wider gutter.")

    if problems:
        for problem in problems:
            report({'ERROR'}, f"{prefix}bad UV pack: {problem}")
        props.pack_verified = False
        return False

    report(
        {'INFO'},
        f"{prefix}UV pack verified: {islands} island(s), "
        f"{'gutter %.1fpx' % (gap * resolution) if gap is not None else 'single island'}, "
        f"range {u_min:.3f}-{u_max:.3f}")
    props.pack_verified = True
    return True


def pack_group_once(obj_names, layer_name, props, uv_margin, enter):
    """One pack pass over a group. Enters and leaves edit mode itself.

    Edit mode must not be held across a timer tick: the user can click another
    object between steps, and edit-mode selection is shared state that a
    `bpy.ops.uv.*` call then operates on.

    Returns True when more passes are still wanted.
    """
    if not enter(obj_names, layer_name):
        return False
    try:
        run_blender_pack(props, uv_margin)
        return True
    finally:
        force_object_mode()


def run_uv_pack_steps(obj_name, props):
    """Single-object pack, yielding before each pack iteration."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    layer_name = props.uv_layer_name
    if layer_name not in obj.data.uv_layers:
        return

    uv_margin = pixel_margin_to_uv(props.pack_margin_px, props)

    if props.pack_stack_identical:
        stack_similar_islands([obj_name], layer_name)

    for _i in range(props.pack_iterations):
        props.status_text = f"Packing {obj_name} — pass {_i + 1}/{props.pack_iterations}"
        force_ui_redraw()
        yield
        if not pack_group_once(
                [obj_name], layer_name, props, uv_margin, enter_edit_select_all):
            return


def pack_uv_islands_phased_steps(obj_name, props):
    if props.pack_world_scale:
        run_uv_normalize(obj_name, props)
    if props.pack_image_boost > 1.01:
        apply_image_boost(obj_name, props)
    yield from run_uv_pack_steps(obj_name, props)
    if props.pack_scale_islands:
        scale_uvs_to_bounds(
            [obj_name], props.uv_layer_name,
            margin=pixel_margin_to_uv(props.pack_margin_px, props))


def pack_uv_islands_phased(obj_name, props):
    for _ in pack_uv_islands_phased_steps(obj_name, props):
        pass


def remove_old_uvs(obj_names, keep_layer_name):
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj and obj.type == 'MESH':
            mesh = obj.data
            protected = {keep_layer_name}
            backup_name = mesh.get(
                IMAGE_UV_BACKUP_PROP,
                obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
            if backup_name in mesh.uv_layers:
                protected.add(backup_name)
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or not mat.use_nodes:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type == 'UVMAP' and node.uv_map:
                        protected.add(node.uv_map)
            to_remove = [uv.name for uv in mesh.uv_layers
                         if uv.name not in protected]
            for name in to_remove:
                mesh.uv_layers.remove(mesh.uv_layers[name])


def enter_edit_multi_select_all(obj_names, layer_name):
    """Enter edit mode on exactly `obj_names` with `layer_name` active."""
    force_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    first_obj = None
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        obj.select_set(True)
        if first_obj is None:
            first_obj = obj
    if not first_obj:
        return False
    bpy.context.view_layer.objects.active = first_obj
    for obj in bpy.context.selected_objects:
        if layer_name in obj.data.uv_layers:
            obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    return True


def unwrap_dispatch(props):
    """Run the configured UV unwrap. Caller must already be in edit mode."""
    if props.uv_mode == 'SMART':
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(props.smart_uv_angle),
            island_margin=props.smart_uv_island_margin,
        )
    elif props.uv_mode == 'CUBE':
        bpy.ops.uv.cube_project(cube_size=props.cube_size)
        try:
            bpy.ops.uv.average_islands_scale()
        except Exception:
            pass
    elif props.uv_mode == 'UNWRAP':
        bpy.ops.uv.unwrap(
            method='ANGLE_BASED', margin=props.smart_uv_island_margin)
    elif props.uv_mode == 'AUTO_SEAM':
        bpy.ops.uv.smart_project(
            angle_limit=math.radians(props.auto_seam_angle), island_margin=0.001)
        bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
        bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
    elif props.uv_mode == 'LIGHTMAP':
        try:
            bpy.ops.uv.lightmap_pack(
                PREF_CONTEXT='ALL_FACES',
                PREF_PACK_IN_ONE=True,
                PREF_NEW_UVLAYER=False,
                PREF_BOX_DIV=props.lightmap_quality,
                PREF_MARGIN_DIV=props.lightmap_margin,
            )
        except Exception:
            bpy.ops.uv.smart_project(
                angle_limit=math.radians(66.0), island_margin=0.03)


def subdivide_and_rewrap(obj_names, layer_name):
    """Split oversized islands, then re-unwrap any mesh that was split."""
    if not any(subdivide_large_uv_islands(name, layer_name) for name in obj_names):
        return
    if enter_edit_multi_select_all(obj_names, layer_name):
        try:
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
        finally:
            force_object_mode()


def ensure_uv_steps(obj_name, props):
    """Create/refresh one object's bake UVs, yielding around the pack work."""
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
        return

    mesh = obj.data
    layer_name = props.uv_layer_name

    if layer_name not in mesh.uv_layers:
        mesh.uv_layers.new(name=layer_name)

    if props.uv_mode == 'EXISTING':
        if props.pack_enabled:
            yield from pack_uv_islands_phased_steps(obj_name, props)
        return

    if (props.uv_mode == 'AUTO_SEAM'
            and getattr(props, 'renew_auto_seams_on_preview', False)):
        for edge in mesh.edges:
            edge.use_seam = False

    yield
    enter_edit_select_all(obj, layer_name)
    try:
        unwrap_dispatch(props)
    finally:
        force_object_mode()

    if props.uv_mode == 'AUTO_SEAM':
        subdivide_and_rewrap([obj_name], layer_name)

    if props.pack_enabled:
        yield from pack_uv_islands_phased_steps(obj_name, props)

    if props.remove_old_uvs:
        remove_old_uvs([obj_name], props.uv_layer_name)


def ensure_uv(obj_name, props):
    for _ in ensure_uv_steps(obj_name, props):
        pass


def setup_atlas_uvs_steps(obj_names, props, report=None, label=""):
    """Unwrap and pack a group, yielding around the pack iterations."""
    layer_name = props.uv_layer_name
    force_object_mode()

    valid_objs = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue
        mesh = obj.data
        if layer_name not in mesh.uv_layers:
            mesh.uv_layers.new(name=layer_name)
        mesh.uv_layers.active = mesh.uv_layers[layer_name]
        if (props.uv_mode == 'AUTO_SEAM'
                and getattr(props, 'renew_auto_seams_on_preview', False)):
            for edge in mesh.edges:
                edge.use_seam = False
        valid_objs.append(obj_name)

    if not valid_objs:
        return

    if props.uv_mode == 'EXISTING':
        if props.pack_enabled:
            yield from atlas_pack_phase_steps(valid_objs, props, report, label)
        return

    yield
    if enter_edit_multi_select_all(valid_objs, layer_name):
        try:
            unwrap_dispatch(props)
        finally:
            force_object_mode()

    if props.uv_mode == 'AUTO_SEAM':
        subdivide_and_rewrap(valid_objs, layer_name)

    if props.pack_enabled:
        yield from atlas_pack_phase_steps(valid_objs, props, report, label)

    if props.remove_old_uvs:
        remove_old_uvs(valid_objs, props.uv_layer_name)


def setup_atlas_uvs(obj_names, props):
    """Blocking form of `setup_atlas_uvs_steps`, for the standalone operators."""
    for _ in setup_atlas_uvs_steps(obj_names, props):
        pass


def atlas_pack_phase_steps(obj_names, props, report=None, label=""):
    """Pack UVs, yielding before each pack iteration.

    `bpy.ops.uv.pack_islands` has no invoke path in Blender, so a single call is
    atomic and cannot be interrupted. Measured on this machine: 1.9s for one
    cube, ~7s for a two-object tile, and a few milliseconds with
    `pack_shape_method='AABB'`. Yields here are what keep the longest block down
    to a single pass instead of the whole pipeline.

    ponytail: one pass is the floor, because `bpy.ops.uv.pack_islands` is
    atomic and has no invoke path. Measured at 512px on a two-object tile,
    ~7.5s per pass and ~36s once the FRACTION line search is engaged (which it
    must be, or the result overflows the tile). Quality was chosen over speed:
    pack_iterations=3, rotate_method='ANY' and shape_method='CONCAVE' all stay
    at their quality settings, and the status line shows the pass number so the
    cost is visible. For a faster turnaround build, drop pack_iterations to 1
    or set pack_shape_method to 'AABB' -- both are already in the sidebar.
    """
    layer_name = props.uv_layer_name
    yield
    if props.pack_world_scale:
        if enter_edit_multi_select_all(obj_names, layer_name):
            try:
                bpy.ops.uv.average_islands_scale()
            except Exception:
                pass
            finally:
                force_object_mode()
        for obj_name in obj_names:
            apply_world_area_scale(obj_name, layer_name)

    yield
    if props.pack_stack_identical:
        stack_similar_islands(obj_names, layer_name)

    uv_margin = pixel_margin_to_uv(props.pack_margin_px, props)
    iterations = max(1, props.pack_iterations)
    for index in range(iterations):
        props.status_text = (
            f"Packing {len(obj_names)} object(s) — pass {index + 1}/{iterations}")
        force_ui_redraw()
        yield
        needs_more = pack_group_once(
            obj_names, layer_name, props, uv_margin, enter_edit_multi_select_all)
        if not needs_more:
            break

    if props.pack_scale_islands:
        scale_uvs_to_bounds(obj_names, layer_name, margin=uv_margin)

    yield
    if report is not None:
        verify_uv_pack(obj_names, props, report, label)


def atlas_pack_phase(obj_names, props):
    """Blocking form of `atlas_pack_phase_steps`, for the standalone operators."""
    for _ in atlas_pack_phase_steps(obj_names, props):
        pass


def distribute_tiles(obj_names, tile_count):
    obj_areas = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        area = world_surface_area(obj) if obj.data.polygons else 0.0
        obj_areas.append((obj_name, area))

    obj_areas.sort(key=lambda x: x[1], reverse=True)
    tiles = [[] for _ in range(min(tile_count, len(obj_areas)))]
    tile_areas = [0.0] * len(tiles)

    for obj_name, area in obj_areas:
        min_idx = tile_areas.index(min(tile_areas))
        tiles[min_idx].append(obj_name)
        tile_areas[min_idx] += area

    return [t for t in tiles if t]


def remove_tile_uv_offsets(obj_names, layer_name):
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue
        if layer_name not in obj.data.uv_layers:
            continue

        enter_edit_select_all(obj, layer_name)
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer:
                loops = [loop for face in bm.faces for loop in face.loops]
                if loops:
                    min_x = min(loop[uv_layer].uv.x for loop in loops)
                    tile_offset = math.floor(min_x + 1e-6)
                    if tile_offset != 0:
                        for loop in loops:
                            loop[uv_layer].uv.x -= tile_offset
                        bmesh.update_edit_mesh(obj.data)
        finally:
            force_object_mode()


def inject_bake_node(mat_name, image, tag):
    tag = bake_node_tag(tag)
    mat = bpy.data.materials.get(mat_name)
    if not mat or not mat.use_nodes:
        return None

    nodes = mat.node_tree.nodes
    for n in list(nodes):
        if n.name.startswith(tag):
            nodes.remove(n)

    node = nodes.new('ShaderNodeTexImage')
    node.name  = tag
    node.label = "AHB Bake Target (do not connect)"
    node.image = image

    xs = [n.location.x for n in nodes if n is not node]
    node.location = ((min(xs) - 300 if xs else -400), 200)
    for existing in nodes:
        existing.select = False
    node.select = True
    nodes.active = node
    return node


def remove_bake_nodes(mat_names, tag):
    tag = bake_node_tag(tag)
    for mat_name in mat_names:
        mat = bpy.data.materials.get(mat_name)
        if mat and mat.use_nodes:
            for n in list(mat.node_tree.nodes):
                if n.name.startswith(tag):
                    mat.node_tree.nodes.remove(n)


def cycles_devices(cycles_prefs):
    refreshed = cycles_prefs.get_devices()
    devices = list(getattr(cycles_prefs, "devices", []))
    if devices:
        return devices
    if refreshed:
        for group in refreshed:
            if isinstance(group, (list, tuple)):
                devices.extend(group)
    return devices


def configure_cycles_device(scene, props):
    if props.compute_device == 'CPU':
        scene.cycles.device = 'CPU'
        props.device_status = "CPU"
        return

    cycles_addon = bpy.context.preferences.addons.get('cycles')
    cycles_prefs = cycles_addon.preferences if cycles_addon else None
    if cycles_prefs is None:
        props.device_status = "GPU unavailable (Cycles preferences not found)"
        raise RuntimeError("Cycles preferences were not found; cannot enable GPU baking")

    requested = props.gpu_backend
    configured = getattr(cycles_prefs, "compute_device_type", 'NONE')
    # Cycles only enumerates devices for the backend currently selected, so
    # probing means writing global user preferences. Snapshot them so a failed
    # probe does not leave the user's Cycles prefs reconfigured.
    # ponytail: a successful bake leaves the GPU backend selected; restoring it
    # afterwards needs the bake lifetime threaded through run_bake's finally.
    original_backend = configured
    original_device_use = None

    def restore_prefs():
        try:
            cycles_prefs.compute_device_type = original_backend
            if original_device_use is not None:
                for device, was_used in original_device_use.items():
                    device.use = was_used
        except (TypeError, ValueError, RuntimeError, AttributeError):
            pass

    candidates = []
    if requested == 'AUTO':
        if configured and configured != 'NONE':
            candidates.append(configured)
        for backend in ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'):
            if backend not in candidates:
                candidates.append(backend)
    else:
        candidates.append(requested)

    attempted = []
    for backend in candidates:
        attempted.append(backend)
        try:
            cycles_prefs.compute_device_type = backend
            devices = cycles_devices(cycles_prefs)
        except (TypeError, ValueError, RuntimeError):
            continue

        gpu_devices = [
            device for device in devices
            if getattr(device, "type", 'CPU') != 'CPU'
        ]
        if not gpu_devices:
            continue

        if original_device_use is None:
            original_device_use = {device: device.use for device in devices}
        for device in devices:
            device.use = device in gpu_devices

        scene.cycles.device = 'GPU'
        names = ", ".join(device.name for device in gpu_devices)
        props.device_status = f"GPU / {backend}: {names}"
        return

    restore_prefs()
    props.device_status = "GPU unavailable"
    tried = ", ".join(attempted)
    raise RuntimeError(
        "No compatible Cycles GPU was found "
        f"(tried {tried}). For an RTX 4060, update NVIDIA drivers."
    )


def configure_bake_settings(props, context):
    scene = context.scene
    if props.auto_switch_cycles:
        scene.render.engine = 'CYCLES'

    if scene.render.engine != 'CYCLES':
        raise RuntimeError("Texture baking requires the Cycles render engine")

    scene.cycles.samples = props.samples
    configure_cycles_device(scene, props)

    try:
        scene.cycles.bake_type = props.bake_type
    except (AttributeError, TypeError, ValueError):
        pass

    bake = scene.render.bake
    bake.use_clear = props.clear_bake
    bake.target = getattr(props, 'bake_target', 'IMAGE_TEXTURES')

    use_multires = getattr(props, 'multires_bake', False)
    if use_multires:
        if not uses_image_bake_target(props):
            raise RuntimeError("Multires baking requires Image Textures output")
        if props.use_selected_to_active:
            raise RuntimeError("Multires baking cannot use Selected to Active")
        if props.bake_type != 'NORMAL':
            raise RuntimeError(
                "This add-on supports Multires only for Normal bakes")
    bake.use_multires = use_multires
    if use_multires:
        bake.type = 'NORMALS'

    if props.pack_enabled:
        # Clamp against the gutter the pack *achieved*, not the one that was
        # asked for. The packer cannot always fit the requested margin - two
        # large islands in a small atlas get what they get - and a bake inset
        # of N on each side of a gap narrower than 2N bleeds into the
        # neighbour.
        available = int(props.uv_gutter_px // 2) if props.uv_gutter_px >= 0 \
            else max(0, props.pack_margin_px // 2)
        bake.margin = min(props.margin, max(0, available))
    else:
        bake.margin = props.margin

    try:
        bake.margin_type = props.margin_type
    except Exception:
        pass

    bake.use_pass_direct   = props.use_pass_direct
    bake.use_pass_indirect = props.use_pass_indirect
    bake.use_pass_color    = props.use_pass_color

    if props.bake_type == 'NORMAL':
        bake.normal_space = getattr(props, 'normal_space', 'TANGENT')
        bake.normal_r = getattr(props, 'normal_r', 'POS_X')
        bake.normal_g = getattr(props, 'normal_g', 'POS_Y')
        bake.normal_b = getattr(props, 'normal_b', 'POS_Z')

    bake.use_selected_to_active = (
        props.use_selected_to_active and not use_multires)
    bake.use_cage = False
    try:
        bake.cage_object = ""
    except (AttributeError, TypeError):
        pass
    if bake.use_selected_to_active:
        bake.cage_extrusion   = props.cage_extrusion
        bake.max_ray_distance = props.max_ray_distance
        if getattr(props, 'use_cage', False):
            cage_object = getattr(props, 'cage_object', None)
            if not cage_object or cage_object.type != 'MESH':
                raise RuntimeError("Choose a valid mesh object as the custom cage")
            bake.use_cage = True
            bake.cage_object = cage_object.name


def save_image(image, props, context):
    raw_output_dir = str(getattr(props, 'output_dir', '')).strip() or '//baked/'
    if raw_output_dir.startswith('//') and not bpy.data.filepath:
        raise RuntimeError(
            "The blend file must be saved before using a blend-relative bake output path"
        )

    out_dir = bpy.path.abspath(raw_output_dir)
    os.makedirs(out_dir, exist_ok=True)

    ext  = EXT_MAP.get(props.image_format, '.exr')
    path = os.path.join(out_dir, safe_filename(image.name) + ext)

    scene = context.scene
    img_settings = scene.render.image_settings
    save_format = ('OPEN_EXR_MULTILAYER'
                   if props.image_format == 'OPEN_EXR_MULTILAYER'
                   else props.image_format)
    original_settings = {}
    for setting_name in (
            'media_type', 'file_format', 'color_mode', 'color_depth',
            'exr_codec', 'quality'):
        try:
            original_settings[setting_name] = getattr(img_settings, setting_name)
        except (AttributeError, TypeError):
            continue
    original_path = image.filepath_raw
    try:
        # Blender 5.0+ requires media_type before file_format is assignable.
        if 'media_type' in original_settings:
            img_settings.media_type = 'IMAGE'
        img_settings.file_format = save_format
        img_settings.color_mode = ('RGB' if save_format in ('JPEG', 'HDR')
                                   else props.color_mode)

        if save_format.startswith('OPEN_EXR'):
            img_settings.color_depth = '32' if props.use_hdr_float else '16'
            img_settings.exr_codec = props.exr_codec
        elif save_format == 'JPEG':
            try:
                img_settings.quality = 100
            except (AttributeError, TypeError):
                pass

        try:
            image.filepath_raw = path
            image.file_format = save_format
            image.save()
        except TypeError:
            image.save(filepath=path)
    finally:
        for setting_name, value in original_settings.items():
            try:
                setattr(img_settings, setting_name, value)
            except (AttributeError, TypeError):
                pass
        try:
            image.filepath_raw = original_path
        except (AttributeError, TypeError):
            pass

    return path


def do_bake_batch(obj_names, props, context, source_obj_names=None):
    objs_to_bake = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj and obj.type == 'MESH':
            objs_to_bake.append(obj)

    if not objs_to_bake:
        return False

    problems = validate_bake_ready([obj.name for obj in objs_to_bake], props,
                                   lambda _level, _message: None)
    if problems:
        # `validate_bake_ready` builds a per-object list; throwing it away left
        # the user with "missing UV layer" and no idea which object or why.
        raise RuntimeError(f"Bake target is not ready - {problems}")

    source_objs = []
    if props.use_selected_to_active:
        if len(objs_to_bake) != 1:
            raise RuntimeError("Selected to Active requires exactly one bake target")
        for obj_name in source_obj_names or []:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type == 'MESH' and obj not in objs_to_bake:
                source_objs.append(obj)
        if not source_objs:
            raise RuntimeError("Selected to Active requires at least one selected source mesh")

    for o in context.selected_objects:
        o.select_set(False)

    for obj in objs_to_bake:
        obj.select_set(True)
        if obj.mode != 'OBJECT':
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='OBJECT')

        if bake_type_needs_uv(props):
            activate_bake_uv(obj.name, props)

        if uses_image_bake_target(props):
            for slot in obj.material_slots:
                mat = slot.material
                if mat and mat.use_nodes:
                    tag = bake_node_tag(props.image_node_name)
                    for node in mat.node_tree.nodes:
                        if node.name.startswith(tag):
                            for existing in mat.node_tree.nodes:
                                existing.select = False
                            node.select = True
                            mat.node_tree.nodes.active = node
                            break
        else:
            ensure_bake_color_attribute(obj)

    active_obj = objs_to_bake[0]
    context.view_layer.objects.active = active_obj
    for obj in source_objs:
        obj.select_set(True)

    selected_for_bake = source_objs + objs_to_bake

    def execute_bake(invoke=False):
        # INVOKE_DEFAULT routes through bake_invoke, which starts a WM job:
        # real progress bar, live redraw, and ESC sets G.is_break so the user
        # can abort. The default EXEC path is bake_exec, which blocks the whole
        # UI with no progress and (by design in the C source) never checks ESC.
        how = 'INVOKE_DEFAULT' if invoke else 'EXEC_DEFAULT'
        if getattr(props, 'multires_bake', False):
            return bpy.ops.object.bake_image(how)
        return bpy.ops.object.bake(how, type=props.bake_type)

    if bpy.app.background:
        try:
            with bpy.context.temp_override(
                active_object=active_obj,
                selected_objects=selected_for_bake,
                selected_editable_objects=selected_for_bake,
            ):
                result = execute_bake()
        except RuntimeError:
            # The retry is the authoritative attempt. If it fails too, its
            # error is the real one; re-raising the first hides the cause.
            result = execute_bake()

        if not result or 'FINISHED' not in result:
            raise RuntimeError("Blender cancelled the bake operation")
        return True

    # Interactive: hand the bake to the job system and let the caller poll.
    # Deliberately NOT wrapped in temp_override -- that runs the block in
    # EXEC context, which downgrades INVOKE_DEFAULT back to the blocking
    # bake_exec. The selection and active object set above are enough.
    result = execute_bake(invoke=True)

    if not result:
        raise RuntimeError("Blender refused to start the bake")
    if 'CANCELLED' in result:
        # bake_invoke returns CANCELLED when a bake job is already running.
        raise RuntimeError("Another bake is already running; try again")
    if 'FINISHED' in result:
        return True
    return 'RUNNING_MODAL'


def validate_bake_ready(obj_names, props, report):
    """Returns None when every mesh is bake-ready, else the failure reason.

    Callers must test `is not None`: the reason is a non-empty string, so a
    truthiness check reads success backwards.
    """
    failures = []
    tag = bake_node_tag(props.image_node_name)
    image_target = uses_image_bake_target(props)
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            failures.append(f"{obj_name}: no bakeable mesh faces")
            continue
        if (bake_type_needs_uv(props)
                and props.uv_layer_name not in obj.data.uv_layers):
            failures.append(f"{obj_name}: missing UV layer '{props.uv_layer_name}'")
            continue
        if not image_target and not get_bake_color_attribute(obj):
            failures.append(f"{obj_name}: missing active bake color attribute")
            continue
        if getattr(props, 'multires_bake', False) and not any(
                modifier.type == 'MULTIRES' for modifier in obj.modifiers):
            failures.append(f"{obj_name}: no Multires modifier")
            continue
        for slot_index in sorted({p.material_index for p in obj.data.polygons}):
            if slot_index >= len(obj.material_slots):
                failures.append(f"{obj_name}: invalid material slot {slot_index + 1}")
                continue
            mat = obj.material_slots[slot_index].material
            if not mat or not mat.use_nodes:
                failures.append(f"{obj_name}: empty material slot {slot_index + 1}")
                continue
            if image_target:
                node = next((n for n in mat.node_tree.nodes
                             if n.name.startswith(tag) and n.type == 'TEX_IMAGE'
                             and n.image), None)
                if not node:
                    failures.append(f"{obj_name}: no bake target in '{mat.name}'")

    if failures:
        preview = "; ".join(failures[:4])
        if len(failures) > 4:
            preview += f"; +{len(failures) - 4} more"
        report({'ERROR'}, f"Bake setup incomplete: {preview}")
        return preview
    return None


def get_object_bake_image(obj, props, material=None):
    if not obj:
        return None

    remembered = obj.get("ahb_baked_texture")
    if remembered:
        image = bpy.data.images.get(str(remembered))
        if image:
            return image

    if props.image_mode == 'ATLAS':
        return bpy.data.images.get(f"{props.output_prefix}shared")

    if props.image_mode == 'PER_MATERIAL' and material:
        return bpy.data.images.get(f"{props.output_prefix}{material.name}")

    if props.image_mode == 'COLLECTION_ATLASES':
        groups = get_atlas_groups([obj.name], props)
        for atlas_number, names in groups:
            if obj.name in names:
                atlas_name = get_collection_atlas_name(props, atlas_number)
                return bpy.data.images.get(f"{props.output_prefix}{atlas_name}")

    tag = bake_node_tag(props.image_node_name)
    if obj.material_slots:
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            for node in mat.node_tree.nodes:
                if node.name.startswith(tag) and node.type == 'TEX_IMAGE' and node.image:
                    return node.image
                if (node.name == 'AHB_Applied_Texture'
                        and node.type == 'TEX_IMAGE' and node.image):
                    return node.image
    return None


def apply_single_object_bake(obj, props, mode, report):
    applied = 0
    processed = set()
    color_attribute = (get_bake_color_attribute(obj)
                       if not uses_image_bake_target(props) else None)
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or mat.name in processed:
            continue
        processed.add(mat.name)
        if color_attribute:
            ensure_material_backup(mat)
            if apply_baked_color_to_material(
                    mat.name, color_attribute.name, mode,
                    props.apply_keep_original_nodes):
                applied += 1
            continue
        image = get_object_bake_image(
            obj, props, mat if props.image_mode == 'PER_MATERIAL' else None)
        if not image:
            report({'WARNING'}, f"No existing bake image found for '{mat.name}'.")
            continue
        ensure_material_backup(mat)
        if apply_baked_to_material(
                mat.name, image, mode, props.apply_keep_original_nodes,
                props.uv_layer_name):
            applied += 1
    return applied


def scope_has_material_backups(obj_names):
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            if slot.material and bpy.data.materials.get(
                    f"{slot.material.name}_AHB_backup"):
                return True
    return False


def remove_all_uvs(obj_names):
    removed_count = 0
    obj_count = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if not mesh.uv_layers:
            continue

        if mesh.users > 1:
            mesh = mesh.copy()
            obj.data = mesh

        uv_names = [uv.name for uv in mesh.uv_layers]
        for name in uv_names:
            uv_layer = mesh.uv_layers.get(name)
            if uv_layer:
                mesh.uv_layers.remove(uv_layer)
                removed_count += 1
        obj_count += 1

    return removed_count, obj_count


def clear_and_renew_auto_seams(obj_names, props, report=None):
    seam_rad = math.radians(props.auto_seam_angle)
    count = 0
    force_object_mode()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue

        # Seams must be cleared *before* smart_project, otherwise interior
        # seams from a previous run survive. Snapshot them anyway: the three
        # operators below are fallible, and handing back a mesh with every seam
        # stripped but stale UVs is worse than not touching it at all.
        previous_seams = [(edge, edge.use_seam) for edge in obj.data.edges]
        for edge, _seam in previous_seams:
            edge.use_seam = False

        enter_edit_select_all(obj, props.uv_layer_name)
        try:
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            count += 1
        except Exception as exc:
            force_object_mode()
            for edge, seam in previous_seams:
                edge.use_seam = seam
            if report:
                report({'WARNING'}, f"{obj_name}: seam renewal failed ({exc})")
        finally:
            force_object_mode()
    return count


def rename_objects_by_texture(obj_names, props, report):
    if not uses_image_bake_target(props):
        report(
            {'WARNING'},
            "Object renaming by texture is unavailable for color attribute bakes.")
        return 0

    image_mode = props.image_mode
    renamed = 0

    if image_mode == 'PER_MATERIAL':
        report({'WARNING'}, "Object renaming is ambiguous in Per Material mode and was skipped.")
        return 0

    predicted_suffixes = {}
    if image_mode == 'AUTO_TILES':
        tiles = distribute_tiles(obj_names, props.tile_count)
        for tile_idx, tile_objs in enumerate(tiles):
            suffix = f"_{props.output_prefix}tile_{tile_idx + 1:02d}"
            for o in tile_objs:
                predicted_suffixes[o] = suffix
    elif image_mode == 'ATLAS':
        for o in obj_names:
            predicted_suffixes[o] = f"_{props.output_prefix}shared"
    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_objs in get_atlas_groups(obj_names, props):
            suffix = f"_{props.output_prefix}{get_collection_atlas_name(props, atlas_number)}"
            for o in atlas_objs:
                predicted_suffixes[o] = suffix

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue

        actual_texture = obj.get("ahb_baked_texture")
        if not actual_texture:
            for slot in obj.material_slots:
                if slot.material and slot.material.node_tree:
                    node = slot.material.node_tree.nodes.get(props.image_node_name)
                    if node and node.type == 'TEX_IMAGE' and node.image:
                        actual_texture = node.image.name
                        break

        if actual_texture:
            suffix = f"_{actual_texture}"
        else:
            suffix = predicted_suffixes.get(obj.name)

        if not suffix:
            continue

        # Rename from the name the object had before this addon ever touched
        # it, rather than trying to recognise our own suffixes. The tile an
        # object lands in depends on world area and tile count, so both can
        # change between runs and a suffix list goes stale -- that stacked
        # `Chair_bake_tile_03_bake_tile_01`. The original is recorded on first
        # rename, so later runs are exact and a user object whose real name
        # contains the prefix is never truncated.
        base = obj.get(RENAME_BASE_PROP)
        if not base:
            base = obj.name
            obj[RENAME_BASE_PROP] = base
        target = f"{base}{suffix}"
        if target == obj.name:
            continue
        obj.name = target
        renamed += 1

    return renamed


def group_objects_by_tile(obj_names, props, report):
    if props.image_mode != 'AUTO_TILES':
        report({'WARNING'}, "Group to Collections only works in Auto Tiles mode.")
        return 0

    predicted_tiles = distribute_tiles(obj_names, props.tile_count)
    predicted_col_names = {}
    for tile_idx, tile_objs in enumerate(predicted_tiles):
        col_name = f"{props.output_prefix}Tile_{tile_idx + 1:02d}"
        for o in tile_objs:
            predicted_col_names[o] = col_name

    moved = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj: continue

        col_name = None
        actual_texture = obj.get("ahb_baked_texture")
        if actual_texture:
            match = re.search(r"Tile_(\d+)", str(actual_texture), re.IGNORECASE)
            if match:
                idx = int(match.group(1))
                col_name = f"{props.output_prefix}Tile_{idx:02d}"

        if not col_name:
            col_name = predicted_col_names.get(obj.name)

        if not col_name: continue

        if not actual_texture:
            obj["ahb_texture_pack"] = col_name

        col = bpy.data.collections.get(col_name)
        if not col:
            col = bpy.data.collections.new(col_name)
            bpy.context.scene.collection.children.link(col)

        if obj.name not in col.objects:
            col.objects.link(obj)
        for old_col in list(obj.users_collection):
            if old_col != col:
                old_col.objects.unlink(obj)
        moved += 1

    return moved


def run_setup_steps(obj_names, props, report):
    """Prepare meshes, UVs and materials, yielding around the slow UV work.

    Returns True/False via StopIteration.value so callers keep the original
    contract. Only the UV work yields; everything else is milliseconds.
    """
    normalize_core_names(props)
    props.uv_gutter_px = -1.0   # re-measured per group below
    image_target = uses_image_bake_target(props)
    prepare_mesh_data(
        obj_names, make_unique=(props.auto_create_uv or not image_target))
    created_materials = prepare_material_slots(obj_names)
    if created_materials:
        report({'INFO'}, f"Created {created_materials} missing bake material(s).")

    if props.unlink_materials:
        props.status_text = "Unlinking shared materials..."
        force_ui_redraw()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                if slot.material and slot.material.users > 1:
                    slot.material = slot.material.copy()

    preserve_implicit_texture_uvs(obj_names, props)

    if not image_target:
        mat_map = collect_material_names(obj_names)
        if not mat_map:
            report({'WARNING'}, "No materials found on objects.")
            return False

        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH':
                continue
            if (bake_type_needs_uv(props) and props.auto_create_uv
                    and props.uv_layer_name not in obj.data.uv_layers):
                yield from ensure_uv_steps(obj_name, props)
            if not ensure_bake_color_attribute(obj):
                report({'ERROR'}, f"Cannot create a bake color attribute on {obj_name}")
                return False

        if validate_bake_ready(obj_names, props, report) is not None:
            return False
        msg = (f"Setup done: color attributes ready on "
               f"{len(obj_names)} object(s)")
        props.status_text = msg
        report({'INFO'}, msg)
        return True

    image_mode = props.image_mode
    props.status_text = f"Setting up {image_mode} mode…"

    collection_groups = None
    if image_mode == 'COLLECTION_ATLASES':
        collection_groups = get_atlas_groups(obj_names, props, report)
        if not flatten_atlas_groups(collection_groups):
            report({'ERROR'}, "No scoped objects belong to an atlas collection.")
            return False
        isolate_materials_between_atlas_groups(collection_groups)

    mat_map = collect_material_names(obj_names)
    if not mat_map:
        report({'WARNING'}, "No materials found on objects.")
        return False

    if image_mode == 'ATLAS':
        remove_tile_uv_offsets(obj_names, props.uv_layer_name)
        if props.auto_create_uv:
            try:
                yield from setup_atlas_uvs_steps(obj_names, props, report, "Atlas")
            except Exception as e:
                report({'ERROR'}, f"Atlas UV setup failed: {e}")
                force_object_mode()
                return False

        shared_img = get_or_create_image(props, "shared")
        for mat_name in mat_map:
            inject_bake_node(mat_name, shared_img, props.image_node_name)

    elif image_mode == 'AUTO_TILES':
        remove_tile_uv_offsets(obj_names, props.uv_layer_name)
        tiles = distribute_tiles(obj_names, props.tile_count)
        for tile_idx, tile_obj_names in enumerate(tiles):
            props.status_text = f"Setting up tile {tile_idx + 1}/{len(tiles)}…"
            if props.auto_create_uv:
                try:
                    yield from setup_atlas_uvs_steps(
                        tile_obj_names, props, report,
                        f"Tile {tile_idx + 1}")
                except Exception as e:
                    report({'ERROR'}, f"Tile {tile_idx + 1} UV setup failed: {e}")
                    force_object_mode()
                    return False

            tile_img = get_tile_image(props, tile_idx)
            for obj_name in tile_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                for slot in obj.material_slots:
                    if slot.material and slot.material.node_tree:
                        inject_bake_node(
                            slot.material.name, tile_img, props.image_node_name)

    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_obj_names in collection_groups:
            if not atlas_obj_names:
                continue
            remove_tile_uv_offsets(atlas_obj_names, props.uv_layer_name)
            if props.auto_create_uv:
                try:
                    yield from setup_atlas_uvs_steps(
                        atlas_obj_names, props, report,
                        f"Atlas {atlas_number}")
                except Exception as e:
                    report({'ERROR'}, f"Atlas {atlas_number} UV setup failed: {e}")
                    force_object_mode()
                    return False

            atlas_img = get_collection_atlas_image(props, atlas_number)
            for mat_name in collect_material_names(atlas_obj_names):
                inject_bake_node(mat_name, atlas_img, props.image_node_name)

    elif image_mode == 'PER_MATERIAL':
        for obj_name in obj_names:
            props.status_text = f"Setting up UVs on {obj_name}…"
            if props.auto_create_uv:
                try:
                    yield from ensure_uv_steps(obj_name, props)
                except Exception as e:
                    report({'ERROR'}, f"UV setup failed [{obj_name}]: {e}")
                    force_object_mode()
                    return False

        for mat_name in mat_map:
            inject_bake_node(
                mat_name, get_or_create_image(props, mat_name),
                props.image_node_name)

    ready_names = (flatten_atlas_groups(collection_groups)
                   if collection_groups is not None else obj_names)
    if validate_bake_ready(ready_names, props, report) is not None:
        return False

    msg = f"Setup done: {len(mat_map)} material(s) across {len(obj_names)} object(s)"
    props.status_text = msg
    report({'INFO'}, msg)
    return True


def run_setup(obj_names, props, report, on_done=None):
    """Drive the setup steps.

    `on_done` is the only completion path when supplied, including under
    `--background`, where it runs before this returns. Omit it to get the
    boolean back directly.
    """
    steps = run_setup_steps(obj_names, props, report)
    if bpy.app.background or on_done is None:
        props.web3d_cancel_pending = False
        while True:
            try:
                next(steps)
            except StopIteration as finished:
                if on_done is not None:
                    on_done(finished.value)
                    return None
                return finished.value

    def tick():
        if props.web3d_cancel:
            props.web3d_cancel = False
            props.web3d_cancel_pending = False
            props.bake_cancelled = True
            force_object_mode()
            report({'WARNING'}, "Setup cancelled by user.")
            on_done(False)
            return None
        try:
            next(steps)
        except StopIteration as finished:
            props.web3d_cancel_pending = False
            on_done(finished.value)
            return None
        return 0.0

    props.web3d_cancel = False
    props.web3d_cancel_pending = True
    bpy.app.timers.register(tick, first_interval=0.0)
    return None


def plan_bake_batches(obj_names, props, report, atlas_groups=None):
    """Resolve the image mode into a flat list of (label, image, object_names).

    Every image mode is the same shape -- tag each object with its target
    image, inject the bake node, then bake the whole group at once -- so the
    mode only decides how the groups are formed.
    """
    mode = props.image_mode
    if not uses_image_bake_target(props):
        return [(f"{len(obj_names)} object(s)", None, list(obj_names))]

    def tag(names, image):
        """Record the target image on each object and inject its bake node."""
        valid = []
        for obj_name in names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            obj["ahb_baked_texture"] = image.name
            if any(slot.material for slot in obj.material_slots):
                valid.append(obj_name)
        return valid

    if mode == 'AUTO_TILES':
        return [
            (f"Tile {idx + 1}", get_tile_image(props, idx), tile_names)
            for idx, tile_names in enumerate(distribute_tiles(obj_names, props.tile_count))
        ]

    if mode == 'COLLECTION_ATLASES':
        groups = atlas_groups or get_atlas_groups(obj_names, props, report)
        batches = []
        for atlas_number, atlas_names in groups:
            if not atlas_names:
                continue
            name = get_collection_atlas_name(props, atlas_number)
            batches.append((name, get_collection_atlas_image(props, atlas_number),
                            atlas_names))
        return batches

    if mode == 'ATLAS':
        return [("Single Atlas", get_or_create_image(props, "shared"),
                 list(obj_names))]

    # PER_MATERIAL: one image per material, baked in a single pass.
    batches = []
    valid_objs = set()
    for mat_name, mat_obj_names in collect_material_names(obj_names).items():
        mat_img = get_or_create_image(props, mat_name)
        inject_bake_node(mat_name, mat_img, props.image_node_name)
        valid_objs.update(mat_obj_names)
        batches.append((mat_name, mat_img, mat_obj_names))

    # Only single-image objects get a recorded assignment; multi-image objects
    # would resolve to the wrong texture if they had one.
    per_object = {}
    for _label, mat_img, names in batches:
        for obj_name in names:
            per_object.setdefault(obj_name, set()).add(mat_img.name)
    for obj_name, image_names in per_object.items():
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        if len(image_names) == 1:
            obj["ahb_baked_texture"] = next(iter(image_names))
        elif "ahb_baked_texture" in obj:
            del obj["ahb_baked_texture"]

    ordered = sorted(valid_objs)
    if ordered:
        batches = [(f"{len(batches)} material image(s)", None, ordered)]
    return batches


def _inject_batch_nodes(image, obj_names, props):
    """Inject the bake node into every material that owns a mesh in the batch."""
    names = set()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            if not mat.node_tree:
                continue
            names.add(mat.name)
            if image is not None:
                inject_bake_node(mat.name, image, props.image_node_name)
    return names


def _install_bake_cancel_flag(flag):
    """Return a handler pair that records ESC-cancel of the running bake.

    Blender's own bake_modal handles ESC for a single bake, but it sets
    G.is_break and knows nothing about us -- without this the runner would
    happily start the next batch after the user aborted.
    """
    def on_cancel(_object):
        flag['cancelled'] = True

    def on_complete(_object):
        flag['completed'] = True

    bpy.app.handlers.object_bake_cancel.append(on_cancel)
    bpy.app.handlers.object_bake_complete.append(on_complete)

    def remove():
        for handlers, func in ((bpy.app.handlers.object_bake_cancel, on_cancel),
                               (bpy.app.handlers.object_bake_complete, on_complete)):
            if func in handlers:
                handlers.remove(func)

    return remove


class _BakeRunner:
    """Runs bake batches one at a time through Blender's job system.

    Each `do_bake_batch` starts a WM job; the next batch only starts once the
    previous one has finished, so the interface stays live between batches and
    Blender's own progress bar and ESC handling stay active during them.

    The UV stages cannot use a job -- `bpy.ops.uv.*` is EXEC-only -- so they
    are driven as generators by `run_setup` instead and share this runner's
    cancel flag and interval.
    """

    def __init__(self, batches, props, context, report, source_obj_names):
        self.batches = list(batches)
        self.props = props
        self.context = context
        self.report = report
        self.source_obj_names = source_obj_names
        self.index = 0
        self.baked_names = set()
        self.errors = 0
        self.cancelled = False
        self._label = ""
        self._pending = ()
        self._flag = {'cancelled': False}
        self._remove_handlers = None

    def _step(self):
        """Advance the run. Returns False once there is nothing left to do."""
        if self._remove_handlers is not None:
            if bpy.context.window_manager.is_interface_locked:
                return True
            if bpy.app.is_job_running('OBJECT_BAKE'):
                return True
            self._remove_handlers()
            self._remove_handlers = None
            if self._flag['cancelled']:
                self.cancelled = True
                self.report({'WARNING'}, f"Bake cancelled during {self._label}.")
                return False
            self.baked_names.update(self._pending)
            self.report(
                {'INFO'},
                f"[{self._label}] Baked {len(self._pending)} object(s)")

        while self.index < len(self.batches):
            label, _image, names = self.batches[self.index]
            self.index += 1
            if not names:
                continue
            self.props.status_text = (
                f"Baking {label} ({len(names)} objects, "
                f"{self.index}/{len(self.batches)} batches)")
            force_ui_redraw()
            try:
                result = do_bake_batch(
                    names, self.props, self.context, self.source_obj_names)
            except Exception as e:
                self.errors += 1
                self.report({'ERROR'}, f"Bake FAILED [{label}]: {e}")
                continue
            if result is True:
                self.baked_names.update(names)
                self.report({'INFO'}, f"[{label}] Baked {len(names)} object(s)")
                continue
            # A job is now running; hand control back until it ends.
            self._label, self._pending = label, names
            self._flag = {'cancelled': False}
            self._remove_handlers = _install_bake_cancel_flag(self._flag)
            return True
        return False

    def run(self):
        """Drive every batch to completion without yielding.

        Only valid under `--background`, where `do_bake_batch` takes the
        blocking EXEC path. Interactively a batch returns 'RUNNING_MODAL' and
        nothing would ever service the job, so this spins forever -- fail loudly
        instead.
        """
        if not bpy.app.background:
            raise RuntimeError(
                "_BakeRunner.run() cannot drive an interactive bake; the "
                "batches run as WM jobs and need start_async().")
        while self._step():
            pass

    def start_async(self, on_finish):
        """Return a timer callback that finishes the run and reports back."""
        self.props.web3d_cancel = False
        self.props.web3d_cancel_pending = True

        def tick():
            if self.props.web3d_cancel:
                self.cancelled = True
                self.props.web3d_cancel = False
                self.props.web3d_cancel_pending = False
                self.report({'WARNING'}, "Bake cancelled by user.")
                on_finish(self)
                return None
            if self._step():
                return 0.1
            self.props.web3d_cancel_pending = False
            on_finish(self)
            return None

        return tick


def run_bake(obj_names, props, context, report, source_obj_names=None,
             on_done=None):
    """Bake `obj_names` and report `(baked_count, error_count)`.

    Interactive runs drive the bake through Blender's job system so the UI
    stays live and ESC aborts. When `on_done` is supplied it is the only
    completion path -- including under `--background`, where it is invoked
    before this function returns. Omit it to get `(baked, errors)` back
    directly, which blocks until the bake finishes.
    """
    normalize_core_names(props)
    if (props.bake_type in PASS_FILTER_TYPES
            and not (props.use_pass_direct or props.use_pass_indirect
                     or props.use_pass_color)):
        report({'ERROR'}, "Enable at least one Direct, Indirect, or Color pass.")
        return (0, 1) if on_done is None else None

    try:
        configure_bake_settings(props, context)
    except Exception as e:
        report({'ERROR'}, f"Cannot configure bake: {e}")
        return (0, 1) if on_done is None else None

    all_mat_names = set()
    total = len(obj_names)

    if not uses_image_bake_target(props):
        batches = [(f"{len(obj_names)} object(s)", None, list(obj_names))]
    else:
        atlas_groups = None
        if props.image_mode == 'COLLECTION_ATLASES':
            atlas_groups = get_atlas_groups(obj_names, props, report)
            isolate_materials_between_atlas_groups(atlas_groups)
            total = len(flatten_atlas_groups(atlas_groups))
        batches = plan_bake_batches(obj_names, props, report, atlas_groups)
        for _label, image, names in batches:
            all_mat_names |= _inject_batch_nodes(image, names, props)
        context.scene.render.bake.use_clear = props.clear_bake

    runner = _BakeRunner(batches, props, context, report, source_obj_names)

    def finish(finished):
        baked = len(finished.baked_names)
        errors = finished.errors
        props.bake_cancelled = finished.cancelled

        if baked and not uses_image_bake_target(props):
            for obj_name in finished.baked_names:
                obj = bpy.data.objects.get(obj_name)
                attribute = get_bake_color_attribute(obj)
                if obj and attribute:
                    obj[BAKE_COLOR_ATTRIBUTE_PROP] = attribute.name

        # Save failures are counted separately. Folding them into `errors` made
        # a fully successful bake report "Bake incomplete" and abort the build
        # over a filesystem problem, hiding the fact that the textures are fine.
        save_errors = 0
        if (uses_image_bake_target(props) and props.auto_save
                and getattr(props, 'save_mode', 'EXTERNAL') == 'EXTERNAL'):
            props.status_text = "Saving images\u2026"
            force_ui_redraw()
            for img in list(owned_bake_images(props)):
                if not img.has_data:
                    continue
                try:
                    save_image(img, props, context)
                except Exception as e:
                    save_errors += 1
                    report({'WARNING'}, f"Save failed [{img.name}]: {e}")

        if props.auto_cleanup_nodes and baked and all_mat_names:
            remove_bake_nodes(all_mat_names, props.image_node_name)

        report(
            {'INFO'},
            f"Baked {baked}/{total} object(s). Errors: {errors}"
            + (f", unsaved images: {save_errors}" if save_errors else ""))
        if save_errors:
            props.status_text = (
                f"Baked {baked}/{total} object(s), but {save_errors} image(s) "
                f"could not be written to disk.")
        if on_done is not None:
            on_done(baked, errors)
        return baked, errors

    if bpy.app.background:
        runner.run()
        if on_done is None:
            return finish(runner)
        finish(runner)
        return None

    if on_done is None:
        # Interactively the bake is a WM job with nothing to block on, so there
        # is no way to hand the result back synchronously.
        report({'WARNING'},
               "run_bake() was called without on_done in an interactive "
               "session; the result cannot be returned synchronously.")
    bpy.app.timers.register(
        runner.start_async(finish), first_interval=0.1)
    return None


def resolve_apply_mode(props):
    if props.apply_mode != 'AUTO':
        return props.apply_mode
    if props.bake_type in ('COMBINED', 'DIFFUSE', 'AO', 'ENVIRONMENT', 'SHADOW'):
        return 'EMISSION'
    return 'BASE_COLOR'


def apply_baked_to_material(mat_name, baked_img, mode, keep_original, uv_layer_name):
    mat = bpy.data.materials.get(mat_name)
    if not mat or not mat.use_nodes:
        return False

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    generated_names = {
        'AHB_UV_Map', 'AHB_Applied_Texture', 'AHB_Applied_Color',
        'AHB_Emission', 'AHB_Principled',
    }
    for node in list(nodes):
        if node.name in generated_names:
            nodes.remove(node)

    if keep_original:
        output_node = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
        if not output_node:
            output_node = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)

        for n in nodes:
            if n.type != 'OUTPUT_MATERIAL':
                n.mute = True

        if output_node:
            for link in list(links):
                if link.to_node == output_node and link.to_socket.name == 'Surface':
                    links.remove(link)
        else:
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.name     = 'Material Output'
            output_node.location = (300, 0)

        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.name      = 'AHB_UV_Map'
        uv_node.label     = 'Bake UV'
        uv_node.uv_map    = uv_layer_name
        uv_node.location  = (output_node.location.x - 900, output_node.location.y)

        tex_node = nodes.new('ShaderNodeTexImage')
        tex_node.name     = 'AHB_Applied_Texture'
        tex_node.label    = 'Baked Texture'
        tex_node.image    = baked_img
        tex_node.interpolation = 'Closest' if mode == 'BASE_COLOR' else 'Linear'
        tex_node.extension = 'CLIP'
        tex_node.location = (output_node.location.x - 700, output_node.location.y)

        links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])

        if mode == 'EMISSION':
            emit_node = nodes.new('ShaderNodeEmission')
            emit_node.name     = 'AHB_Emission'
            emit_node.label    = 'Baked Emission'
            emit_node.location = (output_node.location.x - 350, output_node.location.y)
            emit_node.inputs['Strength'].default_value = 1.0

            links.new(tex_node.outputs['Color'], emit_node.inputs['Color'])
            links.new(emit_node.outputs['Emission'], output_node.inputs['Surface'])

        elif mode == 'BASE_COLOR':
            principled = nodes.new('ShaderNodeBsdfPrincipled')
            principled.name     = 'AHB_Principled'
            principled.location = (output_node.location.x - 350, output_node.location.y)

            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
            links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])

    else:
        nodes.clear()

        output = nodes.new('ShaderNodeOutputMaterial')
        output.name     = 'Material Output'
        output.location = (300, 0)

        uv_node = nodes.new('ShaderNodeUVMap')
        uv_node.name     = 'AHB_UV_Map'
        uv_node.label    = 'Bake UV'
        uv_node.uv_map   = uv_layer_name
        uv_node.location = (-600, 0)

        tex_node = nodes.new('ShaderNodeTexImage')
        tex_node.name     = 'AHB_Applied_Texture'
        tex_node.label    = 'Baked Texture'
        tex_node.image    = baked_img
        tex_node.interpolation = 'Closest' if mode == 'BASE_COLOR' else 'Linear'
        tex_node.extension = 'CLIP'
        tex_node.location = (-400, 0)

        links.new(uv_node.outputs['UV'], tex_node.inputs['Vector'])

        if mode == 'EMISSION':
            emit_node = nodes.new('ShaderNodeEmission')
            emit_node.name     = 'AHB_Emission'
            emit_node.label    = 'Baked Emission'
            emit_node.location = (-50, 0)
            emit_node.inputs['Strength'].default_value = 1.0

            links.new(tex_node.outputs['Color'], emit_node.inputs['Color'])
            links.new(emit_node.outputs['Emission'], output.inputs['Surface'])

        elif mode == 'BASE_COLOR':
            principled = nodes.new('ShaderNodeBsdfPrincipled')
            principled.name     = 'AHB_Principled'
            principled.location = (-50, 0)

            links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
            links.new(principled.outputs['BSDF'], output.inputs['Surface'])

    return True


def apply_baked_color_to_material(
        mat_name, attribute_name, mode, keep_original):
    """Wire a baked mesh color attribute into a material shader graph."""
    mat = bpy.data.materials.get(mat_name)
    if not mat or not mat.use_nodes:
        return False

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    generated_names = {
        'AHB_UV_Map', 'AHB_Applied_Texture', 'AHB_Applied_Color',
        'AHB_Emission', 'AHB_Principled',
    }
    for node in list(nodes):
        if node.name in generated_names:
            nodes.remove(node)

    if keep_original:
        output_node = next((
            node for node in nodes
            if node.type == 'OUTPUT_MATERIAL' and node.is_active_output), None)
        if not output_node:
            output_node = next((
                node for node in nodes if node.type == 'OUTPUT_MATERIAL'), None)
        for node in nodes:
            if node.type != 'OUTPUT_MATERIAL':
                node.mute = True
        if output_node:
            for link in list(links):
                if (link.to_node == output_node
                        and link.to_socket.name == 'Surface'):
                    links.remove(link)
        else:
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.name = 'Material Output'
            output_node.location = (300, 0)
        source_x = output_node.location.x - 700
        shader_x = output_node.location.x - 350
        source_y = output_node.location.y
    else:
        nodes.clear()
        output_node = nodes.new('ShaderNodeOutputMaterial')
        output_node.name = 'Material Output'
        output_node.location = (300, 0)
        source_x = -400
        shader_x = -50
        source_y = 0

    color_node = nodes.new('ShaderNodeVertexColor')
    color_node.name = 'AHB_Applied_Color'
    color_node.label = 'Baked Color Attribute'
    color_node.layer_name = attribute_name
    color_node.location = (source_x, source_y)

    if mode == 'EMISSION':
        shader = nodes.new('ShaderNodeEmission')
        shader.name = 'AHB_Emission'
        shader.label = 'Baked Emission'
        shader.inputs['Strength'].default_value = 1.0
        shader.location = (shader_x, source_y)
        links.new(color_node.outputs['Color'], shader.inputs['Color'])
        links.new(shader.outputs['Emission'], output_node.inputs['Surface'])
    else:
        shader = nodes.new('ShaderNodeBsdfPrincipled')
        shader.name = 'AHB_Principled'
        shader.location = (shader_x, source_y)
        links.new(color_node.outputs['Color'], shader.inputs['Base Color'])
        links.new(shader.outputs['BSDF'], output_node.inputs['Surface'])
    return True


def find_material_backup(mat):
    """This addon's backup of `mat`, or None.

    Looked up by recorded source name, not by `<name>_AHB_backup`. When a user
    already owns that name Blender uniquifies our copy to `..._AHB_backup.001`,
    so a name lookup never finds it again and every call leaked another backup.
    Matching on the name also adopted a user's own `X_AHB_backup` as ours,
    which made `restore_material_backups` rename the user's material.
    """
    for candidate in bpy.data.materials:
        if (candidate.get(OWNED_MATERIAL_PROP)
                and candidate.get(BACKUP_SOURCE_PROP) == mat.name):
            return candidate
    return None


def ensure_material_backup(mat):
    """Return this addon's copy of `mat`, creating it once."""
    existing = find_material_backup(mat)
    if existing is not None:
        return existing
    backup = mat.copy()
    backup[OWNED_MATERIAL_PROP] = True
    backup[BACKUP_SOURCE_PROP] = mat.name
    backup.name = f"{mat.name}_AHB_backup"
    return backup


def get_existing_tile_groups(obj_names, props):
    """Group objects by recorded tile image before using distribution fallback."""
    groups = {}
    fallback = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        image = get_object_bake_image(obj, props)
        if image and image.name.startswith(f"{props.output_prefix}tile_"):
            match = re.search(r'tile_(\d+)', image.name, re.IGNORECASE)
            if match:
                tile_idx = int(match.group(1)) - 1
                groups.setdefault(tile_idx, []).append(obj_name)
                continue
        fallback.append(obj_name)

    if fallback:
        predicted = distribute_tiles(fallback, props.tile_count)
        used = set(groups)
        next_idx = 0
        for tile_objs in predicted:
            while next_idx in used:
                next_idx += 1
            groups.setdefault(next_idx, []).extend(tile_objs)
            used.add(next_idx)
            next_idx += 1
    return [(idx, groups[idx]) for idx in sorted(groups)]


def apply_grouped_bake_images(groups, props, mode):
    """Apply one image per object group, isolating cross-group materials."""
    applied = 0
    skipped = 0
    first_group_by_material = {}
    variant_by_material_group = {}

    for group_key, suffix, object_names, image in groups:
        if not image:
            # Count materials, not slots, so `applied` and `skipped` share a
            # unit. Counting slots made one material used twice look like two
            # failures and sank the whole build.
            missing = set()
            for name in object_names:
                obj = bpy.data.objects.get(name)
                if not obj:
                    continue
                for slot in obj.material_slots:
                    if slot.material:
                        missing.add(slot.material)
            skipped += len(missing)
            continue

        done_in_group = set()
        for obj_name in object_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                material = slot.material
                if not material:
                    continue

                first_group = first_group_by_material.setdefault(
                    material, group_key)
                if first_group != group_key:
                    variant_key = (material, group_key)
                    variant = variant_by_material_group.get(variant_key)
                    if not variant:
                        source = ensure_material_backup(material)
                        variant = source.copy()
                        variant.name = f"{material.name}_{suffix}"
                        variant_by_material_group[variant_key] = variant
                    slot.material = variant
                    material = variant

                if material in done_in_group:
                    continue
                done_in_group.add(material)
                ensure_material_backup(material)
                if apply_baked_to_material(
                        material.name, image, mode,
                        props.apply_keep_original_nodes,
                        props.uv_layer_name):
                    applied += 1
                else:
                    skipped += 1

    return applied, skipped


def run_apply(obj_names, props, report):
    normalize_core_names(props)
    mode = resolve_apply_mode(props)
    applied = 0
    skipped = 0

    if not uses_image_bake_target(props):
        processed_mats = set()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            attribute = get_bake_color_attribute(obj)
            if not obj or not attribute:
                skipped += 1
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or mat.name in processed_mats:
                    continue
                processed_mats.add(mat.name)
                ensure_material_backup(mat)
                if apply_baked_color_to_material(
                        mat.name, attribute.name, mode,
                        props.apply_keep_original_nodes):
                    applied += 1
                else:
                    skipped += 1

    elif props.image_mode == 'AUTO_TILES':
        groups = []
        for tile_idx, tile_obj_names in get_existing_tile_groups(obj_names, props):
            image = bpy.data.images.get(
                f"{props.output_prefix}tile_{tile_idx + 1:02d}")
            if not image:
                report({'WARNING'}, f"No image for tile {tile_idx + 1}")
            groups.append((
                tile_idx, f"tile{tile_idx + 1:02d}", tile_obj_names, image))
        applied, skipped = apply_grouped_bake_images(groups, props, mode)

    elif props.image_mode == 'COLLECTION_ATLASES':
        groups = []
        for atlas_number, atlas_obj_names in get_atlas_groups(
                obj_names, props, report):
            if not atlas_obj_names:
                continue
            atlas_name = get_collection_atlas_name(props, atlas_number)
            image = bpy.data.images.get(f"{props.output_prefix}{atlas_name}")
            if not image:
                report({'WARNING'}, f"No baked image for {atlas_name}")
            groups.append((
                atlas_number, f"atlas{atlas_number:02d}",
                atlas_obj_names, image))
        applied, skipped = apply_grouped_bake_images(groups, props, mode)

    else:
        processed_mats = set()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or mat.name in processed_mats:
                    continue
                processed_mats.add(mat.name)

                image = None
                if props.image_mode == 'ATLAS':
                    image = bpy.data.images.get(f"{props.output_prefix}shared")
                elif props.image_mode == 'PER_MATERIAL':
                    # Never fall back to `shared` here: the single-atlas image
                    # has an unrelated UV layout and would silently produce a
                    # garbage texture that still reports success.
                    image = bpy.data.images.get(f"{props.output_prefix}{mat.name}")
                    if not image:
                        report(
                            {'WARNING'},
                            f"{mat.name}: no per-material bake image; skipped")
                        skipped += 1
                        continue
                if not image:
                    skipped += 1
                    continue

                ensure_material_backup(mat)
                if apply_baked_to_material(
                        mat.name, image, mode,
                        props.apply_keep_original_nodes,
                        props.uv_layer_name):
                    applied += 1
                else:
                    skipped += 1

    mode_label = 'Emission' if mode == 'EMISSION' else 'Base Color'
    msg = f"Applied {applied} material(s) as {mode_label}. Skipped: {skipped}"
    props.status_text = msg
    report({'INFO'}, msg)
    return applied, skipped


def restore_material_backups(obj_names, props=None):
    replacements = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            backup = find_material_backup(mat)
            if backup is not None:
                replacements[mat] = backup

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            if slot.material in replacements:
                slot.material = replacements[slot.material]

    restored = 0
    for modified, backup in replacements.items():
        original_name = modified.name
        # Only rename our clone. If the material is still used outside the
        # restored scope it keeps `users > 0`, and renaming it would leave those
        # objects pointing at a mangled name with no way back.
        if modified.get(OWNED_MATERIAL_PROP) or modified.users == 0:
            modified.name = f"{original_name}_AHB_modified"
            if modified.users == 0:
                bpy.data.materials.remove(modified)
        backup.name = original_name
        restored += 1
    restore_image_uv_backups(
        obj_names,
        props.image_node_name if props is not None else None,
    )
    return restored


def status_reporter(props, scene=None):
    """A report() callable that survives after the operator is gone.

    Deferred bake completion runs from a timer, by which point Blender has
    freed the operator's RNA struct -- calling `self.report` there raises
    `ReferenceError: StructRNA ... has been removed`.
    """
    def report(level, message):
        text = str(message)
        if level in {'ERROR', 'WARNING'}:
            props.status_text = text
        if scene is not None and hasattr(scene, 'web3d_status'):
            scene.web3d_status = text
        print(f"[Web3D] {level}: {text}")

    return report


def selection_snapshot(context):
    """Return a callable that restores the active object and selection.

    Kept separate from `save_and_restore_selection` so an asynchronous bake can
    hold the snapshot open until its completion callback fires.
    """
    active_obj = context.view_layer.objects.active
    selected_objects = list(context.selected_objects)

    def restore():
        force_object_mode()
        try:
            bpy.ops.object.select_all(action='DESELECT')
        except Exception:
            pass
        for obj in selected_objects:
            if obj:
                try:
                    obj.select_set(True)
                except Exception:
                    pass
        if active_obj:
            try:
                context.view_layer.objects.active = active_obj
            except Exception:
                pass

    return restore


def save_and_restore_selection(context, func):
    restore = selection_snapshot(context)
    try:
        return func()
    finally:
        restore()
