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
