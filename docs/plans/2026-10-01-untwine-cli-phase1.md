# `untwine` CLI Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `untwine` CLI that syncs every `pxr-*` repository to a new OpenUSD release, with per-repo PRs for review and a guarded promote, plus a read-only `verify`.

**Architecture:** A standard-library Python package (`untwine_cli`) run from the toolbox checkout through an executable shim. All release progress lives in git (refs under `refs/untwine/<tag>/`, notes under `refs/notes/untwine/<tag>`, JSON blobs referenced by refs), so state cannot drift from the repositories. Each sync runs in isolated worktrees of the maintainer's clones; nothing remote changes before `push-prs`, and `main` only moves at `promote`, with leases pinned to SHAs recorded at preflight.

**Tech Stack:** Python ≥ 3.11 standard library (`tomllib`, `argparse`, `dataclasses`, `unittest`), `git`, `git-filter-repo`, `gh`.

**Spec:** `docs/specs/2026-10-01-untwine-cli-design.md`

## Global Constraints

- Python ≥ 3.11, standard library only; tests use `unittest` (no pytest, no third-party packages).
- Run tests from `toolbox/`: `python3 -m unittest discover -s tests -t . -v`.
- Commits in this repository and in `pxr-*` repositories never carry `Co-Authored-By`, AI attribution, or session trailers.
- No push or remote write without an interactive confirmation listing the refs; `promote` has no non-interactive flag.
- Every force push uses `--force-with-lease=<ref>:<recorded sha>`.
- Version forms for tag `vYY.MM`: CMake `0.YY.M`, Conan and PyPI `YY.M` (month without leading zero).
- Sibling Python pins use the wildcard form `==YY.M.*`; Conan `requires` and CMake `EXACT` stay exact.
- Unknown CMake entries, unmapped files, and ambiguous paths are errors; never guess.
- Conflicts are auto-resolved only by the provable rule (a path with no upstream counterpart is never provable).
- `kind = "special"` repositories are never textually transformed; `kind = "support"` repositories are skipped by sync.
- Keep inline code comments to one line.

## Review Focus

1. **Real upstream CMake variants** (`${libs}` defined by `set()`, `${work_impl_target}`, comment lines inside `LIBRARIES`, `${CMAKE_DL_LIBS}`): dependency derivation must match the real repositories, not just the fixture. Pinned by `test_real_declarations` in Task 4.
2. **Binary files** (baselines, `.usdc`, images) reaching `transform` through conflicts or new-file placement: must pass through byte-for-byte. Pinned by `test_binary_passthrough` in Task 3.
3. **Rerunning `sync` after an interruption or after it already finished**: must not duplicate commits or lose the mapping. Pinned by `test_rerun_is_idempotent` in Task 9.
4. **Manual edits in a worktree after finalize** (amend or fixup by the maintainer): status must report `replayed`, `push-prs` must refuse, and a rerun must re-finalize without losing notes. Pinned by `test_edit_after_finalize` in Task 8.
5. **Upstream deleting a file Untwine only moved** (rename/delete conflict): resolved as a provable delete. Pinned by `test_provable_delete` in Task 7.

---

## File Structure

```text
toolbox/
  untwine                      executable shim (Task 1)
  untwine.toml                 manifest of the real repositories (Task 2)
  .gitignore                   (Task 1)
  untwine_cli/
    __init__.py                UntwineError (Task 1)
    gitutil.py                 strict git subprocess wrapper (Task 1)
    manifest.py                manifest loading, levels, version forms (Task 2)
    transform.py               text substitutions, path rules, learned path maps (Task 3)
    upstream.py                mirror, subtree filtering, pxr_library parsing (Task 4)
    verify.py                  conformance checks, diff report (Task 5)
    cli.py                     argparse entry point; commands registered per task (Task 5+)
    state.py                   refs, notes, JSON blobs, derived status (Task 6)
    replay.py                  open-usd advance, cherry-pick replay, provable resolution, resolve (Task 7)
    versions.py                version rewriting (Task 8)
    finalize.py                new files, dependency check, bump, autosquash, verify (Task 8)
    sync.py                    preflight, orchestration, discard (Task 9)
    report.py                  table, detail, PR body (Task 10)
    github.py                  gh wrapper (Task 11)
    release.py                 push-prs, promote (Tasks 11-12)
  tests/
    __init__.py
    helpers.py                 temp dirs and git fixtures (Task 1)
    fake_world.py              fake OpenUSD + pxr-foo + origin (Task 5)
    fake_gh.py                 fake gh CLI (Task 11)
    test_*.py                  one file per module
  releases/                    per-release reports written by promote
  AGENTS.md, README.md, docs/  (Task 13)
```

---

### Task 1: Package skeleton, git wrapper, test fixtures

**Files:**
- Create: `untwine`, `.gitignore`, `untwine_cli/__init__.py`, `untwine_cli/gitutil.py`, `tests/__init__.py`, `tests/helpers.py`
- Test: `tests/test_gitutil.py`

**Interfaces:**
- Produces: `UntwineError`; `gitutil.GitError(UntwineError)`; `gitutil.run(repo, *args, input=None, check=True, env=None) -> CompletedProcess[bytes]`; `gitutil.git(repo, *args, **kw) -> str`; `gitutil.ok(repo, *args) -> bool`; `gitutil.rev(repo, ref) -> str | None` (commits only); `gitutil.ref_exists(repo, ref) -> bool` (any object); `gitutil.blob(repo, revision, path) -> bytes | None`; `gitutil.paths(repo, revision) -> set[str]`; `gitutil.is_ancestor(repo, a, b) -> bool`. Test helpers: `GIT_ENV`, `sh_git`, `init`, `write`, `commit`, `TempTest`, `needs_filter_repo`.

- [ ] **Step 1: Create the skeleton files**

`untwine` (then `chmod +x untwine`):

```python
#!/usr/bin/env python3
"""Run the untwine CLI from this checkout."""
import pathlib
import sys

if sys.version_info < (3, 11):
    sys.exit("untwine requires Python 3.11 or newer")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from untwine_cli.cli import main  # noqa: E402

sys.exit(main())
```

`.gitignore`:

```text
.idea/
.DS_Store
__pycache__/
```

`untwine_cli/__init__.py`:

```python
"""Untwine maintainer CLI."""


class UntwineError(RuntimeError):
    """An error reported to the user without a traceback."""
```

`tests/__init__.py`: empty file.

`tests/helpers.py`:

```python
"""Shared test fixtures: temporary directories and git repositories."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def sh_git(repo: Path, *args: str, input: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], input=input, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV, "GIT_EDITOR": "true"},
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def init(path: Path, bare: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    sh_git(path, "init", "-q", "-b", "main", *(["--bare"] if bare else []))
    return path


def write(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())


def commit(repo: Path, files: dict[str, str | bytes], message: str, *, replace: bool = False) -> str:
    if replace:
        for tracked in sh_git(repo, "ls-files").splitlines():
            if tracked not in files:
                sh_git(repo, "rm", "-q", "--", tracked)
    write(repo, files)
    sh_git(repo, "add", "-A")
    sh_git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return sh_git(repo, "rev-parse", "HEAD")


class TempTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="untwine-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.dict(os.environ, GIT_ENV))


def needs_filter_repo(test):
    return unittest.skipUnless(shutil.which("git-filter-repo"), "git-filter-repo is not installed")(test)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_gitutil.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_gitutil -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'untwine_cli.gitutil'`

- [ ] **Step 4: Write `untwine_cli/gitutil.py`**

```python
"""Strict wrappers around the git command line."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import UntwineError

_ENV = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true"}


class GitError(UntwineError):
    def __init__(self, args: list[str], returncode: int, output: str):
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {output.strip()}")
        self.returncode = returncode
        self.output = output


def run(repo: Path, *args: str, input: bytes | None = None, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=input, capture_output=True,
                          env={**os.environ, **_ENV, **(env or {})})
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, (proc.stderr or proc.stdout).decode(errors="replace"))
    return proc


def git(repo: Path, *args: str, **kw) -> str:
    return run(repo, *args, **kw).stdout.decode().rstrip("\n")


def ok(repo: Path, *args: str) -> bool:
    return run(repo, *args, check=False).returncode == 0


def rev(repo: Path, ref: str) -> str | None:
    proc = run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return proc.stdout.decode().strip() or None


def ref_exists(repo: Path, ref: str) -> bool:
    return ok(repo, "rev-parse", "--verify", "--quiet", ref)


def blob(repo: Path, revision: str, path: str) -> bytes | None:
    proc = run(repo, "cat-file", "blob", f"{revision}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def paths(repo: Path, revision: str) -> set[str]:
    out = run(repo, "ls-tree", "-r", "-z", "--name-only", revision).stdout.decode()
    return {p for p in out.split("\0") if p}


def is_ancestor(repo: Path, a: str, b: str) -> bool:
    return run(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_gitutil -v`
Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add untwine .gitignore untwine_cli tests
git commit -m "Add the untwine package skeleton and git wrapper."
```

---

### Task 2: Manifest

**Files:**
- Create: `untwine_cli/manifest.py`, `untwine.toml`
- Test: `tests/test_manifest.py`

**Interfaces:**
- Consumes: `UntwineError`.
- Produces: `ManifestError(UntwineError)`; `Repo` (fields `name, kind, upstream, python, deps, test_deps, extra_deps, path_rules`; properties `lib`, `has_python`); `Manifest` (fields `path, workspace, upstream_url, github_org, openusd, tbb, repos`; `repo_path(name) -> Path`; `state_dir` property; `libraries() -> list[Repo]`); `load(path) -> Manifest`; `version_forms(tag) -> tuple[str, str]`; `levels(m) -> list[list[str]]`; `level_map(m) -> dict[str, int]`; `selected(m, names) -> list[Repo]` (dependency order, support repos excluded); `transitive_deps(m, repo) -> set[str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_manifest.py`:

```python
import textwrap
from pathlib import Path

from tests.helpers import TempTest
from untwine_cli import manifest

SAMPLE = textwrap.dedent("""\
    workspace = ".."
    upstream_url = "git@example.com:OpenUSD.git"
    github_org = "untwine"

    [release]
    openusd = "v26.08"
    tbb = "2023.1.0"

    [repos.pxr-tbb]
    kind = "support"

    [repos.pxr-arch]
    upstream = "pxr/base/arch"

    [repos.pxr-tf]
    upstream = "pxr/base/tf"
    python = "optional"
    deps = ["arch"]

    [repos.pxr-js]
    upstream = "pxr/base/js"
    deps = ["arch", "tf"]
    extra_deps = { arch = "includes <pxr/arch/...> headers directly" }
    """)


class ManifestTest(TempTest):
    def load(self, text: str) -> manifest.Manifest:
        (self.tmp / "toolbox").mkdir(exist_ok=True)
        path = self.tmp / "toolbox" / "untwine.toml"
        path.write_text(text)
        return manifest.load(path)

    def test_load(self):
        m = self.load(SAMPLE)
        self.assertEqual(m.workspace, self.tmp)
        self.assertEqual(m.repos["pxr-js"].lib, "js")
        self.assertEqual(m.repos["pxr-js"].extra_deps, {"arch": "includes <pxr/arch/...> headers directly"})
        self.assertTrue(m.repos["pxr-tf"].has_python)
        self.assertEqual(m.repo_path("pxr-tf"), self.tmp / "pxr-tf")
        self.assertEqual(m.state_dir, self.tmp / ".untwine")
        self.assertEqual([r.name for r in m.libraries()], ["pxr-arch", "pxr-tf", "pxr-js"])

    def test_levels_and_selection(self):
        m = self.load(SAMPLE)
        self.assertEqual(manifest.levels(m), [["pxr-arch", "pxr-tbb"], ["pxr-tf"], ["pxr-js"]])
        self.assertEqual(manifest.level_map(m)["pxr-js"], 2)
        self.assertEqual([r.name for r in manifest.selected(m, [])], ["pxr-arch", "pxr-tf", "pxr-js"])
        self.assertEqual([r.name for r in manifest.selected(m, ["pxr-js", "pxr-arch"])], ["pxr-arch", "pxr-js"])
        self.assertEqual(manifest.transitive_deps(m, m.repos["pxr-js"]), {"arch", "tf"})
        with self.assertRaises(manifest.ManifestError):
            manifest.selected(m, ["pxr-nope"])

    def test_version_forms(self):
        self.assertEqual(manifest.version_forms("v26.08"), ("0.26.8", "26.8"))
        self.assertEqual(manifest.version_forms("v26.11"), ("0.26.11", "26.11"))
        with self.assertRaises(manifest.ManifestError):
            manifest.version_forms("26.08")

    def test_rejects_bad_manifests(self):
        cases = {
            "unknown dependency": SAMPLE.replace('deps = ["arch"]', 'deps = ["nope"]'),
            "cycle": SAMPLE.replace('upstream = "pxr/base/arch"', 'upstream = "pxr/base/arch"\ndeps = ["js"]'),
            "bad tag": SAMPLE.replace('"v26.08"', '"26.08"'),
            "unknown key": SAMPLE.replace('python = "optional"', 'python = "optional"\ncolour = "red"'),
            "missing upstream": SAMPLE.replace('upstream = "pxr/base/tf"\n', ""),
            "extra not in deps": SAMPLE.replace('{ arch = ', '{ gf = '),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(manifest.ManifestError):
                self.load(text)

    def test_real_manifest_loads(self):
        m = manifest.load(Path(__file__).resolve().parent.parent / "untwine.toml")
        self.assertEqual(manifest.levels(m)[0], ["pxr-arch", "pxr-boost", "pxr-pegtl", "pxr-tbb"])
        self.assertEqual(manifest.levels(m)[-1], ["pxr-sdf"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_manifest -v`
Expected: FAIL with `ImportError: cannot import name 'manifest'`

- [ ] **Step 3: Write `untwine_cli/manifest.py`**

```python
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
```

- [ ] **Step 4: Write `untwine.toml`**

Values come from each repository's `conanfile.py` (`self.requires`), `test_requires`, and Python option. The extras were confirmed by `grep -rl "<pxr/arch/" pxr-js/src` (and likewise for `work`, `ts`, and `sdf`→`plug`).

```toml
# Untwine release manifest. `deps` are the direct pxr-* dependencies each
# repository declares (conanfile requires); `extra_deps` explains entries that
# upstream's pxr_library() does not list.
workspace = ".."
upstream_url = "git@github.com:PixarAnimationStudios/OpenUSD.git"
github_org = "untwine"

[release]
openusd = "v26.08"
tbb = "2023.1.0"

[repos.pxr-tbb]
kind = "support"

[repos.pxr-boost]
kind = "special"
upstream = "pxr/external/boost"
python = "required"

[repos.pxr-pegtl]
kind = "special"
upstream = "pxr/base/pegtl"

[repos.pxr-arch]
upstream = "pxr/base/arch"

[repos.pxr-tf]
upstream = "pxr/base/tf"
python = "optional"
deps = ["arch", "boost"]

[repos.pxr-js]
upstream = "pxr/base/js"
deps = ["arch", "tf"]
extra_deps = { arch = "includes <pxr/arch/...> headers directly" }

[repos.pxr-gf]
upstream = "pxr/base/gf"
python = "optional"
deps = ["arch", "tf", "boost"]

[repos.pxr-trace]
upstream = "pxr/base/trace"
python = "optional"
deps = ["arch", "tf", "js", "boost"]

[repos.pxr-work]
upstream = "pxr/base/work"
python = "optional"
deps = ["arch", "tf", "trace", "boost"]
extra_deps = { arch = "includes <pxr/arch/...> headers directly" }

[repos.pxr-plug]
upstream = "pxr/base/plug"
python = "optional"
deps = ["arch", "tf", "js", "trace", "work", "boost"]
test_deps = ["gf"]

[repos.pxr-vt]
upstream = "pxr/base/vt"
python = "optional"
deps = ["arch", "tf", "gf", "trace", "boost"]

[repos.pxr-ts]
upstream = "pxr/base/ts"
python = "optional"
deps = ["arch", "tf", "gf", "vt", "boost"]
extra_deps = { arch = "includes <pxr/arch/...> headers directly" }

[repos.pxr-ar]
upstream = "pxr/usd/ar"
python = "optional"
deps = ["arch", "tf", "js", "plug", "vt", "boost"]

[repos.pxr-kind]
upstream = "pxr/usd/kind"
python = "optional"
deps = ["tf", "plug", "boost"]

[repos.pxr-sdf]
upstream = "pxr/usd/sdf"
python = "optional"
deps = ["arch", "tf", "gf", "pegtl", "trace", "ts", "vt", "work", "ar", "plug", "boost"]
extra_deps = { plug = "includes <pxr/plug/...> headers directly" }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_manifest -v`
Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add untwine_cli/manifest.py untwine.toml tests/test_manifest.py
git commit -m "Add the release manifest and its loader."
```

---

### Task 3: Transform

**Files:**
- Create: `untwine_cli/transform.py`
- Test: `tests/test_transform.py`

**Interfaces:**
- Consumes: `gitutil.run`, `gitutil.paths`, `UntwineError`.
- Produces: `TransformError(UntwineError)`; `transform_text(text, lib) -> str`; `transform_bytes(data, lib, *, special=False) -> bytes`; `DEFAULT_RULES`; `rule_target(rules, lib, path) -> tuple[str, str]` (target, description); `PathMap` with `lib`, `learned: dict[str, str]`, `rules`, `standalone(upstream_path) -> tuple[str, str]`, `upstream(standalone_path) -> str | None`; `learn(repo, restructure_sha, lib, extra_rules=()) -> PathMap`.

- [ ] **Step 1: Write the failing tests**

`tests/test_transform.py`:

```python
from tests.helpers import TempTest, commit, init, sh_git
from untwine_cli import transform


class TextTest(TempTest):
    def test_include_rules(self):
        src = (
            '#include "pxr/pxr.h"\n'
            '#include "pxr/base/tf/token.h"\n'
            '#include "pxr/usd/sdf/path.h"\n'
            '#include "pxr/external/boost/python/def.hpp"\n'
            '#include "pxr/foo/own.h"\n'
            '  #  include "pxr/base/arch/api.h"\n'
            '///  #include "pxr/base/arch/export.h"\n'
            '#include <string>\n'
            'const char* s = "pxr/base/tf/token.h";\n'
        )
        self.assertEqual(transform.transform_text(src, "foo"), (
            '#include <pxr/foo/pxr.h>\n'
            '#include <pxr/tf/token.h>\n'
            '#include <pxr/sdf/path.h>\n'
            '#include <pxr/boost/python/def.hpp>\n'
            '#include <pxr/foo/own.h>\n'
            '  #  include <pxr/arch/api.h>\n'
            '///  #include <pxr/arch/export.h>\n'
            '#include <string>\n'
            'const char* s = "pxr/base/tf/token.h";\n'
        ))

    def test_namespace_macros(self):
        src = "PXR_NAMESPACE_OPEN_SCOPE\nPXR_NAMESPACE_CLOSE_SCOPE\nPXR_NAMESPACE_USING_DIRECTIVE\nPXR_NS::X\n"
        self.assertEqual(transform.transform_text(src, "tf"),
                         "TF_NAMESPACE_OPEN_SCOPE\nTF_NAMESPACE_CLOSE_SCOPE\nTF_NAMESPACE_USING_DIRECTIVE\nPXR_NS::X\n")

    def test_pxrpythonsubst(self):
        self.assertEqual(transform.transform_text("#!/pxrpythonsubst\n#\n# Copyright\nimport x\n", "tf"),
                         "# Copyright\nimport x\n")
        self.assertEqual(transform.transform_text("#!/usr/bin/env python\n#\nimport x\n", "tf"),
                         "#!/usr/bin/env python\n#\nimport x\n")

    def test_whitespace_preserved(self):
        src = '#endif // PXR_BASE_ARCH_ALIGN_H \r\n#include "pxr/base/arch/defines.h" \n\n'
        self.assertEqual(transform.transform_text(src, "arch"),
                         '#endif // PXR_BASE_ARCH_ALIGN_H \r\n#include <pxr/arch/defines.h> \n\n')

    def test_binary_passthrough(self):
        data = b"\x89PNG\r\n\x1a\n\x00#include \"pxr/pxr.h\"\xff"
        self.assertEqual(transform.transform_bytes(data, "foo"), data)

    def test_special_is_identity(self):
        data = b'#include "pxr/pxr.h"\n'
        self.assertEqual(transform.transform_bytes(data, "boost", special=True), data)


class PathTest(TempTest):
    def test_default_rules(self):
        rules = transform.DEFAULT_RULES
        self.assertEqual(transform.rule_target(rules, "tf", "testenv/a/b.py")[0], "test/a/b.py")
        self.assertEqual(transform.rule_target(rules, "tf", "wrapToken.cpp")[0], "src/python/wrapToken.cpp")
        self.assertEqual(transform.rule_target(rules, "tf", "module.cpp")[0], "src/python/module.cpp")
        self.assertEqual(transform.rule_target(rules, "tf", "__init__.py")[0], "src/python/__init__.py")
        self.assertEqual(transform.rule_target(rules, "tf", "token.h")[0], "src/pxr/tf/token.h")
        self.assertEqual(transform.rule_target(rules, "tf", "sub/x.h")[0], "src/pxr/tf/sub/x.h")

    def test_learn_from_restructure(self):
        repo = init(self.tmp / "repo")
        big = "".join(f"line {i}\n" for i in range(40))
        commit(repo, {"bar.h": big, "tiny.h": '#include "pxr/pxr.h"\n', "CMakeLists.txt": "x\n",
                      "keep.txt": "k\n", "stub.cpp": "\n"}, "upstream")
        (repo / "src/pxr/foo").mkdir(parents=True)
        sh_git(repo, "mv", "bar.h", "src/pxr/foo/bar.h")
        sh_git(repo, "rm", "-q", "tiny.h", "stub.cpp")
        (repo / "src/pxr/foo/tiny.h").write_text("#include <pxr/foo/pxr.h>\n")
        restructure = commit(repo, {}, "Restructure")
        pm = transform.learn(repo, restructure, "foo")
        self.assertEqual(pm.standalone("bar.h"), ("src/pxr/foo/bar.h", "learned"))
        self.assertEqual(pm.upstream("src/pxr/foo/tiny.h"), "tiny.h")
        self.assertEqual(pm.upstream("CMakeLists.txt"), "CMakeLists.txt")
        self.assertIsNone(pm.upstream("src/pxr/foo/stub.cpp"))
        self.assertEqual(pm.standalone("new.h"), ("src/pxr/foo/new.h", r"rule (.*)"))

    def test_extra_rules_take_precedence(self):
        pm = transform.PathMap("gf", {}, (("(.*)\\.template\\.(h|cpp)", r"resources/templates/\1.template.\2"),
                                          *transform.DEFAULT_RULES))
        self.assertEqual(pm.standalone("vec.template.h")[0], "resources/templates/vec.template.h")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_transform -v`
Expected: FAIL with `ImportError: cannot import name 'transform'`

- [ ] **Step 3: Write `untwine_cli/transform.py`**

```python
"""Standalone substitutions and the upstream <-> standalone path mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import UntwineError, gitutil

_INC = r"^([ \t]*(?:///[ \t]*)?#[ \t]*include[ \t]+)"
_INCLUDE_RULES = (
    (re.compile(_INC + r'"pxr/pxr\.h"', re.M), r"\1<pxr/{lib}/pxr.h>"),
    (re.compile(_INC + r'"pxr/external/boost/([^"\n]+)"', re.M), r"\1<pxr/boost/\2>"),
    (re.compile(_INC + r'"pxr/(?:base|usd|imaging)/([^"\n]+)"', re.M), r"\1<pxr/\2>"),
    (re.compile(_INC + r'"(pxr/[^"\n]+)"', re.M), r"\1<\2>"),
)
_NAMESPACE = re.compile(r"\bPXR_NAMESPACE_(OPEN_SCOPE|CLOSE_SCOPE|USING_DIRECTIVE)\b")
_PYSUBST = re.compile(r"\A#!/pxrpythonsubst[^\n]*\n(?:#\r?\n)?")

DEFAULT_RULES: tuple[tuple[str, str], ...] = (
    (r"testenv/(.*)", r"test/\1"),
    (r"((?:wrap[^/]*|module|moduleDeps)\.cpp|__init__\.py)", r"src/python/\1"),
    (r"(.*)", r"src/pxr/{lib}/\1"),
)


class TransformError(UntwineError):
    pass


def transform_text(text: str, lib: str) -> str:
    text = _PYSUBST.sub("", text, count=1)
    for pattern, repl in _INCLUDE_RULES:
        text = pattern.sub(repl.replace("{lib}", lib), text)
    return _NAMESPACE.sub(lambda m: f"{lib.upper()}_NAMESPACE_{m.group(1)}", text)


def transform_bytes(data: bytes, lib: str, *, special: bool = False) -> bytes:
    if special:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return transform_text(text, lib).encode("utf-8")


def rule_target(rules: tuple[tuple[str, str], ...], lib: str, path: str) -> tuple[str, str]:
    for pattern, target in rules:
        match = re.fullmatch(pattern, path)
        if match:
            return match.expand(target.replace("{lib}", lib)), f"rule {pattern}"
    raise TransformError(f"no path rule matches {path!r}")


@dataclass
class PathMap:
    lib: str
    learned: dict[str, str]
    rules: tuple[tuple[str, str], ...] = DEFAULT_RULES
    _inverse: dict[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._inverse = {target: source for source, target in self.learned.items()}

    def standalone(self, upstream_path: str) -> tuple[str, str]:
        if upstream_path in self.learned:
            return self.learned[upstream_path], "learned"
        return rule_target(self.rules, self.lib, upstream_path)

    def upstream(self, standalone_path: str) -> str | None:
        return self._inverse.get(standalone_path)


def learn(repo: Path, restructure: str, lib: str, extra_rules: tuple[tuple[str, str], ...] = ()) -> PathMap:
    """Learn the exact mapping recorded by the repository's Restructure commit."""
    out = gitutil.run(repo, "diff", "-M", "--name-status", "-z", f"{restructure}^", restructure).stdout.decode()
    fields = [f for f in out.split("\0") if f]
    learned: dict[str, str] = {}
    deleted: list[str] = []
    added: set[str] = set()
    i = 0
    while i < len(fields):
        status = fields[i]
        if status[0] in "RC":
            learned[fields[i + 1]] = fields[i + 2]
            i += 3
            continue
        if status == "D":
            deleted.append(fields[i + 1])
        elif status == "A":
            added.add(fields[i + 1])
        i += 2
    rules = (*extra_rules, *DEFAULT_RULES)
    for path in deleted:  # renames below git's similarity threshold
        target, _ = rule_target(rules, lib, path)
        if target in added:
            learned[path] = target
    for path in gitutil.paths(repo, f"{restructure}^") & gitutil.paths(repo, restructure):
        learned.setdefault(path, path)
    return PathMap(lib, learned, rules)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_transform -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add untwine_cli/transform.py tests/test_transform.py
git commit -m "Add the standalone transform and path mapping."
```

---

### Task 4: Upstream mirror, filtering, and dependency derivation

**Files:**
- Create: `untwine_cli/upstream.py`
- Test: `tests/test_upstream.py`

**Interfaces:**
- Consumes: `gitutil`, `Manifest`, `Repo`, `UntwineError`.
- Produces: `UpstreamError(UntwineError)`; `PxrLibrary(name, libraries, has_python)`; `Dependencies(pxr, tbb)`; `parse_pxr_library(cmake_text) -> PxrLibrary`; `derive_deps(library, known: set[str]) -> Dependencies`; `expected_deps(repo, derived) -> set[str]`; `mirror_path(m) -> Path`; `ensure_mirror(m) -> Path`; `filter_subtree(mirror, tag, subtree, scratch_root) -> Path`; `import_filtered(repo, scratch, ref) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_upstream.py` (the declarations are the real v26.08 ones, trimmed of file lists):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_upstream -v`
Expected: FAIL with `ImportError: cannot import name 'upstream'`

- [ ] **Step 3: Write `untwine_cli/upstream.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_upstream -v`
Expected: 5 tests PASS (the filter test is skipped only if `git-filter-repo` is missing)

- [ ] **Step 5: Commit**

```bash
git add untwine_cli/upstream.py tests/test_upstream.py
git commit -m "Add the OpenUSD mirror, subtree filtering, and dependency derivation."
```

---

### Task 5: Verify, fake world fixture, and the CLI entry point

**Files:**
- Create: `untwine_cli/verify.py`, `untwine_cli/cli.py`, `tests/fake_world.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `gitutil`, `transform.learn`, `transform.transform_bytes`, `manifest.*`, `upstream.ensure_mirror/filter_subtree/import_filtered`.
- Produces: `verify.Finding(check, message)`; `verify.RESTRUCTURE` (format string with `{lib}`), `verify.CONFIG`; `verify.find_commit(repo, base, head, subject) -> str`; `verify.run_checks(path, m, repo, base="open-usd", head="HEAD") -> list[Finding]`; `verify.check_history(path, repo, base, head) -> list[Finding]`; `verify.diff_report(path, repo, base="open-usd", head="HEAD") -> list[tuple[str, int, bool]]`. `cli.main(argv=None) -> int`; `cli.COMMANDS` list of `(name, help, add_arguments, run)`; `cli.confirm(lines) -> bool`; `cli.current_tag(m, given) -> str`. Test fixture: `FakeWorld(tmp, scenarios=(), *, with_fix=True)` with `.upstream`, `.workspace`, `.manifest_path`, `.m`, `.origin`, `.clone`; `upstream_tree(scenarios)`; `config_files(tag)`.

- [ ] **Step 1: Write `tests/fake_world.py`**

```python
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_verify.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_verify -v`
Expected: FAIL with `ImportError: cannot import name 'cli'`

- [ ] **Step 4: Write `untwine_cli/verify.py`**

```python
"""Read-only conformance checks for a pxr-* repository."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from . import UntwineError, gitutil, transform
from .manifest import Manifest, Repo, transitive_deps

RESTRUCTURE = "Restructure the '{lib}' library as a standalone package."
CONFIG = "Add minimal release configuration."
SOURCE_SUFFIXES = (".cpp", ".cc", ".h", ".hpp", ".mm", ".py", ".template", ".in")
NOTICE_PARAGRAPH = "modified, standalone redistribution"
AI_ATTRIBUTION = re.compile(r"co-authored-by|claude|generated with", re.I)
QUOTED = re.compile(r'^[ \t]*(?:///[ \t]*)?#[ \t]*include[ \t]+"pxr/')
OLD_PATH = re.compile(r'^[ \t]*#[ \t]*include[ \t]+[<"]pxr/(base|external|usd|imaging)/')
MONOLITHIC_NS = re.compile(r"\bPXR_NAMESPACE_(OPEN|CLOSE)_SCOPE\b")
REQUIRES = re.compile(r'self\.requires\("pxr-([a-z]+)/')
TEST_REQUIRES = re.compile(r'self\.test_requires\("pxr-([a-z]+)/')
EXACT_PIN = re.compile(r'"pxr-[a-z]+(?:-dev)?==[0-9.]+"')
UNANCHORED = re.compile(
    r"^(build|develop-eggs|dist|downloads|eggs|\.eggs|lib|lib64|parts|sdist|var|wheels|_skbuild|wheelhouse|env|venv|ENV)/$",
    re.M)


@dataclass(frozen=True)
class Finding:
    check: str
    message: str


class _Tree:
    """Tracked files of a checkout, read from its working tree."""

    def __init__(self, path: Path):
        self.path = path
        out = gitutil.run(path, "ls-files", "-z").stdout.decode()
        self.files = sorted(f for f in out.split("\0") if f)

    def text(self, rel: str) -> str | None:
        try:
            return (self.path / rel).read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            return None

    def lines(self, rel: str) -> list[tuple[int, str]]:
        return list(enumerate((self.text(rel) or "").splitlines(), 1))

    def grep(self, pattern: re.Pattern, prefixes: tuple[str, ...] = ()) -> list[str]:
        hits = []
        for rel in self.files:
            if rel.startswith(".github/") or (prefixes and not rel.startswith(prefixes)):
                continue
            hits += [f"{rel}:{no}: {line.strip()}" for no, line in self.lines(rel) if pattern.search(line)]
        return hits


def _source(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    found = [Finding("quoted-include", h) for h in tree.grep(QUOTED)]
    found += [Finding("old-include-path", h) for h in tree.grep(OLD_PATH)]
    found += [Finding("namespace-macro", h) for h in tree.grep(MONOLITHIC_NS, ("src/", "test/"))]
    found += [Finding("pxrpythonsubst", h) for h in tree.grep(re.compile(r"^#!/pxrpythonsubst"), ("src/", "test/"))]
    if NOTICE_PARAGRAPH not in (tree.text("NOTICE.txt") or ""):
        found.append(Finding("notice", "NOTICE.txt lacks the repository-level attribution paragraph"))
    return found


def _layout(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    return [Finding("root-source", f"{rel} is left at the repository root") for rel in tree.files
            if "/" not in rel and rel.endswith(SOURCE_SUFFIXES) and rel not in ("conanfile.py", "setup.py")]


def _cmake(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    found = []
    src = tree.text("src/CMakeLists.txt")
    if src is not None and "cxx_std_17" not in src:
        found.append(Finding("cxx-standard", "src/CMakeLists.txt does not set target_compile_features(... cxx_std_17)"))
    for rel in ("CMakeLists.txt", "src/CMakeLists.txt"):
        found += [Finding("cxx-standard", f"{rel}:{no}: global CMAKE_CXX_STANDARD")
                  for no, line in tree.lines(rel) if re.search(r"set\(CMAKE_CXX_STANDARD\s+\d", line)]
    for rel in tree.files:
        if rel == "CMakeLists.txt" or (rel.startswith(("src/", "cmake/")) and rel.endswith(("CMakeLists.txt", ".cmake", ".in"))):
            found += [Finding("install-dir", f"{rel}:{no}: CMAKE_INSTALL_BINDIR (install everything to CMAKE_INSTALL_LIBDIR)")
                      for no, line in tree.lines(rel) if "CMAKE_INSTALL_BINDIR" in line]
    for rel in ["CMakeLists.txt", *[r for r in tree.files if r.startswith("cmake/") and r.endswith(".in")]]:
        found += [Finding("exact-version", f"{rel}:{no}: {line.strip()}") for no, line in tree.lines(rel)
                  if re.search(r"find_(?:package|dependency)\(pxr-", line) and "EXACT" not in line]
    return found


def _conan(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    text = tree.text("conanfile.py")
    if text is None:
        return [Finding("conan", "conanfile.py is missing")]
    found = []
    if 'set_property("cmake_find_mode", "none")' not in text:
        found.append(Finding("conan", "cmake_find_mode is not 'none'"))
    if "system_package_version" not in text:
        found.append(Finding("conan", "system_package_version is not set"))
    if "builddirs" not in text:
        found.append(Finding("conan", "builddirs does not expose the installed CMake config"))
    requires, test_requires = set(REQUIRES.findall(text)), set(TEST_REQUIRES.findall(text))
    if requires != set(repo.deps):
        found.append(Finding("deps", f"conanfile.py requires {sorted(requires)} but untwine.toml declares {sorted(repo.deps)}"))
    if test_requires != set(repo.test_deps):
        found.append(Finding("deps", f"conanfile.py test-requires {sorted(test_requires)} but untwine.toml declares {sorted(repo.test_deps)}"))
    if '"python_version"' in text:
        for dep in sorted(requires):
            other = m.repos.get(f"pxr-{dep}")
            if other and other.has_python and f'options["pxr-{dep}"].python_version' not in text:
                found.append(Finding("python-forward", f"conanfile.py does not forward python_version to pxr-{dep}"))
    return found


def _bridges(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    text = tree.text(f"src/pxr/{repo.lib}/pxr.h.in")
    if text is None:
        return []
    return [Finding("bridge", f"pxr.h.in does not include <pxr/{dep}/pxr.h> for direct dependency {dep}")
            for dep in repo.deps if dep not in ("boost", "pegtl") and f"<pxr/{dep}/pxr.h>" not in text]


def _gitignore(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    text = tree.text(".gitignore") or ""
    found = [] if "CMakeUserPresets.json" in text else [Finding("gitignore", ".gitignore does not ignore CMakeUserPresets.json")]
    return found + [Finding("gitignore", f".gitignore: unanchored {match.group(0)} (prefix with /)")
                    for match in UNANCHORED.finditer(text)]


def _windows_path(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    text = tree.text("test/CMakeLists.txt") or ""
    if "TARGET_RUNTIME_DLL_DIRS" not in text or repo.lib == "tf" or "tf" not in transitive_deps(m, repo):
        return []
    return [] if "onetbb_" in text else [Finding("windows-path", "test/CMakeLists.txt Windows PATH override lacks the onetbb directories")]


def _ci(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    found, urls, actions = [], set(), {}
    for rel in (r for r in tree.files if r.startswith(".github/workflows/")):
        for no, line in tree.lines(rel):
            if re.search(r"defender|Add-MpPreference", line, re.I):
                found.append(Finding("ci", f"{rel}:{no}: Windows Defender step"))
            if re.match(r"\s*schedule:", line):
                found.append(Finding("ci", f"{rel}:{no}: scheduled trigger"))
            url = re.search(r"CONAN_REMOTE_URL:\s*(\S+)", line)
            if url:
                urls.add(url.group(1))
            for action in re.finditer(r"uses:\s*(actions/[\w-]+)@(\S+)", line):
                actions.setdefault(action.group(1), set()).add(action.group(2))
    if len(urls) > 1:
        found.append(Finding("ci", f"workflows disagree on CONAN_REMOTE_URL: {sorted(urls)}"))
    found += [Finding("ci", f"several versions of {name}: {sorted(v)}") for name, v in sorted(actions.items()) if len(v) > 1]
    return found


def _pins(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    return [Finding("python-pin", f"{rel}:{no}: exact sibling pin (use ==YY.M.*): {line.strip()}")
            for rel in ("pyproject.toml", "pyproject-dev.toml") for no, line in tree.lines(rel) if EXACT_PIN.search(line)]


def _markers(tree: _Tree, m: Manifest, repo: Repo) -> list[Finding]:
    return [Finding("todo", h) for h in tree.grep(re.compile(r"TODO\(untwine\)"))]


def check_history(path: Path, repo: Repo, base: str, head: str) -> list[Finding]:
    out = gitutil.git(path, "log", "--format=%H%x1f%s%x1f%B%x1e", f"{base}..{head}")
    found, subjects = [], []
    for entry in (e.strip("\n") for e in out.split("\x1e") if e.strip()):
        sha, subject, body = entry.split("\x1f", 2)
        subjects.append(subject)
        if AI_ATTRIBUTION.search(body):
            found.append(Finding("ai-attribution", f"{sha[:12]} {subject}"))
    for subject in (RESTRUCTURE.format(lib=repo.lib), CONFIG):
        if subjects.count(subject) != 1:
            found.append(Finding("history", f"expected one {subject!r} commit, found {subjects.count(subject)}"))
    found += [Finding("history", f"unsquashed {s!r}") for s in subjects if s.startswith(("fixup! ", "amend! ", "squash! "))]
    return found


def run_checks(path: Path, m: Manifest, repo: Repo, base: str = "open-usd", head: str = "HEAD") -> list[Finding]:
    tree = _Tree(path)
    found: list[Finding] = []
    for check in (_source, _layout, _cmake, _conan, _bridges, _gitignore, _windows_path, _ci, _pins, _markers):
        found += check(tree, m, repo)
    return found + check_history(path, repo, base, head)


def find_commit(repo: Path, base: str, head: str, subject: str) -> str:
    out = gitutil.git(repo, "log", "--format=%H%x00%s", f"{base}..{head}")
    hits = [line.split("\0", 1)[0] for line in out.splitlines() if line.split("\0", 1)[1] == subject]
    if len(hits) != 1:
        raise UntwineError(f"expected exactly one commit {subject!r} in {base}..{head}, found {len(hits)}")
    return hits[0]


def _changed_lines(a: bytes, b: bytes) -> int:
    diff = difflib.ndiff(a.decode(errors="replace").splitlines(), b.decode(errors="replace").splitlines())
    return sum(1 for line in diff if line[:2] in ("+ ", "- "))


def _whitespace_only(a: bytes, b: bytes) -> bool:
    split = lambda data: [line.split() for line in data.decode(errors="replace").splitlines()]  # noqa: E731
    return split(a) == split(b)


def diff_report(path: Path, repo: Repo, base: str = "open-usd", head: str = "HEAD") -> list[tuple[str, int, bool]]:
    """Files that differ from transform(open-usd); intentional fixes are expected here."""
    restructure = find_commit(path, base, head, RESTRUCTURE.format(lib=repo.lib))
    pathmap = transform.learn(path, restructure, repo.lib, repo.path_rules)
    rows = []
    for standalone in sorted(gitutil.paths(path, head)):
        source = pathmap.upstream(standalone)
        if source is None or not standalone.startswith(("src/", "test/")):
            continue
        old, current = gitutil.blob(path, base, source), gitutil.blob(path, head, standalone)
        if old is None or current is None:
            continue
        expected = transform.transform_bytes(old, repo.lib, special=repo.kind == "special")
        if expected != current:
            rows.append((standalone, _changed_lines(expected, current), _whitespace_only(expected, current)))
    return rows
```

- [ ] **Step 5: Write `untwine_cli/cli.py`**

Later tasks insert their commands immediately above `def main`.

```python
"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, manifest, verify
from .manifest import Manifest

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "untwine.toml"

Command = tuple[str, str, Callable[[argparse.ArgumentParser], None], Callable[[Manifest, argparse.Namespace], int]]
COMMANDS: list[Command] = []


def confirm(lines: list[str]) -> bool:
    print("\n".join(lines))
    if not sys.stdin.isatty():
        print("refusing: confirmation needs an interactive terminal")
        return False
    return input("Type 'yes' to continue: ").strip() == "yes"


def current_tag(m: Manifest, given: str | None) -> str:
    if given:
        return given
    work = m.state_dir / "work"
    tags = sorted(p.name for p in work.iterdir() if p.is_dir()) if work.is_dir() else []
    if len(tags) != 1:
        raise UntwineError(f"cannot infer the release in progress (found {tags or 'none'}); pass --tag")
    return tags[0]


def _verify_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--diff", action="store_true", help="also list files that differ from transform(open-usd)")


def _verify_run(m: Manifest, args: argparse.Namespace) -> int:
    failed = False
    for repo in manifest.selected(m, args.repos):
        path = m.repo_path(repo.name)
        findings = verify.run_checks(path, m, repo)
        print(f"{repo.name}: {'ok' if not findings else f'{len(findings)} problem(s)'}")
        for finding in findings:
            print(f"  {finding.check}: {finding.message}")
        failed |= bool(findings)
        if args.diff and repo.kind == "library":
            for rel, count, ws in verify.diff_report(path, repo):
                print(f"  differs from open-usd: {rel} ({count} lines){' whitespace-only' if ws else ''}")
    return 1 if failed else 0


COMMANDS.append(("verify", "check repositories against the Untwine conventions", _verify_args, _verify_run))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="untwine")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    sub = parser.add_subparsers(dest="command", required=True)
    runners = {}
    for name, help_text, add_arguments, run in COMMANDS:
        add_arguments(sub.add_parser(name, help=help_text))
        runners[name] = run
    args = parser.parse_args(argv)
    try:
        return runners[args.command](manifest.load(args.manifest), args)
    except UntwineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_verify -v`
Expected: 6 tests PASS

- [ ] **Step 7: Run the whole suite and the real verify**

Run: `python3 -m unittest discover -s tests -t . -v && ./untwine verify`
Expected: all tests PASS; every real repository prints `ok` and the command exits 0. If a real repository reports a finding, first check whether it is a real problem (report it to the maintainer) or a check bug (fix the check and add a test case for it).

- [ ] **Step 8: Commit**

```bash
git add untwine_cli/verify.py untwine_cli/cli.py tests/fake_world.py tests/test_verify.py
git commit -m "Add untwine verify and the fake release fixture."
```

---

### Task 6: Git-native release state

**Files:**
- Create: `untwine_cli/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `gitutil`, `Manifest`.
- Produces: `ref(tag, name)`, `notes_ref(tag)`, `sync_branch(tag)`, `sync_open_usd(tag)`, `worktree_path(m, tag, repo_name)`; `Note(source, resolution, files)` with `render()` / `parse(text)`; `write_note(repo, tag, commit, note)`, `read_note(repo, tag, commit)`; `write_json(repo, tag, name, data)`, `read_json(repo, tag, name)`, `delete_ref(repo, refname)`; `record_old_tips(repo, tag)`; `source_commits(repo, tag) -> list[str]`; `anchor(repo, tag) -> str | None` (finalized, else replayed marker, else sync tip); `replayed_map(repo, tag, sources) -> dict[str, tuple[str | None, Note]]`; `LocalStatus` dataclass (`state, sources, replayed, pending, attention, review, tip, finalized, promoted`); `local_status(m, tag, repo_name) -> LocalStatus`. States: `pending`, `filtered`, `replaying`, `needs-attention`, `replayed`, `verified`, `promoted`.

Refs used per repository and release:

| Ref | Written by | Meaning |
| --- | --- | --- |
| `refs/untwine/<tag>/old-main`, `old-open-usd` | preflight | tips recorded once |
| `refs/untwine/<tag>/upstream` | sync | filtered subtree at the new tag |
| `refs/untwine/<tag>/pending` (blob) | replay | stopped conflict `{source, auto, manual}` |
| `refs/untwine/<tag>/replayed` | replay | sync tip when every source commit was accounted for |
| `refs/untwine/<tag>/review` (blob) | sync/finalize | `{attention: [...], review: [...]}` |
| `refs/untwine/<tag>/finalized` | finalize | sync tip after finalize |
| `refs/untwine/<tag>/promoted` | promote | tip pushed to `main` |
| `refs/notes/untwine/<tag>` | replay/finalize | one note per replayed commit, and on skipped source commits |

- [ ] **Step 1: Write the failing tests**

`tests/test_state.py`:

```python
from tests.helpers import TempTest, commit, init, sh_git
from untwine_cli import gitutil, state
from untwine_cli.manifest import Manifest, Repo


class StateTest(TempTest):
    def setUp(self):
        super().setUp()
        self.ws = self.tmp / "ws"
        self.repo = init(self.ws / "pxr-foo")
        self.base = commit(self.repo, {"a": "1\n"}, "upstream")
        sh_git(self.repo, "branch", "open-usd")
        self.s1 = commit(self.repo, {"b": "1\n"}, "one")
        self.s2 = commit(self.repo, {"c": "1\n"}, "two")
        self.m = Manifest(self.ws / "toolbox" / "untwine.toml", self.ws, "url", "untwine", "v26.08", "2023.1.0",
                          {"pxr-foo": Repo("pxr-foo", upstream="pxr/base/foo")})

    def test_note_round_trip(self):
        note = state.Note(self.s1, "auto", ("src/a b.h", "x.h"))
        self.assertEqual(state.Note.parse(note.render()), note)
        state.write_note(self.repo, "v26.11", self.s2, note)
        self.assertEqual(state.read_note(self.repo, "v26.11", self.s2), note)
        self.assertIsNone(state.read_note(self.repo, "v26.12", self.s2))

    def test_json_round_trip(self):
        state.write_json(self.repo, "v26.11", "review", {"attention": ["x"], "review": []})
        self.assertEqual(state.read_json(self.repo, "v26.11", "review"), {"attention": ["x"], "review": []})
        state.delete_ref(self.repo, state.ref("v26.11", "review"))
        self.assertIsNone(state.read_json(self.repo, "v26.11", "review"))

    def test_record_old_tips_only_once(self):
        state.record_old_tips(self.repo, "v26.11")
        commit(self.repo, {"d": "1\n"}, "three")
        state.record_old_tips(self.repo, "v26.11")
        self.assertEqual(gitutil.rev(self.repo, state.ref("v26.11", "old-main")), self.s2)
        self.assertEqual(state.source_commits(self.repo, "v26.11"), [self.s1, self.s2])

    def test_status_progression(self):
        tag = "v26.11"
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "pending")
        state.record_old_tips(self.repo, tag)
        sh_git(self.repo, "update-ref", state.ref(tag, "upstream"), self.base)
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "filtered")
        sh_git(self.repo, "branch", state.sync_open_usd(tag), self.base)
        sh_git(self.repo, "branch", state.sync_branch(tag), self.base)
        sh_git(self.repo, "checkout", "-q", state.sync_branch(tag))
        new1 = commit(self.repo, {"b": "1\n"}, "one")
        state.write_note(self.repo, tag, new1, state.Note(self.s1, "clean"))
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "replaying")
        state.write_note(self.repo, tag, self.s2, state.Note(self.s2, "empty"))
        sh_git(self.repo, "update-ref", state.ref(tag, "replayed"), new1)
        status = state.local_status(self.m, tag, "pxr-foo")
        self.assertEqual(status.state, "replayed")
        self.assertEqual(status.replayed[self.s2][0], None)
        sh_git(self.repo, "update-ref", state.ref(tag, "finalized"), new1)
        state.write_json(self.repo, tag, "review", {"attention": [], "review": ["read me"]})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "verified")
        state.write_json(self.repo, tag, "review", {"attention": ["broken"], "review": []})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "needs-attention")
        sh_git(self.repo, "update-ref", state.ref(tag, "promoted"), new1)
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "promoted")

    def test_attention_before_replay(self):
        tag = "v26.11"
        state.record_old_tips(self.repo, tag)
        state.write_json(self.repo, tag, "review", {"attention": ["filter failed"], "review": []})
        self.assertEqual(state.local_status(self.m, tag, "pxr-foo").state, "needs-attention")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_state -v`
Expected: FAIL with `ImportError: cannot import name 'state'`

- [ ] **Step 3: Write `untwine_cli/state.py`**

```python
"""Release state stored in git: refs, notes, and JSON blobs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil
from .manifest import Manifest


def ref(tag: str, name: str) -> str:
    return f"refs/untwine/{tag}/{name}"


def notes_ref(tag: str) -> str:
    return f"refs/notes/untwine/{tag}"


def sync_branch(tag: str) -> str:
    return f"sync/{tag}"


def sync_open_usd(tag: str) -> str:
    return f"sync/{tag}-open-usd"


def worktree_path(m: Manifest, tag: str, repo_name: str) -> Path:
    return m.state_dir / "work" / tag / repo_name


@dataclass(frozen=True)
class Note:
    source: str
    resolution: str  # clean | auto | manual | empty
    files: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"replayed-from {self.source}", f"resolution: {self.resolution}"]
        return "\n".join(lines + [f"file: {f}" for f in self.files]) + "\n"

    @classmethod
    def parse(cls, text: str) -> Note | None:
        source = resolution = None
        files = []
        for line in text.splitlines():
            if line.startswith("replayed-from "):
                source = line.split(" ", 1)[1].strip()
            elif line.startswith("resolution: "):
                resolution = line.split(": ", 1)[1].strip()
            elif line.startswith("file: "):
                files.append(line[len("file: "):])
        return cls(source, resolution, tuple(files)) if source and resolution else None


def write_note(repo: Path, tag: str, commit: str, note: Note) -> None:
    gitutil.run(repo, "notes", "--ref", notes_ref(tag), "add", "-f", "-F", "-", commit, input=note.render().encode())


def read_note(repo: Path, tag: str, commit: str) -> Note | None:
    proc = gitutil.run(repo, "notes", "--ref", notes_ref(tag), "show", commit, check=False)
    return Note.parse(proc.stdout.decode()) if proc.returncode == 0 else None


def write_json(repo: Path, tag: str, name: str, data) -> None:
    payload = json.dumps(data, indent=2, sort_keys=True).encode()
    sha = gitutil.git(repo, "hash-object", "-w", "--stdin", input=payload)
    gitutil.run(repo, "update-ref", ref(tag, name), sha)


def read_json(repo: Path, tag: str, name: str):
    proc = gitutil.run(repo, "cat-file", "blob", ref(tag, name), check=False)
    return json.loads(proc.stdout) if proc.returncode == 0 else None


def delete_ref(repo: Path, refname: str) -> None:
    gitutil.run(repo, "update-ref", "-d", refname, check=False)


def record_old_tips(repo: Path, tag: str) -> None:
    for name, branch in (("old-main", "main"), ("old-open-usd", "open-usd")):
        if not gitutil.ref_exists(repo, ref(tag, name)):
            gitutil.run(repo, "update-ref", ref(tag, name), gitutil.rev(repo, f"refs/heads/{branch}"))


def source_commits(repo: Path, tag: str) -> list[str]:
    span = f"{ref(tag, 'old-open-usd')}..{ref(tag, 'old-main')}"
    return gitutil.git(repo, "rev-list", "--reverse", span).split()


def anchor(repo: Path, tag: str) -> str | None:
    for name in ("finalized", "replayed"):
        sha = gitutil.rev(repo, ref(tag, name))
        if sha:
            return sha
    return gitutil.rev(repo, sync_branch(tag))


def replayed_map(repo: Path, tag: str, sources: list[str]) -> dict[str, tuple[str | None, Note]]:
    result: dict[str, tuple[str | None, Note]] = {}
    head, base = anchor(repo, tag), gitutil.rev(repo, sync_open_usd(tag))
    if head and base:
        for commit in gitutil.git(repo, "rev-list", "--reverse", f"{base}..{head}").split():
            note = read_note(repo, tag, commit)
            if note:
                result[note.source] = (commit, note)
    for source in sources:
        note = read_note(repo, tag, source)
        if note and note.source == source and note.resolution == "empty":
            result.setdefault(source, (None, note))
    return result


@dataclass
class LocalStatus:
    state: str
    sources: list[str] = field(default_factory=list)
    replayed: dict[str, tuple[str | None, Note]] = field(default_factory=dict)
    pending: dict | None = None
    attention: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    tip: str | None = None
    finalized: str | None = None
    promoted: bool = False


def local_status(m: Manifest, tag: str, repo_name: str) -> LocalStatus:
    repo = m.repo_path(repo_name)
    if not repo.is_dir() or not gitutil.ref_exists(repo, ref(tag, "old-main")):
        return LocalStatus("pending")
    review = read_json(repo, tag, "review") or {}
    status = LocalStatus(
        "filtered" if gitutil.ref_exists(repo, ref(tag, "upstream")) else "pending",
        sources=source_commits(repo, tag), tip=gitutil.rev(repo, sync_branch(tag)),
        attention=list(review.get("attention", [])), review=list(review.get("review", [])),
        finalized=gitutil.rev(repo, ref(tag, "finalized")),
        promoted=gitutil.ref_exists(repo, ref(tag, "promoted")),
    )
    if status.promoted:
        status.state = "promoted"
        return status
    if status.tip:
        status.replayed = replayed_map(repo, tag, status.sources)
        status.pending = read_json(repo, tag, "pending")
        worktree = worktree_path(m, tag, repo_name)
        picking = worktree.exists() and gitutil.ref_exists(worktree, "CHERRY_PICK_HEAD")
        if status.pending or picking:
            status.state = "needs-attention"
        elif not gitutil.ref_exists(repo, ref(tag, "replayed")):
            status.state = "replaying"
        elif status.finalized != status.tip:
            status.state = "replayed"
        else:
            status.state = "verified"
    if status.attention:
        status.state = "needs-attention"
    return status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_state -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add untwine_cli/state.py tests/test_state.py
git commit -m "Store release state in git refs and notes."
```

---

### Task 7: Replay with provable conflict resolution

**Files:**
- Create: `untwine_cli/replay.py`
- Test: `tests/test_replay.py`

**Interfaces:**
- Consumes: `state.*`, `transform.learn/transform_bytes`, `verify.find_commit/RESTRUCTURE`, `upstream.*`, `gitutil.*`.
- Produces: `ReplayError(UntwineError)`; `path_map(m, repo, tag) -> PathMap`; `advance_open_usd(repo_path, tag) -> str`; `ensure_worktree(m, repo_name, tag) -> Path`; `Resolution(path, content: bytes | None)`; `prove(clone, repo, tag, source, path, pathmap) -> Resolution | None`; `replay(m, repo, tag) -> bool` (True when every source commit is accounted for); `resolve(m, repo, tag) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_replay.py`:

```python
from tests.fake_world import FakeWorld, upstream_tree
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import gitutil, replay, state, transform, upstream

TAG = "v26.11"


def prepare(world: FakeWorld):
    m, repo = world.m, world.m.repos["pxr-foo"]
    state.record_old_tips(world.clone, TAG)
    scratch = upstream.filter_subtree(upstream.ensure_mirror(m), TAG, repo.upstream, m.state_dir / "tmp")
    upstream.import_filtered(world.clone, scratch, state.ref(TAG, "upstream"))
    replay.advance_open_usd(world.clone, TAG)
    return m, repo, replay.ensure_worktree(m, repo.name, TAG)


def notes(world):
    status = state.local_status(world.m, TAG, "pxr-foo")
    return [(status.replayed[s][1].resolution, status.replayed[s][1].files) for s in status.sources]


@needs_filter_repo
class ReplayTest(TempTest):
    def test_clean_replay(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual([r for r, _ in notes(world)], ["clean", "clean", "clean"])
        self.assertIn("return 2", (wt / "src/pxr/foo/bar.cpp").read_text())
        self.assertTrue(gitutil.ref_exists(world.clone, state.ref(TAG, "replayed")))

    def test_provable_auto_resolve(self):
        world = FakeWorld(self.tmp, {"include-conflict"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[0], ("auto", ("src/pxr/foo/bar.cpp",)))
        expected = transform.transform_text(upstream_tree(frozenset({"include-conflict"}))["pxr/base/foo/bar.cpp"], "foo")
        self.assertEqual((wt / "src/pxr/foo/bar.cpp").read_text(), expected)

    def test_provable_delete(self):
        world = FakeWorld(self.tmp, {"delete"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[0], ("auto", ("src/pxr/foo/old.h",)))
        self.assertFalse((wt / "src/pxr/foo/old.h").exists())

    def test_empty_pick(self):
        world = FakeWorld(self.tmp, {"fix-upstreamed"})
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual([r for r, _ in notes(world)], ["clean", "clean", "empty"])

    def test_manual_conflict_then_resolve(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        m, repo, wt = prepare(world)
        self.assertFalse(replay.replay(m, repo, TAG))
        pending = state.read_json(world.clone, TAG, "pending")
        self.assertEqual(pending["manual"], ["src/pxr/foo/bar.h"])
        self.assertEqual(state.local_status(m, TAG, "pxr-foo").state, "needs-attention")
        self.assertFalse(replay.replay(m, repo, TAG))
        with self.assertRaisesRegex(replay.ReplayError, "unresolved"):
            replay.resolve(m, repo, TAG)
        bar = wt / "src/pxr/foo/bar.h"
        text = bar.read_text()
        start, end = text.index("<<<<<<<"), text.index(">>>>>>>")
        bar.write_text(text[:start] + "int FooBar(int) noexcept;\n" + text[text.index("\n", end) + 1:])
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        replay.resolve(m, repo, TAG)
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(notes(world)[2], ("manual", ("src/pxr/foo/bar.h",)))
        self.assertIsNone(state.read_json(world.clone, TAG, "pending"))

    def test_resolve_rejects_markers(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        m, repo, wt = prepare(world)
        replay.replay(m, repo, TAG)
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        with self.assertRaisesRegex(replay.ReplayError, "conflict markers"):
            replay.resolve(m, repo, TAG)

    def test_replay_twice_does_not_duplicate(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        m, repo, wt = prepare(world)
        replay.replay(m, repo, TAG)
        tip = gitutil.rev(wt, "HEAD")
        self.assertTrue(replay.replay(m, repo, TAG))
        self.assertEqual(gitutil.rev(wt, "HEAD"), tip)

    def test_open_usd_must_be_an_ancestor(self):
        world = FakeWorld(self.tmp)
        state.record_old_tips(world.clone, TAG)
        sh_git(world.clone, "fetch", "-q", str(world.upstream), "v26.11")
        sh_git(world.clone, "update-ref", state.ref(TAG, "upstream"), sh_git(world.upstream, "rev-parse", "v26.11"))
        with self.assertRaisesRegex(replay.ReplayError, "ancestor"):
            replay.advance_open_usd(world.clone, TAG)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_replay -v`
Expected: FAIL with `ImportError: cannot import name 'replay'`

- [ ] **Step 3: Write `untwine_cli/replay.py`**

```python
"""Replay Untwine commits onto a new open-usd history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import UntwineError, gitutil, state, transform, verify
from .manifest import Manifest, Repo


class ReplayError(UntwineError):
    pass


@dataclass(frozen=True)
class Resolution:
    path: str
    content: bytes | None  # None deletes the path


def path_map(m: Manifest, repo: Repo, tag: str) -> transform.PathMap:
    clone = m.repo_path(repo.name)
    restructure = verify.find_commit(clone, state.ref(tag, "old-open-usd"), state.ref(tag, "old-main"),
                                     verify.RESTRUCTURE.format(lib=repo.lib))
    return transform.learn(clone, restructure, repo.lib, repo.path_rules)


def advance_open_usd(repo: Path, tag: str) -> str:
    old = gitutil.rev(repo, state.ref(tag, "old-open-usd"))
    new = gitutil.rev(repo, state.ref(tag, "upstream"))
    if not gitutil.is_ancestor(repo, old, new):
        raise ReplayError("open-usd is not an ancestor of the filtered upstream history; the manifest "
                          "'upstream' path probably differs from the one that built open-usd")
    gitutil.run(repo, "branch", "--force", state.sync_open_usd(tag), new)
    return new


def ensure_worktree(m: Manifest, repo_name: str, tag: str) -> Path:
    clone, worktree = m.repo_path(repo_name), state.worktree_path(m, tag, repo_name)
    if worktree.exists():
        return worktree
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if gitutil.rev(clone, state.sync_branch(tag)):
        gitutil.run(clone, "worktree", "add", "--quiet", str(worktree), state.sync_branch(tag))
    else:
        gitutil.run(clone, "worktree", "add", "--quiet", "-b", state.sync_branch(tag), str(worktree),
                    state.sync_open_usd(tag))
    return worktree


def prove(clone: Path, repo: Repo, tag: str, source: str, path: str, pathmap: transform.PathMap) -> Resolution | None:
    """Resolve `path` only when `source` left it exactly transform(old upstream)."""
    upstream_path = pathmap.upstream(path)
    if upstream_path is None:
        return None
    old = gitutil.blob(clone, state.ref(tag, "old-open-usd"), upstream_path)
    ours = gitutil.blob(clone, source, path)
    special = repo.kind == "special"
    if old is None or ours is None or ours != transform.transform_bytes(old, repo.lib, special=special):
        return None
    new = gitutil.blob(clone, state.ref(tag, "upstream"), upstream_path)
    return Resolution(path, None if new is None else transform.transform_bytes(new, repo.lib, special=special))


def _unmerged(worktree: Path) -> list[str]:
    out = gitutil.run(worktree, "diff", "--name-only", "--diff-filter=U", "-z").stdout.decode()
    return sorted({p for p in out.split("\0") if p})


def _picking(worktree: Path) -> bool:
    return gitutil.ref_exists(worktree, "CHERRY_PICK_HEAD")


def _apply(worktree: Path, resolution: Resolution) -> None:
    if resolution.content is None:
        gitutil.run(worktree, "rm", "-q", "--force", "--ignore-unmatch", "--", resolution.path)
        return
    target = worktree / resolution.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resolution.content)
    gitutil.run(worktree, "add", "--", resolution.path)


def _finish_pick(worktree: Path, clone: Path, tag: str, source: str, resolution: str, files: list[str]) -> None:
    if gitutil.ok(worktree, "diff", "--cached", "--quiet"):
        gitutil.run(worktree, "cherry-pick", "--skip")
        state.write_note(clone, tag, source, state.Note(source, "empty", tuple(files)))
        return
    gitutil.run(worktree, "cherry-pick", "--continue")
    state.write_note(clone, tag, gitutil.rev(worktree, "HEAD"), state.Note(source, resolution, tuple(files)))


def replay(m: Manifest, repo: Repo, tag: str) -> bool:
    clone, worktree = m.repo_path(repo.name), state.worktree_path(m, tag, repo.name)
    if gitutil.ref_exists(clone, state.ref(tag, "replayed")):
        return True
    if _picking(worktree) or state.read_json(clone, tag, "pending"):
        return False
    span = f"{state.ref(tag, 'old-open-usd')}..{state.ref(tag, 'old-main')}"
    if gitutil.git(clone, "rev-list", "--min-parents=2", span):
        raise ReplayError("main contains merge commits; replay needs a linear history")
    sources = state.source_commits(clone, tag)
    done = state.replayed_map(clone, tag, sources)
    todo = [s for s in sources if s not in done]
    if done and todo and sources.index(todo[0]) < max(sources.index(s) for s in done):
        raise ReplayError("the sync branch no longer matches the recorded replay; run `untwine discard` and sync again")
    pathmap = None
    for source in todo:
        proc = gitutil.run(worktree, "cherry-pick", source, check=False)
        if proc.returncode == 0:
            state.write_note(clone, tag, gitutil.rev(worktree, "HEAD"), state.Note(source, "clean"))
            continue
        unmerged = _unmerged(worktree)
        if not unmerged:
            if _picking(worktree) and gitutil.ok(worktree, "diff", "--cached", "--quiet"):
                gitutil.run(worktree, "cherry-pick", "--skip")
                state.write_note(clone, tag, source, state.Note(source, "empty"))
                continue
            raise ReplayError(f"cherry-pick of {source[:12]} failed: {proc.stderr.decode().strip()}")
        pathmap = pathmap or path_map(m, repo, tag)
        auto, manual = [], []
        for path in unmerged:
            resolution = prove(clone, repo, tag, source, path, pathmap)
            if resolution is None:
                manual.append(path)
            else:
                _apply(worktree, resolution)
                auto.append(path)
        if manual:
            state.write_json(clone, tag, "pending", {"source": source, "auto": auto, "manual": manual})
            return False
        _finish_pick(worktree, clone, tag, source, "auto", auto)
    gitutil.run(clone, "update-ref", state.ref(tag, "replayed"), gitutil.rev(worktree, "HEAD"))
    return True


def _conflict_markers(worktree: Path) -> list[str]:
    names = gitutil.run(worktree, "diff", "--cached", "--name-only", "-z").stdout.decode().split("\0")
    found = []
    for name in filter(None, names):
        try:
            text = (worktree / name).read_text()
        except (FileNotFoundError, UnicodeDecodeError, IsADirectoryError):
            continue
        if re.search(r"^(<{7} |>{7} |={7}$)", text, re.M):
            found.append(name)
    return found


def resolve(m: Manifest, repo: Repo, tag: str) -> None:
    clone, worktree = m.repo_path(repo.name), state.worktree_path(m, tag, repo.name)
    pending = state.read_json(clone, tag, "pending")
    if not pending:
        raise ReplayError(f"{repo.name} has no stopped conflict to resolve")
    if not _picking(worktree):
        raise ReplayError("no cherry-pick is in progress in the worktree; it was committed or aborted by hand. "
                          f"Run `untwine discard {tag} {repo.name}` and sync again")
    unresolved = _unmerged(worktree)
    if unresolved:
        raise ReplayError("unresolved paths remain (git add them when fixed): " + ", ".join(unresolved))
    markers = _conflict_markers(worktree)
    if markers:
        raise ReplayError("conflict markers remain in: " + ", ".join(markers))
    _finish_pick(worktree, clone, tag, pending["source"], "manual", [*pending["manual"], *pending["auto"]])
    state.delete_ref(clone, state.ref(tag, "pending"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_replay -v`
Expected: 8 tests PASS. If `test_provable_delete` fails because git reports the rename/delete conflict at the *upstream* path (`old.h`) instead of `src/pxr/foo/old.h`, print `_unmerged(wt)` to confirm, then map such paths in `replay()` with `pathmap.learned.get(path, path)` before calling `prove`, resolving both the upstream path (remove) and the standalone path.

- [ ] **Step 5: Commit**

```bash
git add untwine_cli/replay.py tests/test_replay.py
git commit -m "Replay Untwine commits with provable conflict resolution."
```

---

### Task 8: Version bump and finalize

**Files:**
- Create: `untwine_cli/versions.py`, `untwine_cli/finalize.py`
- Test: `tests/test_versions.py`, `tests/test_finalize.py`

**Interfaces:**
- Consumes: `manifest.version_forms`, `replay.path_map/ReplayError`, `verify.run_checks/find_commit/RESTRUCTURE/CONFIG`, `upstream.parse_pxr_library/derive_deps/expected_deps`, `state.*`, `transform.transform_bytes`.
- Produces: `versions.bump(root, old_tag, new_tag) -> list[str]`; `versions.leftovers(root, old_tag) -> list[str]`; `finalize.place_new_files(m, repo, tag, worktree, pathmap) -> list[str]`; `finalize.dependency_items(m, repo, tag) -> tuple[list[str], list[str]]`; `finalize.finalize(m, repo, tag) -> None`.

- [ ] **Step 1: Write the failing version tests**

`tests/test_versions.py`:

```python
from tests.fake_world import config_files
from tests.helpers import TempTest, write
from untwine_cli import versions


class VersionsTest(TempTest):
    def test_bump_and_leftovers(self):
        write(self.tmp, config_files("v26.08"))
        changed = versions.bump(self.tmp, "v26.08", "v26.11")
        self.assertEqual(sorted(changed), ["CMakeLists.txt", "README.md", "cmake/pxr-foo-config.cmake.in",
                                           "conanfile.py", "pyproject.toml"])
        self.assertEqual({rel: (self.tmp / rel).read_text() for rel in config_files("v26.11")}, config_files("v26.11"))
        self.assertEqual(versions.leftovers(self.tmp, "v26.08"), [])
        self.assertIn('"pxr-tbb==2023.1.0.*"', (self.tmp / "pyproject.toml").read_text())

    def test_same_tag_is_a_no_op(self):
        write(self.tmp, config_files("v26.08"))
        self.assertEqual(versions.bump(self.tmp, "v26.08", "v26.08"), [])

    def test_leftovers_reported(self):
        write(self.tmp, {"README.md": "see 26.8 and 0.26.8 and v26.08 but not 26.80 or 2026.8\n"})
        self.assertEqual(len(versions.leftovers(self.tmp, "v26.08")), 1)
```

- [ ] **Step 2: Write `untwine_cli/versions.py`**

```python
"""Rewrite release-derived version strings."""

from __future__ import annotations

import re
from pathlib import Path

from .manifest import version_forms

FILES = ("CMakeLists.txt", "README.md", "conanfile.py", "pyproject.toml", "pyproject-dev.toml")


def _files(root: Path) -> list[Path]:
    return [root / f for f in FILES if (root / f).is_file()] + sorted((root / "cmake").glob("*.in"))


def _patterns(tag: str) -> list[re.Pattern]:
    cmake, short = version_forms(tag)
    return [re.compile(rf"(?<![\w.]){re.escape(form)}(?!\d)") for form in (tag, cmake, short)]


def bump(root: Path, old_tag: str, new_tag: str) -> list[str]:
    if old_tag == new_tag:
        return []
    replacements = list(zip(_patterns(old_tag), (new_tag, *version_forms(new_tag))))
    changed = []
    for path in _files(root):
        text = path.read_text()
        new = text
        for pattern, value in replacements:
            new = pattern.sub(value, new)
        if new != text:
            path.write_text(new)
            changed.append(path.relative_to(root).as_posix())
    return changed


def leftovers(root: Path, old_tag: str) -> list[str]:
    patterns = _patterns(old_tag)
    return [f"{path.relative_to(root).as_posix()}:{no}: {line.strip()}"
            for path in _files(root) for no, line in enumerate(path.read_text().splitlines(), 1)
            if any(p.search(line) for p in patterns)]
```

- [ ] **Step 3: Run version tests**

Run: `python3 -m unittest tests.test_versions -v`
Expected: 3 tests PASS

- [ ] **Step 4: Write the failing finalize tests**

`tests/test_finalize.py`:

```python
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from tests.test_replay import TAG, prepare
from untwine_cli import finalize, gitutil, replay, state


@needs_filter_repo
class FinalizeTest(TempTest):
    def run_to_finalized(self, scenarios):
        world = FakeWorld(self.tmp, scenarios)
        m, repo, wt = prepare(world)
        self.assertTrue(replay.replay(m, repo, TAG))
        finalize.finalize(m, repo, TAG)
        return world, m, repo, wt

    def test_bump_autosquash_and_verify(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        subjects = gitutil.git(wt, "log", "--format=%s", f"{state.sync_open_usd(TAG)}..HEAD").splitlines()
        self.assertEqual(subjects, ["bar: mark FooBar noexcept.", "Add minimal release configuration.",
                                    "Restructure the 'foo' library as a standalone package."])
        self.assertIn("VERSION 0.26.11", (wt / "CMakeLists.txt").read_text())
        self.assertEqual(len(status.replayed), 3)

    def test_new_file_is_placed(self):
        world, m, repo, wt = self.run_to_finalized({"new-file"})
        self.assertFalse((wt / "baz.h").exists())
        self.assertEqual((wt / "src/pxr/foo/baz.h").read_text().splitlines()[-1], "#include <pxr/foo/pxr.h>")
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertTrue(any("baz.h" in item for item in status.review))
        restructure = gitutil.git(wt, "log", "--format=%H", "--grep=^Restructure", f"{state.sync_open_usd(TAG)}..HEAD")
        self.assertIn("src/pxr/foo/baz.h", gitutil.git(wt, "show", "--name-only", "--format=", restructure))

    def test_dependency_change_needs_attention(self):
        world, m, repo, wt = self.run_to_finalized({"dep-change"})
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertTrue(any("implies deps ['arch', 'boost', 'tf']" in a for a in status.attention))
        self.assertTrue(any("upstream dependencies changed" in r for r in status.review))

    def test_finalize_is_idempotent(self):
        world, m, repo, wt = self.run_to_finalized({"new-file"})
        tip = gitutil.rev(wt, "HEAD")
        finalize.finalize(m, repo, TAG)
        self.assertEqual(gitutil.rev(wt, "HEAD"), tip)
        self.assertTrue(any("baz.h" in item for item in state.local_status(m, TAG, "pxr-foo").review))

    def test_edit_after_finalize(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        target = gitutil.git(wt, "log", "--format=%H", "--grep=^Add minimal", f"{state.sync_open_usd(TAG)}..HEAD")
        (wt / "README.md").write_text("edited\n")
        sh_git(wt, "commit", "-q", "-a", f"--fixup={target}")
        self.assertEqual(state.local_status(m, TAG, "pxr-foo").state, "replayed")
        finalize.finalize(m, repo, TAG)
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(len(status.replayed), 3)
        self.assertEqual((wt / "README.md").read_text(), "edited\n")

    def test_structure_change_after_finalize_is_reported(self):
        world, m, repo, wt = self.run_to_finalized({"upstream-edit"})
        commit(wt, {"extra.txt": "x\n"}, "An unrelated commit.")
        finalized = gitutil.rev(world.clone, state.ref(TAG, "finalized"))
        finalize.finalize(m, repo, TAG)
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertTrue(any("expected 3 commits" in a for a in status.attention))
        self.assertEqual(gitutil.rev(world.clone, state.ref(TAG, "finalized")), finalized)
```

- [ ] **Step 5: Write `untwine_cli/finalize.py`**

```python
"""Post-replay steps: place new upstream files, check dependencies, bump versions, verify."""

from __future__ import annotations

from pathlib import Path

from . import gitutil, replay, state, transform, upstream, verify, versions
from .manifest import Manifest, Repo


def place_new_files(m: Manifest, repo: Repo, tag: str, worktree: Path, pathmap: transform.PathMap) -> list[str]:
    clone = m.repo_path(repo.name)
    new = gitutil.paths(clone, state.ref(tag, "upstream")) - gitutil.paths(clone, state.ref(tag, "old-open-usd"))
    present = gitutil.paths(worktree, "HEAD")
    items = []
    for upstream_path in sorted(new):
        target, how = pathmap.standalone(upstream_path)
        if upstream_path in present and target != upstream_path:
            if target in present:
                raise replay.ReplayError(f"new upstream file {upstream_path} maps to {target}, which already exists")
            data = gitutil.blob(worktree, "HEAD", upstream_path)
            gitutil.run(worktree, "rm", "-q", "--", upstream_path)
            destination = worktree / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(transform.transform_bytes(data, repo.lib, special=repo.kind == "special"))
            gitutil.run(worktree, "add", "--", target)
        items.append(f"new upstream file {upstream_path} placed at {target} ({how}); register it in CMake if needed")
    return items


def dependency_items(m: Manifest, repo: Repo, tag: str) -> tuple[list[str], list[str]]:
    clone = m.repo_path(repo.name)
    known = {r.lib for r in m.libraries()}

    def derive(revision: str):
        data = gitutil.blob(clone, revision, "CMakeLists.txt")
        return None if data is None else upstream.derive_deps(upstream.parse_pxr_library(data.decode()), known)

    old, new = derive(state.ref(tag, "old-open-usd")), derive(state.ref(tag, "upstream"))
    if new is None:
        return ["upstream CMakeLists.txt not found; cannot check dependencies"], []
    review = []
    if old and (set(old.pxr), old.tbb) != (set(new.pxr), new.tbb):
        review.append(f"upstream dependencies changed: {sorted(old.pxr)} -> {sorted(new.pxr)}, TBB {old.tbb} -> {new.tbb}")
    expected = upstream.expected_deps(repo, new)
    attention = []
    if expected != set(repo.deps):
        attention.append(f"upstream {tag} implies deps {sorted(expected)} but untwine.toml declares "
                         f"{sorted(repo.deps)}; update the manifest and the repository's CMake, Conan, "
                         "pxr.h.in, and pyproject declarations")
    return attention, review


def _fixup(worktree: Path, base: str, subject: str) -> None:
    target = verify.find_commit(worktree, base, "HEAD", subject)
    gitutil.run(worktree, "commit", "--quiet", f"--fixup={target}")


def _squash_and_annotate(worktree: Path, clone: Path, tag: str, base: str) -> list[str]:
    """Fold fixups, then re-attach notes by position from the anchored commits."""
    anchor = state.anchor(clone, tag)
    notes = [state.read_note(clone, tag, c) for c in gitutil.git(clone, "rev-list", "--reverse", f"{base}..{anchor}").split()]
    subjects = gitutil.git(worktree, "log", "--reverse", "--format=%s", f"{base}..HEAD").splitlines()
    if any(s.startswith(("fixup! ", "amend! ", "squash! ")) for s in subjects):
        gitutil.run(worktree, "rebase", "--quiet", "--interactive", "--autosquash", base)
    commits = gitutil.git(worktree, "rev-list", "--reverse", f"{base}..HEAD").split()
    if len(commits) != len(notes):
        return [f"expected {len(notes)} commits on the sync branch after folding fixups, found {len(commits)}; "
                "edit with `git commit --fixup=<target>` only"]
    for commit, note in zip(commits, notes):
        if note:
            state.write_note(clone, tag, commit, note)
    return []


def _resolution_items(clone: Path, tag: str) -> list[str]:
    items = []
    sources = state.source_commits(clone, tag)
    for source, (_, note) in sorted(state.replayed_map(clone, tag, sources).items(), key=lambda kv: sources.index(kv[0])):
        subject = gitutil.git(clone, "log", "-1", "--format=%s", source)
        files = ", ".join(note.files)
        if note.resolution == "auto":
            items.append(f"auto-resolved {files} in '{subject}': each file was exactly transform(old upstream) "
                         "and is now transform(new upstream)")
        elif note.resolution == "manual":
            items.append(f"manually resolved {files} in '{subject}'")
        elif note.resolution == "empty":
            items.append(f"'{subject}' became empty and was skipped: confirm upstream contains the change")
    return items


def finalize(m: Manifest, repo: Repo, tag: str) -> None:
    clone, worktree = m.repo_path(repo.name), state.worktree_path(m, tag, repo.name)
    base = state.sync_open_usd(tag)
    attention: list[str] = []
    review: list[str] = []
    review += place_new_files(m, repo, tag, worktree, replay.path_map(m, repo, tag))
    if not gitutil.ok(worktree, "diff", "--cached", "--quiet"):
        _fixup(worktree, base, verify.RESTRUCTURE.format(lib=repo.lib))
    if repo.kind == "library":
        extra_attention, extra_review = dependency_items(m, repo, tag)
        attention += extra_attention
        review += extra_review
    changed = versions.bump(worktree, m.openusd, tag)
    if changed:
        gitutil.run(worktree, "add", "--", *changed)
        _fixup(worktree, base, verify.CONFIG)
    if m.openusd != tag:
        attention += [f"old version string left: {line}" for line in versions.leftovers(worktree, m.openusd)]
    structure = _squash_and_annotate(worktree, clone, tag, base)
    if structure:
        state.write_json(clone, tag, "review", {"attention": attention + structure, "review": review})
        return
    review += _resolution_items(clone, tag)
    attention += [f"verify: {f.check}: {f.message}" for f in verify.run_checks(worktree, m, repo, base=base)]
    state.write_json(clone, tag, "review", {"attention": attention, "review": review})
    gitutil.run(clone, "update-ref", state.ref(tag, "finalized"), gitutil.rev(worktree, "HEAD"))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_versions tests.test_finalize -v`
Expected: 9 tests PASS

- [ ] **Step 7: Commit**

```bash
git add untwine_cli/versions.py untwine_cli/finalize.py tests/test_versions.py tests/test_finalize.py
git commit -m "Finalize synced repositories: new files, dependencies, versions, verify."
```

---

### Task 9: Sync orchestration, discard, and the `sync`/`resolve`/`discard` commands

**Files:**
- Create: `untwine_cli/sync.py`
- Modify: `untwine_cli/cli.py` (insert above `def main`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `sync.preflight(m, tag, repos)`; `sync.sync_repo(m, repo, tag, mirror)`; `sync.sync(m, tag, names) -> list[Repo]`; `sync.discard(m, tag, names, *, confirm, remote=True)`; CLI commands `sync <tag> [repos] [--dry-run]`, `resolve <repo> [--tag]`, `discard <tag> [repos]`; helper `cli._print_states(m, tag, repos)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sync.py`:

```python
import io
from contextlib import redirect_stdout

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from untwine_cli import UntwineError, cli, gitutil, state, sync

TAG = "v26.11"


@needs_filter_repo
class SyncTest(TempTest):
    def test_full_sync(self):
        world = FakeWorld(self.tmp, {"upstream-edit", "new-file"})
        sync.sync(world.m, TAG, ["pxr-foo"])
        status = state.local_status(world.m, TAG, "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(gitutil.rev(world.clone, "main"), gitutil.rev(world.clone, "origin/main"))

    def test_rerun_is_idempotent(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        sync.sync(world.m, TAG, [])
        tip = gitutil.rev(world.clone, state.sync_branch(TAG))
        sync.sync(world.m, TAG, [])
        self.assertEqual(gitutil.rev(world.clone, state.sync_branch(TAG)), tip)
        self.assertEqual(state.local_status(world.m, TAG, "pxr-foo").state, "verified")

    def test_conflict_stops_then_cli_resolve(self):
        world = FakeWorld(self.tmp, {"fix-conflict"})
        sync.sync(world.m, TAG, [])
        self.assertEqual(state.local_status(world.m, TAG, "pxr-foo").state, "needs-attention")
        wt = state.worktree_path(world.m, TAG, "pxr-foo")
        (wt / "src/pxr/foo/bar.h").write_text((world.clone / "src/pxr/foo/bar.h").read_text())
        sh_git(wt, "add", "src/pxr/foo/bar.h")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(world.manifest_path), "resolve", "pxr-foo"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-foo: verified", out.getvalue())

    def test_preflight_refusals(self):
        world = FakeWorld(self.tmp)
        (world.clone / "README.md").write_text("dirty\n")
        with self.assertRaisesRegex(UntwineError, "uncommitted"):
            sync.sync(world.m, TAG, [])
        sh_git(world.clone, "checkout", "--", "README.md")
        commit(world.clone, {"x": "1\n"}, "local only")
        with self.assertRaisesRegex(UntwineError, "differs from origin/main"):
            sync.sync(world.m, TAG, [])

    def test_errors_are_recorded_per_repository(self):
        world = FakeWorld(self.tmp)
        text = world.manifest_path.read_text().replace('upstream = "pxr/base/foo"', 'upstream = "pxr/base"')
        world.manifest_path.write_text(text)
        from untwine_cli import manifest
        m = manifest.load(world.manifest_path)
        sync.sync(m, TAG, [])
        status = state.local_status(m, TAG, "pxr-foo")
        self.assertEqual(status.state, "needs-attention")
        self.assertIn("ancestor", status.attention[0])

    def test_dry_run_leaves_nothing(self):
        world = FakeWorld(self.tmp, {"upstream-edit"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--manifest", str(world.manifest_path), "sync", TAG, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("pxr-foo: verified", out.getvalue())
        self.assertEqual(gitutil.git(world.clone, "for-each-ref", "refs/untwine", "refs/notes", "refs/heads/sync"), "")
        self.assertFalse(state.worktree_path(world.m, TAG, "pxr-foo").exists())

    def test_rehearsal_on_current_tag_changes_nothing(self):
        world = FakeWorld(self.tmp)
        sync.sync(world.m, "v26.08", [])
        status = state.local_status(world.m, "v26.08", "pxr-foo")
        self.assertEqual(status.state, "verified", status.attention)
        self.assertEqual(gitutil.git(world.clone, "diff", "main", state.sync_branch("v26.08")), "")
        self.assertEqual({note.resolution for _, note in status.replayed.values()}, {"clean"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sync -v`
Expected: FAIL with `ImportError: cannot import name 'sync'`

- [ ] **Step 3: Write `untwine_cli/sync.py`**

```python
"""`untwine sync`: preflight, filter, replay, finalize; and `discard`."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, finalize, gitutil, manifest, replay, state, upstream
from .manifest import Manifest, Repo


def preflight(m: Manifest, tag: str, repos: list[Repo]) -> None:
    manifest.version_forms(tag)
    if subprocess.run(["git", "filter-repo", "--version"], capture_output=True).returncode != 0:
        raise UntwineError("git-filter-repo is required (brew install git-filter-repo)")
    problems = []
    for repo in repos:
        clone = m.repo_path(repo.name)
        if not clone.is_dir():
            problems.append(f"{repo.name}: clone not found at {clone}")
            continue
        if gitutil.git(clone, "status", "--porcelain", "--untracked-files=no"):
            problems.append(f"{repo.name}: working tree has uncommitted changes")
        gitutil.run(clone, "fetch", "--quiet", "origin")
        for branch in ("main", "open-usd"):
            local = gitutil.rev(clone, f"refs/heads/{branch}")
            if local != gitutil.rev(clone, f"refs/remotes/origin/{branch}"):
                problems.append(f"{repo.name}: local {branch} differs from origin/{branch}")
            recorded = gitutil.rev(clone, state.ref(tag, f"old-{branch}"))
            if recorded and recorded != local:
                problems.append(f"{repo.name}: {branch} moved since this release started; run `untwine discard {tag}`")
    if problems:
        raise UntwineError("preflight failed:\n  " + "\n  ".join(problems))
    for repo in repos:
        state.record_old_tips(m.repo_path(repo.name), tag)


def sync_repo(m: Manifest, repo: Repo, tag: str, mirror: Path) -> None:
    clone = m.repo_path(repo.name)
    if not gitutil.ref_exists(clone, state.ref(tag, "upstream")):
        scratch = upstream.filter_subtree(mirror, tag, repo.upstream, m.state_dir / "tmp")
        upstream.import_filtered(clone, scratch, state.ref(tag, "upstream"))
    if not gitutil.rev(clone, state.sync_open_usd(tag)):
        replay.advance_open_usd(clone, tag)
    replay.ensure_worktree(m, repo.name, tag)
    if replay.replay(m, repo, tag):
        finalize.finalize(m, repo, tag)


def sync(m: Manifest, tag: str, names: list[str]) -> list[Repo]:
    repos = manifest.selected(m, names)
    preflight(m, tag, repos)
    mirror = upstream.ensure_mirror(m)
    for repo in repos:
        clone = m.repo_path(repo.name)
        state.delete_ref(clone, state.ref(tag, "review"))
        try:
            sync_repo(m, repo, tag, mirror)
        except UntwineError as exc:
            state.write_json(clone, tag, "review", {"attention": [str(exc)], "review": []})
    return repos


def discard(m: Manifest, tag: str, names: list[str], *, confirm: Callable[[list[str]], bool], remote: bool = True) -> None:
    repos = [r for r in manifest.selected(m, names) if m.repo_path(r.name).is_dir()]
    for repo in repos:
        if gitutil.ref_exists(m.repo_path(repo.name), state.ref(tag, "promoted")):
            raise UntwineError(f"{repo.name} is already promoted for {tag}; nothing to discard")
    branches = (state.sync_branch(tag), state.sync_open_usd(tag))
    remote_refs: dict[str, list[str]] = {}
    if remote:
        for repo in repos:
            out = gitutil.git(m.repo_path(repo.name), "ls-remote", "--heads", "origin", *branches)
            found = [line.split("\t", 1)[1].removeprefix("refs/heads/") for line in out.splitlines()]
            if found:
                remote_refs[repo.name] = found
        if remote_refs and not confirm([f"delete {', '.join(b)} on origin of {name}" for name, b in remote_refs.items()]):
            raise UntwineError("discard cancelled")
    for repo in repos:
        clone = m.repo_path(repo.name)
        worktree = state.worktree_path(m, tag, repo.name)
        if worktree.exists():
            gitutil.run(clone, "worktree", "remove", "--force", str(worktree))
        gitutil.run(clone, "worktree", "prune")
        for branch in branches:
            gitutil.run(clone, "branch", "-D", branch, check=False)
        for refname in gitutil.git(clone, "for-each-ref", "--format=%(refname)", f"refs/untwine/{tag}/").split():
            state.delete_ref(clone, refname)
        state.delete_ref(clone, state.notes_ref(tag))
        if repo.name in remote_refs:
            gitutil.run(clone, "push", "--quiet", "origin", "--delete", *remote_refs[repo.name])
    work = m.state_dir / "work" / tag
    if work.is_dir() and not any(work.iterdir()):
        shutil.rmtree(work)
```

- [ ] **Step 4: Add the commands to `untwine_cli/cli.py`**

Change the import line to:

```python
from . import UntwineError, manifest, replay, state, sync, upstream, verify
```

Insert above `def main`:

```python
def _print_states(m: Manifest, tag: str, repos: list[manifest.Repo]) -> None:
    for repo in repos:
        status = state.local_status(m, tag, repo.name)
        print(f"{repo.name}: {status.state}")
        for item in status.attention:
            print(f"  ! {item}")


def _sync_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tag")
    p.add_argument("repos", nargs="*")
    p.add_argument("--dry-run", action="store_true", help="run everything, report, then discard")


def _sync_run(m: Manifest, args: argparse.Namespace) -> int:
    repos = sync.sync(m, args.tag, args.repos)
    _print_states(m, args.tag, repos)
    if args.dry_run:
        sync.discard(m, args.tag, [r.name for r in repos], confirm=lambda lines: True, remote=False)
        print("dry run: discarded")
    return 0


def _resolve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo")
    p.add_argument("--tag")


def _resolve_run(m: Manifest, args: argparse.Namespace) -> int:
    tag = current_tag(m, args.tag)
    [repo] = manifest.selected(m, [args.repo])
    replay.resolve(m, repo, tag)
    sync.sync_repo(m, repo, tag, upstream.mirror_path(m))
    _print_states(m, tag, [repo])
    return 0


def _discard_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tag")
    p.add_argument("repos", nargs="*")


def _discard_run(m: Manifest, args: argparse.Namespace) -> int:
    sync.discard(m, args.tag, args.repos, confirm=confirm)
    print(f"discarded {args.tag}")
    return 0


COMMANDS.append(("sync", "sync repositories to an OpenUSD release", _sync_args, _sync_run))
COMMANDS.append(("resolve", "continue a sync after fixing a conflict", _resolve_args, _resolve_run))
COMMANDS.append(("discard", "remove all local (and pushed) state of a release", _discard_args, _discard_run))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_sync -v`
Expected: 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add untwine_cli/sync.py untwine_cli/cli.py tests/test_sync.py
git commit -m "Add untwine sync, resolve, and discard."
```

---

### Task 10: Reports and `untwine status`

**Files:**
- Create: `untwine_cli/report.py`
- Modify: `untwine_cli/cli.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `state.*`, `manifest.level_map`, `gitutil`; `github.find_pr/checks/slug` (module created in Task 11; `collect(..., use_github=True)` is only used from Task 11 on, so import `github` lazily inside `collect`).
- Produces: `Row`, `RepoReport` (with `counts`, `display_state`, `waiting`: deps in this release not yet promoted), `collect(m, tag, name, *, use_github=False) -> RepoReport`, `table(m, tag, reports) -> str`, `detail(report, m, tag) -> str`, `pr_body(report, m, tag) -> str`, `range_diff(clone, tag) -> str`, `version_diff(clone, tag) -> str`; CLI `status [repo] [--tag] [--no-github]`; `untwine sync` prints the table.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
import io
from contextlib import redirect_stdout

from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo
from untwine_cli import cli, report, sync

TAG = "v26.11"


@needs_filter_repo
class ReportTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"include-conflict", "new-file"})
        sync.sync(self.world.m, TAG, [])
        self.report = report.collect(self.world.m, TAG, "pxr-foo")

    def test_counts_and_table(self):
        self.assertEqual(self.report.counts["auto"], 1)
        self.assertEqual(self.report.counts["clean"], 2)
        text = report.table(self.world.m, TAG, [self.report])
        self.assertIn("v26.08 → v26.11", text)
        self.assertIn("pxr-foo", text)
        self.assertIn("verified", text)
        self.assertIn("3 (2/1/0/0)", text)
        waiting = report.RepoReport("pxr-foo", 1, self.report.status, [], [], 0, checks="fail", waiting=["pxr-arch"])
        self.assertIn("waits pxr-arch", report.table(self.world.m, TAG, [waiting]))

    def test_detail(self):
        text = report.detail(self.report, self.world.m, TAG)
        self.assertIn("Release 26.11", text)
        self.assertIn("auto", text)
        self.assertIn("src/pxr/foo/bar.cpp", text)
        self.assertIn("transform(old upstream)", text)
        self.assertIn("baz.h", text)

    def test_pr_body(self):
        body = report.pr_body(self.report, self.world.m, TAG)
        self.assertIn("## Commit mapping", body)
        self.assertIn("## Range diff of Untwine commits", body)
        self.assertIn("## Version changes", body)
        self.assertIn("- [ ] new upstream file baz.h", body)
        self.assertIn("0.26.11", body)

    def test_cli_status(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--manifest", str(self.world.manifest_path), "status", "--no-github"]), 0)
            self.assertEqual(cli.main(["--manifest", str(self.world.manifest_path), "status", "pxr-foo", "--no-github"]), 0)
        self.assertIn("3 (2/1/0/0)", out.getvalue())
        self.assertIn("Untwine commits:", out.getvalue())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_report -v`
Expected: FAIL with `ImportError: cannot import name 'report'`

- [ ] **Step 3: Write `untwine_cli/report.py`**

```python
"""Views of a release: cross-repo table, per-repo detail, PR body."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil, manifest, state
from .manifest import Manifest

PR_BODY_LIMIT = 60000
CI_MARKS = {"pass": "✓", "fail": "✗", "pending": "…", "none": "–", None: "–"}


@dataclass(frozen=True)
class Row:
    source: str
    subject: str
    new: str | None
    resolution: str
    files: tuple[str, ...]


@dataclass
class RepoReport:
    name: str
    level: int
    status: state.LocalStatus
    upstream_commits: list[str]
    rows: list[Row]
    changed_files: int | None
    pr: dict | None = None
    checks: str | None = None
    waiting: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {"clean": 0, "auto": 0, "manual": 0, "empty": 0, "pending": 0}
        for row in self.rows:
            counts[row.resolution] = counts.get(row.resolution, 0) + 1
        return counts

    @property
    def display_state(self) -> str:
        pr = self.pr
        if self.status.state == "verified" and pr and pr["state"] == "OPEN" and pr["headRefOid"] == self.status.tip:
            return "pr-open"
        return self.status.state


def collect(m: Manifest, tag: str, name: str, *, use_github: bool = False) -> RepoReport:
    clone = m.repo_path(name)
    status = state.local_status(m, tag, name)
    commits: list[str] = []
    rows: list[Row] = []
    changed = None
    if status.sources or gitutil.ref_exists(clone, state.ref(tag, "upstream")):
        old_usd, up = state.ref(tag, "old-open-usd"), state.ref(tag, "upstream")
        if gitutil.ref_exists(clone, up):
            commits = gitutil.git(clone, "log", "--format=%h %s", f"{old_usd}..{up}").splitlines()
        for source in status.sources:
            new, note = status.replayed.get(source, (None, None))
            rows.append(Row(source, gitutil.git(clone, "log", "-1", "--format=%s", source), new,
                            note.resolution if note else "pending", note.files if note else ()))
    if status.tip:
        changed = len(gitutil.git(clone, "diff", "--name-only", state.ref(tag, "old-main"), status.tip).splitlines())
    result = RepoReport(name, manifest.level_map(m)[name], status, commits, rows, changed)
    for dep in (f"pxr-{d}" for d in (*m.repos[name].deps, *m.repos[name].test_deps)):
        dep_clone = m.repo_path(dep)
        if (dep_clone.is_dir() and gitutil.ref_exists(dep_clone, state.ref(tag, "old-main"))
                and not gitutil.ref_exists(dep_clone, state.ref(tag, "promoted"))):
            result.waiting.append(dep)
    if use_github and status.tip:
        from . import github
        result.pr = github.find_pr(github.slug(m, name), state.sync_branch(tag))
        if result.pr and result.pr["state"] == "OPEN":
            result.checks = github.checks(github.slug(m, name), result.pr["number"])
    return result


def table(m: Manifest, tag: str, reports: list[RepoReport]) -> str:
    ready = sum(r.display_state in ("verified", "pr-open", "promoted") for r in reports)
    lines = [f"{m.openusd + ' → ' + tag:<60}{ready} of {len(reports)} ready",
             f"{'lvl':<4} {'repo':<11} {'status':<16} {'upstream':>8}  {'ours (clean/auto/manual/empty)':<31} "
             f"{'files':>5}  {'flag':<12} {'CI':<3} PR"]
    for r in reports:
        c = r.counts
        ours = f"{len(r.rows)} ({c['clean']}/{c['auto']}/{c['manual']}/{c['empty']})"
        flag = "conflict" if r.status.pending else (f"{len(r.status.attention)} issue(s)" if r.status.attention else "")
        if not flag and r.checks in ("fail", "pending") and r.waiting:
            flag = f"waits {r.waiting[0]}"
        files = "–" if r.changed_files is None else str(r.changed_files)
        pr = f"#{r.pr['number']}" if r.pr else "–"
        lines.append(f"{r.level:<4} {r.name:<11} {r.display_state:<16} {'+' + str(len(r.upstream_commits)):>8}  "
                     f"{ours:<31} {files:>5}  {flag:<12} {CI_MARKS[r.checks]:<3} {pr}")
    return "\n".join(lines)


def detail(r: RepoReport, m: Manifest, tag: str) -> str:
    status = r.status
    out = [f"{r.name}: {r.display_state} (level {r.level})", "", f"Upstream commits ({len(r.upstream_commits)}):"]
    out += [f"  {c}" for c in r.upstream_commits[:50]]
    if len(r.upstream_commits) > 50:
        out.append(f"  … and {len(r.upstream_commits) - 50} more")
    out += ["", "Untwine commits:"]
    for row in r.rows:
        files = f"  [{', '.join(row.files)}]" if row.files else ""
        out.append(f"  {row.source[:12]} -> {(row.new or '-')[:12]:<12} {row.resolution:<8} {row.subject}{files}")
    if status.pending:
        out += ["", f"Stopped conflict in {status.pending['source'][:12]}:"]
        out += [f"  needs resolution: {p}" for p in status.pending["manual"]]
        out += [f"  auto-resolved:    {p}" for p in status.pending["auto"]]
        out.append(f"  fix the files in {state.worktree_path(m, tag, r.name)}, `git add` them, "
                   f"then run: untwine resolve {r.name}")
    if status.attention:
        out += ["", "Needs attention:", *[f"  - {a}" for a in status.attention]]
    if status.review:
        out += ["", "For review:", *[f"  - {i}" for i in status.review]]
    if r.changed_files is not None:
        out += ["", f"Files changed relative to the old main: {r.changed_files}"]
    return "\n".join(out)


def range_diff(clone: Path, tag: str) -> str:
    return gitutil.git(clone, "range-diff", "--no-color",
                       f"{state.ref(tag, 'old-open-usd')}..{state.ref(tag, 'old-main')}",
                       f"{state.sync_open_usd(tag)}..{state.sync_branch(tag)}")


def version_diff(clone: Path, tag: str) -> str:
    return gitutil.git(clone, "diff", "--no-color", state.ref(tag, "old-main"), state.sync_branch(tag), "--",
                       "CMakeLists.txt", "cmake", "conanfile.py", "pyproject.toml", "pyproject-dev.toml", "README.md")


def pr_body(r: RepoReport, m: Manifest, tag: str) -> str:
    clone, c = m.repo_path(r.name), r.counts
    parts = [
        f"Sync `{r.name}` from OpenUSD {m.openusd} to {tag}.", "",
        f"**Upstream:** {len(r.upstream_commits)} new commits.",
        f"**Untwine commits:** {len(r.rows)} ({c['clean']} clean, {c['auto']} auto-resolved, "
        f"{c['manual']} manual, {c['empty']} empty).", "",
        "## Commit mapping", "", "| old | new | resolution | subject |", "| --- | --- | --- | --- |",
    ]
    for row in r.rows:
        files = f" ({', '.join(row.files)})" if row.files else ""
        parts.append(f"| `{row.source[:12]}` | `{(row.new or '-')[:12]}` | {row.resolution}{files} | {row.subject} |")
    if r.status.review:
        parts += ["", "## For review", "", *[f"- [ ] {item}" for item in r.status.review]]
    parts += ["", "## Range diff of Untwine commits", "", "```diff", range_diff(clone, tag), "```",
              "", "## Version changes", "", "```diff", version_diff(clone, tag), "```"]
    body = "\n".join(parts)
    if len(body) > PR_BODY_LIMIT:
        body = body[:PR_BODY_LIMIT] + "\n```\n\n(truncated; run `untwine status` locally for the full report)"
    return body
```

- [ ] **Step 4: Update `untwine_cli/cli.py`**

Change the import line to:

```python
from . import UntwineError, manifest, replay, report, state, sync, upstream, verify
```

Replace `_print_states` with:

```python
def _print_states(m: Manifest, tag: str, repos: list[manifest.Repo]) -> None:
    print(report.table(m, tag, [report.collect(m, tag, r.name) for r in repos]))
```

Insert above `def main`:

```python
def _status_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo", nargs="?")
    p.add_argument("--tag")
    p.add_argument("--no-github", action="store_true", help="do not query PRs and CI")


def _status_run(m: Manifest, args: argparse.Namespace) -> int:
    tag = current_tag(m, args.tag)
    use_github = not args.no_github
    if use_github:
        from . import github
        use_github = github.available()
    if args.repo:
        [repo] = manifest.selected(m, [args.repo])
        print(report.detail(report.collect(m, tag, repo.name, use_github=use_github), m, tag))
    else:
        repos = [r for r in manifest.selected(m, []) if m.repo_path(r.name).is_dir()]
        print(report.table(m, tag, [report.collect(m, tag, r.name, use_github=use_github) for r in repos]))
    return 0


COMMANDS.append(("status", "show the release in progress", _status_args, _status_run))
```

Update the assertions in `tests/test_sync.py` that read `"pxr-foo: verified"` to check the table instead: replace `self.assertIn("pxr-foo: verified", out.getvalue())` with `self.assertRegex(out.getvalue(), r"pxr-foo\s+verified")` (two places).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_report tests.test_sync -v`
Expected: 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add untwine_cli/report.py untwine_cli/cli.py tests/test_report.py tests/test_sync.py
git commit -m "Add release reports and untwine status."
```

---

### Task 11: GitHub wrapper and `untwine push-prs`

**Files:**
- Create: `untwine_cli/github.py`, `untwine_cli/release.py`, `tests/fake_gh.py`
- Modify: `untwine_cli/cli.py`
- Test: `tests/test_push_prs.py`

**Interfaces:**
- Consumes: `report.collect/pr_body`, `state.*`, `verify.check_history`, `manifest.selected`.
- Produces: `github.GitHubError`, `github._gh(args, input=None)` (patched in tests), `github.available() -> bool`, `github.slug(m, name) -> str`, `github.find_pr(slug, head) -> dict | None` (keys `number, url, state, headRefOid`), `github.upsert_pr(slug, *, head, base, title, body) -> dict`, `github.checks(slug, number) -> str` (`pass | fail | pending | none`); `release.push_prs(m, tag, names, *, confirm) -> list[str]`; CLI `push-prs [repos] [--tag]`. Test fixture `FakeGh(origins: dict[str, Path])` with `.prs`, `.check_state`.

- [ ] **Step 1: Write `tests/fake_gh.py`**

```python
"""An in-memory stand-in for the gh CLI, backed by bare origin repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.helpers import sh_git


def _done(args, stdout="", code=0):
    return subprocess.CompletedProcess(["gh", *args], code, stdout, "")


class FakeGh:
    def __init__(self, origins: dict[str, Path]):
        self.origins = origins
        self.prs: dict[int, dict] = {}
        self.check_state = "pass"

    def _head(self, repo: str, branch: str) -> str | None:
        out = sh_git(self.origins[repo], "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}")
        return out or None

    def _view(self, pr: dict) -> dict:
        return {"number": pr["number"], "url": pr["url"], "state": pr["state"],
                "headRefOid": self._head(pr["repo"], pr["head"]) or pr["last_oid"]}

    def __call__(self, args: list[str], input: str | None = None):
        value = lambda flag: args[args.index(flag) + 1]  # noqa: E731
        if args[:2] == ["auth", "status"]:
            return _done(args)
        if args[:2] == ["pr", "list"]:
            prs = [self._view(p) for p in self.prs.values() if p["repo"] == value("--repo") and p["head"] == value("--head")]
            return _done(args, json.dumps(prs))
        if args[:2] == ["pr", "create"]:
            number = len(self.prs) + 1
            repo = value("--repo")
            self.prs[number] = {"number": number, "repo": repo, "head": value("--head"), "base": value("--base"),
                                "title": value("--title"), "body": input, "state": "OPEN",
                                "url": f"https://github.com/{repo}/pull/{number}",
                                "last_oid": self._head(repo, value("--head"))}
            return _done(args, self.prs[number]["url"])
        if args[:2] == ["pr", "edit"]:
            pr = self.prs[int(args[2])]
            pr.update(title=value("--title"), body=input)
            return _done(args)
        if args[:2] == ["pr", "checks"]:
            return _done(args, json.dumps([{"bucket": self.check_state}]))
        raise AssertionError(f"unexpected gh call: {args}")
```

- [ ] **Step 2: Write the failing tests**

`tests/test_push_prs.py`:

```python
from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, needs_filter_repo, sh_git
from untwine_cli import UntwineError, gitutil, release, state, sync

TAG = "v26.11"


@needs_filter_repo
class PushPrsTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"upstream-edit"})
        self.gh = FakeGh({"untwine/pxr-foo": self.world.origin})
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))

    def test_push_creates_then_updates_pr(self):
        sync.sync(self.world.m, TAG, [])
        urls = release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.assertEqual(urls, ["pxr-foo: https://github.com/untwine/pxr-foo/pull/1"])
        tip = gitutil.rev(self.world.clone, state.sync_branch(TAG))
        self.assertEqual(sh_git(self.world.origin, "rev-parse", f"refs/heads/{state.sync_branch(TAG)}"), tip)
        self.assertIn("## Commit mapping", self.gh.prs[1]["body"])
        self.assertEqual(self.gh.prs[1]["title"], "Sync pxr-foo to OpenUSD v26.11")
        release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.assertEqual(len(self.gh.prs), 1)

    def test_refuses_unverified(self):
        world = FakeWorld(self.tmp / "other", {"fix-conflict"})
        sync.sync(world.m, TAG, [])
        with self.assertRaisesRegex(UntwineError, "only verified"):
            release.push_prs(world.m, TAG, [], confirm=lambda lines: True)

    def test_nothing_happens_without_confirmation(self):
        sync.sync(self.world.m, TAG, [])
        self.assertEqual(release.push_prs(self.world.m, TAG, [], confirm=lambda lines: False), [])
        self.assertEqual(sh_git(self.world.origin, "branch", "--list", "sync/*"), "")
        self.assertEqual(self.gh.prs, {})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_push_prs -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'untwine_cli.release'`

- [ ] **Step 4: Write `untwine_cli/github.py`**

```python
"""Thin wrapper around the gh command line."""

from __future__ import annotations

import json
import subprocess

from . import UntwineError
from .manifest import Manifest


class GitHubError(UntwineError):
    pass


def _gh(args: list[str], input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], input=input, capture_output=True, text=True)


def _call(args: list[str], input: str | None = None) -> str:
    proc = _gh(args, input)
    if proc.returncode != 0:
        raise GitHubError(f"gh {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def available() -> bool:
    try:
        return _gh(["auth", "status"]).returncode == 0
    except FileNotFoundError:
        return False


def slug(m: Manifest, repo_name: str) -> str:
    return f"{m.github_org}/{repo_name}"


def find_pr(repo_slug: str, head: str) -> dict | None:
    out = _call(["pr", "list", "--repo", repo_slug, "--head", head, "--state", "all",
                 "--json", "number,url,state,headRefOid"])
    prs = json.loads(out or "[]")
    return ([p for p in prs if p["state"] == "OPEN"] or prs or [None])[0]


def upsert_pr(repo_slug: str, *, head: str, base: str, title: str, body: str) -> dict:
    existing = find_pr(repo_slug, head)
    if existing and existing["state"] == "OPEN":
        _call(["pr", "edit", str(existing["number"]), "--repo", repo_slug, "--title", title, "--body-file", "-"], body)
    else:
        _call(["pr", "create", "--repo", repo_slug, "--head", head, "--base", base, "--title", title,
               "--body-file", "-"], body)
    pr = find_pr(repo_slug, head)
    if pr is None:
        raise GitHubError(f"PR for {head} on {repo_slug} not found after creating it")
    return pr


def checks(repo_slug: str, number: int) -> str:
    proc = _gh(["pr", "checks", str(number), "--repo", repo_slug, "--json", "bucket"])
    try:
        buckets = {c["bucket"] for c in json.loads(proc.stdout or "[]")}
    except json.JSONDecodeError:
        return "none"
    if not buckets:
        return "none"
    if buckets & {"fail", "cancel"}:
        return "fail"
    return "pending" if "pending" in buckets else "pass"
```

- [ ] **Step 5: Write `untwine_cli/release.py`**

```python
"""Remote steps of a release: push-prs and promote."""

from __future__ import annotations

from collections.abc import Callable

from . import UntwineError, github, gitutil, manifest, report, state, verify
from .manifest import Manifest

Confirm = Callable[[list[str]], bool]


def _assert_no_ai_attribution(m: Manifest, tag: str, repo: manifest.Repo) -> None:
    clone = m.repo_path(repo.name)
    findings = [f for f in verify.check_history(clone, repo, state.sync_open_usd(tag), state.sync_branch(tag))
                if f.check == "ai-attribution"]
    if findings:
        raise UntwineError(f"{repo.name}: AI attribution in commits: " + "; ".join(f.message for f in findings))


def push_prs(m: Manifest, tag: str, names: list[str], *, confirm: Confirm) -> list[str]:
    repos = [r for r in manifest.selected(m, names) if m.repo_path(r.name).is_dir()
             and state.local_status(m, tag, r.name).state != "pending"]
    reports = [report.collect(m, tag, r.name) for r in repos]
    not_ready = [f"{r.name}: {r.status.state}" for r in reports if r.status.state != "verified"]
    if not_ready:
        raise UntwineError("only verified repositories can be pushed:\n  " + "\n  ".join(not_ready))
    for repo in repos:
        _assert_no_ai_attribution(m, tag, repo)
    lines = [f"push {state.sync_branch(tag)} and {state.sync_open_usd(tag)} to {github.slug(m, r.name)} "
             f"and open or update its PR" for r in repos]
    if not repos or not confirm(lines):
        return []
    urls = []
    for repo, rep in zip(repos, reports):
        clone = m.repo_path(repo.name)
        refspecs = [f"{b}:refs/heads/{b}" for b in (state.sync_branch(tag), state.sync_open_usd(tag))]
        gitutil.run(clone, "push", "--quiet", "--force-with-lease", "origin", *refspecs)
        pr = github.upsert_pr(github.slug(m, repo.name), head=state.sync_branch(tag), base="main",
                              title=f"Sync {repo.name} to OpenUSD {tag}", body=report.pr_body(rep, m, tag))
        urls.append(f"{repo.name}: {pr['url']}")
    return urls
```

- [ ] **Step 6: Add the command to `untwine_cli/cli.py`**

Insert above `def main`:

```python
def _push_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--tag")


def _push_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import release
    for line in release.push_prs(m, current_tag(m, args.tag), args.repos, confirm=confirm):
        print(line)
    return 0


COMMANDS.append(("push-prs", "push sync branches and open or update PRs", _push_args, _push_run))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_push_prs -v`
Expected: 3 tests PASS

- [ ] **Step 8: Commit**

```bash
git add untwine_cli/github.py untwine_cli/release.py untwine_cli/cli.py tests/fake_gh.py tests/test_push_prs.py
git commit -m "Add untwine push-prs."
```

---

### Task 12: `untwine promote`

**Files:**
- Modify: `untwine_cli/release.py`, `untwine_cli/cli.py`
- Test: `tests/test_promote.py`

**Interfaces:**
- Consumes: `github.*`, `report.collect/detail`, `state.*`, `manifest.levels`.
- Produces: `release.promote_problem(m, tag, repo) -> str | None`; `release.promote(m, tag, names, *, confirm, out=print) -> list[str]`; `release.write_release(m, tag) -> Path`; CLI `promote [repos] [--tag]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_promote.py`:

```python
import io
from unittest import mock

from tests.fake_gh import FakeGh
from tests.fake_world import FakeWorld
from tests.helpers import TempTest, commit, needs_filter_repo, sh_git
from untwine_cli import gitutil, manifest, release, state, sync

TAG = "v26.11"


@needs_filter_repo
class PromoteTest(TempTest):
    def setUp(self):
        super().setUp()
        self.world = FakeWorld(self.tmp, {"upstream-edit"})
        self.gh = FakeGh({"untwine/pxr-foo": self.world.origin})
        self.enterContext(mock.patch("untwine_cli.github._gh", self.gh))
        sync.sync(self.world.m, TAG, [])
        release.push_prs(self.world.m, TAG, [], confirm=lambda lines: True)
        self.tip = gitutil.rev(self.world.clone, state.sync_branch(TAG))
        self.usd = gitutil.rev(self.world.clone, state.sync_open_usd(TAG))

    def promote(self):
        out = io.StringIO()
        done = release.promote(self.world.m, TAG, [], confirm=lambda lines: True, out=lambda s: out.write(s + "\n"))
        return done, out.getvalue()

    def test_promote(self):
        done, _ = self.promote()
        self.assertEqual(done, ["pxr-foo"])
        origin = self.world.origin
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/main"), self.tip)
        self.assertEqual(sh_git(origin, "rev-parse", "refs/heads/open-usd"), self.usd)
        self.assertEqual(sh_git(origin, "branch", "--list", "sync/*"), "")
        self.assertEqual(gitutil.rev(self.world.clone, "main"), self.tip)
        self.assertEqual(gitutil.rev(self.world.clone, "open-usd"), self.usd)
        self.assertEqual(state.local_status(self.world.m, TAG, "pxr-foo").state, "promoted")
        self.assertFalse(state.worktree_path(self.world.m, TAG, "pxr-foo").exists())
        self.assertTrue((self.world.manifest_path.parent / "releases" / "v26.11.md").is_file())
        self.assertEqual(manifest.load(self.world.manifest_path).openusd, "v26.11")

    def test_refuses_failing_checks(self):
        self.gh.check_state = "fail"
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("CI checks are fail", out)

    def test_refuses_when_origin_moved(self):
        other = self.tmp / "other"
        sh_git(self.tmp, "clone", "-q", str(self.world.origin), str(other))
        commit(other, {"x": "1\n"}, "Someone else pushed.")
        sh_git(other, "push", "-q", "origin", "main")
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("origin/main moved", out)
        self.assertNotEqual(sh_git(self.world.origin, "rev-parse", "refs/heads/main"), self.tip)

    def test_refuses_stale_pr(self):
        wt = state.worktree_path(self.world.m, TAG, "pxr-foo")
        target = gitutil.git(wt, "log", "--format=%H", "--grep=^Add minimal", f"{state.sync_open_usd(TAG)}..HEAD")
        (wt / "README.md").write_text("edited\n")
        sh_git(wt, "commit", "-q", "-a", f"--fixup={target}")
        sync.sync(self.world.m, TAG, [])
        done, out = self.promote()
        self.assertEqual(done, [])
        self.assertIn("run push-prs", out)

    def test_discard_refused_after_promote(self):
        self.promote()
        with self.assertRaisesRegex(Exception, "already promoted"):
            sync.discard(self.world.m, TAG, [], confirm=lambda lines: True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_promote -v`
Expected: FAIL with `AttributeError: module 'untwine_cli.release' has no attribute 'promote'`

- [ ] **Step 3: Add promote to `untwine_cli/release.py`**

Add `import re` and `from pathlib import Path` to the imports, then append:

```python
def _in_release(m: Manifest, tag: str, name: str) -> bool:
    clone = m.repo_path(name)
    return clone.is_dir() and gitutil.ref_exists(clone, state.ref(tag, "old-main"))


def promote_problem(m: Manifest, tag: str, repo: manifest.Repo) -> str | None:
    clone = m.repo_path(repo.name)
    status = state.local_status(m, tag, repo.name)
    if status.state == "promoted":
        return "already promoted"
    if status.state != "verified":
        return f"status is {status.state}"
    for dep in (*repo.deps, *repo.test_deps):
        name = f"pxr-{dep}"
        if _in_release(m, tag, name) and not gitutil.ref_exists(m.repo_path(name), state.ref(tag, "promoted")):
            return f"dependency {name} is not promoted yet"
    repo_slug = github.slug(m, repo.name)
    pr = github.find_pr(repo_slug, state.sync_branch(tag))
    if not pr or pr["state"] != "OPEN":
        return "no open PR; run push-prs"
    if pr["headRefOid"] != status.tip:
        return "PR head does not match the local sync branch; run push-prs"
    checks = github.checks(repo_slug, pr["number"])
    if checks != "pass":
        return f"CI checks are {checks}"
    gitutil.run(clone, "fetch", "--quiet", "origin")
    for name, branch in (("old-main", "main"), ("old-open-usd", "open-usd")):
        if gitutil.rev(clone, f"refs/remotes/origin/{branch}") != gitutil.rev(clone, state.ref(tag, name)):
            return f"origin/{branch} moved since this release started"
    return None


def _promote_one(m: Manifest, tag: str, repo: manifest.Repo) -> None:
    clone = m.repo_path(repo.name)
    tip, usd = gitutil.rev(clone, state.sync_branch(tag)), gitutil.rev(clone, state.sync_open_usd(tag))
    old_main, old_usd = gitutil.rev(clone, state.ref(tag, "old-main")), gitutil.rev(clone, state.ref(tag, "old-open-usd"))
    snapshot = m.state_dir / "releases" / tag
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / f"{repo.name}.md").write_text(report.detail(report.collect(m, tag, repo.name), m, tag) + "\n")
    gitutil.run(clone, "push", "--quiet", "--atomic",
                f"--force-with-lease=refs/heads/main:{old_main}", f"--force-with-lease=refs/heads/open-usd:{old_usd}",
                "origin", f"{tip}:refs/heads/main", f"{usd}:refs/heads/open-usd")
    gitutil.run(clone, "update-ref", state.ref(tag, "promoted"), tip)
    gitutil.run(clone, "push", "--quiet", "origin", "--delete", state.sync_branch(tag), state.sync_open_usd(tag))
    worktree = state.worktree_path(m, tag, repo.name)
    if worktree.exists():
        gitutil.run(clone, "worktree", "remove", "--force", str(worktree))
    head = gitutil.run(clone, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.decode().strip()
    for branch, sha in (("main", tip), ("open-usd", usd)):
        if head == branch:
            gitutil.run(clone, "reset", "--quiet", "--keep", sha)
        else:
            gitutil.run(clone, "update-ref", f"refs/heads/{branch}", sha)
    gitutil.run(clone, "branch", "-D", state.sync_branch(tag), state.sync_open_usd(tag))
    gitutil.run(clone, "fetch", "--quiet", "--prune", "origin")


def write_release(m: Manifest, tag: str) -> Path:
    snapshot = m.state_dir / "releases" / tag
    order = [n for level in manifest.levels(m) for n in level]
    sections = [(snapshot / f"{n}.md").read_text() for n in order if (snapshot / f"{n}.md").is_file()]
    target = m.path.parent / "releases" / f"{tag}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# OpenUSD {tag}\n\nSynced from {m.openusd}.\n\n" + "\n".join(f"```text\n{s}```\n" for s in sections))
    text = m.path.read_text()
    m.path.write_text(re.sub(r'(?m)^openusd = "v\d\d\.\d\d"$', f'openusd = "{tag}"', text, count=1))
    return target


def promote(m: Manifest, tag: str, names: list[str], *, confirm: Confirm, out: Callable[[str], None] = print) -> list[str]:
    done: list[str] = []
    for level in manifest.levels(m):
        batch = [m.repos[n] for n in level if m.repos[n].kind != "support" and (not names or n in names)
                 and _in_release(m, tag, n)]
        ready = []
        for repo in batch:
            problem = promote_problem(m, tag, repo)
            if problem:
                if problem != "already promoted":
                    out(f"skip {repo.name}: {problem}")
            else:
                ready.append(repo)
        if not ready:
            continue
        lines = [f"{github.slug(m, r.name)}: main -> {gitutil.rev(m.repo_path(r.name), state.sync_branch(tag))[:12]}, "
                 f"open-usd -> {gitutil.rev(m.repo_path(r.name), state.sync_open_usd(tag))[:12]} "
                 "(force-with-lease on the recorded tips)" for r in ready]
        if not confirm(lines):
            return done
        for repo in ready:
            _promote_one(m, tag, repo)
            done.append(repo.name)
    members = [r.name for r in m.libraries() if _in_release(m, tag, r.name)]
    if members and all(gitutil.ref_exists(m.repo_path(n), state.ref(tag, "promoted")) for n in members):
        out(f"release complete: wrote {write_release(m, tag)} and recorded {tag} in untwine.toml; commit both")
        work = m.state_dir / "work" / tag
        if work.is_dir() and not any(work.iterdir()):
            work.rmdir()
    return done
```

- [ ] **Step 4: Add the command to `untwine_cli/cli.py`**

Insert above `def main`:

```python
def _promote_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--tag")


def _promote_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import release
    done = release.promote(m, current_tag(m, args.tag), args.repos, confirm=confirm)
    print(f"promoted: {', '.join(done) or 'nothing'}")
    return 0


COMMANDS.append(("promote", "move main and open-usd to the reviewed sync branches", _promote_args, _promote_run))
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add untwine_cli/release.py untwine_cli/cli.py tests/test_promote.py
git commit -m "Add untwine promote."
```

---

### Task 13: Documentation

**Files:**
- Create: `AGENTS.md`, `README.md`, `docs/sync-upstream.md`, `docs/split-library.md`, `docs/history.md`, `docs/namespaces.md`, `docs/packaging.md`, `docs/python-bindings.md`, `docs/source-transformations.md`, `docs/validation.md`, `docs/windows-debugging.md`

The reference documents exist on the local branch `archive/pre-cli-worktree` (and as untracked files in the working tree). Restore them from that branch, apply the listed edits, and write the four workflow documents fresh.

- [ ] **Step 1: Restore the reference documents**

```bash
for f in history namespaces packaging python-bindings source-transformations validation windows-debugging; do
  git show archive/pre-cli-worktree:docs/$f.md > docs/$f.md
done
```

- [ ] **Step 2: Apply the edits**

```bash
python3 - <<'EOF'
from pathlib import Path

def edit(path, old, new):
    p = Path(path)
    text = p.read_text()
    assert old in text, (path, old)
    p.write_text(text.replace(old, new, 1))

packaging = Path("docs/packaging.md").read_text()
start = packaging.index("### `testWrapper.py` must `os.chdir()`")
end = packaging.index("### Windows `PATH` for tests must include `onetbb`")
Path("docs/packaging.md").write_text(packaging[:start] + packaging[end:])

edit("docs/validation.md",
     "Run `toolbox/verify-standalone.sh` first: it automates most of this list and\n`--diff` automates the comparison against `open-usd`. The list below remains\nthe source of truth for what it checks.",
     "Run `untwine verify` first: it automates most of this list, and `--diff`\nautomates the comparison against `open-usd`. The list below remains the\nsource of truth for what it checks.")
edit("docs/validation.md",
     "* `testWrapper.py` calls `os.chdir()`; Windows test `PATH` includes `onetbb`",
     "* Windows test `PATH` includes `onetbb`")
edit("docs/history.md",
     "entries, the `testWrapper.py` `os.chdir()`, the Windows `onetbb` `PATH`, `EXACT`,",
     "entries, the Windows `onetbb` `PATH`, `EXACT`,")
EOF
grep -rn "verify-standalone\|sync-upstream.sh\|os.chdir" docs/*.md && echo "FIX THE HITS ABOVE" || echo "clean"
```

Expected: `clean`

- [ ] **Step 3: Write `AGENTS.md`**

```markdown
# AGENTS.md: maintaining Untwine `pxr-*` repositories

This repository holds the `untwine` CLI and the guidance for the standalone
libraries extracted from Pixar's OpenUSD repository. Use the CLI for every
sync and conformance check; read the documents in `docs/` only when a step
hands judgment back to you.

## Invariants

* `main` = filtered `open-usd` history, then
  `Restructure the '<lib>' library as a standalone package.`, then
  `Add minimal release configuration.`, then one focused commit per fix.
* Standalone source = upstream source + mechanical transformations +
  separately justified fixes. No cosmetic drift.
* Direct dependencies only, and they agree across CMake, the installed
  config, Conan, pyproject, `pxr.h.in`, and `untwine.toml`.
* Never add AI attribution, `Co-Authored-By`, or session trailers.
* Never push without explicit maintainer confirmation; rewritten branches go
  out with `--force-with-lease` only.

## Tasks

* Sync to a new OpenUSD release: [`docs/sync-upstream.md`](docs/sync-upstream.md)
* Add a library: [`docs/split-library.md`](docs/split-library.md)
* Check a repository: `./untwine verify [repos] [--diff]`

## Reference

* source transformations: [`docs/source-transformations.md`](docs/source-transformations.md)
* namespaces and bridges: [`docs/namespaces.md`](docs/namespaces.md)
* CMake, Conan, Cloudsmith, CI: [`docs/packaging.md`](docs/packaging.md)
* Python bindings and wheels: [`docs/python-bindings.md`](docs/python-bindings.md)
* validation and the conformance checklist: [`docs/validation.md`](docs/validation.md)
* history rewriting and commit messages: [`docs/history.md`](docs/history.md)
* Windows loader hangs: [`docs/windows-debugging.md`](docs/windows-debugging.md)
* CLI design: [`docs/specs/2026-10-01-untwine-cli-design.md`](docs/specs/2026-10-01-untwine-cli-design.md)
```

- [ ] **Step 4: Write `README.md`**

````markdown
# Untwine toolbox

Maintainer tooling for [Untwine](https://github.com/untwine), which makes
OpenUSD's lower level libraries available as independent packages: one
`pxr-*` repository per library, each preserving its filtered upstream history
(`open-usd`) and adding the minimum needed to build, test, and distribute it
(`main`). C++ packages are published to Untwine's Cloudsmith Conan remote
(hosted under Cloudsmith's open-source program); libraries with Python
bindings also ship runtime and `-dev` wheels on PyPI.

## Requirements

Python 3.11+, `git`, `git-filter-repo`, and an authenticated `gh`. No Python
packages are needed. Clones of every `pxr-*` repository live next to this
one (see `workspace` in `untwine.toml`).

## Usage

```
./untwine verify [repos] [--diff]       conformance checks, read-only
./untwine sync v26.11 [repos]           prepare a release in isolated worktrees
./untwine sync v26.11 --dry-run         same, then discard everything
./untwine status [repo]                 cross-repo table, or one repo in detail
./untwine resolve <repo>                continue after fixing a stopped conflict
./untwine push-prs [repos]              push sync branches and open PRs (asks first)
./untwine promote [repos]               move main/open-usd to the reviewed PR heads (asks first)
./untwine discard v26.11 [repos]        throw a release attempt away
```

Run tests with `python3 -m unittest discover -s tests -t . -v`.
````

- [ ] **Step 5: Write `docs/sync-upstream.md`**

````markdown
# Sync to a newer OpenUSD release

1. `./untwine sync v26.11`. Preflight refuses dirty clones or clones whose
   `main`/`open-usd` differ from `origin`. Every repository is synced in
   `.untwine/work/v26.11/<repo>`; your clones are not touched.
2. `./untwine status` and `./untwine status <repo>` for each repository that
   is not `verified`:
   * **Stopped conflict.** Fix the listed files in the worktree using
     [`source-transformations.md`](source-transformations.md): rebuild the
     file from the new upstream content plus the standard transformations,
     then reapply the intentional fix. `git add` them, then
     `./untwine resolve <repo>`.
   * **Dependency change.** Update `untwine.toml` and the repository's CMake,
     installed config, Conan, `pxr.h.in`, and pyproject together (see
     [`namespaces.md`](namespaces.md)), as `git commit --fixup=<owning commit>`
     in the worktree, then rerun `./untwine sync v26.11 <repo>`.
   * **Verify failure.** Fix it in the worktree with
     `git commit --fixup=<owning commit>` and rerun sync.
   Edit the sync branch only with fixup commits; finalize folds them and
   refuses any other change to the commit structure.
3. Review the "For review" items: register new upstream files in CMake
   (fixup of `Add minimal release configuration.`), confirm empty commits.
4. `./untwine push-prs`. Review each PR: the commit mapping, the range diff
   of Untwine commits, and the version changes. CI runs on the exact commits
   that `main` will become.
5. CI for a repository with `pxr-*` dependencies can only pass once those
   dependencies are published at the new version. Promote level by level:
   `./untwine promote`, publish the promoted level yourself (`conan.yml`,
   then `pypi.yml` once you are sure: PyPI never lets a version be replaced),
   re-run CI on the next level's PRs, promote again.
6. When the last repository is promoted, commit `releases/v26.11.md` and the
   updated `untwine.toml`.
````

- [ ] **Step 6: Write `docs/split-library.md`**

````markdown
# Add a library

`untwine add` is not built yet (phase 2 of the CLI design). Until then:

1. Add the repository to `untwine.toml` with its upstream path, Python shape,
   and deps. Derive deps from upstream's `pxr_library(LIBRARIES ...)` (plus
   `boost` when the library has Python bindings) and record any extra direct
   dependency in `extra_deps` with the reason.
2. Create `open-usd` by filtering the subtree at the current release in a
   throwaway clone (`git filter-repo --path <subtree> --path-rename
   <subtree>/:`), never inside an existing repository.
3. Create `Restructure the '<lib>' library as a standalone package.` with the
   transformations in [`source-transformations.md`](source-transformations.md)
   and the commit message style in [`history.md`](history.md).
4. Create `Add minimal release configuration.` by adapting the nearest
   sibling with the same dependency and Python shape, following
   [`packaging.md`](packaging.md), [`namespaces.md`](namespaces.md), and
   [`python-bindings.md`](python-bindings.md).
5. Put each genuine fix in its own commit after the two core commits.
6. `./untwine verify <repo> --diff` and [`validation.md`](validation.md).
````

- [ ] **Step 7: Check links and commit**

```bash
for f in README.md AGENTS.md docs/*.md; do d=$(dirname $f); grep -oE '\]\(([^)#]+)' $f | sed 's/^](//' | grep -vE '^https?:' | while read l; do [ -e "$d/$l" ] || echo "BROKEN $f -> $l"; done; done
git add AGENTS.md README.md docs
git commit -m "Document the untwine CLI workflows and restore the reference guidance."
```

Expected: no `BROKEN` lines.

---

### Task 14: Safety gates on the real repositories

**Files:**
- Modify: `untwine.toml` only if a gate shows a wrong `upstream` path

These steps run against the real `~/dev/untwine/pxr-*` clones. Nothing in them pushes.

- [ ] **Step 1: Verify passes on every current repository**

Run: `./untwine verify`
Expected: every repository prints `ok`; exit code 0.

- [ ] **Step 2: Verify flags the issues fixed on 2026-10-01 at their old tips**

```bash
python3 - <<'EOF'
import subprocess, tempfile
from pathlib import Path
from untwine_cli import manifest, verify
m = manifest.load(Path("untwine.toml"))
expected = {"pxr-arch": ("6e9816c", {"quoted-include"}), "pxr-boost": ("3464df7", {"quoted-include"}),
            "pxr-gf": ("8b4c6be", {"root-source", "old-include-path", "cxx-standard"}),
            "pxr-kind": ("d40f0a7", {"python-forward"}), "pxr-trace": ("52fb6b1", {"cxx-standard"})}
for name, (sha, checks) in expected.items():
    clone = m.repo_path(name)
    wt = Path(tempfile.mkdtemp()) / name
    subprocess.run(["git", "-C", str(clone), "worktree", "add", "-q", "--detach", str(wt), sha], check=True)
    try:
        found = {f.check for f in verify.run_checks(wt, m, m.repos[name])}
        print(name, "OK" if checks <= found else f"MISSING {checks - found}", sorted(found))
    finally:
        subprocess.run(["git", "-C", str(clone), "worktree", "remove", "--force", str(wt)], check=True)
EOF
```

Expected: five `OK` lines. The old tips also report `gitignore` (they predate anchoring); that is expected.

- [ ] **Step 3: Review the diff report**

Run: `./untwine verify --diff`
Expected: no `whitespace-only` entries; every listed file corresponds to a focused fix commit or a documented `Restructure` change. Report anything else to the maintainer.

- [ ] **Step 4: Rehearse a sync on the current release**

Run: `./untwine sync v26.08 --dry-run`
Expected: every repository is `verified`, every commit is clean (`n (n/0/0/0)`), the `files` column is `0`, and the run ends with `dry run: discarded`. If a repository reports "open-usd is not an ancestor", its `upstream` path in `untwine.toml` differs from the path that built `open-usd`: find the right one (`git log --format=%B -1 open-usd` and the OpenUSD tree), fix the manifest, commit, and rerun.

- [ ] **Step 5: Hand off to the maintainer**

Report the outcome of steps 1-4. Then ask before either of these remote actions:

1. Pushing the rewritten toolbox history: `git push --force-with-lease=main:30a81f4 origin main` (the old history is preserved by the `archive/pre-cli` tag on GitHub).
2. The first real release, once OpenUSD tags `v26.11`: `./untwine sync v26.11 pxr-arch`, then `status`, `push-prs pxr-arch`, review, `promote pxr-arch`, before syncing the remaining repositories.
