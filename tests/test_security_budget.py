"""本地 API 防护与预算护栏测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from videoforge.config import Settings
from videoforge.pipeline.director import Director
from videoforge.schemas import Project
from videoforge.web import security


class SecurityGuardTest(unittest.TestCase):
    def make_client(self) -> TestClient:
        app = FastAPI()
        security.install(app, "secret-token")

        @app.get("/api/health")
        def health():
            return {"ok": True}

        @app.get("/api/projects")
        def projects():
            return []

        @app.get("/media/demo/file.mp4")
        def media():
            return {"media": True}

        return TestClient(app, base_url="http://127.0.0.1")

    def test_api_requires_token(self):
        client = self.make_client()
        self.assertEqual(client.get("/api/projects").status_code, 401)
        r = client.get("/api/projects", headers={"X-VF-Token": "secret-token"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_health_is_public(self):
        client = self.make_client()
        r = client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_rejects_dns_rebinding_host(self):
        client = self.make_client()
        r = client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(r.status_code, 400)

    def test_media_requires_same_site_cookie_or_query_token(self):
        client = self.make_client()
        self.assertEqual(client.get("/media/demo/file.mp4").status_code, 401)
        self.assertEqual(client.get("/media/demo/file.mp4?token=secret-token").status_code, 200)
        client.cookies.set(security.TOKEN_COOKIE, "secret-token")
        self.assertEqual(client.get("/media/demo/file.mp4").status_code, 200)

    def test_inject_token_adds_window_value(self):
        html = security.inject_token("<html><head></head><body></body></html>", "abc")
        self.assertIn('window.VF_TOKEN="abc";', html)


class BudgetGuardTest(unittest.TestCase):
    def make_director(self, root: Path) -> tuple[Settings, Director]:
        s = Settings()
        s.root = root
        s.pipeline.projects_dir = str(root / "projects")
        s.pipeline.mock = True
        s.pipeline.budget = 0.05
        s.pipeline.prices.update({"deepseek": 0.05, "minimax_video": 3.0})
        return s, Director(s)

    def test_budget_blocks_paid_call_and_charge_is_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, director = self.make_director(root)
            project = Project.create(root / "projects" / "demo", title="demo",
                                     brief="brief", slug="demo", mock=True)

            # 等于预算仍放行（不超出才放行）
            reservation = director._budget_check(project, "deepseek", "剧本生成")
            director._charge(project, "deepseek", "plan")
            director._release(reservation)
            self.assertEqual(project.meta["costs"]["total"], 0.05)

            # 下一次付费调用会超过上限，必须在调用前阻断
            with self.assertRaisesRegex(RuntimeError, "预算护栏"):
                director._budget_check(project, "minimax_video", "S01 镜头")

            # 本地 / mock 通道价格为 0，不记账
            director._charge(project, "mock", "S01")
            self.assertEqual(len(project.meta["costs"]["entries"]), 1)
            self.assertTrue((project.path("project.json")).exists())

    def test_concurrent_jobs_reserve_budget_before_spending(self):
        """3 个镜头并发时，不能都看到同一个余额然后一起超额。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s = Settings()
            s.root = root
            s.pipeline.projects_dir = str(root / "projects")
            s.pipeline.mock = True
            s.pipeline.budget = 4.0
            s.pipeline.prices["minimax_video"] = 3.0
            director = Director(s)
            project = Project.create(root / "projects" / "demo", title="demo",
                                     brief="brief", slug="demo", mock=True)

            first = director._budget_check(project, "minimax_video", "S01")
            with self.assertRaisesRegex(RuntimeError, "在途"):
                director._budget_check(project, "minimax_video", "S02")
            director._release(first)
            second = director._budget_check(project, "minimax_video", "S02")
            director._release(second)


if __name__ == "__main__":
    unittest.main()
