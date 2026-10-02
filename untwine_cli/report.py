"""Views of a release: cross-repo table, per-repo detail, PR body."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil, manifest, state
from .manifest import Manifest

PR_BODY_LIMIT = 60000
CI_MARKS = {"pass": "✓", "fail": "✗", "pending": "…", "none": "–", None: "–"}


@dataclass(frozen=True)
class Row:
    source: str
    subject: str
    new: str | None
    resolution: str
    files: tuple[str, ...]


@dataclass
class RepoReport:
    name: str
    level: int
    status: state.LocalStatus
    upstream_commits: list[str]
    rows: list[Row]
    changed_files: int | None
    pr: dict | None = None
    checks: str | None = None
    waiting: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {"clean": 0, "auto": 0, "manual": 0, "empty": 0, "pending": 0}
        for row in self.rows:
            counts[row.resolution] = counts.get(row.resolution, 0) + 1
        return counts

    @property
    def display_state(self) -> str:
        pr = self.pr
        if self.status.state == "verified" and pr and pr["state"] == "OPEN" and pr["headRefOid"] == self.status.tip:
            return "pr-open"
        return self.status.state


def collect(m: Manifest, tag: str, name: str, *, use_github: bool = False) -> RepoReport:
    clone = m.repo_path(name)
    status = state.local_status(m, tag, name)
    commits: list[str] = []
    rows: list[Row] = []
    changed = None
    if status.sources or gitutil.ref_exists(clone, state.ref(tag, "upstream")):
        old_usd, up = state.ref(tag, "old-open-usd"), state.ref(tag, "upstream")
        if gitutil.ref_exists(clone, up):
            commits = gitutil.git(clone, "log", "--format=%h %s", f"{old_usd}..{up}").splitlines()
        for source in status.sources:
            new, note = status.replayed.get(source, (None, None))
            rows.append(Row(source, gitutil.git(clone, "log", "-1", "--format=%s", source), new,
                            note.resolution if note else "pending", note.files if note else ()))
    if status.tip:
        changed = len(gitutil.git(clone, "diff", "--name-only", state.ref(tag, "old-main"), status.tip).splitlines())
    result = RepoReport(name, manifest.level_map(m)[name], status, commits, rows, changed)
    for dep in (f"pxr-{d}" for d in (*m.repos[name].deps, *m.repos[name].test_deps)):
        dep_clone = m.repo_path(dep)
        if (dep_clone.is_dir() and gitutil.ref_exists(dep_clone, state.ref(tag, "old-main"))
                and not gitutil.ref_exists(dep_clone, state.ref(tag, "promoted"))):
            result.waiting.append(dep)
    if use_github and status.tip:
        from . import github
        result.pr = github.find_pr(github.slug(m, name), state.sync_branch(tag))
        if result.pr and result.pr["state"] == "OPEN":
            result.checks = github.checks(github.slug(m, name), result.pr["number"])
    return result


def table(m: Manifest, tag: str, reports: list[RepoReport]) -> str:
    ready = sum(r.display_state in ("verified", "pr-open", "promoted") for r in reports)
    lines = [f"{m.openusd + ' → ' + tag:<60}{ready} of {len(reports)} ready",
             f"{'lvl':<4} {'repo':<11} {'status':<16} {'upstream':>8}  {'ours (clean/auto/manual/empty)':<31} "
             f"{'files':>5}  {'flag':<12} {'CI':<3} PR"]
    for r in reports:
        c = r.counts
        ours = f"{len(r.rows)} ({c['clean']}/{c['auto']}/{c['manual']}/{c['empty']})"
        flag = "conflict" if r.status.pending else (f"{len(r.status.attention)} issue(s)" if r.status.attention else "")
        if not flag and r.checks in ("fail", "pending") and r.waiting:
            flag = f"waits {r.waiting[0]}"
        files = "–" if r.changed_files is None else str(r.changed_files)
        pr = f"#{r.pr['number']}" if r.pr else "–"
        lines.append(f"{r.level:<4} {r.name:<11} {r.display_state:<16} {'+' + str(len(r.upstream_commits)):>8}  "
                     f"{ours:<31} {files:>5}  {flag:<12} {CI_MARKS[r.checks]:<3} {pr}")
    return "\n".join(lines)


def detail(r: RepoReport, m: Manifest, tag: str) -> str:
    status = r.status
    out = [f"{r.name}: {r.display_state} (level {r.level})", "", f"Upstream commits ({len(r.upstream_commits)}):"]
    out += [f"  {c}" for c in r.upstream_commits[:50]]
    if len(r.upstream_commits) > 50:
        out.append(f"  … and {len(r.upstream_commits) - 50} more")
    out += ["", "Untwine commits:"]
    for row in r.rows:
        files = f"  [{', '.join(row.files)}]" if row.files else ""
        out.append(f"  {row.source[:12]} -> {(row.new or '-')[:12]:<12} {row.resolution:<8} {row.subject}{files}")
    if status.pending:
        out += ["", f"Stopped conflict in {status.pending['source'][:12]}:"]
        out += [f"  needs resolution: {p}" for p in status.pending["manual"]]
        out += [f"  auto-resolved:    {p}" for p in status.pending["auto"]]
        out.append(f"  fix the files in {state.worktree_path(m, tag, r.name)}, `git add` them, "
                   f"then run: untwine resolve {r.name}")
    if status.attention:
        out += ["", "Needs attention:", *[f"  - {a}" for a in status.attention]]
    if status.review:
        out += ["", "For review:", *[f"  - {i}" for i in status.review]]
    if r.changed_files is not None:
        out += ["", f"Files changed relative to the old main: {r.changed_files}"]
    return "\n".join(out)


def range_diff(clone: Path, tag: str) -> str:
    return gitutil.git(clone, "range-diff", "--no-color",
                       f"{state.ref(tag, 'old-open-usd')}..{state.ref(tag, 'old-main')}",
                       f"{state.sync_open_usd(tag)}..{state.sync_branch(tag)}")


def version_diff(clone: Path, tag: str) -> str:
    return gitutil.git(clone, "diff", "--no-color", state.ref(tag, "old-main"), state.sync_branch(tag), "--",
                       "CMakeLists.txt", "cmake", "conanfile.py", "pyproject.toml", "pyproject-dev.toml", "README.md")


def pr_body(r: RepoReport, m: Manifest, tag: str) -> str:
    clone, c = m.repo_path(r.name), r.counts
    parts = [
        f"Sync `{r.name}` from OpenUSD {m.openusd} to {tag}.", "",
        f"**Upstream:** {len(r.upstream_commits)} new commits.",
        f"**Untwine commits:** {len(r.rows)} ({c['clean']} clean, {c['auto']} auto-resolved, "
        f"{c['manual']} manual, {c['empty']} empty).", "",
        "## Commit mapping", "", "| old | new | resolution | subject |", "| --- | --- | --- | --- |",
    ]
    for row in r.rows:
        files = f" ({', '.join(row.files)})" if row.files else ""
        parts.append(f"| `{row.source[:12]}` | `{(row.new or '-')[:12]}` | {row.resolution}{files} | {row.subject} |")
    if r.status.review:
        parts += ["", "## For review", "", *[f"- [ ] {item}" for item in r.status.review]]
    parts += ["", "## Range diff of Untwine commits", "", "```diff", range_diff(clone, tag), "```",
              "", "## Version changes", "", "```diff", version_diff(clone, tag), "```"]
    body = "\n".join(parts)
    if len(body) > PR_BODY_LIMIT:
        body = body[:PR_BODY_LIMIT] + "\n```\n\n(truncated; run `untwine status` locally for the full report)"
    return body
