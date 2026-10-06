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
OLD_PATH = re.compile(r'^[ \t]*#[ \t]*include[ \t]+[<"]pxr/(base|external|usd|imaging)/[^/>"]+/')
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
    return [Finding("todo", f"{rel}:{no}: {line.strip()}") for rel in tree.files
            for no, line in tree.lines(rel) if "TODO(untwine)" in line]


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
