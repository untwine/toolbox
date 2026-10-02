from tests.helpers import TempTest, commit, init, sh_git
from untwine_cli import gitutil


class GitutilTest(TempTest):
    def setUp(self):
        super().setUp()
        self.repo = init(self.tmp / "repo")
        self.first = commit(self.repo, {"a.txt": "one\n", "dir/b.bin": b"\x00\xff"}, "first")
        self.second = commit(self.repo, {"a.txt": "two\n"}, "second")

    def test_git_returns_stripped_stdout(self):
        self.assertEqual(gitutil.git(self.repo, "log", "-1", "--format=%s"), "second")

    def test_failure_raises_with_message(self):
        with self.assertRaises(gitutil.GitError) as ctx:
            gitutil.git(self.repo, "rev-parse", "--verify", "nope")
        self.assertIn("rev-parse", str(ctx.exception))

    def test_rev_and_ref_exists(self):
        self.assertEqual(gitutil.rev(self.repo, "main"), self.second)
        self.assertIsNone(gitutil.rev(self.repo, "missing"))
        blob = sh_git(self.repo, "hash-object", "-w", "--stdin", input="x")
        sh_git(self.repo, "update-ref", "refs/x/blob", blob)
        self.assertTrue(gitutil.ref_exists(self.repo, "refs/x/blob"))
        self.assertIsNone(gitutil.rev(self.repo, "refs/x/blob"))

    def test_blob_and_paths(self):
        self.assertEqual(gitutil.blob(self.repo, self.first, "a.txt"), b"one\n")
        self.assertEqual(gitutil.blob(self.repo, self.first, "dir/b.bin"), b"\x00\xff")
        self.assertIsNone(gitutil.blob(self.repo, self.first, "missing"))
        self.assertEqual(gitutil.paths(self.repo, "HEAD"), {"a.txt", "dir/b.bin"})

    def test_is_ancestor(self):
        self.assertTrue(gitutil.is_ancestor(self.repo, self.first, self.second))
        self.assertFalse(gitutil.is_ancestor(self.repo, self.second, self.first))
