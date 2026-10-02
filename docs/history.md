# History rewrite safety

Read this before rebasing, autosquashing, reconstructing commits, or replacing
published branch history.

## Metadata rules

Filtered OpenUSD commits retain their original author and committer metadata.

Untwine-specific commits retain original author attribution, but replaying them
with rebase, autosquash, or cherry-pick should record current committer
metadata.

Do not restore or backdate old Untwine committer dates after replaying them on
top of newer OpenUSD history.

Never add AI attribution, `Co-Authored-By`, or session tracking trailers.

## Before rewriting

Record the old branch tip.

When replacing source wholesale, inspect every later commit that touches the
file. Reapply legitimate later changes or use a targeted edit instead.

Prefer an isolated reconstruction worktree when useful:

```bash
git worktree add --detach <commit>
```

If updating a worktree from a tree object, use:

```bash
git read-tree --reset -u <tree>
```

Do not use `git checkout-index -a -f` as a replacement for this operation. It
does not remove paths absent from the target tree and can leave stale files.

## Commit ownership

Keep the established history model:

* mechanical standalone source transformation -> `Restructure ...`
* build, package, CI, tests, and pins -> `Add minimal release configuration.`
* genuine source changes -> separate focused commits

Use fixup commits and autosquash when curating unpublished or explicitly
rewriteable Untwine history.

If the task requires preserving published commit identities, use new focused
commits instead of rewriting those commits.

## After rewriting

Inspect both the history and the resulting tree.

At minimum:

```bash
git diff --stat <old-tip> <new-tip>
git diff <old-tip> <new-tip>
```

A rewrite intended to alter only commit structure or metadata must have an
empty tree diff.

A rewrite that intentionally changes content should have exactly the expected
tree delta and no incidental changes.

If a later upstream change genuinely deletes a file, preserve the deletion. Do
not resurrect the file merely to satisfy an earlier cherry-pick.

## Folding changes into existing commits

Show the working-tree diff of a slice to the maintainer before folding it in.
Then:

```bash
git commit --fixup=<target>
git rebase -i --autosquash ...
```

Use a plain fixup by default and leave the target's message alone. If the
description would become actively wrong, propose the exact replacement wording
first and use `git commit --fixup=amend:<target>`, preserving Git's `amend!`
marker line (passing `-m`/`-F` is rejected, and overwriting the message file
defeats autosquash and leaves a duplicate). After rebasing, verify exactly one
commit with the target title remains.

If a correction would require *describing* content the repository does not yet
have (a missing Conan recipe, for example), add that content first as its own
reviewed slice instead of rewording.

## Commit descriptions

Match the style used across the coordinated release, not just the wording in
one repository.

`Restructure the '<lib>' library as a standalone package.` has a
semicolon-separated bullet body, one item per line (wrapped items use a
two-space continuation), the last ending in a period. Include only the bullets
that apply, in this order:

1. `Isolate the '<lib>' module from the OpenUSD repository;`
2. the `pxr.h.in` bullet:
   * no direct dependency to bridge: `Integrate a customized 'pxr.h.in' into
     the '<lib>' library, with renamed namespace and version macros bound to
     the library;`
   * direct dependencies bridged: `Add a 'pxr.h.in' customized for the
     library, with renamed namespace/version macros and internal-namespace
     imports for its dependencies;`
3. `Update include directives to use the new header prefix path;`
4. any other restructuring actually performed (moving tests, removing
   obsolete build artifacts, dropping `#!/pxrpythonsubst`, ...), stating only
   what was done
5. always last: `Add identification and licensing information mandated by
   OpenUSD.`

Never add a bullet about per-file attribution; the repository-level notice is
covered by bullet 5 and the `NOTICE.txt` paragraph in
[`source-transformations.md`](source-transformations.md).

`Add minimal release configuration.` has the same bullet format, in this
order:

1. `Configure CMake to build and test the library;`
2. `Package the library for both PyPI (runtime and development wheels via
   pyproject.toml) and Conan (recipe and release workflow publishing to the
   shared 'untwine' Cloudsmith remote), so it can be consumed from Python or from plain
   CMake/Conan projects;` only once both packaging paths exist
3. `Add GitHub CI for major platforms and a .gitignore;`
4. other fixes actually made (restoring `moduleDeps.cpp`, adding a minimal
   `testWrapper.py`, fixing test paths, linking a missing test dependency)

Bullet 4 is for a fix to something that already existed. Do not enumerate
correctness details of content introduced for the first time in the same pass
(a first Conan recipe or CI workflow); they fold silently into bullets 1-3.
It also excludes the standing checklist items from
[`validation.md`](validation.md) (install destinations, unanchored `.gitignore`
entries, the Windows `onetbb` `PATH`, `EXACT`,
version pins): finding one is baseline compliance. Reserve a bullet for
something particular to this repository that the checklist could not have
predicted.

## Pushing

Never push without explicit maintainer confirmation.

For rewritten remote branches:

```bash
git push --force-with-lease
```

Never use unconditional force push.
