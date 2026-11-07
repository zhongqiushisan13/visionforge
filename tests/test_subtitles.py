"""字幕/文字层测试：时间轴、折行、转场处截断。"""

import unittest

from videoforge.config import RenderConfig
from videoforge.pipeline.subtitles import build_ass, build_title_ass, wrap_text
from videoforge.schemas import Shot

CFG = RenderConfig(width=1920, height=1080, subtitle_size=54)


def shots():
    return [
        Shot(id="S01", duration=6, subtitle="第一句字幕"),
        Shot(id="S02", duration=5, subtitle="第二句字幕", audio_duration=4.0,
             title_text="雨停之前", title_position="top"),
    ]


class TestAss(unittest.TestCase):
    def test_cumulative_timeline(self):
        ass = build_ass(shots(), CFG)
        self.assertIn("0:00:00.12,0:00:05.92", ass)
        self.assertIn("0:00:06.12,0:00:10.92", ass)

    def test_explicit_starts(self):
        ass = build_ass(shots(), CFG, starts=[0.0, 5.5], durations=[6.0, 5.0])
        self.assertIn("0:00:05.62", ass)

    def test_next_shot_caps_previous_subtitle(self):
        ass = build_ass(shots(), CFG, starts=[0.0, 3.0], durations=[6.0, 5.0])
        # 第一条应在 3.0 - 0.05 之前结束，避免转场时两条字幕同屏
        self.assertNotIn("0:00:05.92", ass)
        self.assertIn("0:00:02.95", ass)

    def test_wrap_chinese(self):
        text = wrap_text("这是一句很长的中文字幕用来测试自动折行效果")
        self.assertIn("\\N", text)

    def test_wrap_escapes_braces(self):
        self.assertNotIn("{", wrap_text("危险{样式}注入"))

    def test_title_layer_style_by_position(self):
        ass = build_title_ass(shots(), CFG, durations=[6, 5])
        self.assertIn("VF_TITLE_TOP", ass)
        self.assertIn("雨停之前", ass)

    def test_title_size_override(self):
        s = shots()
        s[1].title_size = 88
        ass = build_title_ass(s, CFG, durations=[6, 5])
        self.assertIn("{\\fs88}", ass)

    def test_empty_subtitle_no_event(self):
        s = [Shot(id="S01", duration=4, subtitle="")]
        ass = build_ass(s, CFG)
        self.assertNotIn("Dialogue: 0,", ass)


if __name__ == "__main__":
    unittest.main()
