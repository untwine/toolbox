# AGENT.md: maintaining Untwine `pxr-*` repositories

This guide is for agents working on the standalone libraries extracted from
Pixar's OpenUSD repository.

Untwine has three distinct maintenance workflows:

1. Split a new OpenUSD library into a standalone repository.
2. Apply a new Untwine convention to an existing repository.
3. Sync an existing repository to a newer OpenUSD release.

Choose one workflow before editing. Do not mix them unless the request
explicitly requires it. The shared rules below apply to all three.

## Choose the workflow

| Requested work | Use |
| --- | --- |
| Create a new `pxr-*` repository from an OpenUSD subtree | Workflow 1 |
| Bring an existing repository in line with a newer Untwine convention, without changing its OpenUSD release | Workflow 2 |
| Move an existing repository from one OpenUSD release to another | Workflow 3 |

If the task is ambiguous, inspect the current branches, version, and commit
history before deciding. Ask rather than silently turning a convention update
into a release sync, or a release sync into a broad cleanup.

## Repository model

An extracted repository normally has two long lived branches:

* `open-usd` contains the history filtered from OpenUSD for one subtree.
* `main` contains the same source after Untwine's standalone transformation,
  build configuration, packaging, CI, and any necessary fixes.

The intended `main` history begins with these commits, in this order:

1. `Restructure the '<lib>' library as a standalone package.`
2. `Add minimal release configuration.`
3. One focused commit per independent source, portability, or build fix.

The first commit is mechanical. The second owns build and release
configuration. A behavioral or portability fix belongs in neither.

`pxr-tbb` is a support package used at build time and runtime, including by
Python wheels. It is not an OpenUSD library extraction, so do not apply the
subtree workflow to it unless the task specifically says to do so.

## Shared rules

### Keep the source delta minimal

The standalone source should be reconstructible as:

```text
upstream source + required standalone substitutions + separately justified fixes
```

Do not introduce cosmetic formatting, spelling cleanup, unrelated header guard
changes, or stylistic rewrites. A small diff is an important project property:
it makes release syncs reviewable and makes fixes suitable for contributing
back to OpenUSD.

When an existing file has accumulated old cosmetic edits, reconstruct it from
the matching `open-usd` version and reapply only the required standalone
changes. Do not patch forward from already modified content when a clean
reconstruction is practical.

#### Verify the whole file, not just the pattern you targeted

A narrow, single-purpose edit (a script that strips one comment line, a regex
that renames one macro) only proves that one pattern is gone. It says nothing
about the rest of the file, and other drift hides in exactly the files you
are already touching. After any mechanical edit to a source file, diff that
file's full content against its matching `open-usd` version and confirm every
remaining difference is one of the known, catalogued substitutions (namespace
macros, include path rewrite, the standalone `pxr.h.in` swap) or a separately
justified later fix. Do not stop at confirming your own change landed.

Header include guards (`#ifndef PXR_BASE_<LIB>_<NAME>_H`) are NOT one of
those substitutions: leave them exactly as upstream has them. Only the
per-library internal-namespace macros (`<LIB>_NAMESPACE_OPEN_SCOPE`,
`_CLOSE_SCOPE`, `_USING_DIRECTIVE`, per the namespace strategy reference)
and the freestanding `pxr.h.in` (which has no upstream counterpart at all)
use the flattened `PXR_<LIB>_...` form. A completed repository that already
has every header guard renamed to `PXR_<LIB>_..._H` is not evidence that the
rename is correct; check what the *majority* of guards in the sibling
repositories look like (`rg -c '#ifndef PXR_BASE_' src` versus
`rg -c '#ifndef PXR_[A-Z]+_[A-Z0-9_]+_H$' src`, excluding each library's own
`pxr.h.in`) before trusting a single repository's existing state, since that
state can itself be the unreviewed drift you are trying to catch.

Do this for the full file set being touched, not a sample. A script is
reasonable for applying the fix; do not skip writing one for verifying it.
Known failure patterns worth checking explicitly, each of which has slipped
through unnoticed before:

* `#include "pxr/...">` left as a quoted include instead of
  `#include <pxr/...>` (see "Use `<angle-bracket>` form consistently" under
  the CMake reference). Grep the whole tree for `#include "pxr/` after any
  include-touching change; it should return nothing.
* A blank line dropped or added immediately after the closing `//`/`#` of the
  license header. The correct shape differs by file kind: header guards have
  no blank line before them; `.cpp` sources and Python modules keep exactly
  the one blank line upstream has before the first `#include`/`import`. A
  line-removal script that also strips "the blank line after" will get this
  wrong for whichever file kind it wasn't written for.
* A header include guard renamed away from its upstream
  `PXR_BASE_<LIB>_..._H` form, or a namespace-scope macro left in its
  monolithic upstream form (`PXR_NAMESPACE_OPEN_SCOPE`) instead of the
  library-specific rename (`<LIB>_NAMESPACE_OPEN_SCOPE`). These are opposite
  mistakes; do not fix one by introducing the other.

When the same kind of fix applies to many files, write the diff-against-
`open-usd` check as a small script before declaring the fix complete, the
same way you would script the fix itself. Spot-checking two or three files
and generalizing from them is how this drift survives past review.

### Put attribution in `NOTICE.txt`, not source files

Do not add `Modified by ...`, `Updated by ...`, or similar comments to
individual files. Remove old Untwine attribution comments when normalizing a
repository, but preserve upstream copyright and license headers exactly.

Append one repository level notice:

```text
This repository is a modified, standalone redistribution of the '<lib>'
library from Pixar's OpenUSD, restructured for independent building and
packaging. See the git history for details of the changes made.
```

### Preserve commit boundaries

Classify every change before committing it:

* File moves, include rewrites, generated namespace header, and removal of
  obsolete monolithic build artifacts belong to `Restructure ...`.
* CMake, package metadata, CI, tests, and version pins belong to
  `Add minimal release configuration.`
* Source fixes, warning fixes, portability changes, and macro qualification
  each get their own focused commit after the two core commits.

When curating an existing repository's history, use fixup commits and
autosquash to place changes into the correct existing commit. Preserve the
original metadata of filtered OpenUSD commits. For Untwine specific commits,
preserve the original author attribution but let rebase or cherry pick record
fresh committer metadata. Their committer dates should follow the newer
OpenUSD commits they have been replayed onto. If the task explicitly requires
preserving published commit identities, use new focused commits instead of
rewriting history.

Never add AI attribution, `Co-Authored-By`, or session tracking trailers.
Authorship of these commits belongs to the maintainer, who must carefully
review the diff before it lands; the commit record should reflect that
review, not the tool used to help produce it.

### Declare only direct dependencies

The following places describe the direct dependency graph and must agree:

* top level `CMakeLists.txt`
* installed `cmake/<lib>-config.cmake.in`
* `conanfile.py`
* Python build metadata where relevant
* dependency includes and namespace bridges in `pxr.h.in`

Do not add transitive dependencies merely because their symbols are visible
through another library.

### Build and test before declaring success

Use the repository's Conan workflow as the canonical C++ build and test path.
It must configure, build, package, and test the library against packaged
dependencies. An ad hoc CMake build is useful for diagnosis, but does not
replace validation through the Conan recipe and workflow.

For every repository that ships Python bindings, also build the Python wheel
through the wheel workflow and verify that the built wheel installs and
imports successfully. Testing only from the source or build tree is not
sufficient.

The monolithic OpenUSD build can hide missing includes, libraries, namespace
imports, and runtime files, so it is not a substitute for either standalone
packaging path.

During diagnosis, prefer a keep going build so one pass exposes more than the
first failure:

```bash
cmake --build . -- -k
```

Run the native tests through Conan, the Python tests from the built wheel when
enabled, package consumption tests, and the relevant workflow matrix before
finishing.

## Workflow 1: split a new library

Use this workflow when creating a new standalone repository from an OpenUSD
subtree.

### 1. Establish the source and dependency graph

Before editing:

1. Identify the exact OpenUSD tag and subtree path.
2. Create or verify the filtered `open-usd` branch.
3. Read the upstream CMake declarations for sources, public headers, tests,
   Python modules, generated files, and direct dependencies.
4. Inspect the nearest completed Untwine repositories for current packaging
   and CI conventions. Prefer a library with similar C++, Python, and
   dependency requirements.
5. Record whether Python bindings are absent, required, or optional.

Do not infer the complete file or test list from directory contents alone.
OpenUSD's CMake files contain platform gates and registrations that filenames
do not reveal.

### 2. Create the restructuring commit

Move the filtered source into the standalone layout:

* library source and headers: `src/pxr/<lib>/`
* tests formerly in `testenv/`: `test/`
* Python binding glue: `src/python/`

Then apply only the mechanical substitutions required by the new layout:

* Rewrite include paths to the installed standalone prefix.
* Use `<angle-bracket>` form consistently for `pxr/...` includes.
* Add the per library `src/pxr/<lib>/pxr.h.in` described in the namespace
  reference below.
* Remove empty single include `.cpp` stubs made unnecessary by a header only
  conversion.
* Remove `#!/pxrpythonsubst` and the single `#` spacer immediately following
  it, when present.
* Preserve real shebangs such as `#!/usr/bin/env python` and their spacing.
* Add the repository level `NOTICE.txt` statement and do not add per file
  attribution.

Apply include substitutions to every file type, including `.cc`, `.dox`, and
vendored patch files. A scripted extension allowlist is not enough.

Do not include build configuration, behavior changes, warning fixes, or
cosmetic cleanup in this commit.

### 3. Create the release configuration commit

Add the files that make the restructured source independently consumable:

* top level and subdirectory `CMakeLists.txt` files
* `cmake/<lib>-config.cmake.in`
* `conanfile.py`
* `pyproject.toml` and `pyproject-dev.toml` when applicable
* `.github/workflows/`
* `.gitignore`
* repository `README.md`
* standalone test registration and explicit Python module dependency glue

Update imports that depended on the old `testenv` Python package layout.

#### Restore `moduleDeps.cpp` explicitly

OpenUSD commit `7437d0c5f07b9eebbd179242633fa1995295b819` replaced the checked
in `moduleDeps.cpp` with one generated by `genModuleDepsCpp.cmake` at build
time. Untwine keeps it checked in and reviewable: add it and compile it for
libraries with a Python module, skip it otherwise.

Reconstruct it from the target release's rules, not an old deleted copy:

1. Read the library's `pxr_library()` declaration at the target tag.
2. Read `Public.cmake`, `genModuleDepsCpp.cmake`, and `moduleDeps.cpp.in` at
   that tag.
3. Set `libraryName` to the CMake target name and `moduleName` to
   `_get_python_module_name()`'s output.
4. Set `reqs` to the `LIBRARIES` entries that resolve to OpenUSD targets
   (including `python` when the generator would), in CMake order.
5. Apply the normal standalone include and namespace substitutions.
6. Register the file under the same Python enabled condition as the bindings.

The result must match what the generator would have emitted — just checked in
instead of generated. Treat this as OpenUSD's current build rules, not a
permanent contract; revisit if they or Untwine's conventions change.

Use the build and packaging reference below rather than copying old workflow
versions or remote configuration blindly from an early repository.

### 4. Isolate genuine fixes

Build the two core commits first. If the standalone build exposes a real
source defect, confirm whether it already exists upstream. Give each
independent fix its own commit after `Add minimal release configuration.`

Examples include:

* a missing link dependency
* a required namespace qualification
* an MSVC warning fix
* a Windows path or DLL placement fix
* a musl portability definition

Keep fixes small enough to propose upstream independently.

### 5. Validate the extracted repository

Complete the common validation checklist near the end of this file. Also
compare the standalone source against the filtered branch using the known path
mapping. Review unexpected differences one by one.

## Workflow 2: apply a new convention to existing repositories

Use this workflow for a policy change that does not change the OpenUSD release.
Examples include removing per file attribution, reverting cosmetic formatting,
adopting the shared public `PXR_NS`, or updating a packaging convention.

Never let this workflow drift into workflow 3. If a target repository is
behind the newer OpenUSD release that its siblings have already moved to,
apply the convention at the repository's *current* release anyway. A release
sync is a separate, later task that the maintainer will request explicitly;
do not fold it in just because a newer tag exists or because sibling
repositories used it as their canonical example at a newer version. When
copying a convention from a canonical example that is itself ahead on the
OpenUSD release, translate its version-specific values (dependency pins,
`system_package_version`, etc.) down to the target repository's current
release rather than importing the example's newer values verbatim.

### 1. Define the convention and its scope

Write down the exact invariant being introduced and which repositories it
affects. Choose one completed repository as the canonical example, but verify
that its dependency and Python shape match the target before copying anything.

Do not opportunistically modernize unrelated files. If several independent
conventions are requested, treat them as separate changes even when applying
them in one work session.

### 2. Audit before editing

For each target repository:

1. Confirm its current OpenUSD version.
2. Inspect `main` relative to its own `open-usd` branch.
3. Locate the commit that owns the content being changed.
4. Search the full tree for all occurrences, including uncommon extensions,
   generated templates, patches, tests, and workflow files.
5. Note later commits that touch the same files before reconstructing them.

A later commit may contain a legitimate fix. Replacing a whole file from
upstream without checking later history can silently erase that fix.

### 3. Apply the smallest policy delta, one owning commit at a time

Treat the restructuring commit and the release-configuration commit as
separate units of work, even within a single convention change. Do not mix
their content in the working tree at the same time.

Start with whichever part of the convention belongs to the restructuring
commit, if any:

For source cleanup, reconstruct from the repository's current `open-usd`
content and reapply only the required standalone transformations plus later
intentional fixes.

Stop there and complete step 4 for this slice before touching anything that
belongs to the release-configuration commit.

Only once the restructuring slice (if any) has been folded in, move on to the
part of the convention that belongs to the release-configuration commit:

For build or packaging policy, change only the owning configuration files and
the tests needed to prove the new convention.

Do not bump the OpenUSD derived version merely because a convention changed.

### 4. Show the slice for validation, then fold it into history

Before folding anything in, present the working-tree diff for the current
slice (restructuring-owned changes, or release-configuration-owned changes)
to the maintainer and wait for confirmation. Do not chain straight from
editing into the fixup/rebase.

Once confirmed, place the change correctly in history: in Untwine's curated
history, use `git commit --fixup=<target>` followed by an autosquash rebase,
so the slice lands in the commit it belongs to. A genuine source fix remains
a separate commit.

Default to leaving the target commit's title and body untouched. A workflow
2 change folds new file content into an existing commit; it does not rewrite
that commit's description. Do not add bullets narrating the convention just
applied, reword existing bullets, or otherwise touch the message, even when
you can see how to phrase it more accurately. If the existing description
would become actively wrong or misleading once the slice lands (not merely
incomplete), propose the exact replacement wording to the maintainer and get
explicit confirmation before using `--fixup=amend:<target>` instead of a
plain `--fixup=<target>`.

`--fixup=amend:<target>` requires editing the commit message interactively
(or via a `GIT_EDITOR` override that preserves the `amend!` marker line
git pre-populates) so autosquash can still recognize and fold it; passing
`-m`/`-F` directly is rejected by git and, if worked around by overwriting
the whole message file, silently defeats autosquash detection and leaves a
duplicate commit. Verify immediately after the rebase that only one commit
with the target's title remains.

After rewriting, verify metadata follows the shared rule: OpenUSD metadata is
preserved, Untwine author attribution is preserved, and Untwine committer
metadata reflects the replay. Then compare the new tip with the old tip. The
tree delta should contain exactly the requested policy change and no incidental
content.

Repeat steps 3 and 4 for the next slice, if the convention has one belonging
to the other commit.

### 5. Validate every affected repository

Run the same searches, build, test, and package checks in each repository. Do
not assume a convention that worked in `pxr-arch` also works unchanged in a
Python dependent library such as `pxr-tf`.

## Workflow 3: sync to a newer OpenUSD release

Use this workflow when moving a repository to a new OpenUSD tag.

### 1. Inspect before running the sync

Confirm:

* the working tree is clean
* `main` and `open-usd` exist locally or on `origin`
* the subtree path is correct
* the new OpenUSD tag exists
* the current repository builds before the sync, when practical

Read `README.md` and the comments in `sync-upstream.sh` before running it.

### 2. Run the mechanical sync

From the target `pxr-*` repository:

```bash
~/dev/untwine/toolbox/sync-upstream.sh pxr/base/<lib> v26.08
```

The script:

1. clones OpenUSD into an isolated temporary directory
2. filters the requested subtree at the new tag
3. rebases `open-usd` onto the new filtered history
4. creates `main-sync` from the updated `open-usd`
5. cherry picks the repository's own commits one at a time

It intentionally stops at the first real conflict. Resolve the stopped cherry
pick, then continue with the remaining commit list printed by the script.

### 3. Resolve conflicts according to their type

#### Source content conflict

Regenerate the standalone file from the new `open-usd` content plus the same
mechanical substitutions used by the restructuring commit. Do not hand merge
old upstream source into new upstream source unless reconstruction would erase
a later intentional fix.

#### Rename and delete conflict

Inspect why upstream deleted the old path:

```bash
git log --oneline open-usd -- <old-path>
```

If it is a deliberate upstream removal, delete the standalone copy too.

#### New file under a renamed directory

Move the file into the standalone layout and apply the normal substitutions.
Register it in the correct standalone CMake list.

#### Cherry pick becomes empty

Verify that the new upstream source already contains the intended change. If
so, skip that cherry pick. Never assume an empty result is harmless without
checking what made it empty.

### 4. Find changes Git did not flag

An entirely new upstream file at the filtered repository root may land in the
old location without a conflict. A clean cherry pick therefore does not prove
the transformation is complete.

After replaying all commits:

* inspect files outside the expected `src/`, `test/`, `cmake/`, and repository
  configuration locations
* repeat the full tree searches from the validation checklist
* compare the new upstream CMake declarations with standalone source and test
  registration
* inspect upstream changes to already converted files for newly introduced
  monolithic namespace macros or old include prefixes

Fold new source movement and substitutions into `Restructure ...`. Fold new
source and test registration into `Add minimal release configuration.`

### 5. Update versions and exact pins

Once the replay is correct, update the release derived versions together:

* CMake project version
* installed CMake dependency versions
* top level CMake dependency versions
* Conan recipe version and direct requirements
* `system_package_version`
* Python project version and dependency pins
* documented install examples

For OpenUSD tag `v26.08`, use:

* `0.26.8` for CMake project and package config versions
* `26.8` for Conan and Python package versions where the repository already
  follows that short form

Every sibling library intended to remain on the same release must be pinned
exactly. Fold version changes into `Add minimal release configuration.`

### 6. Validate and finish

Run the full validation checklist. Pay particular attention to new upstream
files and to dependency ABI mismatches from stale local packages.

Delete the temporary `onto-<version>` branch only after validation. Do not push
without explicit confirmation. When pushing rewritten branches, use
`--force-with-lease`, never an unconditional force push.

## Reference: namespace strategy

Every generated `pxr.h.in` defines two different namespace concepts.

### Shared public namespace

`PXR_NS` and `PXR_NS_GLOBAL` remain the literal public namespace `pxr` in every
repository. Do not introduce `GF_NS`, `VT_NS`, or another per library public
namespace. Existing OpenUSD consumers are written against names such as
`pxr::TfToken` and `pxr::SdfPath`.

### Versioned internal namespace

Each library defines its own internal namespace macro:

```text
<LIB>_INTERNAL_NS = pxrInternal_v<major>_<minor>_<patch>__pxrReserved__
```

The matching `<LIB>_NAMESPACE_OPEN_SCOPE`, `_CLOSE_SCOPE`, and
`_USING_DIRECTIVE` macros also use that internal namespace.

Include each direct dependency's `pxr.h` and import its internal namespace into
the current library's internal namespace:

```cpp
#include <pxr/arch/pxr.h>

namespace TF_INTERNAL_NS {
    using namespace ARCH_INTERNAL_NS;
}
```

Place these bridges after the library's own public namespace bridge so every
dependency macro has already been defined. List direct dependencies only.
Using directives propagate through the dependency chain.

Do not replace the explicit bridges with `using namespace PXR_NS;`. That makes
symbol visibility depend on include order and can hide a missing dependency.
Do not assume all libraries share one literal internal namespace. Exact package
pins enforce the intended release alignment, while explicit bridges keep the
code correct if versions ever drift.

### Macros expanded in consumer code

A macro invoked from another library expands in the caller's namespace. Such a
macro must qualify every referenced public symbol with `PXR_NS::`; the current
library's internal bridge cannot help at the caller's expansion site.

Examples include `TF_ERROR`, `TF_INSTANTIATE_SINGLETON`,
`TF_BITS_FOR_VALUES`, `ARCH_CONSTRUCTOR`, and `ARCH_DESTRUCTOR`.

A qualification fix is a focused source fix after the two core commits. When
editing a backslash continued macro, preserve the original continuation
alignment line by line.

## Reference: build and packaging conventions

### CMake

* Express the language floor on the target with
  `target_compile_features(<target> PUBLIC cxx_std_17)`.
* Do not rely on a guarded global `CMAKE_CXX_STANDARD`; Conan toolchains set it
  before the project is evaluated.
* Use `find_package(... EXACT REQUIRED)` for direct sibling libraries in both
  the build and installed package config.
* Install Windows runtime artifacts to `${CMAKE_INSTALL_BINDIR}`. `LIBRARY` and
  `ARCHIVE` use `${CMAKE_INSTALL_LIBDIR}`.
* Wheel builds that collect DLLs under a package `.libs` directory must set
  both `CMAKE_INSTALL_LIBDIR` and `CMAKE_INSTALL_BINDIR` to that destination.

### Conan

* `requires()` lists direct dependencies only.
* The recipe's package identity may use `26.8`, while CMake reports `0.26.8`.
  Set `system_package_version` accordingly in `package_info()` so generated
  CMake metadata satisfies exact pins.
* If a package must use its installed CMake config instead of Conan's generated
  config, set `cmake_find_mode` to `none` and expose the installed config
  directory through `builddirs`.
* Configure the public Nexus read remote in every workflow that resolves
  Untwine packages. Authenticate only where the server operation requires it,
  especially release uploads. Never embed credentials in the repository.
* Rebuilding the same version creates another recipe or package revision on
  Nexus. Verify the uploaded revision rather than assuming a rerun replaced the
  previous one.

### Python aware Conan packages

When the C++ package builds Python bindings:

* Pass the interpreter running Conan to CMake with `sys.executable`, converting
  Windows backslashes to forward slashes before writing the toolchain value.
* Represent the Python minor version as a Conan option so it participates in
  `package_id`.
* Validate that the requested option matches the interpreter running Conan.
* For optional bindings, allow `None`, omit the Python dependency when disabled,
  and forward the option one dependency edge at a time.
* Publish a workflow matrix for each supported Python version, plus a no
  bindings variant when the library supports one.

Keep the Python versions in the Conan and wheel workflows aligned and revisit
the list as CPython releases change.

### CI

Use the newest already validated Untwine workflow as the template. In
particular, verify:

* manual dispatch is available and obsolete inactivity prone schedules are not
  the only trigger
* action major versions match the current canonical repositories
* the operating system matrix keys and `include` keys use identical casing
* current GitHub runner labels and current Visual Studio generators are used
* release and test workflows select the same Python version for the same binary
* each workflow configures every external package remote it consumes

Do not treat a copied workflow as correct merely because its YAML parses.

## Reference: validation checklist

Run searches from the repository root. Prefer `rg`; the following patterns are
examples and may need exclusions for documentation that intentionally explains
an old form.

```bash
rg -n '#include "pxr/' .
rg -n 'pxr/(base|external)/' .
rg -n '\bPXR_NAMESPACE_(OPEN|CLOSE)_SCOPE\b' src test
rg -n '(Modified|Updated) by Jeremy Retailleau' .
```

Then verify:

* no unexpected source files remain at the old flat root
* source, headers, generated files, Python modules, and tests match the new
  upstream CMake declarations
* direct dependencies agree across CMake, installed config, Conan, Python
  metadata, and `pxr.h.in`
* sibling dependency pins are exact and use the correct version form
* the Conan workflow configures, builds, packages, and runs the C++ tests for
  every relevant variant
* a minimal downstream project can consume the installed CMake package
* Conan package consumption tests pass for relevant variants
* the wheel workflow builds a wheel for every supported Python version, and
  each built wheel installs and imports successfully in a clean environment
* the GitHub workflow matrix covers Linux, Windows, Intel macOS, and Apple
  silicon where supported

When many Python tests fail together, first check for a stale dependency built
against another Python ABI. On macOS and Linux, inspect linked libraries with
`otool -L` or `ldd`. On Windows, use `dumpbin /dependents`.

## Reference: history rewrite safety

* Never push without explicit confirmation.
* Use `--force-with-lease` for rewritten remote branches.
* Preserve the original author and committer metadata of filtered OpenUSD
  commits.
* For Untwine specific commits, preserve author attribution but allow rebase,
  autosquash, and cherry pick to write current committer metadata. Do not
  restore or backdate their old committer dates after replaying them onto a
  newer OpenUSD history.
* Before a rewrite, record the old tip. Afterward, inspect
  `git diff --stat <old-tip> <new-tip>` and the full diff.
* A rewrite intended to alter only commit structure or metadata must have an
  empty tree diff.
* Resolve a genuine later deletion as a deletion; do not resurrect the file
  merely to satisfy an earlier cherry pick.
* Prefer `git worktree add --detach <commit>` for a clean reconstruction.
* If updating a worktree from a tree object, use
  `git read-tree --reset -u <tree>`. `git checkout-index -a -f` does not remove
  files absent from the target tree and can leave stale content behind.
* Before replacing a file wholesale, inspect every later commit that touches
  it. Reapply legitimate later changes or use a targeted edit.

## Reference: diagnosing Windows test hangs

A Windows test that hangs before normal program output may be blocked in the
loader rather than in the test. Check these causes first:

1. A dependent DLL was installed under `lib/` instead of `bin/`.
2. A published dependency was built against a different Python DLL.
3. CMake discovered a different Python interpreter from the one selected by
   the workflow.
4. The test environment's `PATH` does not include all runtime directories.

Use `dumpbin /dependents` recursively to identify the actual missing or
mismatched DLL. Reproduce through `ctest`, because the project may attach a
test specific environment that is absent when launching the executable
directly.

If live runner access is explicitly authorized, use a Windows capable,
version pinned upterm action temporarily and remove it when diagnosis is done.
An invasive `cdb` attach should exit with `qd` to detach; plain `q` terminates
the target process. Prefer crash logs and noninvasive evidence when they are
sufficient.
