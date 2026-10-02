# Installation Guide

This guide covers installing and configuring **Web3D Asset Compiler** in Blender.

## Prerequisites

- **Blender**: Version 5.1.0 or higher.
- **Operating System**: Windows, macOS, or Linux.
- **GPU (Recommended)**: NVIDIA RTX/GTX with OptiX/CUDA, AMD GPU with HIP, Intel GPU with oneAPI, or Apple Silicon with Metal.

## Step-by-Step Installation

1. **Download Release**:
   Download `web3d_asset_compiler-1.0.0.zip` from the GitHub Releases page, or build it locally using `python scripts/build_extension.py`.

2. **Open Blender**:
   Launch Blender 5.1+.

3. **Open Preferences**:
   Go to the top menu bar and select **Edit ➔ Preferences**.

4. **Navigate to Add-ons**:
   Click on **Add-ons** in the left sidebar menu.

5. **Install Addon**:
   - Click **Install...** (or **Install from Disk...**) at the top right of the Preferences window.
   - Browse to your downloaded `web3d_asset_compiler-1.0.0.zip` file and select it.
   - Click **Install Add-on**.

6. **Enable Addon**:
   - In the search bar at the top right of the Add-ons panel, type `Web3D`.
   - Check the checkbox next to **Import-Export: Web3D Asset Compiler**.

7. **Verify Panel Location**:
   - In the 3D Viewport, press `N` to toggle the Sidebar.
   - Click on the **Web3D** tab to access the main user interface.
   - Alternatively, open **Properties Editor ➔ Render Properties** to locate the embedded render controls.

## Optional External Integrations

- **Draco mesh compression** (export only): enabled per-export under
  **Export ➔ Mesh Compression**. When Blender's glTF exporter has Draco
  available it is used; otherwise the export falls back to uncompressed mesh
  data and says so.

Atlas packing always uses Blender's own UV packer. No third-party packer is
installed, downloaded, or called.
