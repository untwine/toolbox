"""Load and validate untwine.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import UntwineError

KINDS = ("library", "special", "support")
PYTHON = ("none", "optional", "required")
_REPO_KEYS = {"kind", "upstream", "python", "deps", "test_deps", "extra_deps", "path_rules"}
_TAG = re.compile(r"^v(\d{2})\.(\d{2})$")


class ManifestError(UntwineError):
    pass


@dataclass(frozen=True)
class Repo:
    name: str
    kind: str = "library"
    upstream: str | None = None
    python: str = "none"
    deps: tuple[str, ...] = ()
    test_deps: tuple[str, ...] = ()
    extra_deps: dict[str, str] = field(default_factory=dict)
    path_rules: tuple[tuple[str, str], ...] = ()

    @property
    def lib(self) -> str:
        return self.name.removeprefix("pxr-")

    @property
    def has_python(self) -> bool:
        return self.python != "none"


@dataclass(frozen=True)
class Manifest:
    path: Path
    workspace: Path
    upstream_url: str
    github_org: str
    openusd: str
    tbb: str
    repos: dict[str, Repo]

    def repo_path(self, name: str) -> Path:
        return self.workspace / name

    @property
    def state_dir(self) -> Path:
        return self.workspace / ".untwine"

    def libraries(self) -> list[Repo]:
        return [r for r in self.repos.values() if r.kind != "support"]


def version_forms(tag: str) -> tuple[str, str]:
    match = _TAG.match(tag)
    if not match:
        raise ManifestError(f"release tag {tag!r} is not of the form vYY.MM")
    yy, mm = match.group(1), int(match.group(2))
    return f"0.{yy}.{mm}", f"{yy}.{mm}"


def _repo(name: str, spec: dict) -> Repo:
    if not name.startswith("pxr-"):
        raise ManifestError(f"repository {name!r} must be named pxr-<lib>")
    unknown = set(spec) - _REPO_KEYS
    if unknown:
        raise ManifestError(f"{name}: unknown keys {sorted(unknown)}")
    kind, python = spec.get("kind", "library"), spec.get("python", "none")
    if kind not in KINDS:
        raise ManifestError(f"{name}: kind must be one of {KINDS}")
    if python not in PYTHON:
        raise ManifestError(f"{name}: python must be one of {PYTHON}")
    if kind != "support" and not spec.get("upstream"):
        raise ManifestError(f"{name}: 'upstream' is required")
    return Repo(
        name=name, kind=kind, upstream=spec.get("upstream"), python=python,
        deps=tuple(spec.get("deps", ())), test_deps=tuple(spec.get("test_deps", ())),
        extra_deps=dict(spec.get("extra_deps", {})),
        path_rules=tuple((rule["match"], rule["to"]) for rule in spec.get("path_rules", ())),
    )


def load(path: Path) -> Manifest:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from None
    try:
        release = data["release"]
        m = Manifest(
            path=path.resolve(),
            workspace=(path.resolve().parent / data.get("workspace", "..")).resolve(),
            upstream_url=data["upstream_url"], github_org=data["github_org"],
            openusd=release["openusd"], tbb=release["tbb"],
            repos={name: _repo(name, spec) for name, spec in data["repos"].items()},
        )
    except KeyError as exc:
        raise ManifestError(f"{path}: missing key {exc}") from None
    version_forms(m.openusd)
    levels(m)
    return m


def levels(m: Manifest) -> list[list[str]]:
    graph: dict[str, set[str]] = {}
    for repo in m.repos.values():
        for dep in repo.extra_deps:
            if dep not in repo.deps:
                raise ManifestError(f"{repo.name}: extra dependency {dep!r} is not listed in deps")
        needs = set()
        for dep in (*repo.deps, *repo.test_deps):
            if f"pxr-{dep}" not in m.repos:
                raise ManifestError(f"{repo.name}: unknown dependency {dep!r}")
            needs.add(f"pxr-{dep}")
        graph[repo.name] = needs
    result: list[list[str]] = []
    placed: set[str] = set()
    while len(placed) < len(graph):
        level = sorted(n for n, needs in graph.items() if n not in placed and needs <= placed)
        if not level:
            raise ManifestError(f"dependency cycle among {sorted(set(graph) - placed)}")
        result.append(level)
        placed.update(level)
    return result


def level_map(m: Manifest) -> dict[str, int]:
    return {name: i for i, level in enumerate(levels(m)) for name in level}


def selected(m: Manifest, names: list[str]) -> list[Repo]:
    unknown = sorted(set(names) - set(m.repos))
    if unknown:
        raise ManifestError(f"unknown repositories: {', '.join(unknown)}")
    order = [n for level in levels(m) for n in level]
    return [m.repos[n] for n in order if (not names or n in names) and m.repos[n].kind != "support"]


def transitive_deps(m: Manifest, repo: Repo) -> set[str]:
    seen: set[str] = set()
    stack = list(repo.deps)
    while stack:
        dep = stack.pop()
        if dep not in seen:
            seen.add(dep)
            stack.extend(m.repos[f"pxr-{dep}"].deps)
    return seen
