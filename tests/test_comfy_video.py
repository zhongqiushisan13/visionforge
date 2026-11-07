"""ComfyUI 视频工作流补丁测试：提示词字段兼容 + 采样链种子。

覆盖两个真机踩到的问题：
1. MiniMax H3 的提示词字段叫 `prompt`，老代码只认 `text`，导致提示词永远为空；
2. `SamplerCustomAdvanced` 链路用 `RandomNoise.noise_seed`，不设种子就每镜一样。
"""

import unittest

from videoforge.config import Settings
from videoforge.providers.comfy_video import ComfyVideoProvider
from videoforge.schemas import Shot

H3_WORKFLOW = {
    "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "x.gguf"}},
    "5": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    "6": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": "",
                   "width": 1024, "height": 576, "length": 121, "first_frame": ["5", 0]},
        "_meta": {"title": "POSITIVE"},
    },
    "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
    "13": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "fps": 8}},
    "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0],
                                                 "filename_prefix": "video/vf",
                                                 "format": "auto", "codec": "auto"}},
}

SDXL_WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]},
          "_meta": {"title": "POSITIVE"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "vf"}},
}


def make_provider(workflow):
    s = Settings()
    s.comfy.width, s.comfy.height = 608, 352
    s.render.fps = 24
    s.minimax.video_workflow = "wf.json"
    p = ComfyVideoProvider(s)
    p.load_workflow = lambda name: workflow          # 不落盘
    return p


class TestComfyVideoPatch(unittest.TestCase):
    def test_prompt_written_to_h3_field(self):
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(
            Shot(id="S01", duration=5, video_prompt="a cat in rain"), None)
        self.assertEqual(wf["6"]["inputs"]["prompt"], "a cat in rain")

    def test_legacy_text_field_still_works(self):
        wf, _ = make_provider(SDXL_WORKFLOW).patch_workflow(
            Shot(id="S01", duration=5, image_prompt="legacy prompt"), None)
        self.assertEqual(wf["6"]["inputs"]["text"], "legacy prompt")

    def test_size_and_frame_count_patched(self):
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(
            Shot(id="S01", duration=5), None)
        self.assertEqual((wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]), (608, 352))
        self.assertEqual(wf["6"]["inputs"]["length"], 120)      # 5s × 24fps

    def test_random_noise_seed_injected(self):
        wf, seed = make_provider(H3_WORKFLOW).patch_workflow(
            Shot(id="S01", duration=5, seed=4242), None)
        self.assertEqual(seed, 4242)
        self.assertEqual(wf["7"]["inputs"]["noise_seed"], 4242)

    def test_save_node_fps_synced(self):
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(Shot(id="S01", duration=3), None)
        self.assertEqual(wf["13"]["inputs"]["fps"], 24)


if __name__ == "__main__":
    unittest.main()
