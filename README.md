# Untwine

Maintainer tooling for the [Untwine](https://github.com/untwine) project.

## Project goal

Untwine makes OpenUSD's lower level libraries available as independent,
conventional packages. Each `pxr-*` repository extracts one library from the
monolithic OpenUSD source tree, preserves its relevant upstream history, and
adds the minimum structure required to build, test, and distribute it on its
own.

This allows a project to consume only the OpenUSD libraries it needs through
standard CMake packages, rather than configuring and building the complete
OpenUSD repository. C++ packages are distributed through Conan from Untwine's
public Nexus server. Libraries with Python bindings are also distributed as
two Python wheels: a runtime wheel with just what's needed to import and use
the library, and a `-dev` wheel that adds the C++ headers needed to build
against it.

The project aims to:

* preserve OpenUSD's public API and the shared `pxr` namespace
* keep the source delta from OpenUSD small, mechanical, and easy to audit
* retain filtered upstream history instead of starting each package from a
  source snapshot
* separate restructuring, release configuration, and genuine source fixes
  into reviewable commits
* test the standalone packages across supported Linux, Windows, and macOS
  platforms
* follow OpenUSD releases as a coordinated package family while keeping the
  library boundaries robust if their versions eventually drift
* keep generally useful source fixes suitable for contributing back to
  OpenUSD

Untwine is not an alternative implementation of OpenUSD and does not aim to
diverge from its behavior. It is a modular packaging and build effort for
projects that do not need the entire OpenUSD stack.

## Repository model

An extracted library normally has two long lived branches:

* `open-usd` contains the library's history filtered from OpenUSD
* `main` replays the Untwine restructuring, build configuration, packaging,
  and focused fixes on top of that history

Support repositories such as `pxr-tbb` package dependencies needed at build
time and runtime, including inside Python wheels. They are not OpenUSD subtree
extractions and do not use the same branch workflow.

The [`AGENT.md`](AGENT.md) guide describes the three maintainer workflows:

1. splitting a new OpenUSD library
2. applying a new Untwine convention to existing libraries
3. syncing existing libraries to a newer OpenUSD release

This toolbox currently provides the mechanical support for the third
workflow.

## sync-upstream.sh

Rebases a `pxr-*` repo's `open-usd` and `main` branches onto a newer
OpenUSD release tag.

```
cd ~/dev/untwine/pxr-arch
~/dev/untwine/toolbox/sync-upstream.sh pxr/base/arch v26.08
```

- `pxr/base/arch` is this library's path in the upstream OpenUSD tree.
- `v26.08` is the new tag to sync to.

The script handles the mechanical part: cloning upstream, filtering its
history down to this library's path, rebasing `open-usd` onto the new tag, and
replaying the Untwine commits one at a time on a temporary `main-sync` branch.
It deliberately stops at the first real conflict because resolving changes
between new upstream source and the standalone transformation requires review.

After it finishes, follow the sync workflow in `AGENT.md`: inspect new upstream
files, update all version pins, run the Conan build and tests, build and test
the Python wheel when applicable, then review the rewritten history before
pushing with `--force-with-lease`.
