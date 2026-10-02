# `untwine` CLI: design

Date: 2026-10-01
Status: approved for spec review

## Goal

Make the two maintainer workflows easy and robust as the number of `pxr-*`
repositories grows:

1. **Sync** every repository to a new OpenUSD release (quarterly).
2. **Add** a library that has not been made standalone yet.

For a sync, the maintainer gets a clear view of what changed upstream, how
each Untwine commit was replayed (clean, auto-resolved, manual, empty), and
which conflicts remain.

## Decisions

| Topic | Decision |
| --- | --- |
| Review surface | One GitHub PR per repository and release, plus a cross-repo terminal view |
| Implementation | Python CLI, standard library only, run from the toolbox checkout |
| Publishing | Out of scope. The CLI never dispatches Conan or PyPI workflows |
| Conflicts | Auto-resolve only provably mechanical conflicts; everything else stops that repository |
| Workspace | Isolated git worktrees of the local clones; shared OpenUSD mirror |
| State | Stored in git (refs and notes), never in a separate state file |
| New repositories | Dependencies and Python shape derived from upstream CMake; config copied from the nearest sibling |

## Non-goals

* Dispatching `conan.yml` or `pypi.yml`, or any other publishing step.
* Templates and config drift detection (possible later phase, see Phases).
* Generating `moduleDeps.cpp` or parsing upstream source/test lists for new
  repositories.
* Resolving non-mechanical conflicts by heuristic.
* Supporting `pxr-tbb` (support package; listed in the manifest only for
  ordering and version information).

## Layout

```text
toolbox/
  untwine                 executable shim: runs untwine_cli with python3 (>= 3.11)
  untwine.toml            manifest
  untwine_cli/            package, one module per responsibility (below)
  tests/                  unittest suites and fixtures
  releases/<tag>.md       permanent per-release report, written at the end
  AGENTS.md               always-on invariants for agents; points to the CLI
  docs/                   reference guidance and workflow guides

~/dev/untwine/            workspace root (manifest: workspace = "..")
  pxr-*/                  maintainer clones (never modified by sync before promote)
  .untwine/cache/OpenUSD.git          bare mirror of upstream OpenUSD
  .untwine/work/<tag>/<repo>/         worktree per repository and release
```

External tools checked at preflight: `git`, `git-filter-repo`, `gh`
(authenticated). No Python packages are required.

## Manifest: `untwine.toml`

```toml
workspace = ".."
upstream_url = "git@github.com:PixarAnimationStudios/OpenUSD.git"
github_org = "untwine"

[release]
openusd = "v26.08"        # current coordinated release
tbb = "2023.1.0"

[repos.pxr-arch]
upstream = "pxr/base/arch"
python = "none"           # none | optional | required
deps = []                 # derived from upstream CMake, stored for review

[repos.pxr-tf]
upstream = "pxr/base/tf"
python = "optional"
deps = ["arch", "boost"]

[repos.pxr-boost]
upstream = "pxr/external/boost/python"
kind = "special"          # versioned and ordered, not transformed

[repos.pxr-tbb]
kind = "support"          # ordering and version only; no open-usd branch
```

`manifest` derives dependency levels (level 0 has no `pxr-*` deps) and rejects
cycles, unknown deps, and repositories missing from the workspace.

Overrides are explicit and carry a reason:

```toml
[repos.pxr-plug.override]
extra_deps = { gf = "test-only dependency of the test plugins" }
```

### Version forms

For tag `vYY.MM`: CMake version `0.YY.M`, Conan and PyPI version `YY.M`
(month without leading zero). `v26.08` gives `0.26.8` and `26.8`.

Sibling Python dependencies are always pinned with a wildcard on the next
segment (`pxr-tf==26.8.*`, `pxr-tbb==2023.1.0.*`), never an exact version.
A broken release can then be yanked and replaced by a trailing-segment
republish (`26.8.1`, `2023.1.0.1`) that consumers pick up without any sibling
changing its pins. `versions` writes pins in this form, and `verify` rejects
an exact sibling pin.

The wildcard exists only because PyPI never lets a published version be
replaced. Conan `requires` (`pxr-tf/26.8`) and CMake `find_package(... EXACT)`
stay exact: a fixed Conan package is re-uploaded under the same version as a
new revision.

## Modules

Each module has one job and is tested on its own.

| Module | Responsibility |
| --- | --- |
| `manifest` | Load and validate `untwine.toml`; dependency levels; version forms |
| `upstream` | Maintain the mirror; filter one subtree at one tag in a throwaway clone of the mirror; parse `pxr_library()` declarations |
| `transform` | The single implementation of the standalone substitutions and path mapping |
| `replay` | Rebase `open-usd`, cherry-pick Untwine commits, classify and auto-resolve conflicts |
| `versions` | Rewrite release-derived version fields and prove none of the old ones remain |
| `verify` | Read-only conformance checks and the transform-based diff |
| `report` | Build the status table, per-repo detail, PR body, and release report |
| `github` | Thin `gh` wrapper: push branch, create or update PR, read checks |
| `cli` | Argument parsing and command dispatch; confirmation prompts |

### `upstream`: dependency derivation

Parses `pxr_library(<name> ... LIBRARIES ...)` in the subtree's
`CMakeLists.txt` at a given tag and maps entries:

* an OpenUSD library target (`tf`, `arch`, ...) to `pxr-<target>`
* `TBB::tbb` to `onetbb` (not a `pxr-*` dependency)
* Python and Boost.Python entries to `boost`, gated on Python support
* platform libraries (`${M_LIB}`, `dl`, ...) are ignored by an explicit list

Any other entry is an error. The Python shape is detected from
`PYTHON_CPPFILES` / `PYMODULE_FILES`; whether bindings are `optional` or
`required` is an Untwine choice taken from the manifest.

### `transform`

Pure functions over text and paths:

* path mapping: upstream `X` to `src/pxr/<lib>/X`; Python glue to
  `src/python/`; `testenv/X` to `test/X`; repository-specific extra mappings
  declared in the manifest (for example `pxr-gf` `resources/`)
* includes: `"pxr/pxr.h"` to `<pxr/<lib>/pxr.h>`;
  `"pxr/(base|usd|imaging)/X"` to `<pxr/X>`;
  `"pxr/external/boost/X"` to `<pxr/boost/X>`; remaining `"pxr/X"` to `<pxr/X>`;
  applied to `#include` lines including `/// #include` doc examples
* `PXR_NAMESPACE_{OPEN_SCOPE,CLOSE_SCOPE,USING_DIRECTIVE}` to the
  `<LIB>_` form
* removal of `#!/pxrpythonsubst` and its single following `#` line
* everything else byte-for-byte, including whitespace

`kind = "special"` repositories are never transformed.

## Git-native state

All progress is derived from git, so the state cannot drift from the repos.

Per repository and release:

| Ref | Meaning |
| --- | --- |
| `refs/untwine/<tag>/old-main` | `main` tip recorded at preflight |
| `refs/untwine/<tag>/old-open-usd` | `open-usd` tip recorded at preflight |
| `refs/untwine/<tag>/upstream` | subtree filtered at the new tag |
| `sync/<tag>` (branch) | candidate `main` |
| `sync/<tag>-open-usd` (branch) | candidate `open-usd` |
| `refs/untwine/<tag>/pending` (blob) | stopped conflict: source commit, auto and manual paths |
| `refs/untwine/<tag>/replayed` | sync tip once every source commit is accounted for |
| `refs/untwine/<tag>/review` (blob) | needs-attention and for-review items |
| `refs/untwine/<tag>/finalized` | sync tip after finalize |
| `refs/untwine/<tag>/promoted` | tip pushed to `main` |
| `refs/notes/untwine/<tag>` | per new commit: source commit and resolution |

Notes record `replayed-from <sha>` and `resolution: clean | auto <files> |
manual <files>`, plus notes on skipped source commits (`empty` or `dropped`).
After fixups are folded, notes are re-attached by position from the last
finalized commits; the maintainer edits the sync branch only with
`git commit --fixup`, and any other change to the commit structure is
reported instead of finalized. Notes and `refs/untwine/*` are local and never
pushed.

Derived status per repository:

```text
pending → filtered → replaying ──→ replayed → verified → pr-open → promoted
                         └→ needs-attention
```

`needs-attention` is any of: a cherry-pick stopped on a manual conflict,
unregistered new upstream files, a dependency change, or a verify failure.

## Commands

### `untwine sync <tag> [repos...] [--dry-run]`

Preflight (all repositories before any work):

1. Tools present; `gh` authenticated.
2. Every clone is clean, and local `main`/`open-usd` equal `origin`.
3. Mirror fetched once.
4. Record `old-main` and `old-open-usd` refs (only if absent, so a rerun
   never moves them).

Then per repository, idempotently (rerunning resumes):

1. **Filter.** Write `refs/untwine/<tag>/upstream`.
2. **Rebase `open-usd`.** `sync/<tag>-open-usd` = old `open-usd` rebased onto
   the filtered history (fast-forwards through commits identical to the
   upstream ones).
3. **Replay.** `sync/<tag>` starts at the new `open-usd`; each commit in
   `old-open-usd..old-main` is cherry-picked in order, recording a note.
   Conflicts follow the rules in "Conflict rules". A manual conflict stops
   this repository only.
4. **Audit.** Upstream files outside the mapped layout are moved and
   transformed, folded into `Restructure` as a fixup; their CMake
   registration becomes a needs-review item. Upstream dependencies are
   re-derived and compared with the manifest and the repository's CMake,
   Conan, `pxr.h.in`, and pyproject declarations; a difference is
   needs-attention.
5. **Bump.** `versions` rewrites version fields as a fixup of
   `Add minimal release configuration.`; autosquash folds fixups.
6. **Verify.** Errors make the repository needs-attention.

`--dry-run` runs everything in the worktrees and then discards them.

### `untwine resolve <repo>`

After the maintainer fixes a stopped cherry-pick in the worktree: checks no
conflict markers remain, runs `transform`-based checks on resolved files,
continues the cherry-pick (recording `manual`), and resumes the replay.

### `untwine status [<repo>]`

Without a repository, a table in dependency order:

```text
v26.08 → v26.11                                    12 of 15 ready
lvl  repo        status            upstream  ours (clean/auto/manual/empty)  CI   PR
0    pxr-arch    verified          +14       8 (7/1/0/0)                     ✓    #41
1    pxr-tf      needs-attention   +37       6 (4/1/1/0)  conflict           –    –
```

With a repository: upstream commits pulled in, the old to new commit mapping
with resolutions, conflicted files with their type and the next command,
auto-resolved files with their proof, needs-review items, dependency
differences, and verify findings that are new relative to `old-main`.

### `untwine push-prs [repos...]`

Confirms the list, pushes `sync/<tag>` and `sync/<tag>-open-usd`, and creates
or updates a PR from `sync/<tag>` into `main`. The PR body is regenerated on
every push:

* upstream summary (commit count, files touched)
* commit mapping table with resolutions
* `git range-diff old-open-usd..old-main sync/<tag>-open-usd..sync/<tag>`
* version-bump diff
* needs-review items

### `untwine promote [repos...]`

Per dependency level, after one confirmation listing exact refs:

* refuses a repository whose status is not `pr-open` with green checks,
  whose `origin/main` or `origin/open-usd` moved since preflight, or whose
  `sync/<tag>` no longer matches the PR head
* pushes `git push --force-with-lease=main:<old-main> origin sync/<tag>:main`
  and the same for `open-usd`
* deletes the remote `sync/*` branches; GitHub marks the PR merged because
  `main` now contains its head commit
* moves the local clone's `main` and `open-usd` to the promoted commits

Because the CLI does not publish, a PR whose repository depends on another
`pxr-*` package can only pass CI after that dependency has been published at
the new version. The expected flow is: promote level 0, publish it yourself,
re-run CI on level 1 PRs, promote level 1, and so on. `status` shows which
dependency a failing PR is waiting on when the failure is a missing package.

When every repository is promoted, writes `releases/<tag>.md` from the
final report and updates `release.openusd` in the manifest.

### `untwine discard <tag> [repos...]`

Removes worktrees, local and remote `sync/*` branches (remote deletion
confirmed), `refs/untwine/<tag>/*`, and notes for those commits. Nothing
else was touched before promote.

### `untwine verify [repos...] [--diff]`

Read-only conformance checks; exits non-zero on failure:

* no quoted or old-path `pxr/` includes, no monolithic namespace macros, no
  `#!/pxrpythonsubst`, `NOTICE.txt` paragraph present
* no source files left at the repository root
* `cxx_std_17` on the target, no global `CMAKE_CXX_STANDARD`, everything
  installed to `CMAKE_INSTALL_LIBDIR`, sibling `find_package` with `EXACT`
* Conan: `cmake_find_mode` `none`, `system_package_version`, `builddirs`,
  `python_version` forwarded to every direct dependency exposing it
* `pxr.h.in` bridges exactly the direct dependencies
* `.gitignore` ignores `CMakeUserPresets.json` and anchors build and
  environment directories
* Windows test `PATH` includes `onetbb`
* CI: one remote URL, one version per action, no schedule, no Defender step
* history: one `Restructure ...`, one `Add minimal release configuration.`,
  no unsquashed fixups, no AI trailers
* sibling Python pins use the wildcard form

`--diff` lists every file that differs from `transform(open-usd)`, marking
whitespace-only differences.

### `untwine add <lib> [--upstream PATH] [--python optional|required]`

1. Locate the upstream path (`pxr/*/<lib>`); error if ambiguous.
2. Derive deps; error if a dep has no repository yet, printing the order to
   add the missing ones.
3. Create `pxr-<lib>` with `open-usd` filtered at the current release.
4. Generate `Restructure` with `transform`, a `pxr.h.in` with bridges for the
   derived deps, `NOTICE.txt`, and the standard commit message.
5. Copy the nearest sibling's release configuration (closest by deps and
   Python shape), rewriting names, deps, and pins. Insert
   `TODO(untwine): review` markers, listing upstream's declarations, for
   source/test lists and `moduleDeps.cpp`; `verify` fails while any marker remains.
6. Add the manifest entry. Stop for review. Creating the GitHub repository
   and pushing is a separate confirmed command: `untwine publish-repo pxr-<lib>`.

## Conflict rules

During replay of source commit `C`, for each conflicted path `P`:

* Let `U_old` and `U_new` be the upstream files mapping to `P` at the old and
  new tags.
* **Provable:** if `P` as it exists right after `C` in the old history equals
  `transform(U_old)` exactly, then `C` contributed nothing Untwine-specific
  to `P`. Resolve to `transform(U_new)`, or delete `P` when `U_new` does not
  exist. Record `auto` with this proof.
* A path with no upstream counterpart (build, packaging, CI files) is never
  provable.
* **Otherwise:** stop with the conflict type (content, rename/delete, add
  under a transformed path) and leave it to the maintainer.

An empty cherry-pick is skipped and recorded as `empty`, which is a
needs-review item shown in status and the PR. Dropping a commit is never
automatic.

## Safety invariants

* No push or remote write without an interactive confirmation listing the
  refs. `promote` has no non-interactive flag.
* Every force push uses `--force-with-lease=<ref>:<recorded sha>`.
* Commits are authored by the maintainer's git identity with no trailers; a
  pre-push check refuses `Co-Authored-By` or AI attribution on Untwine
  commits.
* Unknown CMake entries, unmapped files, and ambiguous paths are errors.
* Each step checks its preconditions against actual refs, so manual changes
  in a worktree are detected, not overwritten.

## Testing

* **Unit:** `transform` cases taken from real fixes (quoted includes,
  `pxr/base` paths, the `export.h` doc example, trailing whitespace,
  `pxrpythonsubst`); version forms and rewriting; manifest validation;
  `pxr_library()` parsing.
* **Integration:** a generated fake OpenUSD repository with two tags and a
  fake `pxr-foo` with the two core commits and one fix. Scenarios: clean
  replay; provable auto-resolve; manual conflict stops and `resolve`
  continues; empty cherry-pick; new upstream file; upstream dependency
  change; interrupted run resumes; `origin/main` moved before promote.
* **Regression:** `untwine verify` passes on every current repository's
  `main`, and reports the issues fixed on 2026-10-01 when run on their
  pre-fix commits.
* **Rehearsal:** `untwine sync <current tag> --dry-run` on the real
  repositories must replay with zero changes before the first real use.

Tests use `unittest` so no packages need installing.

## Documentation

* `AGENTS.md`: the invariants (history model, no AI trailers, never push
  without confirmation, `--force-with-lease`) and an instruction to use the
  CLI.
* `docs/`: reference guidance (source transformations, namespaces,
  packaging, Python bindings, history, Windows debugging), plus one guide
  per workflow covering the judgment steps the CLI hands back (manual
  conflicts, new file registration, dependency changes, review).
* `README.md`: project goal and CLI usage.

## Phases

1. **Before the next quarterly sync:** `manifest`, `upstream`, `transform`,
   `verify`, `replay`, `versions`, `report`, `github`; commands `sync`,
   `resolve`, `status`, `push-prs`, `promote`, `discard`, `verify`.
2. **Done:** `add` and `publish-repo`.
3. **Only if needed:** templates and config drift detection; a read-only
   check that dependency packages exist on Cloudsmith.
