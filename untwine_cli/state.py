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
