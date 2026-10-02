"""Regression tests for the phase 2 whole-branch review findings."""

from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import UntwineError, add, gitutil, manifest
from untwine_cli.manifest import Repo


class RenameTest(TempTest):
    def test_rename_keeps_openusd_references(self):
        text = ('description = "Universal Scene Description library used in OpenUSD"\n'
                'topics = ("pixar", "open-usd", "usd")\n'
                'See https://github.com/PixarAnimationStudios/OpenUSD and https://graphics.pixar.com/usd/release\n'
                'add_library(usd)\nUSD_API pxr-usd pxr/usd/stage.h\n')
        self.assertEqual(add.rename(text, "usd", "esf"),
                         'description = "Universal Scene Description library used in OpenUSD"\n'
                         'topics = ("pixar", "open-usd", "esf")\n'
                         'See https://github.com/PixarAnimationStudios/OpenUSD and https://graphics.pixar.com/usd/release\n'
                         'add_library(esf)\nESF_API pxr-esf pxr/esf/stage.h\n')


@needs_filter_repo
class AddReviewTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)

    def test_descriptions_and_odd_identifiers_are_marked(self):
        conan = self.world.clone / "conanfile.py"
        text = conan.read_text().replace('    name = "pxr-foo"\n',
                                         '    name = "pxr-foo"\n    description = "Foo things"\n')
        conan.write_text(text.replace("class PxrFooConan", "FOO_IMPL = 'fooTBB'\n\n\nclass PxrFooConan"))
        sh_git(self.world.clone, "commit", "-q", "-a", "--amend", "--no-edit")
        sh_git(self.world.clone, "push", "-q", "-f", "origin", "main")
        path, _ = add.add(self.world.m, "qux")
        conan = (path / "conanfile.py").read_text()
        self.assertIn("TODO(untwine): review description", conan)
        self.assertIn("TODO(untwine): review renamed sibling identifiers", conan)
        self.assertIn("QUX_IMPL", conan)

    def test_external_libraries_are_listed(self):
        path, _ = add.add(self.world.m, "qux")
        self.assertIn("external LIBRARIES: TBB::tbb", (path / "src/CMakeLists.txt").read_text())

    def test_refuses_a_sibling_with_another_python_shape(self):
        with self.assertRaisesRegex(add.AddError, "same Python shape"):
            add.add(self.world.m, "nope")

    def test_never_picks_a_sibling_without_dependencies(self):
        m = self.world.m
        repos = dict(m.repos)
        repos["pxr-baz"] = Repo("pxr-baz", upstream="pxr/base/baz", python="optional")
        (self.world.workspace / "pxr-baz").mkdir()
        sibling = add.nearest_sibling(manifest.Manifest(m.path, m.workspace, m.upstream_url, m.github_org,
                                                        m.openusd, m.tbb, repos), {"boost"}, "optional")
        self.assertEqual(sibling.name, "pxr-foo")

    def test_refuses_camel_case_names(self):
        with self.assertRaisesRegex(add.AddError, "lowercase"):
            add.add(self.world.m, "usdGeom")


def resolve_all_markers(path):
    """Resolve every review marker the way a maintainer would, then amend the config commit."""
    for rel in sh_git(path, "grep", "-l", "TODO(untwine)").splitlines():
        lines = (path / rel).read_text().splitlines(True)
        if rel in ("src/CMakeLists.txt", "test/CMakeLists.txt"):
            lines = [l for l in lines if not l.startswith("#")]
        else:
            lines = [l for l in lines if "TODO(untwine)" not in l]
        (path / rel).write_text("".join(lines))


@needs_filter_repo
class PublishReviewTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)
        self.gh = FakeGh({}, root=self.tmp / "remotes")
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        self.path, _ = add.add(self.world.m, "qux")
        self.m = manifest.load(self.world.manifest_path)

    def strip_markers(self, commit: bool) -> None:
        resolve_all_markers(self.path)
        if commit:
            sh_git(self.path, "commit", "-q", "-a", "--amend", "--no-edit")

    def test_refuses_uncommitted_changes(self):
        self.strip_markers(commit=False)
        with self.assertRaisesRegex(UntwineError, "uncommitted"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        self.assertEqual(self.gh.origins, {})

    def test_refuses_another_branch(self):
        self.strip_markers(commit=True)
        sh_git(self.path, "checkout", "-q", "-b", "other")
        with self.assertRaisesRegex(UntwineError, "main"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)

    def test_resumes_an_interrupted_publish(self):
        self.strip_markers(commit=True)
        real = add.gitutil.run

        def failing_push(repo, *args, **kw):
            if args[:1] == ("push",):
                raise UntwineError("network down")
            return real(repo, *args, **kw)

        with mock.patch("untwine_cli.add.gitutil.run", side_effect=failing_push):
            with self.assertRaisesRegex(UntwineError, "network down"):
                add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        url = add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        self.assertEqual(sh_git(self.gh.origins["untwine/pxr-qux"], "rev-parse", "refs/heads/main"),
                         gitutil.rev(self.path, "main"))
        self.assertTrue(url)
        with self.assertRaisesRegex(UntwineError, "already published"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
