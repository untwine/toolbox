from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import UntwineError, add, gitutil, manifest


@needs_filter_repo
class PublishRepoTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)
        self.gh = FakeGh({}, root=self.tmp / "remotes")
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        self.path, _ = add.add(self.world.m, "qux")
        self.m = manifest.load(self.world.manifest_path)

    def resolve_markers(self):
        for rel in ("src/CMakeLists.txt",):
            text = (self.path / rel).read_text()
            (self.path / rel).write_text("".join(l for l in text.splitlines(True) if not l.startswith("#")))
        sh_git(self.path, "commit", "-q", "-a", "--amend", "--no-edit")

    def test_publish_refuses_markers(self):
        with self.assertRaisesRegex(UntwineError, "todo"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        self.assertEqual(self.gh.origins, {})

    def test_publish(self):
        self.resolve_markers()
        self.assertIsNone(add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: False))
        self.assertEqual(self.gh.origins, {})
        url = add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        origin = self.gh.origins["untwine/pxr-qux"]
        self.assertEqual(url, str(origin))
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/main"), gitutil.rev(self.path, "main"))
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/open-usd"), gitutil.rev(self.path, "open-usd"))
        self.assertEqual(gitutil.git(self.path, "rev-parse", "--abbrev-ref", "main@{upstream}"), "origin/main")
        with self.assertRaisesRegex(UntwineError, "already has an origin"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
