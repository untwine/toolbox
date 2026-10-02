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
