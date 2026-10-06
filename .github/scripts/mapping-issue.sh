#!/usr/bin/env bash
# Keeps one GitHub issue in step with the build's mapping report (build.py --warnings FILE).
#
# The report lists what a build couldn't map or can no longer vouch for: a broadcaster name that
# isn't in rights.toml, a competition whose usual home disagrees with ESPN's listings or has
# reached the end of its season, a fact unchecked for six months. Written only to the run's page,
# it waited for someone to look; as an issue it reaches the owner by email. The issue opens when
# the report has something in it, is updated (with a comment, which notifies) when the report
# changes, and closes when a build's report is clean.
#
# Usage: mapping-issue.sh REPORT
# Needs gh on the PATH and, in the environment, GH_TOKEN (with issues: write), GITHUB_REPOSITORY,
# GITHUB_SERVER_URL, GITHUB_RUN_ID and MENTION (the GitHub user to notify).
set -euo pipefail

report="$1"
label="mapping"
title="Broadcast mapping needs a look"
run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"

if [ ! -f "$report" ]; then
  echo "No report at ${report}; the build didn't get that far."
  exit 0
fi

gh label create "$label" --color D93F0B \
  --description "The build found broadcasts it couldn't map or vouch for" >/dev/null 2>&1 || true
number=$(gh issue list --label "$label" --state open --limit 1 --json number --jq '.[0].number // empty')

if [ ! -s "$report" ]; then
  if [ -n "$number" ]; then
    gh issue close "$number" --comment "All clear as of [this run](${run_url}): every broadcaster is mapped and every usual home is current."
    echo "Closed #${number}: the report is clean."
  else
    echo "The report is clean."
  fi
  exit 0
fi

# The marker lets a later run tell whether the report changed without comparing prose.
digest=$(sha256sum "$report" | cut -c1-16)
body=$(mktemp)
{
  echo "<!-- mapping-report: ${digest} -->"
  echo "The page's build found broadcasts it couldn't map, or facts it can no longer vouch for. Until they're fixed, the page shows an unrecognized channel as unrecognized, and a competition whose usual home has lapsed as having none."
  echo
  sed 's/^/- /' "$report"
  echo
  echo "**To fix:** open this repository in Claude Code and ask it to resolve this issue. Each item needs some research on the web, then an edit to \`rights.toml\` with the source and today's date, then \`python3 -m unittest\`. Pushing to main rebuilds the page, and a build with a clean report closes this issue."
  echo
  echo "Latest report: [this run](${run_url}). cc @${MENTION}"
} > "$body"

if [ -z "$number" ]; then
  gh issue create --title "$title" --label "$label" --body-file "$body"
elif ! gh issue view "$number" --json body --jq .body | grep -qF "<!-- mapping-report: ${digest} -->"; then
  gh issue edit "$number" --body-file "$body" >/dev/null
  gh issue comment "$number" --body "The report changed in [this run](${run_url}); the list above is current."
  echo "Updated #${number}."
else
  echo "#${number} already has this report."
fi
