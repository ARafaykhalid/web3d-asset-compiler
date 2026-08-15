"""
Core lightmap and texture baking pipeline for Web3D Asset Compiler.
"""

import bpy
import bmesh
import math
import os
import re
import traceback
from mathutils import Vector

from ..utils import (
    force_ui_redraw,
    safe_filename,
    prepare_mesh_data,
    prepare_material_slots,
    get_render_uv_name,
    copy_image_uv_backup,
    preserve_implicit_texture_uvs,
    restore_image_uv_backups,
)
from .properties import (
    NODE_TAG,
    IMAGE_UV_BACKUP_LAYER,
    IMAGE_UV_SOURCE_PROP,
    IMAGE_UV_BACKUP_PROP,
    PASS_FILTER_TYPES,
    LINEAR_COLORSPACES,
    DATA_COLORSPACES,
    SRGB_COLORSPACES,
    EXT_MAP,
)

BAKE_COLOR_ATTRIBUTE = "AHB_BakedColor"
BAKE_COLOR_ATTRIBUTE_PROP = "ahb_baked_color_attribute"


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
    if is_data:
        candidates = DATA_COLORSPACES
    else:
        candidates = LINEAR_COLORSPACES if is_hdr else SRGB_COLORSPACES
    for cs in candidates:
        try:
            image.colorspace_settings.name = cs
            return
        except (TypeError, RuntimeError):
            continue


def get_or_create_image(props, name):
    normalize_core_names(props)
    rx, ry = get_resolution(props)
    is_hdr = props.image_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR')
    is_data = props.bake_type in ('AO', 'NORMAL', 'ROUGHNESS', 'SHADOW', 'UV')
    img_name = f"{props.output_prefix}{name}"

    img = bpy.data.images.get(img_name)
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
                    new_mat = mat.copy()
                    new_mat.name = f"{mat.name}_atlas{atlas_number:02d}"
                    slot.material = new_mat
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
    rx, ry = get_resolution(props)
    return px / min(rx, ry)


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
    force_object_mode()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if layer_name in obj.data.uv_layers:
        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]


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
                data = {
                    'area': sum(face.calc_area() for face in faces),
                    'perimeter': sum(face.calc_perimeter() for face in faces),
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

    force_object_mode()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    if layer_name in obj.data.uv_layers:
        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

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
    rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'
    rotation_mode = props.pack_rotation_step
    if rotation_mode in ('90', '45'):
        rotation_mode = 'AXIS_ALIGNED'
    rot_method = ('ANY' if rotation_mode == 'ANY' else 'AXIS_ALIGNED')
    shape_method = ('CONCAVE' if props.pack_nest_holes
                    else props.pack_shape_method)
    scale = True
    merge_overlap = props.pack_stack_identical

    try:
        bpy.ops.uv.pack_islands(
            margin_method='FRACTION',
            margin=uv_margin,
            rotate=rotate,
            rotate_method=rot_method,
            scale=scale,
            shape_method=shape_method,
            merge_overlap=merge_overlap,
        )
        return
    except Exception:
        pass

    try:
        bpy.ops.uv.pack_islands(
            margin=uv_margin,
            rotate=rotate,
            scale=scale,
            shape_method=shape_method,
            merge_overlap=merge_overlap,
        )
        return
    except Exception:
        pass

    try:
        bpy.ops.uv.pack_islands(margin=uv_margin, rotate=rotate)
    except Exception:
        pass


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


def run_uv_pack(obj_name, props):
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    layer_name = props.uv_layer_name
    if layer_name not in obj.data.uv_layers:
        return

    uv_margin = pixel_margin_to_uv(props.pack_margin_px, props)
    rotate = props.pack_rotate and props.pack_rotation_step != 'NONE'

    if props.pack_engine == 'BLENDER' and props.pack_stack_identical:
        stack_similar_islands([obj_name], layer_name)

    enter_edit_select_all(obj, layer_name)
    try:
        if props.pack_engine == 'UVP3' and hasattr(bpy.ops, 'uvpackmaster3'):
            try:
                bpy.context.scene.uvp3_props.margin = uv_margin
                bpy.ops.uvpackmaster3.pack()
                return
            except Exception:
                pass
        elif props.pack_engine == 'UVP2' and hasattr(bpy.ops, 'uvpackmaster2'):
            try:
                bpy.context.scene.uvp2_props.margin = uv_margin
                bpy.ops.uvpackmaster2.uv_pack()
                return
            except Exception:
                pass

        for _i in range(props.pack_iterations):
            run_blender_pack(props, uv_margin)

    finally:
        force_object_mode()


def pack_uv_islands_phased(obj_name, props):
    if props.pack_world_scale:
        run_uv_normalize(obj_name, props)
    if props.pack_image_boost > 1.01:
        apply_image_boost(obj_name, props)
    run_uv_pack(obj_name, props)
    if props.pack_scale_islands:
        scale_uvs_to_bounds(
            [obj_name], props.uv_layer_name,
            margin=pixel_margin_to_uv(props.pack_margin_px, props))


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


def ensure_uv(obj_name, props):
    obj = bpy.data.objects.get(obj_name)
    if not obj or obj.type != 'MESH':
        return
    if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
        return

    mesh = obj.data
    layer_name = props.uv_layer_name

    if layer_name not in mesh.uv_layers:
        mesh.uv_layers.new(name=layer_name)

    uv_layer = mesh.uv_layers[layer_name]
    mesh.uv_layers.active = uv_layer

    if props.uv_mode == 'EXISTING':
        if props.pack_enabled:
            pack_uv_islands_phased(obj_name, props)
        return

    if (props.uv_mode == 'AUTO_SEAM'
            and getattr(props, 'renew_auto_seams_on_preview', False)):
        for edge in mesh.edges:
            edge.use_seam = False

    force_object_mode()
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        mesh.uv_layers.active = mesh.uv_layers[layer_name]

        if props.uv_mode == 'SMART':
            angle_rad = math.radians(props.smart_uv_angle)
            bpy.ops.uv.smart_project(
                angle_limit=angle_rad,
                island_margin=props.smart_uv_island_margin,
            )
        elif props.uv_mode == 'CUBE':
            bpy.ops.uv.cube_project(cube_size=props.cube_size)
            try:
                bpy.ops.uv.average_islands_scale()
            except Exception:
                pass
        elif props.uv_mode == 'UNWRAP':
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=props.smart_uv_island_margin)
        elif props.uv_mode == 'AUTO_SEAM':
            seam_rad = math.radians(props.auto_seam_angle)
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
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
                angle_rad = math.radians(66.0)
                bpy.ops.uv.smart_project(angle_limit=angle_rad, island_margin=0.03)
    finally:
        force_object_mode()

    if (props.uv_mode == 'AUTO_SEAM'
            and subdivide_large_uv_islands(obj_name, layer_name)):
        obj = bpy.data.objects.get(obj_name)
        if obj:
            enter_edit_select_all(obj, layer_name)
            try:
                bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            finally:
                force_object_mode()

    if props.pack_enabled:
        pack_uv_islands_phased(obj_name, props)

    if props.remove_old_uvs:
        remove_old_uvs([obj_name], props.uv_layer_name)


def setup_atlas_uvs(obj_names, props):
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
            atlas_pack_phase(valid_objs, props)
        return

    force_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    first_obj = None
    for obj_name in valid_objs:
        obj = bpy.data.objects.get(obj_name)
        if obj:
            obj.select_set(True)
            if first_obj is None:
                first_obj = obj

    if not first_obj:
        return
    bpy.context.view_layer.objects.active = first_obj

    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        for obj_name in valid_objs:
            obj = bpy.data.objects.get(obj_name)
            if obj and layer_name in obj.data.uv_layers:
                obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

        if props.uv_mode == 'SMART':
            angle_rad = math.radians(props.smart_uv_angle)
            bpy.ops.uv.smart_project(
                angle_limit=angle_rad,
                island_margin=props.smart_uv_island_margin,
            )
        elif props.uv_mode == 'CUBE':
            bpy.ops.uv.cube_project(cube_size=props.cube_size)
            try:
                bpy.ops.uv.average_islands_scale()
            except Exception:
                pass
        elif props.uv_mode == 'UNWRAP':
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=props.smart_uv_island_margin)
        elif props.uv_mode == 'AUTO_SEAM':
            seam_rad = math.radians(props.auto_seam_angle)
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
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
                angle_rad = math.radians(66.0)
                bpy.ops.uv.smart_project(angle_limit=angle_rad, island_margin=0.03)
    finally:
        force_object_mode()

    if props.uv_mode == 'AUTO_SEAM':
        any_split = False
        for obj_name in valid_objs:
            if subdivide_large_uv_islands(obj_name, layer_name):
                any_split = True
        if any_split:
            force_object_mode()
            bpy.ops.object.select_all(action='DESELECT')
            first_obj = None
            for obj_name in valid_objs:
                obj = bpy.data.objects.get(obj_name)
                if obj:
                    obj.select_set(True)
                    if first_obj is None:
                        first_obj = obj
            if first_obj:
                bpy.context.view_layer.objects.active = first_obj
                try:
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='SELECT')
                    for obj_name in valid_objs:
                        obj = bpy.data.objects.get(obj_name)
                        if obj and layer_name in obj.data.uv_layers:
                            obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
                    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
                finally:
                    force_object_mode()

    if props.pack_enabled:
        atlas_pack_phase(valid_objs, props)

    if props.remove_old_uvs:
        remove_old_uvs(valid_objs, props.uv_layer_name)


def atlas_pack_phase(obj_names, props):
    layer_name = props.uv_layer_name
    if props.pack_world_scale:
        force_object_mode()
        bpy.ops.object.select_all(action='DESELECT')
        first_obj = None
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if obj:
                obj.select_set(True)
                if first_obj is None:
                    first_obj = obj
        if first_obj:
            bpy.context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                for obj_name in obj_names:
                    obj = bpy.data.objects.get(obj_name)
                    if obj and layer_name in obj.data.uv_layers:
                        obj.data.uv_layers.active = obj.data.uv_layers[layer_name]
                try:
                    bpy.ops.uv.average_islands_scale()
                except Exception:
                    pass
            finally:
                force_object_mode()
        for obj_name in obj_names:
            apply_world_area_scale(obj_name, layer_name)

    if props.pack_engine == 'BLENDER' and props.pack_stack_identical:
        stack_similar_islands(obj_names, layer_name)

    force_object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    first_obj = None
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj:
            obj.select_set(True)
            if first_obj is None:
                first_obj = obj
    if first_obj:
        bpy.context.view_layer.objects.active = first_obj
        uv_margin = pixel_margin_to_uv(props.pack_margin_px, props)
        packed = False
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if obj and layer_name in obj.data.uv_layers:
                    obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

            if props.pack_engine == 'UVP3' and hasattr(bpy.ops, 'uvpackmaster3'):
                try:
                    bpy.context.scene.uvp3_props.margin = uv_margin
                    bpy.ops.uvpackmaster3.pack()
                    packed = True
                except Exception:
                    pass
            if (not packed and props.pack_engine == 'UVP2'
                    and hasattr(bpy.ops, 'uvpackmaster2')):
                try:
                    bpy.context.scene.uvp2_props.margin = uv_margin
                    bpy.ops.uvpackmaster2.uv_pack()
                    packed = True
                except Exception:
                    pass

            if not packed:
                for _i in range(props.pack_iterations):
                    run_blender_pack(props, uv_margin)
        finally:
            force_object_mode()

        if props.pack_scale_islands:
            scale_uvs_to_bounds(obj_names, layer_name, margin=uv_margin)


def distribute_tiles(obj_names, tile_count):
    obj_areas = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        area = sum(p.area for p in obj.data.polygons) if obj.data.polygons else 0.0
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

        force_object_mode()
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer:
                loops = [loop for face in bm.faces for loop in face.loops]
                if loops:
                    min_x = min(loop[uv_layer].uv.x for loop in loops)
                    tile_offset = math.floor(min_x + 1e-6)
                    if tile_offset > 0:
                        for loop in loops:
                            loop[uv_layer].uv.x -= tile_offset
                        bmesh.update_edit_mesh(obj.data)
        finally:
            force_object_mode()


def offset_tile_uvs(obj_names, tile_idx, layer_name):
    if tile_idx == 0:
        return

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        if len(obj.data.vertices) == 0 or len(obj.data.polygons) == 0:
            continue
        if layer_name not in obj.data.uv_layers:
            continue

        force_object_mode()
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer:
                offset_x = float(tile_idx)
                for face in bm.faces:
                    for loop in face.loops:
                        loop[uv_layer].uv.x += offset_x
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

        for device in devices:
            device.use = device in gpu_devices

        scene.cycles.device = 'GPU'
        names = ", ".join(device.name for device in gpu_devices)
        props.device_status = f"GPU / {backend}: {names}"
        return

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
        bake.margin = min(props.margin, max(0, props.pack_margin_px // 2))
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
    save_format = ('OPEN_EXR' if props.image_format == 'OPEN_EXR_MULTILAYER'
                   else props.image_format)
    original_settings = {}
    for setting_name in (
            'file_format', 'color_mode', 'color_depth', 'exr_codec', 'quality'):
        try:
            original_settings[setting_name] = getattr(img_settings, setting_name)
        except (AttributeError, TypeError):
            continue
    try:
        img_settings.file_format = save_format
        img_settings.color_mode = ('RGB' if save_format in ('JPEG', 'HDR')
                                   else props.color_mode)

        if save_format == 'OPEN_EXR':
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

    return path


def do_bake_batch(obj_names, props, context, source_obj_names=None):
    objs_to_bake = []
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if obj and obj.type == 'MESH':
            objs_to_bake.append(obj)

    if not objs_to_bake:
        return False

    if not validate_bake_ready([obj.name for obj in objs_to_bake], props,
                                lambda _level, _message: None):
        target_label = ("UV layer or active image node"
                        if uses_image_bake_target(props)
                        else "active color attribute")
        raise RuntimeError(f"Bake target is missing its required {target_label}")

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

    def execute_bake():
        if getattr(props, 'multires_bake', False):
            return bpy.ops.object.bake_image()
        return bpy.ops.object.bake(type=props.bake_type)

    try:
        with bpy.context.temp_override(
            active_object=active_obj,
            selected_objects=selected_for_bake,
            selected_editable_objects=selected_for_bake,
        ):
            result = execute_bake()
    except RuntimeError as e:
        try:
            result = execute_bake()
        except Exception:
            raise e

    if not result or 'FINISHED' not in result:
        raise RuntimeError("Blender cancelled the bake operation")

    return True


def validate_bake_ready(obj_names, props, report):
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
        for slot_index in {poly.material_index for poly in obj.data.polygons}:
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
        return False
    return True


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


def clear_and_renew_auto_seams(obj_names, props):
    seam_rad = math.radians(props.auto_seam_angle)
    count = 0
    force_object_mode()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data

        for edge in mesh.edges:
            edge.use_seam = False

        bpy.context.view_layer.objects.active = obj
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=seam_rad, island_margin=0.001)
            bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
            bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.001)
            count += 1
        except Exception:
            pass
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
            for o in tile_objs: predicted_suffixes[o] = suffix
    elif image_mode == 'ATLAS':
        for o in obj_names: predicted_suffixes[o] = f"_{props.output_prefix}shared"
    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_objs in get_atlas_groups(obj_names, props):
            suffix = f"_{props.output_prefix}{get_collection_atlas_name(props, atlas_number)}"
            for o in atlas_objs:
                predicted_suffixes[o] = suffix

    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj: continue

        actual_texture = obj.get("ahb_baked_texture")
        if not actual_texture:
            for slot in obj.material_slots:
                if slot.material and slot.material.use_nodes:
                    node = slot.material.node_tree.nodes.get(props.image_node_name)
                    if node and node.type == 'TEX_IMAGE' and node.image:
                        actual_texture = node.image.name
                        break

        if actual_texture:
            suffix = f"_{actual_texture}".replace("Tile", "tile").replace("TILE", "tile")
        else:
            suffix = predicted_suffixes.get(obj.name)

        if not suffix:
            continue

        if obj.name.endswith(suffix):
            continue

        obj.name = f"{obj.name}{suffix}"
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
            obj["ahb_baked_texture"] = col_name

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


def run_setup(obj_names, props, report):
    normalize_core_names(props)
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
                ensure_uv(obj_name, props)
            if not ensure_bake_color_attribute(obj):
                report({'ERROR'}, f"Cannot create a bake color attribute on {obj_name}")
                return False

        if not validate_bake_ready(obj_names, props, report):
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
                setup_atlas_uvs(obj_names, props)
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
                    setup_atlas_uvs(tile_obj_names, props)
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
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        inject_bake_node(slot.material.name, tile_img, props.image_node_name)

    elif image_mode == 'COLLECTION_ATLASES':
        for atlas_number, atlas_obj_names in collection_groups:
            if not atlas_obj_names:
                continue
            remove_tile_uv_offsets(atlas_obj_names, props.uv_layer_name)
            if props.auto_create_uv:
                try:
                    setup_atlas_uvs(atlas_obj_names, props)
                except Exception as e:
                    report({'ERROR'}, f"Atlas {atlas_number} UV setup failed: {e}")
                    force_object_mode()
                    return False

            atlas_img = get_collection_atlas_image(props, atlas_number)
            atlas_mats = collect_material_names(atlas_obj_names)
            for mat_name in atlas_mats:
                inject_bake_node(mat_name, atlas_img, props.image_node_name)

    elif image_mode == 'PER_MATERIAL':
        for obj_name in obj_names:
            if props.auto_create_uv:
                try:
                    ensure_uv(obj_name, props)
                except Exception as e:
                    report({'ERROR'}, f"UV setup failed [{obj_name}]: {e}")
                    force_object_mode()
                    return False

        for mat_name in mat_map:
            img = get_or_create_image(props, mat_name)
            inject_bake_node(mat_name, img, props.image_node_name)

    ready_names = (flatten_atlas_groups(collection_groups)
                   if collection_groups is not None else obj_names)
    if not validate_bake_ready(ready_names, props, report):
        return False

    msg = f"Setup done: {len(mat_map)} material(s) across {len(obj_names)} object(s)"
    props.status_text = msg
    report({'INFO'}, msg)
    return True


def run_bake(obj_names, props, context, report, source_obj_names=None):
    normalize_core_names(props)
    if (props.bake_type in PASS_FILTER_TYPES
            and not (props.use_pass_direct or props.use_pass_indirect
                     or props.use_pass_color)):
        report({'ERROR'}, "Enable at least one Direct, Indirect, or Color pass.")
        return 0, 1

    try:
        configure_bake_settings(props, context)
    except Exception as e:
        report({'ERROR'}, f"Cannot configure bake: {e}")
        return 0, 1

    image_mode = props.image_mode
    all_mat_names = set()
    baked_names = set()
    errors = 0
    total = len(obj_names)

    if not uses_image_bake_target(props):
        props.status_text = f"Baking color attributes on {len(obj_names)} object(s)"
        force_ui_redraw()
        try:
            if do_bake_batch(obj_names, props, context, source_obj_names):
                baked_names.update(obj_names)
                for obj_name in obj_names:
                    obj = bpy.data.objects.get(obj_name)
                    attribute = get_bake_color_attribute(obj)
                    if obj and attribute:
                        obj[BAKE_COLOR_ATTRIBUTE_PROP] = attribute.name
                report({'INFO'}, f"Baked color attributes on {len(obj_names)} object(s)")
        except Exception as e:
            errors += 1
            report({'ERROR'}, f"Color attribute bake FAILED: {e}")

    elif image_mode == 'AUTO_TILES':
        tiles = distribute_tiles(obj_names, props.tile_count)
        for tile_idx, tile_obj_names in enumerate(tiles):
            tile_img = get_tile_image(props, tile_idx)
            valid_objs = []
            for obj_name in tile_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                obj["ahb_baked_texture"] = tile_img.name
                has_mat = False
                for slot in obj.material_slots:
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        all_mat_names.add(slot.material.name)
                        inject_bake_node(slot.material.name, tile_img, props.image_node_name)
                        has_mat = True
                if has_mat:
                    valid_objs.append(obj_name)

            if not valid_objs:
                continue
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking tile {tile_idx + 1}/{len(tiles)} ({len(valid_objs)} objects)"
            force_ui_redraw()

            try:
                success = do_bake_batch(valid_objs, props, context, source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"[Tile {tile_idx + 1}] Baked {len(valid_objs)} objects")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED [Tile {tile_idx + 1}]: {e}")

    elif image_mode == 'COLLECTION_ATLASES':
        groups = get_atlas_groups(obj_names, props, report)
        total = len(flatten_atlas_groups(groups))
        isolate_materials_between_atlas_groups(groups)
        for atlas_number, atlas_obj_names in groups:
            if not atlas_obj_names:
                continue
            col_name = get_collection_atlas_name(props, atlas_number)
            atlas_img = get_collection_atlas_image(props, atlas_number)
            valid_objs = []
            for obj_name in atlas_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                obj["ahb_baked_texture"] = atlas_img.name
                has_mat = False
                for slot in obj.material_slots:
                    if slot.material:
                        if not slot.material.use_nodes:
                            slot.material.use_nodes = True
                        all_mat_names.add(slot.material.name)
                        inject_bake_node(slot.material.name, atlas_img, props.image_node_name)
                        has_mat = True
                if has_mat:
                    valid_objs.append(obj_name)

            if not valid_objs:
                continue
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking {col_name} ({len(valid_objs)} objects)"
            force_ui_redraw()

            try:
                success = do_bake_batch(valid_objs, props, context, source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"[{col_name}] Baked {len(valid_objs)} objects")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED [{col_name}]: {e}")

    elif image_mode == 'ATLAS':
        atlas_img = get_or_create_image(props, "shared")
        valid_objs = []
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            obj["ahb_baked_texture"] = atlas_img.name
            has_mat = False
            for slot in obj.material_slots:
                if slot.material:
                    if not slot.material.use_nodes:
                        slot.material.use_nodes = True
                    all_mat_names.add(slot.material.name)
                    inject_bake_node(slot.material.name, atlas_img, props.image_node_name)
                    has_mat = True
            if has_mat:
                valid_objs.append(obj_name)

        if valid_objs:
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking Single Atlas ({len(valid_objs)} objects)"
            force_ui_redraw()

            try:
                success = do_bake_batch(valid_objs, props, context, source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"Baked Single Atlas ({len(valid_objs)} objects)")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Bake FAILED: {e}")

    elif image_mode == 'PER_MATERIAL':
        mat_map = collect_material_names(obj_names)
        valid_objs = set()
        object_material_images = {}
        for mat_name, mat_obj_names in mat_map.items():
            mat_img = get_or_create_image(props, mat_name)
            inject_bake_node(mat_name, mat_img, props.image_node_name)
            all_mat_names.add(mat_name)
            for obj_name in mat_obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    continue
                valid_objs.add(obj_name)
                object_material_images.setdefault(obj_name, set()).add(mat_img.name)

        for obj_name, image_names in object_material_images.items():
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            if len(image_names) == 1:
                obj["ahb_baked_texture"] = next(iter(image_names))
            elif "ahb_baked_texture" in obj:
                del obj["ahb_baked_texture"]

        if valid_objs:
            valid_objs = sorted(valid_objs)
            bake = context.scene.render.bake
            bake.use_clear = props.clear_bake
            props.status_text = f"Baking {len(mat_map)} material image(s) across {len(valid_objs)} object(s)"
            force_ui_redraw()

            try:
                success = do_bake_batch(valid_objs, props, context, source_obj_names)
                if success:
                    baked_names.update(valid_objs)
                    report({'INFO'}, f"Baked {len(mat_map)} per-material image(s)")
            except Exception as e:
                errors += 1
                report({'ERROR'}, f"Per-material bake FAILED: {e}")

    if (uses_image_bake_target(props) and props.auto_save
            and getattr(props, 'save_mode', 'EXTERNAL') == 'EXTERNAL'):
        props.status_text = "Saving images…"
        force_ui_redraw()
        saved = 0
        for img in bpy.data.images:
            if img.name.startswith(props.output_prefix) and img.has_data:
                try:
                    path = save_image(img, props, context)
                    saved += 1
                except Exception as e:
                    errors += 1
                    report({'WARNING'}, f"Save failed [{img.name}]: {e}")

    if props.auto_cleanup_nodes and baked_names and all_mat_names:
        remove_bake_nodes(all_mat_names, props.image_node_name)

    baked = len(baked_names)
    msg = f"Baked {baked}/{total} object(s). Errors: {errors}"
    report({'INFO'}, msg)
    return baked, errors


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


def ensure_material_backup(mat):
    backup_name = f"{mat.name}_AHB_backup"
    backup = bpy.data.materials.get(backup_name)
    if not backup:
        backup = mat.copy()
        backup.name = backup_name
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
            skipped += sum(
                len(obj.material_slots) for name in object_names
                if (obj := bpy.data.objects.get(name)))
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
                    image = bpy.data.images.get(f"{props.output_prefix}{mat.name}")
                if not image:
                    image = bpy.data.images.get(f"{props.output_prefix}shared")
                if not image:
                    image = bpy.data.images.get(f"{props.output_prefix}{mat.name}")
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


def restore_material_backups(obj_names):
    replacements = {}
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue
            backup = bpy.data.materials.get(f"{mat.name}_AHB_backup")
            if backup:
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
        modified.name = f"{original_name}_AHB_modified"
        backup.name = original_name
        if modified.users == 0:
            bpy.data.materials.remove(modified)
        restored += 1
    restore_image_uv_backups(obj_names)
    return restored


def save_and_restore_selection(context, func):
    active_obj = context.view_layer.objects.active
    selected_objects = list(context.selected_objects)

    try:
        result = func()
    finally:
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

    return result
