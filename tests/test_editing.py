"""剪辑相关纯函数测试（不需要 ffmpeg / 网络）。"""

import unittest

from videoforge.editing import (FILTER_PRESETS, TRANSITIONS, color_chain, overlaps,
                                timeline, transition_name, transition_xfade)
from videoforge.schemas import Shot


def shot(dur=6, transition="cut", td=0.0, **kw):
    s = Shot(id="S01", duration=dur, transition=transition, transition_duration=td)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class TestTransitions(unittest.TestCase):
    def test_registry_sane(self):
        self.assertIn("cut", TRANSITIONS)
        self.assertIn("dissolve", TRANSITIONS)
        self.assertGreaterEqual(len(TRANSITIONS), 10)
        for key, meta in TRANSITIONS.items():
            self.assertIn("label", meta)
            if key != "cut":
                self.assertIsNotNone(meta["xfade"])
            else:
                self.assertIsNone(meta["xfade"])

    def test_unknown_falls_back_to_cut(self):
        s = shot(transition="???")
        self.assertEqual(transition_name(s), "cut")
        self.assertIsNone(transition_xfade(s))

    def test_timeline_shrinks_on_overlap(self):
        shots = [shot(6), shot(6, "dissolve", 0.5), shot(6, "wipeleft", 0.5)]
        starts, total = timeline(shots, 0.4)
        self.assertEqual(starts[0], 0.0)
        self.assertAlmostEqual(starts[1], 5.5, places=3)
        self.assertAlmostEqual(starts[2], 11.0, places=3)
        self.assertAlmostEqual(total, 17.0, places=3)

    def test_overlap_clamped_for_short_shots(self):
        shots = [shot(1), shot(1, "dissolve", 2.0)]
        ov = overlaps(shots, 0.4)
        self.assertLessEqual(ov[0], 0.5)   # 最多吃掉单条镜头一半

    def test_no_overlap_for_cuts(self):
        shots = [shot(5), shot(5), shot(5)]
        _, total = timeline(shots, 0.4)
        self.assertEqual(total, 15.0)


class TestColor(unittest.TestCase):
    def test_preset_chain(self):
        s = shot(filter_preset="bw")
        chain = color_chain(s)
        self.assertTrue(any("hue=s=0" in c for c in chain))

    def test_numeric_adjustments(self):
        s = shot(brightness=0.1, contrast=1.2, saturation=0.8, temperature=0.5)
        chain = " ".join(color_chain(s))
        self.assertIn("brightness=0.100", chain)
        self.assertIn("contrast=1.200", chain)
        self.assertIn("saturation=0.800", chain)
        self.assertIn("colorbalance", chain)

    def test_neutral_shot_has_empty_chain(self):
        self.assertEqual(color_chain(shot()), [])

    def test_unknown_preset_is_ignored(self):
        s = shot(filter_preset="not-a-preset")
        self.assertEqual(color_chain(s), [])


class TestPresets(unittest.TestCase):
    def test_filter_presets_have_labels(self):
        for key, meta in FILTER_PRESETS.items():
            self.assertTrue(meta["label"], key)
            self.assertIsInstance(meta["chain"], list)


if __name__ == "__main__":
    unittest.main()
