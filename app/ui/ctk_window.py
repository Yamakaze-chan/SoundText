"""SoundText CustomTkinter UI.

This module controls session preferences, recording lifecycle and rendering.
ASR, translation and speaker algorithms live in the existing pipeline.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
import sys
import traceback
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from app.ui.branding import (
    load_branding, resolve_branding_path, open_session_directory, branding_file_error,
)
from app.ui.meeting_window import MeetingUIBus, UIEvent
from app.ui.recording_controller import RecordingController
from app.ui.splash_assets import choose_splash_art
from app.ui.window_icon import WindowIconManager, configure_windows_app_identity

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

BG = "#F5F7F9"
SURFACE = "#FFFFFF"
INK = "#1D303A"
MUTED = "#657783"
LINE = "#DFE7EA"
TEAL = "#16736C"
TEAL_SOFT = "#E8F3F1"
BLUE = "#366C9B"
DANGER = "#A54840"
FONT = "Segoe UI"  # Windows default; readable even without custom font installs


def style(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def label(parent, text, *, size=13, weight="normal", color=INK, **kwargs):
    return ctk.CTkLabel(parent, text=text, font=style(size, weight), text_color=color, **kwargs)


def surface(parent, *, color=SURFACE, radius=17, **kwargs):
    return ctk.CTkFrame(parent, fg_color=color, corner_radius=radius, border_width=1,
                        border_color=LINE, **kwargs)


def button(parent, text, command, *, primary=False, danger=False, width=None):
    args = dict(text=text, command=command, height=37, corner_radius=9,
                font=style(12, "bold"), border_spacing=6)
    if width is not None:
        args["width"] = width
    if primary:
        args.update(fg_color=TEAL, hover_color="#105E59", text_color="#FFFFFF")
    elif danger:
        args.update(fg_color="#FBEFEC", hover_color="#F5DFDA", text_color=DANGER)
    else:
        args.update(fg_color="#F0F4F5", hover_color="#E5ECEE", text_color=INK)
    return ctk.CTkButton(parent, **args)


class SubtitlePane(ctk.CTkToplevel):
    """One independently movable, resizable overlay window (STT or translation)."""

    def __init__(self, owner, kind):
        super().__init__(owner.parent)
        self.owner = owner
        self.kind = kind
        owner.parent._window_icon.install(self)
        is_original = kind == "stt"
        title = "LỜI THOẠI GỐC" if is_original else "BẢN DỊCH TIẾNG VIỆT"
        self.title(f"SoundText · {title}")
        self.configure(fg_color="#14222D")
        self.geometry("520x280")
        self.minsize(360, 180)
        self.attributes("-topmost", True)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 3))
        label(heading, title, size=11, weight="bold", color="#D6E8EB").pack(side="left", padx=(3, 6))
        button(heading, "Ẩn", owner.withdraw, width=52).pack(side="right")

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 7))
        label(controls, "Cỡ chữ", size=10, color="#D6E8EB").pack(side="left", padx=(4, 5))
        font_var = owner.stt_size if is_original else owner.vi_size
        picker = ctk.CTkOptionMenu(
            controls, values=[str(n) for n in (5, 7, 9, 11, 13, 15, 18, 21, 24, 28, 32, 38, 44, 50)],
            width=65, height=29, font=style(10), fg_color="#2E4856",
            button_color="#2E4856", command=lambda value: owner.set_font(kind, value),
        )
        picker.set(str(font_var.get()))
        picker.pack(side="left")
        # Every pane gets the display selector, so STT-only can switch back
        # to translation without needing to reopen a hidden window.
        visibility_picker = ctk.CTkOptionMenu(
            controls, values=owner.available_views(), variable=owner.view,
            command=lambda _value: owner._visibility_changed(), width=138, height=29,
            font=style(10), fg_color="#2E4856", button_color="#2E4856",
        )
        visibility_picker.pack(side="left", padx=(9, 0))
        owner._view_pickers.append(visibility_picker)
        if is_original:
            ctk.CTkCheckBox(
                controls, text="Cuộn đôi", variable=owner.sync_scroll,
                font=style(10), text_color="#E0E9ED", fg_color="#35A18F",
                checkbox_width=15, checkbox_height=15, width=94,
            ).pack(side="left", padx=(9, 0))

        if not is_original:
            options = ctk.CTkFrame(self, fg_color="transparent")
            options.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
            ctk.CTkCheckBox(
                options, text="Tự cuộn", variable=owner.follow,
                command=owner._follow_changed, font=style(10), width=93,
                text_color="#E0E9ED", fg_color="#35A18F",
                checkbox_width=15, checkbox_height=15,
            ).pack(side="left")
            ctk.CTkCheckBox(
                options, text="Mở rộng", variable=owner.auto_expand,
                font=style(10), width=98, text_color="#E0E9ED", fg_color="#35A18F",
                checkbox_width=15, checkbox_height=15,
            ).pack(side="left", padx=(4, 0))
            layouts = ctk.CTkOptionMenu(
                options, values=["Cạnh nhau", "Chồng dọc", "Bên phải"],
                command=owner._layout_changed, width=123, height=27,
                fg_color="#2E4856", button_color="#2E4856", font=style(10),
            )
            layouts.set("Cạnh nhau")
            layouts.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="#1B2F3D", corner_radius=9,
                            border_width=1, border_color="#334B5A")
        body.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.text = tk.Text(
            body, wrap="word", height=6, bd=0, relief="flat", padx=12, pady=9,
            background="#1B2F3D", foreground="#F6FBFF", insertbackground="#F6FBFF",
            highlightthickness=0, selectbackground="#326D7C", state="disabled",
            font=(FONT, font_var.get()),
        )
        self.text.grid(row=0, column=0, sticky="nsew", padx=(2, 0), pady=2)
        bar = ctk.CTkScrollbar(body, command=self.text.yview, fg_color="#1B2F3D",
                               button_color="#526976", button_hover_color="#6A8898")
        bar.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        self.text.configure(yscrollcommand=bar.set)
        self.text.bind("<MouseWheel>", lambda _event: self.after_idle(self._scroll_finished), add="+")
        self.text.bind("<Button-4>", lambda _event: self.after_idle(self._scroll_finished), add="+")
        self.text.bind("<Button-5>", lambda _event: self.after_idle(self._scroll_finished), add="+")
        bar.bind("<ButtonRelease-1>", lambda _event: self.after_idle(self._scroll_finished), add="+")
        self.protocol("WM_DELETE_WINDOW", owner.withdraw)
        self.bind("<Escape>", lambda _event: owner.withdraw())
        self.withdraw()

    def _scroll_finished(self):
        self.owner._user_scrolled(self.kind)


class FloatingSubtitles:
    """Two independent subtitle windows backed by the same transcript history.

    STT includes interim speech; translated text appears only after Final.
    This controller has three display modes independent of the recording mode.
    """

    def __init__(self, parent, on_visibility_change=None):
        self.parent = parent
        self.on_visibility_change = on_visibility_change
        self.entries = []
        self.partials = {"mic": ("", ""), "system": ("", "")}
        self.follow = tk.BooleanVar(master=parent, value=True)
        self.sync_scroll = tk.BooleanVar(master=parent, value=True)
        self.auto_expand = tk.BooleanVar(master=parent, value=True)
        self.view = tk.StringVar(master=parent, value="Cả hai")
        self.translation_enabled = bool(parent.config.translation.enabled)
        if not self.translation_enabled:
            self.view.set("Chỉ lời thoại")
        self._view_pickers = []
        self.stt_size = tk.IntVar(master=parent, value=13)
        self.vi_size = tk.IntVar(master=parent, value=18)
        self._syncing = False
        self._positioned = False
        self._last_text = {"stt": None, "vi": None}
        self.stt = SubtitlePane(self, "stt")
        self.vi = SubtitlePane(self, "vi")
        self._position_panes()

    def winfo_exists(self):
        return bool(self.vi.winfo_exists() and self.stt.winfo_exists())

    def winfo_viewable(self):
        return bool(self.vi.winfo_viewable() or self.stt.winfo_viewable())

    def available_views(self):
        return ["Cả hai", "Chỉ lời thoại", "Chỉ bản dịch"] if self.translation_enabled else ["Chỉ lời thoại"]

    def set_translation_enabled(self, enabled, *, choose_default=False):
        self.translation_enabled = bool(enabled)
        if choose_default:
            self.view.set("Cả hai" if enabled else "Chỉ lời thoại")
        elif self.view.get() not in self.available_views():
            self.view.set("Chỉ lời thoại")
        for picker in self._view_pickers:
            picker.configure(values=self.available_views())
        self._visibility_changed()

    def withdraw(self):
        for pane in (self.stt, self.vi):
            if pane.winfo_exists():
                pane.withdraw()
        self._emit_visibility(False)

    def destroy(self):
        for pane in (self.stt, self.vi):
            if pane.winfo_exists():
                pane.destroy()
        self._emit_visibility(False)

    def _emit_visibility(self, value):
        if self.on_visibility_change is not None:
            self.on_visibility_change(value)

    def _visibility_changed(self):
        if not self.winfo_viewable():
            return
        self._apply_visibility()
        self._render(force=True)

    def _apply_visibility(self):
        mode = self.view.get()
        # Don't expose an empty translation-only window in transcript-only mode.
        if mode not in self.available_views():
            mode = "Chỉ lời thoại"
            self.view.set(mode)
        for pane, should_show in (
            (self.stt, mode in ("Cả hai", "Chỉ lời thoại")),
            (self.vi, mode in ("Cả hai", "Chỉ bản dịch")),
        ):
            if should_show:
                pane.deiconify()
                pane.lift()
            else:
                pane.withdraw()

    def set_font(self, kind, value):
        size = max(5, min(50, int(value)))
        target = self.stt if kind == "stt" else self.vi
        (self.stt_size if kind == "stt" else self.vi_size).set(size)
        target.text.configure(font=(FONT, size))
        if self.winfo_viewable() and self.auto_expand.get():
            self._expand_pane(target)

    def _position_panes(self, layout="side"):
        if self._positioned:
            return  # Do not reset dragged positions when reopening.
        sw = self.parent.winfo_screenwidth()
        sh = self.parent.winfo_screenheight()
        gap = 14
        width = min(560, max(360, (sw - 3 * gap) // 2))
        height = min(310, max(195, sh // 3))
        if layout == "side":
            x = max(8, (sw - 2 * width - gap) // 2)
            y = max(12, sh - height - 72)
            self.stt.geometry(f"{width}x{height}+{x}+{y}")
            self.vi.geometry(f"{width}x{height}+{x + width + gap}+{y}")
        else:
            width = min(590, max(360, sw - 45))
            height = min(290, max(180, (sh - 120) // 2))
            x = max(8, (sw - width) // 2) if layout == "stack" else max(8, sw - width - 24)
            y = max(8, sh - 2 * height - gap - 60) if layout == "stack" else max(8, (sh - 2 * height - gap) // 2)
            self.stt.geometry(f"{width}x{height}+{x}+{y}")
            self.vi.geometry(f"{width}x{height}+{x}+{y + height + gap}")
        self._positioned = True

    def _layout_changed(self, value):
        layout = {"Cạnh nhau": "side", "Chồng dọc": "stack", "Bên phải": "right"}.get(value, "side")
        self._positioned = False
        self._position_panes(layout)

    def _follow_changed(self):
        if self.follow.get() and self.winfo_viewable():
            self.stt.text.see("end")
            self.vi.text.see("end")

    def show_overlay(self):
        self._apply_visibility()
        # A newly mapped Tk Toplevel may not report winfo_viewable() until
        # the next idle cycle. Paint existing history immediately anyway.
        self._render(force=True)
        self._emit_visibility(True)

    def reset(self):
        self.entries.clear()
        self.partials = {"mic": ("", ""), "system": ("", "")}
        self._last_text = {"stt": None, "vi": None}
        if self.winfo_viewable():
            self._render()

    def set_entries(self, entries):
        self.entries = [dict(x) for x in entries[-120:]]
        if self.winfo_viewable():
            self._render()

    def update_row(self, row):
        key = (row["source"], row["id"])
        for index, old in enumerate(self.entries):
            if (old["source"], old["id"]) == key:
                self.entries[index] = dict(row)
                break
        else:
            self.entries.append(dict(row))
        self.entries = self.entries[-120:]
        if self.winfo_viewable():
            self._render()

    def update_partial(self, source, text, language=""):
        self.partials[source] = (str(text or ""), str(language or ""))
        if self.winfo_viewable():
            self._render()

    @staticmethod
    def _speaker(item):
        who = "MIC" if item.get("source") == "mic" else "REMOTE"
        if item.get("speaker"):
            who += " · " + str(item["speaker"])
        if item.get("overlap"):
            who += " · NÓI CHỒNG"
        return who

    def _text_for(self, kind):
        # Consecutive REMOTE finals from the same speaker form one readable
        # paragraph. Avoid two empty lines and a repeated REMOTE prefix for
        # every short VAD sentence; retain boundaries on speaker/source change.
        paragraphs = []
        previous_key = None
        for item in self.entries:
            who = self._speaker(item)
            if kind == "stt":
                content = item.get("original", "")
            else:
                content = item.get("translation") or (
                    item.get("original") if item.get("lang") == "vi" else ""
                )
            content = " ".join(str(content).split())
            if not content:
                continue
            key = (item.get("source"), item.get("speaker"), bool(item.get("overlap")))
            if item.get("source") == "system" and key == previous_key and paragraphs:
                paragraphs[-1] += " " + content
            else:
                paragraphs.append(f"{who}  {content}")
            previous_key = key
        # Partials stay separate and do not replace previously finalised text.
        for source, (text, language) in self.partials.items():
            if not text or (kind == "vi" and language != "vi"):
                continue
            paragraphs.append(("MIC" if source == "mic" else "REMOTE") + "  " + text)
        return "\n".join(paragraphs)

    def _render(self, force=False):
        if not force and not self.winfo_viewable():
            return
        for kind, pane in (("stt", self.stt), ("vi", self.vi)):
            new_text = self._text_for(kind)
            if self._last_text[kind] == new_text:
                continue
            self._last_text[kind] = new_text
            position = pane.text.yview()[0]
            pane.text.configure(state="normal")
            pane.text.delete("1.0", "end")
            pane.text.insert("1.0", new_text)
            pane.text.configure(state="disabled")
            if self.follow.get():
                pane.text.see("end")
            else:
                pane.text.yview_moveto(position)
            if self.auto_expand.get() and pane.winfo_viewable():
                self._expand_pane(pane)

    def _expand_pane(self, pane):
        # Grow only for new lines, never collapse a user-resized window.
        max_height = max(220, int(pane.winfo_screenheight() * .65))
        current_height = pane.winfo_height()
        if current_height >= max_height:
            return
        width = max(300, pane.winfo_width() - 45)
        size = self.stt_size.get() if pane.kind == "stt" else self.vi_size.get()
        chars = max(16, round(width / max(4, size * .62)))
        text = pane.text.get("1.0", "end-1c")[-1200:]
        recent = text.splitlines()[-12:]
        rows = sum(max(1, (len(line) + chars - 1) // chars) for line in recent)
        desired = min(max_height, max(265, min(13, rows) * round(size * 1.5) + 115))
        if desired > current_height + 18:
            pane.geometry(f"{pane.winfo_width()}x{desired}")

    def _user_scrolled(self, source):
        if self._syncing:
            return
        pane = self.stt if source == "stt" else self.vi
        bottom = pane.text.yview()[1] >= .98
        self.follow.set(bottom)
        if not self.sync_scroll.get() or self.view.get() != "Cả hai":
            return
        other = self.vi if source == "stt" else self.stt
        if not other.winfo_viewable():
            return
        self._syncing = True
        try:
            other.text.yview_moveto(pane.text.yview()[0])
        finally:
            self._syncing = False


class TranscriptCard(ctk.CTkFrame):
    def __init__(self, parent, row, *, translation_enabled=True):
        self.translation_enabled = translation_enabled
        super().__init__(parent, fg_color=SURFACE, corner_radius=12,
                         border_width=1, border_color=LINE)
        self.data = row
        self.grid_columnconfigure(0, weight=1)
        self.header = label(self, "", size=10, weight="bold", color=TEAL)
        self.header.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
        self.translation = label(self, "", size=14, weight="bold", wraplength=650,
                                 anchor="w", justify="left")
        self.translation.grid(row=1, column=0, sticky="ew", padx=16, pady=(3, 4))
        self.original = label(self, "", size=12, color=MUTED, wraplength=650,
                              anchor="w", justify="left")
        self.original.grid(row=2, column=0, sticky="ew", padx=16, pady=(1, 12))
        self.bind("<Configure>", self._resize_text)
        self.refresh(True)

    def _resize_text(self, event):
        width=max(200, event.width-42)
        self.translation.configure(wraplength=width)
        self.original.configure(wraplength=width)

    def refresh(self, show_original, translation_enabled=None):
        if translation_enabled is not None:
            self.translation_enabled = bool(translation_enabled)
        d = self.data
        who = "MIC" if d["source"]=="mic" else "REMOTE"
        if d.get("speaker"):
            who += "  ·  " + d["speaker"]
        if d.get("overlap"):
            who += "  ·  NÓI CHỒNG"
        self.header.configure(text=who + "     " + d["time"])
        if not self.translation_enabled:
            self.translation.configure(text=d.get("original", ""))
            self.original.grid_remove()
            return
        txt = d.get("translation") or (d.get("original") if d.get("lang")=="vi" else "Đang dịch…")
        self.translation.configure(text=txt)
        if show_original and d.get("lang") != "vi" and d.get("original"):
            self.original.configure(text=d["original"])
            self.original.grid()
        else:
            self.original.grid_remove()


class SoundTextWindow(ctk.CTk):
    LANGS = [("Tiếng Việt", "vi"), ("日本語", "ja"), ("English", "en"), ("JA / EN Auto", "auto")]
    MODES = [("MIC + REMOTE", "both"), ("Chỉ microphone", "you"), ("Chỉ âm thanh máy", "remote")]
    RECORD_MODES = (("Ghi & dịch", True), ("Chỉ ghi lời thoại", False))

    def __init__(self, config, *, startup_hidden=False):
        super().__init__()
        if startup_hidden:
            self.withdraw()
        self.config = config
        self.branding = load_branding()
        self.bus = MeetingUIBus()
        self.controller = RecordingController(self.bus)
        self.entries = []
        self.entry_by_key = {}
        self.current_folder = ""
        self.last_folder = ""
        self.last_error = ""
        self.started_at = None
        self.is_recording = False
        self._session_translate = bool(config.translation.enabled)
        self.overlay = None
        self.max_history = max(30, int(config.meeting.ui_history_items))
        self._next_card_row = 1
        self.title(str(self.branding.get("app_name") or "SoundText") + " — Studio")
        self.geometry("1260x820")
        self.minsize(970, 650)
        self.configure(fg_color=BG)
        # CTk can restore its bundled icon after window creation on Windows.
        # Install the configured icon and re-apply it after CTk's delayed setup.
        self._window_icon = WindowIconManager(str(self.branding.get("icon_path") or ""))
        self._window_icon.install(self)
        self._build()
        self._set_state("idle", "Sẵn sàng. Chọn nguồn và nhấn Bắt đầu ghi âm.")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._closed = False
        self._poll_ms = max(30, int(config.meeting.ui_refresh_ms))
        self.after(self._poll_ms, self._tick)

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        side = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=0, width=225,
                            border_width=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        label(side, "ST", size=26, weight="bold", color=TEAL).grid(row=0, column=0, sticky="w", padx=24, pady=(29, 2))
        self.brand_label = label(side, str(self.branding.get("app_name") or "SoundText"),
                                 size=22, weight="bold", wraplength=195, justify="left")
        self.brand_label.grid(row=1, column=0, sticky="w", padx=24)
        label(side, "GHI ÂM VÀ LỜI THOẠI", size=10, color=MUTED).grid(row=2, column=0,
            sticky="w", padx=24, pady=(5, 38))
        self.nav_live = button(side, "Trực tiếp", lambda: self._show_page("live"))
        self.nav_live.grid(row=3, column=0, sticky="ew", padx=16, pady=5)
        self.nav_archive = button(side, "Nội dung cuộc họp", lambda: self._show_page("archive"))
        self.nav_archive.grid(row=4, column=0, sticky="ew", padx=16, pady=5)
        side.grid_rowconfigure(6, weight=1)
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=26, pady=23)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 17))
        header.grid_columnconfigure(0, weight=1)
        label(header, "KHÔNG GIAN CUỘC HỌP", size=10, weight="bold", color=TEAL).grid(row=0, column=0, sticky="w")
        label(header, "Lời thoại trực tiếp", size=26, weight="bold").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.state_pill = label(header, "CHƯA GHI ÂM", size=11, weight="bold", color=TEAL,
            fg_color=TEAL_SOFT, corner_radius=8, width=155, height=36)
        self.state_pill.grid(row=0, column=1, rowspan=2, sticky="e")
        self.content = ctk.CTkFrame(main, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.live_page = ctk.CTkFrame(self.content, fg_color="transparent")
        self.archive_page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._build_live(self.live_page)
        self._build_archive(self.archive_page)
        self._show_page("live")
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.status_text = label(footer, "", size=11, color=MUTED, anchor="w",
                                 justify="left", wraplength=650)
        self.status_text.grid(row=0, column=0, sticky="w")
        self.elapsed = label(footer, "00:00:00", size=13, weight="bold")
        self.elapsed.grid(row=0, column=1, sticky="e")

    def _build_live(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)
        toolbar = surface(page)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        # Session content mode is chosen *before* pressing Start. It changes
        # only the copy of config passed to RecordingController, not core code.
        record_options = ctk.CTkFrame(toolbar, fg_color="transparent")
        record_options.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        label(record_options, "Nội dung", size=12, color=MUTED).pack(side="left", padx=(0, 12))
        self.record_mode_by_name = dict(self.RECORD_MODES)
        self.record_mode = ctk.CTkOptionMenu(
            record_options, values=list(self.record_mode_by_name),
            command=self._record_mode_changed, fg_color="#EEF3F5",
            button_color="#D7E4E6", button_hover_color="#C4D8DC",
            text_color=INK, font=style(12), width=185, height=34,
        )
        self.record_mode.set("Ghi & dịch" if self.config.translation.enabled else "Chỉ ghi lời thoại")
        self.record_mode.pack(side="left")
        self.record_mode_hint = label(record_options,
            "Có bản dịch tiếng Việt" if self.config.translation.enabled else "Chỉ lưu lời thoại gốc",
            size=11, color=MUTED)
        self.record_mode_hint.pack(side="left", padx=14)
        label(toolbar, "Nguồn thu", size=12, color=MUTED).pack(side="left", padx=(16, 10), pady=13)
        self.mode_by_name = dict(self.MODES)
        reverse = {v:k for k,v in self.MODES}
        self.mode = ctk.CTkOptionMenu(toolbar, values=list(self.mode_by_name), command=self._mode_changed,
            fg_color="#EEF3F5", button_color="#D7E4E6", button_hover_color="#C4D8DC",
            text_color=INK, font=style(12), width=155, height=36)
        self.mode.set(reverse.get(self.config.meeting.audio_mode, self.MODES[0][0]))
        self.mode.pack(side="left")
        self.folder_button = button(toolbar, "Mở thư mục lưu", self._open_folder)
        self.folder_button.pack(side="right", padx=(5, 13))
        self.stop_button = button(toolbar, "Dừng & lưu", self._stop, danger=True)
        self.stop_button.pack(side="right", padx=5)
        self.start_button = button(toolbar, "Bắt đầu ghi âm", self._start, primary=True)
        self.start_button.pack(side="right", padx=5)
        panels = ctk.CTkFrame(page, fg_color="transparent")
        panels.grid(row=1, column=0, sticky="ew", pady=(0, 18))
        panels.grid_columnconfigure(0, weight=1, uniform="same")
        panels.grid_columnconfigure(1, weight=1, uniform="same")
        self.live_labels, self.meters, self.languages = {}, {}, {}
        for i, source in enumerate(("mic", "system")):
            box = self._live_card(panels, source)
            box.grid(row=0, column=i, sticky="nsew", padx=(0, 7) if i==0 else (7, 0))
        section = ctk.CTkFrame(page, fg_color="transparent")
        section.grid(row=2, column=0, sticky="ew", pady=(0, 11))
        label(section, "Dòng hội thoại", size=17, weight="bold").pack(side="left")
        self.floating_btn = button(section, "Phụ đề nổi", self._toggle_overlay)
        self.floating_btn.pack(side="right")
        self.follow = tk.BooleanVar(value=True)
        self.show_original = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(section, text="Tự cuộn", variable=self.follow, width=90,
            fg_color=TEAL, font=style(11)).pack(side="right", padx=(7, 13))
        ctk.CTkCheckBox(section, text="Hiện câu gốc", variable=self.show_original,
            fg_color=TEAL, font=style(11), command=self._refresh_cards).pack(side="right", padx=9)
        self.timeline = ctk.CTkScrollableFrame(page, fg_color="transparent", corner_radius=0,
            scrollbar_button_color="#BCCDD2", scrollbar_button_hover_color="#95AFB6")
        self.timeline.grid(row=3, column=0, sticky="nsew")
        self.timeline.grid_columnconfigure(0, weight=1)
        self.empty_state = label(self.timeline, "Chưa có hội thoại. Nhấn Bắt đầu ghi âm để tạo phiên mới.",
                                 size=13, color=MUTED)
        self.empty_state.grid(row=0, column=0, sticky="w", pady=28, padx=15)
        # CTkScrollableFrame owns a Tk canvas; a user wheel means they chose to read history.
        self.timeline._parent_canvas.bind("<MouseWheel>", self._timeline_wheel, add="+")
        self.timeline._parent_canvas.bind("<Button-4>", self._timeline_wheel, add="+")
        self.timeline._parent_canvas.bind("<Button-5>", self._timeline_wheel, add="+")

    def _live_card(self, parent, source):
        mic = source == "mic"
        pane = surface(parent)
        pane.grid_columnconfigure(0, weight=1)
        title = "MICROPHONE" if mic else "ÂM THANH HỆ THỐNG"
        label(pane, title, size=10, weight="bold", color=TEAL).grid(
            row=0, column=0, sticky="w", padx=18, pady=(17, 4))
        name = label(pane, "Mic / bạn" if mic else "Remote / cuộc họp", size=17, weight="bold")
        name.grid(row=1, column=0, sticky="w", padx=18)
        langs = {t:v for t,v in self.LANGS}
        reverse = {v:t for t,v in self.LANGS}
        selected = self.config.meeting.mic_language if mic else self.config.meeting.system_language
        menu = ctk.CTkOptionMenu(pane, values=list(langs), height=33,
            fg_color="#EFF4F5", button_color="#D7E3E6", button_hover_color="#C7D9DD",
            text_color=INK, font=style(11), command=lambda v,s=source: self._language_changed(s,langs[v]))
        menu.set(reverse.get(selected, self.LANGS[0][0]))
        menu.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 10))
        self.languages[source] = menu
        meter = ctk.CTkProgressBar(pane, height=7, progress_color=TEAL,
            fg_color="#E7EDEF")
        meter.set(0)
        meter.grid(row=3, column=0, sticky="ew", padx=18)
        self.meters[source] = meter
        label(pane, "ĐANG NHẬN DIỆN", size=10, color=MUTED, weight="bold").grid(
            row=4, column=0, sticky="w", padx=18, pady=(14, 4))
        preview = label(pane, "Chưa có âm thanh.", size=12, color=INK,
            fg_color="#F2F6F7", corner_radius=9, height=77,
            wraplength=350, anchor="nw", justify="left")
        preview.grid(row=5, column=0, sticky="nsew", padx=18, pady=(0, 16))
        preview.bind("<Configure>", lambda e,w=preview: w.configure(wraplength=max(120,e.width-24)))
        self.live_labels[source] = preview
        return pane

    def _build_archive(self, page):
        page.grid_columnconfigure(0, weight=1)
        label(page, "NỘI DUNG ĐÃ LƯU", size=10, color=TEAL, weight="bold").grid(row=0,column=0,sticky="w")
        label(page, "Phiên ghi âm", size=25, weight="bold").grid(row=1,column=0,sticky="w",pady=(8,8))
        label(page, "Các phiên hội thoại được lưu trong thư mục output.",
              size=12,color=MUTED).grid(row=2,column=0,sticky="w",pady=(0,24))
        panel=surface(page)
        panel.grid(row=3,column=0,sticky="ew")
        self.archive_path = label(panel, "Chưa có phiên trong lần mở ứng dụng này.",
            size=12,color=MUTED,wraplength=630,justify="left")
        self.archive_path.pack(anchor="w",padx=22,pady=(22,12))
        actions=ctk.CTkFrame(panel,fg_color="transparent")
        actions.pack(anchor="w",padx=22,pady=(0,24))
        button(actions,"Mở thư mục lưu",self._open_folder,primary=True).pack(side="left",padx=(0,8))
        button(actions,"Sao chép hội thoại",self._copy_transcript).pack(side="left")

    def _show_page(self, value):
        for page in (self.live_page,self.archive_page):
            page.grid_forget()
        page=self.live_page if value=="live" else self.archive_page
        page.grid(row=0,column=0,sticky="nsew")
        self.nav_live.configure(fg_color=TEAL_SOFT if value=="live" else "#F0F4F5",
                                text_color=TEAL if value=="live" else INK)
        self.nav_archive.configure(fg_color=TEAL_SOFT if value=="archive" else "#F0F4F5",
                                   text_color=TEAL if value=="archive" else INK)

    def _set_state(self,state,message):
        self.status_text.configure(text=message)
        names={"idle":"CHƯA GHI ÂM","starting":"ĐANG CHUẨN BỊ","recording":"● ĐANG GHI ÂM",
               "stopping":"ĐANG LƯU","stopped":"ĐÃ LƯU","failed":"LỖI GHI ÂM"}
        self.state_pill.configure(text=names.get(state,state.upper()),
           fg_color=TEAL_SOFT if state=="recording" else "#EAF0F2",
           text_color=TEAL if state=="recording" else (DANGER if state=="failed" else MUTED))
        self.start_button.configure(state="normal" if state in ("idle","stopped","failed") else "disabled")
        self.stop_button.configure(state="normal" if state in ("starting","recording") else "disabled")
        self.record_mode.configure(state="normal" if state in ("idle","stopped","failed") else "disabled")
        self.mode.configure(state="normal" if state in ("idle","stopped","failed","recording") else "disabled")
        for menu in self.languages.values():
            menu.configure(state="normal" if state in ("idle","stopped","failed","recording") else "disabled")
        self.is_recording = state=="recording"

    def _start(self):
        if self.controller.busy:
            return
        for widget in list(self.entry_by_key.values()):
            widget.destroy()
        self.entries.clear()
        self.entry_by_key.clear()
        self._next_card_row = 1
        self.empty_state.grid()
        for text in self.live_labels.values():
            text.configure(text="Đang chờ âm thanh...")
        for meter in self.meters.values():
            meter.set(0)
        if self.overlay is not None:
            self.overlay.reset()
        self.started_at = None
        self.elapsed.configure(text="00:00:00")
        self.last_error = ""
        self._session_translate = self.record_mode_by_name[self.record_mode.get()]
        self.config.translation.enabled = self._session_translate
        if self.overlay is not None:
            self.overlay.set_translation_enabled(self._session_translate)
        self._set_state("starting", "Đang chuẩn bị thiết bị ghi âm...")
        if not self.controller.start(self.config):
            self._set_state("idle", "Không thể bắt đầu khi phiên trước chưa kết thúc.")

    def _stop(self):
        self.controller.stop()
        self._set_state("stopping", "Đang hoàn tất và lưu phiên ghi âm...")

    def _record_mode_changed(self, value):
        enabled = self.record_mode_by_name[value]
        self.record_mode_hint.configure(
            text="Có bản dịch tiếng Việt" if enabled else "Chỉ lưu lời thoại gốc"
        )
        if self.overlay is not None:
            self.overlay.set_translation_enabled(enabled, choose_default=True)

    def _mode_changed(self, value):
        mode=self.mode_by_name[value]
        self.config.meeting.audio_mode=mode
        self.controller.set_mode(mode)
        disabled = "mic" if mode == "remote" else ("system" if mode == "you" else None)
        if disabled is not None:
            self.meters[disabled].set(0)
            self.live_labels[disabled].configure(text="Không sử dụng trong chế độ này.")

    def _language_changed(self, source, value):
        if source=="mic":
            self.config.meeting.mic_language=value
        else:
            self.config.meeting.system_language=value
        self.controller.set_language(source,value)

    def _tick(self):
        if self._closed:
            return
        for _ in range(20):
            try:
                status=self.controller.statuses.get_nowait()
            except Empty:
                break
            if status.session_dir:
                self.current_folder=status.session_dir
                self.last_folder=status.session_dir
                self.archive_path.configure(text="Phiên gần nhất: "+status.session_dir)
            if status.kind=="recording":
                self.started_at=time.monotonic()
                self._set_state("recording",status.message)
            elif status.kind=="stopped":
                if self.started_at is not None:
                    self._update_clock()
                    self.started_at=None
                self._set_state("failed" if (self.last_error or status.message) else "stopped",
                    self.last_error or status.message or "Đã lưu phiên. Có thể bắt đầu phiên mới.")
            elif status.kind=="failed":
                self.last_error=status.message
                self._set_state("failed",status.message)
            else:
                self._set_state(status.kind,status.message)
        for _ in range(42):
            try:
                event=self.bus.queue.get_nowait()
            except Empty:
                break
            self._process_event(event)
        if self.is_recording:
            selected_mode = self.config.meeting.audio_mode
            for source in ("mic", "system"):
                if selected_mode == "remote" and source == "mic" or selected_mode == "you" and source == "system":
                    self.meters[source].set(0)
            for source,levels in self.bus.snapshot_audio_levels().items():
                enabled = selected_mode == "both" or (selected_mode == "remote" and source == "system") or (selected_mode == "you" and source == "mic")
                if enabled and source in self.meters:
                    db=levels[0]
                    self.meters[source].set(max(0,min(1,(db+66)/63)))
        self._update_clock()
        self.after(self._poll_ms,self._tick)

    def _update_clock(self):
        if self.started_at is not None:
            seconds=int(time.monotonic()-self.started_at)
            self.elapsed.configure(text=f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}")

    def _process_event(self, event: UIEvent):
        if event.stage=="status":
            if event.message:
                self.status_text.configure(text=event.message)
            return
        if event.stage not in ("asr","translation") or event.source not in ("mic","system"):
            return
        key=(event.source,event.utterance_id)
        if event.stage=="asr":
            if not event.final:
                if event.source_text:
                    self.live_labels[event.source].configure(text=event.source_text)
                    if self.overlay is not None:
                        self.overlay.update_partial(event.source,event.source_text,event.source_language)
                return
            if not event.source_text.strip():
                return
            self.live_labels[event.source].configure(text=event.source_text)
            if self.overlay is not None:
                self.overlay.update_partial(event.source,"")
            if key not in self.entry_by_key:
                row=dict(source=event.source,id=event.utterance_id,original=event.source_text,
                    translation="",lang=event.source_language,time=datetime.now().strftime("%H:%M:%S"),
                    speaker=event.speaker_id or "",overlap=bool(event.speaker_overlap))
                widget=TranscriptCard(self.timeline,row,translation_enabled=self._session_translate)
                self.empty_state.grid_remove()
                widget.grid(row=self._next_card_row,column=0,sticky="ew",padx=(0,8),pady=5)
                self._next_card_row += 1
                self.entry_by_key[key]=widget
                self.entries.append(row)
                while len(self.entries)>self.max_history:
                    old=self.entries.pop(0)
                    stale=self.entry_by_key.pop((old["source"],old["id"]),None)
                    if stale:
                        stale.destroy()
                if self._next_card_row > self.max_history * 2:
                    for i, item in enumerate(self.entries, start=1):
                        current = self.entry_by_key.get((item["source"], item["id"]))
                        if current is not None:
                            current.grid_configure(row=i)
                    self._next_card_row = len(self.entries) + 1
                self._scroll_to_latest()
                if self.overlay is not None:
                    self.overlay.update_row(row)
            return
        widget=self.entry_by_key.get(key)
        if widget is not None:
            widget.data["translation"]=event.translated_text
            widget.refresh(self.show_original.get(), self._session_translate)
            if self.overlay is not None:
                self.overlay.update_row(widget.data)
            self._scroll_to_latest()

    def _timeline_wheel(self, _event):
        self.after_idle(self._timeline_user_scrolled)

    def _timeline_user_scrolled(self):
        if self.timeline._parent_canvas.yview()[1]>=0.98:
            self.follow.set(True)
        else:
            self.follow.set(False)

    def _scroll_to_latest(self):
        if self.follow.get():
            self.after_idle(lambda: self.timeline._parent_canvas.yview_moveto(1.0))

    def _refresh_cards(self):
        for widget in self.entry_by_key.values():
            widget.refresh(self.show_original.get(), self._session_translate)

    def _toggle_overlay(self):
        if self.overlay is None or not self.overlay.winfo_exists():
            self.overlay=FloatingSubtitles(self, on_visibility_change=self._overlay_visibility_changed)
            self.overlay.set_translation_enabled(self._session_translate, choose_default=True)
            self.overlay.set_entries(self.entries)
            self.overlay.withdraw()
        if self.overlay.winfo_viewable():
            self.overlay.withdraw()
            self.floating_btn.configure(text="Phụ đề nổi")
        else:
            self.overlay.show_overlay()
            self.floating_btn.configure(text="Ẩn phụ đề nổi")

    def _overlay_visibility_changed(self, visible):
        self.floating_btn.configure(text="Ẩn phụ đề nổi" if visible else "Phụ đề nổi")

    def _open_folder(self):
        try:
            session = Path(self.last_folder) if self.last_folder else Path(self.config.storage.output_dir)
            readable = session / "Noi_dung"
            open_session_directory(str(readable if readable.is_dir() else session))
        except Exception as exc:
            messagebox.showwarning("Không thể mở thư mục",str(exc),parent=self)

    def _copy_transcript(self):
        parts=[]
        for item in self.entries:
            parts.append(f'[{item["time"]}] {item["source"].upper()} {item.get("speaker","")}\n'+
                         item["original"]+'\n'+(item["translation"] or ""))
        self.clipboard_clear()
        self.clipboard_append("\n\n".join(parts))
        self.status_text.configure(text=f"Đã sao chép {len(parts)} lượt nói.")

    def _close(self):
        if self.controller.busy:
            if not messagebox.askyesno("Đang ghi âm", "Dừng và lưu phiên ghi âm trước khi thoát?",parent=self):
                return
            self.controller.stop()
            self._set_state("stopping","Đang lưu trước khi thoát...")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.after(90,self._close_after_stop)
            return
        self._closed=True
        if self.overlay is not None and self.overlay.winfo_exists():
            self.overlay.destroy()
        self.destroy()

    def _close_after_stop(self):
        if self.controller.busy:
            self.after(90,self._close_after_stop)
        else:
            self._closed=True
            if self.overlay is not None and self.overlay.winfo_exists():
                self.overlay.destroy()
            self.destroy()


def run_ctk_application(config):
    """Show splash while all selected models load, then reveal the idle UI.

    No recording port, session folder or inference worker is started here.
    Tk operations remain on the main thread; model loading runs in the loader
    thread and gives ownership of the prepared sessions to RecordingController.
    """
    configure_windows_app_identity()
    app = SoundTextWindow(config, startup_hidden=True)
    branding = load_branding()
    splash = None
    info = None
    config_warning = branding_file_error()
    if config_warning:
        image_path, art_warning = None, config_warning
    else:
        image_path, art_warning = choose_splash_art(branding)
    if art_warning:
        print(f"[SPLASH WARN] {art_warning}")
    try:
        # Always show loading feedback until the selected models have finished
        # initialization. A missing/broken custom image is reported visibly;
        # it must NOT be silently replaced with the bundled stock image.
        splash = ctk.CTkToplevel(app)
        splash.overrideredirect(True)
        splash.attributes("-topmost", True)
        splash.configure(fg_color="#14222D")

        art_loaded = False
        if image_path is not None:
            try:
                from PIL import Image, ImageTk
                with Image.open(image_path) as pic:
                    raw = pic.convert("RGBA")
                    raw.thumbnail((760, 480), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(raw, master=splash)
                image_label = tk.Label(splash, image=photo, bd=0, bg="#14222D")
                image_label.image = photo  # Keep alive until splash closes.
                image_label.pack()
                art_loaded = True
                print(f"[SPLASH] Using configured art: {image_path}")
            except Exception as exc:
                art_warning = f"Không mở được ảnh splash {image_path.name}: {exc}"
                print(f"[SPLASH WARN] {art_warning}")

        if not art_loaded:
            tk.Label(splash, text=str(branding.get("app_name") or "SoundText"),
                     bg="#14222D", fg="#FFFFFF", font=(FONT, 24, "bold"),
                     padx=80, pady=35).pack()
        if art_warning:
            tk.Label(splash, text=art_warning, bg="#14222D", fg="#FFCF99",
                     font=(FONT, 10), wraplength=600, padx=20, pady=6).pack(fill="x")
        info = tk.Label(splash, text="Đang chuẩn bị ứng dụng…", bg="#14222D",
                        fg="#C8DEE0", font=(FONT, 11), pady=13, padx=20)
        info.pack(fill="x")
        splash.update_idletasks()
        sw, sh = splash.winfo_screenwidth(), splash.winfo_screenheight()
        w, h = splash.winfo_reqwidth(), splash.winfo_reqheight()
        splash.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")
        splash.lift()
    except Exception as exc:
        print(f"[SPLASH WARN] Cannot create splash window: {exc}")
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass
        splash = None
        info = None

    messages = SimpleQueue()

    def load_models():
        # Keep the full traceback in output/logs/startup.log in frozen mode.
        # The splash used to discard everything except the final OSError text,
        # hiding which model or package actually failed.
        last_stage = "Đang chuẩn bị ứng dụng"

        def report_progress(value):
            nonlocal last_stage
            last_stage = str(value)
            messages.put(("progress", value))

        try:
            from app.ui.model_preloader import prepare_models
            models = prepare_models(config, progress=report_progress)
            messages.put(("ready", models))
        except Exception as exc:
            print(f"[MODEL ERROR] Stage: {last_stage}\n{traceback.format_exc()}",
                  file=sys.stderr, flush=True)
            messages.put(("error", f"{last_stage}\n{type(exc).__name__}: {exc}\n"
                                   "Chi tiết: output/logs/startup.log"))

    Thread(target=load_models, daemon=True, name="soundtext-startup-models").start()

    def finish():
        app.deiconify()
        app.update_idletasks()
        if splash is not None and splash.winfo_exists():
            splash.destroy()
        app.lift()

    def poll_load():
        if app._closed:
            return
        result = None
        for _ in range(40):
            try:
                kind, payload = messages.get_nowait()
            except Empty:
                break
            if kind == "progress":
                if info is not None:
                    info.configure(text=payload)
            else:
                result = (kind, payload)
                break
        if result is None:
            app.after(70, poll_load)
            return
        kind, payload = result
        if kind == "ready":
            app.controller.set_prepared_models(payload)
            finish()
            app._set_state("idle", "Sẵn sàng. Chọn nguồn và nhấn Bắt đầu ghi âm.")
            if payload.warnings:
                app.status_text.configure(text="; ".join(payload.warnings))
                for warning in payload.warnings:
                    print("[MODEL WARN]", warning)
        else:
            finish()
            app._set_state("failed", "Không tải được mô hình. Xem thông báo để kiểm tra.")
            app.start_button.configure(state="disabled")
            app.status_text.configure(text="Không tải được mô hình: " + payload)
            print("[MODEL ERROR]", payload)
            messagebox.showerror("Không thể khởi tạo SoundText",
                                 "Không tải được mô hình cần thiết.\n\n" + payload,
                                 parent=app)

    app.after(40, poll_load)
    app.mainloop()
    return 0
