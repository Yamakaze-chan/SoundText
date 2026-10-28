# -*- mode: python ; coding: utf-8 -*-
"""Windows portable one-folder build. Run only on Windows in the target venv.

User configuration, branding art, model weights and output stay OUTSIDE the
bundle, next to SoundText.exe. PyInstaller's private dependencies go _internal/.
We do not add PyInstaller's own splash, because the application displays the
user-selected branding.json splash while models are preloaded.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()

# FunASR registers implementations dynamically, as does Optimum. These are
# actual Python package modules, NOT model checkpoints or downloaded weights.
hiddenimports = [
    # Explicitly package the top-level module used by entry_soundtext.py.
    # Some PyInstaller analysis configurations can otherwise omit it.
    'main', 'app.asr.deferred_engine',
    'pyaudiowpatch', 'sherpa_onnx', 'funasr',
    'optimum.onnxruntime', 'transformers.models.nllb',
    'sentencepiece', 'onnxruntime',
]
for package in ('funasr', 'optimum.onnxruntime', 'transformers.models.nllb'):
    hiddenimports.extend(collect_submodules(package, on_error='warn once'))
hiddenimports = sorted(set(hiddenimports))

# FunASR registries and Transformers/TorchScript may inspect Python source
# at *import* time. Keeping only .pyc in PYZ can produce:
# OSError: could not get source code (not an ONNX weights error).
# PyInstaller 6.x recommends module_collection_mode for this use case.
# Keep both frozen bytecode and readable .py files for packages used by
# SenseVoice and NLLB/Optimum. This collects Python code, NOT model weights.
# 'optimum' also covers optimum.onnxruntime provided by optimum-onnx.
module_collection_mode = {
    'funasr': 'pyz+py',
    'torch': 'pyz+py',
    'transformers': 'pyz+py',
    'optimum': 'pyz+py',
}
# FunASR reads its version from a physical file beside funasr/__init__.py.
# Freezing its Python modules (pyz+py) does NOT automatically include data.
# Collect this file in _internal/funasr/version.txt for the onedir EXE.
funasr_version_data = collect_data_files('funasr', includes=['version.txt'])
if not any(
    Path(src).name == 'version.txt' and Path(dest).as_posix() == 'funasr'
    for src, dest in funasr_version_data
):
    raise RuntimeError(
        'FunASR version.txt not found in the active Python environment. '
        'Please verify the soundtext-build environment before building.'
    )
datas = collect_data_files('customtkinter') + funasr_version_data

icon = ROOT / 'build' / 'soundtext-exe.ico'
version_info = ROOT / 'build' / 'soundtext-version.txt'

analysis = Analysis(
    [str(ROOT / 'entry_soundtext.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'jupyterlab'],
    noarchive=False,
    module_collection_mode=module_collection_mode,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name='SoundText',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(version_info) if version_info.is_file() else None,
)
coll = COLLECT(
    exe, analysis.binaries, analysis.datas,
    strip=False, upx=False,
    name='SoundText',
)
