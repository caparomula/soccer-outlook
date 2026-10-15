#!/usr/bin/env bash
# A queued run must not publish data or AI generated from superseded source code.
set -euo pipefail

expected=${GITHUB_SHA:-}
if [[ ! "$expected" =~ ^[0-9a-fA-F]{40,64}$ ]]; then
  echo "::error::A valid workflow source revision is required before publication." >&2
  exit 1
fi

# actions/checkout supplies authentication through git configuration. Do not put
# credentials in this command or print the remote URL.
remote=$(git ls-remote --exit-code origin refs/heads/main)
read -r current ref <<< "$remote"
if [[ ! "$current" =~ ^[0-9a-fA-F]{40,64}$ || "$ref" != refs/heads/main ]]; then
  echo "::error::Could not verify the current main revision; keeping the published site." >&2
  exit 1
fi
if [[ "$current" != "$expected" ]]; then
  echo "::notice::This run's source revision has been superseded on main; keeping the published site for the newer refresh."
  exit 1
fi
