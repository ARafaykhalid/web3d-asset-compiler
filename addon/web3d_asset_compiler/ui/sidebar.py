"""
Compact 3D Viewport sidebar for the Web3D Asset Compiler.

The overview keeps the common workflow visible while Blender-native child
panels provide progressive disclosure for the complete set of working tools.
"""

from textwrap import wrap

from bpy.types import Panel

from ..baking.properties import PASS_FILTER_TYPES
from ..exporter.binary_exporter import animation_export_readiness


_READY_TEXT = "Ready"


def _has_scene_props(context):
    scene = getattr(context, "scene", None)
    return bool(
        scene
        and hasattr(scene, "ahb_props")
        and hasattr(scene, "tjs_props")
    )


def _is_mesh(obj):
    return bool(
        obj
        and obj.type == "MESH"
        and getattr(obj, "data", None)
        and getattr(obj.data, "polygons", None)
    )


def _is_visible(obj, context):
    if getattr(obj, "hide_viewport", False):
        return False
    try:
        return not obj.hide_get(view_layer=context.view_layer)
    except (AttributeError, TypeError, RuntimeError):
        try:
            return not obj.hide_get()
        except (AttributeError, RuntimeError):
            return True


def _scope_meshes(context, props):
    scope = getattr(props, "bake_scope", "SELECTED")
    if scope == "ACTIVE":
        active = context.view_layer.objects.active
        return [active] if _is_mesh(active) else []
    if scope == "VISIBLE":
        return [
            obj for obj in context.scene.objects
            if _is_mesh(obj) and _is_visible(obj, context)
        ]
    return [obj for obj in context.selected_objects if _is_mesh(obj)]


def _uses_armature(obj, armature):
    if not _is_mesh(obj):
        return False
    if obj.parent == armature and obj.parent_type in {"ARMATURE", "BONE"}:
        return True
    return any(
        modifier.type == "ARMATURE" and modifier.object == armature
        for modifier in obj.modifiers
    )


def _bake_readiness(context, props):
    if (
        getattr(props, "bake_target", "IMAGE_TEXTURES") == "IMAGE_TEXTURES"
        and getattr(props, "save_mode", "EXTERNAL") == "EXTERNAL"
        and getattr(props, "auto_save", True)
    ):
        output_dir = str(getattr(props, "output_dir", "")).strip()
        blend_path = getattr(getattr(context, "blend_data", None), "filepath", "")
        if output_dir.startswith("//") and not blend_path:
            return False, "Bake: save the blend file or choose an absolute output path"

    if (
        getattr(props, "bake_type", "COMBINED") in PASS_FILTER_TYPES
        and not (
            getattr(props, "use_pass_direct", False)
            or getattr(props, "use_pass_indirect", False)
            or getattr(props, "use_pass_color", False)
        )
    ):
        return False, "Bake: enable Direct, Indirect, or Color pass contribution"

    if getattr(props, "multires_bake", False):
        if getattr(props, "bake_type", "COMBINED") != "NORMAL":
            return False, "Bake: Multires requires a Normal bake"
        if getattr(props, "bake_target", "IMAGE_TEXTURES") != "IMAGE_TEXTURES":
            return False, "Bake: Multires requires image output"
        if getattr(props, "use_selected_to_active", False):
            return False, "Bake: Multires cannot use Selected to Active"
        meshes = _scope_meshes(context, props)
        if any(
            not any(modifier.type == "MULTIRES" for modifier in obj.modifiers)
            for obj in meshes
        ):
            return False, "Bake: each target needs a Multires modifier"

    if getattr(props, "use_selected_to_active", False):
        active = context.view_layer.objects.active
        cage = (
            getattr(props, "cage_object", None)
            if getattr(props, "use_cage", False)
            else None
        )
        sources = [
            obj for obj in context.selected_objects
            if _is_mesh(obj) and obj != active and obj != cage
        ]
        if not _is_mesh(active):
            return False, "Bake: active mesh target required"
        if cage is not None and cage is active:
            return False, "Bake: custom cage must be separate from the target"
        if not sources:
            return False, "Bake: select a source mesh too"
        if getattr(props, "use_cage", False) and not _is_mesh(cage):
            return False, "Bake: choose a custom cage mesh"
        return True, f"Bake: 1 target, {len(sources)} source(s)"

    meshes = _scope_meshes(context, props)
    if not meshes:
        return False, "Bake: no mesh in the chosen scope"
    return True, f"Bake: {len(meshes)} mesh object(s)"


def _export_readiness(context, props, *, animations=True):
    portfolio_mode = getattr(props, "portfolio_one_click", False)
    export_format = getattr(props, "export_format", "GLB")
    if animations and not portfolio_mode and export_format != "GLB":
        return False, "Export: animations require GLB format"
    if animations and not getattr(props, "export_skins", True):
        return False, "Export: animations require skins enabled"
    if (
        animations
        and getattr(props, "animation_mode", "ACTIONS") == "NLA_TRACKS"
        and not getattr(props, "export_nla_strips", True)
    ):
        return False, "Export: enable NLA strips for NLA mode"

    if animations:
        ready, detail = animation_export_readiness(
            context,
            props,
            force_all_actions=portfolio_mode,
            force_character_scope=portfolio_mode,
        )
        return ready, f"Export: {detail}"

    broad_scope = bool(
        portfolio_mode or getattr(props, "export_scope", "SELECTED") == "SCENE"
    )
    if broad_scope:
        scoped_objects = list(context.scene.objects)
        count = len(scoped_objects)
        if not count:
            return False, "Export: scene is empty"
        return True, f"Export: {count} scene object(s)"

    scoped_objects = list(context.selected_objects)
    count = len(scoped_objects)
    if not count:
        return False, "Export: select at least one object"
    return True, f"Export: {count} selected object(s)"


def _build_export_readiness(context, props):
    animation_ready, animation_text = _export_readiness(
        context,
        props,
        animations=True,
    )
    if animation_ready:
        return True, animation_text, True
    if getattr(props, "portfolio_one_click", False):
        return False, animation_text, False

    model_ready, model_text = _export_readiness(
        context,
        props,
        animations=False,
    )
    if model_ready:
        return True, f"{model_text} (model only)", False
    return False, model_text, False


def _status_icon(text):
    lowered = str(text).casefold()
    if any(
        word in lowered
        for word in (
            "fail",
            "error",
            "cancel",
            "stopped",
            "cannot",
            "missing",
            "requires",
            "invalid",
            "incomplete",
        )
    ):
        return "ERROR"
    if any(
        word in lowered
        for word in ("complete", "done", "exported", "applied", "success")
    ):
        return "CHECKMARK"
    return "INFO"


def _status_lines(text, width=46, limit=2):
    normalized = " ".join(str(text or _READY_TEXT).split())
    lines = wrap(
        normalized,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [_READY_TEXT]
    if len(lines) > limit:
        lines = lines[:limit]
        lines[-1] = lines[-1].rstrip(".") + "..."
    return lines


def _draw_status(layout, label, text):
    icon = _status_icon(text)
    for index, line in enumerate(_status_lines(text)):
        row = layout.row(align=True)
        row.alert = icon == "ERROR"
        prefix = f"{label}: " if index == 0 else ""
        row.label(
            text=prefix + line,
            icon=icon if index == 0 else "NONE",
        )


def _draw_ready_state(layout, ready, text):
    for index, line in enumerate(_status_lines(text, width=42)):
        row = layout.row(align=True)
        row.alert = not ready
        row.label(
            text=line,
            icon=("CHECKMARK" if ready else "ERROR") if index == 0 else "NONE",
        )


def _draw_current_status(layout, context):
    scene = context.scene
    ahb = scene.ahb_props
    tjs = scene.tjs_props
    compiler_status = getattr(scene, "web3d_status", _READY_TEXT)
    bake_status = getattr(ahb, "status_text", _READY_TEXT)
    export_status = getattr(tjs, "status", _READY_TEXT)

    if str(compiler_status or _READY_TEXT).strip() != _READY_TEXT:
        _draw_status(layout, "Build", compiler_status)

    active = [
        ("Bake", bake_status),
        ("Export", export_status),
    ]
    active = [
        (label, text) for label, text in active
        if str(text or _READY_TEXT).strip() != _READY_TEXT
    ]
    if not active and str(compiler_status or _READY_TEXT).strip() == _READY_TEXT:
        _draw_status(layout, "Compiler", _READY_TEXT)
        return
    for label, text in active:
        _draw_status(layout, label, text)


def _draw_readiness(layout, context):
    bake_ready, bake_text = _bake_readiness(
        context, context.scene.ahb_props,
    )
    export_ready, export_text, animated = _build_export_readiness(
        context, context.scene.tjs_props,
    )

    for ready, text in ((bake_ready, bake_text), (export_ready, export_text)):
        _draw_ready_state(layout, ready, text)
    return bake_ready, export_ready, animated


def _draw_preset_menu(layout):
    layout.operator_menu_enum(
        "web3d.apply_preset",
        "preset_key",
        text="Choose Optimization Preset",
    )


def draw_compile_overview(layout, context, compact=False):
    """Draw status, readiness, presets, and the primary workflow actions."""
    if not _has_scene_props(context):
        row = layout.row()
        row.alert = True
        row.label(text="Compiler properties are not registered", icon="ERROR")
        return

    layout.use_property_split = True
    layout.use_property_decorate = False

    status_box = layout.box()
    _draw_current_status(status_box, context)

    # The runners yield between steps, so the window is live while a build
    # runs and this button is actually clickable.
    if getattr(context.scene.ahb_props, "web3d_cancel_pending", False):
        status_box.separator()
        status_box.operator(
            "web3d.cancel_build", text="Cancel Build", icon="CANCEL")

    if not compact:
        readiness = layout.column(align=True)
        bake_ready, export_ready, animated = _draw_readiness(readiness, context)
    else:
        bake_ready, bake_text = _bake_readiness(
            context, context.scene.ahb_props,
        )
        export_ready, export_text, animated = _build_export_readiness(
            context, context.scene.tjs_props,
        )
        if not bake_ready or not export_ready:
            issue = bake_text if not bake_ready else export_text
            _draw_ready_state(layout, False, issue)
    pipeline_ready = bake_ready and export_ready

    _draw_preset_menu(layout)

    build = layout.row()
    build.enabled = pipeline_ready
    build.scale_y = 1.45 if not compact else 1.2
    build.operator(
        "web3d.build_web_asset",
        text="Build Web Asset",
        icon="PACKAGE",
    )

    quick = layout.row(align=True)
    bake_only = quick.row(align=True)
    bake_only.enabled = bake_ready
    bake_only.operator("ahb.quick_bake", text="Bake Only", icon="RENDER_STILL")
    export_only = quick.row(align=True)
    export_only.enabled = export_ready
    export_only.operator(
        "tjs.export_animations" if animated else "tjs.export_character_glb",
        text="Export Animated" if animated else "Export Model",
        icon="EXPORT",
    )


def draw_bake_settings(layout, context):
    ahb = context.scene.ahb_props
    layout.use_property_split = True
    layout.use_property_decorate = False

    bake_ready, bake_text = _bake_readiness(context, ahb)
    _draw_ready_state(layout, bake_ready, bake_text)

    row = layout.row(align=True)
    quick_bake = row.row(align=True)
    quick_bake.enabled = bake_ready
    quick_bake.operator("ahb.quick_bake", text="Quick Bake", icon="RENDER_STILL")
    rebake = row.row(align=True)
    rebake.enabled = _is_mesh(context.view_layer.objects.active)
    rebake.operator("ahb.rebake_selected", text="Rebake Active")

    layout.separator()
    layout.label(text="Manual Pipeline")
    steps = layout.grid_flow(
        row_major=True,
        columns=3,
        even_columns=True,
        even_rows=True,
        align=True,
    )
    steps.enabled = bake_ready
    steps.operator("ahb.setup_materials", text="1 Setup")
    steps.operator("ahb.bake_all", text="2 Bake")
    steps.operator("ahb.save_images", text="3 Save")

    layout.separator()
    layout.label(text="Target and Pass", icon="OBJECT_DATA")
    layout.prop(ahb, "bake_scope", text="Scope")
    layout.prop(ahb, "bake_type", text="Bake Type")
    if ahb.bake_type in PASS_FILTER_TYPES:
        filters = layout.row(align=True)
        filters.prop(ahb, "use_pass_direct", toggle=True)
        filters.prop(ahb, "use_pass_indirect", toggle=True)
        filters.prop(ahb, "use_pass_color", toggle=True)

    if ahb.bake_type == "NORMAL":
        layout.prop(ahb, "normal_space")
        swizzle = layout.row(align=True)
        swizzle.prop(ahb, "normal_r", text="X")
        swizzle.prop(ahb, "normal_g", text="Y")
        swizzle.prop(ahb, "normal_b", text="Z")

    selected_to_active = layout.column(align=True)
    selected_to_active.enabled = not ahb.multires_bake
    selected_to_active.prop(ahb, "use_selected_to_active")
    if ahb.use_selected_to_active and not ahb.multires_bake:
        ray = layout.row(align=True)
        ray.prop(ahb, "cage_extrusion")
        ray.prop(ahb, "max_ray_distance")
        layout.prop(ahb, "use_cage")
        if ahb.use_cage:
            layout.prop(ahb, "cage_object")

    layout.separator()
    layout.label(text="Bake Output", icon="IMAGE_DATA")
    layout.prop(ahb, "bake_target")
    if (
        ahb.multires_bake
        or (ahb.bake_target == "IMAGE_TEXTURES" and ahb.bake_type == "NORMAL")
    ):
        layout.prop(ahb, "multires_bake")

    images = layout.column(align=True)
    images.enabled = ahb.bake_target == "IMAGE_TEXTURES"
    images.prop(ahb, "image_mode", text="Image Layout")
    if ahb.image_mode == "AUTO_TILES":
        images.prop(ahb, "tile_count")
        images.operator(
            "ahb.group_to_collections",
            text="Group Objects into Tile Collections",
        )
    elif ahb.image_mode == "COLLECTION_ATLASES":
        images.prop(ahb, "collection_atlas_prefix", text="Collection Prefix")
        images.prop(ahb, "collection_atlas_count", text="Atlas Count")
        images.operator(
            "ahb.create_atlas_collections",
            text="Create Collections",
        )

    images.prop(ahb, "resolution")
    if ahb.resolution == "CUSTOM":
        resolution = images.row(align=True)
        resolution.prop(ahb, "custom_res_x", text="Width")
        resolution.prop(ahb, "custom_res_y", text="Height")
    images.prop(ahb, "image_format")
    if ahb.image_format in {"OPEN_EXR", "OPEN_EXR_MULTILAYER"}:
        images.prop(ahb, "exr_codec")
    images.prop(ahb, "color_mode", text="Channels")
    images.prop(ahb, "use_hdr_float")
    images.prop(ahb, "save_mode")
    auto_save = images.column()
    auto_save.enabled = ahb.save_mode == "EXTERNAL"
    auto_save.prop(ahb, "auto_save")

    files = images.column(align=True)
    files.enabled = ahb.auto_save and ahb.save_mode == "EXTERNAL"
    files.prop(ahb, "output_dir")
    files.prop(ahb, "output_prefix")

    layout.separator()
    layout.label(text="Quality and Compute", icon="RENDER_RESULT")
    quality = layout.row(align=True)
    quality.prop(ahb, "samples")
    quality.prop(ahb, "margin")
    layout.prop(ahb, "margin_type")
    layout.prop(ahb, "clear_bake")
    layout.prop(ahb, "unlink_materials")
    layout.prop(ahb, "auto_switch_cycles")
    layout.prop(ahb, "compute_device", expand=True)
    if ahb.compute_device == "GPU":
        layout.prop(ahb, "gpu_backend")
    _draw_status(layout, "Device", ahb.device_status)


def draw_uv_settings(layout, context):
    ahb = context.scene.ahb_props
    layout.use_property_split = True
    layout.use_property_decorate = False

    layout.label(text="UV Generation", icon="UV")
    layout.prop(ahb, "auto_create_uv")
    if ahb.auto_create_uv:
        layout.prop(ahb, "uv_layer_name")
        layout.prop(ahb, "remove_old_uvs")
        layout.prop(ahb, "uv_mode")

        if ahb.uv_mode == "SMART":
            smart = layout.row(align=True)
            smart.prop(ahb, "smart_uv_angle")
            smart.prop(ahb, "smart_uv_island_margin", text="Island Margin")
        elif ahb.uv_mode == "AUTO_SEAM":
            layout.prop(ahb, "auto_seam_angle")
            layout.prop(ahb, "renew_auto_seams_on_preview")
        elif ahb.uv_mode == "LIGHTMAP":
            lightmap = layout.row(align=True)
            lightmap.prop(ahb, "lightmap_quality")
            lightmap.prop(ahb, "lightmap_margin")
        elif ahb.uv_mode == "CUBE":
            layout.prop(ahb, "cube_size")

    actions = layout.row(align=True)
    actions.operator("ahb.preview_uv", text="Preview UVs", icon="UV")
    actions.operator("ahb.renew_auto_seams", text="Renew Seams")

    destructive = layout.row(align=True)
    destructive.alert = True
    destructive.operator("ahb.delete_old_uv_maps", text="Delete Old UVs")
    destructive.operator("ahb.remove_all_uv_maps", text="Remove All UVs")

    layout.separator()
    layout.label(text="Island Packing", icon="PACKAGE")
    layout.prop(ahb, "pack_enabled")
    packing = layout.column(align=True)
    packing.enabled = ahb.pack_enabled
    packing.prop(ahb, "pack_world_scale")
    packing.prop(ahb, "pack_image_boost", text="Texture Density Boost")

    packing.prop(ahb, "pack_margin_px", text="Gutter (px)")
    blender_packing = packing.column(align=True)
    blender_packing.prop(ahb, "pack_iterations", text="Iterations")
    blender_packing.prop(ahb, "pack_rotate")
    if ahb.pack_rotate:
        blender_packing.prop(ahb, "pack_rotation_step")
    blender_packing.prop(ahb, "pack_shape_method")
    blender_packing.prop(ahb, "pack_nest_holes")
    blender_packing.prop(ahb, "pack_stack_identical")
    packing.prop(ahb, "pack_scale_islands")

    repack = layout.row()
    repack.enabled = ahb.pack_enabled
    repack.operator("ahb.pack_islands", text="Repack Islands", icon="PACKAGE")


def draw_export_settings(layout, context):
    tjs = context.scene.tjs_props
    layout.use_property_split = True
    layout.use_property_decorate = False

    _draw_status(layout, "Exporter", tjs.status)
    model_ready, model_text = _export_readiness(
        context, tjs, animations=False,
    )
    animation_ready, animation_text = _export_readiness(
        context, tjs, animations=True,
    )
    _draw_ready_state(layout, model_ready, model_text)
    if model_ready and not animation_ready:
        _draw_ready_state(layout, False, animation_text)

    actions = layout.row(align=True)
    animation_action = actions.row(align=True)
    animation_action.enabled = animation_ready
    animation_action.operator(
        "tjs.export_animations",
        text="Character + Animations",
        icon="EXPORT",
    )
    model_action = actions.row(align=True)
    model_action.enabled = model_ready
    model_action.operator(
        "tjs.export_character_glb",
        text="Model Only",
        icon="MESH_DATA",
    )

    layout.separator()
    layout.label(text="Destination", icon="FILE_FOLDER")
    layout.prop(tjs, "portfolio_one_click")
    if not tjs.portfolio_one_click:
        layout.prop(tjs, "export_scope")
        layout.prop(tjs, "output_dir")
        layout.prop(tjs, "base_name")
        layout.prop(tjs, "export_format")

    layout.separator()
    layout.label(text="Geometry and Materials", icon="MESH_DATA")
    layout.prop(tjs, "apply_modifiers")
    layout.prop(tjs, "include_materials")
    materials = layout.column(align=True)
    materials.enabled = tjs.include_materials
    materials.prop(tjs, "export_materials_mode")
    texture_export = materials.column(align=True)
    texture_export.enabled = tjs.export_materials_mode == "EXPORT"
    texture_export.prop(tjs, "include_textures")

    textures = texture_export.column(align=True)
    textures.enabled = tjs.include_textures
    textures.prop(tjs, "image_format")
    if tjs.image_format in {"JPEG", "WEBP"}:
        textures.prop(tjs, "texture_quality")

    scene_items = layout.row(align=True)
    scene_items.prop(tjs, "export_cameras")
    scene_items.prop(tjs, "export_lights")
    layout.prop(tjs, "export_skins")
    influences = layout.column()
    influences.enabled = tjs.export_skins
    influences.prop(tjs, "export_all_influences")
    layout.prop(tjs, "include_morphs")
    layout.prop(tjs, "include_vertex_colors")
    layout.prop(tjs, "export_tangents")
    layout.prop(tjs, "export_attributes")
    layout.prop(tjs, "export_yup")
    layout.prop(tjs, "custom_properties")

    layout.separator()
    layout.label(text="Mesh Compression")
    layout.prop(tjs, "compression")
    if tjs.compression == "DRACO":
        layout.prop(tjs, "draco_level")
        draco = layout.grid_flow(
            row_major=True,
            columns=2,
            even_columns=True,
            align=True,
        )
        draco.prop(tjs, "draco_position")
        draco.prop(tjs, "draco_normal")
        draco.prop(tjs, "draco_texcoord")
        draco.prop(tjs, "draco_color")
        draco.prop(tjs, "draco_generic")

    layout.separator()
    layout.label(text="Animation Export", icon="ACTION")
    if not tjs.portfolio_one_click:
        selection = layout.column(align=True)
        selection.prop(tjs, "animation_mode")
        if tjs.animation_mode == "ACTIONS":
            selection.prop(tjs, "export_all_actions")
        selection.prop(tjs, "exclude_actions")
        if tjs.animation_mode in {"ACTIONS", "NLA_TRACKS"}:
            selection.prop(tjs, "export_nla_strips")
    frame = layout.row(align=True)
    frame.prop(tjs, "export_frame_range")
    frame.prop(tjs, "export_frame_step")
    layout.prop(tjs, "export_reset_pose_at_frame_zero")
    layout.prop(tjs, "sample_baked")

    sampling = layout.row(align=True)
    sampling.prop(tjs, "sampling_fps")
    sampling.prop(tjs, "decimal_precision")

    layout.separator()
    layout.label(text="Animation Optimization")
    layout.prop(tjs, "keyframe_reduction")
    reduction = layout.column(align=True)
    reduction.enabled = tjs.keyframe_reduction
    tolerances = reduction.grid_flow(
        row_major=True,
        columns=2,
        even_columns=True,
        align=True,
    )
    tolerances.prop(tjs, "position_tolerance")
    tolerances.prop(tjs, "rotation_tolerance")
    tolerances.prop(tjs, "scale_tolerance")
    tolerances.prop(tjs, "morph_tolerance")
    layout.prop(tjs, "remove_static_tracks")

    layout.prop(tjs, "enable_animation_quantization")
    quantization = layout.column(align=True)
    quantization.enabled = tjs.enable_animation_quantization
    quantization.prop(tjs, "quantize_quaternions")
    vectors = quantization.row(align=True)
    vectors.prop(tjs, "quantize_positions")
    vectors.prop(tjs, "quantize_scales")
    quantization.prop(tjs, "quantize_morphs")


def draw_advanced_settings(layout, context):
    ahb = context.scene.ahb_props
    layout.use_property_split = True
    layout.use_property_decorate = False

    material_tools = layout.column(align=True)
    material_tools.label(text="Baked Material View", icon="NODE_MATERIAL")
    view = material_tools.row()
    view.operator(
        "ahb.toggle_baked_view",
        text=(
            "Show Original Materials"
            if ahb.baked_view
            else "Show Baked Result"
        ),
        icon="LOOP_BACK" if ahb.baked_view else "CHECKMARK",
    )
    material_tools.prop(ahb, "apply_mode")
    material_tools.prop(ahb, "apply_keep_original_nodes")
    material_actions = material_tools.row(align=True)
    material_actions.operator("ahb.apply_baked", text="Apply Bake to Materials")
    material_actions.operator("ahb.restore_materials", text="Restore Originals")

    layout.separator()
    layout.label(text="Cleanup", icon="TRASH")
    cleanup = layout.grid_flow(
        row_major=True,
        columns=2,
        even_columns=True,
        align=True,
    )
    cleanup.operator("ahb.clear_bake_nodes", text="Remove Bake Nodes")
    cleanup.operator("ahb.cleanup_images", text="Remove Bake Images")
    cleanup.operator("ahb.remove_unused_materials", text="Remove Unused Slots")
    cleanup.operator("ahb.rename_objects", text="Rename by Texture")
    layout.prop(ahb, "image_node_name")
    layout.prop(ahb, "auto_rename_objects")
    layout.prop(ahb, "auto_cleanup_nodes")

    layout.separator()
    layout.label(text="Settings Transfer")
    transfer = layout.row(align=True)
    transfer.operator("ahb.export_materials_json", text="Export JSON", icon="EXPORT")
    transfer.operator("ahb.import_materials_json", text="Import JSON", icon="IMPORT")


class WEB3D_PT_Sidebar(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Web3D"
    bl_label = "Web3D Asset Compiler"
    bl_idname = "WEB3D_PT_Sidebar"
    bl_order = 0

    def draw(self, context):
        draw_compile_overview(self.layout, context)


class _WEB3D_PT_SidebarChild:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Web3D"
    bl_parent_id = "WEB3D_PT_Sidebar"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _has_scene_props(context)


class WEB3D_PT_Bake(_WEB3D_PT_SidebarChild, Panel):
    bl_label = "Bake"
    bl_idname = "WEB3D_PT_Bake"
    bl_order = 1

    def draw(self, context):
        draw_bake_settings(self.layout, context)


class WEB3D_PT_UVPacking(_WEB3D_PT_SidebarChild, Panel):
    bl_label = "UV and Packing"
    bl_idname = "WEB3D_PT_UVPacking"
    bl_order = 2

    def draw(self, context):
        draw_uv_settings(self.layout, context)


class WEB3D_PT_Export(_WEB3D_PT_SidebarChild, Panel):
    bl_label = "Export"
    bl_idname = "WEB3D_PT_Export"
    bl_order = 3

    def draw(self, context):
        draw_export_settings(self.layout, context)


class WEB3D_PT_Advanced(_WEB3D_PT_SidebarChild, Panel):
    bl_label = "Advanced and Utilities"
    bl_idname = "WEB3D_PT_Advanced"
    bl_order = 4

    def draw(self, context):
        draw_advanced_settings(self.layout, context)


SIDEBAR_CLASSES = (
    WEB3D_PT_Sidebar,
    WEB3D_PT_Bake,
    WEB3D_PT_UVPacking,
    WEB3D_PT_Export,
    WEB3D_PT_Advanced,
)
