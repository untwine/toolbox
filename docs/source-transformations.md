# Source transformations

Use this reference whenever editing source derived from OpenUSD.

The goal is not merely to make the standalone repository build. The standalone
tree should remain mechanically explainable relative to its matching
`open-usd` source.

## Reconstruction rule

A standalone file should equal:

```text
matching open-usd file
+ required standalone transformations
+ separately justified later fixes
```

When an existing file has accumulated unexplained drift, reconstruct it from
the matching `open-usd` version and reapply only the required transformations
and intentional fixes.

Before replacing a file wholesale, inspect later commits that touched it so a
legitimate fix is not accidentally erased.

## Standard standalone transformations

Depending on the file and library:

* move source and headers into `src/pxr/<lib>/`
* move tests formerly under `testenv/` into `test/`
* move Python binding glue into `src/python/`
* rewrite old monolithic include paths to the installed standalone prefix
* use `<angle-bracket>` form for `pxr/...` includes
* replace monolithic namespace scope macros with the library-specific namespace
  macros defined by its standalone `pxr.h.in`
* add the freestanding `src/pxr/<lib>/pxr.h.in`
* remove obsolete monolithic build artifacts
* remove empty single-include `.cpp` stubs made unnecessary by a header-only
  conversion
* remove `#!/pxrpythonsubst` and its single `#` spacer when present
* preserve real shebangs such as `#!/usr/bin/env python`
* add repository-level attribution in `NOTICE.txt`, not per-file comments

Apply include substitutions to every relevant file type, including uncommon
extensions such as `.cc`, `.dox`, templates, generated inputs, and vendored
patches. Do not rely on a narrow extension allowlist.

## Legacy build files carried by rename

A `git mv` of the old monolithic tree sometimes carries forward a file the
standalone build never reads again, most commonly the old `pxr_library(...)`
`CMakeLists.txt` superseded by the files `Add minimal release configuration.`
introduces (top-level `CMakeLists.txt`, `src/CMakeLists.txt`,
`src/pxr/<lib>/CMakeLists.txt` generated fresh for the standalone build).

Before mechanically rewriting such a file's internal relative paths (source
lists, test paths) to match the new tree layout, confirm the standalone build
actually consumes it. If nothing includes or references it, reconstruct it
byte-for-byte from `open-usd`, unmodified apart from its new location, instead
of hand-adjusting paths inside a file nothing reads. Path drift in a dead file
is indistinguishable from a real fix until someone checks whether the file is
live, so leaving it identical to upstream removes the ambiguity.

## Preserve upstream content that is not a standalone transformation

Do not introduce:

* cosmetic formatting
* spelling cleanup
* unrelated stylistic rewrites
* arbitrary header-guard changes
* source behavior changes inside the mechanical restructuring commit

Preserve upstream copyright and license headers exactly.

Do not add `Modified by ...`, `Updated by ...`, or similar comments to source
files. The repository-level notice should be:

```text
This repository is a modified, standalone redistribution of the '<lib>'
library from Pixar's OpenUSD, restructured for independent building and
packaging. See the git history for details of the changes made.
```

## Header guards and namespace macros are different rules

Header include guards are not part of the namespace conversion.

Keep upstream guards such as:

```cpp
#ifndef PXR_BASE_<LIB>_<NAME>_H
```

exactly as upstream has them.

Only the per-library namespace-scope macros and the freestanding `pxr.h.in`
use the flattened library-specific form.

Do not infer that a renamed guard is correct merely because an older Untwine
repository already contains it. Existing repository state can itself contain
unreviewed drift.

## Verify the whole file after mechanical edits

A narrow script proves only that the pattern it targeted changed.

After a mechanical edit, diff every touched source file against the matching
`open-usd` version and account for every remaining difference.

Known drift patterns to check explicitly:

* `#include "pxr/...">` or `#include "pxr/..."` remains instead of
  `<pxr/...>` — this includes a library's includes of its own headers
  (`"pxr/vt/array.h"` inside another `vt` file, for example), not only
  includes of other libraries. Do not assume a same-library include is
  exempt; run the whole-tree `rg` search below with no path restriction and
  check every hit, not just the files a narrower search (such as one scoped
  to files carrying old attribution comments) already flagged.
* a blank line is added or removed immediately after the license header
* an upstream `PXR_BASE_<LIB>_..._H` header guard is renamed
* `PXR_NAMESPACE_OPEN_SCOPE` or `PXR_NAMESPACE_CLOSE_SCOPE` remains where the
  library-specific namespace macro is required
* `NOTICE.txt` is missing the repository-level attribution paragraph (see
  above)
* a later intentional fix disappears during reconstruction

License-header spacing differs by file kind. Header guards have no extra blank
line before the guard; `.cpp` sources and Python modules should preserve the
spacing present upstream.

When the same transformation affects many files, script both the edit and the
verification. Do not spot-check a few files and generalize.

Useful whole-tree searches include:

```bash
rg -n '#include "pxr/' .
rg -n 'pxr/(base|external)/' .
rg -n '\bPXR_NAMESPACE_(OPEN|CLOSE)_SCOPE\b' src test
```

Use exclusions only for documentation that intentionally describes an old
form.
