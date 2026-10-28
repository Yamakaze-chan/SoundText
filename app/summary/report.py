"""Offline, resumable post-meeting report pipeline.

Quick mode deliberately extracts an evidence index, NOT an AI summary.
LFM mode performs hierarchically bounded summarization over ALL transcript chunks;
progress is cached per chunk so a long 4-8h meeting can resume after interruption.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import time
from typing import Callable


@dataclass(frozen=True)
class Turn:
    index: int
    start_s: float
    end_s: float
    source: str
    text: str
    original: str


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    found = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict):
                found.append(item)
    return found


def format_clock(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def load_turns(session: Path) -> list[Turn]:
    translations = {
        (str(e.get("source", "")), int(e.get("utterance_id", -1))): str(e.get("translated_text", "")).strip()
        for e in read_jsonl(session / "translations.jsonl")
        if str(e.get("translated_text", "")).strip()
    }
    rows = []
    for row in read_jsonl(session / "transcript.jsonl"):
        orig = str(row.get("source_text") or row.get("raw_text") or "").strip()
        if not orig or not any(ch.isalnum() for ch in orig):
            continue
        source = str(row.get("source") or "system")
        translated = translations.get((source, int(row.get("utterance_id", -1))), "")
        language = str(row.get("language") or "").lower()
        text = translated or orig
        # Avoid pretending a Japanese original is a Vietnamese summary input.
        if language in ("ja", "en") and not translated:
            text = "[Chưa dịch " + language.upper() + "] " + orig
        rows.append((float(row.get("start_s") or 0), float(row.get("end_s") or 0), source, text, orig))
    rows.sort(key=lambda x: (x[0], x[1]))
    return [Turn(i + 1, *row) for i, row in enumerate(rows)]


def chunk_turns(turns: list[Turn], max_chars: int = 1900) -> list[list[Turn]]:
    """Character-bounded chunks, preserving every turn including oversized ones."""
    if max_chars < 100:
        raise ValueError("max_chars must be >= 100")
    chunks: list[list[Turn]] = []
    active: list[Turn] = []
    active_size = 0
    for turn in turns:
        line_size = len(turn.text) + 48
        if active and active_size + line_size > max_chars:
            chunks.append(active)
            active, active_size = [], 0
        active.append(turn)
        active_size += line_size
    if active:
        chunks.append(active)
    return chunks


def as_evidence_line(turn: Turn) -> str:
    who = "YOU" if turn.source == "mic" else "REMOTE"
    return f"[{turn.index} | {format_clock(turn.start_s)} | {who}] {turn.text}"


def short_extract(turns: list[Turn], max_samples: int = 12) -> list[Turn]:
    if len(turns) <= max_samples:
        return turns
    # Spread throughout the entire recording; never silently omit the final hours.
    indices = {round(i * (len(turns) - 1) / (max_samples - 1)) for i in range(max_samples)}
    return [turns[i] for i in sorted(indices)]


def _build_lfm_callable(model_path: Path, cpu_threads: int, dtype: str) -> Callable[[str, int], str]:
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    if not model_path.joinpath("config.json").is_file():
        raise FileNotFoundError(f"LFM model missing at {model_path}. Download using SoundText.bat menu 9.")
    torch.set_num_threads(max(1, cpu_threads))
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_path), local_files_only=True, trust_remote_code=False,
        dtype=torch_dtype, low_cpu_mem_usage=True,
    ).eval().to("cpu")

    def generate(prompt: str, max_new_tokens: int = 240) -> str:
        messages = [
            {"role": "system", "content": (
                "Bạn là trợ lý lập biên bản. Chỉ dùng thông tin xuất hiện trong dữ liệu. "
                "Viết tiếng Việt, ngắn gọn. Không tự thêm tên người, quyết định, thời hạn. "
                "Nếu không rõ thì ghi 'Chưa xác định'. Phải dẫn mã [số] của dòng nguồn. "
                "Không viết lời dẫn hoặc giải thích bên ngoài nội dung yêu cầu."
            )},
            {"role": "user", "content": prompt},
        ]
        tokens = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_dict=True, return_tensors="pt", tokenize=True
        )
        if tokens["input_ids"].shape[-1] > 3072:
            raise ValueError("Summary prompt exceeded 3072 tokens; reduce --chunk-chars.")
        with torch.inference_mode():
            output = model.generate(
                **tokens, do_sample=False, num_beams=1, max_new_tokens=max_new_tokens,
                use_cache=True, repetition_penalty=1.08,
                pad_token_id=tokenizer.eos_token_id,
            )
        raw = tokenizer.decode(output[0][tokens["input_ids"].shape[-1]:], skip_special_tokens=True)
        return raw.strip()[:5000]
    return generate


def _chunks_fingerprint(chunks: list[list[Turn]], dtype: str) -> str:
    digest = hashlib.sha256(dtype.encode())
    for chunk in chunks:
        for line in (as_evidence_line(turn) for turn in chunk):
            digest.update(line.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _summarize_group(generate: Callable[[str, int], str], items: list[str], *, leaf: bool) -> str:
    intro = (
        "Tóm tắt ĐOẠN HỘI THOẠI sau: chủ đề chính; quyết định nếu được nói rõ; "
        "việc cần làm nếu được giao cụ thể; thông tin chưa chắc chắn. "
        "Mỗi nhận định cần kèm mã dòng như [12]. Không tự suy diễn."
        if leaf else
        "Gộp các tóm tắt đoạn sau thành thông tin xuyên suốt theo thứ tự thời gian. "
        "Nêu chủ đề, các quyết định rõ ràng, việc cần làm và điểm chưa chắc chắn. "
        "Không thêm sự kiện. Giữ nguyên mã dòng [số] nếu có; nếu mâu thuẫn, ghi rõ."
    )
    return generate(intro + "\n\n" + "\n".join(items), 240)


def summarize_hierarchical(
    turns: list[Turn], session: Path, model_path: Path, cpu_threads: int = 2,
    dtype: str = "bfloat16", chunk_chars: int = 1900,
    generate: Callable[[str, int], str] | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[str, list[str]]:
    if not turns:
        return "Không có lời thoại hợp lệ.", []
    chunks = chunk_turns(turns, chunk_chars)
    fingerprint = _chunks_fingerprint(chunks, dtype)
    cache_path = session / "summary_progress.json"
    cached = {}
    if cache_path.is_file():
        try:
            saved = json.loads(cache_path.read_text('utf-8'))
            if saved.get('fingerprint') == fingerprint:
                cached = saved.get('chunks', {})
        except (OSError, ValueError):
            pass
    if generate is None:
        progress("[SUMMARY] Loading LFM on CPU. May be slow for a 4-8 hour meeting.")
        generate = _build_lfm_callable(model_path, cpu_threads, dtype)
    summaries = []
    for index, chunk in enumerate(chunks):
        key = str(index)
        if key not in cached:
            start = time.monotonic()
            text = _summarize_group(generate, [as_evidence_line(t) for t in chunk], leaf=True)
            cached[key] = text
            # Atomic checkpoint: interruption does not destroy previous progress.
            tmp = cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'fingerprint': fingerprint, 'chunks': cached}, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(cache_path)
            progress(f"[SUMMARY] {index+1}/{len(chunks)} completed in {time.monotonic()-start:.1f}s")
        else:
            progress(f"[SUMMARY] {index+1}/{len(chunks)} restored from cache")
        summaries.append(str(cached[key]))
    # Repeated aggregation is bounded; 4-8h transcript never goes in one prompt.
    # Group small summaries into max ~1000 chars total to avoid context overflow.
    stage = summaries
    round_n = 0
    while len(stage) > 1:
        round_n += 1
        groups: list[list[str]] = []
        current: list[str] = []
        size = 0
        for item in stage:
            if current and size + len(item) > 1100:
                groups.append(current)
                current, size = [], 0
            current.append(item)
            size += len(item)
        if current:
            groups.append(current)
        # Too-long single summaries would prevent reduction: trim for hierarchy only.
        if len(groups) == len(stage):
            groups = [stage[i:i+3] for i in range(0, len(stage), 3)]
            groups = [[text[:550] for text in group] for group in groups]
        progress(f"[SUMMARY] Hierarchy level {round_n}: {len(stage)} -> {len(groups)}")
        stage = [_summarize_group(generate, group, leaf=False) for group in groups]
    return stage[0] if stage else "", summaries


def _write_html(session: Path, turns: list[Turn], summary: str, mode: str, chunk_count: int) -> Path:
    safe = html.escape
    duration_s = max((t.end_s for t in turns), default=0.0)
    you = sum(t.source == "mic" for t in turns)
    remote = len(turns) - you
    if mode == 'quick':
        title = "Chỉ mục cuộc họp (không dùng AI)"
        label = "Xem nhanh • Không phải bản tóm tắt AI"
        desc = "Các trích đoạn phân bố xuyên suốt phiên, giúp tìm lại lời thoại. Chưa tự động suy ra quyết định hay công việc."
    else:
        title = "Bản tóm tắt AI — cần xác minh"
        label = "LFM2.5 • Phân cấp • Chạy sau cuộc họp"
        desc = "Bản tổng hợp tự động bằng mô hình 1.2B, có thể bỏ sót hoặc hiểu sai. Đối chiếu những kết luận quan trọng với transcript bên dưới."
    rows_html = "\n".join(
        f'<article class="turn" id="turn-{t.index}"><div class="stamp">{format_clock(t.start_s)}</div>'
        f'<div class="turn-body"><span class="speaker {"mic" if t.source == "mic" else "remote"}">'
        f'{"YOU" if t.source == "mic" else "REMOTE"}</span>'
        f'<p>{safe(t.text)}</p>' + (f'<div class="original">Gốc: {safe(t.original)}</div>' if t.text != t.original else '') +
        f'<div class="ref">#{t.index}</div></div></article>' for t in turns
    )
    summary_html = ''.join(f'<p>{safe(line)}</p>' for line in summary.split('\n') if line.strip()) or '<p>Chưa có dữ liệu.</p>'
    # Link reference codes to evidence only if they are actual rows; never trust generated links/HTML.
    valid = {t.index for t in turns}
    def link_ref(m):
        n = int(m.group(1))
        return f'<a href="#turn-{n}" class="inline-ref">[{n}]</a>' if n in valid else f'[{n}]'
    summary_html = re.sub(r'\[(\d+)\]', link_ref, summary_html)
    out = session / 'meeting_report.html'
    out.write_text(f'''<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SoundText Studio · {safe(title)}</title>
<style>
:root{{--bg:#080d18;--panel:#101a2b;--panel2:#142139;--stroke:#263852;--txt:#f4f7ff;--muted:#a5b3c9;--cyan:#69d9ff;--violet:#c6a9ff;--green:#5ce6b3}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(ellipse at 80% 0,#193251 0,transparent 40%),var(--bg);color:var(--txt);font:15px/1.65 'Segoe UI',system-ui,sans-serif}}
.wrap{{max-width:1220px;margin:auto;padding:42px 26px 70px}}.eyebrow{{color:var(--cyan);font-weight:700;letter-spacing:.2em;text-transform:uppercase;font-size:11px}}h1{{font-size:clamp(30px,5vw,54px);line-height:1.13;letter-spacing:-.04em;margin:10px 0 12px}}.muted{{color:var(--muted)}}.hero{{padding:28px 0 30px}}.pill{{border:1px solid var(--stroke);background:#13273a;border-radius:30px;padding:7px 13px;display:inline-block;color:var(--cyan);font-size:12px;font-weight:700}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:24px 0 34px}}.stat,.panel{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--stroke);border-radius:20px}}.stat{{padding:21px}}.stat b{{font-size:27px;display:block;letter-spacing:-.04em}}.stat span{{color:var(--muted);font-size:12px}}.columns{{display:grid;grid-template-columns: minmax(0,4fr) minmax(330px,5fr);gap:18px;align-items:start}}.panel{{padding:26px;min-width:0}}h2{{font-size:20px;letter-spacing:-.02em;margin:0 0 14px}}.report{{font-size:15px;line-height:1.85;white-space:normal;overflow-wrap:anywhere}}.report p{{margin:0 0 12px}}.warning{{font-size:13px;color:#ffda9a;border-left:3px solid #f4c66a;padding:9px 12px;background:#45321e55;border-radius:4px}}.turns{{max-height:76vh;overflow-y:auto;scrollbar-color:#496483 transparent}}.turn{{display:flex;gap:15px;border-top:1px solid var(--stroke);padding:16px 0;scroll-margin-top:25px}}.stamp{{width:72px;flex-shrink:0;color:var(--muted);font-variant-numeric:tabular-nums;font-size:12px;padding-top:3px}}.turn-body{{min-width:0;flex:1}}.turn p{{margin:7px 0 3px;overflow-wrap:anywhere}}.speaker{{font-size:10px;font-weight:800;letter-spacing:.08em;border-radius:8px;padding:4px 7px}}.mic{{background:#17443c;color:#96f1cc}}.remote{{background:#362854;color:#d6baff}}.original{{color:var(--muted);font-size:12px;margin:3px 0}}.ref{{color:#6885a4;font-size:11px}}a{{color:var(--cyan);text-decoration:none}}a:hover{{text-decoration:underline}}.inline-ref{{border:1px solid #27617a;padding:1px 4px;border-radius:5px}}.sticky{{position:sticky;top:12px}}footer{{margin-top:30px;color:var(--muted);font-size:12px}}
@media(max-width:800px){{.stats{{grid-template-columns:repeat(2,1fr)}}.columns{{grid-template-columns:1fr}}.sticky{{position:static}}.turns{{max-height:none}}}}
</style></head><body><main class="wrap"><header class="hero"><div class="eyebrow">SoundText / Meeting Intelligence</div><h1>{safe(title)}</h1><p class="muted">{safe(desc)}</p><span class="pill">{safe(label)}</span></header>
<section class="stats"><div class="stat"><b>{format_clock(duration_s)}</b><span>Thời lượng theo timestamp</span></div><div class="stat"><b>{len(turns):,}</b><span>Lượt lời thoại</span></div><div class="stat"><b>{you:,}</b><span>YOU / microphone</span></div><div class="stat"><b>{remote:,}</b><span>REMOTE / hệ thống</span></div></section>
<div class="columns"><section class="panel sticky"><h2>Điểm chính</h2><div class="report">{summary_html}</div><p class="warning">{safe(desc)}</p><p class="muted" style="font-size:12px">Đã xử lý {chunk_count} đoạn dữ liệu. Nội dung chưa được người tham dự xác nhận.</p></section>
<section class="panel"><h2>Transcript &amp; dẫn chứng</h2><p class="muted">Đọc theo thứ tự thời gian • Bản dịch nếu có, nếu không giữ nguyên tiếng gốc</p><div class="turns">{rows_html}</div></section></div><footer>SoundText Studio · Báo cáo tạo offline • {datetime.now().strftime('%Y-%m-%d %H:%M')}</footer></main></body></html>''', encoding='utf-8')
    return out


def create_report(session: Path, mode: str = 'quick', *, model_path: Path | None = None,
                  cpu_threads: int = 2, dtype: str = 'bfloat16', chunk_chars: int = 1900,
                  generate: Callable[[str, int], str] | None = None,
                  progress: Callable[[str], None] = print) -> Path:
    turns = load_turns(session)
    if not turns:
        raise ValueError(f"No usable transcript.jsonl found in {session}")
    if mode == 'quick':
        examples = short_extract(turns)
        summary = "TRÍCH ĐOẠN THEO THỜI GIAN (không phải tóm tắt ngữ nghĩa):\n" + "\n".join(
            as_evidence_line(t) for t in examples
        )
        summaries = chunk_turns(turns)
    elif mode == 'lfm':
        if model_path is None and generate is None:
            raise ValueError('model_path is required in LFM mode')
        summary, summaries = summarize_hierarchical(
            turns, session, model_path or Path('models/lfm2.5-1.2b-instruct'),
            cpu_threads, dtype, chunk_chars, generate, progress,
        )
    else:
        raise ValueError(f'Unknown summary mode {mode}')
    out = _write_html(session, turns, summary, mode, len(summaries))
    (session / 'meeting_summary.md').write_text(
        f'# {"Tóm tắt AI (cần xác minh)" if mode == "lfm" else "Chỉ mục cuộc họp (không dùng AI)"}\n\n{summary}\n\n'
        '---\nThông tin quan trọng cần đối chiếu với transcript.jsonl.\n', encoding='utf-8'
    )
    return out
