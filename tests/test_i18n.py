"""界面多语言：词库完整性与占位符一致性。"""

import json
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "videoforge" / "web" / "static"
I18N = STATIC / "i18n"
LANGS = ("en", "fr", "de", "ja", "ko", "zh-TW", "es", "ar")
# 源语言是简体中文，作为 key 基准
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(lang):
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


class TestI18nCatalog(unittest.TestCase):
    def test_all_languages_have_catalog(self):
        for lang in LANGS:
            self.assertTrue((I18N / f"{lang}.json").exists(), f"{lang} 缺词库")

    def test_catalog_keys_match_english(self):
        base = set(load("en"))
        for lang in LANGS:
            data = load(lang)
            self.assertTrue(base.issubset(set(data)),
                            f"{lang} 缺少 {len(base - set(data))} 个词条")
            self.assertEqual(sorted(set(data) - base), [], f"{lang} 有多余词条")

    def test_placeholders_preserved(self):
        """{n} {sec} 这类占位符必须原样保留，否则运行时会漏值。"""
        for lang in LANGS:
            for key, val in load(lang).items():
                need = set(PLACEHOLDER.findall(key))
                got = set(PLACEHOLDER.findall(str(val)))
                missing = need - got
                self.assertFalse(missing, f"{lang} 词条「{key}」丢了占位符 {missing}")

    def test_no_empty_translation(self):
        for lang in LANGS:
            for key, val in load(lang).items():
                self.assertTrue(str(val).strip(), f"{lang} 空翻译：{key}")

    def test_index_loads_i18n_before_app(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/static/i18n.js"), html.index("/static/app.js"))
        self.assertIn('id="langSlot"', html)

    def test_engine_exposes_nine_languages(self):
        js = (STATIC / "i18n.js").read_text(encoding="utf-8")
        for lang in LANGS + ("zh-CN",):
            self.assertIn(f"id: '{lang}'", js)


if __name__ == "__main__":
    unittest.main()
