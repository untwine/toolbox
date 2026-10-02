import io
from contextlib import redirect_stdout

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from untwine_cli import UntwineError, cli, gitutil, state, sync

TAG = "v26.11"


@needs_filter_repo
class SyncTest(TempTest):
    def test_full_sync(self):
        world = FakeWorld(self.tmp, {"upstream-edit", "new-file"})
        sync.sync(world.m, TAG, ["pxr-foo"])
        status = state.local_status(world.m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(gitutil.rev(world.clone, "main"), gitutil.rev(world.clone, "origin/main"))

    def test_rerun_is_idempotent(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        sync.sync(world.m, TAG, ["pxr-foo"])
        tip = gitutil.rev(world.clone, state.sync_branch(TAG))
        sync.sync(world.m, TAG, ["pxr-foo"])
        self.assertEqual(gitutil.rev(world.clone, state.sync_branch(TAG)), tip)
        self.assertEqual(state.local_status(world.m, TAG, "pxr-foo").state, "verified")

    def test_conflict_stops_then_cli_resolve(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        sync.sync(world.m, TAG, ["pxr-foo"])
        self.assertEqual(state.local_status(world.m, TAG, "pxr-foo").state, "needs-attention")
        wt = state.worktree_path(world.m, TAG, "pxr-foo")
        (wt / "src/pxr/foo/bar.h").write_text((world.clone / "src/pxr/foo/bar.h").read_text())
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(world.manifest_path), "resolve", "pxr-foo"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-foo: verified", out.getvalue())

    def test_preflight_refusals(self):
        world = FakeWorld(self.tmp)
        (world.clone / "README.md").write_text("dirty\n")
        with self.assertRaisesRegex(UntwineError, "uncommitted"):
            sync.sync(world.m, TAG, ["pxr-foo"])
        sh_git(world.clone, "checkout", "--", "README.md")
        commit(world.clone, {"x": "1\n"}, "local only")
        with self.assertRaisesRegex(UntwineError, "differs from origin/main"):
            sync.sync(world.m, TAG, ["pxr-foo"])

    def test_errors_are_recorded_per_repository(self):
        world = FakeWorld(self.tmp)
        text = world.manifest_path.read_text().replace('upstream = "pxr/base/foo"', 'upstream = "pxr/base"')
        world.manifest_path.write_text(text)
        from untwine_cli import manifest
        m = manifest.load(world.manifest_path)
        sync.sync(m, TAG, ["pxr-foo"])
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertIn("ancestor", status.attention[0])

    def test_dry_run_leaves_nothing(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(world.manifest_path), "sync", TAG, "pxr-foo", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-foo: verified", out.getvalue())
        self.assertEqual(gitutil.git(world.clone, "for-each-ref", "refs/untwine", "refs/notes", "refs/heads/sync"), "")
        self.assertFalse(state.worktree_path(world.m, TAG, "pxr-foo").exists())

    def test_rehearsal_on_current_tag_changes_nothing(self):
        world = FakeWorld(self.tmp)
        sync.sync(world.m, "v26.08", ["pxr-foo"])
        status = state.local_status(world.m, "v26.08", "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(gitutil.git(world.clone, "diff", "main", state.sync_branch("v26.08")), "")
        self.assertEqual({note.resolution for _, note in status.replayed.values()}, {"clean"})
