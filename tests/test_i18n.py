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

    def test_high_risk_strings_present_everywhere(self):
        """这些是曾经漏翻的：成片页右栏、分镜按钮、时间线工具条。"""
        must = ("成本小结", "估算，非账单", "已发生", "调用次数", "生成参数",
                "剧本：", "镜头：", "关键帧：", "工作流默认", "素材",
                "预览", "编辑", "重拍", "出关键帧", "导入素材",
                "用当前剪辑合成", "补渲缺失镜头", "下载 MP4", "上传 BGM")
        for lang in LANGS:
            data = load(lang)
            missing = [k for k in must if k not in data]
            self.assertFalse(missing, f"{lang} 缺少 {missing}")

    def test_arabic_keeps_ltr_shell(self):
        """阿拉伯语只让文本 RTL：整体 dir=ltr，时间线不反转。"""
        js = (STATIC / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("document.documentElement.dir = 'ltr'", js)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('[dir="rtl"] .tl-track', html)
        self.assertIn("html.lang-ar", html)


if __name__ == "__main__":
    unittest.main()
