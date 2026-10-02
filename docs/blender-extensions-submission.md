# Blender Extensions Submission Checklist & Guide

This document covers preparing, building, verifying, and manually submitting **Web3D Asset Compiler** to the official Blender Extensions platform ([extensions.blender.org](https://extensions.blender.org/)).

---

## Submission Checklist

- [x] **`blender_manifest.toml` Metadata Valid**: Contains `schema_version`, `id`, `version`, `name`, `tagline`, `maintainer`, `type`, `blender_version_min`, `license`, `tags`, and `[permissions]`.
- [x] **License Compliant**: Licensed under `SPDX:GPL-3.0-or-later`.
- [x] **Version Consistency**: `blender_manifest.toml` is the single source of truth; the build script reads it to name the artifact.
- [x] **Blender Compatibility**: Verified against Blender 5.1+.
- [x] **Package Structure**: Extension archive contains `blender_manifest.toml` and code at the root of `dist/web3d_asset_compiler-1.0.0.zip`.
- [x] **No Forbidden Development Files**: `__pycache__`, `.git`, `.github`, `.venv`, `venv`, `dist`, `build`, `.blend`, and `.pyc` are excluded from the distribution artifact.
- [x] **No Secrets or Hardcoded Machine Paths**: Scanned and verified clean.
- [x] **Graceful Dependencies**: Optional tools (Draco) degrade gracefully without breaking extension registration or core functionality.
- [x] **No Bundled Third-Party Binaries**: Atlas packing uses Blender's own packer only.
- [x] **Clean Installation & Uninstallation**: Registration and unregistration tested cleanly in headless Blender 5.1 without errors or memory leaks.
- [x] **Baking & Exporter Subsystems Verified**: Auto HDR Baker and Three.js Exporter operators verified active and working.

---

## How to Build the Extension Artifact

Run the automated extension build script:

```bash
blender --command extension validate addon/web3d_asset_compiler --valid-tags=""
python scripts/build_extension.py
```

Output location:
`dist/web3d_asset_compiler-1.0.0.zip`

---

## Manual Steps on extensions.blender.org

1. **Log in to Blender Extensions**:
   Navigate to [https://extensions.blender.org/](https://extensions.blender.org/) and log in with your Blender ID account.
2. **Submit New Extension**:
   Click on **Submit Extension** or go to your user profile ➔ **My Extensions** ➔ **Upload Extension**.
3. **Upload ZIP Artifact**:
   Upload `dist/web3d_asset_compiler-1.0.0.zip`.
4. **Verify Metadata**:
   The platform will parse `blender_manifest.toml` automatically to populate the listing title, tagline, maintainer, license, tags, and minimum Blender version (`5.1.0`).
5. **Add Screenshots & Marketing Material**:
   - Upload high-resolution screenshots of the **Web3D** N-Panel in action.
   - Upload sample baked renders and Three.js web asset previews.
6. **Submit for Moderation**:
   Submit the listing for community/staff review. Once approved, the extension will be publicly downloadable and directly installable within Blender 4.2+ and 5.1+ via **Preferences ➔ Get Extensions**.
