# Temporary convention migration

This workflow exists only while older `pxr-*` repositories are being brought
onto current Untwine conventions.

Delete this document and its `AGENT.md` routing entry once all repositories are
updated.

Use this workflow only for a policy change that does **not** change the
repository's OpenUSD release.

Examples include:

* removing per-file attribution
* reverting historical cosmetic formatting
* adopting the shared public `PXR_NS`
* changing a packaging convention

When a repository carries more than one outstanding convention, always apply
"removing per-file attribution" and "reverting historical cosmetic
formatting" first, as their own slice, ahead of any other convention. That
slice reconstructs every touched file byte-for-byte against `open-usd`
(verified per [`source-transformations.md`](source-transformations.md)), so
later convention slices in the same repository start from a clean, verified
baseline instead of being tangled up with unrelated header-comment or
guard-naming drift.

## Sequencing with a release sync

When a repository also needs an OpenUSD release sync
([`sync-upstream.md`](sync-upstream.md)), do the three kinds of work in this
order, never interleaved:

1. Apply any outstanding convention slice owned by the `Restructure ...`
   commit (source-level conventions: attribution/cosmetic cleanup first as
   above, then `PXR_NS` adoption or another source convention).
2. Run the release sync.
3. Apply any outstanding convention slice owned by the `Add minimal release
   configuration.` commit (Conan, CI, CMake packaging conventions).

Fixing `Restructure`-owned drift before the sync keeps the sync's
cherry-pick conflicts limited to genuine upstream changes instead of also
re-litigating unrelated convention drift on top of them. Release-configuration
conventions are independent of OpenUSD source content and never interact
with the sync mechanics, so they belong after it, once the synced tree is
built and its tests pass.

## 1. Freeze the OpenUSD release

Confirm the repository's current OpenUSD version and keep it unchanged.

Do not fold a release sync into this work merely because sibling repositories
have already moved to a newer OpenUSD tag.

When copying a convention from a newer sibling, translate version-dependent
values such as dependency pins and `system_package_version` back to the target
repository's current release.

## 2. Define the exact invariant

Write down the convention being introduced and its scope.

Choose a completed repository as a canonical example, but verify that its
dependency shape and Python shape match the target before copying anything.

Do not opportunistically modernize unrelated files. Treat independent
conventions as independent changes even when applying them in one session.

## 3. Audit before editing

For each target repository:

1. confirm its current OpenUSD version
2. inspect `main` relative to its own `open-usd`
3. locate the commit that owns the content being changed
4. search the full tree, including uncommon extensions, templates, patches,
   tests, and workflow files
5. inspect later commits that touch the same files
6. when the convention touches CMake install rules, confirm every
   `install(TARGETS ...)` sends `RUNTIME`, `LIBRARY`, and `ARCHIVE` all to
   `${CMAKE_INSTALL_LIBDIR}`, matching upstream OpenUSD's own
   `pxr_library()` macro (see `packaging.md`); a `RUNTIME` destination split
   off into `${CMAKE_INSTALL_BINDIR}` builds and passes on every platform
   except Windows, where any file installed alongside the target by a
   separate rule still pointed at `${CMAKE_INSTALL_LIBDIR}` (a `plugInfo.json`
   resource, for example) silently ends up in the wrong directory
7. when the convention touches `.gitignore` or a repository vendors
   third-party source into the working tree at CI time rather than
   committing it, confirm no unanchored pattern (`env/`, `lib/`, `build/`,
   ...) matches a real vendored directory of the same name; see "Python
   sdist contents follow `.gitignore`" in `packaging.md`
8. if the repository has a `test/testWrapper.py`, confirm it actually
   `os.chdir()`s into the temporary directory it creates
   (`Path(tempfile.mkdtemp())`) rather than computing a resolved path and
   discarding it. This is a known copy-paste bug present, as of this
   writing, in every repository that carries this script. It is invisible
   under serial `ctest`; it only surfaces as cross-test fixture
   contamination or, on Windows, a full hang once tests actually run in
   parallel (`ctest --preset conan-release -jN`). Fix it in place — fold
   the fix into the commit that owns the file (usually `Add minimal
   release configuration.`) via a plain `--fixup` — and re-validate with
   that repository's real parallel `ctest` invocation, not a serial one.
   A test that reads a fixture file directly, with no `testWrapper.py`
   and no `WORKING_DIRECTORY` of its own, may have been silently relying
   on another test's leftover file in the shared working directory and
   need its own `WORKING_DIRECTORY` fix once that contamination stops.
9. if the repository's `test/CMakeLists.txt` has a `WIN32`-only
   `add_test()` override that computes a test's `PATH` from
   `$<TARGET_RUNTIME_DLL_DIRS:...>`, confirm that list also includes
   `onetbb`'s directories explicitly (`"${onetbb_LIB_DIRS_RELEASE}"`,
   `"${onetbb_BIN_DIRS_RELEASE}"`) whenever the target links `pxr::tf`
   (or anything else that pulls in `onetbb` transitively).
   `TARGET_RUNTIME_DLL_DIRS` does not pick up a Conan-imported transitive
   dependency's own directory, so `PxrTf.dll`'s own dependency on
   `tbb12.dll` silently fails to resolve. On a headless Windows runner
   this does not fail fast — it hangs for the full `ctest --timeout`
   (`NtRaiseHardError`/`LdrpReportError`, see `windows-debugging.md`),
   and only some tests in the suite are affected, not obviously all of
   them, which makes it easy to misdiagnose as something else (a Windows
   Defender scan, a stdin/subprocess issue, or the parser code itself
   were all tried and ruled out chasing this exact bug once). Do not fix
   it by deleting the macro and relying on `ctest`'s own
   Conan-generated preset environment instead — that looks correct (a
   manual reproduction of the preset's `PATH` on a live runner did fix
   the immediate symptom) but did not hold up in the real, automated
   `ctest --preset conan-release` run. Keep the macro; extend the `PATH`
   it computes, matching this proven pattern instead.
10. when applying the attribution/cosmetic-cleanup slice, run a whole-tree,
    unscoped `rg -n '#include "pxr/' .` — not one narrowed to the files a
    "Modified by" search already flagged. A repository split before the
    all-angle-bracket convention was settled can have same-library includes
    (a `vt` file including another `vt` header) still in quoted form; these
    are easy to miss because they look like a same-library include is
    supposed to stay quoted. It is not: see `source-transformations.md`.
11. confirm `NOTICE.txt` carries the repository-level attribution paragraph
    from `source-transformations.md`. A repository split before that
    paragraph became standard will be missing it entirely, not merely
    carrying a stale version of it.
12. for any renamed file the standalone build does not actually consume (a
    superseded monolithic `pxr_library(...)` `CMakeLists.txt` left in place
    by the original split, for example), check whether its internal paths
    were hand-adjusted to the new tree layout. If so, reconstruct it
    byte-for-byte from `open-usd` instead per the "Legacy build files
    carried by rename" section of `source-transformations.md` — do not
    spend a slice keeping a dead file's paths consistent.

A later commit may contain a legitimate fix. Do not erase it by replacing a
whole file from upstream without reviewing its history.

## 4. Apply one owning-commit slice at a time

Do not mix restructuring-owned and release-configuration-owned changes in the
working tree.

For a source convention:

1. reconstruct from the repository's current `open-usd` content
2. reapply required standalone transformations
3. reapply later intentional fixes
4. stop before changing release-configuration files

For a build or packaging convention, change only the owning configuration
files and the tests needed to prove it.

Do not bump the OpenUSD-derived version merely because a convention changed.

## 5. Review the slice before rewriting history

Show the working-tree diff for the current slice to the maintainer before
folding it into history.

Once confirmed:

```bash
git commit --fixup=<target>
git rebase -i --autosquash ...
```

Use a plain fixup by default. Do not rewrite an existing commit message merely
to narrate the newly applied convention.

If the existing commit description would become actively wrong or misleading,
propose the exact replacement wording before using:

```bash
git commit --fixup=amend:<target>
```

`--fixup=amend:<target>` requires preserving Git's `amend!` marker in the
edited commit message so autosquash can recognize it. Do not overwrite the
message in a way that removes that marker.

### Commit description style

When a `Restructure ...` or `Add minimal release configuration.` description
needs correcting as part of a slice, match the style already established
across the coordinated release, not just the wording already in this one
repository.

`Restructure the '<lib>' library as a standalone package.`:

* Subject: `Restructure the '<lib>' library as a standalone package.`
* Body: a semicolon-separated bullet list, one item per line (wrap a long
  item with a two-space continuation indent), the final bullet ending with a
  period instead of a semicolon. Include, in this order, only the bullets
  that apply:
  1. `Isolate the '<lib>' module from the OpenUSD repository;`
  2. the `pxr.h.in` bullet: `Integrate a customized 'pxr.h.in' into the
     '<lib>' library, with renamed namespace and version macros bound to the
     library;` when the library has no direct dependency to bridge, or
     `Add a 'pxr.h.in' customized for the library, with renamed
     namespace/version macros and internal-namespace imports for its
     dependencies;` when one or more direct dependencies are bridged into the
     internal namespace;
  3. `Update include directives to use the new header prefix path;`
  4. any other bullet describing restructuring actually performed (moving
     tests, removing obsolete build artifacts, dropping the
     `#!/pxrpythonsubst` shebang, and so on) — state only what was actually
     done;
  5. always last: `Add identification and licensing information mandated by
     OpenUSD.`

  Do not add a bullet describing per-file attribution or copyright-disclaimer
  edits. The repository-level notice belongs to bullet 5 and to the standard
  `NOTICE.txt` paragraph in
  [`source-transformations.md`](source-transformations.md), not to a
  source-file-level bullet.

`Add minimal release configuration.`:

* Subject: `Add minimal release configuration.`
* Body: a semicolon-separated bullet list, the final bullet ending with a
  period instead of a semicolon. Include, in this order:
  1. `Configure CMake to build and test the library;`
  2. `Package the library for both PyPI (runtime and development wheels via
     pyproject.toml) and Conan (recipe and release workflow publishing to
     the shared 'untwine' Nexus remote), so it can be consumed from Python
     or from plain CMake/Conan projects;` — only once both packaging paths
     actually exist in the repository. Do not describe Conan packaging the
     repository does not yet have.
  3. `Add GitHub CI for major platforms and a .gitignore;`
  4. any other bullet describing a release-configuration fix actually made
     (restoring `moduleDeps.cpp`, adding a minimal `testWrapper.py`, fixing
     test paths, linking a missing test dependency, and so on) — state only
     what was actually done. This is for a fix to something that already
     existed and behaved differently before. Do not add a bullet enumerating
     correctness details of content introduced for the first time in the same
     pass (a first Conan recipe, a first CI workflow, a dependency the
     repository never declared before) — those belong folded silently into
     whichever bullet above already covers the addition, since there is no
     prior behavior being corrected.

     This also excludes the cataloged, checklist-driven fixes from the
     "Audit before editing" section above (an `install(TARGETS ...)` that
     split `RUNTIME` off into `${CMAKE_INSTALL_BINDIR}`, an unanchored
     `.gitignore` template entry, the
     `testWrapper.py` `os.chdir()` bug, the Windows onetbb DLL-path gap, an
     `EXACT` keyword, a dependency version bump to match the coordinated
     release). Every migration checks for these by design, so finding and
     fixing one is expected baseline compliance, not a repository-specific
     discovery — fold it silently into bullet 1 or 3 above rather than giving
     it its own bullet. Reserve a standalone bullet for a fix that is
     genuinely particular to this repository's own history or behavior (a
     wrong test binary selected on Windows, a missing RPATH entry for this
     library's own Conan-installed dependency, and so on) — something a
     reader could not have predicted from the checklist alone.

If a repository's existing description has merely drifted in wording from
this style, correct it in the same `--fixup=amend:<target>` pass. If the
correction would require *describing* content the repository does not yet
have (a missing Conan recipe, for example), do not just reword the
description to claim it; adding that content is its own properly scoped and
reviewed slice, done first.

After rebasing, verify that only one commit with the target title remains.

## 6. Verify the rewrite

Follow [`history.md`](history.md).

In particular:

* preserve filtered OpenUSD metadata
* preserve Untwine author attribution
* allow current Untwine committer metadata after replay
* compare the new tip with the old tip
* verify the resulting tree delta contains only the requested convention

Repeat the owning-commit cycle for the next slice when needed.

## 7. Validate every repository

Run [`validation.md`](validation.md) for every affected repository.

Do not assume a convention proven in a small C++ library also works unchanged
in a Python-dependent library.

Do not push without explicit confirmation.

## 8. Fixing a bug that requires an immediate republish

The decision that matters is not whether the package has ever been published,
but whether *this specific fix* needs to go out now because the currently
published version is actually broken for some consumer.

A dormant or preventive fix (a convention applied proactively, a hardening
change with no known broken consumer) still belongs in the owning commit as
usual, per the [history invariant](../AGENT.md), even in a repository whose
current version is already live on PyPI. Rewriting that commit does not
retroactively change what PyPI already has; it only shapes what the *next*
real release will contain.

Use this section instead when the published version is actually broken and
needs a new release now:

1. PyPI never allows replacing the file for an already-published version.
   Bump the version by appending a new trailing numeric segment instead of
   advancing to the next semantic-looking version:

   ```text
   2023.1.0   ->   2023.1.0.1
   ```

2. Check every sibling repository for a version pin on the fixed package. A
   wildcard pin on the changed segment (`pxr-tbb-dev==2023.1.0.*`) already
   accepts the bump and needs no change; an exact pin does not and must be
   updated alongside the fix.

3. Land the fix as a new, focused commit rather than folding it into or
   rewriting the repository's existing `Add minimal release configuration.`
   commit. That commit's exact content already correlates with the version
   currently marked broken; do not make history claim the fix was present
   in a release where it was not.

Do not push, and do not dispatch the release workflow, without explicit
confirmation.
