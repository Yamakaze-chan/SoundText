"""Small tests that don't download or load the 2.34GB checkpoint."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.config import AppConfig
from app.translation.factory import build_translation_engine
from app.translation.lfm_transformers_engine import LocalLfmTranslator


class TranslationTests(unittest.TestCase):
    def test_config_default_is_nllb(self):
        conf = AppConfig.load(Path(__file__).resolve().parents[1] / "config_stt_vi_ja.json")
        self.assertEqual(conf.translation.backend, "nllb_onnx")
        self.assertEqual(conf.translation.model_path, "models/lfm2.5-1.2b-instruct")
        self.assertEqual(conf.translation.lfm_cpu_dtype, "bfloat16")

    def test_factory_switches_without_repointing_model(self):
        conf = AppConfig()
        conf.translation.backend = "lfm_transformers"
        with patch('app.translation.factory.LocalLfmTranslator', return_value='LFM') as lfm:
            self.assertEqual(build_translation_engine(conf), 'LFM')
            lfm.assert_called_once()
            self.assertEqual(lfm.call_args.args[0], "models/lfm2.5-1.2b-instruct")
        conf.translation.backend = 'nllb_onnx'
        with patch('app.translation.factory.LocalNllbOnnxTranslator', return_value='NLLB') as nllb:
            self.assertEqual(build_translation_engine(conf), 'NLLB')
            self.assertEqual(nllb.call_args.args[0], "models/nllb-trilingual-en-vi-ja-onnx")

    def test_clean_output(self):
        self.assertEqual(LocalLfmTranslator._clean_output('Bản dịch: "Xin chào."'), 'Xin chào.')
        self.assertEqual(LocalLfmTranslator._clean_output('Vietnamese translation: Tạm biệt.'), 'Tạm biệt.')

    def test_avoid_empty_sentence(self):
        eng = object.__new__(LocalLfmTranslator)
        self.assertEqual(eng.translate('。', 'ja', 'vi'), ('', 0.0))
        self.assertEqual(eng.translate('...!', 'en', 'vi'), ('', 0.0))
        with self.assertRaises(ValueError):
            eng.translate('Hello', 'fr', 'vi')


if __name__ == '__main__':
    unittest.main()
