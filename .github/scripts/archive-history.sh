#!/usr/bin/env bash
# Append one build's observations to a durable branch before publishing the page.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
metrics=${1:-work/metrics.json}
story=${2:-site/story.json}
shared=${3:-site/shared-ai.json}
archive_dir=$(mktemp -d "${RUNNER_TEMP:-/tmp}/soccer-outlook-history.XXXXXX")
trap 'rm -rf "$archive_dir"' EXIT

git init -q --initial-branch=match-history "$archive_dir"
git -C "$archive_dir" remote add origin "$(git -C "$repo_root" remote get-url origin)"

# actions/checkout stores its token in this header. Copy it without printing it
# or embedding it in a remote URL; the temporary checkout is removed on exit.
if auth_header=$(git -C "$repo_root" config --get http.https://github.com/.extraheader); then
  git -C "$archive_dir" config http.https://github.com/.extraheader "$auth_header"
  unset auth_header
fi

remote_status=0
git -C "$archive_dir" ls-remote --exit-code --heads origin refs/heads/match-history > /dev/null || remote_status=$?
case "$remote_status" in
  0)
    git -C "$archive_dir" fetch -q --no-tags --depth=1 origin refs/heads/match-history
    git -C "$archive_dir" checkout -q -B match-history FETCH_HEAD
    ;;
  2) ;; # The repository is reachable, but the archive branch does not exist yet.
  *)
    echo "Could not check the match-history branch; refusing to publish without an archive." >&2
    exit "$remote_status"
    ;;
esac

source_revision=${GITHUB_SHA:-$(git -C "$repo_root" rev-parse HEAD)}
run_id=${GITHUB_RUN_ID:-local}
run_attempt=${GITHUB_RUN_ATTEMPT:-1}
if [[ ! "$source_revision" =~ ^[0-9a-fA-F]{40,64}$ ||
      ! "$run_id" =~ ^[a-zA-Z0-9_-]+$ || ! "$run_attempt" =~ ^[0-9]+$ ]]; then
  echo "Invalid source revision or workflow run identifier for the archive filename." >&2
  exit 1
fi
snapshot_path="snapshots/$(date -u +%Y/%m)/$(date -u +%Y-%m-%dT%H-%M-%SZ)-${source_revision}-${run_id}-${run_attempt}.json.gz"
python3 "$repo_root/history.py" --metrics "$metrics" --story "$story" --shared "$shared" \
  --observations "$repo_root/content/match-observations.json" \
  --out "$archive_dir/$snapshot_path"
mkdir -p "$repo_root/work"
cp "$archive_dir/$snapshot_path" "$repo_root/work/history-snapshot.json.gz"

git -C "$archive_dir" add -- "$snapshot_path"
git -C "$archive_dir" -c user.name="github-actions[bot]" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "Record match metrics from ${source_revision:0:7}"
# A concurrent writer causes a normal non-fast-forward failure, never an overwrite.
git -C "$archive_dir" push -q origin HEAD:refs/heads/match-history
echo "Archived $snapshot_path on match-history."
