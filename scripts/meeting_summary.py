"""Generate an offline post-meeting report. Never run LFM during recording."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.summary.report import create_report


def main() -> int:
    parser = argparse.ArgumentParser(description='Create SoundText post-meeting report')
    parser.add_argument('--session', type=Path, help='Existing session directory; defaults to latest session containing transcript.jsonl')
    parser.add_argument('--mode', choices=('quick', 'lfm'), default='quick')
    parser.add_argument('--chunk-chars', type=int, default=1900)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    config = json.loads((ROOT/'config_stt_vi_ja.json').read_text('utf-8'))
    root = ROOT / config.get('storage', {}).get('output_dir', 'output')
    if args.session:
        session = args.session.resolve()
    else:
        choices = [p for p in root.glob('meeting_*') if p.is_dir() and (p/'transcript.jsonl').is_file()]
        if not choices:
            print('[SUMMARY ERROR] No sessions with transcript.jsonl in output/.')
            return 2
        session = max(choices, key=lambda p: p.name)
    if not (session/'transcript.jsonl').is_file():
        print(f'[SUMMARY ERROR] Missing {session / "transcript.jsonl"}')
        return 2
    if args.mode == 'lfm':
        state = session / 'session_state.json'
        if state.is_file():
            try:
                status = json.loads(state.read_text('utf-8')).get('status')
                if status == 'running':
                    print('[SUMMARY ERROR] Session still running. Stop recording before LFM summary.')
                    return 2
            except ValueError:
                pass
        print('LFM CPU summaries of long meetings can take a VERY long time. Per-chunk progress is resumable.')
    trans = config.get('translation', {})
    model_path = ROOT / trans.get('model_path', 'models/lfm2.5-1.2b-instruct')
    try:
        report = create_report(
            session, args.mode, model_path=model_path,
            cpu_threads=int(trans.get('cpu_threads', 2)),
            dtype=str(trans.get('lfm_cpu_dtype', 'bfloat16')),
            chunk_chars=args.chunk_chars,
        )
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        print(f'[SUMMARY ERROR] {exc}')
        return 1
    print(f'\n[SUMMARY] HTML report: {report}')
    print(f'[SUMMARY] Markdown: {session / "meeting_summary.md"}')
    if not args.no_open:
        webbrowser.open(report.resolve().as_uri())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
