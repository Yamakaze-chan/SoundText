from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote

import requests
from model_paths import configure_local_model_cache


MODEL_DIR = configure_local_model_cache() / "nllb-trilingual-en-vi-ja-onnx"
REPO_ID = "belumind/nllb-trilingual-en-vi-ja-onnx"
REVISION = "main"

# Only the local INT8 runtime files SoundText actually needs.
FILES = [
    "config.json",
    "generation_config.json",
    "ort_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "encoder_model_quantized.onnx",
    "decoder_model_quantized.onnx",
    "decoder_with_past_model_quantized.onnx",
]

REQUIRED = {
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "sentencepiece.bpe.model",
    "encoder_model_quantized.onnx",
    "decoder_model_quantized.onnx",
    "decoder_with_past_model_quantized.onnx",
}

CHUNK_SIZE = 8 * 1024 * 1024
MAX_ATTEMPTS = 30


def human_bytes(value: int | None) -> str:
    if value is None:
        return "?"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{value} B"


def total_from_response(response: requests.Response, already: int) -> int | None:
    content_range = response.headers.get("Content-Range", "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))

    content_length = response.headers.get("Content-Length")
    if content_length and content_length.isdigit():
        remaining = int(content_length)
        if response.status_code == 206:
            return already + remaining
        return remaining
    return None


def make_url(filename: str) -> str:
    # Direct resolve endpoint: avoids snapshot_download()'s repo_info API call.
    escaped = quote(filename, safe="/")
    return f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/{escaped}?download=true"


def download_one(session: requests.Session, filename: str) -> None:
    target = MODEL_DIR / filename
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Existing finished file: leave it alone.
    if target.is_file() and target.stat().st_size > 0:
        print(f"[SKIP] {filename} ({human_bytes(target.stat().st_size)})")
        return

    for attempt in range(1, MAX_ATTEMPTS + 1):
        already = part.stat().st_size if part.exists() else 0
        headers = {
            "User-Agent": "SoundText/1.0 direct-resume-downloader",
            "Accept-Encoding": "identity",
        }
        if already:
            headers["Range"] = f"bytes={already}-"

        try:
            print(
                f"[GET ] {filename}  attempt {attempt}/{MAX_ATTEMPTS}"
                + (f"  resume={human_bytes(already)}" if already else "")
            )
            with session.get(
                make_url(filename),
                headers=headers,
                stream=True,
                allow_redirects=True,
                timeout=(25, 180),
            ) as response:
                # A complete .part can produce 416 for a Range request.
                if response.status_code == 416 and already:
                    cr = response.headers.get("Content-Range", "")
                    m = re.search(r"\*/(\d+)", cr)
                    if m and already == int(m.group(1)):
                        part.replace(target)
                        print(f"[ OK ] {filename} ({human_bytes(already)})")
                        return
                    part.unlink(missing_ok=True)
                    raise RuntimeError("Server rejected resume range; restarting this file")

                response.raise_for_status()

                # If Range was requested but the server answers 200, it ignored Range.
                # Restart rather than appending a full file to a partial file.
                if already and response.status_code != 206:
                    print("[INFO] Server ignored Range; restarting this file safely.")
                    part.unlink(missing_ok=True)
                    already = 0

                total = total_from_response(response, already)
                mode = "ab" if already and response.status_code == 206 else "wb"
                written = already if mode == "ab" else 0
                last_report = time.monotonic()

                with part.open(mode) as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)

                        now = time.monotonic()
                        if now - last_report >= 1.5:
                            if total:
                                pct = min(100.0, written * 100.0 / total)
                                print(
                                    f"       {human_bytes(written)} / {human_bytes(total)}"
                                    f"  ({pct:5.1f}%)",
                                    flush=True,
                                )
                            else:
                                print(f"       {human_bytes(written)}", flush=True)
                            last_report = now

                final_size = part.stat().st_size
                if total is not None and final_size != total:
                    raise IOError(
                        f"Incomplete stream: got {final_size} bytes, expected {total}. "
                        "Will resume."
                    )

                part.replace(target)
                print(f"[ OK ] {filename} ({human_bytes(target.stat().st_size)})")
                return

        except KeyboardInterrupt:
            print("\n[STOP] Interrupted. Partial file kept; run again to resume.")
            raise
        except Exception as exc:
            wait_s = min(20, 2 + attempt)
            kept = part.stat().st_size if part.exists() else 0
            print(
                f"[WARN] {type(exc).__name__}: {exc}\n"
                f"       kept {human_bytes(kept)}; retrying in {wait_s}s..."
            )
            if attempt >= MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Could not download {filename} after {MAX_ATTEMPTS} attempts. "
                    f"Partial file is kept at {part}"
                ) from exc
            time.sleep(wait_s)


def main() -> int:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.trust_env = True  # respect Windows proxy settings if configured

    print("SoundText NLLB INT8 direct/resumable downloader")
    print(f"Repo   : {REPO_ID}")
    print(f"Target : {MODEL_DIR}")
    print("Mode   : direct file download, retry + resume; no repo_info/snapshot API")
    print()

    try:
        for filename in FILES:
            download_one(session, filename)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print()
        print(f"[ERROR] {exc}")
        print("[INFO] You can run this script again; .part files will be resumed.")
        return 1
    finally:
        session.close()

    missing = sorted(name for name in REQUIRED if not (MODEL_DIR / name).is_file())
    if missing:
        print("\n[ERROR] Required files are still missing:")
        for name in missing:
            print("  -", name)
        return 1

    print()
    print("[OK] NLLB INT8 ONNX is ready.")
    print(f"Model path: {MODEL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
