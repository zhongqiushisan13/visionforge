"""设定图：角色三视图 / 场景板的生成、锁定与选角分配。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from videoforge import series as series_mod
from videoforge.pipeline.design import Designer
from videoforge.providers.base import ProviderResult
from videoforge.schemas import Character, Project, Scene, Script, Shot


class FakeComfy:
    """假装是本地 ComfyUI：只写一张占位图，记录调用参数。"""

    def __init__(self):
        self.cfg = SimpleNamespace(width=1024, height=1024)
        self.calls = []

    def generate_keyframe(self, shot, out, ref_image=None):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + shot.id.encode("utf-8"))
        self.calls.append({
            "prompt": shot.image_prompt, "seed": shot.seed,
            "size": (self.cfg.width, self.cfg.height),
        })
        return ProviderResult(path=str(out), provider="fake", seed=shot.seed)


class DesignerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patches = [
            mock.patch.object(series_mod, "series_root", lambda: self.root / "series"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.comfy = FakeComfy()
        self.providers = SimpleNamespace(comfy=self.comfy)

    def _project(self):
        p = Project.create(self.root / "proj", title="雨夜便利店", brief="b", slug="p",
                           aspect="16:9")
        p.script = Script(
            title="雨夜便利店",
            characters=[
                Character(name="林夏", look="短发米色风衣", look_en="short black hair, beige trench coat"),
                Character(name="店员", look="绿围裙", look_en="young man, green apron"),
            ],
            scenes=[Scene(name="便利店", look="暖光货架", look_en="warm interior, drink shelves")],
        )
        p.shots = [
            Shot(id="S01", index=1, duration=5, scene="便利店门口",
                 description="林夏走进便利店"),
            Shot(id="S02", index=2, duration=5, scene="便利店货架",
                 description="店员抬头看她"),
        ]
        return p

    def test_generates_and_locks_assets(self):
        p = self._project()
        stats = Designer(self.providers).run(p)
        self.assertEqual(stats["characters"], 2)
        self.assertEqual(stats["scenes"], 1)
        data = series_mod.load_series(p.series_id)
        chars = data["characters"]
        self.assertEqual({c["name"] for c in chars}, {"林夏", "店员"})
        for c in chars:
            self.assertTrue(c["images"]["main"].endswith("main.png"))
            self.assertTrue(c["locked"])
            self.assertGreater(c["seed"], 0)
        scene = data["scenes"][0]
        self.assertTrue(scene["images"]["wide"].endswith("wide.png"))
        # 角色 3:4、场景 16:9
        self.assertIn((704, 1152), [c["size"] for c in self.comfy.calls])
        self.assertIn((1216, 704), [c["size"] for c in self.comfy.calls])

    def test_second_run_skips_locked(self):
        p = self._project()
        first = Designer(self.providers).run(p)
        self.assertEqual(sum(first.values()), 3)  # 2 角色 + 1 场景，角色各 3 张视图
        again = Designer(self.providers).run(p)
        self.assertEqual(again["characters"], 0)
        self.assertEqual(again["scenes"], 0)
        self.assertGreaterEqual(again["skipped"], 3)
        self.assertEqual(len(self.comfy.calls), 7)   # 2×3 视图 + 1 场景

    def test_force_regenerates(self):
        p = self._project()
        Designer(self.providers).run(p)
        again = Designer(self.providers).run(p, force=True)
        self.assertEqual(again["characters"], 2)
        self.assertEqual(len(self.comfy.calls), 14)

    def test_assign_casts_by_name(self):
        p = self._project()
        Designer(self.providers).run(p)
        data = series_mod.load_series(p.series_id)
        by_name = {c["name"]: c["id"] for c in data["characters"]}
        self.assertEqual(p.shots[0].cast, [by_name["林夏"]])
        self.assertEqual(p.shots[1].cast, [by_name["店员"]])
        self.assertTrue(p.shots[0].scene_ref)
        self.assertTrue(p.shots[1].scene_ref)

    def test_missing_comfy_raises(self):
        p = self._project()
        with self.assertRaises(RuntimeError):
            Designer(SimpleNamespace(comfy=None)).run(p)


if __name__ == "__main__":
    unittest.main()
