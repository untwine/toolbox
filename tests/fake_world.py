"""Throwaway OpenUSD upstream, pxr-foo repository, and origin for tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tests.helpers import commit, init, sh_git
from untwine_cli import manifest, transform, upstream

LICENSE = textwrap.dedent("""\
    //
    // Copyright 2026 Pixar
    //
    // Licensed under the terms set forth in the LICENSE.txt file.
    //
    """)
BAR_H = LICENSE + textwrap.dedent("""\
    #ifndef PXR_BASE_FOO_BAR_H
    #define PXR_BASE_FOO_BAR_H

    #include "pxr/pxr.h"
    #include "pxr/base/arch/api.h"

    PXR_NAMESPACE_OPEN_SCOPE

    int FooBar();

    PXR_NAMESPACE_CLOSE_SCOPE

    #endif
    """)
BAR_CPP = LICENSE + textwrap.dedent("""\

    #include "pxr/pxr.h"
    #include "pxr/base/foo/bar.h"

    PXR_NAMESPACE_OPEN_SCOPE

    int FooBar() { return 1; }

    PXR_NAMESPACE_CLOSE_SCOPE
    """)
OLD_H = LICENSE + '#include "pxr/pxr.h"\n'
MODULE_CPP = LICENSE + '\n#include "pxr/pxr.h"\n\nPXR_NAMESPACE_USING_DIRECTIVE\n'
TEST_PY = "#!/pxrpythonsubst\n#\n# Copyright 2026 Pixar\n\nfrom pxr import Foo\n"
CMAKE = textwrap.dedent("""\
    set(PXR_PREFIX pxr/base)
    set(PXR_PACKAGE foo)

    pxr_library(foo
        LIBRARIES
            arch
            TBB::tbb

        PUBLIC_CLASSES
            bar

        PUBLIC_HEADERS
            old.h

        PYMODULE_CPPFILES
            module.cpp
    )
    """)
ARCH_CMAKE = "pxr_library(arch\n    LIBRARIES\n        ${CMAKE_DL_LIBS}\n)\n"
MANIFEST = textwrap.dedent("""\
    workspace = ".."
    upstream_url = "{url}"
    github_org = "untwine"

    [release]
    openusd = "v26.08"
    tbb = "2023.1.0"

    [repos.pxr-arch]
    upstream = "pxr/base/arch"

    [repos.pxr-tf]
    upstream = "pxr/base/tf"
    deps = ["arch"]

    [repos.pxr-boost]
    kind = "special"
    upstream = "pxr/external/boost"
    python = "required"

    [repos.pxr-foo]
    upstream = "pxr/base/foo"
    python = "optional"
    deps = ["arch", "boost"]
    """)
RESTRUCTURE_MESSAGE = textwrap.dedent("""\
    Restructure the 'foo' library as a standalone package.

    - Isolate the 'foo' module from the OpenUSD repository;
    - Update include directives to use the new header prefix path;
    - Add identification and licensing information mandated by OpenUSD.
    """)
NOTICE = ("Copyright Pixar.\n\nThis repository is a modified, standalone redistribution of the 'foo'\n"
          "library from Pixar's OpenUSD, restructured for independent building and\n"
          "packaging. See the git history for details of the changes made.\n")
PXR_H_IN = "#ifndef PXR_FOO_PXR_H\n#define PXR_FOO_PXR_H\n#include <pxr/arch/pxr.h>\n#endif\n"


def upstream_tree(scenarios: frozenset[str] = frozenset()) -> dict[str, str]:
    foo = "pxr/base/foo/"
    files = {
        "pxr/base/arch/CMakeLists.txt": ARCH_CMAKE, foo + "CMakeLists.txt": CMAKE,
        foo + "bar.h": BAR_H, foo + "bar.cpp": BAR_CPP, foo + "old.h": OLD_H,
        foo + "module.cpp": MODULE_CPP, foo + "testenv/testFooBar.py": TEST_PY,
    }
    if "upstream-edit" in scenarios:
        files[foo + "bar.cpp"] = files[foo + "bar.cpp"].replace("return 1", "return 2")
    if "include-conflict" in scenarios:
        files[foo + "bar.cpp"] = files[foo + "bar.cpp"].replace(
            '#include "pxr/base/foo/bar.h"\n', '#include "pxr/base/foo/bar.h"\n#include "pxr/base/arch/defines.h"\n')
    if "fix-conflict" in scenarios:
        files[foo + "bar.h"] = files[foo + "bar.h"].replace("int FooBar();", "int FooBar(int);")
    if "fix-upstreamed" in scenarios:
        files[foo + "bar.h"] = files[foo + "bar.h"].replace("int FooBar();", "int FooBar() noexcept;")
    if "new-file" in scenarios:
        files[foo + "baz.h"] = LICENSE + '#include "pxr/pxr.h"\n'
    if "dep-change" in scenarios:
        files[foo + "CMakeLists.txt"] = CMAKE.replace("        arch\n", "        arch\n        tf\n")
    if "delete" in scenarios:
        del files[foo + "old.h"]
    return files


def config_files(tag: str) -> dict[str, str]:
    cmake_version, short = manifest.version_forms(tag)
    return {
        "CMakeLists.txt": textwrap.dedent(f"""\
            cmake_minimum_required(VERSION 3.23...4.2)
            project(pxr-foo
                VERSION {cmake_version}
                LANGUAGES C CXX
            )

            find_package(pxr-arch {cmake_version} EXACT REQUIRED)
            add_subdirectory(src)
            """),
        "src/CMakeLists.txt": textwrap.dedent("""\
            add_library(foo pxr/foo/bar.cpp)
            target_compile_features(foo PUBLIC cxx_std_17)
            install(TARGETS foo
                RUNTIME DESTINATION ${CMAKE_INSTALL_LIBDIR}
                LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
                ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
            )
            """),
        "cmake/pxr-foo-config.cmake.in": f"find_dependency(pxr-arch {cmake_version} EXACT REQUIRED)\n",
        "conanfile.py": textwrap.dedent(f'''\
            from conan import ConanFile


            class PxrFooConan(ConanFile):
                name = "pxr-foo"
                version = "{short}"
                options = {{"shared": [True, False], "python_version": ["ANY", None]}}
                default_options = {{"shared": True, "python_version": None}}

                def configure(self):
                    if self.options.python_version:
                        self.options["pxr-boost"].python_version = self.options.python_version

                def requirements(self):
                    self.requires("pxr-arch/{short}")
                    if self.options.python_version:
                        self.requires("pxr-boost/{short}")

                def package_info(self):
                    self.cpp_info.set_property("cmake_find_mode", "none")
                    self.cpp_info.builddirs = ["share/cmake/pxr-foo"]
                    self.cpp_info.set_property("system_package_version", "{cmake_version}")
            '''),
        "pyproject.toml": textwrap.dedent(f"""\
            [project]
            name = "pxr-foo"
            version = "{short}"
            dependencies = [
                "pxr-arch=={short}.*",
                "pxr-boost=={short}.*",
                "pxr-tbb==2023.1.0.*",
            ]
            """),
        ".gitignore": "CMakeUserPresets.json\n/build/\n__pycache__/\n",
        ".github/workflows/linux.yml": textwrap.dedent("""\
            on:
              push:
                branches: [ dev, main ]
              workflow_dispatch:
            jobs:
              test:
                runs-on: ubuntu-latest
                env:
                  CONAN_REMOTE_URL: https://conan.cloudsmith.io/untwine/conan/
                steps:
                  - uses: actions/checkout@v7
            """),
        "README.md": f"Built from OpenUSD [{tag}](https://github.com/PixarAnimationStudios/OpenUSD/releases/tag/{tag}).\n",
    }


class FakeWorld:
    """OpenUSD with tags v26.08 and v26.11, and pxr-foo built at v26.08 with an origin."""

    def __init__(self, tmp: Path, scenarios=(), *, with_fix: bool = True):
        self.tmp = tmp
        self.upstream = init(tmp / "OpenUSD")
        commit(self.upstream, upstream_tree(), "Release 26.08")
        sh_git(self.upstream, "tag", "v26.08")
        commit(self.upstream, upstream_tree(frozenset(scenarios)), "Release 26.11", replace=True)
        sh_git(self.upstream, "tag", "v26.11")
        self.workspace = tmp / "ws"
        toolbox = self.workspace / "toolbox"
        toolbox.mkdir(parents=True)
        self.manifest_path = toolbox / "untwine.toml"
        self.manifest_path.write_text(MANIFEST.format(url=self.upstream))
        self.m = manifest.load(self.manifest_path)
        self.origin = init(tmp / "remotes" / "pxr-foo.git", bare=True)
        self.clone = self.workspace / "pxr-foo"
        self._build(with_fix)

    def _build(self, with_fix: bool) -> None:
        clone = init(self.clone)
        mirror = upstream.ensure_mirror(self.m)
        scratch = upstream.filter_subtree(mirror, "v26.08", "pxr/base/foo", self.m.state_dir / "tmp")
        upstream.import_filtered(clone, scratch, "refs/heads/open-usd")
        sh_git(clone, "checkout", "-q", "-B", "main", "open-usd")
        for path in sh_git(clone, "ls-files").splitlines():
            target, _ = transform.rule_target(transform.DEFAULT_RULES, "foo", path)
            data = (clone / path).read_bytes()
            (clone / target).parent.mkdir(parents=True, exist_ok=True)
            sh_git(clone, "mv", path, target)
            (clone / target).write_bytes(transform.transform_bytes(data, "foo"))
        commit(clone, {"NOTICE.txt": NOTICE, "src/pxr/foo/pxr.h.in": PXR_H_IN}, RESTRUCTURE_MESSAGE)
        commit(clone, config_files("v26.08"), "Add minimal release configuration.")
        if with_fix:
            bar = clone / "src/pxr/foo/bar.h"
            bar.write_text(bar.read_text().replace("int FooBar();", "int FooBar() noexcept;"))
            commit(clone, {}, "bar: mark FooBar noexcept.")
        sh_git(clone, "remote", "add", "origin", str(self.origin))
        sh_git(clone, "push", "-q", "origin", "main", "open-usd")
        sh_git(clone, "fetch", "-q", "origin")
        sh_git(clone, "branch", "-q", "--set-upstream-to=origin/main", "main")
