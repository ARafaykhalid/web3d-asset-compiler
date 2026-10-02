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
    selection_snapshot,
    status_reporter,
    restore_material_backups,
    normalize_core_names,
    prepare_mesh_data,
    prepare_material_slots,
    preserve_implicit_texture_uvs,
    setup_atlas_uvs,
    owned_bake_images,
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
    distribute_tiles,
    get_atlas_groups,
    ensure_uv,
    atlas_pack_phase,
    pack_uv_islands_phased,
    force_object_mode,
    force_ui_redraw,
    uses_image_bake_target,
    bake_type_needs_uv,
    ensure_bake_color_attribute,
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

        restore = selection_snapshot(context)

        def after_bake(baked, errors):
            report = status_reporter(props, context.scene)
            restore()
            if baked and not errors:
                report({'INFO'}, f"Baked {baked} object(s).")
            else:
                props.status_text = f"Bake finished with {errors} error(s)"
                report({'ERROR'}, props.status_text)

        try:
            run_bake(obj_names, props, context, status_reporter(props, context.scene),
                     source_names, on_done=after_bake)
        except Exception as e:
            restore()
            self.report({'ERROR'}, f"Bake failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


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
        cage_object = (getattr(props, 'cage_object', None)
                       if getattr(props, 'use_cage', False) else None)
        source_names = [o.name for o in context.selected_objects
                        if (o.type == 'MESH' and o != obj and o != cage_object
                            and o.data.polygons)]

        def work():
            restore_material_backups(obj_names, props)
            prepare_mesh_data(
                obj_names,
                make_unique=(props.auto_create_uv
                             or not uses_image_bake_target(props)))
            prepare_material_slots(obj_names)
            preserve_implicit_texture_uvs(obj_names, props)

            if (bake_type_needs_uv(props)
                    and props.uv_layer_name not in obj.data.uv_layers):
                if props.image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
                    setup_atlas_uvs(obj_names, props)
                else:
                    ensure_uv(obj.name, props)

            target_images = {}
            if uses_image_bake_target(props):
                had_recorded_assignment = bool(obj.get("ahb_baked_texture"))
                for slot in obj.material_slots:
                    mat = slot.material
                    if not mat:
                        continue
                    image = get_object_bake_image(
                        obj, props,
                        mat if props.image_mode == 'PER_MATERIAL' else None)
                    if image:
                        target_images[mat.name] = image
                        inject_bake_node(mat.name, image, props.image_node_name)

                if not target_images:
                    raise RuntimeError(
                        "The object has no existing bake image/texture-pack assignment")
                if (props.image_mode in ('AUTO_TILES', 'COLLECTION_ATLASES')
                        and not had_recorded_assignment):
                    raise RuntimeError(
                        "This object has no recorded texture-pack assignment; "
                        "run a full bake first")
            elif not ensure_bake_color_attribute(obj):
                raise RuntimeError("The object cannot store a bake color attribute")

            if validate_bake_ready(obj_names, props, self.report) is not None:
                return False

            configure_bake_settings(props, context)
            context.scene.render.bake.use_clear = False
            props.status_text = f"Rebaking {obj.name} in its existing texture pack…"
            force_ui_redraw()

            def finish_bake():
                if (uses_image_bake_target(props) and props.auto_save
                        and getattr(props, 'save_mode', 'EXTERNAL')
                        == 'EXTERNAL'):
                    for image in target_images.values():
                        if image.has_data:
                            save_image(image, props, context)

                if uses_image_bake_target(props) and props.auto_cleanup_nodes:
                    remove_bake_nodes(set(target_images), props.image_node_name)

                applied = apply_single_object_bake(
                    obj, props, resolve_apply_mode(props),
                    status_reporter(props, context.scene))
                if applied == 0:
                    raise RuntimeError(
                        "Rebake finished, but no baked material could be applied")

                props.baked_view = True
                props.status_text = (
                    f"Rebaked {obj.name} in its existing texture pack.")
                return True

            # Same job hand-off as the full pipeline: the rebake must not block
            # the UI, and ESC must abort it.
            run_bake(
                obj_names, props, context, status_reporter(props, context.scene),
                source_names, on_done=lambda _b, _e: _finish_rebake(finish_bake),
            )
            return True

        def _finish_rebake(finish_bake):
            report = status_reporter(props, context.scene)
            try:
                finish_bake()
                restore()
            except Exception as e:
                print(f"[AHB] Selected rebake error:\n{traceback.format_exc()}")
                props.status_text = f"Selected rebake failed: {e}"
                report({'ERROR'}, f"Selected rebake failed: {e}")
                restore()

        restore = selection_snapshot(context)
        try:
            ok = work()
        except Exception as e:
            restore()
            print(f"[AHB] Selected rebake error:\n{traceback.format_exc()}")
            props.status_text = f"Selected rebake failed: {e}"
            self.report({'ERROR'}, f"Selected rebake failed: {e}")
            return {'CANCELLED'}
        restore()
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

        restore = selection_snapshot(context)

        def fail(message):
            props.status_text = message
            status_reporter(props, context.scene)({'ERROR'}, message)
            restore()
            return {'CANCELLED'}

        def apply_results():
            report = status_reporter(props, context.scene)
            props.status_text = "Applying baked textures…"
            applied, skipped = run_apply(obj_names, props, report)
            if applied == 0 or skipped:
                return fail(
                    f"Material application incomplete — {applied} applied, "
                    f"{skipped} skipped"
                )
            if props.auto_rename_objects:
                rename_objects_by_texture(obj_names, props, report)
            props.baked_view = True
            props.status_text = "Pipeline complete ✓"
            status_reporter(props, context.scene)({'INFO'}, props.status_text)
            restore()
            return {'FINISHED'}

        # Runs from a timer after execute() returns, when the operator's RNA
        # struct no longer exists -- it must not touch `self`.
        def after_bake(baked, errs):
            report = status_reporter(props, context.scene)
            if props.bake_cancelled:
                # The user pressed ESC; that is not a failure.
                fail("Bake cancelled.")
            elif baked == 0:
                fail("Bake failed — 0 objects baked!")
            elif errs:
                fail(f"Bake incomplete — {baked} object(s), {errs} error(s)")
            else:
                try:
                    apply_results()
                except Exception as e:
                    props.status_text = f"Pipeline failed: {e}"
                    report({'ERROR'}, f"Pipeline failed: {e}")
                    restore()

        def after_setup(ok):
            if not ok:
                cancelled = props.bake_cancelled
                message = "Setup cancelled." if cancelled else "Setup failed!"
                props.status_text = message
                status_reporter(props, context.scene)(
                    {'WARNING' if cancelled else 'ERROR'}, message)
                restore()
                return
            # Hands off to Blender's job system: the interface stays live and
            # ESC (or the Cancel button) aborts the bake.
            try:
                run_bake(
                    obj_names, props, context,
                    status_reporter(props, context.scene),
                    source_names, on_done=after_bake,
                )
            except Exception as e:
                props.status_text = f"Pipeline failed: {e}"
                status_reporter(props, context.scene)(
                    {'ERROR'}, f"Pipeline failed: {e}")
                restore()

        try:
            restored = restore_material_backups(obj_names, props)
            if restored:
                self.report(
                    {'INFO'},
                    f"Restored {restored} original material(s) before rebaking.")
            # UV setup yields between steps as well, so it is deferred too and
            # hands off to the bake from `after_setup`.
            run_setup(
                obj_names, props, status_reporter(props, context.scene),
                on_done=after_setup,
            )
        except Exception as e:
            restore()
            props.status_text = f"Pipeline failed: {e}"
            self.report({'ERROR'}, f"Pipeline failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


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
        if not uses_image_bake_target(props):
            self.report(
                {'INFO'},
                "Color attribute bakes are stored on the mesh and saved with the blend file.")
            return {'FINISHED'}
        saved = 0
        failed = 0

        for img in list(owned_bake_images(props)):
            if not img.has_data:
                continue
            try:
                path = save_image(img, props, context)
                saved += 1
                self.report({'INFO'}, f"Saved: {path}")
            except Exception as e:
                failed += 1
                self.report({'WARNING'}, f"Save failed [{img.name}]: {e}")

        self.report({'INFO'}, f"Saved {saved} image(s); failed: {failed}.")
        return {'FINISHED'} if saved and not failed else {'CANCELLED'}


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

        count = clear_and_renew_auto_seams(obj_names, props, self.report)
        setup_atlas_uvs(obj_names, props)

        force_object_mode()
        bpy.ops.object.select_all(action='DESELECT')
        first_obj = None
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if obj and obj.type == 'MESH':
                obj.select_set(True)
                if first_obj is None:
                    first_obj = obj
        if first_obj:
            context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
            except Exception:
                pass

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

        force_object_mode()
        image_mode = props.image_mode
        layer_name = props.uv_layer_name

        if image_mode == 'ATLAS':
            try:
                remove_tile_uv_offsets(obj_names, layer_name)
                setup_atlas_uvs(obj_names, props)
                self.report(
                    {'INFO'},
                    f"Atlas UV layout created for {len(obj_names)} object(s).")
            except Exception as e:
                self.report({'WARNING'}, f"Atlas UV preview failed: {e}")
                print(f"[AHB] Atlas UV preview error:\n{traceback.format_exc()}")
                force_object_mode()

        elif image_mode == 'AUTO_TILES':
            remove_tile_uv_offsets(obj_names, layer_name)
            tiles = distribute_tiles(obj_names, props.tile_count)
            succeeded = 0
            tile_info = []
            for tile_idx, tile_obj_names in enumerate(tiles):
                try:
                    # No horizontal offset: every tile gets its own image
                    # (bake_tile_01, bake_tile_02, ...), so shifting by
                    # tile_idx would push UVs outside the image they bake into
                    # and the preview would not match the bake.
                    setup_atlas_uvs(tile_obj_names, props)
                    succeeded += 1
                    tile_info.append(
                        f"Tile {tile_idx + 1}: {len(tile_obj_names)} obj(s)")
                except Exception as e:
                    self.report(
                        {'WARNING'},
                        f"Tile {tile_idx + 1} UV preview failed: {e}")
                    print(
                        f"[AHB] Tile {tile_idx + 1} UV preview error:\n"
                        f"{traceback.format_exc()}")
                    force_object_mode()
            info = " | ".join(tile_info)
            self.report(
                {'INFO'},
                f"UV preview: {succeeded} tile(s) side-by-side. {info}")

        elif image_mode == 'COLLECTION_ATLASES':
            remove_tile_uv_offsets(obj_names, layer_name)
            groups = get_atlas_groups(obj_names, props, self.report)
            succeeded = 0
            atlas_info = []
            for atlas_number, atlas_obj_names in groups:
                if not atlas_obj_names:
                    continue
                try:
                    # Each atlas has its own image, so no offset here either.
                    setup_atlas_uvs(atlas_obj_names, props)
                    succeeded += 1
                    atlas_name = get_collection_atlas_name(props, atlas_number)
                    atlas_info.append(
                        f"{atlas_name}: {len(atlas_obj_names)} obj(s)")
                except Exception as e:
                    self.report(
                        {'WARNING'}, f"Atlas {atlas_number} UV preview failed: {e}")
                    print(
                        f"[AHB] Collection atlas {atlas_number} preview error:\n"
                        f"{traceback.format_exc()}")
                    force_object_mode()
            info = " | ".join(atlas_info)
            self.report(
                {'INFO'},
                f"Collection atlas preview: {succeeded} group(s). {info}")

        elif image_mode == 'PER_MATERIAL':
            succeeded = 0
            if props.auto_create_uv:
                for obj_name in obj_names:
                    try:
                        ensure_uv(obj_name, props)
                        succeeded += 1
                    except Exception as e:
                        self.report({'WARNING'}, f"UV setup failed [{obj_name}]: {e}")
                        force_object_mode()
            self.report(
                {'INFO'},
                f"UV layers created/updated for {succeeded}/{len(obj_names)} object(s).")

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
            if layer_name in obj.data.uv_layers:
                obj.data.uv_layers.active = obj.data.uv_layers[layer_name]

        if first_obj:
            context.view_layer.objects.active = first_obj
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
            except Exception:
                pass

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

        force_object_mode()
        image_mode = props.image_mode
        layer_name = props.uv_layer_name

        if image_mode in ('ATLAS', 'AUTO_TILES', 'COLLECTION_ATLASES'):
            valid = []
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj or obj.type != 'MESH' or not obj.data.polygons:
                    continue
                if layer_name not in obj.data.uv_layers:
                    self.report(
                        {'WARNING'}, f"{obj_name}: No UV layer '{layer_name}'.")
                    continue
                valid.append(obj_name)
            if not valid:
                self.report({'WARNING'}, "No objects with valid UV layers.")
                return {'CANCELLED'}

            if image_mode == 'ATLAS':
                remove_tile_uv_offsets(valid, layer_name)
                try:
                    atlas_pack_phase(valid, props)
                except Exception as e:
                    self.report({'WARNING'}, f"Atlas pack failed: {e}")
                    print(f"[AHB] Atlas pack error:\n{traceback.format_exc()}")
                    force_object_mode()
            elif image_mode == 'AUTO_TILES':
                remove_tile_uv_offsets(valid, layer_name)
                for tile_idx, tile_objs in enumerate(
                        distribute_tiles(valid, props.tile_count)):
                    try:
                        atlas_pack_phase(tile_objs, props)
                    except Exception as e:
                        self.report(
                            {'WARNING'}, f"Tile {tile_idx + 1} pack failed: {e}")
                        force_object_mode()
            else:
                remove_tile_uv_offsets(valid, layer_name)
                for atlas_number, atlas_objs in get_atlas_groups(
                        valid, props, self.report):
                    if not atlas_objs:
                        continue
                    try:
                        atlas_pack_phase(atlas_objs, props)
                    except Exception as e:
                        self.report(
                            {'WARNING'}, f"Atlas {atlas_number} pack failed: {e}")
                        force_object_mode()

            msg = (
                f"Packed islands across {len(valid)} object(s) "
                f"({props.pack_margin_px}px margin, "
                f"{props.pack_iterations} iterations)")
        else:
            packed = 0
            for obj_name in obj_names:
                obj = bpy.data.objects.get(obj_name)
                if not obj or obj.type != 'MESH' or not obj.data.polygons:
                    continue
                if layer_name not in obj.data.uv_layers:
                    self.report(
                        {'WARNING'}, f"{obj_name}: No UV layer '{layer_name}'.")
                    continue
                try:
                    pack_uv_islands_phased(obj_name, props)
                    packed += 1
                except Exception as e:
                    self.report({'WARNING'}, f"Pack failed [{obj_name}]: {e}")
                    print(
                        f"[AHB] Pack error for {obj_name}:\n"
                        f"{traceback.format_exc()}")
                    force_object_mode()
            msg = (
                f"Packed islands on {packed} object(s) "
                f"({props.pack_margin_px}px margin, "
                f"{props.pack_iterations} iterations)")

        props.status_text = msg
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class AHB_OT_CleanupImages(Operator):
    """Remove all AHB-generated images from the blend file"""
    bl_idname  = "ahb.cleanup_images"
    bl_label   = "Remove Bake Images"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ahb_props
        normalize_core_names(props)
        removed = 0

        for img in list(owned_bake_images(props)):
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
        obj_names = get_object_names(props, context, include_empty=True)
        if not obj_names:
            self.report({'WARNING'}, "No valid mesh objects in scope.")
            return {'CANCELLED'}

        force_object_mode()
        total_removed = 0
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj or obj.type != 'MESH':
                continue

            mesh = obj.data
            context.view_layer.objects.active = obj
            if not mesh.polygons:
                while obj.material_slots:
                    obj.active_material_index = 0
                    bpy.ops.object.material_slot_remove()
                    total_removed += 1
                continue

            used_indices = {poly.material_index for poly in mesh.polygons}
            slots_to_remove = [
                idx for idx in range(len(obj.material_slots))
                if idx not in used_indices
            ]

            for idx in reversed(slots_to_remove):
                obj.active_material_index = idx
                bpy.ops.object.material_slot_remove()
                total_removed += 1

        msg = f"Removed {total_removed} unused material slot(s)."
        props.status_text = msg
        self.report({'INFO'}, msg)
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

        applied, skipped = run_apply(obj_names, props, self.report)
        props.baked_view = applied > 0
        return {'FINISHED'} if applied else {'CANCELLED'}


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
            restored = restore_material_backups(obj_names, props)
            props.baked_view = False
            props.status_text = f"Showing original materials ({restored} restored)."
            self.report({'INFO'}, props.status_text)
        else:
            applied, skipped = run_apply(obj_names, props, self.report)
            props.baked_view = applied > 0
            if applied == 0 and skipped:
                return {'CANCELLED'}
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

        self.report(
            {'WARNING'},
            "Objects move into the tile collections and leave their "
            "current collections (Ctrl+Z undoes this).")
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

        restored = restore_material_backups(obj_names, props)
        props.baked_view = False
        self.report({'INFO'}, f"Restored {restored} material(s) to original.")
        return {'FINISHED'}
