"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, manifest, replay, report, state, sync, upstream, verify
from .manifest import Manifest

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "untwine.toml"

Command = tuple[str, str, Callable[[argparse.ArgumentParser], None], Callable[[Manifest, argparse.Namespace], int]]
COMMANDS: list[Command] = []


def confirm(lines: list[str]) -> bool:
    print("\n".join(lines))
    if not sys.stdin.isatty():
        print("refusing: confirmation needs an interactive terminal")
        return False
    return input("Type 'yes' to continue: ").strip() == "yes"


def current_tag(m: Manifest, given: str | None) -> str:
    if given:
        return given
    work = m.state_dir / "work"
    tags = sorted(p.name for p in work.iterdir() if p.is_dir()) if work.is_dir() else []
    if len(tags) != 1:
        raise UntwineError(f"cannot infer the release in progress (found {tags or 'none'}); pass --tag")
    return tags[0]


def _verify_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--diff", action="store_true", help="also list files that differ from transform(open-usd)")


def _verify_run(m: Manifest, args: argparse.Namespace) -> int:
    failed = False
    for repo in manifest.selected(m, args.repos):
        path = m.repo_path(repo.name)
        findings = verify.run_checks(path, m, repo)
        print(f"{repo.name}: {'ok' if not findings else f'{len(findings)} problem(s)'}")
        for finding in findings:
            print(f"  {finding.check}: {finding.message}")
        failed |= bool(findings)
        if args.diff and repo.kind == "library":
            for rel, count, ws in verify.diff_report(path, repo):
                print(f"  differs from open-usd: {rel} ({count} lines){' whitespace-only' if ws else ''}")
    return 1 if failed else 0


COMMANDS.append(("verify", "check repositories against the Untwine conventions", _verify_args, _verify_run))


def _print_states(m: Manifest, tag: str, repos: list[manifest.Repo]) -> None:
    print(report.table(m, tag, [report.collect(m, tag, r.name) for r in repos]))


def _sync_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tag")
    p.add_argument("repos", nargs="*")
    p.add_argument("--dry-run", action="store_true", help="run everything, report, then discard")


def _sync_run(m: Manifest, args: argparse.Namespace) -> int:
    if args.dry_run:
        busy = sync.in_progress(m, args.tag, manifest.selected(m, args.repos))
        if busy:
            raise UntwineError(f"{args.tag} is already in progress for {', '.join(busy)}; a dry run would discard it")
    repos = sync.sync(m, args.tag, args.repos)
    _print_states(m, args.tag, repos)
    if args.dry_run:
        sync.discard(m, args.tag, [r.name for r in repos], confirm=lambda lines: True, remote=False)
        print("dry run: discarded")
    return 0


def _resolve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo")
    p.add_argument("--tag")


def _resolve_run(m: Manifest, args: argparse.Namespace) -> int:
    tag = current_tag(m, args.tag)
    [repo] = manifest.selected(m, [args.repo])
    replay.resolve(m, repo, tag)
    sync.sync_repo(m, repo, tag, upstream.mirror_path(m))
    _print_states(m, tag, [repo])
    return 0


def _discard_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tag")
    p.add_argument("repos", nargs="*")


def _discard_run(m: Manifest, args: argparse.Namespace) -> int:
    sync.discard(m, args.tag, args.repos, confirm=confirm)
    print(f"discarded {args.tag}")
    return 0


COMMANDS.append(("sync", "sync repositories to an OpenUSD release", _sync_args, _sync_run))
COMMANDS.append(("resolve", "continue a sync after fixing a conflict", _resolve_args, _resolve_run))
COMMANDS.append(("discard", "remove all local (and pushed) state of a release", _discard_args, _discard_run))


def _status_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo", nargs="?")
    p.add_argument("--tag")
    p.add_argument("--no-github", action="store_true", help="do not query PRs and CI")


def _status_run(m: Manifest, args: argparse.Namespace) -> int:
    tag = current_tag(m, args.tag)
    use_github = not args.no_github
    if use_github:
        from . import github
        use_github = github.available()
    if args.repo:
        [repo] = manifest.selected(m, [args.repo])
        print(report.detail(report.collect(m, tag, repo.name, use_github=use_github), m, tag))
    else:
        repos = [r for r in manifest.selected(m, []) if m.repo_path(r.name).is_dir()]
        print(report.table(m, tag, [report.collect(m, tag, r.name, use_github=use_github) for r in repos]))
    return 0


COMMANDS.append(("status", "show the release in progress", _status_args, _status_run))


def _push_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--tag")


def _push_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import release
    for line in release.push_prs(m, current_tag(m, args.tag), args.repos, confirm=confirm):
        print(line)
    return 0


COMMANDS.append(("push-prs", "push sync branches and open or update PRs", _push_args, _push_run))


def _promote_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repos", nargs="*")
    p.add_argument("--tag")


def _promote_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import release
    done = release.promote(m, current_tag(m, args.tag), args.repos, confirm=confirm)
    print(f"promoted: {', '.join(done) or 'nothing'}")
    return 0


COMMANDS.append(("promote", "move main and open-usd to the reviewed sync branches", _promote_args, _promote_run))


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("lib", help="library name, for example pcp")
    p.add_argument("--upstream", help="subtree path in OpenUSD, when it cannot be located automatically")
    p.add_argument("--python", choices=("optional", "required"), help="override the Python packaging shape")


def _add_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import add
    path, sibling = add.add(m, args.lib, upstream_path=args.upstream, python=args.python)
    repo = manifest.load(m.path).repos[f"pxr-{args.lib}"]
    print(f"created {path} (release configuration copied from {sibling.name})")
    print(f"  upstream {repo.upstream}, python {repo.python}, deps {', '.join(repo.deps) or 'none'}")
    for finding in verify.run_checks(path, manifest.load(m.path), repo):
        print(f"  {finding.check}: {finding.message}")
    print("review it (docs/split-library.md), resolve every TODO(untwine) marker, build it, "
          f"then run: untwine publish-repo pxr-{args.lib}")
    return 0


COMMANDS.append(("add", "create a new pxr-<lib> repository from OpenUSD", _add_args, _add_run))


def _publish_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo", help="for example pxr-pcp")


def _publish_run(m: Manifest, args: argparse.Namespace) -> int:
    from . import add
    url = add.publish_repo(m, args.repo, confirm=confirm)
    print(f"published {args.repo}: {url}" if url else "nothing published")
    return 0


COMMANDS.append(("publish-repo", "create the GitHub repository of a new library and push it", _publish_args, _publish_run))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="untwine")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    sub = parser.add_subparsers(dest="command", required=True)
    runners = {}
    for name, help_text, add_arguments, run in COMMANDS:
        add_arguments(sub.add_parser(name, help=help_text))
        runners[name] = run
    args = parser.parse_args(argv)
    try:
        return runners[args.command](manifest.load(args.manifest), args)
    except UntwineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
