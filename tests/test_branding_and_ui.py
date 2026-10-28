from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.ui import branding


def test_branding_defaults_and_roundtrip(tmp_path):
    cfg = tmp_path / 'branding.json'
    with patch.object(branding, 'BRANDING_FILE', cfg):
        defaults = branding.load_branding()
        assert defaults['app_name'] == 'SoundText'
        branding.save_branding({**defaults, 'app_name':'  Chương trình của tôi  ', 'show_splash':False})
        got = branding.load_branding()
        assert got['app_name'] == 'Chương trình của tôi'
        assert got['show_splash'] is False
        assert json.loads(cfg.read_text(encoding='utf-8'))['app_name'] == 'Chương trình của tôi'


def test_brand_image_import(tmp_path):
    asset_dir = tmp_path / 'assets'
    source = tmp_path / 'custom.png'
    source.write_bytes(b'PNG placeholder')
    with patch.object(branding, 'ASSET_DIR', asset_dir), patch.object(branding, 'PROJECT_ROOT', tmp_path):
        rel = branding.store_brand_asset(str(source), 'icon')
        assert rel == 'assets/custom-icon.png'
        assert (asset_dir / 'custom-icon.png').read_bytes() == source.read_bytes()


def test_open_folder_is_actual_session_folder(tmp_path):
    folder = tmp_path / 'output' / 'meeting_test'
    with patch.object(branding, 'PROJECT_ROOT', tmp_path), patch.object(branding.subprocess, 'Popen') as popen:
        result = branding.open_session_directory('output/meeting_test')
    assert result == folder
    assert folder.is_dir()
    popen.assert_called_once()


def test_window_branding_and_overlay_smoke():
    import tkinter as tk
    from app.ui.meeting_window import MeetingUIBus, MeetingWindow, UIEvent
    try:
        win = MeetingWindow(MeetingUIBus(), title='SoundText test', session_dir='output/test_session')
    except tk.TclError:
        import pytest
        pytest.skip('No graphical environment')
    try:
        assert win.brand_title.cget('text')
        assert win.brand_subtitle.cget('text')
        assert win.BG == '#F3F6FA'
        win._open_branding_settings()
        assert any(isinstance(child, tk.Toplevel) for child in win.root.winfo_children())
        for child in win.root.winfo_children():
            if isinstance(child, tk.Toplevel): child.destroy()
        win.subtitle_enabled.set(True)
        win._toggle_subtitle_overlay()
        win._finish_open_subtitle_overlay()
        win.root.update_idletasks()
        assert win.subtitle_vi_window is not None
        win._destroy_subtitle_overlay()
    finally:
        win._close()
        win.root.update()
