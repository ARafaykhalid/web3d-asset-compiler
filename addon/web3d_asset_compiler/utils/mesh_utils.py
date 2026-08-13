"""
Mesh manipulation and slot management utilities.
"""

import bpy


def prepare_mesh_data(obj_names, make_unique=False):
    """Make mesh data unique when objects need independent atlas UVs."""
    seen_meshes = set()
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH':
            continue
        mesh = obj.data
        if make_unique and (mesh.users > 1 or mesh.as_pointer() in seen_meshes):
            obj.data = mesh.copy()
            mesh = obj.data
        seen_meshes.add(mesh.as_pointer())


def prepare_material_slots(obj_names):
    """Ensure every material slot used by a face has a node material."""
    created = 0
    for obj_name in obj_names:
        obj = bpy.data.objects.get(obj_name)
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            continue

        used_indices = {poly.material_index for poly in obj.data.polygons}
        if not obj.material_slots:
            mat = bpy.data.materials.new(f"AHB_Default_{obj.name}")
            mat.use_nodes = True
            obj.data.materials.append(mat)
            created += 1
            continue

        for slot_index in used_indices:
            if slot_index >= len(obj.material_slots):
                continue
            slot = obj.material_slots[slot_index]
            if not slot.material:
                mat = bpy.data.materials.new(
                    f"AHB_Default_{obj.name}_{slot_index + 1:02d}")
                mat.use_nodes = True
                slot.material = mat
                created += 1
            elif not slot.material.use_nodes:
                slot.material.use_nodes = True
    return created
