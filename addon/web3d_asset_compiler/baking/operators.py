"""
Operator classes for Auto HDR Baker within Web3D Asset Compiler.
"""

import bpy
import json
import traceback
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .pipeline import (
    get_object_names,
    resolve_pipeline_targets,
    run_setup,
    run_bake,
    run_apply,
    save_and_restore_selection,
    restore_material_backups,
    normalize_core_names,
    prepare_mesh_data,
    prepare_material_slots,
    preserve_implicit_texture_uvs,
    setup_atlas_uvs,
    get_object_bake_image,
    inject_bake_node,
    remove_bake_nodes,
    validate_bake_ready,
    configure_bake_settings,
    do_bake_batch,
    save_image,
    apply_single_object_bake,
    resolve_apply_mode,
    scope_has_material_backups,
    group_objects_by_tile,
    get_collection_atlas_name,
    rename_objects_by_texture,
    remove_all_uvs,
    clear_and_renew_auto_seams,
    remove_tile_uv_offsets,
    offset_tile_uvs,
    distribute_tiles,
    get_atlas_groups,
    ensure_uv,
    atlas_pack_phase,
    pack_uv_islands_phased,
    IMAGE_UV_BACKUP_PROP,
    IMAGE_UV_BACKUP_LAYER,
)
from .json_io import (
    get_principled_bsdf,
    extract_socket_value,
    load_texture,
)


class AHB_OT_ExportMaterialsJSON(Operator, ExportHelper):
    """Export material assignments and PBR properties to JSON"""
    bl_idname = "ahb.export_materials_json"
    bl_label = "Export Materials to JSON"
    bl_options = {'REGISTER'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = get_object_names(props, context)

        data = {'objects': {}, 'materials': {}}

        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH': continue

            mats = [slot.material.name for slot in obj.material_slots if slot.material]
            data['objects'][obj_name] = mats

            for mat_name in mats:
                if mat_name in data['materials']: continue
                mat = bpy.data.materials.get(mat_name)

                mat_data = {'name': mat_name}
                bsdf = get_principled_bsdf(mat)
                if bsdf:
                    mat_data['pbr'] = {}
                    for key in ['Base Color', 'Metallic', 'Roughness', 'Specular IOR Level', 'Emission Color', 'Normal', 'Alpha']:
                        if key in bsdf.inputs:
                            val = extract_socket_value(bsdf.inputs[key])
                            if val:
                                mat_data['pbr'][key] = val

                data['materials'][mat_name] = mat_data

        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=4)

        self.report({'INFO'}, f"Exported {len(data['materials'])} materials to JSON.")
        return {'FINISHED'}


class AHB_OT_ImportMaterialsJSON(Operator, ImportHelper):
    """Import material assignments and PBR properties from JSON"""
    bl_idname = "ahb.import_materials_json"
    bl_label = "Import Materials from JSON"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            try:
                data = json.load(f)
            except Exception as e:
                self.report({'ERROR'}, f"Failed to parse JSON: {e}")
                return {'CANCELLED'}

        for mat_name, mat_data in data.get('materials', {}).items():
            mat = bpy.data.materials.get(mat_name)
            if not mat:
                mat = bpy.data.materials.new(mat_name)
                mat.use_nodes = True

            bsdf = get_principled_bsdf(mat)
            if not bsdf and mat.use_nodes:
                bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')

            if bsdf and 'pbr' in mat_data:
                for key, val in mat_data['pbr'].items():
                    if key in bsdf.inputs:
                        socket = bsdf.inputs[key]
                        if val['type'] == 'texture':
                            img = load_texture(val['path'])
                            if img:
                                tex_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
                                tex_node.image = img
                                if key == 'Normal':
                                    norm_node = mat.node_tree.nodes.new('ShaderNodeNormalMap')
                                    mat.node_tree.links.new(tex_node.outputs['Color'], norm_node.inputs['Color'])
                                    mat.node_tree.links.new(norm_node.outputs['Normal'], socket)
                                else:
                                    mat.node_tree.links.new(tex_node.outputs['Color'], socket)
                        elif val['type'] == 'vector':
                            socket.default_value = val['value']
                        elif val['type'] == 'float':
                            socket.default_value = val['value']

        for obj_name, mats in data.get('objects', {}).items():
            obj = bpy.data.objects.get(obj_name)
            if not obj: continue

            obj.data.materials.clear()
            for mat_name in mats:
                mat = bpy.data.materials.get(mat_name)
                if mat:
                    obj.data.materials.append(mat)

        self.report({'INFO'}, "Imported materials from JSON successfully.")
        return {'FINISHED'}


class AHB_OT_SetupMaterials(Operator):
    """Scan objects, create bake UV layers, and inject Image Texture nodes"""
    bl_idname  = "ahb.setup_materials"
    bl_label   = "Setup Material Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, _source_names = resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        def work():
            return run_setup(obj_names, props, self.report)

        ok = save_and_restore_selection(context, work)
        return {'FINISHED'} if ok else {'CANCELLED'}


class AHB_OT_BakeAll(Operator):
    """Run the automated HDR bake pipeline on all objects in scope"""
    bl_idname  = "ahb.bake_all"
    bl_label   = "Bake All"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, source_names = resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        def work():
            return run_bake(obj_names, props, context, self.report, source_names)

        baked, _errors = save_and_restore_selection(context, work)
        return {'FINISHED'} if baked else {'CANCELLED'}


class AHB_OT_RebakeSelected(Operator):
    """Rebake the active object into its existing atlas/tile/material image."""
    bl_idname = "ahb.rebake_selected"
    bl_label = "Rebake Selected Object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH' and obj.data.polygons)

    def execute(self, context):
        props = context.scene.ahb_props
        normalize_core_names(props)
        obj = context.active_object
        if not obj or obj.type != 'MESH' or not obj.data.polygons:
            self.report({'WARNING'}, "Select an active mesh object to rebake.")
            return {'CANCELLED'}

        obj_names = [obj.name]
        source_names = [o.name for o in context.selected_objects
                        if o.type == 'MESH' and o != obj and o.data.polygons]

        def work():
            restore_material_backups(obj_names)
            prepare_mesh_data(obj_names, make_unique=props.auto_create_uv)
            prepare_material_slots(obj_names)
            preserve_implicit_texture_uvs(obj_names, props)

            if props.uv_layer_name not in obj.data.uv_layers:
                if props.image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
                    setup_atlas_uvs(obj_names, props)
                else:
                    ensure_uv(obj.name, props)

            target_images = {}
            for slot in obj.material_slots:
                mat = slot.material
                if not mat:
                    continue
                image = get_object_bake_image(
                    obj, props, mat if props.image_mode == 'PER_MATERIAL' else None)
                if image:
                    target_images[mat.name] = image
                    inject_bake_node(mat.name, image, props.image_node_name)

            if not target_images:
                raise RuntimeError("The object has no existing bake image/texture-pack assignment")
            if not validate_bake_ready(obj_names, props, self.report):
                return False

            configure_bake_settings(props, context)
            context.scene.render.bake.use_clear = False
            props.status_text = f"Rebaking {obj.name} in its existing texture pack…"

            do_bake_batch(obj_names, props, context, source_names)

            if props.auto_save:
                for image in {image.name: image for image in target_images.values()}.values():
                    if image.has_data:
                        save_image(image, props, context)

            if props.auto_cleanup_nodes:
                remove_bake_nodes(set(target_images), props.image_node_name)

            props.baked_view = True
            props.status_text = f"Rebaked {obj.name} in its existing texture pack."
            return True

        try:
            ok = save_and_restore_selection(context, work)
        except Exception as e:
            props.status_text = f"Selected rebake failed: {e}"
            self.report({'ERROR'}, f"Selected rebake failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'} if ok else {'CANCELLED'}


class AHB_OT_QuickBake(Operator):
    """One-click: detect materials → setup nodes → bake → save"""
    bl_idname  = "ahb.quick_bake"
    bl_label   = "Quick Bake (Full Pipeline)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}
        obj_names, source_names = resolve_pipeline_targets(
            context, props, obj_names, self.report)
        if not obj_names:
            return {'CANCELLED'}

        self.report({'INFO'}, f"Starting full pipeline for {len(obj_names)} object(s)…")
        props.status_text = f"Pipeline: {len(obj_names)} object(s)…"

        def work():
            restored = restore_material_backups(obj_names)
            if restored:
                self.report({'INFO'}, f"Restored {restored} original material(s) before rebaking.")

            ok = run_setup(obj_names, props, self.report)
            if not ok:
                props.status_text = "Setup failed!"
                return False

            baked, errs = run_bake(obj_names, props, context, self.report, source_names)
            if baked == 0:
                props.status_text = "Bake failed — 0 objects baked!"
                return False
            if errs:
                props.status_text = f"Bake incomplete — {baked} object(s), {errs} error(s)"
                return False

            props.status_text = "Applying baked textures…"
            run_apply(obj_names, props, self.report)

            if props.auto_rename_objects:
                rename_objects_by_texture(obj_names, props, self.report)

            props.status_text = f"Pipeline complete ✓  ({baked} baked, {errs} errors)"
            props.baked_view = True
            return True

        try:
            ok = save_and_restore_selection(context, work)
        except Exception as e:
            props.status_text = f"Pipeline failed: {e}"
            self.report({'ERROR'}, f"Pipeline failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'} if ok else {'CANCELLED'}


class AHB_OT_RemoveAllUVMaps(Operator):
    """Remove ALL UV maps from objects in scope, leaving meshes completely clean"""
    bl_idname  = "ahb.remove_all_uv_maps"
    bl_label   = "Remove All UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        removed_count, obj_count = remove_all_uvs(obj_names)
        msg = f"Removed {removed_count} UV map(s) across {obj_count} object(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AHB_OT_DeleteOldUVMaps(Operator):
    """Delete old UV maps while retaining bake and image-material backups."""
    bl_idname = "ahb.delete_old_uv_maps"
    bl_label = "Delete Old UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        props = context.scene.ahb_props
        keep_name = props.uv_layer_name

        count = 0
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                mesh = obj.data
                keep_names = {keep_name}
                backup_name = mesh.get(
                    IMAGE_UV_BACKUP_PROP,
                    obj.get(IMAGE_UV_BACKUP_PROP, IMAGE_UV_BACKUP_LAYER))
                if backup_name in mesh.uv_layers:
                    keep_names.add(backup_name)
                to_remove = [uv.name for uv in mesh.uv_layers
                             if uv.name not in keep_names]
                for name in to_remove:
                    mesh.uv_layers.remove(mesh.uv_layers[name])
                    count += 1

        self.report({'INFO'}, f"Deleted {count} old UV maps")
        return {'FINISHED'}


class AHB_OT_ClearBakeNodes(Operator):
    """Remove all auto-added AHB Image Texture nodes from materials"""
    bl_idname  = "ahb.clear_bake_nodes"
    bl_label   = "Remove Bake Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        mat_names = set()
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            for slot in obj.material_slots:
                if slot.material:
                    mat_names.add(slot.material.name)

        remove_bake_nodes(mat_names, props.image_node_name)
        self.report({'INFO'}, f"Removed bake nodes from {len(mat_names)} material(s).")
        return {'FINISHED'}


class AHB_OT_SaveImages(Operator):
    """Save all baked images to the output directory"""
    bl_idname  = "ahb.save_images"
    bl_label   = "Save Baked Images"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.ahb_props
        normalize_core_names(props)
        saved = 0

        for img in bpy.data.images:
            if img.name.startswith(props.output_prefix) and img.has_data:
                try:
                    path = save_image(img, props, context)
                    saved += 1
                    self.report({'INFO'}, f"Saved: {path}")
                except Exception as e:
                    self.report({'WARNING'}, f"Save failed [{img.name}]: {e}")

        self.report({'INFO'}, f"Saved {saved} image(s).")
        return {'FINISHED'}


class AHB_OT_RenewAutoSeams(Operator):
    """Clear all existing seams and recalculate fresh auto seams based on Seam Angle"""
    bl_idname  = "ahb.renew_auto_seams"
    bl_label   = "Renew Auto Seams"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        count = clear_and_renew_auto_seams(obj_names, props)
        setup_atlas_uvs(obj_names, props)

        msg = f"Renewed auto seams & re-unwrapped {count} object(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AHB_OT_PreviewUV(Operator):
    """Preview the UV layout that will be used for baking"""
    bl_idname  = "ahb.preview_uv"
    bl_label   = "Preview Bake UVs"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        image_mode = props.image_mode

        if image_mode == 'ATLAS':
            setup_atlas_uvs(obj_names, props)
            self.report({'INFO'}, f"Atlas UV layout created for {len(obj_names)} object(s).")
        elif image_mode == 'PER_MATERIAL':
            for obj_name in obj_names:
                ensure_uv(obj_name, props)
            self.report({'INFO'}, f"UV layers created for {len(obj_names)} object(s).")

        return {'FINISHED'}


class AHB_OT_PackIslands(Operator):
    """Run optimized UV island packing on selected objects"""
    bl_idname  = "ahb.pack_islands"
    bl_label   = "Pack UV Islands"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        if props.image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
            atlas_pack_phase(obj_names, props)
        else:
            for obj_name in obj_names:
                pack_uv_islands_phased(obj_name, props)

        self.report({'INFO'}, f"Packed islands for {len(obj_names)} object(s).")
        return {'FINISHED'}


class AHB_OT_CleanupImages(Operator):
    """Remove all AHB-generated images from the blend file"""
    bl_idname  = "ahb.cleanup_images"
    bl_label   = "Remove Bake Images"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props   = context.scene.ahb_props
        normalize_core_names(props)
        prefix  = props.output_prefix
        removed = 0

        for img in list(bpy.data.images):
            if img.name.startswith(prefix):
                bpy.data.images.remove(img)
                removed += 1

        self.report({'INFO'}, f"Removed {removed} bake image(s) from blend data.")
        return {'FINISHED'}


class AHB_OT_RemoveUnusedMaterials(Operator):
    """Remove material slots that are assigned to an object but not used by any face"""
    bl_idname  = "ahb.remove_unused_materials"
    bl_label   = "Remove Unused Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        total_removed = 0
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH': continue

            used_indices = {poly.material_index for poly in obj.data.polygons}
            slots_to_remove = [idx for idx in range(len(obj.material_slots)) if idx not in used_indices]

            for idx in reversed(slots_to_remove):
                obj.active_material_index = idx
                bpy.ops.object.material_slot_remove()
                total_removed += 1

        self.report({'INFO'}, f"Removed {total_removed} unused material slot(s).")
        return {'FINISHED'}


class AHB_OT_ApplyBakedTextures(Operator):
    """Wire baked images into material shader graphs so they appear in viewport/render"""
    bl_idname  = "ahb.apply_baked"
    bl_label   = "Apply Baked Textures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        run_apply(obj_names, props, self.report)
        props.baked_view = True
        return {'FINISHED'}


class AHB_OT_ToggleBakedView(Operator):
    """Toggle scoped materials between their original and baked shaders."""
    bl_idname = "ahb.toggle_baked_view"
    bl_label = "Toggle Baked / Original"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        if scope_has_material_backups(obj_names):
            restored = restore_material_backups(obj_names)
            props.baked_view = False
            self.report({'INFO'}, f"Showing original materials ({restored} restored).")
        else:
            run_apply(obj_names, props, self.report)
            props.baked_view = True
        return {'FINISHED'}


class AHB_OT_CreateAtlasCollections(Operator):
    """Create missing collection atlas containers in Scene Collection"""
    bl_idname = "ahb.create_atlas_collections"
    bl_label = "Create Atlas Collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        normalize_core_names(props)
        created = 0
        for atlas_number in range(1, props.collection_atlas_count + 1):
            col_name = get_collection_atlas_name(props, atlas_number)
            col = bpy.data.collections.get(col_name)
            if not col:
                col = bpy.data.collections.new(col_name)
                context.scene.collection.children.link(col)
                created += 1
        self.report({'INFO'}, f"Created {created} atlas collection container(s).")
        return {'FINISHED'}


class AHB_OT_GroupToCollections(Operator):
    """Move objects into tile-based collections (e.g. Tile_01, Tile_02)"""
    bl_idname  = "ahb.group_to_collections"
    bl_label   = "Group to Tile Collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        moved = group_objects_by_tile(obj_names, props, self.report)
        return {'FINISHED'} if moved else {'CANCELLED'}


class AHB_OT_RenameObjects(Operator):
    """Rename objects by appending their baked texture name"""
    bl_idname  = "ahb.rename_objects"
    bl_label   = "Rename Objects by Texture"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        renamed = rename_objects_by_texture(obj_names, props, self.report)
        return {'FINISHED'} if renamed else {'CANCELLED'}


class AHB_OT_RestoreOriginalMaterials(Operator):
    """Restore materials to their pre-bake state from backups"""
    bl_idname  = "ahb.restore_materials"
    bl_label   = "Restore Original Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props     = context.scene.ahb_props
        obj_names = get_object_names(props, context)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        restored = restore_material_backups(obj_names)
        props.baked_view = False
        self.report({'INFO'}, f"Restored {restored} material(s) to original.")
        return {'FINISHED'}
