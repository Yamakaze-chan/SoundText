"""Generate a Windows EXE icon from the same branding.json used by the GUI."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ui.branding import load_branding, resolve_branding_path  # noqa: E402
from app.user_config import ensure_user_configs


def make_exe_icon() -> Path:
    ensure_user_configs(ROOT)
    from PIL import Image

    path = resolve_branding_path(str(load_branding().get("icon_path") or ""))
    if path is None:
        raise FileNotFoundError("icon_path trong branding.json không tồn tại")
    out = ROOT / "build" / "soundtext-exe.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as img:
        image = img.convert("RGBA")
        image.thumbnail((256, 256), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((256 - image.width)//2, (256 - image.height)//2))
        canvas.save(out, format="ICO", sizes=[(16,16), (24,24), (32,32),
                                               (48,48), (64,64), (128,128), (256,256)])
    print(f"[EXE ICON] {path} -> {out}")
    return out


if __name__ == "__main__":
    try:
        make_exe_icon()
    except Exception as exc:
        print(f"[EXE ICON ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
