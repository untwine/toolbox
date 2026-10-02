"""An in-memory stand-in for the gh CLI, backed by bare origin repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.helpers import sh_git


def _done(args, stdout="", code=0):
    return subprocess.CompletedProcess(["gh", *args], code, stdout, "")


class FakeGh:
    def __init__(self, origins: dict[str, Path]):
        self.origins = origins
        self.prs: dict[int, dict] = {}
        self.check_state = "pass"

    def _head(self, repo: str, branch: str) -> str | None:
        out = sh_git(self.origins[repo], "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}")
        return out or None

    def _view(self, pr: dict) -> dict:
        return {"number": pr["number"], "url": pr["url"], "state": pr["state"],
                "headRefOid": self._head(pr["repo"], pr["head"]) or pr["last_oid"]}

    def __call__(self, args: list[str], input: str | None = None):
        value = lambda flag: args[args.index(flag) + 1]  # noqa: E731
        if args[:2] == ["auth", "status"]:
            return _done(args)
        if args[:2] == ["pr", "list"]:
            prs = [self._view(p) for p in self.prs.values() if p["repo"] == value("--repo") and p["head"] == value("--head")]
            return _done(args, json.dumps(prs))
        if args[:2] == ["pr", "create"]:
            number = len(self.prs) + 1
            repo = value("--repo")
            self.prs[number] = {"number": number, "repo": repo, "head": value("--head"), "base": value("--base"),
                                "title": value("--title"), "body": input, "state": "OPEN",
                                "url": f"https://github.com/{repo}/pull/{number}",
                                "last_oid": self._head(repo, value("--head"))}
            return _done(args, self.prs[number]["url"])
        if args[:2] == ["pr", "edit"]:
            pr = self.prs[int(args[2])]
            pr.update(title=value("--title"), body=input)
            return _done(args)
        if args[:2] == ["pr", "checks"]:
            return _done(args, json.dumps([{"bucket": self.check_state}]))
        raise AssertionError(f"unexpected gh call: {args}")
