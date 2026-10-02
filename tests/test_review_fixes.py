"""Regression tests for the final whole-branch review findings."""

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from untwine_cli import UntwineError, cli, finalize, gitutil, release, replay, state, sync
from tests.test_replay import prepare

TAG = "v26.11"


def fixup(wt, subject_prefix, rel, old, new):
    target = gitutil.git(wt, "log", "--format=%H", f"--grep=^{subject_prefix}", f"{state.sync_open_usd(TAG)}..HEAD")
    path = wt / rel
    path.write_text(path.read_text().replace(old, new))
    sh_git(wt, "commit", "-q", "-a", f"--fixup={target}")


@needs_filter_repo
class ReviewFixesTest(TempTest):
    def world(self, scenarios=("upstream-edit",)):
        world = FakeWorld(self.tmp, set(scenarios))
        self.gh = FakeGh({"untwine/pxr-foo": world.origin})
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        return world

    def test_dry_run_refuses_a_release_in_progress(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        with redirect_stdout(io.StringIO()):
            code = cli.main(["--manifest", str(world.manifest_path), "sync", TAG, "pxr-foo", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertTrue(state.worktree_path(world.m, TAG, "pxr-foo").exists())
        self.assertTrue(gitutil.ref_exists(world.clone, state.ref(TAG, "finalized")))

    def test_interrupted_rerun_is_not_verified(self):
        world = self.world(("dep-change",))
        sync.sync(world.m, TAG, ["pxr-foo"])
        self.assertEqual(state.local_status(world.m, TAG, "pxr-foo").state, "needs-attention")
        with mock.patch("untwine_cli.finalize.finalize", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                sync.sync(world.m, TAG, ["pxr-foo"])
        self.assertNotEqual(state.local_status(world.m, TAG, "pxr-foo").state, "verified")

    def test_push_prs_does_not_overwrite_remote_commits(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        lines = []
        release.push_prs(world.m, TAG, [], confirm=lambda l: lines.extend(l) or True)
        tip = gitutil.rev(world.clone, state.sync_branch(TAG))
        self.assertTrue(any(tip[:12] in line for line in lines))
        reviewer = self.tmp / "reviewer"
        sh_git(self.tmp, "clone", "-q", "-b", state.sync_branch(TAG), str(world.origin), str(reviewer))
        commit(reviewer, {"suggestion.txt": "x\n"}, "Apply suggestion.")
        sh_git(reviewer, "push", "-q", "origin", state.sync_branch(TAG))
        theirs = sh_git(reviewer, "rev-parse", "HEAD")
        fixup(state.worktree_path(world.m, TAG, "pxr-foo"), "Add minimal", "README.md", "Built", "Made")
        sync.sync(world.m, TAG, ["pxr-foo"])
        with self.assertRaises(UntwineError):
            release.push_prs(world.m, TAG, [], confirm=lambda l: True)
        self.assertEqual(sh_git(world.origin, "rev-parse", f"refs/heads/{state.sync_branch(TAG)}"), theirs)

    def test_promote_keeps_local_commits(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        release.push_prs(world.m, TAG, [], confirm=lambda l: True)
        hotfix = commit(world.clone, {"hotfix.txt": "x\n"}, "Local hotfix.")
        out = io.StringIO()
        done = release.promote(world.m, TAG, [], confirm=lambda l: True, out=lambda s: out.write(s + "\n"))
        self.assertEqual(done, [])
        self.assertIn("local main", out.getvalue())
        self.assertTrue(gitutil.is_ancestor(world.clone, hotfix, "main"))

    def test_promote_completes_after_a_cleanup_failure(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        release.push_prs(world.m, TAG, [], confirm=lambda l: True)
        hook = world.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nwhile read old new ref; do\n"
                        "  case \"$new\" in 0000000000000000000000000000000000000000) exit 1;; esac\ndone\n")
        hook.chmod(0o755)
        out = io.StringIO()
        done = release.promote(world.m, TAG, [], confirm=lambda l: True, out=lambda s: out.write(s + "\n"))
        self.assertEqual(done, ["pxr-foo"])
        self.assertIn("rerun", out.getvalue())
        tip = sh_git(world.origin, "rev-parse", "refs/heads/main")
        self.assertEqual(gitutil.rev(world.clone, "main"), tip)
        hook.unlink()
        release.promote(world.m, TAG, [], confirm=lambda l: True, out=lambda s: None)
        self.assertEqual(sh_git(world.origin, "branch", "--list", "sync/*"), "")
        self.assertFalse(state.worktree_path(world.m, TAG, "pxr-foo").exists())
        self.assertIsNone(gitutil.rev(world.clone, state.sync_branch(TAG)))

    def test_failed_autosquash_is_aborted(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        wt = state.worktree_path(world.m, TAG, "pxr-foo")
        fixup(wt, "Restructure", "src/pxr/foo/bar.h", "int FooBar() noexcept;", "int FooBar() noexcept; // x")
        sync.sync(world.m, TAG, ["pxr-foo"])
        rebase_dir = gitutil.git(wt, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge")
        self.assertFalse(Path(rebase_dir).exists())
        status = state.local_status(world.m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertTrue(any("autosquash" in a for a in status.attention))

    def test_preflight_requires_a_clean_worktree(self):
        world = self.world()
        sync.sync(world.m, TAG, ["pxr-foo"])
        wt = state.worktree_path(world.m, TAG, "pxr-foo")
        (wt / "README.md").write_text("staged\n")
        sh_git(wt, "add", "README.md")
        with self.assertRaisesRegex(UntwineError, "worktree"):
            sync.sync(world.m, TAG, ["pxr-foo"])

    def test_crash_between_pick_and_note_is_adopted(self):
        world = self.world()
        m, repo, wt = prepare(world)
        real = state.write_note
        with mock.patch("untwine_cli.state.write_note", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                replay.replay(m, repo, TAG)
        self.assertTrue(replay.replay(m, repo, TAG))
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual([status.replayed[s][1].resolution for s in status.sources], ["clean"] * 3)
        self.assertEqual(len(gitutil.git(wt, "rev-list", f"{state.sync_open_usd(TAG)}..HEAD").split()), 3)
        self.assertIs(state.write_note, real)

    def test_crash_during_resolve_is_adopted(self):
        world = self.world(("fix-conflict",))
        m, repo, wt = prepare(world)
        self.assertFalse(replay.replay(m, repo, TAG))
        (wt / "src/pxr/foo/bar.h").write_text((world.clone / "src/pxr/foo/bar.h").read_text())
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        with mock.patch("untwine_cli.state.write_note", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                replay.resolve(m, repo, TAG)
        replay.resolve(m, repo, TAG)
        self.assertTrue(replay.replay(m, repo, TAG))
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.replayed[status.sources[2]][1].resolution, "manual")
