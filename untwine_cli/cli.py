"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import UntwineError, manifest, verify
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
