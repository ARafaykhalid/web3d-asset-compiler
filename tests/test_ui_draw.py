"""Drive every sidebar/render draw function with a genuine UILayout.

Blender swallows exceptions inside Panel.draw, so a broken draw path looks fine
until a user opens that tab. Properties-space panels are not category-gated, so
a throwaway panel there is guaranteed to be drawn by Blender's own UI and gets a
real `self.layout`. The addon's real WEB3D_PT_RenderProps is drawn in the same
pass, so its registration and poll are covered too.
"""
import os, sys, traceback, bpy
from bpy.types import Panel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "addon"))
import web3d_asset_compiler as w
from web3d_asset_compiler.ui.sidebar import (
    draw_compile_overview, draw_bake_settings, draw_uv_settings,
    draw_export_settings, draw_advanced_settings)

DRAWS = [
    ("overview", lambda l, c: draw_compile_overview(l, c)),
    ("overview_compact", lambda l, c: draw_compile_overview(l, c, compact=True)),
    ("bake", draw_bake_settings),
    ("uv", draw_uv_settings),
    ("export", draw_export_settings),
    ("advanced", draw_advanced_settings),
]
FAILURES, CALLS = [], {"n": 0}
STATE = {"cfg": -1}

class TEST_PT_AllDraw(Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'render'
    bl_label = "Web3D DrawTest"
    bl_idname = "TEST_PT_web3d_draw_all"

    def draw(self, context):
        for name, fn in DRAWS:
            try:
                fn(self.layout, context)
                CALLS["n"] += 1
            except Exception as exc:
                FAILURES.append(f"[{STATE['cfg']}] {name}: {type(exc).__name__}: {exc}")
                print(f"DRAWFAIL [{STATE['cfg']}] {name}: "
                      f"{type(exc).__name__}: {exc}")
                traceback.print_exc()

def build_scene(kind):
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete()
    if kind == "empty":
        return
    bpy.ops.mesh.primitive_cube_add(size=1.5)
    ob = bpy.context.active_object
    m = bpy.data.materials.new(f"M_{STATE['cfg']}")
    if kind != "nomaterial":
        ob.data.materials.append(m)
    if kind == "rig":
        bpy.ops.object.armature_add()
        rig = bpy.context.active_object
        vg = ob.vertex_groups.new(name="Bone")
        vg.add(list(range(len(ob.data.vertices))), 1.0, "REPLACE")
        mod = ob.modifiers.new(name="Armature", type="ARMATURE"); mod.object = rig
        ob.parent = rig
        rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions.new("Idle")
        rig.pose.bones[0].location = (0, 0, 1.0)
        rig.pose.bones[0].keyframe_insert(data_path="location", frame=1)
        ob.select_set(True); rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
    else:
        ob.select_set(True); bpy.context.view_layer.objects.active = ob

CONFIGS = [
    ("defaults/empty", "empty", {}),
    ("selected/with-mat", "mesh", {}),
    ("selected/no-mat", "nomaterial", {}),
    ("rig+animation", "rig", {}),
    ("atlas", "mesh", dict(image_mode='ATLAS')),
    ("auto_tiles", "mesh", dict(image_mode='AUTO_TILES', tile_count=4)),
    ("collection_atlases", "mesh", dict(image_mode='COLLECTION_ATLASES',
                                        collection_atlas_count=4)),
    ("per_material", "mesh", dict(image_mode='PER_MATERIAL')),
    ("uv_smart", "mesh", dict(uv_mode='SMART')),
    ("uv_auto_seam", "mesh", dict(uv_mode='AUTO_SEAM')),
    ("uv_cube", "mesh", dict(uv_mode='CUBE')),
    ("uv_lightmap", "mesh", dict(uv_mode='LIGHTMAP')),
    ("uv_existing", "mesh", dict(uv_mode='EXISTING')),
    ("no_rotate", "mesh", dict(pack_rotate=False)),
    ("axis_aligned", "mesh", dict(pack_rotation_step='AXIS_ALIGNED')),
    ("rot_none", "mesh", dict(pack_rotation_step='NONE')),
    ("stack_identical", "mesh", dict(pack_stack_identical=True)),
    ("nest_holes", "mesh", dict(pack_nest_holes=True)),
    ("no_pack", "mesh", dict(pack_enabled=False)),
    ("no_auto_create_uv", "mesh", dict(auto_create_uv=False)),
    ("no_pack_world_scale", "mesh", dict(pack_world_scale=False)),
    ("scale_islands", "mesh", dict(pack_scale_islands=True)),
    ("hdr_exr", "mesh", dict(image_format='OPEN_EXR', use_hdr_float=True)),
    ("multilayer", "mesh", dict(image_format='OPEN_EXR_MULTILAYER')),
    ("gltf_separate", "rig", dict(export_format='GLTF_SEPARATE')),
    ("gltf_embedded", "rig", dict(export_format='GLTF_EMBEDDED')),
    ("draco", "rig", dict(compression='DRACO')),
    ("no_skins", "mesh", dict(export_skins=False)),
    ("no_textures", "mesh", dict(include_textures=False)),
    ("nla_mode", "rig", dict(animation_mode='NLA_TRACKS')),
    ("scene_mode", "rig", dict(animation_mode='SCENE')),
    ("scene_scope", "mesh", dict(export_scope='SCENE')),
    ("portfolio", "rig", dict(portfolio_one_click=True)),
    ("cage", "mesh", dict(use_cage=True)),
    ("multires", "mesh", dict(multires_bake=True)),
    ("use_selected_to_active", "mesh", dict(use_selected_to_active=True)),
    ("exclude_actions", "rig", dict(exclude_actions="Nope")),
    ("baked_verified", "mesh", dict(baked_view=True, pack_verified=True,
                                   uv_gutter_px=16.0, bake_cancelled=False,
                                   status_text="Pipeline complete")),
    ("bake_cancelled", "mesh", dict(baked_view=False, pack_verified=False,
                                   bake_cancelled=True,
                                   status_text="Bake cancelled.")),
    ("bad_pack", "mesh", dict(pack_verified=False, uv_gutter_px=0.0,
                              status_text="bad UV pack: gutter 0px")),
    ("build_running", "mesh", dict(web3d_cancel_pending=True,
                                   web3d_cancel=False,
                                   status_text="Packing 2 object(s)")),
    ("cancel_pressed", "mesh", dict(web3d_cancel_pending=False,
                                   web3d_cancel=True,
                                   status_text="Cancelling")),
    ("many_tiles", "mesh", dict(image_mode='AUTO_TILES', tile_count=32)),
    ("many_atlases", "mesh", dict(image_mode='COLLECTION_ATLASES',
                                  collection_atlas_count=32)),
]

def apply(props_dict):
    ahb = bpy.context.scene.ahb_props
    tjs = bpy.context.scene.tjs_props
    for key, value in props_dict.items():
        if hasattr(ahb, key): setattr(ahb, key, value)
        elif hasattr(tjs, key): setattr(tjs, key, value)
        else: FAILURES.append(f"unknown property {key}")

AREA = {"area": None}

def ensure_props_area():
    if AREA["area"] is None:
        for area in bpy.context.window.screen.areas:
            if area.type == 'VIEW_3D':
                area.type = 'PROPERTIES'
                AREA["area"] = area
                break
    area = AREA["area"]
    area.spaces.active.context = 'RENDER'
    return area

def step():
    STATE["cfg"] += 1
    if STATE["cfg"] >= len(CONFIGS):
        print(f"\n=== CONFIGS: {len(CONFIGS)}  DRAW CALLS: {CALLS['n']} "
              f"(expected {len(CONFIGS)*len(DRAWS)})")
        print(f"=== FAILURES: {len(FAILURES)}")
        for f in FAILURES:
            print(f"    {f}")
        bpy.ops.wm.quit_blender()
        if FAILURES:
            raise SystemExit(f"{len(FAILURES)} UI draw failure(s)")
        return None
    label, kind, props_dict = CONFIGS[STATE["cfg"]]
    build_scene(kind)
    apply(props_dict)
    area = ensure_props_area()
    area.tag_redraw()
    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except Exception as exc:
        FAILURES.append(f"[{STATE['cfg']}] redraw: {type(exc).__name__}: {exc}")
    print(f"DREW {STATE['cfg']:2d} {label} (calls so far {CALLS['n']})")
    return 0.02

w.register()
bpy.utils.register_class(TEST_PT_AllDraw)
bpy.app.timers.register(step, first_interval=0.1)
