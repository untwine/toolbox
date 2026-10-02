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
