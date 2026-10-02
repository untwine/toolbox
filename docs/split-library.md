# Add a library

`untwine add` is not built yet (phase 2 of the CLI design). Until then:

1. Add the repository to `untwine.toml` with its upstream path, Python shape,
   and deps. Derive deps from upstream's `pxr_library(LIBRARIES ...)` (plus
   `boost` when the library has Python bindings) and record any extra direct
   dependency in `extra_deps` with the reason.
2. Create `open-usd` by filtering the subtree at the current release in a
   throwaway clone (`git filter-repo --path <subtree> --path-rename
   <subtree>/:`), never inside an existing repository.
3. Create `Restructure the '<lib>' library as a standalone package.` with the
   transformations in [`source-transformations.md`](source-transformations.md)
   and the commit message style in [`history.md`](history.md).
4. Create `Add minimal release configuration.` by adapting the nearest
   sibling with the same dependency and Python shape, following
   [`packaging.md`](packaging.md), [`namespaces.md`](namespaces.md), and
   [`python-bindings.md`](python-bindings.md).
5. Put each genuine fix in its own commit after the two core commits.
6. `./untwine verify <repo> --diff` and [`validation.md`](validation.md).
