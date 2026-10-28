from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import queue
import threading
import time
from typing import Callable

from app.ui.branding import (
    DEFAULTS as BRAND_DEFAULTS, load_branding, save_branding,
    resolve_branding_path, store_brand_asset, open_session_directory,
)


@dataclass(slots=True)
class UIEvent:
    source: str
    utterance_id: int = -1
    stage: str = "asr"  # asr | translation | status
    final: bool = False
    source_text: str = ""
    translated_text: str = ""
    source_language: str = ""
    target_language: str = "vi"
    stt_latency_ms: float = 0.0
    translation_latency_ms: float = 0.0
    translation_wait_ms: float = 0.0
    speaker_id: str | None = None
    speaker_overlap: bool = False
    message: str = ""


class MeetingUIBus:
    """Text events + coalesced audio meters.

    STT/final/translation events remain lossless in the queue. Audio-level
    samples are overwrite-only snapshots so a busy UI can never accumulate
    thousands of VU-meter events.
    """

    def __init__(self) -> None:
        self.queue: queue.SimpleQueue[UIEvent] = queue.SimpleQueue()
        self._level_lock = threading.Lock()
        self._audio_levels: dict[str, tuple[float, float, float | None]] = {}

    def publish(self, event: UIEvent) -> None:
        self.queue.put(event)

    def publish_audio_level(
        self,
        source: str,
        *,
        dbfs: float,
        peak_dbfs: float,
        clean_dbfs: float | None = None,
    ) -> None:
        with self._level_lock:
            self._audio_levels[str(source)] = (
                float(dbfs),
                float(peak_dbfs),
                None if clean_dbfs is None else float(clean_dbfs),
            )

    def clear_audio_level(self, source: str) -> None:
        with self._level_lock:
            self._audio_levels.pop(str(source), None)

    def snapshot_audio_levels(self) -> dict[str, tuple[float, float, float | None]]:
        with self._level_lock:
            return dict(self._audio_levels)


class MeetingWindow:
    """Meeting-first UI.

    The top area is intentionally compact and "live".
    Final utterances from YOU + REMOTE are merged into one chronological
    conversation timeline so a meeting can be read naturally from top to bottom.
    Vietnamese is visually primary; source text is secondary and can be hidden.
    """

    MODE_LABELS = {"both": "BOTH", "remote": "REMOTE", "you": "YOU"}
    LANG_LABELS = {
        "vi": "Tiếng Việt",
        "ja": "日本語",
        "en": "English",
        "auto": "JA / EN Auto",
    }
    SPEAKER_LABELS = {"mic": "YOU", "system": "REMOTE"}

    # Calm, editorial desktop palette: neutral paper + one restrained accent.
    # Audio, ASR, translation, and floating subtitle behavior are unchanged.
    BG = "#F3F6FA"
    SURFACE = "#FFFFFF"
    SURFACE_2 = "#F0F4F8"
    BORDER = "#DCE4ED"
    TEXT = "#213247"
    TEXT_SOFT = "#52657A"
    TEXT_MUTED = "#718398"
    ACCENT = "#22688D"
    ACCENT_SOFT = "#E3EFF6"
    GREEN = "#16856A"
    GREEN_SOFT = "#E4F4ED"
    REMOTE = "#7055A3"
    REMOTE_SOFT = "#F0EDFA"
    WARNING = "#D09A35"

    def __init__(
        self,
        bus: MeetingUIBus,
        *,
        title: str,
        refresh_ms: int = 60,
        history_items: int = 120,
        initial_audio_mode: str = "both",
        on_audio_mode_change: Callable[[str], None] | None = None,
        initial_you_language: str = "vi",
        initial_remote_language: str = "ja",
        on_you_language_change: Callable[[str], None] | None = None,
        on_remote_language_change: Callable[[str], None] | None = None,
        translation_available: bool = True,
        translation_status: str = "",
        session_dir: str = "",
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.branding = load_branding()
        self._default_window_title = str(title)
        self._window_icon = None
        self._splash_window = None
        self._splash_photo = None
        self.root.title(str(self.branding.get("app_name") or "SoundText") + "  ·  Trực tiếp")
        self.root.geometry("1320x840")
        self.root.minsize(1000, 680)
        self.root.configure(bg=self.BG)
        self._apply_brand_icon()

        self.bus = bus
        self.refresh_ms = max(30, int(refresh_ms))
        self.history_items = max(30, int(history_items))
        self.on_audio_mode_change = on_audio_mode_change
        self.on_you_language_change = on_you_language_change
        self.on_remote_language_change = on_remote_language_change
        self.translation_available = bool(translation_available)
        self.translation_status = str(translation_status or "")
        self.session_dir = str(session_dir or "")
        self.stop_requested = False

        self._timeline: list[dict[str, object]] = []
        self._last_final_id: dict[str, int | None] = {"mic": None, "system": None}
        self._session_started = time.monotonic()
        self._status_extra = ""

        self._setup_styles()

        mode = str(initial_audio_mode or "both").strip().lower()
        if mode not in self.MODE_LABELS:
            mode = "both"

        self.audio_mode = tk.StringVar(value=mode)
        self.you_language = tk.StringVar(value=self._valid_lang(initial_you_language, "vi"))
        self.remote_language = tk.StringVar(value=self._valid_lang(initial_remote_language, "ja"))
        self.show_original = tk.BooleanVar(value=True)
        self.auto_follow = tk.BooleanVar(value=True)
        self.subtitle_enabled = tk.BooleanVar(value=False)
        self.subtitle_mode = tk.StringVar(value="translation")
        self.subtitle_show_original = tk.BooleanVar(value=True)
        self.subtitle_stt_font_size = tk.IntVar(value=13)
        self.subtitle_vi_font_size = tk.IntVar(value=18)
        self.subtitle_auto_follow = tk.BooleanVar(value=True)
        self.subtitle_sync_scroll = tk.BooleanVar(value=True)
        # Applies to the floating overlay, not just the main meeting window.
        self.subtitle_auto_expand = tk.BooleanVar(value=True)
        self.elapsed_text = tk.StringVar(value="00:00")
        self.timeline_summary = tk.StringVar(value="Chưa có hội thoại")

        # Two independent subtitle windows. subtitle_window remains an alias to
        # the STT window for compatibility with older helper code.
        self.subtitle_window = None
        self.subtitle_stt_window = None
        self.subtitle_vi_window = None
        self.subtitle_speaker = None
        self.subtitle_source = None
        self.subtitle_translation = None
        self.subtitle_stt_title = None
        self.subtitle_vi_title = None
        self.subtitle_resize_grip = None
        self._subtitle_last_source = "system"
        self._subtitle_last_utterance_id = -1

        self._subtitle_stt_width = 620
        self._subtitle_stt_height = 300
        self._subtitle_vi_width = 640
        self._subtitle_vi_height = 300
        self._subtitle_expand_after_id = None
        self._subtitle_scroll_syncing = False
        self._subtitle_sync_after_id = None
        self._subtitle_last_preview_text = ""
        self._subtitle_rebuilding = False
        self._subtitle_open_after_id = None

        self._meter_smoothed = {"mic": -72.0, "system": -72.0}
        self._meter_last_seen = {"mic": 0.0, "system": 0.0}

        # Subtitle overlay keeps finalized STT/VI history instead of replacing
        # one label every time a new utterance arrives.
        self._subtitle_rows: list[dict[str, object]] = []
        self._subtitle_row_by_key: dict[tuple[str, int], dict[str, object]] = {}
        self._subtitle_sequence = 0
        self._subtitle_live_preview = None
        self._subtitle_vi_live_preview = None
        self._subtitle_stt_text = None
        self._subtitle_vi_text = None
        self._subtitle_last_partial_render = 0.0

        shell = ttk.Frame(self.root, style="App.TFrame", padding=(24, 20, 24, 14))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        self._build_header(shell)
        self._build_live_area(shell)
        self._build_timeline_toolbar(shell)
        self._build_timeline(shell)
        self._build_footer(shell)

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_mode_hint()
        self._refresh_language_labels()
        self._refresh_footer()
        self._update_elapsed()
        # Non-modal; never block capture or UI event pumping.
        self.root.after(250, self._show_splash)

    def _setup_styles(self) -> None:
        ttk = self.ttk
        # ttkbootstrap uses existing ttk widgets and leaves the complex floating
        # subtitle implementation untouched. Clam is the safe offline fallback.
        try:
            import ttkbootstrap as ttkb
            style = ttkb.Style(theme="flatly")
        except Exception:
            style = ttk.Style(self.root)
            try:
                style.theme_use("clam")
            except Exception:
                pass
        self._style = style

        style.configure("App.TFrame", background=self.BG)
        style.configure("Surface.TFrame", background=self.SURFACE)
        style.configure("Surface2.TFrame", background=self.SURFACE_2)
        style.configure("Header.TFrame", background=self.SURFACE)
        style.configure("Toolbar.TFrame", background=self.SURFACE)
        style.configure("TranslationSurface.TFrame", background="#EAF3F9")

        style.configure(
            "Brand.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI Variable Display", 25, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "TopMeta.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Timer.TLabel",
            background=self.GREEN_SOFT,
            foreground="#136E56",
            font=("Segoe UI", 9, "bold"),
            padding=(11, 6),
        )
        style.configure(
            "Target.TLabel",
            background=self.ACCENT_SOFT,
            foreground="#245C78",
            font=("Segoe UI", 9, "bold"),
            padding=(8, 4),
        )

        style.configure(
            "LiveTitle.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "YouTitle.TLabel",
            background=self.SURFACE,
            foreground="#28638D",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "RemoteTitle.TLabel",
            background=self.SURFACE,
            foreground="#694C9A",
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "LiveMeta.TLabel",
            background=self.SURFACE,
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 8),
        )
        style.configure(
            "Activity.TLabel",
            background=self.SURFACE,
            foreground=self.GREEN,
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "Source.TLabel",
            background=self.SURFACE_2,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 11),
            padding=(12, 8),
        )
        style.configure(
            "Translation.TLabel",
            background="#EAF3F9",
            foreground=self.TEXT,
            font=("Segoe UI", 14, "bold"),
            padding=(13, 11),
        )
        style.configure(
            "TranslationSection.TLabel",
            background="#EAF3F9",
            foreground="#28638D",
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "SectionLabel.TLabel",
            background=self.SURFACE_2,
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "TimelineTitle.TLabel",
            background=self.BG,
            foreground=self.TEXT,
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=self.BG,
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Status.TLabel",
            background=self.BG,
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 8),
        )

        style.configure(
            "Mode.TRadiobutton",
            background=self.SURFACE,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Mode.TRadiobutton",
            background=[("active", self.SURFACE)],
            foreground=[("selected", self.TEXT)],
        )
        style.configure(
            "Lang.TCombobox", padding=(8, 6), fieldbackground=self.SURFACE,
            background=self.SURFACE, foreground=self.TEXT, arrowcolor=self.ACCENT,
            bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER,
        )
        style.map("Lang.TCombobox", fieldbackground=[("readonly", self.SURFACE)])

        style.configure(
            "Tool.TCheckbutton",
            background=self.SURFACE,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 9),
        )
        style.map(
            "Tool.TCheckbutton",
            background=[("active", self.SURFACE)],
            foreground=[("active", self.TEXT)],
        )

        style.configure(
            "Tool.TButton",
            background=self.SURFACE_2,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 9, "bold"),
            padding=(10, 6),
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Tool.TButton",
            background=[("active", self.BORDER)],
            foreground=[("active", self.TEXT)],
        )
        style.configure(
            "Primary.TButton",
            background=self.ACCENT,
            foreground="#FFFFFF",
            font=("Segoe UI", 9, "bold"),
            padding=(12, 7),
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#174F70")],
            foreground=[("active", "#FFFFFF")],
        )
        style.configure(
            "Setting.TRadiobutton",
            background=self.SURFACE,
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 9),
        )
        style.map(
            "Setting.TRadiobutton",
            background=[("active", self.SURFACE)],
            foreground=[("selected", self.TEXT), ("active", self.TEXT)],
        )

        style.configure(
            "Secondary.TButton", background=self.SURFACE, foreground=self.ACCENT,
            font=("Segoe UI", 9, "bold"), padding=(11, 8), relief="flat",
            borderwidth=1, bordercolor=self.BORDER,
        )
        style.map(
            "Secondary.TButton",
            background=[("active", self.ACCENT_SOFT)],
            foreground=[("active", self.ACCENT)],
        )
        style.configure(
            "DialogTitle.TLabel", background=self.SURFACE, foreground=self.TEXT,
            font=("Segoe UI", 16, "bold"),
        )
        style.configure(
            "DialogDesc.TLabel", background=self.SURFACE, foreground=self.TEXT_MUTED,
            font=("Segoe UI", 9),
        )

    def _apply_brand_icon(self) -> None:
        """Set runtime Tk window/taskbar icon; packaged EXE metadata is separate."""
        icon = resolve_branding_path(str(self.branding.get("icon_path") or ""))
        if icon is None:
            return
        try:
            if icon.suffix.lower() == ".ico":
                self.root.iconbitmap(str(icon))
            else:
                # PNG/GIF is supported by Tk PhotoImage; keep a strong reference.
                self._window_icon = self.tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, self._window_icon)
        except Exception as exc:
            print(f"[UI] Could not apply app icon: {exc}")

    def _show_splash(self) -> None:
        """Optional asynchronous splash; image is user-replaceable."""
        if self.stop_requested or not bool(self.branding.get("show_splash", True)):
            return
        path = resolve_branding_path(str(self.branding.get("splash_path") or ""))
        if path is None:
            return
        try:
            tk = self.tk
            win = tk.Toplevel(self.root)
            self._splash_window = win
            win.overrideredirect(True)
            win.configure(bg=self.SURFACE)
            # PIL is already installed with ttkbootstrap; fallback to Tk PNG/GIF.
            try:
                from PIL import Image, ImageTk
                with Image.open(path) as source:
                    img = source.convert("RGB")
                    img.thumbnail((850, 520), Image.Resampling.LANCZOS)
                    self._splash_photo = ImageTk.PhotoImage(img, master=win)
            except (ImportError, OSError):
                self._splash_photo = tk.PhotoImage(file=str(path), master=win)
            width, height = self._splash_photo.width(), self._splash_photo.height()
            tk.Label(win, image=self._splash_photo, bg=self.SURFACE, bd=0).pack()
            x = max(0, (win.winfo_screenwidth() - width) // 2)
            y = max(0, (win.winfo_screenheight() - height) // 2)
            win.geometry(f"{width}x{height}+{x}+{y}")
            win.attributes("-topmost", True)
            duration = max(300, min(5000, int(self.branding.get("splash_duration_ms", 1400))))
            self.root.after(duration, self._dismiss_splash)
        except Exception as exc:
            print(f"[UI] Splash skipped: {exc}")
            self._dismiss_splash()

    def _dismiss_splash(self) -> None:
        win = self._splash_window
        self._splash_window = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._splash_photo = None

    def _open_session_folder(self) -> None:
        from tkinter import messagebox
        try:
            open_session_directory(self.session_dir or "output")
        except Exception as exc:
            messagebox.showerror("Không mở được thư mục", str(exc), parent=self.root)

    def _open_branding_settings(self) -> None:
        """Edit user-visible metadata without touching the inference pipeline."""
        from tkinter import filedialog, messagebox
        tk, ttk = self.tk, self.ttk
        win = tk.Toplevel(self.root)
        win.title("Tùy chỉnh ứng dụng · SoundText")
        win.geometry("670x540")
        win.minsize(570, 490)
        win.configure(bg=self.SURFACE)
        win.transient(self.root)
        win.grab_set()
        self._apply_brand_icon()

        body = ttk.Frame(win, style="Surface.TFrame", padding=(25, 23))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="Tùy chỉnh ứng dụng", style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Tên, biểu tượng và ảnh mở đầu — lưu riêng trong thư mục dự án.",
                  style="DialogDesc.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 19))

        name = tk.StringVar(value=str(self.branding.get("app_name", "SoundText")))
        tagline = tk.StringVar(value=str(self.branding.get("tagline", "")))
        icon = tk.StringVar(value=str(self.branding.get("icon_path", "")))
        splash = tk.StringVar(value=str(self.branding.get("splash_path", "")))
        enabled = tk.BooleanVar(value=bool(self.branding.get("show_splash", True)))

        def entry_row(row: int, title: str, var, chooser=None):
            ttk.Label(body, text=title, style="LiveTitle.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(8, 6))
            frame = ttk.Frame(body, style="Surface.TFrame")
            frame.grid(row=row+1, column=0, columnspan=2, sticky="ew", pady=(0, 3))
            frame.columnconfigure(0, weight=1)
            ttk.Entry(frame, textvariable=var, font=("Segoe UI", 10)).grid(
                row=0, column=0, sticky="ew", ipady=4)
            if chooser:
                ttk.Button(frame, text="Chọn ảnh…", command=chooser, style="Secondary.TButton").grid(
                    row=0, column=1, padx=(10, 0))

        def pick(var, patterns):
            value = filedialog.askopenfilename(
                parent=win, title="Chọn hình ảnh", filetypes=patterns + [("Tất cả", "*.*")]
            )
            if value:
                var.set(value)

        entry_row(2, "Tên hiển thị", name)
        entry_row(4, "Dòng mô tả", tagline)
        entry_row(6, "Icon ứng dụng · PNG / ICO / GIF", icon,
                  lambda: pick(icon, [("Biểu tượng", "*.png *.ico *.gif")]))
        entry_row(8, "Splash screen · PNG / JPG / GIF", splash,
                  lambda: pick(splash, [("Hình ảnh", "*.png *.jpg *.jpeg *.gif")]))
        ttk.Checkbutton(body, text="Hiện splash khi mở SoundText",
                        variable=enabled, style="Tool.TCheckbutton").grid(
            row=10, column=0, sticky="w", pady=(10, 4))
        ttk.Label(body, text="Ảnh được sao chép vào assets/branding/. Không lưu đường dẫn ra ngoài dự án.",
                  style="DialogDesc.TLabel").grid(row=11, column=0, columnspan=2, sticky="w")
        controls = ttk.Frame(body, style="Surface.TFrame")
        controls.grid(row=12, column=0, columnspan=2, sticky="e", pady=(20, 0))

        def save():
            try:
                next_brand = dict(self.branding)
                next_brand["app_name"] = name.get()
                next_brand["tagline"] = tagline.get()
                next_brand["show_splash"] = bool(enabled.get())
                for field, selected, kind in (("icon_path", icon.get(), "icon"),
                                              ("splash_path", splash.get(), "splash")):
                    selected = selected.strip()
                    if not selected:
                        next_brand[field] = ""
                    elif resolve_branding_path(selected) is None:
                        raise FileNotFoundError(f"Không tìm thấy ảnh: {selected}")
                    elif str(selected).replace(chr(92), "/").startswith("assets/branding/"):
                        next_brand[field] = selected
                    else:
                        next_brand[field] = store_brand_asset(selected, kind)
                save_branding(next_brand)
                self.branding = load_branding()
                self.root.title(str(self.branding["app_name"]) + "  ·  Trực tiếp")
                self.brand_title.configure(text=str(self.branding["app_name"]))
                self.brand_subtitle.configure(text=str(self.branding["tagline"]))
                self._apply_brand_icon()
                win.destroy()
            except Exception as exc:
                messagebox.showerror("Không lưu được tùy chỉnh", str(exc), parent=win)

        ttk.Button(controls, text="Khôi phục", command=lambda: (
            name.set(str(BRAND_DEFAULTS["app_name"])),
            tagline.set(str(BRAND_DEFAULTS["tagline"])),
            icon.set(str(BRAND_DEFAULTS["icon_path"])),
            splash.set(str(BRAND_DEFAULTS["splash_path"])), enabled.set(True)
        ), style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Hủy", command=win.destroy,
                   style="Tool.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Lưu thay đổi", command=save,
                   style="Primary.TButton").pack(side="left")

    @staticmethod
    def _valid_lang(value: str, fallback: str) -> str:
        value = str(value or "").strip().lower()
        return value if value in {"vi", "ja", "en", "auto"} else fallback

    def _build_header(self, parent) -> None:
        ttk = self.ttk

        header = ttk.Frame(
            parent,
            style="Header.TFrame",
            padding=(16, 13),
        )
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        brand = ttk.Frame(header, style="Header.TFrame")
        brand.grid(row=0, column=0, sticky="w")
        self.brand_title = ttk.Label(
            brand, text=str(self.branding.get("app_name", "SoundText")), style="Brand.TLabel")
        self.brand_title.pack(anchor="w")
        self.brand_subtitle = ttk.Label(
            brand, text=str(self.branding.get("tagline", "")), style="Subtitle.TLabel")
        self.brand_subtitle.pack(anchor="w", pady=(3, 0))

        right = ttk.Frame(header, style="Header.TFrame")
        right.grid(row=0, column=1, sticky="e")

        self.live_timer = ttk.Label(
            right,
            textvariable=self.elapsed_text,
            style="Timer.TLabel",
        )
        self.live_timer.pack(side="left", padx=(0, 14))

        ttk.Label(right, text="NGUỒN THU", style="TopMeta.TLabel").pack(
            side="left", padx=(0, 8)
        )
        for value, label in (("you", "YOU"), ("remote", "REMOTE"), ("both", "BOTH")):
            ttk.Radiobutton(
                right,
                text=label,
                value=value,
                variable=self.audio_mode,
                command=self._mode_changed,
                style="Mode.TRadiobutton",
            ).pack(side="left", padx=(0, 5))

        self.mode_hint = ttk.Label(right, text="", style="Subtitle.TLabel")
        self.mode_hint.pack(side="left", padx=(8, 0))

    def _build_live_area(self, parent) -> None:
        ttk = self.ttk

        live = ttk.Frame(parent, style="App.TFrame")
        live.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        live.columnconfigure(0, weight=1, uniform="live")
        live.columnconfigure(1, weight=1, uniform="live")

        self._build_live_card(live, "mic", 0)
        self._build_live_card(live, "system", 1)

    def _build_live_card(self, parent, source: str, column: int) -> None:
        tk, ttk = self.tk, self.ttk
        is_mic = source == "mic"

        card = ttk.Frame(parent, style="Surface.TFrame", padding=(19, 16))
        card.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=((0, 7) if is_mic else (7, 0)),
        )
        card.columnconfigure(0, weight=1)

        top = ttk.Frame(card, style="Surface.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)

        title = "YOU  •  Microphone" if is_mic else "REMOTE  •  Âm thanh hệ thống"
        title_style = "YouTitle.TLabel" if is_mic else "RemoteTitle.TLabel"
        ttk.Label(top, text=title, style=title_style).grid(row=0, column=0, sticky="w")

        controls = ttk.Frame(top, style="Surface.TFrame")
        controls.grid(row=0, column=1, sticky="e")

        lang_var = self.you_language if is_mic else self.remote_language
        combo = ttk.Combobox(
            controls,
            textvariable=lang_var,
            values=("vi", "ja", "en", "auto"),
            width=9,
            state="readonly",
            style="Lang.TCombobox",
        )
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e, s=source: self._language_changed(s))
        ttk.Label(controls, text="→ VI", style="Target.TLabel").pack(side="left", padx=(7, 0))

        meta = ttk.Frame(card, style="Surface.TFrame")
        meta.grid(row=1, column=0, sticky="ew", pady=(5, 9))
        meta.columnconfigure(0, weight=1)

        state = ttk.Label(meta, text="", style="LiveMeta.TLabel")
        state.grid(row=0, column=0, sticky="w")
        activity = ttk.Label(meta, text="● ĐANG NGHE", style="Activity.TLabel")
        activity.grid(row=0, column=1, sticky="e")

        level_row = ttk.Frame(card, style="Surface.TFrame")
        level_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        level_row.columnconfigure(0, weight=1)

        meter = tk.Canvas(
            level_row,
            height=9,
            bg=self.SURFACE_2,
            highlightthickness=0,
            bd=0,
        )
        meter.grid(row=0, column=0, sticky="ew")
        meter_text = ttk.Label(
            level_row,
            text="TÍN HIỆU  -- dBFS",
            style="LiveMeta.TLabel",
        )
        meter_text.grid(row=0, column=1, sticky="e", padx=(9, 0))

        source_live = tk.StringVar(value="Đang chờ giọng nói…")
        translation_live = tk.StringVar(
            value=(
                "Bản dịch sẽ xuất hiện tại đây sau khi câu hoàn tất."
                if self.translation_available
                else "Dịch chưa sẵn sàng • STT vẫn hoạt động."
            )
        )

        source_panel = ttk.Frame(card, style="Surface2.TFrame")
        source_panel.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        source_panel.columnconfigure(0, weight=1)
        ttk.Label(
            source_panel,
            text="NGUYÊN VĂN / STT",
            style="SectionLabel.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        source_label = ttk.Label(
            source_panel,
            textvariable=source_live,
            style="Source.TLabel",
            anchor="w",
            justify="left",
            wraplength=520,
        )
        source_label.grid(row=1, column=0, sticky="ew")

        translation_panel = ttk.Frame(card, style="TranslationSurface.TFrame")
        translation_panel.grid(row=4, column=0, sticky="ew")
        translation_panel.columnconfigure(0, weight=1)
        ttk.Label(
            translation_panel,
            text="TIẾNG VIỆT",
            style="TranslationSection.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=13, pady=(9, 0))
        translation_label = ttk.Label(
            translation_panel,
            textvariable=translation_live,
            style="Translation.TLabel",
            anchor="w",
            justify="left",
            wraplength=520,
        )
        translation_label.grid(row=1, column=0, sticky="ew")

        # Keep long live sentences within the card during window resizing.
        source_panel.bind("<Configure>", lambda e, w=source_label: w.configure(
            wraplength=max(170, e.width - 35)))
        translation_panel.bind("<Configure>", lambda e, w=translation_label: w.configure(
            wraplength=max(170, e.width - 35)))

        if is_mic:
            self.you_state = state
            self.you_activity = activity
            self.you_live_source = source_live
            self.you_live_translation = translation_live
            self.you_meter = meter
            self.you_meter_text = meter_text
        else:
            self.remote_state = state
            self.remote_activity = activity
            self.remote_live_source = source_live
            self.remote_live_translation = translation_live
            self.remote_meter = meter
            self.remote_meter_text = meter_text

    @staticmethod
    def _meter_fraction(dbfs: float) -> float:
        # Useful speech range on consumer microphones is roughly -55..-10 dBFS.
        return max(0.0, min(1.0, (float(dbfs) + 60.0) / 54.0))

    def _update_audio_meter(
        self,
        source: str,
        dbfs: float,
        peak_dbfs: float,
        clean_dbfs: float | None,
    ) -> None:
        source = "mic" if source == "mic" else "system"
        canvas = self.you_meter if source == "mic" else self.remote_meter
        label = self.you_meter_text if source == "mic" else self.remote_meter_text

        previous = float(self._meter_smoothed.get(source, -72.0))
        # Fast attack / slower release gives a readable VU-like motion.
        rate = 0.58 if dbfs > previous else 0.18
        smooth = previous + rate * (float(dbfs) - previous)
        self._meter_smoothed[source] = smooth
        self._meter_last_seen[source] = time.monotonic()

        try:
            canvas.update_idletasks()
            width = max(40, int(canvas.winfo_width()))
            height = max(5, int(canvas.winfo_height()))
            frac = self._meter_fraction(smooth)
            peak_frac = self._meter_fraction(peak_dbfs)

            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill=self.SURFACE_2, outline="")
            fill = self.GREEN
            if peak_dbfs > -5.0:
                fill = "#ff6b6b"
            elif peak_dbfs > -12.0:
                fill = self.WARNING
            canvas.create_rectangle(0, 0, int(width * frac), height, fill=fill, outline="")
            canvas.create_line(
                int(width * peak_frac), 0,
                int(width * peak_frac), height,
                fill="#ffffff",
                width=1,
            )

            if source == "mic" and clean_dbfs is not None:
                label.configure(
                    text=f"MIC {dbfs:5.1f} dB  •  LỌC {clean_dbfs:5.1f} dB"
                )
            else:
                label.configure(text=f"TÍN HIỆU {dbfs:5.1f} dBFS")
        except Exception:
            pass

    def _build_timeline_toolbar(self, parent) -> None:
        ttk = self.ttk

        row = ttk.Frame(
            parent,
            style="Toolbar.TFrame",
            padding=(14, 10),
        )
        row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        row.columnconfigure(0, weight=1)

        left = ttk.Frame(row, style="Toolbar.TFrame")
        left.grid(row=0, column=0, sticky="w")
        ttk.Label(
            left,
            text="Hội thoại",
            style="LiveTitle.TLabel",
        ).pack(side="left")
        ttk.Label(
            left,
            textvariable=self.timeline_summary,
            style="LiveMeta.TLabel",
        ).pack(side="left", padx=(9, 0))

        tools = ttk.Frame(row, style="Toolbar.TFrame")
        tools.grid(row=0, column=1, sticky="e")

        ttk.Checkbutton(
            tools,
            text="Bản gốc",
            variable=self.show_original,
            command=self._render_timeline,
            style="Tool.TCheckbutton",
        ).pack(side="left", padx=(0, 9))

        ttk.Checkbutton(
            tools,
            text="Theo mới nhất",
            variable=self.auto_follow,
            style="Tool.TCheckbutton",
        ).pack(side="left", padx=(0, 9))

        ttk.Checkbutton(
            tools,
            text="Phụ đề",
            variable=self.subtitle_enabled,
            command=self._toggle_subtitle_overlay,
            style="Tool.TCheckbutton",
        ).pack(side="left", padx=(0, 9))

        ttk.Button(
            tools,
            text="⚙ Phụ đề",
            command=self._open_subtitle_settings,
            style="Tool.TButton",
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            tools, text="Mở thư mục lưu", command=self._open_session_folder,
            style="Secondary.TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            tools, text="Tùy chỉnh app", command=self._open_branding_settings,
            style="Secondary.TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            tools, text="Sao chép", command=self._copy_timeline,
            style="Primary.TButton",
        ).pack(side="left")

    def _build_timeline(self, parent) -> None:
        tk, ttk = self.tk, self.ttk

        frame = ttk.Frame(parent, style="Surface.TFrame", padding=(16, 12))
        frame.grid(row=3, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        text = tk.Text(
            frame,
            wrap="word",
            state="disabled",
            bg=self.SURFACE,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=10,
            selectbackground=self.BORDER,
            spacing1=0,
            spacing2=0,
            spacing3=0,
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)

        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns", padx=(10, 0))

        # Timeline hierarchy: speaker/time -> source -> prominent Vietnamese.
        text.tag_configure(
            "you_meta",
            foreground="#256B93",
            font=("Segoe UI", 9, "bold"),
            spacing1=12,
        )
        text.tag_configure(
            "remote_meta",
            foreground="#7453AA",
            font=("Segoe UI", 9, "bold"),
            spacing1=12,
        )
        text.tag_configure(
            "lang_chip",
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 8, "bold"),
        )
        text.tag_configure(
            "source",
            foreground=self.TEXT_SOFT,
            font=("Segoe UI", 10),
            lmargin1=18,
            lmargin2=18,
            spacing1=4,
            spacing3=5,
        )
        text.tag_configure(
            "vi_label",
            foreground="#28638D",
            font=("Segoe UI", 8, "bold"),
            lmargin1=18,
            lmargin2=18,
            spacing1=2,
        )
        text.tag_configure(
            "translation",
            foreground=self.TEXT,
            font=("Segoe UI", 13, "bold"),
            lmargin1=18,
            lmargin2=18,
            rmargin=12,
            spacing1=2,
            spacing3=10,
        )
        text.tag_configure(
            "pending",
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 10, "italic"),
            lmargin1=18,
            lmargin2=18,
            spacing1=3,
            spacing3=10,
        )
        text.tag_configure(
            "divider",
            foreground=self.BORDER,
            font=("Segoe UI", 7),
            spacing3=0,
        )
        text.tag_configure(
            "empty",
            foreground=self.TEXT_MUTED,
            font=("Segoe UI", 11),
            justify="center",
            spacing1=50,
        )

        self.timeline_widget = text
        self._render_timeline()

    def _build_footer(self, parent) -> None:
        ttk = self.ttk

        footer = ttk.Frame(parent, style="App.TFrame")
        footer.grid(row=4, column=0, sticky="ew", pady=(9, 0))
        footer.columnconfigure(0, weight=1)

        self.status = ttk.Label(footer, text="", style="Status.TLabel")
        self.status.grid(row=0, column=0, sticky="w")

        ttk.Label(
            footer,
            text=("Local CPU  •  STT + "
                  + ("NLLB ONNX" if "nllb" in self.translation_status.lower() else "MT")
                  + "  •  đích: Tiếng Việt"),
            style="Status.TLabel",
        ).grid(row=0, column=1, sticky="e")

    def _language_changed(self, source: str) -> None:
        if source == "mic":
            lang = self._valid_lang(self.you_language.get(), "vi")
            self.you_live_source.set("Đang chuyển model nhận dạng…")
            self.you_live_translation.set("Đang chờ câu mới…")
            if self.on_you_language_change is not None:
                self.on_you_language_change(lang)
        else:
            lang = self._valid_lang(self.remote_language.get(), "ja")
            self.remote_live_source.set("Đang chuyển model nhận dạng…")
            self.remote_live_translation.set("Đang chờ câu mới…")
            if self.on_remote_language_change is not None:
                self.on_remote_language_change(lang)

        self._refresh_language_labels()

    def _refresh_language_labels(self) -> None:
        def desc(lang: str) -> str:
            model = "Gipformer" if lang == "vi" else "SenseVoice"
            if lang == "auto":
                return "JA / EN tự động • SenseVoice LID"
            return f"{self.LANG_LABELS.get(lang, lang.upper())} • {model}"

        self.you_state.configure(text=desc(self.you_language.get()))
        self.remote_state.configure(text=desc(self.remote_language.get()))

    def _mode_changed(self) -> None:
        mode = self.audio_mode.get().strip().lower()
        if mode not in self.MODE_LABELS:
            mode = "both"
            self.audio_mode.set(mode)

        if self.on_audio_mode_change is not None:
            self.on_audio_mode_change(mode)

        self._refresh_mode_hint()
        self._refresh_footer()

    def _refresh_mode_hint(self) -> None:
        self.mode_hint.configure(
            text={
                "remote": "system",
                "you": "micro",
                "both": "micro + system",
            }.get(self.audio_mode.get(), "")
        )

    def _refresh_footer(self, extra: str = "") -> None:
        mode = self.MODE_LABELS.get(self.audio_mode.get(), "BOTH")
        if self.translation_available:
            mt = "Dịch VI sẵn sàng"
        else:
            mt = f"Dịch chưa sẵn sàng: {self.translation_status or 'hãy chuẩn bị model'}"

        self._status_extra = extra
        value = f"Thu âm: {mode}  •  {mt}"
        if extra:
            value += f"  •  {extra}"
        self.status.configure(text=value)

    def _activity(self, source: str, text: str) -> None:
        (self.you_activity if source == "mic" else self.remote_activity).configure(text=text)

    def _source_var(self, source: str):
        return self.you_live_source if source == "mic" else self.remote_live_source

    def _translation_var(self, source: str):
        return self.you_live_translation if source == "mic" else self.remote_live_translation

    def _find_timeline(self, source: str, utterance_id: int):
        for item in reversed(self._timeline):
            if (
                str(item.get("source", "")) == source
                and int(item.get("utterance_id", -1)) == int(utterance_id)
            ):
                return item
        return None

    @staticmethod
    def _clock_now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _speaker(self, source: str, speaker_id: str | None = None, overlap: bool = False) -> str:
        label = self.SPEAKER_LABELS.get(source, 'REMOTE')
        if speaker_id:
            label += f' · {speaker_id}'
        if overlap:
            label += ' · NÓI CHỒNG'
        return label

    def _render_timeline(self) -> None:
        widget = self.timeline_widget
        try:
            old_y = widget.yview()[0]
        except Exception:
            old_y = 1.0

        widget.configure(state="normal")
        widget.delete("1.0", "end")

        if not self._timeline:
            widget.insert(
                "end",
                "\nCuộc hội thoại sẽ xuất hiện ở đây theo thứ tự thời gian.\n"
                "Bản dịch tiếng Việt được ưu tiên để có thể đọc nhanh trong cuộc họp.",
                "empty",
            )
            widget.configure(state="disabled")
            self.timeline_summary.set("Chưa có hội thoại")
            return

        items = self._timeline[-self.history_items:]
        show_src = bool(self.show_original.get())

        for index, item in enumerate(items):
            source = str(item.get("source", "system"))
            speaker = self._speaker(source, item.get("speaker_id"), bool(item.get("speaker_overlap")))
            clock = str(item.get("clock", "--:--:--"))
            lang = str(item.get("source_language", "")).upper() or "STT"
            src = str(item.get("source_text", "")).strip()
            tr = str(item.get("translated_text", "")).strip()
            pending = bool(item.get("translation_pending", False))
            stt_ms = float(item.get("stt_latency_ms", 0.0) or 0.0)
            mt_ms = float(item.get("translation_latency_ms", 0.0) or 0.0)

            meta_tag = "you_meta" if source == "mic" else "remote_meta"
            meta = f"{speaker}   {clock}"
            if stt_ms > 0:
                meta += f"   •   STT {stt_ms:.0f} ms"
            widget.insert("end", meta, meta_tag)
            widget.insert("end", f"   {lang}\n", "lang_chip")

            if lang == "VI":
                # Vietnamese source is already the primary readable text.
                widget.insert("end", "VI\n", "vi_label")
                widget.insert("end", f"{src}\n", "translation")
            else:
                if show_src and src:
                    widget.insert("end", f"{src}\n", "source")

                if tr:
                    label = "VI"
                    if mt_ms > 0:
                        label += f"   •   MT {mt_ms:.0f} ms"
                    widget.insert("end", f"{label}\n", "vi_label")
                    widget.insert("end", f"{tr}\n", "translation")
                elif pending:
                    widget.insert("end", "VI   •   đang dịch…\n", "pending")
                else:
                    widget.insert("end", "Chưa có bản dịch.\n", "pending")

            if index != len(items) - 1:
                widget.insert("end", "────────────────────────────────────────\n", "divider")

        self.timeline_summary.set(f"{len(self._timeline)} lượt nói")

        if self.auto_follow.get():
            widget.see("end")
        else:
            widget.yview_moveto(old_y)

        widget.configure(state="disabled")

    def _handle_asr(self, ev: UIEvent) -> bool:
        source = ev.source if ev.source in {"mic", "system"} else "system"
        lang = self._valid_lang(ev.source_language, "vi")
        label = self.LANG_LABELS.get(lang, lang.upper())

        if not ev.final:
            self._activity(source, "● ĐANG NHẬN DẠNG")
            self._source_var(source).set(f"{label}  ·  {ev.source_text}")
            if lang == "vi":
                self._translation_var(source).set(ev.source_text or "Đang nghe…")
            else:
                self._translation_var(source).set("Chờ câu hoàn chỉnh để dịch…")

            self._subtitle_last_source = source
            self._subtitle_last_utterance_id = ev.utterance_id
            self._update_subtitle_overlay(
                source=source,
                utterance_id=ev.utterance_id,
                source_text=ev.source_text,
                translated_text=(ev.source_text if lang == "vi" else ""),
                final=False,
                source_language=lang,
            )
            return False

        self._last_final_id[source] = ev.utterance_id
        pending = lang != "vi" and self.translation_available

        item = self._find_timeline(source, ev.utterance_id)
        if item is None:
            item = {
                "source": source,
                "utterance_id": ev.utterance_id,
                "clock": self._clock_now(),
                "source_language": lang,
                "source_text": ev.source_text,
                "translated_text": "",
                "translation_pending": pending,
                "speaker_id": ev.speaker_id,
                "speaker_overlap": ev.speaker_overlap,
                "stt_latency_ms": ev.stt_latency_ms,
                "translation_latency_ms": 0.0,
            }
            self._timeline.append(item)
        else:
            item.update(
                source_language=lang,
                source_text=ev.source_text,
                translation_pending=pending,
                speaker_id=ev.speaker_id,
                speaker_overlap=ev.speaker_overlap,
                stt_latency_ms=ev.stt_latency_ms,
            )

        metric = f"  ·  STT {ev.stt_latency_ms:.0f} ms" if ev.stt_latency_ms > 0 else ""
        self._source_var(source).set(f"{label}  ·  {ev.source_text}{metric}")

        if lang == "vi":
            self._translation_var(source).set(ev.source_text)
            self._activity(source, "● ĐANG NGHE")
        elif self.translation_available:
            self._translation_var(source).set("Đang dịch sang tiếng Việt…")
            self._activity(source, "● ĐANG DỊCH")
        else:
            self._translation_var(source).set("Dịch chưa sẵn sàng • STT vẫn hoạt động.")
            self._activity(source, "● ĐANG NGHE")

        self._subtitle_last_source = source
        self._subtitle_last_utterance_id = ev.utterance_id
        self._update_subtitle_overlay(
            source=source,
            utterance_id=ev.utterance_id,
            source_text=ev.source_text,
            translated_text=(ev.source_text if lang == "vi" else ""),
            final=True,
            source_language=lang,
            speaker_id=ev.speaker_id,
            speaker_overlap=ev.speaker_overlap,
        )

        return True

    def _handle_translation(self, ev: UIEvent) -> bool:
        source = ev.source if ev.source in {"mic", "system"} else "system"
        item = self._find_timeline(source, ev.utterance_id)

        if item is None:
            item = {
                "source": source,
                "utterance_id": ev.utterance_id,
                "clock": self._clock_now(),
                "source_language": self._valid_lang(ev.source_language, "ja"),
                "source_text": ev.source_text,
                "translated_text": ev.translated_text,
                "translation_pending": False,
                "stt_latency_ms": ev.stt_latency_ms,
                "translation_latency_ms": ev.translation_latency_ms,
            }
            self._timeline.append(item)
        else:
            item["translated_text"] = ev.translated_text
            item["translation_pending"] = False
            item["translation_latency_ms"] = ev.translation_latency_ms

        if self._last_final_id[source] == ev.utterance_id:
            metric = (
                f"  ·  MT {ev.translation_latency_ms:.0f} ms"
                if ev.translation_latency_ms > 0
                else ""
            )
            self._translation_var(source).set(f"{ev.translated_text}{metric}")
            self._activity(source, "● ĐANG NGHE")
            if ev.translation_wait_ms >= 800:
                self._refresh_footer(f"Hàng đợi dịch {ev.translation_wait_ms/1000:.1f}s")

        # Translation events must update their exact subtitle row regardless of
        # which utterance is currently live. The previous _subtitle_pair_key
        # guard belonged to the old single-pair overlay and prevented completed
        # translations from replacing "Đang dịch câu này…".
        if self.subtitle_enabled.get():
            self._update_subtitle_overlay(
                source=source,
                utterance_id=ev.utterance_id,
                source_text=ev.source_text,
                translated_text=ev.translated_text,
                final=True,
                source_language=ev.source_language,
            )

        return True

    def _toggle_subtitle_overlay(self) -> None:
        # Never build two Toplevels and replay subtitle history inside the
        # checkbox event itself.  Return control to Tk first.
        if self._subtitle_open_after_id is not None:
            try:
                self.root.after_cancel(self._subtitle_open_after_id)
            except Exception:
                pass
            self._subtitle_open_after_id = None
        if self.subtitle_enabled.get():
            self._subtitle_open_after_id = self.root.after(1, self._finish_open_subtitle_overlay)
        else:
            self._destroy_subtitle_overlay()

    def _finish_open_subtitle_overlay(self) -> None:
        self._subtitle_open_after_id = None
        if not self.stop_requested and self.subtitle_enabled.get():
            self._ensure_subtitle_overlay()
            self._subtitle_refresh_visibility()

    def _open_subtitle_settings(self) -> None:
        tk, ttk = self.tk, self.ttk

        win = tk.Toplevel(self.root)
        win.title("Phụ đề")
        win.configure(bg=self.SURFACE)
        win.resizable(False, False)
        win.transient(self.root)

        body = ttk.Frame(win, style="Surface.TFrame", padding=18)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="Phụ đề màn hình",
            style="LiveTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="Chọn đúng lượng thông tin bạn muốn thấy khi đang họp.",
            style="LiveMeta.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        ttk.Label(body, text="Hiển thị", style="LiveTitle.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 6)
        )
        mode_row = ttk.Frame(body, style="Surface.TFrame")
        mode_row.grid(row=3, column=0, sticky="w", pady=(0, 14))
        ttk.Radiobutton(
            mode_row,
            text="Chỉ bản dịch",
            value="translation",
            variable=self.subtitle_mode,
            command=self._subtitle_mode_changed,
            style="Setting.TRadiobutton",
        ).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(
            mode_row,
            text="STT + bản dịch",
            value="dual",
            variable=self.subtitle_mode,
            command=self._subtitle_mode_changed,
            style="Setting.TRadiobutton",
        ).pack(side="left")

        ttk.Label(body, text="Cỡ chữ STT", style="LiveTitle.TLabel").grid(
            row=4, column=0, sticky="w", pady=(0, 4)
        )
        tk.Scale(
            body,
            from_=5,
            to=50,
            orient="horizontal",
            variable=self.subtitle_stt_font_size,
            command=lambda _v: self._apply_subtitle_font_sizes(),
            bg=self.SURFACE,
            fg=self.TEXT,
            troughcolor=self.BORDER,
            activebackground=self.ACCENT,
            highlightthickness=0,
            length=320,
            resolution=1,
        ).grid(row=5, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(body, text="Cỡ chữ bản dịch", style="LiveTitle.TLabel").grid(
            row=6, column=0, sticky="w", pady=(0, 4)
        )
        tk.Scale(
            body,
            from_=5,
            to=50,
            orient="horizontal",
            variable=self.subtitle_vi_font_size,
            command=lambda _v: self._apply_subtitle_font_sizes(),
            bg=self.SURFACE,
            fg=self.TEXT,
            troughcolor=self.BORDER,
            activebackground=self.ACCENT,
            highlightthickness=0,
            length=320,
            resolution=1,
        ).grid(row=7, column=0, sticky="ew", pady=(0, 12))

        ttk.Checkbutton(
            body,
            text="Tự cuộn đến câu mới nhất",
            variable=self.subtitle_auto_follow,
            command=self._subtitle_auto_follow_changed,
            style="Tool.TCheckbutton",
        ).grid(row=8, column=0, sticky="w", pady=(0, 6))

        ttk.Checkbutton(
            body,
            text="Đồng bộ cuộn STT ↔ bản dịch",
            variable=self.subtitle_sync_scroll,
            style="Tool.TCheckbutton",
        ).grid(row=9, column=0, sticky="w", pady=(0, 6))

        ttk.Checkbutton(
            body,
            text="Hiện nguyên văn trong cửa sổ STT",
            variable=self.subtitle_show_original,
            command=self._subtitle_rebuild_texts,
            style="Tool.TCheckbutton",
        ).grid(row=10, column=0, sticky="w", pady=(0, 14))

        ttk.Label(body, text="Đặt nhanh", style="LiveTitle.TLabel").grid(
            row=11, column=0, sticky="w", pady=(0, 7)
        )
        positions = ttk.Frame(body, style="Surface.TFrame")
        positions.grid(row=12, column=0, sticky="ew")

        for label, layout in (
            ("Dưới giữa", "bottom"),
            ("Bên phải", "right"),
            ("Song song", "side"),
        ):
            ttk.Button(
                positions,
                text=label,
                command=lambda value=layout: self._position_subtitle_overlay(value),
                style="Tool.TButton",
            ).pack(side="left", padx=(0, 7))

        ttk.Label(
            body,
            text=(
                "Khi bạn tự cuộn, chế độ theo câu mới nhất sẽ tự tắt. "
                "Sync chỉ hoạt động khi mở STT + bản dịch."
            ),
            style="LiveMeta.TLabel",
            wraplength=390,
            justify="left",
        ).grid(row=13, column=0, sticky="w", pady=(13, 0))

        ttk.Checkbutton(
            body,
            text="Tự mở rộng phụ đề nổi khi bản dịch dài (nhiều dòng)",
            variable=self.subtitle_auto_expand,
            style="Tool.TCheckbutton",
        ).grid(row=14, column=0, sticky="w", pady=(9, 0))

        self.root.update_idletasks()
        x = self.root.winfo_x() + max(20, (self.root.winfo_width() - 430) // 2)
        y = self.root.winfo_y() + max(20, (self.root.winfo_height() - 500) // 2)
        win.geometry(f"+{x}+{y}")

    def _subtitle_mode_changed(self) -> None:
        mode = str(self.subtitle_mode.get() or "translation").strip().lower()
        if mode not in {"translation", "dual"}:
            mode = "translation"
            self.subtitle_mode.set(mode)

        if self.subtitle_enabled.get():
            self._destroy_subtitle_overlay()
            self._ensure_subtitle_overlay()
            self._subtitle_refresh_visibility()

    @staticmethod
    def _text_near_bottom(widget) -> bool:
        try:
            _first, last = widget.yview()
            return float(last) >= 0.965
        except Exception:
            return True

    @staticmethod
    def _subtitle_row_tag(source: str, utterance_id: int) -> str:
        return f"row_{source}_{int(utterance_id)}"

    @staticmethod
    def _subtitle_vi_body_tag(source: str, utterance_id: int) -> str:
        return f"vi_body_{source}_{int(utterance_id)}"

    def _configure_subtitle_text_tags(self) -> None:
        if self._subtitle_stt_text is not None:
            w = self._subtitle_stt_text
            w.tag_configure("meta", foreground="#8fa3b8", font=("Segoe UI", 9, "bold"))
            w.tag_configure(
                "body",
                foreground="#e0e6ed",
                font=("Segoe UI", int(self.subtitle_stt_font_size.get())),
                spacing1=3,
                spacing3=10,
            )
            w.tag_configure("divider", foreground="#293440", font=("Segoe UI", 6))

        if self._subtitle_vi_text is not None:
            w = self._subtitle_vi_text
            w.tag_configure("meta", foreground="#79baff", font=("Segoe UI", 9, "bold"))
            w.tag_configure(
                "body",
                foreground="#ffffff",
                font=("Segoe UI", int(self.subtitle_vi_font_size.get()), "bold"),
                spacing1=3,
                spacing3=10,
            )
            w.tag_configure(
                "pending",
                foreground="#8492a0",
                font=(
                    "Segoe UI",
                    max(5, int(self.subtitle_vi_font_size.get()) - 4),
                    "italic",
                ),
                spacing1=3,
                spacing3=10,
            )
            w.tag_configure("divider", foreground="#293440", font=("Segoe UI", 6))

    @staticmethod
    def _set_subtitle_preview(widget, value: str) -> None:
        """Live partials wrap naturally and scroll, without hiding old lines.

        If the viewer has just manually scrolled upward, preserve their reading
        position until they stop interacting for a few seconds.
        """
        if widget is None:
            return
        try:
            # Keep translation-only mode compact when no Vietnamese partial is
            # available; do not reserve an empty multi-line box for JA/EN.
            container = getattr(widget, "_preview_container", None)
            if container is not None and getattr(widget, "_allow_hide", False):
                if value and not container.winfo_manager():
                    container.pack(fill="x", pady=(0, 6), before=widget._after_preview_panel)
                elif not value and container.winfo_manager():
                    container.pack_forget()
            first, last = widget.yview()
            manual = time.monotonic() - getattr(widget, "_user_scroll_at", 0.0) < 4.0
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", value)
            widget.configure(state="disabled")
            # Grow the *floating live strip* as a person keeps speaking.
            # A fixed three-line strip scrolls away the beginning of a long
            # unfinished statement, which feels like a one-line subtitle.
            if value:
                try:
                    widget.update_idletasks()
                    count = widget.count('1.0', 'end-1c', 'displaylines')
                    displayed = max(1, int(count[0])) if count else 1
                    wanted_lines = min(9, max(3, displayed + 1))
                    if int(widget.cget('height')) != wanted_lines:
                        widget.configure(height=wanted_lines)
                        callback = getattr(widget, '_on_preview_resize', None)
                        if callback is not None:
                            callback()
                except Exception:
                    pass
            if manual and last < 0.98:
                widget.yview_moveto(first)
            else:
                widget.see("end")
        except Exception:
            pass

    def _make_overlay_window(
        self,
        *,
        kind: str,
        title: str,
        bg: str,
        border: str,
        width: int,
        height: int,
        x: int,
        y: int,
    ):
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.97)
        except Exception:
            pass
        win.configure(bg="#070a0e")
        win.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")

        outer = tk.Frame(win, bg="#070a0e", padx=8, pady=8)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg="#070a0e", height=28)
        header.pack(fill="x", pady=(0, 5))

        title_label = tk.Label(
            header,
            text=title,
            bg="#070a0e",
            fg=("#9fb2c8" if kind == "stt" else "#79baff"),
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        title_label.pack(side="left")

        hint = tk.Label(
            header,
            text="kéo • resize cạnh/góc • Esc ẩn",
            bg="#070a0e",
            fg="#5d6b79",
            font=("Segoe UI", 8),
            anchor="e",
        )
        hint.pack(side="right")

        # Text widgets wrap long partials immediately and remain bounded in
        # height. A one-line Label used to cut off words until the ASR Final.
        # Wheel scrolling allows looking back while the person is still talking.
        preview = tk.Frame(outer, bg="#0b1118" if kind == "stt" else "#101a27")
        preview.pack(fill="x", pady=(0, 6))
        preview_text = tk.Text(
            preview,
            height=3 if kind == "stt" else 4,
            width=1,
            wrap="word",
            state="disabled",
            bg="#0b1118" if kind == "stt" else "#101a27",
            fg="#8090a0" if kind == "stt" else "#c8d8ea",
            font=("Segoe UI", 11 if kind == "stt" else max(5, int(self.subtitle_vi_font_size.get()))),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=5,
            cursor="arrow",
        )
        preview_scroll = self.ttk.Scrollbar(preview, orient="vertical", command=preview_text.yview)
        preview_text.configure(yscrollcommand=preview_scroll.set)
        preview_text.pack(side="left", fill="x", expand=True)
        preview_scroll.pack(side="right", fill="y")
        preview_text._user_scroll_at = 0.0
        preview_text._preview_container = preview
        preview_text._allow_hide = kind == "vi"
        preview_text._on_preview_resize = lambda which=kind: self._expand_subtitle_for_live_preview(which)
        preview_text.bind("<MouseWheel>", lambda _event, w=preview_text: setattr(w, "_user_scroll_at", time.monotonic()), add="+")
        preview_text.bind("<Button-4>", lambda _event, w=preview_text: setattr(w, "_user_scroll_at", time.monotonic()), add="+")
        preview_text.bind("<Button-5>", lambda _event, w=preview_text: setattr(w, "_user_scroll_at", time.monotonic()), add="+")
        if kind == "stt":
            self._subtitle_live_preview = preview_text
            self._set_subtitle_preview(preview_text, "Đang nghe…")
        else:
            self._subtitle_vi_live_preview = preview_text

        panel = tk.Frame(
            outer,
            bg=bg,
            highlightbackground=border,
            highlightthickness=1,
        )
        panel.pack(fill="both", expand=True)
        preview_text._after_preview_panel = panel
        if kind == "vi":
            # No empty four-line strip while translating JA/EN. Show it only
            # for progressive native Vietnamese speech.
            preview.pack_forget()
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        text = tk.Text(
            panel,
            wrap="word",
            state="disabled",
            bg=bg,
            fg=("#e0e6ed" if kind == "stt" else "#ffffff"),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=12,
            pady=8,
            selectbackground="#314050",
        )
        scroll = self.ttk.Scrollbar(panel, orient="vertical")
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        # Keep yscrollcommand passive. Insert/reflow callbacks used to trigger
        # synchronization continuously and made both windows twitch.
        text.configure(yscrollcommand=scroll.set)
        scroll.configure(
            command=lambda *args, w=kind, widget=text: self._subtitle_scroll_command(
                w, widget, *args
            )
        )

        def user_scrolled(_event=None, which=kind):
            self._subtitle_user_scrolled(which)

        text.bind("<MouseWheel>", user_scrolled, add="+")
        text.bind("<Button-4>", user_scrolled, add="+")
        text.bind("<Button-5>", user_scrolled, add="+")
        text.bind("<KeyRelease>", user_scrolled, add="+")
        text.bind("<ButtonRelease-1>", user_scrolled, add="+")

        # Drag from header.
        drag = {"dx": 0, "dy": 0}

        def start_drag(event):
            drag["dx"] = event.x_root - win.winfo_x()
            drag["dy"] = event.y_root - win.winfo_y()

        def move_drag(event):
            win.geometry(
                f"+{event.x_root - drag['dx']}+{event.y_root - drag['dy']}"
            )

        for widget in (header, title_label, hint):
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", move_drag)

        # Borderless resizing from 4 edges + 4 corners.
        self._install_resize_handles(win, kind)

        def hide_all(_event=None):
            self.subtitle_enabled.set(False)
            self._destroy_subtitle_overlay()

        win.bind("<Escape>", hide_all)
        return win, text

    def _install_resize_handles(self, win, kind: str) -> None:
        tk = self.tk
        thickness = 6
        corner = 12

        specs = (
            ("n", 0, 0, 1, 0, "sb_v_double_arrow"),
            ("s", 0, 1, 1, 0, "sb_v_double_arrow"),
            ("w", 0, 0, 0, 1, "sb_h_double_arrow"),
            ("e", 1, 0, 0, 1, "sb_h_double_arrow"),
            ("nw", 0, 0, 0, 0, "size_nw_se"),
            ("ne", 1, 0, 0, 0, "size_ne_sw"),
            ("sw", 0, 1, 0, 0, "size_ne_sw"),
            ("se", 1, 1, 0, 0, "size_nw_se"),
        )

        handles = []
        for edge, rx, ry, stretch_x, stretch_y, cursor in specs:
            try:
                h = tk.Frame(win, bg="#26313d", cursor=cursor)
            except tk.TclError:
                # Tk/X11 does not recognize every Windows resize cursor name.
                h = tk.Frame(win, bg="#26313d", cursor="fleur")

            if edge == "n":
                h.place(x=corner, y=0, relwidth=1.0, width=-2 * corner, height=thickness)
            elif edge == "s":
                h.place(
                    x=corner,
                    rely=1.0,
                    y=-thickness,
                    relwidth=1.0,
                    width=-2 * corner,
                    height=thickness,
                )
            elif edge == "w":
                h.place(x=0, y=corner, width=thickness, relheight=1.0, height=-2 * corner)
            elif edge == "e":
                h.place(
                    relx=1.0,
                    x=-thickness,
                    y=corner,
                    width=thickness,
                    relheight=1.0,
                    height=-2 * corner,
                )
            elif edge == "nw":
                h.place(x=0, y=0, width=corner, height=corner)
            elif edge == "ne":
                h.place(relx=1.0, x=-corner, y=0, width=corner, height=corner)
            elif edge == "sw":
                h.place(rely=1.0, x=0, y=-corner, width=corner, height=corner)
            else:
                h.place(
                    relx=1.0,
                    rely=1.0,
                    x=-corner,
                    y=-corner,
                    width=corner,
                    height=corner,
                )

            state = {}

            def start(event, edge=edge, state=state):
                state["x"] = event.x_root
                state["y"] = event.y_root
                state["wx"] = win.winfo_x()
                state["wy"] = win.winfo_y()
                state["w"] = win.winfo_width()
                state["h"] = win.winfo_height()

            def move(event, edge=edge, state=state):
                if not state:
                    return
                dx = event.x_root - state["x"]
                dy = event.y_root - state["y"]

                nx, ny = state["wx"], state["wy"]
                nw, nh = state["w"], state["h"]

                if "e" in edge:
                    nw = state["w"] + dx
                if "s" in edge:
                    nh = state["h"] + dy
                if "w" in edge:
                    nw = state["w"] - dx
                    nx = state["wx"] + dx
                if "n" in edge:
                    nh = state["h"] - dy
                    ny = state["wy"] + dy

                # A one-line overlay is not useful for live subtitles.
                min_w, min_h = 300, 220
                if nw < min_w:
                    if "w" in edge:
                        nx -= min_w - nw
                    nw = min_w
                if nh < min_h:
                    if "n" in edge:
                        ny -= min_h - nh
                    nh = min_h

                sw = win.winfo_screenwidth()
                sh = win.winfo_screenheight()
                nx = max(0, min(nx, sw - min_w))
                ny = max(0, min(ny, sh - min_h))
                nw = max(min_w, min(nw, sw - nx))
                nh = max(min_h, min(nh, sh - ny))

                win.geometry(f"{int(nw)}x{int(nh)}+{int(nx)}+{int(ny)}")
                if kind == "stt":
                    self._subtitle_stt_width = int(nw)
                    self._subtitle_stt_height = int(nh)
                else:
                    self._subtitle_vi_width = int(nw)
                    self._subtitle_vi_height = int(nh)

            h.bind("<ButtonPress-1>", start)
            h.bind("<B1-Motion>", move)
            handles.append(h)

        # Keep handles alive.
        win._soundtext_resize_handles = handles

    def _ensure_subtitle_overlay(self) -> None:
        mode = str(self.subtitle_mode.get() or "translation").strip().lower()
        if mode not in {"translation", "dual"}:
            mode = "translation"
            self.subtitle_mode.set(mode)

        def alive(win) -> bool:
            try:
                return win is not None and bool(win.winfo_exists())
            except Exception:
                return False

        vi_ok = alive(self.subtitle_vi_window)
        stt_ok = alive(self.subtitle_stt_window)

        if mode == "translation" and vi_ok and not stt_ok:
            return
        if mode == "dual" and vi_ok and stt_ok:
            return

        self._destroy_subtitle_overlay()

        sw = max(900, int(self.root.winfo_screenwidth()))
        sh = max(600, int(self.root.winfo_screenheight()))
        gap = 14

        if mode == "translation":
            vi_w = min(max(420, self._subtitle_vi_width), max(420, sw - 48))
            vi_h = min(max(240, self._subtitle_vi_height), max(240, sh - 100))
            x = max(18, (sw - vi_w) // 2)
            y = max(18, sh - vi_h - 58)

            vi_win, vi_text = self._make_overlay_window(
                kind="vi",
                title="TIẾNG VIỆT",
                bg="#0d151f",
                border="#33516d",
                width=vi_w,
                height=vi_h,
                x=x,
                y=y,
            )

            self.subtitle_window = vi_win
            self.subtitle_vi_window = vi_win
            self.subtitle_stt_window = None
            self._subtitle_stt_text = None
            self._subtitle_vi_text = vi_text
            self.subtitle_source = None
            self.subtitle_translation = vi_text
        else:
            stt_w = min(max(420, self._subtitle_stt_width), max(420, (sw - gap * 3) // 2))
            vi_w = min(max(420, self._subtitle_vi_width), max(420, (sw - gap * 3) // 2))
            stt_h = min(max(240, self._subtitle_stt_height), max(240, sh - 100))
            vi_h = min(max(240, self._subtitle_vi_height), max(240, sh - 100))

            total_w = stt_w + gap + vi_w
            start_x = max(gap, (sw - total_w) // 2)
            y = max(gap, sh - max(stt_h, vi_h) - 58)

            stt_win, stt_text = self._make_overlay_window(
                kind="stt",
                title="SPEECH → TEXT",
                bg="#111821",
                border="#2a3745",
                width=stt_w,
                height=stt_h,
                x=start_x,
                y=y,
            )
            vi_win, vi_text = self._make_overlay_window(
                kind="vi",
                title="TIẾNG VIỆT",
                bg="#0d151f",
                border="#33516d",
                width=vi_w,
                height=vi_h,
                x=start_x + stt_w + gap,
                y=y,
            )

            self.subtitle_window = stt_win
            self.subtitle_stt_window = stt_win
            self.subtitle_vi_window = vi_win
            self._subtitle_stt_text = stt_text
            self._subtitle_vi_text = vi_text
            self.subtitle_source = stt_text
            self.subtitle_translation = vi_text

        self._configure_subtitle_text_tags()
        self._subtitle_rebuild_texts()

    def _subtitle_sync_allowed(self) -> bool:
        return (
            str(self.subtitle_mode.get()) == "dual"
            and bool(self.subtitle_sync_scroll.get())
            and not bool(self.subtitle_auto_follow.get())
            and self._subtitle_stt_text is not None
            and self._subtitle_vi_text is not None
        )

    def _subtitle_user_scrolled(self, which: str) -> None:
        # Decide after Tk has applied the wheel/scrollbar movement. If the user
        # reaches the bottom again, resume following newest content automatically.
        self.root.after_idle(
            lambda w=which: self._subtitle_finish_user_scroll(w)
        )

    def _subtitle_finish_user_scroll(self, which: str) -> None:
        widget = (
            self._subtitle_stt_text if which == "stt" else self._subtitle_vi_text
        )
        if widget is None:
            return

        at_bottom = self._text_near_bottom(widget)
        if at_bottom:
            if not self.subtitle_auto_follow.get():
                self.subtitle_auto_follow.set(True)

            # In dual mode, reaching the newest item in either pane means both
            # panes should return to the newest item together.
            for peer in (self._subtitle_stt_text, self._subtitle_vi_text):
                if peer is not None:
                    try:
                        peer.see("end")
                    except Exception:
                        pass
            return

        # Reading older content disables follow. Only then may paired-scroll sync
        # run, so the two mechanisms never fight each other.
        if self.subtitle_auto_follow.get():
            self.subtitle_auto_follow.set(False)
        self._schedule_subtitle_scroll_sync(which)

    def _schedule_subtitle_scroll_sync(self, which: str) -> None:
        if self._subtitle_scroll_syncing or not self._subtitle_sync_allowed():
            return
        try:
            if self._subtitle_sync_after_id is not None:
                self.root.after_cancel(self._subtitle_sync_after_id)
        except Exception:
            pass
        self._subtitle_sync_after_id = self.root.after(
            55,
            lambda w=which: self._sync_subtitle_scroll(w),
        )

    def _subtitle_scroll_command(self, which: str, widget, *args) -> None:
        try:
            widget.yview(*args)
        finally:
            self._subtitle_user_scrolled(which)

    def _subtitle_auto_follow_changed(self) -> None:
        if not self.subtitle_auto_follow.get():
            return
        # Auto-follow owns positioning while enabled. Do not also run cross-sync.
        for widget in (self._subtitle_stt_text, self._subtitle_vi_text):
            if widget is not None:
                try:
                    widget.see("end")
                except Exception:
                    pass

    def _row_tag_at_top(self, widget) -> str | None:
        try:
            top_index = widget.index("@2,2")
            # If top is in blank inter-line space, search a few display lines down.
            for delta in range(0, 8):
                idx = widget.index(f"{top_index} +{delta} displaylines")
                tags = widget.tag_names(idx)
                for tag in tags:
                    if str(tag).startswith("row_"):
                        return str(tag)
        except Exception:
            pass
        return None

    def _sync_subtitle_scroll(self, source_kind: str) -> None:
        if self._subtitle_scroll_syncing or not self._subtitle_sync_allowed():
            return

        source_widget = (
            self._subtitle_stt_text if source_kind == "stt" else self._subtitle_vi_text
        )
        target_widget = (
            self._subtitle_vi_text if source_kind == "stt" else self._subtitle_stt_text
        )
        if source_widget is None or target_widget is None:
            return

        tag = self._row_tag_at_top(source_widget)
        if not tag:
            return

        try:
            ranges = target_widget.tag_ranges(tag)
            if not ranges:
                return
            target_index = str(ranges[0])

            self._subtitle_scroll_syncing = True
            target_widget.yview(target_index)
        except Exception:
            pass
        finally:
            self._subtitle_scroll_syncing = False

    def _subtitle_insert_row_widgets(self, row: dict[str, object]) -> None:
        stt = self._subtitle_stt_text
        vi = self._subtitle_vi_text
        if vi is None:
            return

        source = str(row["source"])
        uid = int(row["utterance_id"])
        seq = int(row["sequence"])
        clock = str(row["clock"])
        lang = str(row["source_language"]).upper()
        speaker = self._speaker(source, row.get("speaker_id"), bool(row.get("speaker_overlap")))
        header = f"#{seq:03d}  {speaker}  •  {lang}  •  {clock}\n"
        divider = "────────────────────────────────────\n"
        row_tag = self._subtitle_row_tag(source, uid)
        vi_body_tag = self._subtitle_vi_body_tag(source, uid)
        auto_follow = bool(self.subtitle_auto_follow.get())

        if stt is not None:
            stt.configure(state="normal")
            stt.insert("end", header, ("meta", row_tag))
            if self.subtitle_show_original.get():
                stt.insert(
                    "end",
                    str(row.get("source_text", "")).strip() + "\n",
                    ("body", row_tag),
                )
            else:
                stt.insert("end", "(nguyên văn đang ẩn)\n", ("body", row_tag))
            stt.insert("end", divider, ("divider", row_tag))
            stt.configure(state="disabled")

        vi.configure(state="normal")
        vi.insert("end", header, ("meta", row_tag))

        translated = str(row.get("translated_text", "")).strip()
        if translated:
            vi.insert("end", translated + "\n", ("body", row_tag, vi_body_tag))
        else:
            vi.insert(
                "end",
                "Đang dịch câu này…\n",
                ("pending", row_tag, vi_body_tag),
            )

        vi.insert("end", divider, ("divider", row_tag))
        vi.configure(state="disabled")

        if auto_follow and not self._subtitle_rebuilding:
            if stt is not None:
                stt.see("end")
            vi.see("end")
        if not self._subtitle_rebuilding:
            self._schedule_subtitle_expand()

    def _subtitle_replace_translation(
        self,
        source: str,
        utterance_id: int,
        translated_text: str,
    ) -> None:
        """Update only one translated row; avoid repainting all subtitle history."""
        key = (str(source), int(utterance_id))
        row = self._subtitle_row_by_key.get(key)
        vi = self._subtitle_vi_text
        stt = self._subtitle_stt_text
        if row is None or vi is None:
            return

        translated = str(translated_text or "").strip()
        if not translated:
            return
        row["translated_text"] = translated

        row_tag = self._subtitle_row_tag(source, utterance_id)
        body_tag = self._subtitle_vi_body_tag(source, utterance_id)

        stt_top_tag = None
        vi_top_tag = None
        if not self.subtitle_auto_follow.get():
            if stt is not None:
                stt_top_tag = self._row_tag_at_top(stt)
            vi_top_tag = self._row_tag_at_top(vi)

        try:
            ranges = vi.tag_ranges(body_tag)
            if len(ranges) < 2:
                self._subtitle_rebuild_texts()
                return

            start_index, end_index = str(ranges[0]), str(ranges[1])
            vi.configure(state="normal")
            vi.delete(start_index, end_index)
            vi.insert(
                start_index,
                translated + "\n",
                ("body", row_tag, body_tag),
            )
            vi.configure(state="disabled")

            if self.subtitle_auto_follow.get():
                if stt is not None:
                    stt.see("end")
                vi.see("end")
                self._schedule_subtitle_expand()
            else:
                if stt is not None and stt_top_tag:
                    ranges = stt.tag_ranges(stt_top_tag)
                    if ranges:
                        stt.yview(str(ranges[0]))
                if vi_top_tag:
                    ranges = vi.tag_ranges(vi_top_tag)
                    if ranges:
                        vi.yview(str(ranges[0]))
        except Exception:
            try:
                vi.configure(state="disabled")
            except Exception:
                pass
            self._subtitle_rebuild_texts()

    def _subtitle_rebuild_texts(
        self,
        *,
        force_stt_bottom: bool | None = None,
        force_vi_bottom: bool | None = None,
        preserve_vi_tag: str | None = None,
    ) -> None:
        stt = self._subtitle_stt_text
        vi = self._subtitle_vi_text
        if vi is None:
            return

        if force_stt_bottom is None:
            force_stt_bottom = bool(self.subtitle_auto_follow.get())
        if force_vi_bottom is None:
            force_vi_bottom = bool(self.subtitle_auto_follow.get())

        if stt is not None:
            stt.configure(state="normal")
            stt.delete("1.0", "end")
            stt.configure(state="disabled")

        vi.configure(state="normal")
        vi.delete("1.0", "end")
        vi.configure(state="disabled")

        self._configure_subtitle_text_tags()
        # Building long history is expensive in Tk when each row calls
        # Text.see() and schedules geometry updates. Batch those once below.
        self._subtitle_rebuilding = True
        try:
            for row in self._subtitle_rows:
                self._subtitle_insert_row_widgets(row)
        finally:
            self._subtitle_rebuilding = False
        self._schedule_subtitle_expand()

        try:
            if preserve_vi_tag:
                ranges = vi.tag_ranges(preserve_vi_tag)
                if ranges:
                    vi.yview(str(ranges[0]))
                    if stt is not None:
                        stt_ranges = stt.tag_ranges(preserve_vi_tag)
                        if stt_ranges:
                            stt.yview(str(stt_ranges[0]))
                    return

            if force_stt_bottom and stt is not None:
                stt.see("end")
            if force_vi_bottom:
                vi.see("end")
        except Exception:
            pass

    def _apply_subtitle_font_sizes(self) -> None:
        self._configure_subtitle_text_tags()
        if self._subtitle_vi_live_preview is not None:
            try:
                self._subtitle_vi_live_preview.configure(
                    font=("Segoe UI", max(5, int(self.subtitle_vi_font_size.get())))
                )
            except Exception:
                pass

    def _expand_subtitle_for_live_preview(self, kind: str) -> None:
        """Reserve real screen space for a growing live preview, not one row."""
        if not self.subtitle_enabled.get() or not self.subtitle_auto_expand.get():
            return
        win = self.subtitle_stt_window if kind == 'stt' else self.subtitle_vi_window
        widget = self._subtitle_live_preview if kind == 'stt' else self._subtitle_vi_live_preview
        if win is None or widget is None:
            return
        try:
            win.update_idletasks()
            # 150px minimum for history and headers below the live preview.
            wanted = max(240, widget.winfo_reqheight() + 150)
            screen_h = win.winfo_screenheight()
            wanted = min(wanted, max(240, int(screen_h * .65)))
            current = win.winfo_height()
            if wanted <= current + 8:
                return
            x, y = win.winfo_x(), win.winfo_y()
            top = max(0, min(screen_h - wanted, y + current - wanted))
            win.geometry(f'{win.winfo_width()}x{wanted}+{x}+{top}')
            if kind == 'vi':
                self._subtitle_vi_height = wanted
            else:
                self._subtitle_stt_height = wanted
        except Exception:
            pass

    def _schedule_subtitle_expand(self) -> None:
        """Grow the actual *floating* translation window to fit new wrapped text.

        A Text widget with wrap=word can still appear to have just one line if
        its borderless Toplevel was previously resized to 110px. We keep a
        bounded multi-line viewing area; when speech is longer than the screen,
        the existing scrollbar remains available.
        """
        if (not self.subtitle_enabled.get() or not self.subtitle_auto_expand.get()
                or self._subtitle_vi_text is None or self.subtitle_vi_window is None):
            return
        if self._subtitle_expand_after_id is not None:
            try:
                self.root.after_cancel(self._subtitle_expand_after_id)
            except Exception:
                pass
        self._subtitle_expand_after_id = self.root.after_idle(self._expand_subtitle_for_latest)

    def _expand_subtitle_for_latest(self) -> None:
        self._subtitle_expand_after_id = None
        if (not self.subtitle_auto_expand.get() or not self._subtitle_rows
                or self._subtitle_vi_text is None or self.subtitle_vi_window is None):
            return
        try:
            from tkinter import font as tkfont
            text = self._subtitle_vi_text
            win = self.subtitle_vi_window
            win.update_idletasks()
            latest = self._subtitle_rows[-1]
            row_tag = self._subtitle_row_tag(str(latest['source']), int(latest['utterance_id']))
            spans = text.tag_ranges(row_tag)
            if len(spans) < 2:
                return
            # Count display lines, not newline-separated logical lines. This
            # includes Tk's actual word wrapping at the current user width.
            line_count = text.count(str(spans[0]), str(spans[-1]), 'displaylines')
            lines = max(3, int(line_count[0]) + 1) if line_count else 4
            font = tkfont.Font(font=('Segoe UI', max(5, int(self.subtitle_vi_font_size.get())), 'bold'))
            px = max(12, int(font.metrics('linespace')))
            # Three+ visible lines, header/footer, and room for the overlay's
            # own header; bounded to 65% of the current screen.
            wanted = max(240, 104 + min(lines, 18) * px)
            screen_h = int(win.winfo_screenheight())
            wanted = min(wanted, max(240, int(screen_h * 0.65)))
            current = int(win.winfo_height())
            if wanted <= current + 8:
                return
            # Expand upward, retaining bottom placement when used over a movie.
            x, y = int(win.winfo_x()), int(win.winfo_y())
            new_y = max(0, min(screen_h - wanted, y + current - wanted))
            win.geometry(f'{win.winfo_width()}x{wanted}+{x}+{new_y}')
            self._subtitle_vi_height = wanted
            if self.subtitle_auto_follow.get():
                text.see('end')
        except Exception:
            pass

    def _position_subtitle_overlay(self, layout: str) -> None:
        self.subtitle_enabled.set(True)
        self._ensure_subtitle_overlay()

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        gap = 14
        margin = 22

        stt = self.subtitle_stt_window
        vi = self.subtitle_vi_window
        if vi is None:
            return

        try:
            vi.update_idletasks()
            vwid, vhgt = vi.winfo_width(), vi.winfo_height()

            if stt is None:
                if layout == "right":
                    x = max(margin, sw - vwid - margin)
                    y = max(margin, (sh - vhgt) // 2)
                elif layout == "side":
                    x = margin
                    y = max(margin, (sh - vhgt) // 2)
                else:
                    x = max(margin, (sw - vwid) // 2)
                    y = max(margin, sh - vhgt - 58)
                vi.geometry(f"+{x}+{y}")
                vi.lift()
                return

            stt.update_idletasks()
            swid, shgt = stt.winfo_width(), stt.winfo_height()

            if layout == "right":
                x = max(margin, sw - max(swid, vwid) - margin)
                total_h = shgt + gap + vhgt
                y = max(margin, (sh - total_h) // 2)
                stt.geometry(f"+{x}+{y}")
                vi.geometry(f"+{x}+{y + shgt + gap}")
            elif layout == "bottom":
                x = max(margin, (sw - max(swid, vwid)) // 2)
                total_h = shgt + gap + vhgt
                y = max(margin, sh - total_h - 58)
                stt.geometry(f"+{x}+{y}")
                vi.geometry(f"+{x}+{y + shgt + gap}")
            else:
                total_w = swid + gap + vwid
                x = max(margin, (sw - total_w) // 2)
                y = max(margin, sh - max(shgt, vhgt) - 58)
                stt.geometry(f"+{x}+{y}")
                vi.geometry(f"+{x + swid + gap}+{y}")

            stt.lift()
            vi.lift()
        except Exception:
            pass

    def _destroy_subtitle_overlay(self) -> None:
        if self._subtitle_open_after_id is not None:
            try:
                self.root.after_cancel(self._subtitle_open_after_id)
            except Exception:
                pass
            self._subtitle_open_after_id = None
        if self._subtitle_expand_after_id is not None:
            try:
                self.root.after_cancel(self._subtitle_expand_after_id)
            except Exception:
                pass
            self._subtitle_expand_after_id = None
        for win in (self.subtitle_stt_window, self.subtitle_vi_window):
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass

        self.subtitle_window = None
        self.subtitle_stt_window = None
        self.subtitle_vi_window = None
        self.subtitle_speaker = None
        self.subtitle_source = None
        self.subtitle_translation = None
        self.subtitle_stt_title = None
        self.subtitle_vi_title = None
        self.subtitle_resize_grip = None
        self._subtitle_live_preview = None
        self._subtitle_vi_live_preview = None
        self._subtitle_stt_text = None
        self._subtitle_vi_text = None

    def _subtitle_refresh_visibility(self) -> None:
        if not self.subtitle_enabled.get():
            return
        self._ensure_subtitle_overlay()
        for win in (self.subtitle_stt_window, self.subtitle_vi_window):
            if win is not None:
                try:
                    win.deiconify()
                    win.lift()
                    win.attributes("-topmost", True)
                except Exception:
                    pass

    def _subtitle_refresh_current_pair(self) -> None:
        self._subtitle_rebuild_texts()

    def _update_subtitle_overlay(
        self,
        *,
        source: str,
        utterance_id: int,
        source_text: str,
        translated_text: str = "",
        final: bool = False,
        source_language: str = "",
        speaker_id: str | None = None,
        speaker_overlap: bool = False,
    ) -> None:
        """Stable dual-window subtitle history.

        Partial STT only touches the small live preview in the STT window.
        A final appends one matching row to both histories. Translation updates
        that exact row even if many newer utterances have already arrived.
        """
        if not self.subtitle_enabled.get():
            return

        self._ensure_subtitle_overlay()
        if self.subtitle_vi_window is None:
            return
        if str(self.subtitle_mode.get()) == "dual" and self.subtitle_stt_window is None:
            return

        source = "mic" if source == "mic" else "system"
        key = (source, int(utterance_id))
        lang = self._valid_lang(source_language, "vi")
        speaker = self._speaker(source)

        if not final:
            now = time.monotonic()
            preview = str(source_text or "").strip()
            preview_key = f"{source}|{lang}|{preview}"

            if preview_key == self._subtitle_last_preview_text:
                return

            # VI is already the final display language: show partial STT
            # progressively instead of waiting for the final utterance.
            if lang == "vi":
                if now - self._subtitle_last_partial_render < 0.16:
                    return
                self._subtitle_last_partial_render = now
                self._subtitle_last_preview_text = preview_key

                if self._subtitle_vi_live_preview is not None:
                    try:
                        self._set_subtitle_preview(self._subtitle_vi_live_preview, preview or "…")
                    except Exception:
                        pass

                # In dual mode, the STT live strip may also show the same
                # progressive VI source text.
                if self._subtitle_live_preview is not None:
                    try:
                        self._set_subtitle_preview(self._subtitle_live_preview,
                                                   f"LIVE  •  {speaker} / VI  •  {preview or '…'}")
                    except Exception:
                        pass
                return

            # JA/EN translation still waits for a complete/final utterance.
            # Only the STT window (when explicitly enabled) gets partial text.
            if self._subtitle_live_preview is None:
                return
            if now - self._subtitle_last_partial_render < 0.45:
                return

            self._subtitle_last_partial_render = now
            self._subtitle_last_preview_text = preview_key
            try:
                self._set_subtitle_preview(self._subtitle_live_preview,
                                           f"LIVE  •  {speaker} / {lang.upper()}  •  {preview or '…'}")
            except Exception:
                pass
            return

        row = self._subtitle_row_by_key.get(key)
        translated = str(translated_text or "").strip()

        if lang == "vi" and self._subtitle_vi_live_preview is not None:
            try:
                self._set_subtitle_preview(self._subtitle_vi_live_preview, "")
            except Exception:
                pass

        if row is None:
            self._subtitle_sequence += 1
            row = {
                "source": source,
                "utterance_id": int(utterance_id),
                "sequence": self._subtitle_sequence,
                "clock": self._clock_now(),
                "source_language": lang,
                "speaker_id": speaker_id,
                "speaker_overlap": speaker_overlap,
                "source_text": str(source_text or "").strip(),
                "translated_text": (
                    translated
                    if translated
                    else (str(source_text or "").strip() if lang == "vi" else "")
                ),
            }
            self._subtitle_rows.append(row)
            self._subtitle_row_by_key[key] = row

            if len(self._subtitle_rows) > 140:
                removed = self._subtitle_rows[:20]
                self._subtitle_rows = self._subtitle_rows[20:]
                for old_row in removed:
                    self._subtitle_row_by_key.pop(
                        (str(old_row["source"]), int(old_row["utterance_id"])),
                        None,
                    )
                self._subtitle_rebuild_texts()
            else:
                self._subtitle_insert_row_widgets(row)
        else:
            if source_text:
                row["source_text"] = str(source_text).strip()
            if translated:
                self._subtitle_replace_translation(
                    source,
                    int(utterance_id),
                    translated,
                )

        if self._subtitle_live_preview is not None and row is not None:
            try:
                self._set_subtitle_preview(self._subtitle_live_preview,
                                           f"Đang nghe câu tiếp theo…  •  vừa chốt #{int(row['sequence']):03d}")
            except Exception:
                pass


    def _copy_timeline(self) -> None:
        if not self._timeline:
            self._refresh_footer("Chưa có nội dung để sao chép")
            self.root.after(1800, self._refresh_footer)
            return

        lines: list[str] = []
        for item in self._timeline:
            source = str(item.get("source", "system"))
            speaker = self._speaker(source, item.get("speaker_id"), bool(item.get("speaker_overlap")))
            clock = str(item.get("clock", "--:--:--"))
            lang = str(item.get("source_language", "")).upper() or "STT"
            src = str(item.get("source_text", "")).strip()
            tr = str(item.get("translated_text", "")).strip()

            lines.append(f"[{clock}] {speaker} [{lang}]")
            if src:
                lines.append(src)
            if tr:
                lines.append(f"VI: {tr}")
            lines.append("")

        payload = "\n".join(lines).rstrip()
        self.root.clipboard_clear()
        self.root.clipboard_append(payload)
        self.root.update_idletasks()
        self._refresh_footer("Đã sao chép toàn bộ hội thoại")
        self.root.after(1800, self._refresh_footer)

    def _handle_status(self, ev: UIEvent) -> None:
        if ev.message:
            self._refresh_footer(ev.message)

    def _update_elapsed(self) -> None:
        if self.stop_requested:
            return
        seconds = max(0, int(time.monotonic() - self._session_started))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            value = f"● LIVE  {hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            value = f"● LIVE  {minutes:02d}:{secs:02d}"
        self.elapsed_text.set(value)
        self.root.after(1000, self._update_elapsed)

    def _close(self) -> None:
        self.stop_requested = True
        self._dismiss_splash()
        self._destroy_subtitle_overlay()
        self.root.after(10, self.root.destroy)

    def _drain(self) -> None:
        timeline_changed = False
        pump_started = time.monotonic()
        processed = 0

        for source, values in self.bus.snapshot_audio_levels().items():
            try:
                dbfs, peak_dbfs, clean_dbfs = values
                self._update_audio_meter(source, dbfs, peak_dbfs, clean_dbfs)
            except Exception:
                pass

        while processed < 48 and time.monotonic() - pump_started < 0.016:
            try:
                ev = self.bus.queue.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if ev.stage == "translation":
                timeline_changed = self._handle_translation(ev) or timeline_changed
            elif ev.stage == "status":
                self._handle_status(ev)
            else:
                timeline_changed = self._handle_asr(ev) or timeline_changed

        if len(self._timeline) > self.history_items:
            del self._timeline[:-self.history_items]
            timeline_changed = True

        if timeline_changed:
            self._render_timeline()

        if not self.stop_requested:
            self.root.after(self.refresh_ms, self._drain)

    def run(self) -> None:
        self.root.after(self.refresh_ms, self._drain)
        self.root.mainloop()
