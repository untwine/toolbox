# Sync to a newer OpenUSD release

1. `./untwine sync v26.11`. Preflight refuses dirty clones or clones whose
   `main`/`open-usd` differ from `origin`. Every repository is synced in
   `.untwine/work/v26.11/<repo>`; your clones are not touched.
2. `./untwine status` and `./untwine status <repo>` for each repository that
   is not `verified`:
   * **Stopped conflict.** Fix the listed files in the worktree using
     [`source-transformations.md`](source-transformations.md): rebuild the
     file from the new upstream content plus the standard transformations,
     then reapply the intentional fix. `git add` them, then
     `./untwine resolve <repo>`.
   * **Dependency change.** Update `untwine.toml` and the repository's CMake,
     installed config, Conan, `pxr.h.in`, and pyproject together (see
     [`namespaces.md`](namespaces.md)), as `git commit --fixup=<owning commit>`
     in the worktree, then rerun `./untwine sync v26.11 <repo>`.
   * **Verify failure.** Fix it in the worktree with
     `git commit --fixup=<owning commit>` and rerun sync.
   Edit the sync branch only with fixup commits; finalize folds them and
   refuses any other change to the commit structure.
3. Review the "For review" items: register new upstream files in CMake
   (fixup of `Add minimal release configuration.`), confirm empty commits.
4. `./untwine push-prs`. Review each PR: the commit mapping, the range diff
   of Untwine commits, and the version changes. CI runs on the exact commits
   that `main` will become.
5. CI for a repository with `pxr-*` dependencies can only pass once those
   dependencies are published at the new version. Promote level by level:
   `./untwine promote`, publish the promoted level yourself (`conan.yml`,
   then `pypi.yml` once you are sure: PyPI never lets a version be replaced),
   re-run CI on the next level's PRs, promote again.
6. When the last repository is promoted, commit `releases/v26.11.md` and the
   updated `untwine.toml`.
