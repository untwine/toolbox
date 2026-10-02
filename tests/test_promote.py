import io
from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from untwine_cli import gitutil, manifest, release, state, sync

TAG = "v26.11"


@needs_filter_repo
class PromoteTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"upstream-edit"})
        self.gh = FakeGh({"untwine/pxr-foo": self.world.origin})
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        sync.sync(self.world.m, TAG, ["pxr-foo"])
        release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.tip = gitutil.rev(self.world.clone, state.sync_branch(TAG))
        self.usd = gitutil.rev(self.world.clone, state.sync_open_usd(TAG))

    def promote(self):
        out = io.StringIO()
        done = release.promote(self.world.m, TAG, [], confirm=lambda lines: True, out=lambda s: out.write(s + "\n"))
        return done, out.getvalue()

    def test_promote(self):
        done, _ = self.promote()
        self.assertEqual(done, ["pxr-foo"])
        origin = self.world.origin
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/main"), self.tip)
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/open-usd"), self.usd)
        self.assertEqual(sh_git(origin, "branch", "--list", "sync/*"), "")
        self.assertEqual(gitutil.rev(self.world.clone, "main"), self.tip)
        self.assertEqual(gitutil.rev(self.world.clone, "open-usd"), self.usd)
        self.assertEqual(state.local_status(self.world.m, TAG, "pxr-foo").state, "promoted")
        self.assertFalse(state.worktree_path(self.world.m, TAG, "pxr-foo").exists())
        self.assertTrue((self.world.manifest_path.parent / "releases" / "v26.11.md").is_file())
        self.assertEqual(manifest.load(self.world.manifest_path).openusd, "v26.11")

    def test_refuses_failing_checks(self):
        self.gh.check_state = "fail"
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("CI checks are fail", out)

    def test_refuses_when_origin_moved(self):
        other = self.tmp / "other"
        sh_git(self.tmp, "clone", "-q", str(self.world.origin), str(other))
        commit(other, {"x": "1\n"}, "Someone else pushed.")
        sh_git(other, "push", "-q", "origin", "main")
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("origin/main moved", out)
        self.assertNotEqual(sh_git(self.world.origin, "rev-parse", "refs/heads/main"), self.tip)

    def test_refuses_stale_pr(self):
        wt = state.worktree_path(self.world.m, TAG, "pxr-foo")
        target = gitutil.git(wt, "log", "--format=%H", "--grep=^Add minimal", f"{state.sync_open_usd(TAG)}..HEAD")
        (wt / "README.md").write_text("edited\n")
        sh_git(wt, "commit", "-q", "-a", f"--fixup={target}")
        sync.sync(self.world.m, TAG, ["pxr-foo"])
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("run push-prs", out)

    def test_discard_refused_after_promote(self):
        self.promote()
        with self.assertRaisesRegex(Exception, "already promoted"):
            sync.discard(self.world.m, TAG, [], confirm=lambda lines: True)
