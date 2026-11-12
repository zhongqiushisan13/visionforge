"""v1.4.0 三个新接口的 HTTP 级测试（TestClient，不占端口、不依赖磁盘外的东西）。"""

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="vf-api14-")
os.environ["VF_PROJECTS_DIR"] = str(Path(TMP) / "projects")
os.environ["VF_TOKEN_ROTATE"] = "0"
os.environ["VF_NO_CHILD_REGISTRY"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

import videoforge.web.server as srv  # noqa: E402
from videoforge.schemas import Project, Shot  # noqa: E402
from videoforge.web import security  # noqa: E402

TOKEN = security.token_path().read_text(encoding="utf-8").strip() \
    if security.token_path().exists() else "test-token"
HEADERS = {"X-VF-Token": TOKEN}
SLUG = "api-smoke"


def setUpModule():
    root = Path(os.environ["VF_PROJECTS_DIR"])
    directory = root / SLUG
    directory.mkdir(parents=True, exist_ok=True)
    p = Project.create(directory, title="接口冒烟", brief="测试", slug=SLUG)
    clip = directory / "clips" / "S01.mp4"
    clip.write_bytes(b"clip" * 256)
    p.shots = [Shot(id="S01", index=1, duration=4, subtitle="第一句",
                    status="rendered", clip=str(clip), clip_duration=4.0)]
    p.save()


class TestApi(unittest.TestCase):
    def client(self):
        return TestClient(srv.app, base_url="http://127.0.0.1")

    def test_subtitles_roundtrip(self):
        c = self.client()
        r = c.get(f"/api/projects/{SLUG}/subtitles", headers=HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["rows"]), 1)

        r = c.post(f"/api/projects/{SLUG}/subtitles", headers=HEADERS,
                   json={"rows": [{"shot_id": "S01", "text": "接口改过的"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["changed"], 1)

        r = c.get(f"/api/projects/{SLUG}/subtitles.srt", headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertIn("接口改过的", r.content.decode("utf-8-sig"))

    def test_package_roundtrip(self):
        c = self.client()
        r = c.get(f"/api/projects/{SLUG}/package", headers=HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.content.startswith(b"PK"))

        r2 = c.post("/api/projects/import-package", headers=HEADERS, content=r.content)
        self.assertEqual(r2.status_code, 200, r2.text)
        slug = r2.json()["slug"]
        self.assertTrue(slug.startswith(SLUG))
        # 再导一次应再换一个后缀，不覆盖已有项目
        r3 = c.post("/api/projects/import-package", headers=HEADERS, content=r.content)
        third = r3.json()["slug"]
        self.assertTrue(third.startswith(SLUG))
        self.assertNotIn(third, (SLUG, slug))
        self.assertTrue((Path(os.environ["VF_PROJECTS_DIR"]) / third / "project.json").exists())

    def test_diagnostics_bundle(self):
        c = self.client()
        r = c.get("/api/diagnostics/bundle", headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as zf:
            names = set(zf.namelist())
            self.assertIn("meta.json", names)
            meta = json.loads(zf.read("meta.json"))
        self.assertEqual(meta["version"], "1.4.0")

    def test_subtitle_validation(self):
        c = self.client()
        r = c.post(f"/api/projects/{SLUG}/subtitles", headers=HEADERS, json={"rows": "nope"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
