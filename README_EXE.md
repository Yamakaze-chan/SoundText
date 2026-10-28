# SoundText — Portable EXE (Windows)

**Build type:** PyInstaller `--onedir`-style bundle. **Do not use `--onefile`** for this CPU-only app: large Torch/ONNX dependencies and multi-GB model files make extraction slow and diagnostics difficult. The `.exe` is accompanied by an `_internal/` folder containing runtime dependencies.

## Directory layout

```text
SoundText/                    <-- whole folder is the portable application
├── SoundText.exe              <-- generated on Windows
├── _internal/                 <-- PyInstaller runtime; do not edit
├── branding.json             <-- editable name, icon, splash
├── config_stt_vi_ja.json     <-- editable processing config
├── assets/
│   ├── default-branding/     <-- packaged stock art; safe to replace on rebuild
│   └── branding/             <-- user-supplied PNG/ICO art, if any
├── models/                    <-- COPY your existing downloaded model files here
│   ├── gipformer1.5-65m-rnnt/
│   ├── sensevoice-small/
│   ├── silero-vad/
│   ├── nllb-trilingual-en-vi-ja-onnx/
│   ├── speaker-diarization/
│   └── speaker-embedding/
└── output/                   <-- TXT, sessions, logs
    └── logs/startup.log
```

`models/` is deliberately not included in the ZIP or executable. It must be copied from your existing installation. The runtime stays offline and never downloads models during startup. In an onedir EXE, the entire `SoundText/` folder must be distributed, not just `SoundText.exe`.

## Build on Windows 10/11 x64

1. Use a **64-bit Python 3.11** virtual environment with all `requirements.txt` installed and working. Install the Microsoft Visual C++ runtime only if your specific PyTorch/ONNX dependencies require it.
2. Edit `branding.json` first. Copy your image into `assets/branding/` and prefer a portable relative path, e.g. `"splash_path": "assets/branding/my-splash.png"`. **Windows paths in JSON must use `/` or escaped backslashes `\\`.**
3. Double-click `scripts/build_windows_exe.bat`. It creates the embedded EXE icon from `icon_path`, embeds Windows Explorer icon + version/publisher metadata, builds the EXE, and copies editable config/art into `dist/SoundText/`.
4. Copy your current `models/` **contents** into `dist/SoundText/models/`. They are not bundled, to avoid copying huge model weights into `_internal/`.
5. Run `dist/SoundText/SoundText.exe` and test splash, recording, translation, dual overlay, and TXT export. Launch from a Windows shortcut as well (different working directory).
6. Look at `dist/SoundText/output/logs/startup.log` for startup/model warnings if the app does not open.

## Splash and icon

- The application loads its custom splash **from external `branding.json`** and keeps it on screen while selected models are preloaded. The app opens **idle**, not recording.
- If the configured splash is missing/corrupted, the loading window shows a visible warning and the log records the failing path. It no longer silently swaps in a stock splash image.
- `branding.json` can change the **window icon after installation**. The **Windows Explorer EXE icon and Details metadata** (`app_name`, `app_version`, `publisher`) are embedded at build time; re-run `scripts/build_windows_exe.bat` to change those. The runtime splash/window icon can be changed by editing `branding.json` without rebuilding.
- `splash_duration_ms` is legacy metadata; model loading—not this number—decides when splash closes.

## Deployment cautions

- Do not install under `C:\\Program Files` without an installer that relocates writable `models/` and `output/`; use a writable folder such as your Desktop or Documents for this portable build.
- Native `.pyd`/`.dll` libraries (Torch, FunASR, sherpa-onnx, ONNX Runtime, PyAudioWPatch) must be validated on a real Windows machine. A successful PyInstaller command does **not** guarantee functional capture/inference.
- If other files in your installation use absolute paths outside `SoundText/`, convert them to portable relative paths before distributing.
- Do not enable PyInstaller's own `--splash` option; that would create a **second**, build-time splash independent of `branding.json`.

## Updating the source ZIP without losing your choices

The distributed source update contains only `branding.default.json` and `config_stt_vi_ja.default.json`; it deliberately omits the live `branding.json` / `config_stt_vi_ja.json` so extracting over an older version will not overwrite your custom splash or audio settings. On first run, missing live config files are generated from the templates. Preserve any custom images in `assets/branding/`.
