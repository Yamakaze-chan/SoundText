"""Download only two ONNX speaker models into ./models (no global HF cache)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'models'


def main() -> int:
    seg_dir = MODELS / 'speaker-diarization'
    emb_dir = MODELS / 'speaker-embedding'
    seg_dir.mkdir(parents=True, exist_ok=True)
    emb_dir.mkdir(parents=True, exist_ok=True)
    # Global HF/Transformers model cache is kept under ./models/.
    os.environ['HF_HOME'] = str(MODELS / '_hf_cache')
    os.environ['HUGGINGFACE_HUB_CACHE'] = str(MODELS / '_hf_cache' / 'hub')
    os.environ['HF_HUB_CACHE'] = str(MODELS / '_hf_cache' / 'hub')
    os.environ['HF_XET_CACHE'] = str(MODELS / '_hf_cache' / 'xet')
    from huggingface_hub import hf_hub_download

    tasks = [
        ('FredrikKarlssonSpeech/pyannote-speaker-diarization-onnx', 'segmentation/model_int8.onnx', seg_dir / 'segmentation.onnx'),
        ('OpenVoiceOS/redimnet-b2-vox2-onnx', 'redimnet_b2_vox2.onnx', emb_dir / 'redimnet_b2_vox2.onnx'),
    ]
    for repo, filename, dst in tasks:
        minimum = 400_000 if "segmentation" in str(dst) else 4_000_000
        if dst.is_file() and dst.stat().st_size >= minimum:
            print(f'[OK] Already downloaded: {dst}')
            continue
        print(f'[DOWNLOAD] {repo} / {filename} -> {dst}')
        # local_dir is an in-project staging area for resumed partial downloads.
        staged = MODELS / '_speaker_downloads' / repo.split('/')[-1]
        path = Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(staged)))
        if path.stat().st_size < minimum:
            raise IOError(f'Incomplete model download: {path}')
        # Same ./models volume: atomic move avoids retaining a second 22-MB copy.
        path.replace(dst)
        print(f'[OK] {dst} ({dst.stat().st_size / 1024 / 1024:.1f} MB)')
    print('Speaker models ready. Enable diarization in config_stt_vi_ja.json.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
