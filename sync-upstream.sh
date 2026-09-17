#!/usr/bin/env bash
#
# sync-upstream.sh <subtree-path> <new-tag>
#
# Rebases a pxr-* repo's 'open-usd' and 'main' branches onto a newer
# OpenUSD release tag.
#
# Run from inside an existing clone of the untwine/pxr-* repo you want
# to sync (the one containing the 'main' and 'open-usd' branches):
#
#   cd ~/dev/untwine/pxr-arch
#   ~/dev/untwine/toolbox/sync-upstream.sh pxr/base/arch v26.08
#
# What this does NOT do: resolve conflicts while replaying 'main' onto
# the updated 'open-usd'. That step needs a human - if upstream touched
# a file this repo has also patched, that's a real decision, not
# something to script around.
#
# Safety note #1: git-filter-repo rewrites every ref it can see by
# default, not just the current branch. If it ran directly inside this
# repo, it would also mangle 'main'/'open-usd' (their paths no longer
# match the original pxr/base/<lib> layout). So the filtering step runs
# in a throwaway clone that only ever contains upstream OpenUSD
# history, and only the resulting branch gets pulled into this repo.
#
# Safety note #2: 'main' is replayed onto the new 'open-usd' via
# individual 'git cherry-pick's, not 'git rebase'. Plain rebase can
# silently DROP a commit it decides has become empty after conflict
# resolution - confirmed the hard way syncing pxr-arch to v26.08, where
# a whole commit (adding pyproject.toml, conanfile.py, CI, etc.)
# vanished with no error, only caught by checking the resulting tree by
# hand. Cherry-picking one commit at a time surfaces that as an
# explicit, visible stop instead.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 <subtree-path> <new-tag>" >&2
    echo "example: $0 pxr/base/arch v26.08" >&2
    exit 1
fi

SUBTREE_PATH="$1"
NEW_TAG="$2"

UPSTREAM_URL="git@github.com:PixarAnimationStudios/OpenUSD.git"
ONTO_BRANCH="onto-${NEW_TAG#v}"
REPO_ROOT="$(pwd)"

if ! git -C "$REPO_ROOT" rev-parse --git-dir > /dev/null 2>&1; then
    echo "error: run this from inside the untwine/pxr-* repo you want to sync" >&2
    exit 1
fi

branch_exists() {
    # Accepts a local branch, or a remote-tracking branch on 'origin' that
    # 'git checkout <name>' would auto-create a local tracking branch from.
    git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$1" \
        || git -C "$REPO_ROOT" show-ref --verify --quiet "refs/remotes/origin/$1"
}

if ! branch_exists main || ! branch_exists open-usd; then
    echo "error: expected 'main' and 'open-usd' (local or on origin) in $REPO_ROOT" >&2
    exit 1
fi

if ! command -v git-filter-repo > /dev/null 2>&1; then
    echo "error: git-filter-repo not found (pip install git-filter-repo)" >&2
    exit 1
fi

SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openusd-filter-XXXXXX")"
trap 'echo "Scratch clone left at: $SCRATCH_DIR"' EXIT

echo "==> Cloning upstream OpenUSD into an isolated scratch directory"
git clone --quiet "$UPSTREAM_URL" "$SCRATCH_DIR"

(
    cd "$SCRATCH_DIR"
    git checkout --quiet "$NEW_TAG"
    git switch -c "$ONTO_BRANCH"
    git filter-repo \
        --refs "$ONTO_BRANCH" \
        --path "$SUBTREE_PATH" \
        --path-rename "${SUBTREE_PATH}/:" \
        --force
)

echo "==> Fetching filtered '${ONTO_BRANCH}' into $REPO_ROOT"
git -C "$REPO_ROOT" fetch "$SCRATCH_DIR" "$ONTO_BRANCH:$ONTO_BRANCH"

echo "==> Rebasing 'open-usd' onto ${NEW_TAG}"
# Capture main's own commits (as a range against the OLD open-usd) before
# open-usd moves. This rebase step is a plain 'git rebase' deliberately:
# these commits are byte-identical to what's already in the freshly
# filtered history, so git recognizes them as already-applied and fast-
# forwards through them - it never actually re-diffs their content, so
# the empty-commit-drop risk this script avoids for 'main' doesn't apply
# here.
#
# Check out open-usd before resolving it as a revision: unlike checkout,
# rev-parse won't auto-create a local branch from origin/open-usd, so it
# fails here if this clone has never had open-usd checked out locally.
git -C "$REPO_ROOT" checkout open-usd
OLD_OPEN_USD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
git -C "$REPO_ROOT" rebase "$ONTO_BRANCH"

echo "==> Replaying 'main' onto the updated 'open-usd', one commit at a time"
echo "    (this is the step that may need real conflict resolution)"

# Not using 'mapfile' here: it needs bash 4+, and macOS still ships bash
# 3.2 as /bin/bash. A plain word-split is safe since these are commit
# SHAs (never contain whitespace or glob characters).
OWN_COMMITS=($(git -C "$REPO_ROOT" rev-list --reverse "${OLD_OPEN_USD}..main"))

git -C "$REPO_ROOT" checkout -B main-sync open-usd

total=${#OWN_COMMITS[@]}
for (( i=0; i<total; i++ )); do
    commit="${OWN_COMMITS[$i]}"
    subject="$(git -C "$REPO_ROOT" log -1 --format=%s "$commit")"
    echo "    -> $commit $subject"
    if ! git -C "$REPO_ROOT" cherry-pick "$commit"; then
        echo
        echo "Stopped replaying 'main' at: $commit $subject"
        echo "Resolve by hand in $REPO_ROOT, on branch 'main-sync':"
        echo "  - fix conflicts, 'git add' the files, 'git cherry-pick --continue'"
        echo "  - if it says the cherry-pick is now empty (upstream already made"
        echo "    this exact change), that's fine: 'git cherry-pick --skip'"
        echo "  - build and test after EVERY commit you resolve, not just at the end"
        if (( i + 1 < total )); then
            echo
            echo "Then continue replaying the remaining commits yourself:"
            for (( j=i+1; j<total; j++ )); do
                echo "  git cherry-pick ${OWN_COMMITS[$j]}  # $(git -C "$REPO_ROOT" log -1 --format=%s "${OWN_COMMITS[$j]}")"
            done
        fi
        echo
        echo "Once 'main-sync' looks right: git branch -f main main-sync"
        exit 1
    fi
done

git -C "$REPO_ROOT" branch -f main main-sync
git -C "$REPO_ROOT" checkout main
git -C "$REPO_ROOT" branch -d main-sync

cat <<EOF

==> Done. Next steps:
    1. Build and run the test suite.
    2. Bump the version in CMakeLists.txt / pyproject*.toml / README.md.
    3. git branch -d ${ONTO_BRANCH}   # scratch branch, safe to drop once rebased
    4. git push origin open-usd main --force-with-lease
EOF
