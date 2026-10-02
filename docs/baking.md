# Texture & Lightmap Baking Guide

The baking stage unwraps, packs, verifies and bakes UVs and textures for
multiple objects at once, then rewires materials to the result.

## Image Modes

- **Single Atlas**: All objects in scope are unwrapped and packed into one shared texture map (e.g., `bake_shared.png`). Ideal for draw-call reduction in web scenes.
- **Auto Tiles**: Distributes objects across multiple numbered images (`bake_tile_01.png`, `bake_tile_02.png`) by **world-space** surface area, so a scaled object is weighted by its real size and keeps the same texel density as everything else.
- **Collection Atlases**: Uses collection naming prefixes (`Texture_Pack_1`, `Texture_Pack_2`) to group objects into numbered atlases.
- **Per Material**: Generates one individual image per material name.

## UV Unwrapping & Texel Density

- **Smart UV Project**: Angle-based automatic unwrapping. It produces island *shapes*; non-overlap and layout are the packer's job, so always follow it with packing.
- **Auto Seam Unwrap**: Marks seams at sharp edges, keeping smooth curves together before unwrapping.
- **World-Space Proportional Packing**: Scales UV islands proportional to their real 3D surface area. A 5m wall receives 50× more UV pixels than a 10cm trim piece, ensuring uniform texel density across the entire asset.
- **Image Texture Boost**: Multiplies UV space for materials containing image textures to preserve fine detail.

## Gutter & Pack Verification

`Pack Margin` is the clear width you want **between islands**, honoured to the pixel.
Blender applies its pack margin to each island side, so a naive value produces
roughly double the gutter and wastes about half the atlas — the value here is
already halved for you.

After each group is packed the result is measured and reported:

```
[Atlas] UV pack verified: 24 island(s), gutter 16.0px, range 0.016-0.984
```

- UVs outside the `0-1` tile are an **error**, reported with how many pixels bleed
  into the neighbouring atlas.
- A gutter narrower than requested is a **warning**; the fix is a larger resolution
  or a smaller margin, not a different setting. The bake filter inset is
  automatically clamped to half the gutter actually achieved, so it can never
  exceed the gap it sits in.
- A dense atlas genuinely cannot hold a wide gutter (1920 islands at 1024px get
  none). That is reported honestly rather than hidden.

Atlas packing uses Blender's own packer. `Shape Method` defaults to **Concave**;
Convex is faster with slightly lower density, and *Bounding Box* is much faster
still. Packing is the slowest part of a build by design — correctness first.

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

## Cancelling a Bake

Baking runs as a Blender job rather than a blocking call, so the interface stays
responsive and reports progress. Press <kbd>Esc</kbd> to abort, or click
**Cancel Build** in the panel while a build is in flight; either stops at the next
step boundary and reports `Bake cancelled.` A cancelled build never reaches export.

UV setup runs the same way and yields between steps. The longest uninterruptible
unit is a single pack pass, since Blender's pack operators have no asynchronous
path at all.
