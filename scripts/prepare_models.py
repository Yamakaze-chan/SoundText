from __future__ import annotations

from pathlib import Path
from model_paths import configure_local_model_cache
from urllib.request import urlretrieve

SILERO_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
GIPFORMER_REPO = "g-group-ai-lab/gipformer1.5-65M-rnnt"
SENSEVOICE_REPO = "FunAudioLLM/SenseVoiceSmall"
GIPFORMER_FILES = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt", "bpe.model", "config.json"]


def main() -> None:
    root = configure_local_model_cache()
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError as exc:
        raise SystemExit("Missing huggingface_hub. Run: python -m pip install huggingface_hub") from exc

    vad = root / "silero-vad" / "silero_vad.onnx"
    if not vad.is_file():
        vad.parent.mkdir(parents=True, exist_ok=True)
        print(f"[DOWNLOAD] Silero VAD -> {vad}")
        urlretrieve(SILERO_URL, vad)
    else:
        print(f"[OK] {vad}")

    gip = root / "gipformer1.5-65m-rnnt"
    gip.mkdir(parents=True, exist_ok=True)
    for name in GIPFORMER_FILES:
        dst = gip / name
        if dst.is_file():
            continue
        print(f"[DOWNLOAD] Gipformer: {name}")
        hf_hub_download(repo_id=GIPFORMER_REPO, filename=name, local_dir=str(gip))
    print(f"[OK] Vietnamese model: {gip}")

    sv = root / "sensevoice-small"
    marker = sv / ".soundtext_prepared"
    required = (
        "config.yaml", "model.pt", "chn_jpn_yue_eng_ko_spectok.bpe.model", "am.mvn",
    )
    missing = [name for name in required if not (sv / name).is_file() or (sv / name).stat().st_size == 0]
    if missing:
        sv.mkdir(parents=True, exist_ok=True)
        print(f"[DOWNLOAD] SenseVoiceSmall missing files: {', '.join(missing)} -> {sv}")
        # Download only the files needed for local FunASR inference.
        snapshot_download(repo_id=SENSEVOICE_REPO, local_dir=str(sv),
                          allow_patterns=[*required, "configuration.json"])
        still_missing = [name for name in required if not (sv / name).is_file()]
        if still_missing:
            raise RuntimeError(f"SenseVoice download incomplete: {still_missing}")
        marker.write_text("SoundText local SenseVoice\n", encoding="utf-8")
    else:
        print(f"[OK] Japanese model: {sv}")
    print("Done. Runtime can now run offline.")


if __name__ == "__main__":
    main()
