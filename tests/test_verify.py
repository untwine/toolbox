import io
from contextlib import redirect_stdout

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, write
from untwine_cli import cli, verify


@needs_filter_repo
class VerifyTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp)
        self.repo = self.world.m.repos["pxr-foo"]

    def checks(self) -> set[str]:
        return {f.check for f in verify.run_checks(self.world.clone, self.world.m, self.repo)}

    def mutate(self, rel: str, old: str, new: str) -> None:
        path = self.world.clone / rel
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_conforming_repo_passes(self):
        self.assertEqual(verify.run_checks(self.world.clone, self.world.m, self.repo), [])

    def test_each_check_fires(self):
        cases = [
            ("quoted-include", "src/pxr/foo/bar.h", "#include <pxr/arch/api.h>", '#include "pxr/arch/api.h"'),
            ("old-include-path", "src/pxr/foo/bar.h", "#include <pxr/arch/api.h>", "#include <pxr/base/arch/api.h>"),
            ("namespace-macro", "src/pxr/foo/bar.h", "FOO_NAMESPACE_OPEN_SCOPE", "PXR_NAMESPACE_OPEN_SCOPE"),
            ("notice", "NOTICE.txt", "modified, standalone redistribution", "copy"),
            ("cxx-standard", "src/CMakeLists.txt", "cxx_std_17", "cxx_std_14"),
            ("install-dir", "src/CMakeLists.txt", "RUNTIME DESTINATION ${CMAKE_INSTALL_LIBDIR}", "RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}"),
            ("exact-version", "CMakeLists.txt", " EXACT REQUIRED", " REQUIRED"),
            ("conan", "conanfile.py", '"cmake_find_mode", "none"', '"cmake_find_mode", "both"'),
            ("deps", "conanfile.py", 'self.requires("pxr-arch/26.8")', 'self.requires("pxr-gf/26.8")'),
            ("python-forward", "conanfile.py", 'self.options["pxr-boost"].python_version', "pass  #"),
            ("bridge", "src/pxr/foo/pxr.h.in", "<pxr/arch/pxr.h>", "<string>"),
            ("gitignore", ".gitignore", "/build/", "build/"),
            ("python-pin", "pyproject.toml", '"pxr-arch==26.8.*"', '"pxr-arch==26.8"'),
        ]
        for check, rel, old, new in cases:
            with self.subTest(check):
                original = (self.world.clone / rel).read_text()
                self.mutate(rel, old, new)
                self.assertIn(check, self.checks())
                (self.world.clone / rel).write_text(original)

    def test_category_named_library_paths_are_not_old(self):
        self.mutate("src/pxr/foo/bar.h", "#include <pxr/arch/api.h>", "#include <pxr/usd/stage.h>")
        self.assertNotIn("old-include-path", self.checks())

    def test_root_sources_ci_and_markers(self):
        write(self.world.clone, {
            "stray.cpp": "x\n",
            ".github/workflows/windows.yml": "on:\n  schedule:\n    - cron: '0 0 * * *'\njobs:\n  t:\n    steps:\n      - uses: actions/checkout@v6\n      - run: Add-MpPreference -ExclusionPath x\n",
            "src/pxr/foo/todo.h": "// TODO(untwine): review\n",
        })
        commit(self.world.clone, {}, "stray files")
        self.assertTrue({"root-source", "ci", "todo"} <= self.checks())

    def test_history_rejects_ai_trailers(self):
        commit(self.world.clone, {"x.txt": "x\n"}, "Tweak.\n\nCo-Authored-By: Someone <a@b.c>")
        self.assertIn("ai-attribution", {f.check for f in verify.check_history(self.world.clone, self.repo, "open-usd", "HEAD")})

    def test_diff_report_lists_fix_only(self):
        rows = verify.diff_report(self.world.clone, self.repo)
        self.assertEqual([(rel, ws) for rel, _, ws in rows], [("src/pxr/foo/bar.h", False)])

    def test_cli_verify(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(self.world.manifest_path), "verify", "pxr-foo", "--diff"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-foo: ok", out.getvalue())
        self.assertIn("src/pxr/foo/bar.h", out.getvalue())
