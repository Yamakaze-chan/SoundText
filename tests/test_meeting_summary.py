"""Summary tests require no models or audio devices."""
from pathlib import Path
import json
import tempfile
import unittest

from app.summary.report import (Turn, chunk_turns, create_report, load_turns,
                                short_extract, summarize_hierarchical)
from app.translation.worker import TranslationWorker
from app.translation.types import TranslationJob


class SummaryTests(unittest.TestCase):
    def test_turns_merge_translation_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'transcript.jsonl').write_text('\n'.join([
                json.dumps({'source': 'system', 'utterance_id': 2, 'start_s': 16, 'end_s': 19,
                            'language': 'ja', 'source_text': 'こんにちは'}, ensure_ascii=False),
                json.dumps({'source': 'mic', 'utterance_id': 1, 'start_s': 1, 'end_s': 3,
                            'language': 'vi', 'source_text': 'Xin chào'}, ensure_ascii=False),
                json.dumps({'source': 'system', 'utterance_id': 3, 'start_s': 20, 'end_s': 21,
                            'language': 'ja', 'source_text': '。'}, ensure_ascii=False),
            ]), encoding='utf8')
            (root/'translations.jsonl').write_text(json.dumps({'source': 'system',
                'utterance_id': 2, 'translated_text': 'Chào bạn'}, ensure_ascii=False), encoding='utf8')
            turns = load_turns(root)
            self.assertEqual(len(turns), 2)
            self.assertEqual([t.text for t in turns], ['Xin chào', 'Chào bạn'])
            self.assertEqual([t.index for t in turns], [1, 2])
            report = create_report(root, 'quick')
            html_text = report.read_text('utf-8')
            self.assertIn('Không phải bản tóm tắt AI', html_text)
            self.assertIn('Chào bạn', html_text)
            self.assertTrue((root/'meeting_summary.md').exists())

    def test_chunk_completeness_and_resume(self):
        turns = [Turn(i+1, i*20, i*20+1, 'mic', f'Action item {i} for meeting',
                      f'Action item {i} for meeting') for i in range(30)]
        chunks = chunk_turns(turns, max_chars=150)
        self.assertEqual([t.index for chunk in chunks for t in chunk], list(range(1,31)))
        self.assertEqual(short_extract(turns)[0], turns[0])
        self.assertEqual(short_extract(turns)[-1], turns[-1])
        with tempfile.TemporaryDirectory() as td:
            calls = []
            def fake(prompt, max_new_tokens):
                calls.append(prompt)
                return 'Đoạn ghi chú [1]'
            result, stage = summarize_hierarchical(turns, Path(td), Path(td),
                chunk_chars=150, generate=fake, progress=lambda _: None)
            self.assertIn('[1]', result)
            self.assertEqual(len(stage), len(chunks))
            self.assertTrue((Path(td)/'summary_progress.json').is_file())
            initial_calls = len(calls)
            summarize_hierarchical(turns, Path(td), Path(td), chunk_chars=150,
                generate=fake, progress=lambda _: None)
            self.assertLess(len(calls) - initial_calls, initial_calls)

    def test_html_escapes_untrusted_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'transcript.jsonl').write_text(json.dumps({'source':'mic', 'utterance_id':1,
                'start_s':0, 'end_s':1, 'source_text':'<script>alert(1)</script>'}), encoding='utf8')
            html_text = create_report(root, 'quick').read_text('utf8')
            self.assertIn('&lt;script&gt;', html_text)
            self.assertNotIn('<script>alert', html_text)

    def test_translation_worker_wait_metric(self):
        import time
        import threading
        finished = threading.Event()
        results = []
        class Engine:
            def translate(self, text, source, target):
                return 'Xin chào', 0.01
        worker = TranslationWorker(Engine(), lambda item: (results.append(item), finished.set()))
        worker.start()
        worker.submit(TranslationJob('system', 1, 'こんにちは', 'ja'))
        self.assertTrue(finished.wait(2))
        worker.stop()
        self.assertGreaterEqual(results[0].queue_wait_s, 0)

if __name__ == '__main__':
    unittest.main()
