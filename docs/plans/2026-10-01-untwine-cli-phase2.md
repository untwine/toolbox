# `untwine` CLI Phase 2 Implementation Plan: adding a library

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `untwine add <lib>` creates a new `pxr-<lib>` repository (filtered `open-usd`, generated `Restructure`, release configuration copied from the nearest sibling) and `untwine publish-repo pxr-<lib>` creates and pushes its GitHub repository after confirmation.

**Architecture:** A new `untwine_cli/add.py` module built on phase 1 (`upstream`, `transform`, `verify`, `github`). Everything that can be derived is derived from upstream at the manifest's current release: the subtree path, the direct dependencies (missing ones are reported in the order to add them), and the Python shape. `Restructure` is fully generated. The release configuration is the nearest sibling's current files with names and dependency blocks rewritten; the parts that cannot be derived (source/test lists, `moduleDeps.cpp`) carry `TODO(untwine): review` markers, which `verify` rejects until resolved. A failed `add` removes everything it created.

**Tech Stack:** Python ≥ 3.11 standard library, `git`, `git-filter-repo`, `gh`.

**Spec:** `docs/specs/2026-10-01-untwine-cli-design.md` (section `untwine add`, phase 2)

## Global Constraints

- Everything from the phase 1 plan's Global Constraints applies.
- `add` never touches an existing repository and never writes a remote; only `publish-repo` writes a remote, after confirmation, and only when `verify` is clean.
- Root `CMakeLists.txt` stays at the root in `Restructure` (the release configuration replaces it); everything else moves by `transform.DEFAULT_RULES`.
- `pxr.h.in` bridges every direct dependency except `boost` and `pegtl`; `boost` is bridged under `PXR_PYTHON_SUPPORT_ENABLED`.
- Commit messages follow `docs/history.md`; no trailers.

## Review Focus

1. **Short sibling names** (`ar`, `js`, `tf`) during renaming: must not rewrite unrelated words (`arch`, `json`). Pinned by `test_rename_respects_boundaries` in Task 2.
2. **Python-forwarding lines** in the copied `conanfile.py`: only dependencies that expose `python_version` get one. Pinned by `test_rewrite_dependency_blocks` in Task 2.
3. **A failure halfway through `add`** must leave neither a directory nor a manifest entry. Pinned by `test_failure_cleans_up` in Task 3.
4. **A dependency without a repository** must be reported with the order to add the missing ones, dependencies first. Pinned by `test_missing_dependency_lists_order` in Task 3.
5. **Publishing an unreviewed repository** (markers left): refused before anything remote happens. Pinned by `test_publish_refuses_markers` in Task 4.

---

### Task 1: Upstream sections and fixture libraries

**Files:**
- Modify: `untwine_cli/upstream.py`, `tests/fake_world.py`
- Test: `tests/test_upstream.py`

**Interfaces:**
- Produces: `upstream.PxrLibrary.sections: dict[str, tuple[str, ...]]` (every keyword section, variables expanded); `fake_world.upstream_tree(scenarios, *, extra_libs=False)`; `FakeWorld(tmp, scenarios=(), *, with_fix=True, extra_libs=False)` adding `pxr/base/qux` (deps `arch`, `foo`, Python), `pxr/base/zed` (deps `arch`, `nope`), `pxr/base/nope` (deps `arch`), and `pxr/base/dup` + `pxr/usd/dup`.

- [ ] **Step 1: Write the failing test** (append to `ParseTest` in `tests/test_upstream.py`)

```python
    def test_sections(self):
        library = upstream.parse_pxr_library(TF)
        self.assertEqual(library.sections["PUBLIC_CLASSES"], ("token",))
        self.assertEqual(library.sections["PYMODULE_CPPFILES"], ("module.cpp",))
        self.assertEqual(upstream.parse_pxr_library(TS).sections["LIBRARIES"], ("vt", "gf", "tf"))
```

- [ ] **Step 2: Run it** — `python3 -m unittest tests.test_upstream -v` — Expected: FAIL with `AttributeError: 'PxrLibrary' object has no attribute 'sections'`

- [ ] **Step 3: Implement**

In `untwine_cli/upstream.py`, change the import to `from dataclasses import dataclass, field`, give `PxrLibrary` a last field `sections: dict[str, tuple[str, ...]] = field(default_factory=dict)`, and replace the return statement of `parse_pxr_library` with:

```python
    expanded = {key: tuple(expand(values)) for key, values in sections.items()}
    has_python = any(expanded.get(key) for key in ("PYMODULE_CPPFILES", "PYMODULE_FILES", "PYTHON_CPPFILES"))
    return PxrLibrary(name, expanded.get("LIBRARIES", ()), has_python, expanded)
```

(and delete the previous `has_python = ...` line).

In `tests/fake_world.py`, add after `ARCH_CMAKE`:

```python
QUX_CMAKE = textwrap.dedent("""\
    pxr_library(qux
        LIBRARIES
            arch
            foo
            TBB::tbb

        PUBLIC_CLASSES
            qux

        PYMODULE_CPPFILES
            module.cpp
            wrapQux.cpp

        PYMODULE_FILES
            __init__.py
    )
    """)
QUX_H = LICENSE + textwrap.dedent("""\
    #ifndef PXR_BASE_QUX_QUX_H
    #define PXR_BASE_QUX_QUX_H

    #include "pxr/pxr.h"
    #include "pxr/base/foo/bar.h"

    PXR_NAMESPACE_OPEN_SCOPE

    int Qux();

    PXR_NAMESPACE_CLOSE_SCOPE

    #endif
    """)


def extra_libraries() -> dict[str, str]:
    qux = "pxr/base/qux/"
    return {
        qux + "CMakeLists.txt": QUX_CMAKE, qux + "qux.h": QUX_H,
        qux + "qux.cpp": LICENSE + '\n#include "pxr/pxr.h"\n#include "pxr/base/qux/qux.h"\n',
        qux + "module.cpp": MODULE_CPP, qux + "wrapQux.cpp": LICENSE + '\n#include "pxr/base/qux/qux.h"\n',
        qux + "__init__.py": "from pxr import Tf\n",
        qux + "testenv/testQux.py": TEST_PY,
        "pxr/base/zed/CMakeLists.txt": "pxr_library(zed\n    LIBRARIES\n        arch\n        nope\n)\n",
        "pxr/base/nope/CMakeLists.txt": "pxr_library(nope\n    LIBRARIES\n        arch\n)\n",
        "pxr/base/dup/CMakeLists.txt": "pxr_library(dup\n    LIBRARIES\n        arch\n)\n",
        "pxr/usd/dup/CMakeLists.txt": "pxr_library(dup\n    LIBRARIES\n        arch\n)\n",
    }
```

Change `upstream_tree` to `def upstream_tree(scenarios: frozenset[str] = frozenset(), *, extra_libs: bool = False) -> dict[str, str]:` and, just before its `return files`, add `if extra_libs: files.update(extra_libraries())`. Change `FakeWorld.__init__` to take `*, with_fix: bool = True, extra_libs: bool = False` and pass `extra_libs=extra_libs` to both `upstream_tree(...)` calls.

- [ ] **Step 4: Run** — `python3 -m unittest discover -s tests -t .` — Expected: OK

- [ ] **Step 5: Commit** — `git add untwine_cli/upstream.py tests/fake_world.py tests/test_upstream.py && git commit -m "Expose pxr_library() sections and add fixture libraries."`

---

### Task 2: Derivation and rewriting helpers

**Files:**
- Create: `untwine_cli/add.py`
- Test: `tests/test_add_helpers.py`

**Interfaces:**
- Consumes: `upstream.parse_pxr_library/derive_deps/ensure_mirror`, `gitutil`, `transform`, `manifest`.
- Produces: `AddError(UntwineError)`; `locate(mirror, tag, lib) -> str`; `upstream_library(mirror, tag, path) -> PxrLibrary`; `missing_order(m, mirror, tag, missing) -> list[str]`; `derive(m, mirror, tag, lib, library) -> Dependencies`; `nearest_sibling(m, deps: set[str], python: str) -> Repo`; `rename(text, old, new) -> str`; `rewrite_deps(text, old_deps, new_deps, python_of) -> str`; `pxr_h_in(lib, deps) -> str`; `restructure_message(lib, deps, dropped_subst) -> str`; `config_message(python: bool) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_add_helpers.py`:

```python
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo
from untwine_cli import add, upstream


class RewriteTest(TempTest):
    def test_rename_respects_boundaries(self):
        text = ('project(pxr-ar)\nadd_library(ar)\n#include <pxr/ar/ar.h>\nArResolver pyAr AR_API PXR_AR_H\n'
                'arch archive Arch ARCH json Json\n')
        self.assertEqual(add.rename(text, "ar", "pcp"),
                         'project(pxr-pcp)\nadd_library(pcp)\n#include <pxr/pcp/pcp.h>\nPcpResolver pyPcp PCP_API PXR_PCP_H\n'
                         'arch archive Arch ARCH json Json\n')

    def test_rewrite_dependency_blocks(self):
        text = (
            'find_package(pxr-arch 0.26.8 EXACT REQUIRED)\n'
            'find_package(pxr-tf 0.26.8 EXACT REQUIRED)\n'
            'if(BUILD_PYTHON_BINDINGS)\n'
            '    find_package(pxr-boost 0.26.8 EXACT REQUIRED)\n'
            'endif()\n'
            '        if self.options.python_version:\n'
            '            self.options["pxr-tf"].python_version = self.options.python_version\n'
            '            self.options["pxr-boost"].python_version = self.options.python_version\n'
            '    "pxr-arch-dev==26.8.*",\n'
            '    "pxr-tf-dev==26.8.*",\n'
            '    "pxr-boost-dev==26.8.*",\n'
        )
        python_of = {"arch": False, "tf": True, "vt": True, "boost": True}
        self.assertEqual(add.rewrite_deps(text, ["arch", "tf", "boost"], ["arch", "vt", "boost"], python_of), (
            'find_package(pxr-arch 0.26.8 EXACT REQUIRED)\n'
            'find_package(pxr-vt 0.26.8 EXACT REQUIRED)\n'
            'if(BUILD_PYTHON_BINDINGS)\n'
            '    find_package(pxr-boost 0.26.8 EXACT REQUIRED)\n'
            'endif()\n'
            '        if self.options.python_version:\n'
            '            self.options["pxr-vt"].python_version = self.options.python_version\n'
            '            self.options["pxr-boost"].python_version = self.options.python_version\n'
            '    "pxr-arch-dev==26.8.*",\n'
            '    "pxr-vt-dev==26.8.*",\n'
            '    "pxr-boost-dev==26.8.*",\n'
        ))

    def test_pxr_h_in(self):
        text = add.pxr_h_in("pcp", ["arch", "sdf", "pegtl", "boost"])
        self.assertIn("#include <pxr/arch/pxr.h>\n#include <pxr/sdf/pxr.h>\n", text)
        self.assertNotIn("pegtl", text)
        self.assertIn("#ifdef PXR_PYTHON_SUPPORT_ENABLED\n#include <pxr/boost/python/common.hpp>\n#endif\n", text)
        self.assertIn("    using namespace SDF_INTERNAL_NS;\n#ifdef PXR_PYTHON_SUPPORT_ENABLED\n"
                      "    using namespace BOOST_INTERNAL_NS;\n#endif\n}", text)
        self.assertIn("#define PCP_VERSION (PCP_MAJOR_VERSION * 10000 \\\n"
                      "                   + PCP_MINOR_VERSION * 100   \\\n", text)
        self.assertNotIn("namespace PCP_INTERNAL_NS {\n    using", add.pxr_h_in("pcp", []))

    def test_pxr_h_in_matches_a_real_one(self):
        expected = (
            "#define JS_VERSION (JS_MAJOR_VERSION * 10000 \\\n"
            "                  + JS_MINOR_VERSION * 100   \\\n"
            "                  + JS_PATCH_VERSION)\n"
        )
        self.assertIn(expected, add.pxr_h_in("js", ["arch", "tf"]))

    def test_messages(self):
        message = add.restructure_message("pcp", ["arch"], dropped_subst=True)
        self.assertTrue(message.startswith("Restructure the 'pcp' library as a standalone package.\n\n"))
        self.assertIn("internal-namespace imports", message)
        self.assertIn("pxrpythonsubst", message)
        self.assertTrue(message.rstrip().endswith("mandated by OpenUSD."))
        self.assertIn("bound to the library", add.restructure_message("pcp", [], dropped_subst=False))
        self.assertIn("moduleDeps.cpp", add.config_message(python=True))
        self.assertTrue(add.config_message(python=False).rstrip().endswith("a .gitignore."))


@needs_filter_repo
class DeriveTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)
        self.mirror = upstream.ensure_mirror(self.world.m)

    def test_locate(self):
        self.assertEqual(add.locate(self.mirror, "v26.08", "qux"), "pxr/base/qux")
        with self.assertRaisesRegex(add.AddError, "pxr/base/dup"):
            add.locate(self.mirror, "v26.08", "dup")
        with self.assertRaisesRegex(add.AddError, "nothing"):
            add.locate(self.mirror, "v26.08", "absent")

    def test_derive_and_missing(self):
        library = add.upstream_library(self.mirror, "v26.08", "pxr/base/qux")
        self.assertEqual(add.derive(self.world.m, self.mirror, "v26.08", "qux", library).pxr, ("arch", "foo"))
        zed = add.upstream_library(self.mirror, "v26.08", "pxr/base/zed")
        with self.assertRaisesRegex(add.AddError, "in this order: nope$"):
            add.derive(self.world.m, self.mirror, "v26.08", "zed", zed)

    def test_nearest_sibling(self):
        self.assertEqual(add.nearest_sibling(self.world.m, {"arch", "foo", "boost"}, "optional").name, "pxr-foo")
```

- [ ] **Step 2: Run** — `python3 -m unittest tests.test_add_helpers -v` — Expected: FAIL with `ImportError: cannot import name 'add'`

- [ ] **Step 3: Write `untwine_cli/add.py`**

```python
"""`untwine add` and `untwine publish-repo`: create a standalone repository for an OpenUSD library."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, github, gitutil, manifest, transform, upstream, verify
from .manifest import Manifest, Repo

TODO = "TODO(untwine): review"
NO_BRIDGE = ("boost", "pegtl")
NOTICE_PARAGRAPH = ("This repository is a modified, standalone redistribution of the '{lib}'\n"
                    "library from Pixar's OpenUSD, restructured for independent building and\n"
                    "packaging. See the git history for details of the changes made.\n")
PXR_H_LICENSE = ("// Copyright 2016 Pixar\n//\n"
                 "// Licensed under the terms set forth in the LICENSE.txt file available at\n"
                 "// https://openusd.org/license.\n//\n")
_IDENTIFIER = re.compile(r"[a-z][A-Za-z0-9]*")


class AddError(UntwineError):
    pass


def locate(mirror: Path, tag: str, lib: str) -> str:
    categories = gitutil.git(mirror, "ls-tree", "-d", "--name-only", f"{tag}:pxr").split()
    hits = [f"pxr/{c}/{lib}" for c in categories
            if gitutil.blob(mirror, tag, f"pxr/{c}/{lib}/CMakeLists.txt") is not None]
    if len(hits) != 1:
        raise AddError(f"cannot locate '{lib}' in OpenUSD {tag}: found {hits or 'nothing'}; pass --upstream")
    return hits[0]


def upstream_library(mirror: Path, tag: str, path: str) -> upstream.PxrLibrary:
    data = gitutil.blob(mirror, tag, f"{path}/CMakeLists.txt")
    if data is None:
        raise AddError(f"{path}/CMakeLists.txt not found in OpenUSD {tag}")
    return upstream.parse_pxr_library(data.decode())


def missing_order(m: Manifest, mirror: Path, tag: str, missing: list[str]) -> list[str]:
    """Libraries without a repository, dependencies first."""
    known = {r.lib for r in m.libraries()}
    order: list[str] = []
    seen: set[str] = set()

    def visit(lib: str) -> None:
        if lib in seen or lib in known:
            return
        seen.add(lib)
        for entry in upstream_library(mirror, tag, locate(mirror, tag, lib)).libraries:
            if _IDENTIFIER.fullmatch(entry):
                visit(entry)
        order.append(lib)

    for lib in missing:
        visit(lib)
    return order


def derive(m: Manifest, mirror: Path, tag: str, lib: str, library: upstream.PxrLibrary) -> upstream.Dependencies:
    known = {r.lib for r in m.libraries()}
    unknown = [e for e in library.libraries if _IDENTIFIER.fullmatch(e) and e not in known]
    if unknown:
        raise AddError(f"'{lib}' depends on libraries without a repository yet; add them first, in this order: "
                       + ", ".join(missing_order(m, mirror, tag, unknown)))
    return upstream.derive_deps(library, known)


def nearest_sibling(m: Manifest, deps: set[str], python: str) -> Repo:
    candidates = [r for r in m.libraries() if r.kind == "library" and m.repo_path(r.name).is_dir()]
    if not candidates:
        raise AddError("no existing library repository to copy the release configuration from")
    return min(candidates, key=lambda r: (r.has_python != (python != "none"), len(set(r.deps) ^ deps),
                                          len(r.test_deps), r.name))


def rename(text: str, old: str, new: str) -> str:
    cap_old, cap_new = old[:1].upper() + old[1:], new[:1].upper() + new[1:]
    text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(old)}(?![a-z0-9])", new, text)
    text = re.sub(rf"(?<![A-Z]){re.escape(cap_old)}(?![a-z])", cap_new, text)
    return re.sub(rf"(?<![A-Z]){re.escape(old.upper())}(?![A-Z])", new.upper(), text)


def _dep_of(line: str, names: set[str]) -> str | None:
    found = {d for d in names if re.search(rf"pxr-{d}\b|pxr::{d}\b|<pxr/{d}/pxr\.h>|\b{d.upper()}_INTERNAL_NS\b", line)}
    return found.pop() if len(found) == 1 else None


def _swap(line: str, old: str, new: str) -> str:
    line = re.sub(rf"pxr-{old}\b", f"pxr-{new}", line)
    line = re.sub(rf"pxr::{old}\b", f"pxr::{new}", line)
    line = line.replace(f"<pxr/{old}/", f"<pxr/{new}/")
    return re.sub(rf"\b{old.upper()}_INTERNAL_NS\b", f"{new.upper()}_INTERNAL_NS", line)


def rewrite_deps(text: str, old_deps: list[str], new_deps: list[str], python_of: dict[str, bool]) -> str:
    """Rewrite each run of consecutive per-dependency lines for the new dependency list."""
    names = set(old_deps)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        dep = _dep_of(lines[i], names)
        forward = "python_version" in lines[i]
        if dep is None or (dep == "boost" and not forward):
            out.append(lines[i])
            i += 1
            continue
        block = []
        while i < len(lines):
            d = _dep_of(lines[i], names)
            if d is None or ("python_version" in lines[i]) != forward or (d == "boost" and not forward):
                break
            block.append((d, lines[i]))
            i += 1
        template_dep, template = block[0]
        targets = [d for d in new_deps if python_of[d]] if forward else [d for d in new_deps if d != "boost"]
        out += [_swap(template, template_dep, d) for d in targets]
    return "".join(out)


def pxr_h_in(lib: str, deps: list[str]) -> str:
    up = lib.upper()
    bridged = [d for d in deps if d not in NO_BRIDGE]
    python = "boost" in deps
    includes = "".join(f"#include <pxr/{d}/pxr.h>\n" for d in bridged)
    if python:
        includes += "\n#ifdef PXR_PYTHON_SUPPORT_ENABLED\n#include <pxr/boost/python/common.hpp>\n#endif\n"
    imports = "".join(f"    using namespace {d.upper()}_INTERNAL_NS;\n" for d in bridged)
    if python:
        imports += "#ifdef PXR_PYTHON_SUPPORT_ENABLED\n    using namespace BOOST_INTERNAL_NS;\n#endif\n"
    indent = " " * (len(f"#define {up}_VERSION (") - 2)
    parts = [PXR_H_LICENSE, f"\n#ifndef PXR_{up}_H\n#define PXR_{up}_H\n\n"]
    if includes:
        parts.append(includes + "\n")
    parts.append(
        f"#define {up}_MAJOR_VERSION @PROJECT_VERSION_MAJOR@\n"
        f"#define {up}_MINOR_VERSION @PROJECT_VERSION_MINOR@\n"
        f"#define {up}_PATCH_VERSION @PROJECT_VERSION_PATCH@\n\n"
        f"#define {up}_VERSION ({up}_MAJOR_VERSION * 10000 \\\n"
        f"{indent}+ {up}_MINOR_VERSION * 100   \\\n"
        f"{indent}+ {up}_PATCH_VERSION)\n\n"
        "#define PXR_NS pxr\n"
        f"#define {up}_INTERNAL_NS pxrInternal_v@PROJECT_VERSION_MAJOR@_@PROJECT_VERSION_MINOR@_@PROJECT_VERSION_PATCH@__pxrReserved__\n"
        "#define PXR_NS_GLOBAL ::PXR_NS\n\n"
        f"namespace {up}_INTERNAL_NS {{ }}\n\n"
        f"namespace PXR_NS {{\n    using namespace {up}_INTERNAL_NS;\n}}\n\n")
    if imports:
        parts.append(f"// Import direct dependencies into {lib}'s internal namespace.\n"
                     "// This keeps resolution correct if package versions ever drift.\n"
                     f"namespace {up}_INTERNAL_NS {{\n{imports}}}\n\n")
    parts.append(f"#define {up}_NAMESPACE_OPEN_SCOPE   namespace {up}_INTERNAL_NS {{\n"
                 f"#define {up}_NAMESPACE_CLOSE_SCOPE  }}\n"
                 f"#define {up}_NAMESPACE_USING_DIRECTIVE using namespace PXR_NS;\n\n"
                 f"#endif // PXR_{up}_H\n")
    return "".join(parts)


def restructure_message(lib: str, deps: list[str], *, dropped_subst: bool) -> str:
    bullets = [f"Isolate the '{lib}' module from the OpenUSD repository;"]
    if [d for d in deps if d not in ("pegtl",)]:
        bullets.append("Add a 'pxr.h.in' customized for the library, with renamed\n  namespace/version macros and "
                       "internal-namespace imports for its\n  dependencies;")
    else:
        bullets.append(f"Integrate a customized 'pxr.h.in' into the '{lib}' library, with renamed\n  namespace "
                       "and version macros bound to the library;")
    bullets.append("Update include directives to use the new header prefix path;")
    if dropped_subst:
        bullets.append("Drop the '#!/pxrpythonsubst' shebang from Python test scripts, since\n  the standalone "
                       "build no longer substitutes it for the interpreter path;")
    bullets.append("Add identification and licensing information mandated by OpenUSD.")
    return f"Restructure the '{lib}' library as a standalone package.\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n"


def config_message(python: bool) -> str:
    bullets = [
        "Configure CMake to build and test the library;",
        "Package the library for both PyPI (runtime and development wheels via\n  pyproject.toml) and Conan (recipe "
        "and release workflow publishing to\n  the shared 'untwine' Cloudsmith remote), so it can be consumed from "
        "Python\n  or from plain CMake/Conan projects;",
        "Add GitHub CI for major platforms and a .gitignore;",
    ]
    if python:
        bullets.append("Restore moduleDeps.cpp to explicitly register Python module deps;")
    bullets[-1] = bullets[-1][:-1] + "."
    return "Add minimal release configuration.\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n"
```

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_add_helpers -v` — Expected: 8 tests PASS

- [ ] **Step 5: Commit** — `git add untwine_cli/add.py tests/test_add_helpers.py && git commit -m "Add the derivation and rewriting helpers for untwine add."`

---

### Task 3: `untwine add`

**Files:**
- Modify: `untwine_cli/add.py`, `untwine_cli/cli.py`
- Test: `tests/test_add.py`

**Interfaces:**
- Produces: `add.add(m, lib, *, upstream_path=None, python=None) -> tuple[Path, Repo]` (new repository path, sibling used); CLI `add <lib> [--upstream PATH] [--python optional|required]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_add.py`:

```python
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

    def test_python_none_has_no_boost(self):
        path, _ = add.add(self.world.m, "nope")
        repo = manifest.load(self.world.manifest_path).repos["pxr-nope"]
        self.assertEqual((repo.python, repo.deps), ("none", ("arch",)))

    def test_cli_add(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(self.world.manifest_path), "add", "qux"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-qux", out.getvalue())
        self.assertIn("TODO(untwine)", out.getvalue())
```

- [ ] **Step 2: Run** — `python3 -m unittest tests.test_add -v` — Expected: FAIL with `AttributeError: module 'untwine_cli.add' has no attribute 'add'`

- [ ] **Step 3: Implement** (append to `untwine_cli/add.py`)

```python
def _restructure(m: Manifest, target: Path, lib: str, mirror: Path, tag: str, deps: list[str]) -> None:
    dropped_subst = False
    for path in sorted(gitutil.paths(target, "HEAD")):
        if path == "CMakeLists.txt":
            continue
        destination, _ = transform.rule_target(transform.DEFAULT_RULES, lib, path)
        data = gitutil.blob(target, "HEAD", path)
        dropped_subst |= data.startswith(b"#!/pxrpythonsubst")
        (target / destination).parent.mkdir(parents=True, exist_ok=True)
        if destination != path:
            gitutil.run(target, "mv", "--", path, destination)
        (target / destination).write_bytes(transform.transform_bytes(data, lib))
    notice = gitutil.blob(mirror, tag, "NOTICE.txt")
    paragraph = NOTICE_PARAGRAPH.format(lib=lib)
    (target / "NOTICE.txt").write_text((notice.decode().rstrip("\n") + "\n\n" if notice else "") + paragraph)
    license_text = gitutil.blob(mirror, tag, "LICENSE.txt")
    if license_text is not None:
        (target / "LICENSE.txt").write_bytes(license_text)
    (target / f"src/pxr/{lib}/pxr.h.in").write_text(pxr_h_in(lib, deps))
    gitutil.run(target, "add", "-A")
    gitutil.run(target, "commit", "--quiet", "-F", "-",
                input=restructure_message(lib, deps, dropped_subst=dropped_subst).encode())


def _config_paths(clone: Path, sibling: Repo) -> list[str]:
    config = verify.find_commit(clone, "open-usd", "main", verify.CONFIG)
    out = gitutil.git(clone, "diff", "--name-status", "--no-renames", f"{config}^", config)
    present = gitutil.paths(clone, "main")
    keep = []
    for line in out.splitlines():
        status, path = line.split("\t", 1)
        if status not in ("A", "M") or path not in present:
            continue
        if path.startswith("test/") and path not in ("test/CMakeLists.txt", "test/testWrapper.py"):
            continue
        if path.startswith("src/") and path not in ("src/CMakeLists.txt", "src/python/CMakeLists.txt") \
                and not path.endswith("/moduleDeps.cpp"):
            continue
        keep.append(path)
    return keep


def _todo_block(sibling: Repo, library: upstream.PxrLibrary) -> str:
    lines = [f"# {TODO}: copied from {sibling.name}; replace the sibling's source, header,",
             "# Python, and test lists with this library's own, declared upstream as:"]
    for key, values in library.sections.items():
        if key != "LIBRARIES" and values:
            lines.append(f"#   {key}: {' '.join(values)}")
    return "\n".join(lines) + "\n\n"


def _configure(m: Manifest, target: Path, lib: str, sibling: Repo, deps: list[str],
               library: upstream.PxrLibrary) -> None:
    clone = m.repo_path(sibling.name)
    python_of = {r.lib: r.has_python for r in m.libraries()}
    old_deps = list(sibling.deps)
    for path in _config_paths(clone, sibling):
        data = gitutil.blob(clone, "main", path)
        destination = target / rename(path, sibling.lib, lib)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            destination.write_bytes(data)
            continue
        text = rewrite_deps(rename(text, sibling.lib, lib), old_deps, deps, python_of)
        if path in ("src/CMakeLists.txt", "test/CMakeLists.txt"):
            text = _todo_block(sibling, library) + text
        elif path.endswith("moduleDeps.cpp"):
            text = f"// {TODO}: reconstruct per docs/python-bindings.md (copied from {sibling.name}).\n" + text
        destination.write_text(text)
    gitutil.run(target, "add", "-A")
    gitutil.run(target, "commit", "--quiet", "-F", "-", input=config_message("boost" in deps).encode())


def _append_manifest(m: Manifest, name: str, path: str, python: str, deps: list[str]) -> None:
    entry = f'\n[repos.{name}]\nupstream = "{path}"\npython = "{python}"\n'
    if deps:
        entry += "deps = [" + ", ".join(f'"{d}"' for d in deps) + "]\n"
    m.path.write_text(m.path.read_text().rstrip("\n") + "\n" + entry)
    manifest.load(m.path)


def add(m: Manifest, lib: str, *, upstream_path: str | None = None, python: str | None = None) -> tuple[Path, Repo]:
    name = f"pxr-{lib}"
    if name in m.repos:
        raise AddError(f"{name} is already in untwine.toml")
    target = m.repo_path(name)
    if target.exists():
        raise AddError(f"{target} already exists")
    tag = m.openusd
    mirror = upstream.ensure_mirror(m)
    path = upstream_path or locate(mirror, tag, lib)
    library = upstream_library(mirror, tag, path)
    if python and python != "none" and not library.has_python:
        raise AddError(f"upstream declares no Python module for '{lib}'")
    shape = python or ("optional" if library.has_python else "none")
    deps = list(derive(m, mirror, tag, lib, library).pxr) + (["boost"] if shape != "none" else [])
    sibling = nearest_sibling(m, set(deps), shape)
    manifest_text = m.path.read_text()
    try:
        target.mkdir(parents=True)
        gitutil.run(target, "init", "--quiet", "-b", "main")
        scratch = upstream.filter_subtree(mirror, tag, path, m.state_dir / "tmp")
        upstream.import_filtered(target, scratch, "refs/heads/open-usd")
        gitutil.run(target, "checkout", "--quiet", "-B", "main", "open-usd")
        _restructure(m, target, lib, mirror, tag, deps)
        _configure(m, target, lib, sibling, deps, library)
        _append_manifest(m, name, path, shape, deps)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        m.path.write_text(manifest_text)
        raise
    return target, sibling
```

In `untwine_cli/cli.py`, insert above `def main`:

```python
def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("lib", help="library name, for example pcp")
    p.add_argument("--upstream", help="subtree path in OpenUSD, when it cannot be located automatically")
    p.add_argument("--python", choices=("optional", "required"), help="override the Python packaging shape")


def _add_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import add
    path, sibling = add.add(m, args.lib, upstream_path=args.upstream, python=args.python)
    repo = manifest.load(m.path).repos[f"pxr-{args.lib}"]
    print(f"created {path} (release configuration copied from {sibling.name})")
    print(f"  upstream {repo.upstream}, python {repo.python}, deps {', '.join(repo.deps) or 'none'}")
    for finding in verify.run_checks(path, manifest.load(m.path), repo):
        print(f"  {finding.check}: {finding.message}")
    print("review it (docs/split-library.md), resolve every TODO(untwine) marker, build it, "
          f"then run: untwine publish-repo pxr-{args.lib}")
    return 0


COMMANDS.append(("add", "create a new pxr-<lib> repository from OpenUSD", _add_args, _add_run))
```

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_add -v` — Expected: 6 tests PASS

- [ ] **Step 5: Commit** — `git add untwine_cli/add.py untwine_cli/cli.py tests/test_add.py && git commit -m "Add untwine add."`

---

### Task 4: `untwine publish-repo`

**Files:**
- Modify: `untwine_cli/add.py`, `untwine_cli/github.py`, `untwine_cli/cli.py`, `tests/fake_gh.py`
- Test: `tests/test_publish_repo.py`

**Interfaces:**
- Produces: `github.create_repo(repo_slug, description) -> str` (SSH URL); `add.publish_repo(m, name, *, confirm) -> str | None` (URL, or None when declined); CLI `publish-repo <repo>`; `FakeGh(origins, root=None)` handling `repo create` / `repo view`.

- [ ] **Step 1: Extend `tests/fake_gh.py`**

Change `__init__` to `def __init__(self, origins: dict[str, Path], root: Path | None = None):` storing `self.root = root`, and add before the final `raise`:

```python
        if args[:2] == ["repo", "create"]:
            slug = args[2]
            if slug in self.origins:
                return _done(args, "", 1)
            path = self.root / f"{slug.replace('/', '-')}.git"
            sh_git(path.parent, "init", "-q", "--bare", str(path))
            self.origins[slug] = path
            return _done(args, f"https://github.com/{slug}")
        if args[:2] == ["repo", "view"]:
            return _done(args, str(self.origins[args[2]]) + "\n")
```

- [ ] **Step 2: Write the failing tests**

`tests/test_publish_repo.py`:

```python
from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import UntwineError, add, gitutil, manifest


@needs_filter_repo
class PublishRepoTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, extra_libs=True)
        self.gh = FakeGh({}, root=self.tmp / "remotes")
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        self.path, _ = add.add(self.world.m, "qux")
        self.m = manifest.load(self.world.manifest_path)

    def resolve_markers(self):
        for rel in ("src/CMakeLists.txt",):
            text = (self.path / rel).read_text()
            (self.path / rel).write_text("".join(l for l in text.splitlines(True) if not l.startswith("#")))
        sh_git(self.path, "commit", "-q", "-a", "--amend", "--no-edit")

    def test_publish_refuses_markers(self):
        with self.assertRaisesRegex(UntwineError, "todo"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        self.assertEqual(self.gh.origins, {})

    def test_publish(self):
        self.resolve_markers()
        self.assertIsNone(add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: False))
        self.assertEqual(self.gh.origins, {})
        url = add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
        origin = self.gh.origins["untwine/pxr-qux"]
        self.assertEqual(url, str(origin))
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/main"), gitutil.rev(self.path, "main"))
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/open-usd"), gitutil.rev(self.path, "open-usd"))
        self.assertEqual(gitutil.git(self.path, "rev-parse", "--abbrev-ref", "main@{upstream}"), "origin/main")
        with self.assertRaisesRegex(UntwineError, "already has an origin"):
            add.publish_repo(self.m, "pxr-qux", confirm=lambda lines: True)
```

- [ ] **Step 3: Run** — `python3 -m unittest tests.test_publish_repo -v` — Expected: FAIL with `AttributeError: module 'untwine_cli.add' has no attribute 'publish_repo'`

- [ ] **Step 4: Implement**

Append to `untwine_cli/github.py`:

```python
def create_repo(repo_slug: str, description: str) -> str:
    _call(["repo", "create", repo_slug, "--public", "--description", description])
    return _call(["repo", "view", repo_slug, "--json", "sshUrl", "--jq", ".sshUrl"]).strip()
```

Append to `untwine_cli/add.py`:

```python
def publish_repo(m: Manifest, name: str, *, confirm: Callable[[list[str]], bool]) -> str | None:
    repo = m.repos.get(name)
    path = m.repo_path(name)
    if repo is None or not path.is_dir():
        raise AddError(f"{name} is not a repository in untwine.toml")
    if gitutil.ok(path, "remote", "get-url", "origin"):
        raise AddError(f"{name} already has an origin remote; it is already published")
    findings = verify.run_checks(path, m, repo)
    if findings:
        raise AddError(f"{name} is not ready to publish:\n  " + "\n  ".join(f"{f.check}: {f.message}" for f in findings))
    slug = github.slug(m, name)
    main, usd = gitutil.rev(path, "main"), gitutil.rev(path, "open-usd")
    if not confirm([f"create the public GitHub repository {slug}",
                    f"push main {main[:12]} and open-usd {usd[:12]} to it"]):
        return None
    url = github.create_repo(slug, f"OpenUSD's '{repo.lib}' library as a standalone package.")
    gitutil.run(path, "remote", "add", "origin", url)
    gitutil.run(path, "push", "--quiet", "--atomic", "--force-with-lease=refs/heads/main:",
                "--force-with-lease=refs/heads/open-usd:", "origin", "main", "open-usd")
    gitutil.run(path, "fetch", "--quiet", "origin")
    gitutil.run(path, "branch", "--quiet", "--set-upstream-to=origin/main", "main")
    return url
```

In `untwine_cli/cli.py`, insert above `def main`:

```python
def _publish_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo", help="for example pxr-pcp")


def _publish_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import add
    url = add.publish_repo(m, args.repo, confirm=confirm)
    print(f"published {args.repo}: {url}" if url else "nothing published")
    return 0


COMMANDS.append(("publish-repo", "create the GitHub repository of a new library and push it", _publish_args, _publish_run))
```

- [ ] **Step 5: Run** — `python3 -m unittest discover -s tests -t .` — Expected: OK

- [ ] **Step 6: Commit** — `git add untwine_cli tests && git commit -m "Add untwine publish-repo."`

---

### Task 5: Documentation and a real-repository rehearsal

**Files:**
- Modify: `docs/split-library.md`, `README.md`, `AGENTS.md`, `docs/specs/2026-10-01-untwine-cli-design.md`

- [ ] **Step 1: Rewrite `docs/split-library.md`**

````markdown
# Add a library

1. `./untwine add <lib>` (for example `pcp`). It locates the library in
   OpenUSD at the manifest's release, derives its direct dependencies from
   upstream's `pxr_library()` (and stops with the order to add any library
   that has no repository yet), and creates `../pxr-<lib>` with:
   * `open-usd`: the filtered upstream history;
   * `Restructure the '<lib>' library as a standalone package.`: generated
     (layout, include and namespace substitutions, `pxr.h.in`, `NOTICE.txt`,
     `LICENSE.txt`, commit message);
   * `Add minimal release configuration.`: the nearest sibling's current
     files with names and dependency lists rewritten.
   It also adds the library to `untwine.toml`. Nothing remote is touched.
2. Resolve every `TODO(untwine): review` marker by amending the config
   commit: replace the copied source, header, Python, and test lists with
   the ones listed in the marker (from upstream's `pxr_library()`), and
   reconstruct `moduleDeps.cpp` per [`python-bindings.md`](python-bindings.md).
   Review the rest against [`packaging.md`](packaging.md) and
   [`namespaces.md`](namespaces.md); record any extra direct dependency in
   `untwine.toml` `extra_deps` with the reason.
3. Build and test it through Conan and, with Python bindings, the wheels
   ([`validation.md`](validation.md)). Put each genuine fix in its own commit.
4. `./untwine verify pxr-<lib> --diff` must be clean.
5. `./untwine publish-repo pxr-<lib>` creates the public GitHub repository
   and pushes `main` and `open-usd` after confirmation. Commit the updated
   `untwine.toml`.
````

- [ ] **Step 2: Update `README.md` usage** — add these two lines after the `./untwine discard` line:

```
./untwine add <lib>                     create ../pxr-<lib> from OpenUSD (local only)
./untwine publish-repo pxr-<lib>        create its GitHub repository and push (asks first)
```

- [ ] **Step 3: Update the spec** — in the spec's `### \`untwine add <lib> ...\` (phase 2)` heading remove ` (phase 2)`, and in step 5 replace "Insert `TODO(untwine): review` markers for source/test lists and `moduleDeps.cpp`" with "Insert `TODO(untwine): review` markers, listing upstream's declarations, for source/test lists and `moduleDeps.cpp`". In `## Phases`, change item 2 to `2. **Done:** \`add\` and \`publish-repo\`.`

- [ ] **Step 4: Rehearse on the real repositories in a scratch workspace**

```bash
S=$(mktemp -d)/ws && mkdir -p $S/toolbox $S/.untwine
for r in ../pxr-*; do ln -s "$(cd $r && pwd)" $S/$(basename $r); done
ln -s "$(cd ../.untwine/cache && pwd)" $S/.untwine/cache
cp untwine.toml $S/toolbox/
./untwine --manifest $S/toolbox/untwine.toml add pcp
```

Expected: `created .../pxr-pcp`, deps derived from upstream, and verify findings that are only `todo`. If other findings appear, decide for each whether it is a tool bug (fix with a test) or expected manual work (document it in `split-library.md`). Then `rm -rf $S`. Nothing in `~/dev/untwine` changes (the symlinked clones are only read).

- [ ] **Step 5: Commit** — `git add docs README.md AGENTS.md && git commit -m "Document untwine add and publish-repo."`
