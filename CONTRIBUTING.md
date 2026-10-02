# Contributing to Web3D Asset Compiler

Thank you for your interest in contributing to **Web3D Asset Compiler** by Abdul Rafay Khalid!

## Development Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/ARafayKhalid/web3d-asset-compiler.git
   cd web3d-asset-compiler
   ```

2. **Local Extension Installation for Testing**:
   Link or copy `addon/web3d_asset_compiler` into your Blender extensions folder:
   - **Windows**: `%APPDATA%\Blender Foundation\Blender\5.1\extensions\user_default\web3d_asset_compiler\`
   - **macOS**: `~/Library/Application Support/Blender/5.1/extensions/user_default/web3d_asset_compiler/`
   - **Linux**: `~/.config/blender/5.1/extensions/user_default/web3d_asset_compiler/`

3. **Running Extension Validation**:
   ```bash
   blender --command extension validate addon/web3d_asset_compiler --valid-tags=""
   ```

4. **Running Unit Tests**:
   ```bash
   python -m unittest discover -s tests
   ```

5. **Headless Blender Addon Registration & Pipeline Smoke Tests**:
   ```bash
   blender --background --factory-startup --python tests/test_blender_addon_register.py
   blender --background --factory-startup --python tests/test_blender_pipeline_smoke.py
   ```

6. **Building the Installable Extension ZIP**:
   ```bash
   python scripts/build_extension.py
   ```
   The archive name is read from `blender_manifest.toml`, so bumping the
version there is enough: `dist/web3d_asset_compiler-<version>.zip`.

## Code Guidelines

- **Preserve Existing Functionality**: Ensure no regressions occur in baking or exporting pipelines.
- **Graceful Fallbacks**: Optional third-party software (such as Draco) must degrade gracefully when missing.
- **Single Source of Versioning**: Versioning lives only in `addon/web3d_asset_compiler/blender_manifest.toml`; the build script derives the artifact name from it.
- **Formatting**: Follow PEP 8 guidelines for Python code readability.

## Submitting Pull Requests

1. Fork the repository and create a feature branch (`git checkout -b feature/my-feature`).
2. Run validation scripts and ensure all tests pass.
3. Commit your changes with clear messages (`git commit -m "Add feature X"`).
4. Push to your branch and open a Pull Request.

## Running the checks

The full set, all of which run in CI:

```bash
# Manifest, plus a syntax pass over the package
blender --command extension validate addon/web3d_asset_compiler --valid-tags=""
python scripts/build_extension.py

# Registration and the full bake/export pipeline
blender --background --factory-startup -noaudio --python-exit-code 1 \
  --python tests/test_blender_addon_register.py
blender --background --factory-startup -noaudio --python-exit-code 1 \
  --python tests/test_blender_pipeline_smoke.py

# Every UI panel, drawn with a real UILayout. Needs a display.
xvfb-run -a blender --factory-startup --python-exit-code 1 \
  --python tests/test_ui_draw.py

# The web examples, checked against real three.js / @react-three/fiber types
blender --background --factory-startup -noaudio --python-exit-code 1 \
  --python tests/export_fixtures.py
cd examples && npm install && npm run check
```

`tests/test_ui_draw.py` exists because Blender swallows exceptions raised inside
`Panel.draw`: a panel referencing a removed property keeps registering cleanly and
only breaks when somebody opens that tab. The export fixture exists because the
examples are the only documentation of the generated controller's API, and they
drift silently when that API changes.
