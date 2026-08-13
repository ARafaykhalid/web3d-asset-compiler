bl_info = {
    "name": "Three.js Binary Animation Exporter",
    "author": "Rocky",
    "version": (2, 2, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Three.js",
    "description": "Export an animation-free character GLB plus compact binary Action files",
    "category": "Import-Export",
}

import bpy
import importlib.util
import json
import os
import re
import struct
from bpy.props import (
    BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup


PORTFOLIO_OUTPUT_DIRECTORY = r"C:\Users\rocky\OneDrive\Documents\GitHub\portfolio\public\models"


def _safe_base_name(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    value = value.strip("._")
    return value or "animated_model"


def _read_glb_animation_names(filepath):
    """Read exact animation names from the JSON chunk of a GLB file."""
    with open(filepath, "rb") as handle:
        magic, version, total_length = struct.unpack("<4sII", handle.read(12))
        if magic != b"glTF" or version != 2:
            raise ValueError("The exported file is not a glTF 2.0 binary.")

        while handle.tell() < total_length:
            chunk_length, chunk_type = struct.unpack("<II", handle.read(8))
            chunk_data = handle.read(chunk_length)
            if chunk_type != 0x4E4F534A:
                continue
            document = json.loads(chunk_data.rstrip(b"\x00 \t\r\n").decode("utf-8"))
            return [
                animation.get("name") or f"Animation_{index + 1}"
                for index, animation in enumerate(document.get("animations", []))
            ]
    return []


_TARGET_LENGTHS = {
    "delta_location": 3,
    "delta_rotation_euler": 3,
    "delta_rotation_quaternion": 4,
    "delta_scale": 3,
    "location": 3,
    "rotation_axis_angle": 4,
    "rotation_euler": 3,
    "rotation_quaternion": 4,
    "scale": 3,
    "value": 1,
}


def _iter_action_fcurve_collections(action):
    """Yield each F-curve collection for legacy and Blender 5 slotted Actions."""
    found_slotted_curves = False
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for channelbag in getattr(strip, "channelbags", []):
                found_slotted_curves = True
                yield channelbag.fcurves

    if found_slotted_curves:
        return
    try:
        yield action.fcurves
    except (AttributeError, RuntimeError):
        pass

def _is_unsafe_fcurve(fcurve):
    """Match the component lengths used internally by Blender's glTF exporter."""
    target = fcurve.data_path.rsplit(".", 1)[-1]
    if target.startswith("["):
        target = ""
    target_length = _TARGET_LENGTHS.get(target, 1)
    return fcurve.array_index < 0 or fcurve.array_index >= target_length


def _quarantine_unsafe_fcurves():
    """Temporarily move glTF-crashing curves to an incompatible Action slot."""
    quarantine = bpy.data.actions.new("__TJS_EXPORT_QUARANTINE__")
    slot = quarantine.slots.new("SCENE", "TJS Export Quarantine")
    layer = quarantine.layers.new("Quarantine")
    strip = layer.strips.new(type="KEYFRAME")
    destination = strip.channelbags.new(slot).fcurves
    records = []

    for action in list(bpy.data.actions):
        if action == quarantine:
            continue
        for collection in _iter_action_fcurve_collections(action):
            for fcurve in list(collection):
                if not _is_unsafe_fcurve(fcurve):
                    continue
                copy = destination.new_from_fcurve(fcurve)
                records.append((collection, copy, action.name, fcurve.data_path,
                                fcurve.array_index))
                collection.remove(fcurve)
    return quarantine, records


def _restore_quarantined_fcurves(quarantine, records):
    for collection, copy, _action_name, _data_path, _array_index in records:
        collection.new_from_fcurve(copy)
    if quarantine:
        bpy.data.actions.remove(quarantine)


def _typescript_module(glb_filename, animation_names):
    names_json = json.dumps(animation_names, ensure_ascii=False, indent=2)
    return f"""import * as THREE from 'three';
import {{ GLTFLoader, type GLTF }} from 'three/addons/loaders/GLTFLoader.js';

export const modelUrl = new URL('./{glb_filename}', import.meta.url).href;
export const animationNames = {names_json} as const;
export type AnimationName = (typeof animationNames)[number] | (string & {{}});

export interface LoadAnimatedModelOptions {{
  manager?: THREE.LoadingManager;
  dracoLoader?: Parameters<GLTFLoader['setDRACOLoader']>[0];
  meshoptDecoder?: Parameters<GLTFLoader['setMeshoptDecoder']>[0];
  crossFadeDuration?: number;
}}

export interface PlayOptions {{
  fade?: number;
  loop?: typeof THREE.LoopOnce | typeof THREE.LoopRepeat | typeof THREE.LoopPingPong;
  repetitions?: number;
  clampWhenFinished?: boolean;
  timeScale?: number;
}}

export interface AnimatedModelController {{
  gltf: GLTF;
  root: THREE.Group;
  mixer: THREE.AnimationMixer;
  clips: ReadonlyMap<string, THREE.AnimationClip>;
  animationNames: string[];
  getAction(name: AnimationName): THREE.AnimationAction;
  play(name: AnimationName, options?: PlayOptions): THREE.AnimationAction;
  stop(fade?: number): void;
  update(deltaSeconds: number): void;
  dispose(): void;
}}

export async function loadAnimatedModel(
  options: LoadAnimatedModelOptions = {{}},
): Promise<AnimatedModelController> {{
  const {{
    manager,
    dracoLoader,
    meshoptDecoder,
    crossFadeDuration = 0.25,
  }} = options;

  const loader = new GLTFLoader(manager);
  if (dracoLoader) loader.setDRACOLoader(dracoLoader);
  if (meshoptDecoder) loader.setMeshoptDecoder(meshoptDecoder);

  const gltf = await loader.loadAsync(modelUrl);
  const root = gltf.scene;
  const mixer = new THREE.AnimationMixer(root);
  const clips = new Map<string, THREE.AnimationClip>(
    gltf.animations.map((clip) => [clip.name, clip]),
  );
  const actions = new Map<string, THREE.AnimationAction>();
  let currentAction: THREE.AnimationAction | null = null;

  function getAction(name: AnimationName): THREE.AnimationAction {{
    const clip = clips.get(name);
    if (!clip) {{
      throw new Error(
        `Unknown animation "${{name}}". Available: ${{[...clips.keys()].join(', ') || 'none'}}`
      );
    }}
    if (!actions.has(name)) actions.set(name, mixer.clipAction(clip));
    return actions.get(name)!;
  }}

  function play(
    name: AnimationName,
    playOptions: PlayOptions = {{}},
  ): THREE.AnimationAction {{
    const {{
      fade = crossFadeDuration,
      loop = THREE.LoopRepeat,
      repetitions = Infinity,
      clampWhenFinished = false,
      timeScale = 1,
    }} = playOptions;

    const nextAction = getAction(name);
    nextAction.enabled = true;
    nextAction.clampWhenFinished = clampWhenFinished;
    nextAction.setLoop(loop, repetitions);
    nextAction.setEffectiveTimeScale(timeScale);
    nextAction.setEffectiveWeight(1);

    if (currentAction && currentAction !== nextAction) {{
      currentAction.fadeOut(fade);
    }}

    nextAction.reset().fadeIn(fade).play();
    currentAction = nextAction;
    return nextAction;
  }}

  function stop(fade = crossFadeDuration): void {{
    if (!currentAction) return;
    currentAction.fadeOut(fade);
    currentAction = null;
  }}

  function update(deltaSeconds: number): void {{
    mixer.update(deltaSeconds);
  }}

  function dispose(): void {{
    mixer.stopAllAction();
    mixer.uncacheRoot(root);
    root.traverse((object) => {{
      if (!(object instanceof THREE.Mesh)) return;
      object.geometry.dispose();
      const materials: THREE.Material[] = Array.isArray(object.material)
        ? object.material
        : [object.material];
      for (const material of materials) {{
        if (!material) continue;
        for (const value of Object.values(material)) {{
          if (value instanceof THREE.Texture) value.dispose();
        }}
        material.dispose();
      }}
    }});
  }}

  return {{
    gltf,
    root,
    mixer,
    clips,
    animationNames: [...clips.keys()],
    getAction,
    play,
    stop,
    update,
    dispose,
  }};
}}
"""

def _collect_export_objects(context):
    """Include selected objects plus parents and armatures required by meshes."""
    result = set(context.selected_objects)
    pending = list(result)

    while pending:
        obj = pending.pop()
        if obj.parent and obj.parent not in result:
            result.add(obj.parent)
            pending.append(obj.parent)
        if obj.type == "MESH":
            for modifier in obj.modifiers:
                armature = modifier.object if modifier.type == "ARMATURE" else None
                if armature and armature not in result:
                    result.add(armature)
                    pending.append(armature)

    return sorted(result, key=lambda obj: obj.name)


class TJS_Properties(PropertyGroup):
    portfolio_one_click: BoolProperty(
        name="Portfolio One-Click Export",
        description="Export character.glb, every current Action, manifest, and binary files directly into the linked portfolio",
        default=True,
    )
    output_dir: StringProperty(
        name="Output Directory",
        subtype="DIR_PATH",
        default=PORTFOLIO_OUTPUT_DIRECTORY,
    )
    base_name: StringProperty(
        name="GLB Filename",
        default="character.glb",
    )
    export_scope: EnumProperty(
        name="Scope",
        items=[
            ("SELECTED", "Selected", "Export selected objects and required rigs"),
            ("SCENE", "Entire Scene", "Export the complete scene"),
        ],
        default="SELECTED",
    )
    animation_mode: EnumProperty(
        name="Animations",
        items=[
            ("ACTIONS", "All Actions", "Export compatible actions as separate clips"),
            ("ACTIVE_ACTIONS", "Active Actions", "Export active actions only"),
            ("NLA_TRACKS", "NLA Tracks", "Export non-muted NLA tracks as clips"),
            ("SCENE", "Scene Timeline", "Export the scene timeline as one animation"),
        ],
        default="ACTIONS",
    )
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Export the evaluated mesh with visible modifiers applied; keep disabled when editable shape keys must be preserved",
        default=False,
    )
    include_materials: BoolProperty(name="Include Materials", default=True)
    include_morphs: BoolProperty(name="Include Shape Keys", default=True)
    include_textures: BoolProperty(name="Include Textures", default=True)
    image_format: EnumProperty(
        name="Image Format",
        items=[
            ("AUTO", "Automatic", "Keep compatible source formats"),
            ("JPEG", "JPEG", "Convert exported images to JPEG"),
            ("WEBP", "WebP", "Convert exported images to WebP"),
            ("NONE", "No Images", "Do not export image data"),
        ],
        default="AUTO",
    )
    texture_quality: IntProperty(
        name="Image Quality", default=85, min=0, max=100,
    )
    include_vertex_colors: BoolProperty(name="Include Vertex Colors", default=True)
    export_tangents: BoolProperty(name="Export Tangents", default=False)
    custom_properties: BoolProperty(name="Custom Properties", default=False)
    compression: EnumProperty(
        name="Mesh Compression",
        items=[
            ("NONE", "None", "Export uncompressed mesh data"),
            ("DRACO", "Draco", "Compress final GLB mesh data with Draco"),
        ],
        default="NONE",
    )
    draco_level: IntProperty(
        name="Compression Level", default=6, min=0, max=10,
    )
    draco_position: IntProperty(
        name="Position Bits", default=14, min=0, max=30,
    )
    draco_normal: IntProperty(
        name="Normal Bits", default=10, min=0, max=30,
    )
    draco_texcoord: IntProperty(
        name="UV Bits", default=12, min=0, max=30,
    )
    draco_color: IntProperty(
        name="Color Bits", default=10, min=0, max=30,
    )
    draco_generic: IntProperty(
        name="Generic Bits", default=12, min=0, max=30,
    )
    position_tolerance: FloatProperty(
        name="Position Error", default=0.0001, min=0.0, precision=6,
    )
    rotation_tolerance: FloatProperty(
        name="Rotation Error (deg)", default=0.05, min=0.0, precision=4,
    )
    scale_tolerance: FloatProperty(
        name="Scale Error", default=0.0001, min=0.0, precision=6,
    )
    morph_tolerance: FloatProperty(
        name="Morph Error", default=0.0005, min=0.0, precision=6,
    )
    keyframe_reduction: BoolProperty(name="Reduce Keyframes", default=True)
    remove_static_tracks: BoolProperty(name="Remove Rest-Pose Tracks", default=True)
    enable_animation_quantization: BoolProperty(name="Quantize Animation Data", default=True)
    quantize_quaternions: BoolProperty(name="Int16 Quaternions", default=True)
    quantize_vectors: BoolProperty(name="Uint16 Position / Scale", default=True)
    quantize_morphs: BoolProperty(name="Uint16 Morph Values", default=True)
    export_all_actions: BoolProperty(name="Export All Actions", default=True)
    exclude_actions: StringProperty(
        name="Exclude Actions",
        description="Comma-separated Action names to skip",
        default="",
    )
    decimal_precision: IntProperty(
        name="Decimal Precision", default=5, min=3, max=9,
    )
    sampling_fps: IntProperty(
        name="Sampling FPS", default=30, min=1, max=240,
    )
    sample_baked: BoolProperty(
        name="Sample Evaluated Pose",
        description="Bake constraints and final dependency-graph pose",
        default=True,
    )
    status: StringProperty(default="Ready")


class TJS_OT_ExportAnimations(Operator):
    bl_idname = "tjs.export_animations"
    bl_label = "Export Character + Binary Animations"
    bl_description = "Create animation-free character.glb, animations/*.anim, and animation-manifest.json"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.tjs_props
        wm = context.window_manager
        wm.progress_begin(0, 100)
        props.status = "Loading binary animation exporter…"

        try:
            module_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "threejs_binary_animation_export.py",
            )
            if not os.path.isfile(module_path):
                raise RuntimeError(
                    "Missing threejs_binary_animation_export.py next to the add-on."
                )

            spec = importlib.util.spec_from_file_location(
                "threejs_binary_animation_export_runtime", module_path
            )
            exporter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(exporter)

            if props.portfolio_one_click:
                filename = "character.glb"
                output_directory = PORTFOLIO_OUTPUT_DIRECTORY
            else:
                filename = props.base_name.strip() or "character.glb"
                if not filename.lower().endswith(".glb"):
                    filename += ".glb"
                filename = os.path.basename(filename)
                output_directory = props.output_dir
            excluded = {
                name.strip()
                for name in re.split(r"[,;\n]+", props.exclude_actions)
                if name.strip()
            }

            exporter.OUTPUT_DIRECTORY = output_directory
            exporter.GLB_FILENAME = filename
            exporter.EXPORT_ALL_ACTIONS = True if props.portfolio_one_click else props.export_all_actions
            exporter.ACTIONS_TO_EXCLUDE = excluded
            exporter.DECIMAL_PRECISION = props.decimal_precision
            exporter.SAMPLING_FPS = props.sampling_fps
            exporter.SAMPLE_BAKED_ANIMATION = props.sample_baked
            exporter.APPLY_MODIFIERS = props.apply_modifiers
            exporter.EXPORT_MATERIALS = props.include_materials
            exporter.EXPORT_TEXTURES = props.include_textures
            exporter.TEXTURE_IMAGE_FORMAT = props.image_format
            exporter.TEXTURE_QUALITY = props.texture_quality
            exporter.EXPORT_MORPH_TARGETS = props.include_morphs
            exporter.EXPORT_VERTEX_COLORS = props.include_vertex_colors
            exporter.EXPORT_TANGENTS = props.export_tangents
            exporter.EXPORT_CUSTOM_PROPERTIES = props.custom_properties
            exporter.MESH_COMPRESSION = props.compression
            exporter.DRACO_COMPRESSION_LEVEL = props.draco_level
            exporter.DRACO_POSITION_QUANTIZATION = props.draco_position
            exporter.DRACO_NORMAL_QUANTIZATION = props.draco_normal
            exporter.DRACO_TEXCOORD_QUANTIZATION = props.draco_texcoord
            exporter.DRACO_COLOR_QUANTIZATION = props.draco_color
            exporter.DRACO_GENERIC_QUANTIZATION = props.draco_generic
            exporter.POSITION_TOLERANCE = props.position_tolerance
            exporter.ROTATION_TOLERANCE_DEGREES = props.rotation_tolerance
            exporter.SCALE_TOLERANCE = props.scale_tolerance
            exporter.MORPH_TOLERANCE = props.morph_tolerance
            exporter.ENABLE_KEYFRAME_REDUCTION = props.keyframe_reduction
            exporter.REMOVE_STATIC_TRACKS = props.remove_static_tracks
            exporter.ENABLE_QUANTIZATION = props.enable_animation_quantization
            exporter.QUANTIZE_QUATERNIONS = props.quantize_quaternions
            exporter.QUANTIZE_POSITIONS = props.quantize_vectors
            exporter.QUANTIZE_SCALES = props.quantize_vectors
            exporter.QUANTIZE_MORPHS = props.quantize_morphs

            def update_export_progress(value, message):
                wm.progress_update(value)
                props.status = message
                if context.area:
                    context.area.tag_redraw()

            exporter.PROGRESS_CALLBACK = update_export_progress

            wm.progress_update(1)
            props.status = "Exporting character and binary Actions…"
            result = exporter.export_character_and_animations()
            wm.progress_update(100)

            animation_count = len(result["animations"])
            props.status = (
                f"Done: {animation_count} animation(s), "
                f"{result['tracks']} tracks"
            )
            self.report(
                {"INFO"},
                f"Exported character.glb and {animation_count} binary animation file(s).",
            )
            return {"FINISHED"}
        except Exception as exc:
            props.status = f"Export failed: {exc}"
            self.report({"ERROR"}, props.status)
            return {"CANCELLED"}
        finally:
            wm.progress_end()

class TJS_PT_Exporter(Panel):
    bl_label = "Three.js Animation Export"
    bl_idname = "TJS_PT_animation_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Three.js"

    def draw(self, context):
        layout = self.layout
        props = context.scene.tjs_props

        status = layout.box()
        status.label(text=props.status, icon="INFO")

        output = layout.box()
        output.label(text="Output", icon="FILE_FOLDER")
        output.prop(props, "portfolio_one_click", icon="CHECKMARK")
        if props.portfolio_one_click:
            output.label(text="portfolio/public/models", icon="FILE_TICK")
            output.label(text="Exports every current Action automatically", icon="ACTION")
        else:
            output.prop(props, "output_dir")
            output.prop(props, "base_name")

        geometry = layout.box()
        geometry.label(text="Geometry & Materials", icon="MESH_DATA")
        geometry.prop(props, "apply_modifiers")
        geometry.prop(props, "include_materials")
        textures = geometry.column()
        textures.enabled = props.include_materials
        textures.prop(props, "include_textures")
        image = textures.column()
        image.enabled = props.include_textures
        image.prop(props, "image_format")
        if props.image_format != "NONE":
            image.prop(props, "texture_quality")
        geometry.prop(props, "include_morphs")
        geometry.prop(props, "include_vertex_colors")
        geometry.prop(props, "export_tangents")
        geometry.prop(props, "custom_properties")

        compression = layout.box()
        compression.label(text="Compression", icon="PACKAGE")
        compression.prop(props, "compression")
        if props.compression == "DRACO":
            compression.prop(props, "draco_level")
            row = compression.row(align=True)
            row.prop(props, "draco_position")
            row.prop(props, "draco_normal")
            row = compression.row(align=True)
            row.prop(props, "draco_texcoord")
            row.prop(props, "draco_color")
            compression.prop(props, "draco_generic")
            compression.label(text="Requires Three.js DracoLoader", icon="INFO")

        optimization = layout.box()
        optimization.label(text="Binary Animation Optimization", icon="MOD_SIMPLIFY")
        row = optimization.row(align=True)
        row.prop(props, "position_tolerance")
        row.prop(props, "rotation_tolerance")
        row = optimization.row(align=True)
        row.prop(props, "scale_tolerance")
        row.prop(props, "morph_tolerance")
        optimization.prop(props, "keyframe_reduction")
        optimization.prop(props, "remove_static_tracks")
        optimization.prop(props, "enable_animation_quantization")
        if props.enable_animation_quantization:
            quantization = optimization.column(align=True)
            quantization.prop(props, "quantize_quaternions")
            quantization.prop(props, "quantize_vectors")
            quantization.prop(props, "quantize_morphs")

        settings = layout.box()
        settings.label(text="External Binary Animations", icon="ACTION")
        settings.prop(props, "export_all_actions")
        settings.prop(props, "exclude_actions")
        row = settings.row(align=True)
        row.prop(props, "sampling_fps")
        row.prop(props, "decimal_precision")
        settings.prop(props, "sample_baked")
        settings.label(text="Output: GLB + animations/*.anim + manifest", icon="ACTION")

        button = layout.row()
        button.scale_y = 1.5
        button.operator("tjs.export_animations", icon="EXPORT")


_CLASSES = (
    TJS_Properties,
    TJS_OT_ExportAnimations,
    TJS_PT_Exporter,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.tjs_props = PointerProperty(type=TJS_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "tjs_props"):
        del bpy.types.Scene.tjs_props
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
