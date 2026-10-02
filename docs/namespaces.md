# Namespace strategy

Every standalone `pxr.h.in` defines two separate namespace concepts:

1. the shared public OpenUSD namespace
2. a versioned library-internal namespace

Keep these concepts separate.

## Shared public namespace

`PXR_NS` and `PXR_NS_GLOBAL` remain the literal public namespace `pxr` in every
repository.

Do not introduce per-library public namespaces such as `GF_NS` or `VT_NS`.

Existing OpenUSD consumers are written against names such as:

```cpp
pxr::TfToken
pxr::SdfPath
```

## Versioned internal namespace

Each library defines its own internal namespace token:

```text
<LIB>_INTERNAL_NS = pxrInternal_v<major>_<minor>_<patch>__pxrReserved__
```

Its matching:

```text
<LIB>_NAMESPACE_OPEN_SCOPE
<LIB>_NAMESPACE_CLOSE_SCOPE
<LIB>_NAMESPACE_USING_DIRECTIVE
```

must use that internal namespace.

## Bridge direct dependencies explicitly

Include each direct dependency's `pxr.h` and import its internal namespace into
the current library's internal namespace.

For example:

```cpp
#include <pxr/arch/pxr.h>

namespace TF_INTERNAL_NS {
    using namespace ARCH_INTERNAL_NS;
}
```

Place dependency bridges after the library's own public namespace bridge so the
dependency macros are already defined.

List direct dependencies only. Their own bridges carry transitive dependencies
forward.

Do not replace explicit dependency bridges with:

```cpp
using namespace PXR_NS;
```

That makes symbol visibility depend on include order and can hide a missing
dependency.

Do not assume all packages always share the same literal internal namespace.
Exact pins keep the coordinated release aligned today; explicit bridges keep
the libraries correct if package versions later drift.

## `pxr-boost`

`pxr-boost` does not expose the same public `PXR_NS` bridge as the OpenUSD
library packages.

Its `pxr_boost` namespace is nested directly inside
`BOOST_INTERNAL_NS`, defined by `<pxr/boost/python/common.hpp>`.

A library with optional Python bindings that uses unqualified
`pxr_boost::python::...` needs an explicit bridge gated on Python support:

```cpp
#ifdef PXR_PYTHON_SUPPORT_ENABLED
#include <pxr/boost/python/common.hpp>
#endif

namespace TF_INTERNAL_NS {
    using namespace ARCH_INTERNAL_NS;
#ifdef PXR_PYTHON_SUPPORT_ENABLED
    using namespace BOOST_INTERNAL_NS;
#endif
}
```

This bridge can appear unnecessary while all packages use an identical release
version because their internal namespace tokens happen to resolve to the same
physical namespace.

Do not rely on that coincidence. The bridge is required for correctness when
versions drift.

## Macros expanded in consumer code

A macro invoked by another library expands at the caller's namespace scope.

Such a macro must fully qualify every referenced public symbol with
`PXR_NS::`; the defining library's internal namespace bridge cannot help at the
caller's expansion site.

Examples include:

* `TF_ERROR`
* `TF_INSTANTIATE_SINGLETON`
* `TF_BITS_FOR_VALUES`
* `ARCH_CONSTRUCTOR`
* `ARCH_DESTRUCTOR`

Treat these qualification changes as focused source fixes after the two core
commits.

When editing a backslash-continued macro, preserve the original continuation
alignment line by line.
