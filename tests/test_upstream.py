import textwrap

from tests.helpers import TempTest, commit, init, needs_filter_repo, sh_git
from untwine_cli import gitutil, upstream
from untwine_cli.manifest import Repo

TF = textwrap.dedent("""\
    pxr_library(tf
        LIBRARIES
            arch
            ${WINLIBS}
            TBB::tbb

        PUBLIC_CLASSES
            token

        PYMODULE_CPPFILES
            module.cpp
    )
    """)
TS = textwrap.dedent("""\
    set(libs
        vt
        gf
        tf
    )

    pxr_library(ts
        LIBRARIES
            ${libs}
    )
    """)
WORK = textwrap.dedent("""\
    if (use_tbb)
        set(work_impl_libraries TBB::tbb)
    else()
        set(work_impl_target "${PXR_WORK_IMPL}::${PXR_WORK_IMPL}")
    endif()

    pxr_library(work
        LIBRARIES
            tf
            trace
            # libWork still depends on tbb for concurrent containers even
            # when using a custom implementation.
            TBB::tbb
            ${work_impl_target}

        PYMODULE_FILES
            __init__.py
    )
    """)
ARCH = textwrap.dedent("""\
    pxr_library(arch
        LIBRARIES
            ${CMAKE_DL_LIBS}
            ${ARCH_PLATFORM_LIBS}

        PUBLIC_CLASSES
            align
    )
    """)
KNOWN = {"arch", "tf", "gf", "vt", "trace", "work", "js", "boost"}


class ParseTest(TempTest):
    def test_real_declarations(self):
        cases = {
            "tf": (TF, ("arch",), True, True),
            "ts": (TS, ("vt", "gf", "tf"), False, False),
            "work": (WORK, ("tf", "trace"), True, True),
            "arch": (ARCH, (), False, False),
        }
        for name, (text, deps, tbb, python) in cases.items():
            with self.subTest(name):
                library = upstream.parse_pxr_library(text)
                self.assertEqual(library.name, name)
                self.assertEqual(library.has_python, python)
                derived = upstream.derive_deps(library, KNOWN)
                self.assertEqual(derived, upstream.Dependencies(deps, tbb))

    def test_unknown_entry_is_an_error(self):
        library = upstream.parse_pxr_library(TF.replace("arch", "mystery"))
        with self.assertRaisesRegex(upstream.UpstreamError, "mystery"):
            upstream.derive_deps(library, KNOWN)

    def test_missing_declaration(self):
        with self.assertRaises(upstream.UpstreamError):
            upstream.parse_pxr_library("project(x)\n")

    def test_expected_deps(self):
        repo = Repo("pxr-js", upstream="pxr/base/js", deps=("arch", "tf"), extra_deps={"arch": "why"})
        self.assertEqual(upstream.expected_deps(repo, upstream.Dependencies(("tf",), False)), {"arch", "tf"})
        repo = Repo("pxr-tf", upstream="pxr/base/tf", python="optional", deps=("arch", "boost"))
        self.assertEqual(upstream.expected_deps(repo, upstream.Dependencies(("arch",), True)), {"arch", "boost"})


@needs_filter_repo
class FilterTest(TempTest):
    def test_filter_and_import(self):
        source = init(self.tmp / "OpenUSD")
        commit(source, {"pxr/base/foo/a.h": "a\n", "pxr/base/bar/b.h": "b\n"}, "one")
        sh_git(source, "tag", "v26.08")
        mirror = self.tmp / "mirror.git"
        sh_git(self.tmp, "clone", "-q", "--mirror", str(source), str(mirror))
        target = init(self.tmp / "pxr-foo")
        scratch = upstream.filter_subtree(mirror, "v26.08", "pxr/base/foo", self.tmp / "scratch")
        sha = upstream.import_filtered(target, scratch, "refs/untwine/v26.08/upstream")
        self.assertFalse(scratch.exists())
        self.assertEqual(gitutil.paths(target, sha), {"a.h"})
        with self.assertRaises(upstream.UpstreamError):
            upstream.filter_subtree(mirror, "v99.99", "pxr/base/foo", self.tmp / "scratch")
