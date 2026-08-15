# Texture & Lightmap Baking Guide

Auto HDR Baker provides automated multi-object lightmap and texture baking.

## Image Modes

- **Single Atlas**: All objects in scope are unwrapped and packed into one shared texture map (e.g., `bake_shared.png`). Ideal for draw-call reduction in web scenes.
- **Auto Tiles**: Automatically distributes objects across multiple numbered images (`bake_tile_01.png`, `bake_tile_02.png`) based on 3D surface area balance.
- **Collection Atlases**: Uses collection naming prefixes (`Texture_Pack_1`, `Texture_Pack_2`) to group objects into numbered atlases.
- **Per Material**: Generates one individual image per material name.

## UV Unwrapping & Texel Density

- **Smart UV Project**: Automatic angle-based unwrapping guaranteeing non-overlapping islands.
- **Auto Seam Unwrap**: Automatically marks seams at sharp edges, keeping smooth curves together before unwrapping.
- **World-Space Proportional Packing**: Scales UV islands proportional to their real 3D surface area. A 5m wall receives 50× more UV pixels than a 10cm trim piece, ensuring uniform texel density across the entire asset.
- **Image Texture Boost**: Multiplies UV space for materials containing image textures to preserve fine detail.

## Selected-to-Active High-to-Low Poly Baking

1. Select your high-poly source objects.
2. Select your low-poly target object last so it becomes the Active object.
3. Enable **Selected to Active** under the baking settings.
4. Adjust **Cage Extrusion** and **Max Ray Distance**.
5. Optionally enable **Use Custom Cage** and choose a cage mesh.
6. Click **Bake All**.

## Additional Bake Targets

- **Color Attributes**: Bake directly to the active corner-domain color attribute and apply it through a Color Attribute shader node.
- **Multires Normal Bake**: Enable Multires for image-target Normal bakes when each target has a Multires modifier. Multires and Selected-to-Active are mutually exclusive.
- **Internal Images**: Choose the internal save mode to keep baked images in the `.blend`; external mode writes them to the configured output directory.
