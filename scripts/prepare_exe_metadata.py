"""Create PyInstaller's Windows version resource from editable branding.json."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _safe_four_part_version(raw: str) -> tuple[int, int, int, int]:
    raw = str(raw).strip()
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", raw):
        raise ValueError("app_version phải gồm 1-4 số, ví dụ 1.0.0")
    numbers = [int(n) for n in raw.split(".")]
    if any(n > 65535 for n in numbers):
        raise ValueError("Mỗi thành phần app_version phải trong khoảng 0–65535")
    return tuple((numbers + [0] * 4)[:4])


def _repr_field(value: str) -> str:
    """repr escapes quotes, backslashes, unicode for the resource's Python DSL."""
    return repr(str(value))


def make_version_file() -> Path:
    from app.user_config import ensure_user_configs
    ensure_user_configs(ROOT)
    data = json.loads((ROOT / "branding.json").read_text(encoding="utf-8"))
    product = str(data.get("app_name", "SoundText")).strip() or "SoundText"
    publisher = str(data.get("publisher", "SoundText")).strip() or "SoundText"
    text_version = str(data.get("app_version", "1.0.0"))
    version = _safe_four_part_version(text_version)
    detail_version = ".".join(str(x) for x in version)
    values = {
        'CompanyName': publisher,
        'FileDescription': product + ' — Ghi âm và phụ đề cuộc họp',
        'FileVersion': detail_version,
        'InternalName': 'SoundText',
        'OriginalFilename': 'SoundText.exe',
        'ProductName': product,
        'ProductVersion': detail_version,
    }
    fields = ",\n".join("            StringStruct(" + _repr_field(k) + ", " + _repr_field(v) + ")" for k,v in values.items())
    content = f'''# Generated at build time from branding.json — do not edit directly.\nVSVersionInfo(\n ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,\n                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),\n kids=[StringFileInfo([StringTable('040904b0', [\n{fields}\n ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]\n)\n'''
    output = ROOT / 'build' / 'soundtext-version.txt'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content,encoding='utf8')
    print(f"[EXE METADATA] {product}, {publisher}, {detail_version} -> {output}")
    return output


if __name__ == '__main__':
    try:
        make_version_file()
    except Exception as exc:
        print(f"[EXE METADATA ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
