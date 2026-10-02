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
    (target / f"src/pxr/{lib}").mkdir(parents=True, exist_ok=True)
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
