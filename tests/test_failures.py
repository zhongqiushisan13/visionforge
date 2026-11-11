"""失败归因：分类规则与汇总结构。"""

import unittest
from dataclasses import dataclass

from videoforge.failures import classify, failed_shots, summarize


@dataclass
class FakeShot:
    id: str
    status: str = "failed"
    error: str = ""


class TestClassify(unittest.TestCase):
    def test_known_kinds(self):
        cases = {
            "Minimax 鉴权失败（HTTP 401）": "auth",
            "429 too many requests": "rate",
            "余额不足，请充值": "quota",
            "CUDA out of memory": "vram",
            "提示词包含敏感内容 (1026)": "safety",
            "model not found": "model",
            "任务超时 timeout": "timeout",
            "clip: 未产出文件": "empty",
            "说不清的错误": "other",
        }
        for text, want in cases.items():
            self.assertEqual(classify(text), want, text)

    def test_oom_word_boundary(self):
        """boom 里含 oom，但不能算显存不足。"""
        self.assertEqual(classify("something went boom"), "other")

    def test_auth_beats_timeout(self):
        """错误串同时含 401 与 timeout 时，优先按鉴权处理。"""
        self.assertEqual(classify("401 鉴权失败，请求 timeout"), "auth")


class TestSummarize(unittest.TestCase):
    def test_counts_and_order(self):
        shots = [
            FakeShot("S01", "failed", "401 unauthorized"),
            FakeShot("S02", "failed", "429 rate limit"),
            FakeShot("S03", "failed", "401 invalid api key"),
            FakeShot("S04", "rendered", ""),
            FakeShot("S05", "pending", ""),
        ]
        out = summarize(shots)
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["kinds"][0]["kind"], "auth")
        self.assertEqual(out["kinds"][0]["count"], 2)
        self.assertEqual(out["kinds"][0]["samples"], ["S01", "S03"])

    def test_empty_when_no_failure(self):
        out = summarize([FakeShot("S01", "rendered")])
        self.assertEqual(out, {"total": 0, "kinds": []})

    def test_samples_capped(self):
        shots = [FakeShot(f"S{i:02d}", "failed", "timeout") for i in range(1, 6)]
        out = summarize(shots, limit_samples=2)
        self.assertEqual(len(out["kinds"][0]["samples"]), 2)


class TestFailedShots(unittest.TestCase):
    def test_filter(self):
        shots = [FakeShot("S01", "failed"), FakeShot("S02", "rendered")]
        self.assertEqual([s.id for s in failed_shots(shots)], ["S01"])


if __name__ == "__main__":
    unittest.main()
