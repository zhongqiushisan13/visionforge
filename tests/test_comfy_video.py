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
    s.comfy.width, s.comfy.height = 1344, 768      # 关键帧出图尺寸，不该污染视频
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

    def test_size_kept_from_workflow_by_default(self):
        """没配 MINIMAX_VIDEO_WIDTH 就沿用工作流分辨率，别拿关键帧的 1344x768 去顶。"""
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(Shot(id="S01", duration=5), None)
        self.assertEqual((wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]), (1024, 576))

    def test_size_overridden_when_configured(self):
        p = make_provider(H3_WORKFLOW)
        p.video_cfg.video_width, p.video_cfg.video_height = 608, 352
        wf, _ = p.patch_workflow(Shot(id="S01", duration=5), None)
        self.assertEqual((wf["6"]["inputs"]["width"], wf["6"]["inputs"]["height"]), (608, 352))

    def test_frame_count_aligned_to_h3_grid(self):
        """H3 帧数必须是 4n+1：5s×24fps=120 → 121，3s=72 → 73。"""
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(Shot(id="S01", duration=5), None)
        self.assertEqual(wf["6"]["inputs"]["length"], 121)
        self.assertEqual((wf["6"]["inputs"]["length"] - 1) % 4, 0)
        wf3, _ = make_provider(H3_WORKFLOW).patch_workflow(Shot(id="S02", duration=3), None)
        self.assertEqual(wf3["6"]["inputs"]["length"], 73)

    def test_frame_count_capped_at_h3_ceiling(self):
        wf, _ = make_provider(H3_WORKFLOW).patch_workflow(Shot(id="S01", duration=30), None)
        self.assertEqual(wf["6"]["inputs"]["length"], 361)      # 15s × 24fps，H3 上限

    def test_scheduler_steps_overridable(self):
        wf_with_sched = dict(H3_WORKFLOW)
        wf_with_sched["9"] = {"class_type": "BasicScheduler",
                              "inputs": {"scheduler": "simple", "steps": 4, "denoise": 1.0}}
        wf, _ = make_provider(wf_with_sched).patch_workflow(Shot(id="S01", duration=5), None)
        self.assertEqual(wf["9"]["inputs"]["steps"], 4)          # 默认不动 turbo 的 4 步
        p = make_provider(wf_with_sched)
        p.video_cfg.video_steps = 6
        wf6, _ = p.patch_workflow(Shot(id="S01", duration=5), None)
        self.assertEqual(wf6["9"]["inputs"]["steps"], 6)

    def test_t2v_mode_drops_first_frame(self):
        p = make_provider(H3_WORKFLOW)
        p.video_cfg.local_image_mode = "skip"
        wf, _ = p.patch_workflow(Shot(id="S01", duration=5), None)
        self.assertNotIn("first_frame", wf["6"]["inputs"])

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
