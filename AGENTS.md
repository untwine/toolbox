# AGENTS.md: maintaining Untwine `pxr-*` repositories

This repository holds the `untwine` CLI and the guidance for the standalone
libraries extracted from Pixar's OpenUSD repository. Use the CLI for every
sync and conformance check; read the documents in `docs/` only when a step
hands judgment back to you.

## Invariants

* `main` = filtered `open-usd` history, then
  `Restructure the '<lib>' library as a standalone package.`, then
  `Add minimal release configuration.`, then one focused commit per fix.
* Standalone source = upstream source + mechanical transformations +
  separately justified fixes. No cosmetic drift.
* Direct dependencies only, and they agree across CMake, the installed
  config, Conan, pyproject, `pxr.h.in`, and `untwine.toml`.
* Never add AI attribution, `Co-Authored-By`, or session trailers.
* Never push without explicit maintainer confirmation; rewritten branches go
  out with `--force-with-lease` only.

## Tasks

* Sync to a new OpenUSD release: [`docs/sync-upstream.md`](docs/sync-upstream.md)
* Add a library: `./untwine add <lib>`, then [`docs/split-library.md`](docs/split-library.md)
* Check a repository: `./untwine verify [repos] [--diff]`

## Reference

* source transformations: [`docs/source-transformations.md`](docs/source-transformations.md)
* namespaces and bridges: [`docs/namespaces.md`](docs/namespaces.md)
* CMake, Conan, Cloudsmith, CI: [`docs/packaging.md`](docs/packaging.md)
* Python bindings and wheels: [`docs/python-bindings.md`](docs/python-bindings.md)
* validation and the conformance checklist: [`docs/validation.md`](docs/validation.md)
* history rewriting and commit messages: [`docs/history.md`](docs/history.md)
* Windows loader hangs: [`docs/windows-debugging.md`](docs/windows-debugging.md)
* CLI design: [`docs/specs/2026-10-01-untwine-cli-design.md`](docs/specs/2026-10-01-untwine-cli-design.md)
