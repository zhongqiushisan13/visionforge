"""成片尺寸必须以项目设定为准（真机踩到：项目写 1280x720，成片却是 608x352）。"""

import unittest

from videoforge.pipeline.editor import project_size
from videoforge.schemas import Project


class TestProjectSize(unittest.TestCase):
    def test_parses_project_resolution(self):
        p = Project()
        p.resolution = "1280x720"
        self.assertEqual(project_size(p, (1920, 1080)), (1280, 720))

    def test_accepts_fullwidth_and_star(self):
        p = Project()
        for raw in ("1280×720", "1280*720", " 1280 x 720 "):
            p.resolution = raw
            self.assertEqual(project_size(p, (608, 352)), (1280, 720), raw)

    def test_falls_back_on_unknown_format(self):
        p = Project()
        p.resolution = "768P"
        self.assertEqual(project_size(p, (608, 352)), (608, 352))

    def test_rounds_to_even(self):
        p = Project()
        p.resolution = "1279x719"
        self.assertEqual(project_size(p, (1920, 1080)), (1278, 718))

    def test_rejects_absurd_sizes(self):
        p = Project()
        p.resolution = "20x20"
        self.assertEqual(project_size(p, (1920, 1080)), (1920, 1080))


class TestProjectCreateSize(unittest.TestCase):
    """新建项目时按画幅给分辨率：竖屏别默认成横屏。"""

    def _create(self, aspect):
        import tempfile
        from pathlib import Path
        from videoforge.schemas import Project
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Project.create(Path(td.name) / "p", title="t", brief="b", slug="p", aspect=aspect)
        return p

    def test_vertical_defaults_to_portrait(self):
        self.assertEqual(self._create("9:16").resolution, "720x1280")

    def test_widescreen_defaults_to_720p(self):
        self.assertEqual(self._create("16:9").resolution, "1280x720")

    def test_square_defaults_to_1024(self):
        self.assertEqual(self._create("1:1").resolution, "1024x1024")

    def test_explicit_resolution_wins(self):
        import tempfile
        from pathlib import Path
        from videoforge.schemas import Project
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Project.create(Path(td.name) / "q", title="t", brief="b", slug="q",
                           aspect="9:16", resolution="1080x1920")
        self.assertEqual(p.resolution, "1080x1920")
