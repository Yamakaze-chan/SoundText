"""EXE-preparation regression checks: no Windows build tools or ML weights needed."""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys

from PIL import Image

from app import runtime_paths
from app.ui import branding
from app.ui.splash_assets import choose_splash_art


def test_portable_exe_uses_executable_folder_not_pyinstaller_internal(monkeypatch, tmp_path):
    exe = tmp_path / 'SoundText' / 'SoundText.exe'
    internal = exe.parent / '_internal'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    monkeypatch.setattr(sys, '_MEIPASS', str(internal), raising=False)
    assert runtime_paths.app_root() == exe.parent
    assert runtime_paths.bundled_root() == internal
    runtime_paths.configure_runtime(change_directory=False)
    assert Path(os.environ['HF_HOME']) == exe.parent / 'models' / '_hf_cache'
    assert Path(os.environ['TORCH_HOME']) == exe.parent / 'models' / '_torch_cache'
    assert Path(os.environ['SOUNDTEXT_ROOT']) == exe.parent


def test_splash_is_exactly_user_configured_art_and_not_stock(monkeypatch, tmp_path):
    assets = tmp_path / 'assets' / 'branding'
    assets.mkdir(parents=True)
    custom = assets / 'my-unique-splash.png'
    stock = assets / 'soundtext-splash.png'
    Image.new('RGB', (64, 32), (255, 40, 16)).save(custom)
    Image.new('RGB', (64, 32), (16, 40, 255)).save(stock)
    monkeypatch.setattr(branding, 'PROJECT_ROOT', tmp_path)
    cfg_path = tmp_path / 'branding.json'
    cfg_path.write_text(json.dumps({'splash_path':'assets/branding/my-unique-splash.png',
                                     'show_splash':True}), encoding='utf8')
    monkeypatch.setattr(branding, 'BRANDING_FILE', cfg_path)
    settings = branding.load_branding()
    path, warning = choose_splash_art(settings)
    assert path == custom
    assert warning is None
    assert Image.open(path).getpixel((0, 0)) == (255, 40, 16)
    settings['splash_path'] = 'assets/branding/not-found.png'
    path, warning = choose_splash_art(settings)
    assert path is None and 'not-found.png' in warning


def test_user_broken_branding_json_is_not_silently_accepted(monkeypatch, tmp_path):
    f = tmp_path / 'branding.json'
    f.write_text('{"splash_path":"C:\\my\\splash.png"}', encoding='utf8')  # invalid JSON escape
    monkeypatch.setattr(branding, 'BRANDING_FILE', f)
    assert branding.branding_file_error() is not None
    f.write_text('{"splash_path": "assets/branding/demo.png"}', encoding='utf8')
    assert branding.branding_file_error() is None


def test_windows_exe_metadata_generated_from_branding(tmp_path, monkeypatch):
    from scripts import prepare_exe_metadata as gen
    monkeypatch.setattr(gen, 'ROOT', tmp_path)
    (tmp_path / 'branding.json').write_text(json.dumps({
        'app_name': 'My Meeting Notes',
        'publisher': 'Personal Studio',
        'app_version': '2.5.7',
    }), encoding='utf8')
    resource = gen.make_version_file().read_text(encoding='utf8')
    assert '(2, 5, 7, 0)' in resource
    assert 'My Meeting Notes' in resource
    assert 'Personal Studio' in resource


def test_build_script_and_spec_keep_models_external():
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'SoundText.spec').read_text(encoding='utf8')
    build = (root / 'scripts/build_windows_exe.bat').read_text(encoding='utf8')
    assert "[str(ROOT / 'entry_soundtext.py')]" in spec
    assert "ROOT / 'models'" not in spec  # no explicit model checkpoints in datas
    assert 'COLLECT(' in spec
    assert 'console=False' in spec
    assert 'models\\README.txt' in build
    assert 'dist\\SoundText\\models' in build
    assert len(list(root.glob('*.bat'))) == 1


def test_update_keeps_existing_branding_and_processing_preferences(tmp_path):
    from app.user_config import ensure_user_configs
    (tmp_path / 'branding.default.json').write_text('{"splash_path":"stock.png"}',encoding='utf8')
    (tmp_path / 'config_stt_vi_ja.default.json').write_text('{"meeting":{"audio_mode":"both"}}',encoding='utf8')
    user_brand = tmp_path / 'branding.json'
    user_brand.write_text('{"splash_path":"my-own-art.png"}',encoding='utf8')
    user_config = tmp_path / 'config_stt_vi_ja.json'
    user_config.write_text('{"meeting":{"audio_mode":"remote"}}',encoding='utf8')
    assert ensure_user_configs(tmp_path) == []
    assert json.loads(user_brand.read_text(encoding='utf8'))['splash_path'] == 'my-own-art.png'
    assert json.loads(user_config.read_text(encoding='utf8'))['meeting']['audio_mode'] == 'remote'
    user_brand.unlink()
    assert ensure_user_configs(tmp_path) == [user_brand]
    assert json.loads(user_brand.read_text(encoding='utf8'))['splash_path'] == 'stock.png'
