"""The OpenUSD mirror, subtree filtering, and pxr_library() dependency derivation."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import UntwineError, gitutil
from .manifest import Manifest, Repo

IGNORED_VARIABLES = frozenset({"WINLIBS", "CMAKE_DL_LIBS", "ARCH_PLATFORM_LIBS", "work_impl_target"})
_KEYWORD = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class UpstreamError(UntwineError):
    pass


@dataclass(frozen=True)
class PxrLibrary:
    name: str
    libraries: tuple[str, ...]
    has_python: bool


@dataclass(frozen=True)
class Dependencies:
    pxr: tuple[str, ...]
    tbb: bool


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _variables(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for match in re.finditer(r"\bset\(\s*([A-Za-z_][A-Za-z0-9_]*)([^)]*)\)", text):
        result.setdefault(match.group(1), match.group(2).split())
    return result


def parse_pxr_library(cmake: str) -> PxrLibrary:
    text = _strip_comments(cmake)
    match = re.search(r"\bpxr_library\(([^)]*)\)", text)
    if not match:
        raise UpstreamError("no pxr_library() declaration found")
    tokens = match.group(1).split()
    name, sections, current = tokens[0], {}, None
    for token in tokens[1:]:
        if _KEYWORD.match(token):
            current = sections.setdefault(token, [])
        elif current is None:
            raise UpstreamError(f"pxr_library({name}): unexpected {token!r} before the first keyword")
        else:
            current.append(token)
    variables = _variables(text)

    def expand(entries: list[str]) -> list[str]:
        out = []
        for entry in entries:
            var = _VARIABLE.fullmatch(entry)
            if var and var.group(1) not in IGNORED_VARIABLES and var.group(1) in variables:
                out.extend(variables[var.group(1)])
            else:
                out.append(entry)
        return out

    has_python = any(expand(sections.get(key, [])) for key in ("PYMODULE_CPPFILES", "PYMODULE_FILES", "PYTHON_CPPFILES"))
    return PxrLibrary(name, tuple(expand(sections.get("LIBRARIES", []))), has_python)


def derive_deps(library: PxrLibrary, known: set[str]) -> Dependencies:
    pxr: list[str] = []
    tbb = False
    for entry in library.libraries:
        var = _VARIABLE.fullmatch(entry)
        if entry == "TBB::tbb":
            tbb = True
        elif var and var.group(1) in IGNORED_VARIABLES:
            continue
        elif entry in known:
            if entry not in pxr:
                pxr.append(entry)
        else:
            raise UpstreamError(f"pxr_library({library.name}): unrecognised LIBRARIES entry {entry!r}")
    return Dependencies(tuple(pxr), tbb)


def expected_deps(repo: Repo, derived: Dependencies) -> set[str]:
    deps = set(derived.pxr) | set(repo.extra_deps)
    if repo.has_python:
        deps.add("boost")
    return deps


def mirror_path(m: Manifest) -> Path:
    return m.state_dir / "cache" / "OpenUSD.git"


def ensure_mirror(m: Manifest) -> Path:
    path = mirror_path(m)
    if path.exists():
        gitutil.run(path, "fetch", "--quiet", "--prune", "--tags", "origin")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        gitutil.run(path.parent, "clone", "--quiet", "--mirror", m.upstream_url, str(path))
    return path


def filter_subtree(mirror: Path, tag: str, subtree: str, scratch_root: Path) -> Path:
    """Filter `subtree` at `tag` in a throwaway clone; the result is its `filtered` branch."""
    if not gitutil.rev(mirror, tag):
        raise UpstreamError(f"tag {tag} not found in the OpenUSD mirror")
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="filter-", dir=scratch_root))
    gitutil.run(scratch_root, "clone", "--quiet", "--no-checkout", str(mirror), str(scratch))
    gitutil.run(scratch, "branch", "--quiet", "filtered", f"{tag}^{{commit}}")
    gitutil.run(scratch, "filter-repo", "--refs", "filtered", "--path", subtree,
                "--path-rename", f"{subtree}/:", "--force")
    return scratch


def import_filtered(repo: Path, scratch: Path, ref: str) -> str:
    gitutil.run(repo, "fetch", "--quiet", "--no-tags", str(scratch), f"+refs/heads/filtered:{ref}")
    shutil.rmtree(scratch)
    return gitutil.rev(repo, ref)
