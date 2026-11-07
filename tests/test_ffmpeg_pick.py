"""ffmpeg 选择逻辑测试：打包版优先自带、自检不过就换、VF_FFMPEG 可强制指定。

背景：真机上出现过「PATH 里的 ffmpeg 在并发出片时崩溃」的情况，
事后无法稳定复现，所以策略改成「打包版优先用自带的」，并把自检留下兜底。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import videoforge.utils as u


def touch(dirname, name):
    p = Path(dirname) / name
    p.write_text("", encoding="utf-8")
    return str(p)


class TestFindFfmpeg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bundled = touch(self.tmp, "ffmpeg-bundled.exe")
        self.system = touch(self.tmp, "ffmpeg.exe")
        self.real_probe = u._probe_ffmpeg          # 打补丁前留一份真函数
        u._FFMPEG_CACHE = None
        u._FFMPEG_OK.clear()

        fake = types.ModuleType("imageio_ffmpeg")
        fake.get_ffmpeg_exe = lambda: self.bundled
        self._patches = [
            mock.patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
            mock.patch.object(u.shutil, "which", return_value=self.system),
            mock.patch.object(u, "_probe_ffmpeg", return_value=True),
            mock.patch.dict("os.environ", {"VF_FFMPEG": ""}, clear=False),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        u._FFMPEG_CACHE = None
        u._FFMPEG_OK.clear()

    def test_frozen_prefers_bundled(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(u.find_ffmpeg(), self.bundled)

    def test_source_run_prefers_system(self):
        self.assertEqual(u.find_ffmpeg(), self.system)

    def test_env_override_wins(self):
        with mock.patch.dict("os.environ", {"VF_FFMPEG": self.bundled}):
            u._FFMPEG_CACHE = None
            self.assertEqual(u.find_ffmpeg(), self.bundled)

    def test_probe_failure_falls_back(self):
        # 系统那条自检不过 → 换自带的
        with mock.patch.object(u, "_probe_ffmpeg", side_effect=lambda p: p != self.system):
            self.assertEqual(u.find_ffmpeg(), self.bundled)

    def test_probe_on_missing_binary_returns_false(self):
        u._FFMPEG_OK.clear()
        self.assertFalse(self.real_probe(str(Path(self.tmp) / "nope.exe")))


if __name__ == "__main__":
    unittest.main()
