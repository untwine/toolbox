# Validation

Run validation from the repository root after a split, release sync, or meaningful
packaging change.

## 1. Audit the tree

Useful searches:

```bash
rg -n '#include "pxr/' .
rg -n 'pxr/(base|external)/' .
rg -n '\bPXR_NAMESPACE_(OPEN|CLOSE)_SCOPE\b' src test
```

Exclude documentation only when it intentionally explains an old form (the
`pxr/external/...` and `pxr/base/...` hits inside upstream comments are
upstream content, not drift). Run the searches unscoped across the whole tree,
including a library's includes of its own headers.

Also check that nothing is left at the repository root outside `src/`,
`test/`, `cmake/`, `resources/`, `.github/`, and top-level configuration:

```bash
git ls-files | rg -v '^(src|test|cmake|resources|\.github)/'
```

Then verify:

* no unexpected source files remain at an old flat root
* source, headers, generated files, Python modules, and tests match the target
  upstream CMake declarations
* every touched upstream-derived source file satisfies the reconstruction rule
  from [`source-transformations.md`](source-transformations.md)
* direct dependencies agree across CMake, installed config, Conan, Python
  metadata, and `pxr.h.in`
* sibling dependency pins are exact and use the correct version form

### Standing conformance checklist

Run `untwine verify` first: it automates most of this list, and `--diff`
automates the comparison against `open-usd`. The list below remains the
source of truth for what it checks.

Every repository must satisfy all of these. Each links to its owning document.

Source ([`source-transformations.md`](source-transformations.md),
[`namespaces.md`](namespaces.md)):

* `NOTICE.txt` carries the repository-level attribution paragraph
* all `pxr/...` includes in angle-bracket form
* upstream header guards unchanged
* library-specific namespace macros, no leftover `PXR_NAMESPACE_*_SCOPE`
* `pxr.h.in` bridges exactly the direct dependencies, plus `BOOST_INTERNAL_NS`
  gated on `PXR_PYTHON_SUPPORT_ENABLED` where `pxr_boost` is used unqualified
* no `#!/pxrpythonsubst`; no dead legacy `CMakeLists.txt` hand-edited instead
  of reconstructed
* macros expanded in consumer code qualify public symbols with `PXR_NS::`

Build and packaging ([`packaging.md`](packaging.md),
[`python-bindings.md`](python-bindings.md)):

* `target_compile_features(<target> PUBLIC cxx_std_17)`; no global
  `CMAKE_CXX_STANDARD`
* `find_package(... EXACT REQUIRED)` for sibling libraries, in the build and
  the installed config
* `RUNTIME`, `LIBRARY`, `ARCHIVE` all install to `${CMAKE_INSTALL_LIBDIR}`
* `INSTALL_RPATH` handling for every target depending on another `pxr-*`
  package (Unix only, `SKBUILD` excluded for plain C++ libraries)
* `moduleDeps.cpp` checked in for libraries with a Python module
* Conan: direct `requires()` only, `cmake_find_mode` `none`, `builddirs`,
  `cmake_target_name`, `system_package_version`, `python_version` forwarded to
  every direct dependency exposing it
* `.gitignore`: `CMakeUserPresets.json` ignored, entries anchored
* `pytest-cmake` only alongside a Python-bound module
* Windows test `PATH` includes `onetbb`
* `LIBRARY_PATH_PREPEND` retained for Python tests (required on Windows)
* CI: triggers, runners, action versions, generators, `ctest` invocation and
  remote configuration as listed in `packaging.md`; no Defender step

History ([`history.md`](history.md)):

* `Restructure ...` then `Add minimal release configuration.` then focused
  fixes, with descriptions in the established style
* no AI attribution, `Co-Authored-By`, or session trailers on Untwine commits
  (filtered upstream commits keep their own trailers)

## 2. Validate through Conan

The repository's Conan workflow is the canonical C++ validation path.

It must configure, build, package, and test the library against packaged
dependencies.

An ad hoc CMake build is useful during diagnosis, but it does not replace the
Conan package path.

During diagnosis, prefer a keep-going build when useful:

```bash
cmake --build . -- -k
```

Run:

* native tests
* Conan package creation
* package consumption tests
* relevant workflow variants

A minimal downstream CMake project should be able to consume the installed
package successfully.

The monolithic OpenUSD build is not a substitute. It can hide missing includes,
libraries, namespace imports, and runtime artifacts that are absent in the
standalone package.

## 3. Validate Python packages

For repositories with Python bindings, follow
[`python-bindings.md`](python-bindings.md).

The wheel workflow must build for every supported Python version, and each
built wheel must install and import successfully in a clean environment.

Testing only from the source or build tree is insufficient.

## 4. Validate the CI matrix

Where supported, verify coverage for:

* Linux
* Windows
* Intel macOS
* Apple silicon

Also verify that the Conan and wheel workflows use compatible Python versions
for matching binaries.

## 5. Diagnose broad Python failures as ABI problems first

When many Python tests fail together, first check whether a dependency was
built against a different Python ABI.

Inspect linked libraries with:

* macOS: `otool -L`
* Linux: `ldd`
* Windows: `dumpbin /dependents`

For Windows hangs or loader failures, continue with
[`windows-debugging.md`](windows-debugging.md).

## 6. Review the final delta

For source transformations, compare the standalone tree with the matching
`open-usd` source using the known path mapping and account for every difference.

For history rewrites, also follow [`history.md`](history.md).

Do not declare success until the relevant standalone package and consumption
paths pass.
