"""字幕表：时间轴对齐、空行处理、SRT 文本。"""

import unittest

from videoforge.editing import subtitle_rows
from videoforge.pipeline.subtitles import build_srt_text
from videoforge.schemas import Shot


def shots():
    return [
        Shot(id="S01", duration=6, subtitle="第一句", speaker="旁白"),
        Shot(id="S02", duration=5, transition="fade", transition_duration=0.4,
             subtitle="第二句"),
        Shot(id="S03", duration=4, dialogue="第三句（走台词兜底）", enabled=False),
        Shot(id="S04", duration=3),
    ]


class TestSubtitleRows(unittest.TestCase):
    def test_disabled_shots_excluded(self):
        rows, _ = subtitle_rows(shots())
        self.assertEqual([r["shot_id"] for r in rows], ["S01", "S02", "S04"])

    def test_fade_overlap_shortens_previous(self):
        rows, total = subtitle_rows(shots())
        # S02 有 0.4s 淡入淡出，S01 的结束应提前到 5.6 - 0.05
        self.assertAlmostEqual(rows[0]["end"], 5.55, places=2)
        self.assertAlmostEqual(rows[1]["start"], 5.6, places=2)
        self.assertAlmostEqual(total, 13.6, places=2)

    def test_empty_row_kept_for_editing(self):
        rows, _ = subtitle_rows(shots())
        self.assertTrue(rows[-1]["empty"])
        self.assertEqual(rows[-1]["text"], "")

    def test_dialogue_fallback(self):
        s = [Shot(id="S01", duration=3, dialogue="只有台词")]
        rows, _ = subtitle_rows(s)
        self.assertEqual(rows[0]["text"], "只有台词")
        self.assertFalse(rows[0]["empty"])

    def test_subtitle_overrides_dialogue(self):
        s = [Shot(id="S01", duration=3, dialogue="台词", subtitle="字幕")]
        rows, _ = subtitle_rows(s)
        self.assertEqual(rows[0]["text"], "字幕")


class TestSrtText(unittest.TestCase):
    def test_format_and_indices(self):
        rows, _ = subtitle_rows(shots())
        srt = build_srt_text(rows)
        self.assertTrue(srt.startswith("1\n00:00:00,000 --> 00:00:05,550"))
        self.assertIn("第二句", srt)
        self.assertIn("\n\n", srt)

    def test_empty_rows_skipped(self):
        srt = build_srt_text([
            {"start": 0, "end": 1, "text": ""},
            {"start": 1, "end": 2, "text": "有字"},
        ])
        self.assertEqual(srt.count("-->"), 1)
        self.assertTrue(srt.startswith("1\n00:00:01,000"))

    def test_all_empty_returns_empty_string(self):
        self.assertEqual(build_srt_text([{"start": 0, "end": 1, "text": "  "}]), "")

    def test_end_never_before_start(self):
        srt = build_srt_text([{"start": 3, "end": 3, "text": "x"}])
        self.assertNotIn("00:00:03,000 --> 00:00:03,000", srt)

    def test_crlf_text_cleaned(self):
        srt = build_srt_text([{"start": 0, "end": 1, "text": "a\r\nb"}])
        self.assertIn("a\nb", srt)
        self.assertNotIn("\r", srt)


if __name__ == "__main__":
    unittest.main()
