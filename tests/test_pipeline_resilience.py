"""流水线韧性测试：断点续跑、任务状态流转、Provider 降级链。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from videoforge.config import MiniMaxConfig, Settings
from videoforge.pipeline.director import Director
from videoforge.providers import Providers, ProviderError, ProviderResult
from videoforge.providers.minimax import MiniMaxProvider
from videoforge.providers.tts import VoiceoverEngine
from videoforge.schemas import Project, Shot
from videoforge.web.jobs import JobManager

from tests.fixtures.mock_h3_server import MockH3Server


class ResumeTest(unittest.TestCase):
    def test_existing_clip_is_not_regenerated(self):
        """模拟「跑到一半杀进程再续」：已有 clips/S01.mp4，重跑只补 S02。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = Settings()
            s.root = root
            s.pipeline.projects_dir = str(root / "projects")
            s.pipeline.mock = True
            s.pipeline.max_workers = 1
            project = Project.create(root / "projects" / "demo", title="demo",
                                     brief="brief", slug="demo", mock=True)
            project.shots = [
                Shot(id="S01", index=1, duration=4, video_prompt="old"),
                Shot(id="S02", index=2, duration=4, video_prompt="new"),
            ]
            old_clip = project.path("clips") / "S01.mp4"
            old_clip.write_bytes(b"already-rendered")
            project.shots[0].clip = str(old_clip)
            project.save()

            director = Director(s)
            calls: list[str] = []

            def fake_clip(shot: Shot, out_path: Path, keyframe=None):
                calls.append(shot.id)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"new-render")
                return ProviderResult(path=str(out_path), provider="mock", duration=4.0)

            director.providers.clip = fake_clip
            stats = director.clips(project, force=False)
            self.assertEqual(calls, ["S02"])
            self.assertEqual(stats["ok"], 1)
            self.assertEqual(old_clip.read_bytes(), b"already-rendered")
            self.assertTrue(Path(project.shots[1].clip).exists())


class JobStateTest(unittest.TestCase):
    def test_job_transitions_and_failure_are_observable(self):
        manager = JobManager()
        done = manager.submit("ok-job", lambda job: {"ok": True})
        failed = manager.submit("bad-job", lambda job: (_ for _ in ()).throw(RuntimeError("boom")))

        deadline = time.time() + 5
        while time.time() < deadline and (
            manager.get(done.id).status != "done" or manager.get(failed.id).status != "failed"
        ):
            time.sleep(0.05)

        self.assertEqual(manager.get(done.id).status, "done")
        self.assertEqual(manager.get(done.id).result, {"ok": True})
        self.assertEqual(manager.get(failed.id).status, "failed")
        self.assertIn("boom", manager.get(failed.id).error)
        self.assertIn("ERROR: boom", manager.get(failed.id).logs)


class ProviderFallbackTest(unittest.TestCase):
    def test_retryable_video_provider_falls_back_to_local(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "clip.mp4"
            s = Settings()
            s.pipeline.mock = False
            s.minimax.mode = "cloud"
            s.minimax.api_key = "dummy"
            providers = Providers(s)

            class BadMiniMax:
                name = "minimax"

                def generate(self, shot, out_path, keyframe=None):
                    raise ProviderError("cloud timeout", retryable=True)

            class GoodLocal:
                name = "local"

                def clip(self, shot, out_path, keyframe=None):
                    Path(out_path).write_bytes(b"local")
                    return ProviderResult(path=str(out_path), provider="local", duration=4.0)

            providers.minimax = BadMiniMax()
            providers.comfy_video = None
            providers.local = GoodLocal()
            result = providers.clip(Shot(id="S01", duration=4, video_prompt="x"), out)
            self.assertEqual(result.provider, "local")
            self.assertTrue(out.exists())

    def test_tts_falls_back_without_killing_pipeline(self):
        engine = VoiceoverEngine(MiniMaxConfig(api_key="dummy"))

        class BadTTS:
            name = "minimax"

            def synthesize(self, text, out_path, voice_id="", speed=None):
                raise RuntimeError("cloud down")

        class GoodTTS:
            name = "edge-tts"

            def synthesize(self, text, out_path, voice_id="", speed=None):
                Path(out_path).write_bytes(b"audio")
                return 1.5

        engine._chain = lambda: [BadTTS(), GoodTTS()]
        with tempfile.TemporaryDirectory() as td:
            dur, name = engine.synthesize("你好", Path(td) / "a.mp3")
            self.assertEqual((dur, name), (1.5, "edge-tts"))


class MockH3FixtureTest(unittest.TestCase):
    def test_minimax_provider_against_fixture_gateway(self):
        """把 tools 里的 mock H3 收进测试：走完整 submit → poll → download。"""
        with tempfile.TemporaryDirectory() as td:
            fake_video = Path(td) / "fake.mp4"
            fake_video.write_bytes(b"fake mp4 bytes")
            with MockH3Server(fake_video, ready_after=0.0) as server:
                cfg = MiniMaxConfig(mode="local", base_url=server.base_url,
                                    poll_interval=0.01, task_timeout=5)
                provider = MiniMaxProvider(cfg)
                result = provider.generate(
                    Shot(id="S01", duration=4, video_prompt="a test shot"),
                    Path(td) / "out.mp4",
                )
                self.assertTrue(result.ok)
                self.assertEqual(result.provider, "minimax")
                self.assertTrue(result.task_id)


if __name__ == "__main__":
    unittest.main()
