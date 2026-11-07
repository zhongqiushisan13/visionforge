"""自然语言剪辑指令解析测试（不依赖 DeepSeek）。"""

import unittest

from videoforge.edit_commands import apply_ops, interpret
from videoforge.schemas import Project, Shot


def project(n=4):
    return Project(slug="t", shots=[Shot(id=f"S{i:02d}", duration=6) for i in range(1, n + 1)])


class TestLocalParser(unittest.TestCase):
    def test_global_filter(self):
        p = project()
        r = interpret("全部镜头调成黑白", p, llm=None)
        self.assertTrue(r["ops"])
        apply_ops(p, r["ops"])
        self.assertTrue(all(s.filter_preset == "bw" for s in p.shots))

    def test_specific_shot_speed(self):
        p = project()
        r = interpret("全部调成电影感，第2镜1.5倍速", p, llm=None)
        apply_ops(p, r["ops"])
        self.assertAlmostEqual(p.shots[1].speed, 1.5)
        self.assertAlmostEqual(p.shots[0].speed, 1.0)
        self.assertEqual(p.shots[0].filter_preset, "cinematic")

    def test_transition_keyword(self):
        p = project()
        apply_ops(p, interpret("所有镜头加叠化转场", p)["ops"])
        self.assertEqual(p.shots[0].transition, "dissolve")
        self.assertGreater(p.shots[0].transition_duration, 0)

    def test_volume_and_mute(self):
        p = project()
        apply_ops(p, interpret("第1镜音量调到50%", p)["ops"])
        self.assertAlmostEqual(p.shots[0].volume, 0.5)
        apply_ops(p, interpret("全部静音", p)["ops"])
        self.assertTrue(all(s.volume == 0 for s in p.shots))

    def test_mirror_and_reverse(self):
        p = project()
        apply_ops(p, interpret("第3镜镜像并且倒放", p)["ops"])
        self.assertTrue(p.shots[2].mirror)
        self.assertTrue(p.shots[2].reverse)

    def test_unknown_text(self):
        r = interpret("今天天气不错", project())
        self.assertEqual(r["ops"], [])

    def test_clamps_out_of_range(self):
        p = project()
        apply_ops(p, [{"target": "all", "set": {"speed": 99, "contrast": -3,
                                                "filter_preset": "nope"}}])
        s = p.shots[0]
        self.assertEqual(s.speed, 4.0)
        self.assertEqual(s.contrast, 0.5)
        self.assertEqual(s.filter_preset, "none")

    def test_even_and_first_targets(self):
        p = project(4)
        apply_ops(p, [{"target": "first", "set": {"title_text": "开场"}}])
        self.assertEqual(p.shots[0].title_text, "开场")
        self.assertEqual(p.shots[1].title_text, "")
        apply_ops(p, [{"target": "even", "set": {"mirror": True}}])
        self.assertTrue(p.shots[1].mirror)
        self.assertTrue(p.shots[3].mirror)


if __name__ == "__main__":
    unittest.main()
