"""系列素材库：增删改、提示词拼装、末帧接戏。"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import videoforge.series as series_mod
from videoforge.config import Settings
from videoforge.pipeline.director import Director
from videoforge.schemas import Project, Shot


class SeriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-series-"))
        self._old_root = series_mod.series_root
        series_mod.series_root = lambda: self.tmp / "series"
        self.data = series_mod.create_series("雨夜短剧", style_prompt="cinematic teal")

    def tearDown(self):
        series_mod.series_root = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_list(self):
        rows = series_mod.list_series()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "雨夜短剧")
        self.assertEqual(rows[0]["characters"], 0)

    def test_add_and_update_asset(self):
        a = series_mod.add_asset(self.data["slug"], "characters", "林夏", look_en="26yo woman, short black hair")
        self.assertTrue(a["id"])
        self.assertIn("look_en", a)
        updated = series_mod.update_asset(self.data["slug"], "characters", a["id"], {"name": "林夏（主）"})
        self.assertEqual(updated["name"], "林夏（主）")
        self.assertEqual(len(series_mod.load_series(self.data["slug"])["characters"]), 1)

    def test_delete_asset_removes_dir(self):
        a = series_mod.add_asset(self.data["slug"], "props", "手机")
        d = series_mod.asset_dir(self.data["slug"], "props", a["id"])
        self.assertTrue(d.exists())
        series_mod.delete_asset(self.data["slug"], "props", a["id"])
        self.assertFalse(d.exists())
        self.assertEqual(series_mod.load_series(self.data["slug"])["props"], [])

    def test_set_image_and_relative_path(self):
        a = series_mod.add_asset(self.data["slug"], "characters", "程野", look_en="male, raincoat")
        rel = series_mod.set_asset_image(self.data["slug"], "characters", a["id"], "main", b"x" * 2048)
        self.assertTrue(rel.startswith("characters/"))
        self.assertTrue((series_mod.series_dir(self.data["slug"]) / rel).exists())
        saved = series_mod.load_series(self.data["slug"])["characters"][0]
        self.assertEqual(saved["images"]["main"], rel)

    def test_compose_prompt(self):
        a = series_mod.add_asset(self.data["slug"], "characters", "林夏", look_en="short black hair")
        s = series_mod.add_asset(self.data["slug"], "scenes", "客厅", desc_en="small flat, warm lamp")
        shot = Shot(id="S01", cast=[a["id"]], scene_ref=s["id"])
        prompt = series_mod.compose_prompt(series_mod.load_series(self.data["slug"]), shot)
        self.assertIn("short black hair", prompt)
        self.assertIn("warm lamp", prompt)
        self.assertIn("character", prompt)

    def test_compose_prompt_empty_without_refs(self):
        self.assertEqual(series_mod.compose_prompt(series_mod.load_series(self.data["slug"]), Shot()), "")


class DirectorSeriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-dir-"))
        self._old_root = series_mod.series_root
        series_mod.series_root = lambda: self.tmp / "series"
        self.series = series_mod.create_series("测试系列")
        self.char = series_mod.add_asset(self.series["slug"], "characters", "林夏",
                                         look_en="short black hair, beige coat")
        proj_dir = self.tmp / "projects" / "p1"
        self.project = Project.create(proj_dir, title="第一集", brief="b", slug="p1")
        self.project.series_id = self.series["slug"]
        self.project.shots = [Shot(id="S01", duration=4, image_prompt="rainy alley", cast=[self.char["id"]])]
        self.project.save()
        self.settings = Settings()
        self.settings.pipeline.mock = True

    def tearDown(self):
        series_mod.series_root = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keyframe_prompt_includes_character(self):
        captured = {}

        def fake_keyframe(shot, out):
            captured["prompt"] = shot.image_prompt
            Path(out).write_bytes(b"png")
            from videoforge.providers.base import ProviderResult
            return ProviderResult(path=str(out), provider="fake", task_id="t")

        d = Director(self.settings)
        with mock.patch.object(d.providers, "keyframe", side_effect=fake_keyframe):
            d.keyframes(self.project)
        self.assertIn("rainy alley", captured["prompt"])
        self.assertIn("short black hair", captured["prompt"])

    def test_link_prev_uses_last_frame(self):
        clips = self.project.path("clips")
        clips.mkdir(parents=True, exist_ok=True)
        prev_clip = clips / "S00.mp4"
        prev_clip.write_bytes(b"x" * 4096)
        self.project.shots.insert(0, Shot(id="S00", duration=3, clip=str(prev_clip)))
        self.project.shots[1].link_prev = True
        self.project.save()

        seen = {}

        def fake_clip(shot, out, keyframe=None):
            seen[shot.id] = keyframe
            Path(out).write_bytes(b"mp4")
            from videoforge.providers.base import ProviderResult
            return ProviderResult(path=str(out), provider="fake", task_id="t", duration=3.0)

        d = Director(self.settings)
        with mock.patch.object(d.providers, "clip", side_effect=fake_clip), \
                mock.patch("videoforge.pipeline.director.extract_last_frame",
                           side_effect=lambda clip, out, **kw: (Path(out).write_bytes(b"png") or True)):
            d.clips(self.project)
        self.assertIn("S01", seen)
        self.assertTrue(str(seen["S01"]).endswith("S01_prev.png"))


if __name__ == "__main__":
    unittest.main()
