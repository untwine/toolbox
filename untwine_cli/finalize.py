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
        proc = gitutil.run(worktree, "rebase", "--quiet", "--interactive", "--autosquash", base, check=False)
        if proc.returncode != 0:
            gitutil.run(worktree, "rebase", "--abort", check=False)
            return ["autosquash failed and was aborted: a fixup conflicts with a later commit; "
                    "rework the fixups in the worktree so they fold cleanly, then sync again: "
                    + (proc.stderr or proc.stdout).decode(errors="replace").strip().splitlines()[-1]]
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
