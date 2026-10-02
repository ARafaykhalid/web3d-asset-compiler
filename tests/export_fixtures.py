"""
Export a rigged character so the web examples can be type-checked.

The examples import the addon's generated controller from ./generated, so this
writes a real export there. Run it, then `npm run check` inside examples/:

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/export_fixtures.py
    cd examples && npm install && npm run check
"""

import os
import sys

import bpy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "examples", "generated")

sys.path.insert(0, os.path.join(REPO_ROOT, "addon"))

import web3d_asset_compiler  # noqa: E402

web3d_asset_compiler.register()

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

bpy.ops.object.armature_add(radius=1.0)
rig = bpy.context.active_object
rig.name = "ExampleRig"

bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=2.0)
mesh = bpy.context.active_object
mesh.name = "ExampleMesh"

group = mesh.vertex_groups.new(name="Bone")
group.add(list(range(len(mesh.data.vertices))), 1.0, "REPLACE")
modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
modifier.object = rig
mesh.parent = rig

bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="POSE")
bone = rig.pose.bones["Bone"]

action = bpy.data.actions.new(name="Idle")
rig.animation_data_create()
rig.animation_data.action = action
bone.location = (0.0, 0.0, 0.0)
bone.keyframe_insert(data_path="location", frame=1)
bone.location = (0.0, 0.0, 1.0)
bone.keyframe_insert(data_path="location", frame=30)
bpy.ops.object.mode_set(mode="OBJECT")

bpy.ops.object.select_all(action='DESELECT')
rig.select_set(True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = rig

props = bpy.context.scene.tjs_props
for key, value in {
    "output_dir": OUTPUT_DIR,
    "base_name": "character.glb",
    "export_format": "GLB",
    "export_scope": "SELECTED",
    "animation_mode": "ACTIONS",
    "export_skins": True,
    "compression": "NONE",
    "sampling_fps": 30,
}.items():
    setattr(props, key, value)

result = bpy.ops.tjs.export_animations()
print(f"FIXTURE_EXPORT {result} -> {OUTPUT_DIR}")
if 'FINISHED' not in result:
    raise SystemExit("fixture export failed")
