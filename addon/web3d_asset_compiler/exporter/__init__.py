"""
Three.js Animation and Character Exporter package initialization.
"""

from .properties import TJS_Properties, PORTFOLIO_OUTPUT_DIRECTORY
from .operators import TJS_OT_ExportCharacterGLB, TJS_OT_ExportAnimations

EXPORTER_CLASSES = [
    TJS_Properties,
    TJS_OT_ExportCharacterGLB,
    TJS_OT_ExportAnimations,
]
