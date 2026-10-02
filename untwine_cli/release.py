"""Remote steps of a release: push-prs and promote."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, github, gitutil, manifest, report, state, verify
from .manifest import Manifest

Confirm = Callable[[list[str]], bool]


def _assert_no_ai_attribution(m: Manifest, tag: str, repo: manifest.Repo) -> None:
    clone = m.repo_path(repo.name)
    findings = [f for f in verify.check_history(clone, repo, state.sync_open_usd(tag), state.sync_branch(tag))
                if f.check == "ai-attribution"]
    if findings:
        raise UntwineError(f"{repo.name}: AI attribution in commits: " + "; ".join(f.message for f in findings))


def push_prs(m: Manifest, tag: str, names: list[str], *, confirm: Confirm) -> list[str]:
    repos = [r for r in manifest.selected(m, names) if m.repo_path(r.name).is_dir()
             and state.local_status(m, tag, r.name).state != "pending"]
    reports = [report.collect(m, tag, r.name) for r in repos]
    not_ready = [f"{r.name}: {r.status.state}" for r in reports if r.status.state != "verified"]
    if not_ready:
        raise UntwineError("only verified repositories can be pushed:\n  " + "\n  ".join(not_ready))
    for repo in repos:
        _assert_no_ai_attribution(m, tag, repo)
    lines = [f"push {state.sync_branch(tag)} and {state.sync_open_usd(tag)} to {github.slug(m, r.name)} "
             f"and open or update its PR" for r in repos]
    if not repos or not confirm(lines):
        return []
    urls = []
    for repo, rep in zip(repos, reports):
        clone = m.repo_path(repo.name)
        refspecs = [f"{b}:refs/heads/{b}" for b in (state.sync_branch(tag), state.sync_open_usd(tag))]
        gitutil.run(clone, "push", "--quiet", "--force-with-lease", "origin", *refspecs)
        pr = github.upsert_pr(github.slug(m, repo.name), head=state.sync_branch(tag), base="main",
                              title=f"Sync {repo.name} to OpenUSD {tag}", body=report.pr_body(rep, m, tag))
        urls.append(f"{repo.name}: {pr['url']}")
    return urls


def _in_release(m: Manifest, tag: str, name: str) -> bool:
    clone = m.repo_path(name)
    return clone.is_dir() and gitutil.ref_exists(clone, state.ref(tag, "old-main"))


def promote_problem(m: Manifest, tag: str, repo: manifest.Repo) -> str | None:
    clone = m.repo_path(repo.name)
    status = state.local_status(m, tag, repo.name)
    if status.state == "promoted":
        return "already promoted"
    if status.state != "verified":
        return f"status is {status.state}"
    for dep in (*repo.deps, *repo.test_deps):
        name = f"pxr-{dep}"
        if _in_release(m, tag, name) and not gitutil.ref_exists(m.repo_path(name), state.ref(tag, "promoted")):
            return f"dependency {name} is not promoted yet"
    repo_slug = github.slug(m, repo.name)
    pr = github.find_pr(repo_slug, state.sync_branch(tag))
    if not pr or pr["state"] != "OPEN":
        return "no open PR; run push-prs"
    if pr["headRefOid"] != status.tip:
        return "PR head does not match the local sync branch; run push-prs"
    checks = github.checks(repo_slug, pr["number"])
    if checks != "pass":
        return f"CI checks are {checks}"
    gitutil.run(clone, "fetch", "--quiet", "origin")
    for name, branch in (("old-main", "main"), ("old-open-usd", "open-usd")):
        if gitutil.rev(clone, f"refs/remotes/origin/{branch}") != gitutil.rev(clone, state.ref(tag, name)):
            return f"origin/{branch} moved since this release started"
    return None


def _promote_one(m: Manifest, tag: str, repo: manifest.Repo) -> None:
    clone = m.repo_path(repo.name)
    tip, usd = gitutil.rev(clone, state.sync_branch(tag)), gitutil.rev(clone, state.sync_open_usd(tag))
    old_main, old_usd = gitutil.rev(clone, state.ref(tag, "old-main")), gitutil.rev(clone, state.ref(tag, "old-open-usd"))
    snapshot = m.state_dir / "releases" / tag
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / f"{repo.name}.md").write_text(report.detail(report.collect(m, tag, repo.name), m, tag) + "\n")
    gitutil.run(clone, "push", "--quiet", "--atomic",
                f"--force-with-lease=refs/heads/main:{old_main}", f"--force-with-lease=refs/heads/open-usd:{old_usd}",
                "origin", f"{tip}:refs/heads/main", f"{usd}:refs/heads/open-usd")
    gitutil.run(clone, "update-ref", state.ref(tag, "promoted"), tip)
    gitutil.run(clone, "push", "--quiet", "origin", "--delete", state.sync_branch(tag), state.sync_open_usd(tag))
    worktree = state.worktree_path(m, tag, repo.name)
    if worktree.exists():
        gitutil.run(clone, "worktree", "remove", "--force", str(worktree))
    head = gitutil.run(clone, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.decode().strip()
    for branch, sha in (("main", tip), ("open-usd", usd)):
        if head == branch:
            gitutil.run(clone, "reset", "--quiet", "--keep", sha)
        else:
            gitutil.run(clone, "update-ref", f"refs/heads/{branch}", sha)
    gitutil.run(clone, "branch", "-D", state.sync_branch(tag), state.sync_open_usd(tag))
    gitutil.run(clone, "fetch", "--quiet", "--prune", "origin")


def write_release(m: Manifest, tag: str) -> Path:
    snapshot = m.state_dir / "releases" / tag
    order = [n for level in manifest.levels(m) for n in level]
    sections = [(snapshot / f"{n}.md").read_text() for n in order if (snapshot / f"{n}.md").is_file()]
    target = m.path.parent / "releases" / f"{tag}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# OpenUSD {tag}\n\nSynced from {m.openusd}.\n\n" + "\n".join(f"```text\n{s}```\n" for s in sections))
    text = m.path.read_text()
    m.path.write_text(re.sub(r'(?m)^openusd = "v\d\d\.\d\d"$', f'openusd = "{tag}"', text, count=1))
    return target


def promote(m: Manifest, tag: str, names: list[str], *, confirm: Confirm, out: Callable[[str], None] = print) -> list[str]:
    done: list[str] = []
    for level in manifest.levels(m):
        batch = [m.repos[n] for n in level if m.repos[n].kind != "support" and (not names or n in names)
                 and _in_release(m, tag, n)]
        ready = []
        for repo in batch:
            problem = promote_problem(m, tag, repo)
            if problem:
                if problem != "already promoted":
                    out(f"skip {repo.name}: {problem}")
            else:
                ready.append(repo)
        if not ready:
            continue
        lines = [f"{github.slug(m, r.name)}: main -> {gitutil.rev(m.repo_path(r.name), state.sync_branch(tag))[:12]}, "
                 f"open-usd -> {gitutil.rev(m.repo_path(r.name), state.sync_open_usd(tag))[:12]} "
                 "(force-with-lease on the recorded tips)" for r in ready]
        if not confirm(lines):
            return done
        for repo in ready:
            _promote_one(m, tag, repo)
            done.append(repo.name)
    members = [r.name for r in m.libraries() if _in_release(m, tag, r.name)]
    if members and all(gitutil.ref_exists(m.repo_path(n), state.ref(tag, "promoted")) for n in members):
        out(f"release complete: wrote {write_release(m, tag)} and recorded {tag} in untwine.toml; commit both")
        work = m.state_dir / "work" / tag
        if work.is_dir() and not any(work.iterdir()):
            work.rmdir()
    return done
