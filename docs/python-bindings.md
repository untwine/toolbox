# Python bindings and wheels

Use this document for repositories that build or package Python bindings.

## Explicit `moduleDeps.cpp`

OpenUSD commit
`7437d0c5f07b9eebbd179242633fa1995295b819` replaced checked-in
`moduleDeps.cpp` files with build-time generation through
`genModuleDepsCpp.cmake`.

Untwine keeps `moduleDeps.cpp` checked in and reviewable for libraries that
ship a Python module.

Do not restore an arbitrary old deleted copy. Reconstruct the file from the
rules at the target OpenUSD release.

For the target tag:

1. read the library's `pxr_library()` declaration
2. read `Public.cmake`
3. read `genModuleDepsCpp.cmake`
4. read `moduleDeps.cpp.in`
5. set `libraryName` to the CMake target name
6. set `moduleName` to `_get_python_module_name()`'s output
7. set `reqs` to `LIBRARIES` entries that resolve to OpenUSD targets, including
   `python` when the generator would, in CMake order
8. apply the normal standalone include and namespace substitutions
9. compile/register the file under the same Python-enabled condition as the
   bindings

The checked-in result should match what the target OpenUSD generator would have
emitted.

Treat this as a reconstruction of OpenUSD's current build rules, not a
permanent contract. Revisit it if those rules change.

Skip `moduleDeps.cpp` for libraries without a Python module.

## Python-aware Conan packages

When the C++ package builds Python bindings:

* pass the interpreter running Conan to CMake using `sys.executable`
* normalize Windows backslashes to forward slashes before writing the CMake
  toolchain value
* represent the Python minor version as a Conan option so it participates in
  `package_id`
* validate that the requested option matches the interpreter running Conan
* for optional bindings, allow `None`, omit Python-specific dependencies when
  disabled, and forward the option one dependency edge at a time
* publish a workflow matrix for each supported Python version
* include a no-bindings variant when the library supports one

Keep supported Python versions aligned between the Conan and wheel workflows.

## Python extension modules need `INSTALL_RPATH`, not `LIBRARY_PATH_PREPEND`

A CI failure of the shape "extension module built fine, fails to import/load
a dependency's shared library" on a Conan-based build looks at first like a
`pytest_discover_tests` (`pytest-cmake`) `LIBRARY_PATH_PREPEND` gap — each
Conan dependency lives in its own separate package folder rather than one
shared install prefix, so the dynamic loader has no ambient way to find one
unless something tells it where to look.

Enumerating every transitive dependency's directory into
`LIBRARY_PATH_PREPEND` (checking with `otool -L`/`ldd`/`dumpbin /dependents`
on the built extension module to find what's actually missing) does fix the
symptom, but treat it as a stopgap, not the destination. It only patches the
one build-tree ctest invocation; the same extension module, once packaged and
consumed anywhere else (a downstream project's tests, a plain `import`
outside of ctest), fails again the same way, because the real defect lives in
the packaged binary itself, not in the test harness. See the "RPATH for any
target with a cross-package dependency" section in `packaging.md` for the
root-cause fix: the Python extension module's own `CMakeLists.txt` needs
`INSTALL_RPATH_USE_LINK_PATH` (plus a `file(RELATIVE_PATH ...)`-computed
entry back to its sibling C++ library, since that one is same-project rather
than external) so the shipped `.so` is self-sufficient regardless of who
loads it. Once that is in place, `LIBRARY_PATH_PREPEND` for the
library-loading half of the problem becomes unnecessary **on macOS and
Linux only** — only `PYTHON_PATH_PREPEND` (a `PYTHONPATH`/module-discovery
concern, unrelated to `@rpath`/`RPATH`) still applies there.

**Windows has no rpath equivalent at all**, so none of the above applies to
it. `INSTALL_RPATH`/`INSTALL_RPATH_USE_LINK_PATH` are POSIX-only CMake
properties (guard them `if(NOT WIN32)`; they're silently inert on Windows,
not an error, which makes it easy to assume they cover it when they don't).
A `.pyd`'s dependent-DLL search happens via the process's `PATH` and a small
set of OS-defined directories — there is no per-binary embedded search path
a build step can bake in for an arbitrary transitive dependency. Removing
`LIBRARY_PATH_PREPEND` after fixing rpath on macOS/Linux, and validating
only on those platforms, silently breaks Windows: it was never covered by
the rpath fix and had no other mechanism supplying it. The
`if(WIN32)` `add_test()` override using `$<TARGET_RUNTIME_DLL_DIRS:${_NAME}>`
does not help here either — it only works when `${_NAME}` is a real CMake
target, which the individual test names `pytest_discover_tests` generates
are not. `LIBRARY_PATH_PREPEND` (which pytest-cmake maps onto `PATH` on
Windows, `DYLD_LIBRARY_PATH` on macOS, `LD_LIBRARY_PATH` on Linux) remains
permanently required on Windows for the Python test path — keep it even
after fixing rpath elsewhere, and validate any removal separately on every
platform in CI, not by reasoning from one platform's success.

A frequent trap while fixing the rpath half: the existing CMakeLists code
may already set `INSTALL_RPATH`/`INSTALL_RPATH_USE_LINK_PATH`, but only
inside an `if(SKBUILD ...)` block for the wheel-build path. That leaves the
plain Conan/CMake install path with zero rpath — the packaged extension
module ships with correct `@rpath` dependency entries and nothing to
resolve them with. Verify which install paths a conditional actually covers
before assuming the target already handles this.

## Wheel validation

A Python-enabled repository is not validated merely because bindings import
from the build tree.

For every supported Python version:

1. build the wheel through the wheel workflow
2. install the produced wheel into a clean environment
3. import the packaged module
4. run the relevant packaged Python tests

Where the project publishes a runtime wheel and a `-dev` wheel, validate the
intended consumption shape of both.
