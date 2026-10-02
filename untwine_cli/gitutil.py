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
