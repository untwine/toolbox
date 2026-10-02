from tests.helpers import TempTest, commit, init, sh_git
from untwine_cli import gitutil, state
from untwine_cli.manifest import Manifest, Repo


class StateTest(TempTest):
    def setUp(self):
        super().setUp()
        self.ws = self.tmp / "ws"
        self.repo = init(self.ws / "pxr-foo")
        self.base = commit(self.repo, {"a": "1\n"}, "upstream")
        sh_git(self.repo, "branch", "open-usd")
        self.s1 = commit(self.repo, {"b": "1\n"}, "one")
        self.s2 = commit(self.repo, {"c": "1\n"}, "two")
        self.m = Manifest(self.ws / "toolbox" / "untwine.toml", self.ws, "url", "untwine", "v26.08", "2023.1.0",
                          {"pxr-foo": Repo("pxr-foo", upstream="pxr/base/foo")})

    def test_note_round_trip(self):
        note = state.Note(self.s1, "auto", ("src/a b.h", "x.h"))
        self.assertEqual(state.Note.parse(note.render()), note)
        state.write_note(self.repo, "v26.11", self.s2, note)
        self.assertEqual(state.read_note(self.repo, "v26.11", self.s2), note)
        self.assertIsNone(state.read_note(self.repo, "v26.12", self.s2))

    def test_json_round_trip(self):
        state.write_json(self.repo, "v26.11", "review", {"attention": ["x"], "review": []})
        self.assertEqual(state.read_json(self.repo, "v26.11", "review"), {"attention": ["x"], "review": []})
        state.delete_ref(self.repo, state.ref("v26.11", "review"))
        self.assertIsNone(state.read_json(self.repo, "v26.11", "review"))

    def test_record_old_tips_only_once(self):
        state.record_old_tips(self.repo, "v26.11")
        commit(self.repo, {"d": "1\n"}, "three")
        state.record_old_tips(self.repo, "v26.11")
        self.assertEqual(gitutil.rev(self.repo, state.ref("v26.11", "old-main")), self.s2)
        self.assertEqual(state.source_commits(self.repo, "v26.11"), [self.s1, self.s2])

    def test_status_progression(self):
        tag = "v26.11"
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "pending")
        state.record_old_tips(self.repo, tag)
        sh_git(self.repo, "update-ref", state.ref(tag, "upstream"), self.base)
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "filtered")
        sh_git(self.repo, "branch", state.sync_open_usd(tag), self.base)
        sh_git(self.repo, "branch", state.sync_branch(tag), self.base)
        sh_git(self.repo, "checkout", "-q", state.sync_branch(tag))
        new1 = commit(self.repo, {"b": "1\n"}, "one")
        state.write_note(self.repo, tag, new1, state.Note(self.s1, "clean"))
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "replaying")
        state.write_note(self.repo, tag, self.s2, state.Note(self.s2, "empty"))
        sh_git(self.repo, "update-ref", state.ref(tag, "replayed"), new1)
        status = state.local_status(self.m, tag, "pxr-foo")
        self.assertEqual(status.state, "replayed")
        self.assertEqual(status.replayed[self.s2][0], None)
        sh_git(self.repo, "update-ref", state.ref(tag, "finalized"), new1)
        state.write_json(self.repo, tag, "review", {"attention": [], "review": ["read me"]})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "verified")
        state.write_json(self.repo, tag, "review", {"attention": ["broken"], "review": []})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "needs-attention")
        sh_git(self.repo, "update-ref", state.ref(tag, "promoted"), new1)
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "promoted")

    def test_attention_before_replay(self):
        tag = "v26.11"
        state.record_old_tips(self.repo, tag)
        state.write_json(self.repo, tag, "review", {"attention": ["filter failed"], "review": []})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "needs-attention")
