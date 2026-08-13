# Contributing to Web3D Asset Compiler

Thank you for your interest in contributing to **Web3D Asset Compiler**!

## Development Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/rocky/web3d-asset-compiler.git
   cd web3d-asset-compiler
   ```

2. **Local Addon Installation for Testing**:
   Link or copy `addon/web3d_asset_compiler` into your Blender addons folder:
   - **Windows**: `%APPDATA%\Blender Foundation\Blender\5.1\scripts\addons\`
   - **macOS**: `~/Library/Application Support/Blender/5.1/scripts/addons/`
   - **Linux**: `~/.config/blender/5.1/scripts/addons/`

3. **Running Repository Validation**:
   ```bash
   python scripts/validate_addon.py
   ```

4. **Running Unit Tests**:
   ```bash
   python -m unittest discover -s tests
   ```

5. **Headless Blender Addon Registration Test**:
   If Blender is installed on your system:
   ```bash
   blender --background --python tests/test_blender_addon_register.py
   ```

6. **Building the Installable Addon ZIP**:
   ```bash
   python scripts/build_addon.py
   ```
   The built archive will be saved to `dist/web3d_asset_compiler.zip`.

## Code Guidelines

- **Preserve Existing Functionality**: Ensure no regressions occur in baking or exporting pipelines.
- **Graceful Fallbacks**: Optional third-party software (such as UVPackmaster or Draco) must degrade gracefully when missing.
- **Single Source of Versioning**: Versioning must be updated in `addon/web3d_asset_compiler/version.py`.
- **Formatting**: Follow PEP 8 guidelines for Python code readability.

## Submitting Pull Requests

1. Fork the repository and create a feature branch (`git checkout -b feature/my-feature`).
2. Run validation scripts and ensure all tests pass.
3. Commit your changes with clear messages (`git commit -m "Add feature X"`).
4. Push to your branch and open a Pull Request.
