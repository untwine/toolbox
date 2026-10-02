# Untwine toolbox

Maintainer tooling for [Untwine](https://github.com/untwine), which makes
OpenUSD's lower level libraries available as independent packages: one
`pxr-*` repository per library, each preserving its filtered upstream history
(`open-usd`) and adding the minimum needed to build, test, and distribute it
(`main`). C++ packages are published to Untwine's Cloudsmith Conan remote
(hosted under Cloudsmith's open-source program); libraries with Python
bindings also ship runtime and `-dev` wheels on PyPI.

## Requirements

Python 3.11+, `git`, `git-filter-repo`, and an authenticated `gh`. No Python
packages are needed. Clones of every `pxr-*` repository live next to this
one (see `workspace` in `untwine.toml`).

## Usage

```
./untwine verify [repos] [--diff]       conformance checks, read-only
./untwine sync v26.11 [repos]           prepare a release in isolated worktrees
./untwine sync v26.11 --dry-run         same, then discard everything
./untwine status [repo]                 cross-repo table, or one repo in detail
./untwine resolve <repo>                continue after fixing a stopped conflict
./untwine push-prs [repos]              push sync branches and open PRs (asks first)
./untwine promote [repos]               move main/open-usd to the reviewed PR heads (asks first)
./untwine discard v26.11 [repos]        throw a release attempt away
./untwine add <lib>                     create ../pxr-<lib> from OpenUSD (local only)
./untwine publish-repo pxr-<lib>        create its GitHub repository and push (asks first)
```

Run tests with `python3 -m unittest discover -s tests -t . -v`.
