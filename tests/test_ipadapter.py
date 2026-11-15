"""IP-Adapter 注入：有参考图接上、没参考图旁路、无节点工作流不受影响。"""

import json
import tempfile
import unittest
from pathlib import Path

from videoforge.config import Settings
from videoforge.providers.comfyui import ComfyUIProvider
from videoforge.schemas import Shot


class IPAdapterTest(unittest.TestCase):
    def setUp(self):
        self.s = Settings()
        self.s.comfy.workflow = "sdxl_ipadapter.json"
        self.s.comfy.ipadapter_weight = 0.85
        self.prov = ComfyUIProvider(self.s)
        self.prov.upload_image = lambda p: "uploaded.png"      # 不走网络
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-ipa-"))
        self.ref = self.tmp / "ref.png"
        self.ref.write_bytes(b"\x89PNG" + b"0" * 100)

    def test_reference_is_wired(self):
        wf, _ = self.prov.build_prompt(Shot(id="S01", image_prompt="a woman"), ref_image=self.ref)
        sampler = next(n for n in wf.values() if n.get("class_type") == "KSampler")
        ipa = next(n for n in wf.values() if n.get("class_type") == "IPAdapterAdvanced")
        self.assertEqual(sampler["inputs"]["model"], [next(k for k, v in wf.items() if v is ipa), 0])
        self.assertEqual(ipa["inputs"]["weight"], 0.85)
        load = next(n for n in wf.values() if n.get("class_type") == "LoadImage")
        self.assertEqual(load["inputs"]["image"], "uploaded.png")

    def test_without_reference_bypasses_ipadapter(self):
        wf, _ = self.prov.build_prompt(Shot(id="S01", image_prompt="a woman"))
        sampler = next(n for n in wf.values() if n.get("class_type") == "KSampler")
        model_src = sampler["inputs"]["model"][0]
        self.assertEqual(wf[model_src]["class_type"], "CheckpointLoaderSimple")

    def test_plain_workflow_ignores_reference(self):
        self.s.comfy.workflow = "sdxl_txt2img.json"
        prov = ComfyUIProvider(self.s)
        prov.upload_image = lambda p: "uploaded.png"
        wf, _ = prov.build_prompt(Shot(id="S01", image_prompt="x"), ref_image=self.ref)
        self.assertFalse(any(n.get("class_type") == "IPAdapterAdvanced" for n in wf.values()))

    def test_workflow_json_is_valid(self):
        path = self.s.workflows_dir / "sdxl_ipadapter.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("_vf", data)
        classes = {n.get("class_type") for n in data.values() if isinstance(n, dict)}
        self.assertIn("IPAdapterAdvanced", classes)
        self.assertIn("SaveImage", classes)


if __name__ == "__main__":
    unittest.main()
