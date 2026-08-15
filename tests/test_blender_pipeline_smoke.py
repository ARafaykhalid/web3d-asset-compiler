"""
Blender runtime smoke test suite for Web3D Asset Compiler.
Executed with: blender --background --factory-startup --python tests/test_blender_pipeline_smoke.py
"""

import os
import sys
import tempfile
import json
import unittest

import bpy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(REPO_ROOT, "addon")

if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

import web3d_asset_compiler


def reset_blender_scene():
    """Clear all objects, meshes, materials, armatures, actions, and images."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    for collection in (
        bpy.data.objects,
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.images,
    ):
        for item in list(collection):
            try:
                collection.remove(item)
            except Exception:
                pass


class TestBlenderPipelineSmoke(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        web3d_asset_compiler.register()

    @classmethod
    def tearDownClass(cls):
        try:
            web3d_asset_compiler.unregister()
        except Exception:
            pass

    def setUp(self):
        reset_blender_scene()

    def test_A_registration(self):
        """Verify registration, unregistration, re-registration, operators, panels, and properties."""
        print("\n--- Test A: Registration & Symmetry ---")
        
        # Test unregistration and re-registration symmetry
        web3d_asset_compiler.unregister()
        scene = bpy.context.scene
        self.assertFalse(hasattr(scene, "ahb_props"))
        self.assertFalse(hasattr(scene, "tjs_props"))
        self.assertFalse(hasattr(scene, "web3d_status"))

        web3d_asset_compiler.register()
        self.assertTrue(hasattr(scene, "ahb_props"))
        self.assertTrue(hasattr(scene, "tjs_props"))
        self.assertTrue(hasattr(scene, "web3d_status"))

        # Verify key operator existence
        expected_ops = [
            "web3d.build_web_asset",
            "ahb.quick_bake",
            "tjs.export_character_glb",
            "tjs.export_animations",
            "web3d.apply_preset",
        ]
        for op in expected_ops:
            mod, name = op.split(".")
            group = getattr(bpy.ops, mod, None)
            self.assertIsNotNone(group, f"Operator group '{mod}' missing")
            self.assertTrue(hasattr(group, name), f"Operator '{op}' missing")

        # Verify main sidebar panel class registration
        self.assertTrue(hasattr(bpy.types, "WEB3D_PT_Sidebar"))
        self.assertTrue(hasattr(bpy.types, "WEB3D_PT_Bake"))
        self.assertTrue(hasattr(bpy.types, "WEB3D_PT_UVPacking"))
        self.assertTrue(hasattr(bpy.types, "WEB3D_PT_Export"))
        self.assertTrue(hasattr(bpy.types, "WEB3D_PT_Advanced"))
        print("[OK] Test A Passed!")

    def test_B_baking(self):
        """Test baking pipeline with a simple mesh and internal image output."""
        print("\n--- Test B: Baking Pipeline ---")
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        cube = bpy.context.active_object
        self.assertIsNotNone(cube)

        mat = bpy.data.materials.new(name="TestMaterial")
        mat.use_nodes = True
        cube.data.materials.append(mat)

        scene = bpy.context.scene
        ahb = scene.ahb_props
        ahb.bake_type = "AO"
        ahb.samples = 1
        ahb.compute_device = "CPU"
        ahb.resolution = "CUSTOM"
        ahb.custom_res_x = 64
        ahb.custom_res_y = 64
        ahb.save_mode = "INTERNAL"
        ahb.bake_scope = "SELECTED"

        cube.select_set(True)
        bpy.context.view_layer.objects.active = cube

        res = bpy.ops.ahb.quick_bake()
        self.assertEqual(res, {"FINISHED"}, "quick_bake failed")

        # Verify a baked image exists in bpy.data.images
        baked_images = [img for img in bpy.data.images if img.has_data and img.size[0] == 64]
        self.assertTrue(len(baked_images) > 0, "No 64x64 baked image was created in bpy.data.images")
        print(f"[OK] Test B Passed! Created baked image: {baked_images[0].name}")

    def test_C_model_export(self):
        """Test character GLB model export to temporary directory."""
        print("\n--- Test C: Model Export ---")
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        cube = bpy.context.active_object
        cube.select_set(True)
        bpy.context.view_layer.objects.active = cube

        scene = bpy.context.scene
        tjs = scene.tjs_props

        with tempfile.TemporaryDirectory() as tmp_dir:
            tjs.output_dir = tmp_dir
            tjs.base_name = "test_character.glb"
            tjs.export_format = "GLB"
            tjs.export_scope = "SELECTED"

            res = bpy.ops.tjs.export_character_glb()
            self.assertEqual(res, {"FINISHED"}, "export_character_glb failed")

            expected_glb = os.path.join(tmp_dir, "test_character.glb")
            self.assertTrue(os.path.exists(expected_glb), f"Exported GLB missing: {expected_glb}")
            self.assertGreater(os.path.getsize(expected_glb), 0, "Exported GLB is empty")
            print(f"[OK] Test C Passed! Exported GLB size: {os.path.getsize(expected_glb)} bytes")

    def test_D_animated_character_export(self):
        """Test animated character export with armature, action keyframes, and TypeScript generation."""
        print("\n--- Test D: Animated Character Export ---")
        
        # 1. Create Armature
        bpy.ops.object.armature_add(radius=1.0)
        rig = bpy.context.active_object
        rig.name = "TestRig"

        # 2. Create Mesh
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=2.0)
        mesh = bpy.context.active_object
        mesh.name = "TestMesh"

        # Add vertex group matching bone name 'Bone'
        vg = mesh.vertex_groups.new(name="Bone")
        vg.add(list(range(len(mesh.data.vertices))), 1.0, "REPLACE")

        # Add Armature modifier
        mod = mesh.modifiers.new(name="Armature", type="ARMATURE")
        mod.object = rig
        mesh.parent = rig

        # 3. Create Animation Action
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="POSE")
        pbone = rig.pose.bones["Bone"]

        action = bpy.data.actions.new(name="IdleWalkAction")
        rig.animation_data_create()
        rig.animation_data.action = action

        pbone.location = (0, 0, 0)
        pbone.keyframe_insert(data_path="location", frame=1)
        pbone.location = (0, 0, 1.0)
        pbone.keyframe_insert(data_path="location", frame=10)

        bpy.ops.object.mode_set(mode="OBJECT")

        # Select both rig and mesh
        bpy.ops.object.select_all(action="DESELECT")
        rig.select_set(True)
        mesh.select_set(True)
        bpy.context.view_layer.objects.active = rig

        scene = bpy.context.scene
        tjs = scene.tjs_props

        with tempfile.TemporaryDirectory() as tmp_dir:
            tjs.output_dir = tmp_dir
            tjs.base_name = "character.glb"
            tjs.export_format = "GLB"
            tjs.export_scope = "SELECTED"
            tjs.animation_mode = "ACTIONS"
            tjs.export_skins = True
            tjs.compression = "NONE"

            res = bpy.ops.tjs.export_animations()
            self.assertEqual(res, {"FINISHED"}, "export_animations failed")

            # Verify required output files exist and are non-empty
            expected_files = [
                os.path.join(tmp_dir, "character.glb"),
                os.path.join(tmp_dir, "animation-manifest.json"),
                os.path.join(tmp_dir, "animationClipFactory.ts"),
                os.path.join(tmp_dir, "model_controller.ts"),
                os.path.join(tmp_dir, "animations", "index.ts"),
            ]
            for file_path in expected_files:
                self.assertTrue(os.path.exists(file_path), f"Required file missing: {file_path}")
                self.assertGreater(os.path.getsize(file_path), 0, f"File is empty: {file_path}")

            anim_dir = os.path.join(tmp_dir, "animations")
            anim_files = [f for f in os.listdir(anim_dir) if f.endswith(".anim")]
            self.assertTrue(len(anim_files) > 0, "No .anim binary files generated in animations/")

            # Parse animation-manifest.json
            manifest_path = os.path.join(tmp_dir, "animation-manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            self.assertEqual(manifest.get("format"), "T3AN")
            self.assertEqual(manifest.get("character"), "character.glb")
            self.assertIn("animations", manifest)
            self.assertTrue(len(manifest["animations"]) > 0)
            print(f"[OK] Test D Passed! Created manifest with {len(manifest['animations'])} animation(s).")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
