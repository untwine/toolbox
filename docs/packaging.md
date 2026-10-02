# Build and packaging conventions

This document covers the stable CMake, Conan, remote-hosting, and CI conventions shared
by the standalone `pxr-*` packages.

Declare direct dependencies only. Keep their declarations consistent across
CMake, installed config, Conan, Python metadata, and `pxr.h.in`.

## CMake

* Express the language floor on the target:

  ```cmake
  target_compile_features(<target> PUBLIC cxx_std_17)
  ```

* Do not rely on a guarded global `CMAKE_CXX_STANDARD`. Conan toolchains may
  define it before the project is evaluated.

* Use `find_package(... EXACT REQUIRED)` for direct sibling libraries in both
  the build and installed package config.

* Install `RUNTIME`, `LIBRARY`, and `ARCHIVE` all to `${CMAKE_INSTALL_LIBDIR}`,
  on every platform including Windows. This matches upstream OpenUSD's own
  `pxr_library()` macro (`cmake/macros/Private.cmake`'s `libInstallPrefix`,
  which resolves to the literal `lib` unconditionally); OpenUSD does not
  split a Windows DLL into a separate `bin/`. Do not introduce that split
  here either: nothing in this build — resource-file installs (`plugInfo.json`
  and friends), RPATH computation, wheel packaging — expects one, and adding
  it desyncs any file that must be co-located with the library (a
  `plugInfo.json` install rule left pointing at `CMAKE_INSTALL_LIBDIR` while
  the target itself moves to `CMAKE_INSTALL_BINDIR`, for example) on Windows
  specifically, since `CMAKE_INSTALL_LIBDIR` and the actual DLL location then
  silently diverge.

* Wheel builds that collect DLLs under a package `.libs` directory set
  `CMAKE_INSTALL_LIBDIR` to that destination; there is no separate
  `CMAKE_INSTALL_BINDIR` to set.

## RPATH for any target with a cross-package dependency

Any installed target — a Python extension module (`pyTf`, `pyGf`, ...) **or**
the plain C++ library it wraps (`tf`, `gf`, ...) — that links, even
transitively, against a *different* `pxr-*` Conan package needs its own
`INSTALL_RPATH` handling. Each Conan package installs into its own separate
cache folder, so nothing puts a dependency's shared library on the dynamic
loader's default search path. A target whose dependencies are all system
libraries or frameworks (no other `pxr-*` package — this is why `arch` and
`boost-python` don't need it) is exempt.

This entire section is Unix-only (`RPATH`/`RUNPATH` on Linux, `LC_RPATH` on
macOS). Windows has no equivalent per-binary embedded search path mechanism
at all — dependent-DLL resolution goes through the process's `PATH` and a
handful of OS-defined directories, full stop. None of `INSTALL_RPATH`/
`INSTALL_RPATH_USE_LINK_PATH` do anything there; guard every use `if(NOT
WIN32)`. See "Windows has no rpath equivalent at all" in
`python-bindings.md` for what Windows needs instead, and validate any Unix
rpath fix on all three platforms in CI before assuming it also covers
Windows — it structurally cannot.

Apply this to *every* affected target, including intermediate C++ libraries,
even though only the Python extension module is ever imported directly. A
plain library used only as a link-time dependency can still need its own
correct rpath: on Linux, `DT_RUNPATH` set on an object is used only to
resolve *that object's own* `DT_NEEDED` entries — it is not inherited by
objects loaded transitively through it. If `_tf.so`'s `NEEDED` list has
`libPxrTf.so` before `libtbb.so.12` (matching CMake's own link order: `tf`
linked before the TBB dependency it pulls in transitively), the loader can
recurse into resolving `libPxrTf.so`'s *own* need for `libtbb.so.12` using
`libPxrTf.so`'s *own* (possibly empty) `RUNPATH` before it ever reaches
`_tf.so`'s later, correctly-rpathed entry for the same library — and fail
right there, even though `_tf.so` itself has the correct rpath. This was
tried, reverted based on macOS-only testing (which didn't reproduce the
failure — dyld's resolution order or already-loaded-image deduplication
apparently avoids hitting it there), and had to be reapplied after real
Linux CI failed with exactly this `ImportError: libtbb.so.12: cannot open
shared object file`. Do not trust a fix in this area from macOS testing
alone; validate on Linux CI before considering it settled.

The default CMake behavior only auto-embeds full absolute-path RPATH entries
for **build-tree** binaries (`CMAKE_SKIP_BUILD_RPATH` off by default). Once a
target is *installed* — which is exactly what `cmake.install()` does during
`conan create`/`package()` — `INSTALL_RPATH` governs instead, and it is empty
by default. A packaged extension module can therefore carry correct `@rpath`
dependency entries (the linker did its job) while shipping with nowhere to
resolve them, silently relying on whatever `DYLD_LIBRARY_PATH`/
`LD_LIBRARY_PATH` a consumer happens to supply. This is easy to miss because
build-tree tests (ctest run before `cmake --install`) never exercise it.

Fix pattern for a Python extension module (nested under
`lib/python*/site-packages/pxr/<Lib>/`, needs a path back to its sibling C++
library):

```cmake
if(NOT WIN32)
    set(_rpath_prefix "$ORIGIN")
    if(APPLE)
        set(_rpath_prefix "@loader_path")
    endif()

    if(SKBUILD)
        set(_tgt_rpath "${_rpath_prefix}/../.libs")
    else()
        file(RELATIVE_PATH _tgt_libdir_rel
            "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_PYTHON_LIBDIR}/pxr/<Lib>"
            "${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}"
        )
        set(_tgt_rpath "${_rpath_prefix}/${_tgt_libdir_rel}")
    endif()

    set_target_properties(<pyLib> PROPERTIES
        INSTALL_RPATH "${_tgt_rpath}"
        INSTALL_RPATH_USE_LINK_PATH TRUE
    )
endif()
```

`INSTALL_RPATH_USE_LINK_PATH` alone correctly adds absolute paths for
*external* (imported, out-of-project) dependencies — the other Conan
packages — but does not add a path back to the *same-project* C++ library
(e.g. `tf`) installed to a different relative location (`lib/` vs. the
extension module's `lib/python*/site-packages/pxr/<Lib>/`). Compute that
explicitly via `file(RELATIVE_PATH ...)` rather than hardcoding `../..`, so
it survives layout changes.

For the plain C++ library target itself (`tf`, `gf`), scope the fix to
exclude `SKBUILD` entirely, rather than leaving it on unconditionally:

```cmake
if(NOT WIN32 AND NOT SKBUILD)
    set_target_properties(<lib> PROPERTIES INSTALL_RPATH_USE_LINK_PATH TRUE)
endif()
```

`tf`/`gf` had *zero* rpath handling at all before this fix, in either the
Conan or the wheel path, and wheels already worked correctly under that
code — so a `SKBUILD`-specific override on `tf`/`gf` themselves (analogous
to `pyTf`'s) was tried and removed again as unvalidated, speculative code.
The reasoning in the note above (flattening + no rpath chaining) explains
why it's unneeded: the extension module (`pyTf`, always the actual thing a
consumer loads, in *both* the wheel and the Conan case) already carries
every dependency directly on its own `NEEDED`/`LC_LOAD_DYLIB` list, resolved
via its own rpath — `libPxrTf.dylib`'s own rpath is never consulted for the
wheel path either, matching the observed fact that wheels never needed it.

Explicitly exclude `SKBUILD` rather than leaving the property on
unconditionally: `INSTALL_RPATH_USE_LINK_PATH` computes its paths from the
*build-time* link line, which in a wheel build point at a `-dev` build-only
package's temporary location, not the separately pip-installed runtime
package's final location. Since nothing in the wheel path ever consults
`tf`/`gf`'s own rpath, those entries would be strictly dead — but "inert"
isn't the same as "no reason not to leave it out." Baking unused,
build-machine-specific absolute paths into a shipped wheel binary is an
avoidable wart (it leaks CI directory structure into a public artifact for
zero benefit), so scope the property to the path that actually needs it and
verify with `otool -l`/`readelf -d` that the wheel-built binary's rpath is
completely empty, matching its pre-fix behavior exactly — not just checking
that the *right* entries are present, but that no *extra* ones snuck in.

Fixing a Python extension module's (or a plain library's) own rpath does
**not** make an arbitrary downstream C++ consumer of the underlying library
work automatically — a project linking against `pxr::tf` gets
`libPxrArch.dylib` flattened onto *its own* link line too, and needs
`INSTALL_RPATH_USE_LINK_PATH` on *its own* target to resolve it.

Verify claims like this with `otool -L`/`otool -l` (`ldd`/`readelf -d` on
Linux) on the actual built artifact, not by reasoning about CMake in the
abstract — the per-binary, non-chaining resolution behavior here is easy to
get wrong by assumption.

## Conan

`.gitignore` needs a `CMakeUserPresets.json` entry — `conan install`/`conan
build` generate it in the source directory, and it must not be committed.

`requires()` lists direct dependencies only.

When a recipe exposes a `python_version` option, `configure()` must forward it
to every direct dependency that itself exposes a `python_version` option — not
just whichever subset happened to need it in whatever sibling recipe was used
as a starting template. Check each direct dependency's own `conanfile.py`
`options` dict rather than assuming; a dependency with no Python bindings (for
example `pxr-arch`) has no `python_version` option and needs no propagation
line. Copying a template's `configure()` verbatim silently drops the forward
for any direct dependency the template repo didn't itself depend on — e.g.
adapting `pxr-trace`'s `conanfile.py` (whose own deps are `pxr-tf`/`pxr-js`/
`pxr-boost`) for a repo that depends on `pxr-trace` itself requires *adding* a
`self.options["pxr-trace"].python_version = ...` line that the template never
had.

The Conan package version may use `26.8` while CMake reports `0.26.8`.
Set `system_package_version` in `package_info()` so generated package metadata
satisfies exact CMake version checks.

Every `pxr-*` recipe should:

```python
self.cpp_info.set_property("cmake_find_mode", "none")
```

and expose the installed CMake config directory through `builddirs`, typically:

```text
share/cmake/pxr-<lib>
```

This intentionally makes CMake use the library's authored installed
`pxr-<lib>-config.cmake` instead of a Conan-synthesized package config.

Still define `cmake_target_name` correctly in `package_info()`.

Conan uses that property while synthesizing dependency relationships for
consumers even when `cmake_find_mode` prevents it from generating this
package's own find files. If the property is omitted, Conan may guess a target
name that does not match the target exported by the real CMake config.

This matters especially for libraries exporting more than one target, such as
a C++ target plus a Python binding target.

## Why use the real installed CMake config

`CMakeDeps` builds a synthetic dependency description from the information in
`package_info()`. It does not inspect the package's real `install(EXPORT ...)`
output.

The synthetic description can therefore:

* omit exported targets
* guess incorrect target names
* hide failures until a downstream package consumes the dependency transitively

The authored config already contains the targets and names produced by the
library's own build, so it remains the canonical CMake package description.

## Python sdist contents follow `.gitignore`

`scikit-build-core`'s default sdist mode reads `.gitignore` (and
`.git/info/exclude`) to decide what to include, the same as most Python build
backends. This is invisible for source that was authored in place and
committed, but it silently drops any file that happens to match a pattern
there even when the file is genuinely present on disk at build time.

This matters most for a repository (`pxr-tbb`) that vendors third-party
source into the working tree at CI time (`.github/actions/oneTBB` clones
oneTBB and copies its contents in) rather than committing it. The generic
Python `.gitignore` template's unanchored `env/` pattern, meant to ignore a
local development virtualenv, also matched oneTBB's own
`integration/{linux,mac,windows}/env/` directories anywhere in the tree,
silently excluding them from the published sdist. This built and passed
everywhere until a platform's build actually needed the missing file.

When a repository's `.gitignore` uses the generic Python template
unmodified, anchor every build and environment directory entry to the repo
root (`/env/`, `/build/`, `/dist/`, `/lib/`, ..., not `env/`) so it cannot match
a nested directory it was never meant to ignore. Leave patterns that are meant
to match anywhere unanchored (`__pycache__/`, `*.egg-info/`, `.pytest_cache/`).
Verify with the actual file selection a build backend uses, not by reasoning
about `.gitignore` patterns in the abstract:

```python
from pathlib import Path
from scikit_build_core.build._file_processor import each_unignored_file
files = list(each_unignored_file(Path("."), mode="default"))
```

## Package remote

Every workflow that resolves Untwine packages must configure the public
`untwine` Conan read remote on Cloudsmith
(`https://conan.cloudsmith.io/untwine/conan/`), hosted under Cloudsmith's
[open-source hosting policy](https://docs.cloudsmith.com/resources/open-source-hosting-policy).
Keep the URL identical across all workflows.

Authenticate only for operations that require it, especially release uploads.
Never embed credentials in the repository.

Publishing the same package version again creates another Conan recipe or
package revision on the remote. Verify the uploaded revision rather than assuming a rerun
replaced the previous artifact.

## CI

The current convention, as of the latest coordinated release:

* `linux.yml`, `macos.yml`, `windows.yml` (per-platform test workflows) and
  `pypi.yml` (wheel/sdist build and publish) trigger on `push` to
  `[ dev, main ]` plus `workflow_dispatch`. `conan.yml` (package build and
  publish) triggers on `workflow_dispatch` only. None of them carry a
  `schedule:` trigger; push and manual dispatch are the only triggers. A
  workflow file existing does not by itself publish anything — `pypi.yml`
  and `conan.yml` only act when manually dispatched.
* Matrix keys for the per-platform test workflows: `linux-intel` /
  `linux-arm` on runners `ubuntu-latest` / `ubuntu-24.04-arm`; `macos-intel`
  / `macos-arm` on `macos-15-intel` / `macos-latest`. Windows is a single
  `windows-latest` job with no matrix. `pypi.yml`'s wheel matrix adds
  `windows` (`windows-latest`) to that same set, plus `archs: auto64` by
  default and `archs: auto64,universal2` on `macos-arm`.
* Every job starts with `actions/checkout@v7`. Use `actions/setup-python@v7`
  for a Python setup step and `pypa/gh-action-pypi-publish@release/v1` for
  the PyPI publish step; keep other action major versions current with
  whatever the newest already-validated workflow in the coordinated release
  uses, since these move independently of any single repository's release.
* Add `actions/setup-python` to a test-workflow job only when that job
  actually needs Python at run time — building optional Python bindings, or
  running a Python-driven test wrapper/comparison script — never by default.
  A repository with neither has no Python step in its test workflows.
* Resolve dependencies through Conan, not a custom composite action:

  ```yaml
  - name: Set up Conan
    shell: bash
    run: |
      pip install conan
      conan profile detect
  ```

  Add a remote and the matching job-level `env`:

  ```yaml
  env:
    CONAN_REMOTE_NAME: untwine
    CONAN_REMOTE_URL: https://packages.untwine-openusd.dev/repository/conan/
  ```

  ```yaml
      conan remote add "$CONAN_REMOTE_NAME" "$CONAN_REMOTE_URL" --force
  ```

  only when the package has at least one direct `pxr-*` dependency that must
  be resolved from the shared Cloudsmith remote. A leaf package with none needs
  neither the remote-add step nor the env block.
* Build with `conan build .`, not a hand-rolled configure/build pair:

  ```yaml
  - name: Build
    shell: bash
    run: >
      conan build .
      -o build_tests=True
      --options=*:shared=True
      -c tools.cmake.cmaketoolchain:generator=Ninja
      --build=missing
  ```

  Linux and macOS install Ninja first (`apt install -y ninja-build` /
  `brew install ninja`) and use that generator. Windows skips the Ninja
  install and uses `-c tools.cmake.cmaketoolchain:generator="Visual Studio 18
  2026"` instead, and adds `-o precompiled_headers=True`. Pass any other
  package-specific Conan option (a Python-version option, for example) as a
  further `-o` the same way. Test with
  `ctest --preset conan-release -VV --timeout 3600` (`-j4` too, once the
  package builds more than a couple of test binaries), not a manually
  invoked `ctest` from a hand-created build directory.
* Do not add a Windows Defender exclusion step. It reads like a plausible
  fix for a Windows CI stall and has been added speculatively more than
  once, but it has never actually been confirmed to fix a real hang across
  this codebase — the real cause, every time it's been run down, was
  something else entirely (see "Diagnosing Windows test hangs" in
  `windows-debugging.md`). Remove it if you find it already present in a
  repository rather than treating it as established convention.

* Delete any leftover custom composite action (a hand-rolled dependency
  builder, for example) once Conan replaces what it did. The one exception
  is a repository that vendors third-party source into the working tree at
  CI time instead of committing it — that still needs its own action.

Verify rather than blindly copy, since this list can drift out of date
between coordinated releases:

* manual dispatch is available where expected
* action major versions match current canonical repositories
* matrix keys and `include` keys use identical casing
* current GitHub runner labels are used
* current Visual Studio generators are used
* release and test workflows select the same Python version for the same binary
* every workflow configures every external package remote it consumes

Do not treat copied workflow YAML as correct merely because it parses.

## `pytest-cmake` is for discovering a Python-bound module's own tests

`find_package(Pytest)` plus a `pytest_discover_tests(...)` call in
`test/CMakeLists.txt` exists specifically to run a library's *Python-bound
extension module's* tests (`testPyGf`, for example) through `pytest`, wired
in only inside the `if(BUILD_PYTHON_BINDINGS)` branch of the target's own
`test/CMakeLists.txt`.

It is not a general-purpose CMake test runner. A repository with no Python
bindings module has nothing for `pytest_discover_tests` to discover, and
adding `find_package(Pytest)` (and the CI's `pip install ... pytest-cmake`)
for it would be dead infrastructure. Do not add it merely because a sibling
repository has it; add it only alongside that repository's own Python-bound
module and its own `pytest_discover_tests` call.

A repository whose plain C++ tests need baseline/diff comparison (not a
Python-bound module) uses a small Python test-wrapper script invoked
directly from `add_test(...)`, independent of `pytest-cmake`. Porting that
pattern to `pytest_discover_tests` instead is unproven in this ecosystem —
treat it as a new pattern to validate, not an established convention to copy.

## Test harness

### Windows `PATH` for tests must include `onetbb`

If `test/CMakeLists.txt` has a `WIN32`-only `add_test()` override computing a
test's `PATH` from `$<TARGET_RUNTIME_DLL_DIRS:...>`, that list must also
include `onetbb`'s directories explicitly (`"${onetbb_LIB_DIRS_RELEASE}"`,
`"${onetbb_BIN_DIRS_RELEASE}"`) whenever the target links `pxr::tf` or anything
else pulling in `onetbb` transitively.

`TARGET_RUNTIME_DLL_DIRS` does not include a Conan-imported transitive
dependency's directory, so `PxrTf.dll`'s dependency on `tbb12.dll` fails to
resolve. On a headless runner that hangs for the full `ctest --timeout`
(see [`windows-debugging.md`](windows-debugging.md)), and only some tests are
affected, which makes it easy to misdiagnose (a Defender scan, stdin handling,
and parser code were all tried and ruled out once).

Keep the macro and extend the `PATH` it computes. Do not delete it in favor of
the Conan-generated preset environment: that worked in a manual reproduction
but not in the real automated `ctest --preset conan-release` run.

## Republishing a broken release

Decide by whether *this specific fix* must go out now because the published
version is broken for some consumer, not by whether the package was ever
published. A dormant or preventive fix still belongs in its owning commit.

When the published version is actually broken:

1. PyPI never replaces the file of a published version. Append a trailing
   numeric segment: `2023.1.0` -> `2023.1.0.1`.
2. Check every sibling for a pin on the fixed package. A wildcard on the
   changed segment (`pxr-tbb-dev==2023.1.0.*`) already accepts the bump; an
   exact pin must be updated alongside the fix.
3. Land the fix as a new focused commit. Do not fold it into the existing
   `Add minimal release configuration.` commit, whose content corresponds to
   the version marked broken.

Do not push, and do not dispatch a release workflow, without explicit
confirmation.
