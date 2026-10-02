"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, manifest, replay, state, sync, upstream, verify
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
    for repo in repos:
        status = state.local_status(m, tag, repo.name)
        print(f"{repo.name}: {status.state}")
        for item in status.attention:
            print(f"  ! {item}")


def _sync_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("tag")
    p.add_argument("repos", nargs="*")
    p.add_argument("--dry-run", action="store_true", help="run everything, report, then discard")


def _sync_run(m: Manifest, args: argparse.Namespace) -> int:
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
