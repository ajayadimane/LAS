#!/usr/bin/env bash
# Build las-deploy-<commit>.tar.gz for the demo server (deploy/DEPLOYMENT.md).
#
#   scripts/package_demo.sh            # needs a clean working tree
#   scripts/package_demo.sh --dirty    # package HEAD anyway (uncommitted changes are NOT included)
#   scripts/package_demo.sh --no-install   # build with the node_modules already there (no npm ci;
#                                          # e.g. when the registry is unreachable)
#
# Contents: the committed tree at HEAD (git archive: no .env, no venvs, no
# node_modules) plus frontend/dist, built here with the demo's fixed paths
# (/las for the UI, /las-api for the API). LAS_Email/ is inside it.
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

dirty=false
install=true
for arg in "$@"; do
    case "$arg" in
        --dirty) dirty=true ;;
        --no-install) install=false ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [[ "$dirty" == false ]] && [[ -n "$(git status --porcelain)" ]]; then
    echo "Uncommitted changes: commit them first (the tarball is built from HEAD)," >&2
    echo "or pass --dirty to package HEAD without them." >&2
    exit 1
fi

rev=$(git rev-parse --short HEAD)
out="$root/las-deploy-$rev.tar.gz"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

echo "Building the frontend (VITE_BASE_PATH=/las, VITE_API_BASE_PATH=/las-api)..."
(
    cd frontend
    if [[ "$install" == true ]]; then
        npm ci --no-audit --no-fund
    fi
    # Set here so frontend/.env cannot change them: both are baked into the build.
    VITE_BASE_PATH=/las VITE_API_BASE_PATH=/las-api npm run build
)

mkdir "$stage/las"
git archive --format=tar HEAD | tar -x -C "$stage/las"
mkdir -p "$stage/las/frontend"
cp -r frontend/dist "$stage/las/frontend/dist"
echo "$rev" > "$stage/las/REVISION"

tar -czf "$out" -C "$stage" las
echo "Wrote $out ($(du -h "$out" | cut -f1)), commit $rev"
