from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import UntwineError, gitutil, release, state, sync

TAG = "v26.11"


@needs_filter_repo
class PushPrsTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"upstream-edit"})
        self.gh = FakeGh({"untwine/pxr-foo": self.world.origin})
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))

    def test_push_creates_then_updates_pr(self):
        sync.sync(self.world.m, TAG, ["pxr-foo"])
        urls = release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.assertEqual(urls, ["pxr-foo: https://github.com/untwine/pxr-foo/pull/1"])
        tip = gitutil.rev(self.world.clone, state.sync_branch(TAG))
        self.assertEqual(sh_git(self.world.origin, "rev-parse", f"refs/heads/{state.sync_branch(TAG)}"), tip)
        self.assertIn("## Commit mapping", self.gh.prs[1]["body"])
        self.assertEqual(self.gh.prs[1]["title"], "Sync pxr-foo to OpenUSD v26.11")
        release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.assertEqual(len(self.gh.prs), 1)

    def test_refuses_unverified(self):
        world = FakeWorld(self.tmp / "other", {"fix-conflict"})
        sync.sync(world.m, TAG, ["pxr-foo"])
        with self.assertRaisesRegex(UntwineError, "only verified"):
            release.push_prs(world.m, TAG, [], confirm=lambda lines: True)

    def test_nothing_happens_without_confirmation(self):
        sync.sync(self.world.m, TAG, ["pxr-foo"])
        self.assertEqual(release.push_prs(self.world.m, TAG, [], confirm=lambda lines: False), [])
        self.assertEqual(sh_git(self.world.origin, "branch", "--list", "sync/*"), "")
        self.assertEqual(self.gh.prs, {})
