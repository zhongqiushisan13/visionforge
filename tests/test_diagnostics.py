"""诊断包：脱敏规则与包内容。"""

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from videoforge import diagnostics as D
from videoforge.config import Settings


class TestMask(unittest.TestCase):
    def test_short_value_fully_masked(self):
        self.assertEqual(D.mask_value("abc123"), "******")

    def test_long_value_keeps_edges(self):
        masked = D.mask_value("sk-1234567890abcdef")
        self.assertTrue(masked.startswith("sk-1"))
        self.assertTrue(masked.endswith("cdef"))
        self.assertNotIn("567890", masked)

    def test_secret_name_detection(self):
        self.assertTrue(D.is_secret_name("LLM_API_KEY"))
        self.assertTrue(D.is_secret_name("MINIMAX_TOKEN"))
        self.assertFalse(D.is_secret_name("VF_WORKERS"))


class TestScrub(unittest.TestCase):
    def test_removes_exact_secret(self):
        text = "请求失败：key=sk-live-abcdefgh12345678 被拒绝"
        out = D.scrub_text(text, ["sk-live-abcdefgh12345678"])
        self.assertNotIn("abcdefgh12345678", out)
        self.assertIn("被拒绝", out)

    def test_strips_sk_pattern_even_if_unknown(self):
        out = D.scrub_text("Authorization: Bearer abcdefgh12345678", [])
        self.assertNotIn("abcdefgh12345678", out)

    def test_replaces_home_dir(self):
        home = str(Path.home())
        out = D.scrub_text(f"日志在 {home}\\logs\\runtime.log", [])
        self.assertNotIn(home, out)
        self.assertIn("%USERPROFILE%", out)

    def test_strips_query_token(self):
        out = D.scrub_text("GET /media/x.mp4?token=secretvalue123", [])
        self.assertNotIn("secretvalue123", out)


class TestBundle(unittest.TestCase):
    def _bundle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj = root / "projects" / "demo"
            proj.mkdir(parents=True)
            (proj / "project.json").write_text(json.dumps({
                "slug": "demo",
                "title": "不可外传的片名",
                "created_at": "2026-01-01T00:00:00",
                "shots": [{"id": "S01", "duration": 5, "status": "failed",
                           "error": "401 unauthorized: sk-leaked-key-123456"}],
            }, ensure_ascii=False), encoding="utf-8")

            settings = Settings()
            settings.deepseek.api_key = "sk-my-real-key-abcdef123456"
            settings.minimax.api_key = "mm-secret-9876543210"
            files = D.collect(settings=settings, projects_root=root / "projects",
                              doctor={"ffmpeg": {"ok": True}})
            blob = b"".join(files.values()).decode("utf-8")
            return files, blob

    def test_required_entries(self):
        files, _ = self._bundle()
        for name in ("meta.json", "settings.json", "doctor.json", "projects.json", "README.txt"):
            self.assertIn(name, files)

    def test_no_secret_leaks(self):
        _, blob = self._bundle()
        for secret in ("sk-my-real-key-abcdef123456", "mm-secret-9876543210",
                       "sk-leaked-key-123456"):
            self.assertNotIn(secret, blob)

    def test_project_titles_and_dialogue_excluded(self):
        files, blob = self._bundle()
        self.assertNotIn("不可外传的片名", blob)
        index = json.loads(files["projects.json"])
        self.assertNotIn("title", index[0])          # 标题属于内容，不诊断
        self.assertEqual(index[0]["slug"], "demo")   # slug 与统计要留

    def test_project_index_keeps_stats_drops_text(self):
        files, _ = self._bundle()
        index = json.loads(files["projects.json"])
        self.assertEqual(index[0]["slug"], "demo")
        self.assertEqual(index[0]["shots"], 1)
        self.assertEqual(index[0]["status"], {"failed": 1})
        self.assertEqual(index[0]["failures"], ["401 unauthorized: sk-***"])

    def test_zip_is_readable(self):
        files, _ = self._bundle()
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "diag.zip"
            D.write_bundle(dest, settings=None, projects_root=Path(td) / "nope")
            with zipfile.ZipFile(dest) as zf:
                names = set(zf.namelist())
                self.assertIn("README.txt", names)
                self.assertIsNone(zf.testzip())
        self.assertTrue(files)


if __name__ == "__main__":
    unittest.main()
