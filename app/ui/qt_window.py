"""SoundText Studio / Qt desktop UI.

The inference, diarization, translation and VAD code paths are unchanged.
This module is imported only for interactive runs and requires PySide6.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from queue import Empty
import sys
import time

from PySide6.QtCore import Qt, QSize, QTimer, QEvent, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap, QTextCursor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplashScreen, QSpinBox, QStackedWidget, QSplitter, QVBoxLayout,
    QWidget,
)

from app.ui.branding import (
    load_branding, save_branding, store_brand_asset, resolve_branding_path,
    open_session_directory,
)
from app.ui.meeting_window import MeetingUIBus, UIEvent
from app.ui.recording_controller import RecordingController

INK = '#172C3B'
MUTED = '#66788B'
BORDER = '#E0E7ED'
ACCENT = '#176A62'
CLOUD = '#F4F7F8'
SOFT_GREEN = '#E5F4EF'

STYLE = '''
QMainWindow, QWidget#Root { background: #F4F7F8; color: #172C3B; }
QWidget { font-family: "Segoe UI Variable", "Inter", "Segoe UI", "Noto Sans"; font-size: 13px; color: #172C3B; }
QFrame#Sidebar { background: #FFFFFF; border-right: 1px solid #E0E7ED; }
QFrame#Panel, QFrame#Stats, QFrame#LiveCard { background: #FFFFFF; border: 1px solid #E0E7ED; border-radius: 16px; }
QFrame#Thin { background: #E7EDF0; max-height: 1px; }
QLabel#Overline { font-size: 10px; font-weight: 800; color: #788A96; letter-spacing: 1.7px; }
QLabel#Title { font-size: 26px; font-weight: 750; color: #172C3B; }
QLabel#Section { font-size: 16px; font-weight: 750; color: #172C3B; }
QLabel#Hint { font-size: 12px; color: #66788B; }
QLabel#Brand { font-size: 21px; font-weight: 850; color: #173A3D; }
QLabel#BigNumber { font-size: 23px; font-weight: 800; }
QLabel#StatusPill { background: #EDF2F5; padding: 8px 13px; border-radius: 12px; color: #586C79; font-weight: 650; }
QLabel#SourcePreview { background: #F8FAFB; border: 1px solid #EDF1F3; border-radius: 10px; padding: 13px; color: #2D4756; }
QLabel#TranscriptBody { color: #203546; font-size: 13px; }
QLabel#TranscriptTranslation { color: #155F58; font-size: 14px; font-weight: 650; }
QPushButton { background: #FFFFFF; color: #2A4757; padding: 10px 14px; border: 1px solid #DEE6EA; border-radius: 10px; font-weight: 650; }
QPushButton:hover { background: #F2F7F7; border-color: #B7CECB; }
QPushButton:disabled { color: #A1ADB5; background: #F4F6F7; }
QPushButton#Primary { background: #176A62; border: 1px solid #176A62; color: white; padding: 12px 18px; }
QPushButton#Primary:hover { background: #125C55; }
QPushButton#Danger { background: #FFF3F1; color: #A44236; border: 1px solid #F0D1CB; }
QPushButton#Nav { border: 0; border-radius: 11px; text-align: left; padding: 13px 16px; background: transparent; color: #6A7B85; font-weight: 650; }
QPushButton#Nav:checked { background: #E8F4F0; color: #155F58; }
QPushButton#Nav:hover { background: #F1F6F5; }
QComboBox, QLineEdit, QSpinBox { background: #FFFFFF; color: #263D4A; border: 1px solid #DCE6EA; border-radius: 9px; padding: 8px 11px; min-height: 22px; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border: 1px solid #83B1A7; }
QComboBox QAbstractItemView { background: white; color: #1F3948; selection-background-color: #E5F4EF; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: #F1F4F5; width: 9px; margin: 2px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #C5D1D8; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QProgressBar { height: 7px; border: none; background: #ECF1F2; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #278B79; border-radius: 3px; }
QCheckBox { spacing: 8px; color: #405665; }
'''


def label(text: str, name: str = '', *, wrap: bool = False) -> QLabel:
    el = QLabel(text)
    if name:
        el.setObjectName(name)
    el.setWordWrap(wrap)
    el.setTextFormat(Qt.TextFormat.PlainText)
    return el


def box_layout(parent, *, horizontal=False, margins=(0, 0, 0, 0), gap=10):
    layout = QHBoxLayout(parent) if horizontal else QVBoxLayout(parent)
    layout.setContentsMargins(*margins)
    layout.setSpacing(gap)
    return layout


def separator() -> QFrame:
    f = QFrame(); f.setObjectName('Thin'); f.setFixedHeight(1)
    return f


def add_shadow(w):
    effect = QGraphicsDropShadowEffect(w)
    effect.setBlurRadius(20)
    effect.setOffset(0, 4)
    effect.setColor(QColor(30, 66, 75, 13))
    w.setGraphicsEffect(effect)


class BrandingDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle('Tùy chỉnh ứng dụng')
        self.setMinimumSize(540, 390)
        self.values = load_branding()
        root = box_layout(self, margins=(24, 24, 24, 24), gap=13)
        root.addWidget(label('NHẬN DIỆN ỨNG DỤNG', 'Overline'))
        root.addWidget(label('Dấu ấn SoundText', 'Title'))
        root.addWidget(label('Đổi tên hiển thị, biểu tượng và hình chào. Chỉ thay đổi khi ứng dụng chạy, không sửa icon file .exe.', 'Hint', wrap=True))
        root.addWidget(separator())
        root.addWidget(label('Tên ứng dụng'))
        self.appname = QLineEdit(str(self.values['app_name']))
        root.addWidget(self.appname)
        root.addWidget(label('Dòng mô tả'))
        self.tagline = QLineEdit(str(self.values['tagline']))
        root.addWidget(self.tagline)
        for title, kind in [('Biểu tượng (ICO / PNG)', 'icon'), ('Splash screen (PNG / JPG)', 'splash')]:
            row = QHBoxLayout()
            browse = QPushButton('Chọn ảnh…')
            info = label(str(self.values.get(f'{kind}_path', '')), 'Hint')
            info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.setMaximumWidth(340)
            browse.clicked.connect(lambda _, k=kind, t=info: self._pick(k, t))
            row.addWidget(label(title)); row.addStretch(); row.addWidget(browse)
            root.addLayout(row); root.addWidget(info)
        self.splash_enabled = QCheckBox('Hiển thị splash khi mở ứng dụng')
        self.splash_enabled.setChecked(bool(self.values['show_splash']))
        root.addWidget(self.splash_enabled)
        root.addWidget(label('Splash sẽ đóng ngay khi cửa sổ chính hiện ra, không chờ thêm một khoảng thời gian cố định.', 'Hint', wrap=True))
        row = QHBoxLayout(); row.addStretch()
        cancel = QPushButton('Hủy'); cancel.clicked.connect(self.reject)
        save = QPushButton('Lưu thay đổi'); save.setObjectName('Primary'); save.clicked.connect(self._save)
        row.addWidget(cancel); row.addWidget(save); root.addLayout(row)

    def _pick(self, kind, target):
        filt = 'Images (*.png *.ico *.gif)' if kind == 'icon' else 'Images (*.png *.jpg *.jpeg *.gif)'
        chosen, _ = QFileDialog.getOpenFileName(self, 'Chọn hình ảnh', '', filt)
        if not chosen:
            return
        try:
            self.values[f'{kind}_path'] = store_brand_asset(chosen, kind)
            target.setText(str(self.values[f'{kind}_path']))
        except Exception as exc:
            QMessageBox.warning(self, 'Không thể lưu ảnh', str(exc))

    def _save(self):
        self.values['app_name'] = self.appname.text()
        self.values['tagline'] = self.tagline.text()
        self.values['show_splash'] = self.splash_enabled.isChecked()
        save_branding(self.values)
        self.accept()


class SubtitleEditor(QPlainTextEdit):
    '''Read-only wrapped caption view that reports only actual user scrolling.'''
    userScrolled = Signal()

    def wheelEvent(self, event):
        super().wheelEvent(event)
        self.userScrolled.emit()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self.userScrolled.emit()


class FloatingSubtitles(QWidget):
    '''Always-on-top, resizable Qt subtitle surface.

    The source and translation panes have independent scrollbars, an optional
    synchronized scrolling mode, and keep their history when re-opened. In the
    translation-only mode the source pane is hidden and no JA/EN partial is
    mistaken for a Vietnamese translation.
    '''
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('SoundText · Phụ đề nổi')
        self.setMinimumSize(520, 240)
        self.resize(790, 330)
        self.setWindowOpacity(0.97)
        self.setStyleSheet('''
            QWidget { background: #121E2B; color: #F1F5FA; font-family: "Segoe UI Variable", "Inter", "Segoe UI"; }
            QLabel, QCheckBox { color: #D9E8F0; }
            QPushButton { background: #263647; color: #EBF4FA; border: 1px solid #405265; padding: 6px 10px; border-radius: 7px; }
            QPlainTextEdit { background: #182736; color: #FAFDFF; border: 1px solid #314458; border-radius: 9px; padding: 8px; selection-background-color: #356F7D; }
            QComboBox, QSpinBox { background: #263647; color: #F1F5FA; border: 1px solid #43586D; border-radius: 6px; padding: 5px; }
            QComboBox QAbstractItemView { background: #263647; color: #F1F5FA; }
        ''')
        self.entries: list[dict] = []
        self.partial: dict[str, tuple[str,str]] = {'mic': ('',''), 'system': ('','')}
        self._syncing = False
        base = box_layout(self, margins=(14, 10, 14, 12), gap=8)
        top = QHBoxLayout(); top.setSpacing(10)
        top.addWidget(label('SOUNDTEXT  /  PHỤ ĐỀ TRỰC TIẾP'))
        top.addStretch()
        self.mode = QComboBox()
        self.mode.addItem('Chỉ bản dịch', 'translation')
        self.mode.addItem('STT + bản dịch', 'both')
        self.mode.currentIndexChanged.connect(self._render)
        top.addWidget(self.mode)
        close = QPushButton('Đóng')
        close.clicked.connect(self.hide)
        top.addWidget(close)
        base.addLayout(top)
        settings = QHBoxLayout(); settings.setSpacing(11)
        settings.addWidget(label('STT'))
        self.stt_font_size = QSpinBox(); self.stt_font_size.setRange(5, 50); self.stt_font_size.setValue(13)
        self.stt_font_size.valueChanged.connect(self._render)
        settings.addWidget(self.stt_font_size)
        settings.addWidget(label('Dịch'))
        self.font_size = QSpinBox(); self.font_size.setRange(5, 50); self.font_size.setValue(18)
        self.font_size.valueChanged.connect(self._render)
        settings.addWidget(self.font_size)
        self.follow = QCheckBox('Tự cuộn'); self.follow.setChecked(True)
        settings.addWidget(self.follow)
        self.sync_scroll = QCheckBox('Cuộn đôi'); self.sync_scroll.setChecked(True)
        settings.addWidget(self.sync_scroll)
        self.auto_expand = QCheckBox('Mở rộng'); self.auto_expand.setChecked(True)
        settings.addWidget(self.auto_expand)
        settings.addStretch()
        self.position = QComboBox()
        for text, val in (('Dưới giữa', 'bottom'), ('Bên phải', 'right'), ('Song song', 'side')):
            self.position.addItem(text, val)
        self.position.currentIndexChanged.connect(self._reposition)
        settings.addWidget(self.position)
        base.addLayout(settings)
        self.panes = QSplitter(Qt.Orientation.Vertical)
        self.original = SubtitleEditor()
        self.original.setReadOnly(True); self.original.setMinimumHeight(64)
        self.original.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.vi = SubtitleEditor()
        self.vi.setReadOnly(True); self.vi.setMinimumHeight(76)
        self.vi.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.panes.addWidget(self.original)
        self.panes.addWidget(self.vi)
        base.addWidget(self.panes, 1)
        self.original.userScrolled.connect(lambda: self._user_scrolled('original'))
        self.vi.userScrolled.connect(lambda: self._user_scrolled('vi'))
        self.original.verticalScrollBar().sliderMoved.connect(lambda _v: self._user_scrolled('original'))
        self.vi.verticalScrollBar().sliderMoved.connect(lambda _v: self._user_scrolled('vi'))
        self.live = label('', '', wrap=True)
        self.live.setStyleSheet('color:#B5CBDB; font-size: 12px;')
        base.addWidget(self.live)
        self._render()

    def reset(self):
        self.entries.clear()
        self.partial = {'mic': ('',''), 'system': ('','')}
        self._render()

    def set_entries(self, entries: list[dict]):
        # Opening with 120 rows must render just ONCE, not 120 successive redraws.
        self.entries = [dict(d) for d in entries[-120:]]
        self._render()

    def update_row(self, row: dict):
        key = (row['source'], row['id'])
        for i, current in enumerate(self.entries):
            if (current['source'], current['id']) == key:
                self.entries[i] = dict(row)
                break
        else:
            self.entries.append(dict(row))
        self.entries = self.entries[-120:]
        self._render()

    def update_partial(self, source: str, text: str, language: str = ''):
        self.partial[source] = (text,language)
        self._update_live()

    def _update_live(self):
        display = []
        for source, (text, language) in self.partial.items():
            # A live JA/EN ASR preview isn't a Vietnamese translation.
            if text and (self.mode.currentData() == 'both' or language == 'vi'):
                display.append(('MIC' if source == 'mic' else 'REMOTE') + ': ' + text)
        self.live.setText('  /  '.join(display))
        self.live.setVisible(bool(display))

    def _user_scrolled(self, source: str):
        if self._syncing:
            return
        edit = self.original if source=='original' else self.vi
        bar = edit.verticalScrollBar()
        near_end = bar.maximum()-bar.value() <= 12
        self.follow.setChecked(near_end)
        if self.mode.currentData() != 'both' or not self.sync_scroll.isChecked():
            return
        other = self.vi if source=='original' else self.original
        target = other.verticalScrollBar()
        self._syncing = True
        try:
            fraction = (bar.value()/bar.maximum()) if bar.maximum() else 1.0
            target.setValue(round(fraction*target.maximum()))
        finally:
            self._syncing = False

    def _render(self):
        both = self.mode.currentData() == 'both'
        self.original.setVisible(both)
        self.sync_scroll.setEnabled(both)
        self.panes.setOrientation(Qt.Orientation.Horizontal if both and self.position.currentData()=='side'
                                  else Qt.Orientation.Vertical)
        stt_font = QFont('Segoe UI Variable'); stt_font.setPointSize(self.stt_font_size.value())
        vi_font = QFont('Segoe UI Variable'); vi_font.setPointSize(self.font_size.value())
        self.original.setFont(stt_font)
        self.vi.setFont(vi_font)
        originals = []; translations = []
        for row in self.entries:
            speaker = row.get('speaker', '')
            who = ('MIC' if row['source'] == 'mic' else 'REMOTE') + (f' · {speaker}' if speaker else '')
            if row.get('original'):
                originals.append(f'{who}  {row["original"]}')
            if row.get('translation'):
                translations.append(f'{who}  {row["translation"]}')
            elif row.get('lang') == 'vi' and row.get('original'):
                translations.append(f'{who}  {row["original"]}')
        for widget, lines in ((self.original, originals), (self.vi, translations)):
            new_text = '\n\n'.join(lines)
            if widget.toPlainText() == new_text:
                continue
            bar = widget.verticalScrollBar()
            old_value = bar.value()
            should_follow = self.follow.isChecked()
            widget.setPlainText(new_text)
            bar.setValue(bar.maximum() if should_follow else min(old_value,bar.maximum()))
        self._update_live()
        if self.isVisible() and self.auto_expand.isChecked():
            self._expand_to_fit()

    def _expand_to_fit(self):
        # Grow for long translated lines but never exceed 65% of the monitor.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        max_height = int(screen.availableGeometry().height() * 0.65)
        if self.height() >= max_height:
            return
        chars_per_line = max(18,int(self.vi.viewport().width() / max(4,self.font_size.value()*.62)))
        text = self.vi.toPlainText()[-1400:]
        lines = sum(max(1, (len(part)+chars_per_line-1)//chars_per_line) for part in text.splitlines())
        desired = min(max_height,max(240,min(8,lines)*int(self.font_size.value()*1.6)+155))
        if desired > self.height()+18:
            self.resize(self.width(),desired)

    def _reposition(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:return
        area=screen.availableGeometry()
        location=self.position.currentData()
        if location=='right':
            x=area.right()-self.width()-22
            y=area.top()+(area.height()-self.height())//2
        elif location=='side':
            x=area.left()+(area.width()-self.width())//2
            y=area.top()+(area.height()-self.height())//2
        else:
            x=area.left()+(area.width()-self.width())//2
            y=area.bottom()-self.height()-48
        self.move(max(area.left(),x),max(area.top(),y))
        self._render()

    def showEvent(self,event):
        super().showEvent(event)
        self._reposition()
        if self.auto_expand.isChecked():self._expand_to_fit()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:super().keyPressEvent(event)

class TranscriptRow(QFrame):
    def __init__(self, data: dict):
        super().__init__()
        self.setObjectName('Panel')
        self.data = data
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay = box_layout(self, margins=(16, 13, 16, 14), gap=7)
        top = QHBoxLayout(); top.setSpacing(8)
        self.who = label('', 'Overline')
        self.time = label('', 'Hint')
        top.addWidget(self.who); top.addStretch(); top.addWidget(self.time)
        lay.addLayout(top)
        self.translated = label('', 'TranscriptTranslation', wrap=True)
        self.source = label('', 'TranscriptBody', wrap=True)
        self.source.setStyleSheet('color:#81909D; font-size:12px;')
        lay.addWidget(self.translated)
        lay.addWidget(self.source)
        self.refresh(show_original=True)

    def refresh(self, *, show_original: bool):
        d = self.data
        name = ('MIC' if d['source'] == 'mic' else 'REMOTE')
        if d.get('speaker'):
            name += f'  ·  {d["speaker"]}'
        if d.get('overlap'):
            name += '  ·  NÓI CHỒNG'
        self.who.setText(name)
        self.time.setText(d['time'])
        translation = d.get('translation') or (d['original'] if d.get('lang') == 'vi' else 'Đang dịch…')
        self.translated.setText(translation)
        self.source.setText(d.get('original', ''))
        self.source.setVisible(show_original and bool(d.get('original')) and d.get('lang') != 'vi')


class SoundTextWindow(QMainWindow):
    LANGS = [('Tiếng Việt', 'vi'), ('日本語', 'ja'), ('English', 'en'), ('JA / EN Auto', 'auto')]
    MODES = [('MIC + REMOTE', 'both'), ('Chỉ microphone', 'you'), ('Chỉ âm thanh máy', 'remote')]

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.branding = load_branding()
        self.bus = MeetingUIBus()
        self.controller = RecordingController(self.bus)
        self.entries = []
        self.entry_by_key = {}
        self.current_folder = ''
        self.last_folder = ''
        self.last_error = ''
        self.is_recording = False
        self.started_at = None
        self.overlay = None
        self.max_history = max(30, int(config.meeting.ui_history_items))
        self.setWindowTitle(str(self.branding.get('app_name') or 'SoundText') + ' — Studio')
        self.resize(1330, 860); self.setMinimumSize(970, 660)
        self._apply_icon()
        self._build()
        self._set_state('idle', 'Sẵn sàng. Chọn nguồn và nhấn Bắt đầu ghi âm.')
        self.timer = QTimer(self)
        self.timer.setInterval(max(30, int(config.meeting.ui_refresh_ms)))
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _apply_icon(self):
        icon = resolve_branding_path(str(self.branding.get('icon_path', '')))
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(QIcon(str(icon)))

    def _build(self):
        root = QWidget(); root.setObjectName('Root'); self.setCentralWidget(root)
        base = box_layout(root, horizontal=True, gap=0)
        side = QFrame(); side.setObjectName('Sidebar'); side.setFixedWidth(232)
        base.addWidget(side)
        sl = box_layout(side, margins=(22, 28, 18, 20), gap=11)
        monogram = label('◉', 'Brand'); monogram.setStyleSheet('font-size: 36px; color: #176A62;')
        sl.addWidget(monogram)
        self.brand_name = label(str(self.branding.get('app_name', 'SoundText')), 'Brand', wrap=True)
        sl.addWidget(self.brand_name)
        sl.addWidget(label('WORKSPACE  /  DESKTOP', 'Overline'))
        sl.addSpacing(29)
        self.nav_live = QPushButton('◉    Trực tiếp'); self.nav_live.setObjectName('Nav'); self.nav_live.setCheckable(True)
        self.nav_archive = QPushButton('▤    Nội dung cuộc họp'); self.nav_archive.setObjectName('Nav'); self.nav_archive.setCheckable(True)
        self.nav_settings = QPushButton('⚙    Tùy chỉnh ứng dụng'); self.nav_settings.setObjectName('Nav')
        for x in (self.nav_live, self.nav_archive, self.nav_settings): sl.addWidget(x)
        self.nav_live.setChecked(True)
        self.nav_live.clicked.connect(lambda: self._choose_page('live'))
        self.nav_archive.clicked.connect(lambda: self._choose_page('archive'))
        self.nav_settings.clicked.connect(self._branding_dialog)
        sl.addStretch()
        sl.addWidget(separator())
        sl.addWidget(label('XỬ LÝ CỤC BỘ', 'Overline'))
        sl.addWidget(label('ASR • NLLB • Diarization\nKhông truyền nội dung cuộc họp lên dịch vụ AI.', 'Hint', wrap=True))
        main = QWidget(); base.addWidget(main, 1)
        ml = box_layout(main, margins=(30, 24, 30, 22), gap=16)
        header = QHBoxLayout()
        heads = QVBoxLayout(); heads.setSpacing(5)
        heads.addWidget(label('SOUNDTEXT  /  STUDIO', 'Overline'))
        heads.addWidget(label('Phòng điều khiển', 'Title'))
        header.addLayout(heads); header.addStretch()
        self.state_pill = label('CHƯA GHI ÂM', 'StatusPill'); header.addWidget(self.state_pill)
        ml.addLayout(header)
        self.pages = QStackedWidget(); ml.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_live_page())
        self.pages.addWidget(self._build_archive_page())
        footer = QHBoxLayout()
        self.footer = label('', 'Hint', wrap=True); footer.setMinimumWidth(150)
        footer.addWidget(self.footer, 1)
        self.elapsed = label('00:00:00', 'Hint'); footer.addWidget(self.elapsed)
        ml.addLayout(footer)

    def _build_live_page(self):
        page = QWidget(); pl = box_layout(page, gap=14)
        toolbar = QFrame(); toolbar.setObjectName('Panel')
        tl = box_layout(toolbar, horizontal=True, margins=(18, 14, 18, 14), gap=11)
        self.mode = QComboBox()
        for t,v in self.MODES: self.mode.addItem(t,v)
        self.mode.setCurrentIndex(max(0,self.mode.findData(self.config.meeting.audio_mode)))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        tl.addWidget(label('Nguồn thu', 'Hint')); tl.addWidget(self.mode)
        tl.addStretch()
        self.start_button = QPushButton('●  Bắt đầu ghi âm'); self.start_button.setObjectName('Primary')
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton('■  Dừng & lưu'); self.stop_button.setObjectName('Danger')
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.setEnabled(False)
        tl.addWidget(self.start_button); tl.addWidget(self.stop_button)
        folder_btn = QPushButton('↗  Thư mục lưu')
        folder_btn.clicked.connect(self._open_folder)
        tl.addWidget(folder_btn)
        pl.addWidget(toolbar)
        cards = QHBoxLayout(); cards.setSpacing(13)
        self.live_labels = {}; self.meters = {}; self.languages = {}
        for source in ('mic','system'):
            cards.addWidget(self._build_live_card(source),1)
        pl.addLayout(cards)
        section = QHBoxLayout()
        section.addWidget(label('Dòng hội thoại', 'Section'))
        section.addStretch()
        self.show_original = QCheckBox('Hiện câu gốc'); self.show_original.setChecked(True)
        self.show_original.toggled.connect(self._refresh_rows)
        section.addWidget(self.show_original)
        self.follow = QCheckBox('Tự cuộn'); self.follow.setChecked(True); section.addWidget(self.follow)
        self.floating = QPushButton('↗  Phụ đề nổi'); self.floating.clicked.connect(self._toggle_overlay); section.addWidget(self.floating)
        pl.addLayout(section)
        self.timeline = QScrollArea(); self.timeline.setWidgetResizable(True)
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline_content = QWidget()
        self.timeline_layout = box_layout(self.timeline_content, margins=(2, 3, 7, 6), gap=10)
        self.timeline_layout.addStretch()
        self.timeline.setWidget(self.timeline_content)
        self.timeline.verticalScrollBar().sliderMoved.connect(lambda _: self._timeline_user_scrolled())
        self.timeline.viewport().installEventFilter(self)
        pl.addWidget(self.timeline,1)
        return page

    def _build_live_card(self, source):
        mic = source=='mic'
        f = QFrame(); f.setObjectName('LiveCard')
        lay = box_layout(f, margins=(18, 15, 18, 18), gap=11)
        top=QHBoxLayout()
        top.addWidget(label('01  /  MICROPHONE' if mic else '02  /  ÂM THANH MÁY', 'Overline'))
        top.addStretch()
        chooser = QComboBox()
        for t,v in self.LANGS: chooser.addItem(t,v)
        lang = self.config.meeting.mic_language if mic else self.config.meeting.system_language
        chooser.setCurrentIndex(max(0,chooser.findData(lang)))
        chooser.currentIndexChanged.connect(lambda _, s=source, c=chooser: self._language_changed(s,c.currentData()))
        self.languages[source] = chooser
        top.addWidget(chooser)
        lay.addLayout(top)
        head=label('Mic / bạn' if mic else 'Remote / cuộc họp', 'Section');lay.addWidget(head)
        bar=QProgressBar();bar.setRange(0,100);bar.setValue(0);bar.setTextVisible(False)
        self.meters[source]=bar; lay.addWidget(bar)
        lay.addWidget(label('BẢN NHẬN DIỆN ĐANG CHẠY', 'Overline'))
        preview=label('Chưa có âm thanh. Nhấn Bắt đầu ghi âm.', 'SourcePreview', wrap=True)
        preview.setMinimumHeight(80)
        preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.live_labels[source]=preview
        lay.addWidget(preview,1)
        return f

    def _build_archive_page(self):
        page=QWidget(); lay=box_layout(page, gap=15)
        lay.addWidget(label('NỘI DUNG ĐÃ LƯU', 'Overline'))
        lay.addWidget(label('Phiên ghi âm', 'Title'))
        lay.addWidget(label('Nội dung được lưu cục bộ trong thư mục output. Mỗi lần bấm Bắt đầu là một phiên mới.', 'Hint', wrap=True))
        panel=QFrame(); panel.setObjectName('Panel')
        bl=box_layout(panel,margins=(24,24,24,24),gap=15)
        self.archive_path=label('Chưa có phiên ghi âm trong lần mở ứng dụng này.', 'Hint', wrap=True)
        bl.addWidget(self.archive_path)
        buttons=QHBoxLayout()
        open_btn=QPushButton('↗  Mở thư mục lưu');open_btn.setObjectName('Primary');open_btn.clicked.connect(self._open_folder)
        buttons.addWidget(open_btn)
        copy_btn=QPushButton('Sao chép hội thoại đang hiển thị');copy_btn.clicked.connect(self._copy_transcript)
        buttons.addWidget(copy_btn); buttons.addStretch();bl.addLayout(buttons)
        lay.addWidget(panel);lay.addStretch()
        return page

    def _choose_page(self, page):
        is_live=page=='live'
        self.pages.setCurrentIndex(0 if is_live else 1)
        self.nav_live.setChecked(is_live); self.nav_archive.setChecked(not is_live)

    def _set_state(self,state,message):
        self.footer.setText(message)
        labels={'idle':'CHƯA GHI ÂM','starting':'ĐANG CHUẨN BỊ','recording':'● ĐANG GHI ÂM','stopping':'ĐANG LƯU','stopped':'ĐÃ LƯU','failed':'LỖI GHI ÂM'}
        self.state_pill.setText(labels.get(state,state.upper()))
        color={'recording':('#E5F4EF','#176A62'),'failed':('#FCE9E7','#A44236'),'starting':('#FFF4E4','#99712F')}.get(state,('#EDF2F5','#586C79'))
        self.state_pill.setStyleSheet(f'background:{color[0]};color:{color[1]};padding:8px 13px;border-radius:12px;font-weight:650;')
        self.start_button.setEnabled(state in ('idle','stopped','failed'))
        self.stop_button.setEnabled(state in ('starting','recording'))
        self.mode.setEnabled(state in ('idle','stopped','failed','recording'))
        for selector in self.languages.values():
            selector.setEnabled(state in ('idle','stopped','failed','recording'))
        self.is_recording=(state=='recording')

    def _start(self):
        if self.controller.busy: return
        self.entries.clear();self.entry_by_key.clear()
        for child in list(self.timeline_content.children()):
            if isinstance(child,TranscriptRow):
                self.timeline_layout.removeWidget(child);child.deleteLater()
        for lbl in self.live_labels.values(): lbl.setText('Đang chờ âm thanh...')
        for meter in self.meters.values(): meter.setValue(0)
        if self.overlay is not None: self.overlay.reset()
        self.started_at=None
        self.last_error=''
        self._set_state('starting','Đang chuẩn bị thiết bị và các mô hình cục bộ...')
        if not self.controller.start(self.config):
            self._set_state('idle','Không thể bắt đầu khi phiên trước chưa dừng hẳn.')

    def _stop(self):
        self.controller.stop()
        self._set_state('stopping','Đang hoàn tất và lưu dữ liệu cuộc họp...')

    def _mode_changed(self):
        value=self.mode.currentData()
        self.config.meeting.audio_mode=value
        self.controller.set_mode(value)

    def _language_changed(self,source,value):
        if source=='mic':self.config.meeting.mic_language=value
        else:self.config.meeting.system_language=value
        self.controller.set_language(source,value)

    def _tick(self):
        for _ in range(20):
            try: st=self.controller.statuses.get_nowait()
            except Empty:break
            if st.session_dir:
                self.current_folder=st.session_dir
                self.last_folder=st.session_dir
                self.archive_path.setText('Phiên gần nhất: '+st.session_dir)
            if st.kind=='recording':
                self.started_at=time.monotonic()
                self._set_state('recording',st.message)
            elif st.kind=='stopped':
                if self.started_at is not None:
                    seconds = int(time.monotonic()-self.started_at)
                    self.elapsed.setText(f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}')
                    self.started_at = None
                if self.last_error or st.message:
                    self._set_state('failed',self.last_error or st.message)
                else:
                    self._set_state('stopped','Đã lưu phiên ghi âm. Có thể bấm Bắt đầu để tạo phiên mới.')
            elif st.kind=='failed':
                self.last_error=st.message
                self._set_state('failed',st.message)
            else:self._set_state(st.kind,st.message)
        for _ in range(42):
            try: ev=self.bus.queue.get_nowait()
            except Empty:break
            self._process_event(ev)
        meter_levels=self.bus.snapshot_audio_levels()
        if self.is_recording:
            for source, meter in self.meters.items():
                if source in meter_levels:
                    db=meter_levels[source][0]
                    meter.setValue(max(0,min(100,int((db+66)/63*100))))
        if self.started_at is not None:
            s=int(time.monotonic()-self.started_at)
            self.elapsed.setText(f'{s//3600:02}:{s//60%60:02}:{s%60:02}')

    def _process_event(self,ev:UIEvent):
        if ev.stage=='status':
            if ev.message:self.footer.setText(ev.message)
            return
        if ev.stage not in ('asr','translation') or ev.source not in ('mic','system'):
            return
        key=(ev.source,ev.utterance_id)
        if ev.stage=='asr':
            if not ev.final:
                if ev.source_text:
                    self.live_labels[ev.source].setText(ev.source_text)
                    if self.overlay is not None:self.overlay.update_partial(ev.source,ev.source_text,ev.source_language)
                return
            if not ev.source_text.strip():return
            self.live_labels[ev.source].setText(ev.source_text)
            if self.overlay is not None:self.overlay.update_partial(ev.source,'')
            if key not in self.entry_by_key:
                row={'source':ev.source,'id':ev.utterance_id,
                     'original':ev.source_text,'translation':'','lang':ev.source_language,
                     'time':datetime.now().strftime('%H:%M:%S'),
                     'speaker':ev.speaker_id or '', 'overlap':bool(ev.speaker_overlap)}
                widget=TranscriptRow(row)
                self.timeline_layout.insertWidget(self.timeline_layout.count()-1,widget)
                self.entry_by_key[key]=widget
                self.entries.append(row)
                while len(self.entries)>self.max_history:
                    old=self.entries.pop(0)
                    w=self.entry_by_key.pop((old['source'],old['id']),None)
                    if w:w.setParent(None);w.deleteLater()
                self._scroll_to_latest()
                if self.overlay is not None:self.overlay.update_row(row)
            return
        # Translation may arrive after a new utterance has started. Update by
        # stable source/utterance-id instead of showing it under the wrong row.
        widget=self.entry_by_key.get(key)
        if widget is not None:
            widget.data['translation']=ev.translated_text
            widget.refresh(show_original=self.show_original.isChecked())
            if self.overlay is not None:self.overlay.update_row(widget.data)
            self._scroll_to_latest()

    def eventFilter(self, watched, event):
        if watched is self.timeline.viewport() and event.type() == QEvent.Type.Wheel:
            QTimer.singleShot(0, self._timeline_user_scrolled)
        return super().eventFilter(watched, event)

    def _timeline_user_scrolled(self):
        bar = self.timeline.verticalScrollBar()
        self.follow.setChecked((bar.maximum() - bar.value()) <= 12)

    def _refresh_rows(self):
        for widget in self.entry_by_key.values():
            widget.refresh(show_original=self.show_original.isChecked())

    def _scroll_to_latest(self):
        if not self.follow.isChecked():return
        QTimer.singleShot(0,lambda: self.timeline.verticalScrollBar().setValue(self.timeline.verticalScrollBar().maximum()))

    def _toggle_overlay(self):
        if self.overlay is None:
            self.overlay=FloatingSubtitles()
            self.overlay.set_entries(self.entries)
        if self.overlay.isVisible():
            self.overlay.hide(); self.floating.setText('↗  Phụ đề nổi')
        else:
            self.overlay.show();self.overlay.raise_();self.floating.setText('↙  Ẩn phụ đề nổi')

    def _open_folder(self):
        folder=self.last_folder or self.config.storage.output_dir
        try:open_session_directory(folder)
        except Exception as exc:QMessageBox.warning(self,'Không thể mở thư mục',str(exc))

    def _copy_transcript(self):
        out=[]
        for item in self.entries:
            out.append(f'[{item["time"]}] {item["source"].upper()} {item.get("speaker", "")}\n'
                       +item['original']+'\n'+(item['translation'] or '')+'\n')
        QGuiApplication.clipboard().setText('\n'.join(out))
        self.footer.setText(f'Đã sao chép {len(out)} lượt nói.')

    def _branding_dialog(self):
        if BrandingDialog(self).exec()==QDialog.DialogCode.Accepted:
            self.branding=load_branding()
            self.brand_name.setText(str(self.branding['app_name']))
            self.setWindowTitle(str(self.branding['app_name'])+' — Studio')
            self._apply_icon()

    def closeEvent(self,event):
        if self.controller.busy:
            msg=QMessageBox.question(self,'Phiên ghi âm đang chạy',
                'Dừng và lưu phiên ghi âm trước khi thoát?',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if msg==QMessageBox.StandardButton.No:
                event.ignore();return
            self.controller.stop()
            # No blocking join on the GUI thread. Let the existing recording
            # worker finalize asynchronously; close after it has finished.
            self.setEnabled(False)
            self.footer.setText('Đang hoàn tất phiên ghi âm trước khi thoát...')
            self._quit_when_stopped=QTimer(self)
            self._quit_when_stopped.timeout.connect(self._try_close_after_stop)
            self._quit_when_stopped.start(90)
            event.ignore();return
        if self.overlay is not None:
            self.overlay.close()
        event.accept()

    def _try_close_after_stop(self):
        if not self.controller.busy:
            self._quit_when_stopped.stop()
            if self.overlay is not None:self.overlay.close()
            QApplication.instance().quit()


def run_qt_application(config)->int:
    # Calling main.py run no longer starts any audio capture automatically.
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('SoundText')
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI Variable', 10))
    app.setStyleSheet(STYLE)
    branding=load_branding()
    splash=None
    if branding.get('show_splash'):
        path=resolve_branding_path(str(branding.get('splash_path','')))
        if path:
            pix=QPixmap(str(path))
            if not pix.isNull():
                pix=pix.scaled(QSize(680,390),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                splash=QSplashScreen(pix,Qt.WindowType.WindowStaysOnTopHint)
                splash.show()
                app.processEvents()
    window=SoundTextWindow(config)
    window.show()
    # No 1s/1.4s hard-coded hold: close as soon as the main window is drawn.
    if splash is not None:
        QTimer.singleShot(0,lambda: splash.finish(window))
    return int(app.exec())
