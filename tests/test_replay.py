from tests.fake_world import FakeWorld, upstream_tree
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import gitutil, replay, state, transform, upstream

TAG = "v26.11"


def prepare(world: FakeWorld):
    m, repo = world.m, world.m.repos["pxr-foo"]
    state.record_old_tips(world.clone, TAG)
    scratch = upstream.filter_subtree(upstream.ensure_mirror(m), TAG, repo.upstream, m.state_dir / "tmp")
    upstream.import_filtered(world.clone, scratch, state.ref(TAG, "upstream"))
    replay.advance_open_usd(world.clone, TAG)
    return m, repo, replay.ensure_worktree(m, repo.name, TAG)


def notes(world):
    status = state.local_status(world.m, TAG, "pxr-foo")
    return [(status.replayed[s][1].resolution, status.replayed[s][1].files) for s in status.sources]


@needs_filter_repo
class ReplayTest(TempTest):
    def test_clean_replay(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual([r for r, _ in notes(world)], ["clean", "clean", "clean"])
        self.assertIn("return 2", (wt / "src/pxr/foo/bar.cpp").read_text())
        self.assertTrue(gitutil.ref_exists(world.clone, state.ref(TAG, "replayed")))

    def test_provable_auto_resolve(self):
        world = FakeWorld(self.tmp, {"include-conflict"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[0], ("auto", ("src/pxr/foo/bar.cpp",)))
        expected = transform.transform_text(upstream_tree(frozenset({"include-conflict"}))["pxr/base/foo/bar.cpp"], "foo")
        self.assertEqual((wt / "src/pxr/foo/bar.cpp").read_text(), expected)

    def test_provable_delete(self):
        world = FakeWorld(self.tmp, {"delete"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[0], ("auto", ("src/pxr/foo/old.h",)))
        self.assertFalse((wt / "src/pxr/foo/old.h").exists())

    def test_empty_pick(self):
        world = FakeWorld(self.tmp, {"fix-upstreamed"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual([r for r, _ in notes(world)], ["clean", "clean", "empty"])

    def test_manual_conflict_then_resolve(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        m, repo, wt = prepare(world)
        self.assertFalse(replay.replay(m, repo, TAG))
        pending = state.read_json(world.clone, TAG, "pending")
        self.assertEqual(pending["manual"], ["src/pxr/foo/bar.h"])
        self.assertEqual(state.local_status(m, TAG, "pxr-foo").state, "needs-attention")
        self.assertFalse(replay.replay(m, repo, TAG))
        with self.assertRaisesRegex(replay.ReplayError, "unresolved"):
            replay.resolve(m, repo, TAG)
        bar = wt / "src/pxr/foo/bar.h"
        text = bar.read_text()
        start, end = text.index("<<<<<<<"), text.index(">>>>>>>")
        bar.write_text(text[:start] + "int FooBar(int) noexcept;\n" + text[text.index("\n", end) + 1:])
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        replay.resolve(m, repo, TAG)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[2], ("manual", ("src/pxr/foo/bar.h",)))
        self.assertIsNone(state.read_json(world.clone, TAG, "pending"))

    def test_resolve_rejects_markers(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        m, repo, wt = prepare(world)
        replay.replay(m, repo, TAG)
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        with self.assertRaisesRegex(replay.ReplayError, "conflict markers"):
            replay.resolve(m, repo, TAG)

    def test_replay_twice_does_not_duplicate(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        m, repo, wt = prepare(world)
        replay.replay(m, repo, TAG)
        tip = gitutil.rev(wt, "HEAD")
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(gitutil.rev(wt, "HEAD"), tip)

    def test_open_usd_must_be_an_ancestor(self):
        world = FakeWorld(self.tmp)
        state.record_old_tips(world.clone, TAG)
        sh_git(world.clone, "fetch", "-q", str(world.upstream), "v26.11")
        sh_git(world.clone, "update-ref", state.ref(TAG, "upstream"), sh_git(world.upstream, "rev-parse", "v26.11"))
        with self.assertRaisesRegex(replay.ReplayError, "ancestor"):
            replay.advance_open_usd(world.clone, TAG)
