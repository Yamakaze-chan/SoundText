"""Static contracts for the CTk frontend; do not require the GUI package."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_ctk_is_primary_and_qt_is_not_installed_dependency():
    main = (ROOT / 'main.py').read_text(encoding='utf-8')
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    launcher = (ROOT / 'SoundText.bat').read_text(encoding='utf-8')
    assert 'from app.ui.ctk_window import run_ctk_application' in main
    assert 'customtkinter>=5.2,<6' in requirements
    assert 'PySide6' not in requirements
    assert 'Install CustomTkinter interface only' in launcher
    assert 'PySide6' not in launcher
    assert len(list(ROOT.glob('*.bat'))) == 1


def test_ctk_ui_owns_only_presentation_and_session_controller():
    code = (ROOT / 'app/ui/ctk_window.py').read_text(encoding='utf-8')
    tree = ast.parse(code)
    names = {n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    assert {'SoundTextWindow', 'FloatingSubtitles', 'SubtitlePane', 'run_ctk_application'} <= set(names)
    assert 'BrandingDialog' not in code
    assert '_branding_dialog' not in code
    assert 'RecordingController(self.bus)' in code
    assert 'self.controller.start(self.config)' in code
    assert 'self.controller.stop()' in code
    assert 'self.overlay.update_partial' in code
    assert 'self.overlay.update_row' in code
    assert 'self._next_card_row' in code
    # Never directly instantiate MeetingSTTPipeline in the GUI constructor.
    assert 'MeetingSTTPipeline(' not in code


def test_no_qt_code_remains_in_primary_ui():
    assert not (ROOT / 'app/ui/qt_window.py').exists()
    code = (ROOT / 'app/ui/ctk_window.py').read_text(encoding='utf-8')
    assert 'PySide6' not in code
