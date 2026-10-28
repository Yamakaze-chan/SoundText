"""Regression contracts for the independent floating subtitle windows."""
import importlib
import sys
import types
from unittest.mock import MagicMock


def load_ui(monkeypatch):
    fake = types.ModuleType('customtkinter')
    for name in ('CTkToplevel', 'CTk', 'CTkFrame', 'CTkLabel', 'CTkButton',
                 'CTkFont', 'CTkOptionMenu', 'CTkCheckBox', 'CTkScrollbar',
                 'CTkProgressBar', 'CTkEntry', 'CTkScrollableFrame'):
        setattr(fake, name, type(name, (object,), {}))
    fake.set_appearance_mode = lambda *_: None
    fake.set_default_color_theme = lambda *_: None
    monkeypatch.setitem(sys.modules, 'customtkinter', fake)
    sys.modules.pop('app.ui.ctk_window', None)
    return importlib.import_module('app.ui.ctk_window')


def sample_entries():
    return [
        dict(source='system', id=1, original='こんにちは', translation='', lang='ja', speaker='S1'),
        dict(source='mic', id=1, original='Chào mọi người', translation='', lang='vi', speaker='S1'),
        dict(source='system', id=2, original='Good morning', translation='Chào buổi sáng', lang='en', speaker='S2'),
    ]


def build_overlay(ui, view='Cả hai', enabled=True):
    overlay = object.__new__(ui.FloatingSubtitles)
    overlay.stt = MagicMock()
    overlay.vi = MagicMock()
    overlay.stt.winfo_viewable.return_value = False
    overlay.vi.winfo_viewable.return_value = False
    overlay.view = MagicMock()
    overlay.view.get.return_value = view
    overlay.translation_enabled = enabled
    overlay._view_pickers = []
    overlay._render = MagicMock()
    overlay._emit_visibility = MagicMock()
    return overlay


def test_stt_and_translation_are_independent(monkeypatch):
    ui = load_ui(monkeypatch)
    overlay = object.__new__(ui.FloatingSubtitles)
    overlay.entries = sample_entries()
    overlay.partials = {'mic': ('Xin chào', 'vi'), 'system': ('How are you', 'en')}
    stt = overlay._text_for('stt')
    vi = overlay._text_for('vi')
    assert 'こんにちは' in stt and 'Good morning' in stt
    assert 'How are you' in stt
    assert 'こんにちは' not in vi and 'How are you' not in vi
    assert 'Chào buổi sáng' in vi and 'Chào mọi người' in vi
    assert 'Xin chào' in vi
    assert 'REMOTE · S2' in vi


def test_two_panes_open_close_independently(monkeypatch):
    ui = load_ui(monkeypatch)
    overlay = build_overlay(ui)
    overlay.show_overlay()
    overlay.stt.deiconify.assert_called_once()
    overlay.vi.deiconify.assert_called_once()
    overlay.withdraw()
    overlay.stt.withdraw.assert_called_once()
    overlay.vi.withdraw.assert_called_once()
    overlay._emit_visibility.assert_any_call(False)


def test_translation_only_keeps_vi_window(monkeypatch):
    ui = load_ui(monkeypatch)
    overlay = build_overlay(ui, view='Chỉ bản dịch')
    overlay.show_overlay()
    overlay.vi.deiconify.assert_called_once()
    overlay.stt.withdraw.assert_called_once()
    overlay.stt.deiconify.assert_not_called()


def test_transcript_only_keeps_stt_window(monkeypatch):
    ui = load_ui(monkeypatch)
    overlay = build_overlay(ui, view='Chỉ lời thoại', enabled=False)
    overlay.show_overlay()
    overlay.stt.deiconify.assert_called_once()
    overlay.vi.withdraw.assert_called_once()
    overlay.vi.deiconify.assert_not_called()
    overlay.stt.winfo_viewable.return_value = True
    assert overlay.winfo_viewable()


def test_disabled_translation_restricts_subtitle_choices(monkeypatch):
    ui = load_ui(monkeypatch)
    overlay = build_overlay(ui, view='Chỉ bản dịch', enabled=False)
    assert overlay.available_views() == ['Chỉ lời thoại']
    overlay.show_overlay()
    overlay.view.set.assert_called_with('Chỉ lời thoại')
    overlay.stt.deiconify.assert_called_once()
    overlay.vi.withdraw.assert_called_once()


def test_file_only_branding(monkeypatch):
    ui = load_ui(monkeypatch)
    from app.ui.branding import BRANDING_FILE, load_branding
    assert BRANDING_FILE.name == 'branding.json'
    assert 'app_name' in load_branding()
    assert 'icon_path' in load_branding()
    assert 'splash_path' in load_branding()
    assert not hasattr(ui.SoundTextWindow, '_branding_dialog')
    assert not hasattr(ui, 'BrandingDialog')
    assert not hasattr(ui.SoundTextWindow, '_reload_branding')
