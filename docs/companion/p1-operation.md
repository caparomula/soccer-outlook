# Operating the local companion feed

P1 adds an opt-in exporter beside the existing website, a local immutable
publication writer, and an independent Dart reader. It does not switch the
website or introduce a shipped companion client. The feed is a preview of
[v1](data-contract.md); its policy is `outlook-policy-p1-preview-1`.
The [current evidence record](p1-continuation-evidence.md) covers the fresh
collection, offline replay and browser corrections.

Distribution remains gated by the user's October 20, 2026 decision to continue
local engineering while source permission is unresolved. No feed has been
enabled or deployed by this work. The workflow repository variable
`COMPANION_FEED_ENABLED` must remain unset or false. Changing it to exactly
`true` enables a new public output and requires separate authorization; a
successful local check is not that authorization.

## Offline verification

Run from the repository root with Python 3.13 and the Dart version in
[`companion-toolchain.json`](../../companion-toolchain.json). Use an isolated
Python environment. Dependency installation may access package registries;
the verification commands use local fixtures and make no provider requests.
Prepare the shared Dart runtime using the [development setup](../development.md#run-it-locally)
before importing the builder or running P1 verification. P2's route/calendar
consumers now require it even when companion-feed export is disabled.

```sh
python -m pip install -r tests/requirements-companion.txt
dart --suppress-analytics pub get --enforce-lockfile --directory packages/outlook_data
dart --suppress-analytics pub get --enforce-lockfile --directory tool/companion_feed_reader
python tool/companion_feed_verify.py --out work/companion-p1 --dart dart
```

The verification tool generates publications through the production exporter,
checks them through the independent reader, and exercises rejection cases.
Its generated evidence belongs under ignored `work/`. It does not fetch the
current schedule, call an AI provider, deploy a site, or change repository
settings.

For a fresh source-to-rendered comparison, the separate capture tool runs the
existing full build with the normal nine-date scope and linked images. This is
an explicit network operation: up to 500 logical transport calls, including the
existing transport's bounded retries. It performs no AI generation or deployment.
Use a new directory; failed captures and old evidence are never overwritten.

```sh
python tool/companion_collection_capture.py --fetch \
  --out work/companion-fresh --workers 6
python tool/companion_collection_capture.py --reconcile \
  --out work/companion-fresh --dart dart --browsers chromium webkit
python tool/companion_collection_replay.py --capture work/companion-fresh \
  --out work/companion-lightweight-replay --dart dart
```

Reconciliation and lightweight replay are offline. Browser reconciliation needs
the pinned Playwright browsers installed. The replay consumes retained request
bytes, forbids network fallback and preserves the original build/source/standings
times. Its increased publication revision is not evidence of a new collection.
Capture response limits are enforced after the existing transport returns;
they do not establish a bound on curl's internal allocation. Raw captures stay
in ignored `work/`; compact evidence belongs in the companion documentation.

The producer and integration checks can also run separately:

```sh
python -m unittest tests.test_companion_publication tests.test_companion_export \
  tests.test_companion_integration tests.test_companion_story_bindings -v
```

Run `dart --suppress-analytics analyze` and `dart --suppress-analytics test`
from each of `packages/outlook_data` and `tool/companion_feed_reader`.
The [companion CI workflow](../../.github/workflows/companion.yml) runs these
checks, the cross-language verification tool, and the retained P0 checks. It
has read-only repository permissions and no deployment step. Editing that
workflow does not establish that it has run on GitHub.

To inspect any locally generated publication:

```sh
dart --suppress-analytics run tool/companion_feed_reader/bin/read.dart \
  --api-dir work/companion-site/api/v1 --schema-dir contracts/outlook/v1
```

The CLI reports factual fixture and route IDs and the optional editorial state.
An invalid required publication exits 65. An invalid optional editorial artifact
does not discard validated catalog, schedule and policy. See the
[reader guide](../../tool/companion_feed_reader/README.md) for limits and error
behavior. HTTP refresh, concurrent ordering and durable client installation
remain P3 work.

## Building alongside the website

`build.py` defaults to the existing outputs and does not import the companion
publisher unless `--companion-feed` is supplied. The opt-in path requires an
explicit prior API directory and a full, lowercase 40-character producer Git
revision. The API directory contains `manifest.json`, rather than the website
root. A missing prior directory is an error.

The following is an operational example, not an offline fixture command:
`build.py` makes the existing scoreboard, standings and broadcaster requests.
Use it only when those source requests are intended. Set `producer_revision`
to the source commit being evaluated; an uncommitted checkout still needs to
be identified as uncommitted in its evidence.

```sh
producer_revision=$(git rev-parse HEAD)
python build.py --out work/companion-site/index.html --no-logos \
  --facts work/companion-facts.json --report work/companion-build-report.json \
  --companion-feed --companion-previous-api none \
  --companion-producer-revision "$producer_revision"
```

`none` explicitly declares that there is no external prior publication to
restore. Use it for a first local publication. For a later build, supply the
existing `api/v1` directory or a complete retained copy, for example
`--companion-previous-api work/companion-site/api/v1`. Never replace a missing
or corrupt history path with `none` to bypass validation. Existing target state
is still validated and its sequence is preserved when `none` is used.

For lightweight builds, also pass `--lightweight` and a compatible
`--previous-context` path. Its original standings timestamp is preserved.
Both build paths require complete requested fixture scopes when the companion
export is enabled. A complete empty schedule is valid; incomplete required
input leaves the previous feed usable and fails the build before replacing
the page. Optional broadcaster failures retain their source-health limits.

## Finalizing editorial after generation

The full workflow stages normalized facts before its optional story task and
finalizes the manifest afterward. This prevents a manifest from capturing the
story file before its generation or fallback has finished. The lightweight
workflow uses the same staging/finalization boundary with retained editorial.

For a local equivalent, add `--companion-stage work/companion-stage.json` to
the build command, finish any separately intended story task, then run:

```sh
python .github/scripts/companion-feed.py \
  --stage work/companion-stage.json --api-dir work/companion-site/api/v1 \
  --automatic work/companion-site/story.json \
  --shared work/companion-site/shared-ai.json
```

If generating new automatic editorial for this feed, add
`--companion-bindings` to the separately authorized `story.py` invocation.
That records original fixture ID, league and kickoff with each accepted
response's prompt time, generation time and expiry. Retained ratings and
resumed research preserve those fields. Legacy unbound automatic entries are
omitted with a diagnostic; current fixture facts never supply a replacement
historical binding. A newly written blurb has its own origin, and an explicit
null origin prevents it from inheriting an older rating's identity or lifetime.

Editorial conversion makes no generation requests. Missing, invalid, expired
or rescheduled optional content can be omitted without failing required facts.
Shared responses retain their original prompt time and 24-hour maximum
lifetime. Automatic records retain their original generation/focus constraints
and per-entry expiry. Republishing updates the manifest's publication time,
not these source ages. Explicit null editorial slots are withdrawals.

## Durable state and serialized workflows

Keep the entire `api/v1` tree when moving or restoring a publication:

| Path | Purpose |
| --- | --- |
| `manifest.json` | Current validated snapshot pointer. |
| `objects/<sha256>.json` | Immutable artifacts, checked by decoded size and digest. |
| `manifests/<revision>.json` | Retained prior manifests and their artifact references. |
| `publication-state.json` | Highest reserved publication revision. |

The publisher validates required artifacts before committing them. It takes a
local filesystem lock, reserves the next revision durably, writes immutable
objects and prior history, then atomically replaces `manifest.json`. A failed
attempt may consume a revision. Gaps are valid; deleting reservation state or
rewinding a counter is not recovery. A retention warning after the pointer
commit means the new publication succeeded and unused files may remain.

The full and lightweight GitHub workflows share the `refresh` concurrency
group and retain their superseded-main checks. Full builds restore the whole
published API tree before exporting. Lightweight builds copy the published
site before refreshing it. A full build that cannot fetch existing published
history fails closed even when new export is disabled. Only a conclusive
absence of the `gh-pages` branch permits first-publication bootstrap. These
guards avoid resetting a sequence or deleting an existing feed on a transient
fetch failure.

Turning off new export preserves the prior API tree while allowing the legacy
website flow to continue when its own checks pass. It does not make old data
fresh. Local locks do not serialize distinct remote deployments; the shared
workflow concurrency group and source-revision checks remain required.

## Retention, rollback and compatibility

Server collection retains the current complete publication, the immediately
previous complete publication, and artifacts referenced by manifests published
within the last seven days. Retention is based on publication timestamps and
does not extend source or editorial validity. Do not manually remove objects
still referenced by a retained manifest.

Restore from a complete validated copy with
`companion_publication.restore_previous(target_api_dir, previous_api_dir)`.
It preserves newer valid target state and rejects conflicting equal revisions.
Restore is for recovering state; it is not a mechanism for making an older
revision current.

To roll back content, validate a retained prior manifest and its artifacts
against the existing API tree, then call `companion_publication.publish` with
those artifacts and the current durable API directory. Supply the rollback
producer's full revision and an explicit UTC publication time. This creates a
new higher publication revision pointing to old content. Do not copy an old
`manifest.json` over the current one or reset `publication-state.json`.
Original source observations, fixture bindings and editorial expiry stay
unchanged during rollback. Any eventual deployment remains separately gated.

The [P0 compatibility decision](p0-decisions.md#storage-and-compatibility-limits)
and [limits file](../../tool/companion_p0_limits.json) reserve at least 365 days
of support after the last compatible app distribution, 90 days' retirement
notice, and current/previous schema majors during migration. If that would
strand supported clients, postpone retirement or the incompatible release.
The support clock has not started for this unshipped companion. Seven-day
object retention is separate from feed-major support.

## Preview limits

The exporter preserves existing owners' facts and qualifications. Route scope
comes from the [network registry](../../contracts/outlook/network-ids.json); eligible and excluded plans reflect
the current rights model. It does not invent finer package requirements,
blackout eligibility, account authorization or verified player URLs. Unknown
or rejected/replay evidence cannot grant access. The draft rule IDs still
require the shared decision implementation in P2 before a reader can select
recommendations.

Team form retains `raw_form` and normalizes supported W/D/L strings into
oldest-first order using the [dated source comparison](../../tests/contracts/form/README.md).
The reported values are not independently verified history: form provenance
leaves as-of time and precise competition scope null and records
`values_verified: false`. Missing or unrecognized encoding produces an empty
sequence. The bounded evidence covers five-character strings; shorter strings
use the same encoding inference, and longer upstream fields still need review.
Missing measurements and unconfirmed kickoff times remain explicit unknowns.
Source receipt/validation times are separate from provider observation times;
an unavailable observation time stays null. These limits are part of the
preview evidence, not permission for a client to guess missing facts.
