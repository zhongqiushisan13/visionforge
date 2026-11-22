"""本地出图环境：目录校验、状态判定、导入校验。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from videoforge import setup_env
from videoforge.config import Settings


class FakeProvider:
    def __init__(self, info=None, fail=False):
        self._ok = not fail
        self._info_data = info or {}

    def verify(self):
        if not self._ok:
            raise RuntimeError("连不上")
        return {"system": {"comfyui_version": "0.44.0"}}

    def _info(self):
        return self._info_data


class CatalogTest(unittest.TestCase):
    def test_every_item_has_required_fields(self):
        for item_id, meta in setup_env.CATALOG.items():
            self.assertIn(meta["kind"], ("model", "zip"), item_id)
            self.assertGreater(meta["size"], 0, item_id)
            self.assertTrue(meta["dest"], item_id)
            self.assertGreaterEqual(len(meta["urls"]), 2, item_id)   # 官方 + 镜像
            if meta["sha256"]:
                self.assertEqual(len(meta["sha256"]), 64, item_id)

    def test_standard_tier_covers_sdxl_stack(self):
        self.assertIn("sdxl-base", setup_env.STANDARD)
        self.assertIn("ipa-node", setup_env.STANDARD)


class RootTest(unittest.TestCase):
    def test_accepts_dir_with_models(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "models").mkdir()
            s = Settings()
            info = setup_env.set_root(s, td)
            self.assertEqual(s.comfy.root, info["root"])

    def test_unwraps_nested_comfyui_dir(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ComfyUI" / "models").mkdir(parents=True)
            s = Settings()
            info = setup_env.set_root(s, td)
            self.assertTrue(info["root"].endswith("ComfyUI"))

    def test_rejects_dir_without_models(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                setup_env.set_root(Settings(), td)


class StatusTest(unittest.TestCase):
    def test_ready_flags_from_object_info(self):
        info = {
            "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd_xl_base_1.0_0.9vae.safetensors"]]}}},
            "CLIPVisionLoader": {"input": {"required": {"clip_name": [["CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"]]}}},
            "IPAdapterModelLoader": {"input": {"required": {"ipadapter_file": [["ip-adapter-plus_sdxl_vit-h.safetensors"]]}}},
            "IPAdapterAdvanced": {},
        }
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "models").mkdir()
            s = Settings()
            s.comfy.root = td
            st = setup_env.status(s, provider=FakeProvider(info))
            self.assertTrue(st["comfy"]["reachable"])
            self.assertTrue(st["ready"]["standard"])
            self.assertTrue(st["root"]["valid"])
            self.assertGreater(st["disk_free"], 0)

    def test_offline_reports_missing(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "models").mkdir()
            s = Settings()
            s.comfy.root = td
            st = setup_env.status(s, provider=FakeProvider(fail=True))
            self.assertFalse(st["comfy"]["reachable"])
            self.assertFalse(st["ready"]["standard"])
            self.assertIn("连不上", st["comfy"]["error"])


class VerifyTest(unittest.TestCase):
    def test_size_and_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"hello world" * 100)
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            meta = {"size": p.stat().st_size, "sha256": digest}
            self.assertTrue(setup_env.verify_file(p, meta))
            self.assertFalse(setup_env.verify_file(p, {"size": 1, "sha256": digest}))
            self.assertFalse(setup_env.verify_file(p, {"size": p.stat().st_size, "sha256": "0" * 64}))

    def test_import_local_copies_file(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "model.safetensors"
            src.write_bytes(b"\x00" * 5000)
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            root = Path(td) / "comfy"
            (root / "models").mkdir(parents=True)
            item = "test-item"
            setup_env.CATALOG[item] = {
                "label": "测试模型", "tier": "standard", "kind": "model",
                "size": 5000, "sha256": digest, "dest": "models/ipadapter/x.safetensors",
                "probe": ("ipadapter", "x.safetensors"), "urls": ["http://a", "http://b"],
            }
            try:
                dest = setup_env.import_local(item, root, src)
                self.assertTrue(dest.exists())
                self.assertEqual(dest.read_bytes(), src.read_bytes())
            finally:
                setup_env.CATALOG.pop(item, None)

    def test_import_rejects_wrong_file(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bad.safetensors"
            src.write_bytes(b"not the model")
            root = Path(td) / "comfy"
            (root / "models").mkdir(parents=True)
            with self.assertRaises(ValueError):
                setup_env.import_local("sdxl-base", root, src)


if __name__ == "__main__":
    unittest.main()


class ManualControlTest(unittest.TestCase):
    """手动控制 ComfyUI：界面地址、主目录识别、缺目录时的报错。"""

    def _settings(self, root=""):
        s = Settings()
        s.comfy.root = root
        return s

    def test_ui_url_strips_slash(self):
        s = self._settings()
        s.comfy.base_url = "http://127.0.0.1:8188/"
        self.assertEqual(setup_env.ui_url(s), "http://127.0.0.1:8188")

    def test_port_parsing(self):
        s = self._settings()
        s.comfy.base_url = "http://127.0.0.1:9999"
        self.assertEqual(setup_env._port_of(s), 9999)

    def test_find_main_in_root_and_parent(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "main.py").write_text("", encoding="utf-8")
            self.assertEqual(setup_env.comfy_main(str(base)), base / "main.py")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            inner = base / "ComfyUI"
            inner.mkdir()
            (inner / "main.py").write_text("", encoding="utf-8")
            self.assertEqual(setup_env.comfy_main(str(base)), inner / "main.py")

    def test_launch_requires_root(self):
        s = self._settings("")
        s.comfy.base_url = "http://127.0.0.1:59999"      # 指到没人听的端口，避免本机 ComfyUI 干扰
        with self.assertRaises(ValueError):
            setup_env.launch(s)

    def test_workflows_in_status(self):
        s = self._settings()
        s.comfy.workflow = "sdxl_ipadapter.json"
        s.minimax.video_workflow = "minimax_h3_gguf_i2v.json"
        info = setup_env.status(s, provider=FakeProvider(fail=True))
        self.assertEqual(info["workflows"]["image"], "sdxl_ipadapter.json")
        self.assertEqual(info["workflows"]["video"], "minimax_h3_gguf_i2v.json")
        self.assertTrue(info["workflows"]["dir"])
