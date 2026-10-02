from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from tests.test_replay import TAG, prepare
from untwine_cli import finalize, gitutil, replay, state


@needs_filter_repo
class FinalizeTest(TempTest):
    def run_to_finalized(self, scenarios):
        world = FakeWorld(self.tmp, scenarios)
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        finalize.finalize(m, repo, TAG)
        return world, m, repo, wt

    def test_bump_autosquash_and_verify(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        subjects = gitutil.git(wt, "log", "--format=%s", f"{state.sync_open_usd(TAG)}..HEAD").splitlines()
        self.assertEqual(subjects, ["bar: mark FooBar noexcept.", "Add minimal release configuration.",
                                    "Restructure the 'foo' library as a standalone package."])
        self.assertIn("VERSION 0.26.11", (wt / "CMakeLists.txt").read_text())
        self.assertEqual(len(status.replayed), 3)

    def test_new_file_is_placed(self):
        world, m, repo, wt = self.run_to_finalized({"new-file"})
        self.assertFalse((wt / "baz.h").exists())
        self.assertEqual((wt / "src/pxr/foo/baz.h").read_text().splitlines()[-1], "#include <pxr/foo/pxr.h>")
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertTrue(any("baz.h" in item for item in status.review))
        restructure = gitutil.git(wt, "log", "--format=%H", "--grep=^Restructure", f"{state.sync_open_usd(TAG)}..HEAD")
        self.assertIn("src/pxr/foo/baz.h", gitutil.git(wt, "show", "--name-only", "--format=", restructure))

    def test_dependency_change_needs_attention(self):
        world, m, repo, wt = self.run_to_finalized({"dep-change"})
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertTrue(any("implies deps ['arch', 'boost', 'tf']" in a for a in status.attention))
        self.assertTrue(any("upstream dependencies changed" in r for r in status.review))

    def test_finalize_is_idempotent(self):
        world, m, repo, wt = self.run_to_finalized({"new-file"})
        tip = gitutil.rev(wt, "HEAD")
        finalize.finalize(m, repo, TAG)
        self.assertEqual(gitutil.rev(wt, "HEAD"), tip)
        self.assertTrue(any("baz.h" in item for item in state.local_status(m, TAG, "pxr-foo").review))

    def test_edit_after_finalize(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        target = gitutil.git(wt, "log", "--format=%H", "--grep=^Add minimal", f"{state.sync_open_usd(TAG)}..HEAD")
        (wt / "README.md").write_text("edited\n")
        sh_git(wt, "commit", "-q", "-a", f"--fixup={target}")
        self.assertEqual(state.local_status(m, TAG, "pxr-foo").state, "replayed")
        finalize.finalize(m, repo, TAG)
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(len(status.replayed), 3)
        self.assertEqual((wt / "README.md").read_text(), "edited\n")

    def test_structure_change_after_finalize_is_reported(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        commit(wt, {"extra.txt": "x\n"}, "An unrelated commit.")
        finalized = gitutil.rev(world.clone, state.ref(TAG, "finalized"))
        finalize.finalize(m, repo, TAG)
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertTrue(any("expected 3 commits" in a for a in status.attention))
        self.assertEqual(gitutil.rev(world.clone, state.ref(TAG, "finalized")), finalized)
