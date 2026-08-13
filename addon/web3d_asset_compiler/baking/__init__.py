"""
Baking module initialization.
"""

from .properties import AHB_Properties
from .operators import (
    AHB_OT_ExportMaterialsJSON,
    AHB_OT_ImportMaterialsJSON,
    AHB_OT_SetupMaterials,
    AHB_OT_BakeAll,
    AHB_OT_RebakeSelected,
    AHB_OT_ClearBakeNodes,
    AHB_OT_DeleteOldUVMaps,
    AHB_OT_RemoveAllUVMaps,
    AHB_OT_SaveImages,
    AHB_OT_QuickBake,
    AHB_OT_RenewAutoSeams,
    AHB_OT_PreviewUV,
    AHB_OT_PackIslands,
    AHB_OT_CleanupImages,
    AHB_OT_RemoveUnusedMaterials,
    AHB_OT_ApplyBakedTextures,
    AHB_OT_ToggleBakedView,
    AHB_OT_CreateAtlasCollections,
    AHB_OT_GroupToCollections,
    AHB_OT_RenameObjects,
    AHB_OT_RestoreOriginalMaterials,
)

BAKING_CLASSES = [
    AHB_Properties,
    AHB_OT_ExportMaterialsJSON,
    AHB_OT_ImportMaterialsJSON,
    AHB_OT_SetupMaterials,
    AHB_OT_BakeAll,
    AHB_OT_RebakeSelected,
    AHB_OT_ClearBakeNodes,
    AHB_OT_DeleteOldUVMaps,
    AHB_OT_RemoveAllUVMaps,
    AHB_OT_SaveImages,
    AHB_OT_QuickBake,
    AHB_OT_RenewAutoSeams,
    AHB_OT_PreviewUV,
    AHB_OT_PackIslands,
    AHB_OT_CleanupImages,
    AHB_OT_RemoveUnusedMaterials,
    AHB_OT_ApplyBakedTextures,
    AHB_OT_ToggleBakedView,
    AHB_OT_CreateAtlasCollections,
    AHB_OT_GroupToCollections,
    AHB_OT_RenameObjects,
    AHB_OT_RestoreOriginalMaterials,
]
