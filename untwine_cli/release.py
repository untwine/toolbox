"""Remote steps of a release: push-prs and promote."""

from __future__ import annotations

from collections.abc import Callable

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
