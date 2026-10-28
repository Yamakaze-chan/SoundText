"""Branding regression tests (no CustomTkinter or sound models required)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageTk

from app.ui import branding, window_icon


class FakeWindow:
    def __init__(self):
        self.photos = []
        self.bitmaps = []
        self.callbacks = []
        self.alive = True

    def iconphoto(self, default, image):
        self.photos.append((default, image))

    def iconbitmap(self, path):
        assert Path(path).is_file()
        self.bitmaps.append(Path(path))

    def after(self, ms, fn):
        self.callbacks.append((ms, fn))

    def winfo_exists(self):
        return self.alive


def test_windows_icon_from_branding_png_is_converted_to_ico_and_reapplied(tmp_path, monkeypatch):
    assets = tmp_path / 'assets' / 'branding'
    assets.mkdir(parents=True)
    png = assets / 'biểu tượng mới.png'
    Image.new('RGBA', (512, 512), (30, 130, 100, 230)).save(png)
    monkeypatch.setattr(branding, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(window_icon, 'ASSET_DIR', assets)
    monkeypatch.setattr(window_icon.sys, 'platform', 'win32')
    # Avoid constructing real Tk windows; exercise PhotoImage bindings.
    monkeypatch.setattr(ImageTk, 'PhotoImage', lambda image, master: object())

    manager = window_icon.WindowIconManager('assets/branding/biểu tượng mới.png')
    window = FakeWindow()
    manager.install(window)
    assert window.bitmaps
    with Image.open(window.bitmaps[-1]) as ico:
        assert ico.format == 'ICO'
    assert window.photos[-1][0] is True
    assert window._soundtext_icon_photo is window.photos[-1][1]
    assert [ms for ms, _ in window.callbacks] == [350, 850]
    first_ico = window.bitmaps[-1]
    for _, callback in window.callbacks:
        callback()
    assert window.bitmaps[-1] == first_ico
    # Recreating the app with the same icon reuses the cache.
    assert window_icon.WindowIconManager(str(png))._windows_ico() == first_ico


def test_ico_path_used_directly_and_distinct_panes_get_icon(tmp_path, monkeypatch):
    assets = tmp_path / 'branding'
    assets.mkdir()
    ico = assets / 'new-brand.ico'
    Image.new('RGBA', (256, 256), '#336699').save(ico, format='ICO')
    monkeypatch.setattr(window_icon.sys, 'platform', 'win32')
    monkeypatch.setattr(ImageTk, 'PhotoImage', lambda image, master: object())
    manager = window_icon.WindowIconManager(str(ico))
    windows = [FakeWindow(), FakeWindow(), FakeWindow()]
    for window in windows:
        manager.install(window)
        assert window.bitmaps[-1] == ico
        assert window.photos


def test_invalid_asset_prints_warning_and_does_not_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(branding, 'PROJECT_ROOT', tmp_path)
    manager = window_icon.WindowIconManager('assets/branding/does-not-exist.png')
    window = FakeWindow()
    manager.install(window)
    assert not window.photos and not window.bitmaps and not window.callbacks
    assert 'Icon not found' in capsys.readouterr().out


def test_after_close_does_not_touch_icon(tmp_path, monkeypatch):
    png = tmp_path / 'logo.png'
    Image.new('RGBA', (16, 16), 'green').save(png)
    monkeypatch.setattr(window_icon.sys, 'platform', 'linux')
    monkeypatch.setattr(ImageTk, 'PhotoImage', lambda image, master: object())
    manager = window_icon.WindowIconManager(str(png))
    window = FakeWindow()
    manager.install(window)
    window.alive = False
    n = len(window.photos)
    for _, callback in window.callbacks:
        callback()
    assert len(window.photos) == n


def test_app_icons_do_not_change_audio_or_translation_code():
    source = (Path(__file__).resolve().parents[1] / 'app/ui/ctk_window.py').read_text('utf-8')
    assert 'self._window_icon.install(self)' in source
    assert 'owner.parent._window_icon.install(self)' in source
    assert 'configure_windows_app_identity()' in source
