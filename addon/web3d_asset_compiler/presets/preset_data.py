"""
Target optimization presets for Web3D Asset Compiler.
"""

PRESETS = {
    'R3F_OPTIMIZED': {
        'label': '⚡ React Three Fiber (R3F) Optimized',
        'description': 'Balanced for modern web applications: 2K Atlas, 30 FPS, Int16 quantization, Draco level 6, WebP.',
        'ahb': {
            'resolution': '2048',
            'image_mode': 'ATLAS',
            'image_format': 'PNG',
            'uv_mode': 'SMART',
            'pack_enabled': True,
            'pack_world_scale': True,
            'pack_margin_px': 16,
            'samples': 128,
        },
        'tjs': {
            'image_format': 'WEBP',
            'texture_quality': 85,
            'compression': 'DRACO',
            'draco_level': 6,
            'sampling_fps': 30,
            'keyframe_reduction': True,
            'enable_animation_quantization': True,
            'quantize_quaternions': True,
            'quantize_vectors': True,
        }
    },
    'THREEJS_PORTFOLIO': {
        'label': '🎨 Three.js Portfolio High-Quality',
        'description': 'Maximum visual fidelity for portfolio showcases: 4K Atlas, uncompressed GLB, high precision float keyframes.',
        'ahb': {
            'resolution': '4096',
            'image_mode': 'ATLAS',
            'image_format': 'PNG',
            'uv_mode': 'SMART',
            'pack_enabled': True,
            'pack_world_scale': True,
            'pack_margin_px': 16,
            'samples': 256,
        },
        'tjs': {
            'image_format': 'AUTO',
            'texture_quality': 95,
            'compression': 'NONE',
            'sampling_fps': 30,
            'keyframe_reduction': False,
            'enable_animation_quantization': False,
        }
    },
    'MOBILE_WEB_LIGHT': {
        'label': '📱 Mobile Web Ultra-Light',
        'description': 'Ultra-compact assets for mobile browsers: 1K Auto Tiles, 15 FPS keyframe reduction, Draco level 8, WebP 75%.',
        'ahb': {
            'resolution': '1024',
            'image_mode': 'AUTO_TILES',
            'tile_count': 4,
            'image_format': 'JPEG',
            'uv_mode': 'SMART',
            'pack_enabled': True,
            'pack_margin_px': 8,
            'samples': 64,
        },
        'tjs': {
            'image_format': 'WEBP',
            'texture_quality': 75,
            'compression': 'DRACO',
            'draco_level': 8,
            'sampling_fps': 15,
            'keyframe_reduction': True,
            'enable_animation_quantization': True,
            'quantize_quaternions': True,
            'quantize_vectors': True,
        }
    },
    'ARCHVIZ_LIGHTMAP': {
        'label': '🏛️ ArchViz Lightmap Bake',
        'description': 'High-resolution lightmaps for architectural scenes: 4K EXR/PNG, Auto Seam Unwrap, World-Space Proportional Packing.',
        'ahb': {
            'resolution': '4096',
            'image_mode': 'ATLAS',
            'image_format': 'OPEN_EXR',
            'exr_codec': 'ZIP',
            'use_hdr_float': True,
            'uv_mode': 'AUTO_SEAM',
            'auto_seam_angle': 30.0,
            'pack_enabled': True,
            'pack_world_scale': True,
            'pack_margin_px': 24,
            'samples': 512,
        },
        'tjs': {
            'image_format': 'AUTO',
            'compression': 'NONE',
            'sampling_fps': 30,
        }
    }
}
