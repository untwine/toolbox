import io
from contextlib import redirect_stdout
from unittest import mock

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo
from untwine_cli import add, cli, gitutil, manifest, verify


@needs_filter_repo
class AddTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)

    def test_add_creates_a_conforming_repository(self):
        path, sibling = add.add(self.world.m, "qux")
        self.assertEqual(sibling.name, "pxr-foo")
        m = manifest.load(self.world.manifest_path)
        repo = m.repos["pxr-qux"]
        self.assertEqual((repo.upstream, repo.python, repo.deps), ("pxr/base/qux", "optional", ("arch", "foo", "boost")))
        self.assertEqual({f.check for f in verify.run_checks(path, m, repo)}, {"todo"})
        subjects = gitutil.git(path, "log", "--format=%s", "open-usd..main").splitlines()
        self.assertEqual(subjects, ["Add minimal release configuration.",
                                    "Restructure the 'qux' library as a standalone package."])
        self.assertIn("pxrpythonsubst", gitutil.git(path, "log", "-1", "--format=%B", "main~1"))
        self.assertIn("<pxr/foo/pxr.h>", (path / "src/pxr/qux/pxr.h.in").read_text())
        self.assertIn("#include <pxr/foo/bar.h>", (path / "src/pxr/qux/qux.h").read_text())
        self.assertTrue((path / "src/python/wrapQux.cpp").is_file())
        self.assertFalse((path / "test/testQux.py").read_text().startswith("#!"))
        conan = (path / "conanfile.py").read_text()
        self.assertIn('class PxrQuxConan', conan)
        self.assertIn('self.requires("pxr-foo/26.8")', conan)
        self.assertIn('self.options["pxr-foo"].python_version', conan)
        self.assertNotIn('self.options["pxr-arch"]', conan)
        self.assertIn("PUBLIC_CLASSES: qux", (path / "src/CMakeLists.txt").read_text())

    def test_missing_dependency_lists_order(self):
        with self.assertRaisesRegex(add.AddError, "in this order: nope"):
            add.add(self.world.m, "zed")
        self.assertFalse((self.world.workspace / "pxr-zed").exists())

    def test_refuses_existing(self):
        with self.assertRaisesRegex(add.AddError, "already in untwine.toml"):
            add.add(self.world.m, "foo")
        (self.world.workspace / "pxr-qux").mkdir()
        with self.assertRaisesRegex(add.AddError, "already exists"):
            add.add(self.world.m, "qux")

    def test_failure_cleans_up(self):
        before = self.world.manifest_path.read_text()
        with mock.patch("untwine_cli.add._configure", side_effect=add.AddError("boom")):
            with self.assertRaisesRegex(add.AddError, "boom"):
                add.add(self.world.m, "qux")
        self.assertFalse((self.world.workspace / "pxr-qux").exists())
        self.assertEqual(self.world.manifest_path.read_text(), before)

    def test_cli_add(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(self.world.manifest_path), "add", "qux"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-qux", out.getvalue())
        self.assertIn("TODO(untwine)", out.getvalue())
