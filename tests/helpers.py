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
