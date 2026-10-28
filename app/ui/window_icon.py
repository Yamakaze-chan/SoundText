"""Load window icons from branding.json for CustomTkinter on Windows/Tk.

Presentation only. Never alters recording, ASR, translation, or session data.
The installed Windows icon and the process taskbar identity are distinct from
an optional icon embedded in an EXE/shortcut, which requires packaging support.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from app.ui.branding import ASSET_DIR, resolve_branding_path


APP_USER_MODEL_ID = "SoundText.Studio"
WINDOW_ICON_REAPPLY_MS = (350, 850)


def configure_windows_app_identity() -> None:
    """Tell Windows to group the app under its own taskbar identity."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as exc:  # Taskbar identity is optional; never block startup.
        print(f"[BRANDING WARN] Cannot set Windows taskbar identity: {exc}")


class WindowIconManager:
    """Apply a PNG/ICO from branding.json to each actual Tk/CTk window.

    CustomTkinter may install its own icon using a delayed callback. Applying
    the icon after that callback also keeps titlebars and taskbar consistent.
    """

    def __init__(self, icon_path: str):
        self.source = str(icon_path).strip()
        self.path = resolve_branding_path(self.source)
        self._warnings: set[str] = set()
        self._ico_path: Path | None = None
        if self.source and self.path is None:
            self._warn(f"Icon not found: {self.source!r}; check branding.json")

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            print(f"[BRANDING WARN] {message}")
            self._warnings.add(message)

    def _windows_ico(self) -> Path | None:
        """Tk on Windows needs a real ICO to reliably update taskbar icons."""
        if self.path is None:
            return None
        if self.path.suffix.lower() == ".ico":
            return self.path
        if self._ico_path is not None and self._ico_path.is_file():
            return self._ico_path
        try:
            from PIL import Image
            raw_bytes = self.path.read_bytes()
            digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
            folder = ASSET_DIR / ".icon-cache"
            target = folder / f"icon-{digest}.ico"
            if not target.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                with Image.open(self.path) as source:
                    image = source.convert("RGBA")
                    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    # Preserve aspect ratio and transparency in a square icon.
                    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
                    x = (256 - image.width) // 2
                    y = (256 - image.height) // 2
                    canvas.alpha_composite(image, (x, y))
                    canvas.save(target, format="ICO", sizes=[(16, 16), (24, 24),
                                                              (32, 32), (48, 48),
                                                              (64, 64), (128, 128),
                                                              (256, 256)])
            self._ico_path = target
            return target
        except Exception as exc:  # Never crash recording UI due to a branding asset.
            self._warn(f"Cannot make Windows ICO from {self.path}: {exc}")
            return None

    def apply(self, window) -> bool:
        """Set the icon for a window, retaining its PhotoImage until destruction."""
        if self.path is None:
            return False
        applied = False
        try:
            from PIL import Image, ImageTk
            with Image.open(self.path) as source:
                picture = source.convert("RGBA")
                picture.thumbnail((256, 256), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(picture, master=window)
            window._soundtext_icon_photo = photo  # Keep Tk image alive.
            window.iconphoto(True, photo)
            applied = True
        except Exception as exc:  # Includes Tk/Tcl errors from invalid icons.
            self._warn(f"Cannot set icon image from {self.path}: {exc}")

        if sys.platform == "win32":
            ico = self._windows_ico()
            if ico is not None:
                try:
                    window.iconbitmap(str(ico))
                    applied = True
                except Exception as exc:  # Includes Tk/Tcl errors on Windows.
                    self._warn(f"Cannot set Windows icon from {ico}: {exc}")
        return applied

    def install(self, window) -> None:
        """Apply now and again after CTk's own delayed default-icon setup."""
        if self.path is None:
            return
        self.apply(window)
        for delay in WINDOW_ICON_REAPPLY_MS:
            window.after(delay, lambda win=window: self._apply_if_alive(win))

    def _apply_if_alive(self, window) -> None:
        try:
            if window.winfo_exists():
                self.apply(window)
        except Exception:
            # Window was closed while the callback was pending.
            return
