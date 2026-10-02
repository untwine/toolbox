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
