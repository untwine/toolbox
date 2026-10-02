# Add a library

1. `./untwine add <lib>` (for example `pcp`). It locates the library in
   OpenUSD at the manifest's release, derives its direct dependencies from
   upstream's `pxr_library()` (and stops with the order to add any library
   that has no repository yet), and creates `../pxr-<lib>` with:
   * `open-usd`: the filtered upstream history;
   * `Restructure the '<lib>' library as a standalone package.`: generated
     (layout, include and namespace substitutions, `pxr.h.in`, `NOTICE.txt`,
     `LICENSE.txt`, commit message);
   * `Add minimal release configuration.`: the nearest sibling's current
     files with names and dependency lists rewritten.
   It also adds the library to `untwine.toml`. Nothing remote is touched.
2. Resolve every `TODO(untwine): review` marker by amending the config
   commit: replace the copied source, header, Python, and test lists with
   the ones listed in the marker (from upstream's `pxr_library()`), and
   reconstruct `moduleDeps.cpp` per [`python-bindings.md`](python-bindings.md).
   Review the rest against [`packaging.md`](packaging.md) and
   [`namespaces.md`](namespaces.md); record any extra direct dependency in
   `untwine.toml` `extra_deps` with the reason.
3. Build and test it through Conan and, with Python bindings, the wheels
   ([`validation.md`](validation.md)). Put each genuine fix in its own commit.
4. `./untwine verify pxr-<lib> --diff` must be clean.
5. `./untwine publish-repo pxr-<lib>` creates the public GitHub repository
   and pushes `main` and `open-usd` after confirmation. Commit the updated
   `untwine.toml`.
