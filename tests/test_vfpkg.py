"""项目包 .vfpkg：导出→导入往返、路径重写、恶意包拦截。"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from videoforge import vfpkg
from videoforge.schemas import Project, Shot


def make_project(root: Path, slug: str = "demo") -> Project:
    directory = root / "projects" / slug
    directory.mkdir(parents=True, exist_ok=True)
    p = Project.create(directory, title="演示项目", brief="测试用", slug=slug)
    clip = directory / "clips" / "S01.mp4"
    kf = directory / "keyframes" / "S01.png"
    voice = directory / "audio" / "S01.mp3"
    out = directory / "output" / "final.mp4"
    clip.write_bytes(b"clip" * 512)
    kf.write_bytes(b"png" * 256)
    voice.write_bytes(b"mp3" * 128)
    out.write_bytes(b"mp4" * 1024)
    p.shots = [Shot(id="S01", index=1, duration=5, status="rendered",
                    clip=str(clip), keyframe=str(kf), audio=str(voice),
                    audio_duration=4.5, subtitle="第一句")]
    p.output = str(out)
    p.save()
    return p


class TestExport(unittest.TestCase):
    def test_manifest_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            dest = root / "demo.vfpkg"
            manifest = vfpkg.export_project(p, dest)

            self.assertEqual(manifest["format"], "vfpkg")
            self.assertEqual(manifest["shots"], 1)
            self.assertGreater(dest.stat().st_size, 0)

            with zipfile.ZipFile(dest) as zf:
                names = set(zf.namelist())
                portable = json.loads(zf.read("project.json"))
            self.assertIn("clips/S01.mp4", names)
            self.assertIn("keyframes/S01.png", names)
            self.assertNotIn("output/final.mp4", names)      # 默认不带成片
            self.assertEqual(portable["shots"][0]["clip"], "clips/S01.mp4")
            self.assertEqual(portable["bgm"], "")

    def test_include_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            dest = root / "with-output.vfpkg"
            vfpkg.export_project(p, dest, include_output=True)
            with zipfile.ZipFile(dest) as zf:
                self.assertIn("output/final.mp4", zf.namelist())

    def test_missing_file_is_recorded_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            p.shots[0].clip = str(p.dir / "clips" / "gone.mp4")
            manifest = vfpkg.export_project(p, root / "x.vfpkg")
            self.assertIn("clips/gone.mp4", manifest["missing"])


class TestImport(unittest.TestCase):
    def test_roundtrip_restores_absolute_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            package = root / "demo.vfpkg"
            vfpkg.export_project(p, package)

            target_root = root / "other-machine" / "projects"
            p2 = vfpkg.import_package(package, target_root)

            self.assertEqual(p2.dir.name, "demo")
            self.assertEqual(p2.title, "演示项目")
            shot = p2.shots[0]
            self.assertEqual(Path(shot.clip), p2.dir / "clips" / "S01.mp4")
            self.assertTrue(Path(shot.clip).exists())
            self.assertTrue(Path(shot.keyframe).exists())
            self.assertTrue(Path(shot.audio).exists())
            self.assertEqual(shot.subtitle, "第一句")
            self.assertEqual(p2.output, "")                    # 没打包成片就别指向旧路径

    def test_slug_collision_gets_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            package = root / "demo.vfpkg"
            vfpkg.export_project(p, package)
            root2 = root / "projects2"
            first = vfpkg.import_package(package, root2)
            second = vfpkg.import_package(package, root2)
            self.assertEqual(first.dir.name, "demo")
            self.assertEqual(second.dir.name, "demo-2")
            self.assertTrue((second.dir / "clips" / "S01.mp4").exists())

    def test_rejects_slip_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "bad.vfpkg"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("manifest.json", json.dumps(
                    {"format": "vfpkg", "format_version": 1, "slug": "bad"}))
                zf.writestr("project.json", "{}")
                zf.writestr("../evil.txt", "pwn")
            with self.assertRaises(vfpkg.PackageError):
                vfpkg.import_package(bad, root / "projects")

    def test_rejects_unknown_member(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "bad.vfpkg"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("manifest.json", json.dumps(
                    {"format": "vfpkg", "format_version": 1, "slug": "bad"}))
                zf.writestr("project.json", "{}")
                zf.writestr("startup.bat", "echo hi")
            with self.assertRaises(vfpkg.PackageError):
                vfpkg.import_package(bad, root / "projects")

    def test_rejects_wrong_format(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("manifest.json", json.dumps(
                    {"format": "something-else", "format_version": 1}))
                zf.writestr("project.json", "{}")
            with self.assertRaises(vfpkg.PackageError):
                vfpkg.import_package(bad, root / "projects")

    def test_inspect_does_not_extract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = make_project(root)
            package = root / "demo.vfpkg"
            vfpkg.export_project(p, package)
            info = vfpkg.inspect_package(package)
            self.assertEqual(info["slug"], "demo")
            self.assertEqual(info["shots"], 1)
            self.assertFalse((root / "projects2").exists())


if __name__ == "__main__":
    unittest.main()
