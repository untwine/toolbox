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
        worktree = state.worktree_path(m, tag, repo.name)
        stopped = state.read_json(clone, tag, "pending") or state.worktree_busy(worktree)
        if worktree.exists() and not stopped and gitutil.git(worktree, "status", "--porcelain", "--untracked-files=no"):
            problems.append(f"{repo.name}: worktree {worktree} has uncommitted changes; commit them with "
                            "`git commit --fixup=<owning commit>` or revert them")
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
        state.delete_ref(clone, state.ref(tag, "finalized"))  # before review, so an interruption never reads as verified
        state.delete_ref(clone, state.ref(tag, "review"))
        try:
            sync_repo(m, repo, tag, mirror)
        except UntwineError as exc:
            state.write_json(clone, tag, "review", {"attention": [str(exc)], "review": []})
    return repos


def in_progress(m: Manifest, tag: str, repos: list[Repo]) -> list[str]:
    return [r.name for r in repos if m.repo_path(r.name).is_dir()
            and (gitutil.ref_exists(m.repo_path(r.name), state.ref(tag, "old-main"))
                 or state.worktree_path(m, tag, r.name).exists())]


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
