# Shared Outlook adapters

The publisher CLI and browser bridge call pure rules in
[`outlook_core`](../../packages/outlook_core/) and the narrow
[`ESPN decoder`](../../packages/outlook_data/lib/espn_decoder.dart) in
`outlook_data`. The P2 migrations
cover qualified-route preference/provider ordering, sports-day calendar
boundaries, qualified-route lifetimes, viewer league/fixture eligibility and
normalized fixture focus/schedule windows.
`buildOutlook` composes those
decisions in an immutable snapshot. The source decoder separately interprets
normalized status and reported match scores; it does not inspect a clock or
viewing route. Source-observation freshness, live-overlay reconciliation,
ranking and editorial lifetime rules remain later work. See the
[rule catalog](../../docs/companion/rules/README.md),
[first increment](../../docs/companion/p2-evidence.md),
[eligibility increment](../../docs/companion/p2-eligibility-evidence.md) and
[validity increment](../../docs/companion/p2-validity-evidence.md) and
[window rule](../../docs/companion/rules/fixture-windows.md) and
[status rule](../../docs/companion/rules/espn-status.md).

## Prepare and verify

Use the Dart version in [`companion-toolchain.json`](../../companion-toolchain.json)
and checked-in lockfiles. From the repository root:

```sh
dart --suppress-analytics pub get --enforce-lockfile --directory packages/outlook_core
dart --suppress-analytics pub get --enforce-lockfile --directory packages/outlook_data
dart --suppress-analytics pub get --enforce-lockfile --directory tool/outlook_bridge
python3 tool/build_outlook_runtime.py --dart dart
python3 tool/companion_p2_verify.py --runtime work/outlook-runtime --dart dart
python3 tool/companion_eligibility_verify.py --runtime work/outlook-runtime --dart dart
python3 tool/companion_validity_verify.py --runtime work/outlook-runtime --dart dart
python3 tool/companion_window_verify.py --runtime work/outlook-runtime --dart dart
```

The verifier needs `tests/requirements-browser.txt`, installed Chromium/WebKit,
and localhost permission. It intercepts external requests and makes no ESPN or
AI calls. Compilation resolves dependencies offline after explicit setup.
`--dart /absolute/path/to/dart` selects the pinned SDK.

The validity verifier runs from a clean checkout without historical files in
`work/`. For a local old-page comparison, add `--legacy PATH` pointing to a frozen
capture with its manifest and original script. `--legacy-only` requires that
explicit path. Historical comparison supplements the independent expectations;
it is not required to run the current implementation checks in CI.

The build tool produces `outlook`, `outlook.js` and a source/artifact hash manifest
in ignored `work/outlook-runtime/`. It enforces the complete core gzip ceiling
from [`companion_p0_limits.json`](../companion_p0_limits.json). The publisher
adapter rejects absent, stale or altered artifacts. There is no Python or
JavaScript fallback implementation of the migrated decisions.

Full and lightweight website builds install one content-addressed, integrity-
checked script beside the HTML before writing the page. It loads before the
browser adapter. The previous page's script is retained across a full workflow
build; the website's existing static schedule remains usable if the new script
cannot load. Fragment hosts must also serve this adjacent asset.

## Protocol

Both browser exports, `outlookDecisions(jsonString)` and the compatible
`outlookSportsDay(jsonString)`, dispatch protocol version 1. The CLI accepts the
same JSON on stdin and emits one JSON response. `--serve` uses bounded
newline-delimited requests/replies for the publisher's persistent process.
Each batch has at most 1,000 unique case IDs and is rejected as a whole on a
protocol violation. Malformed provider field values in the status operations
instead yield conservative unknown status or absent scores. The publisher
transport has a total deadline covering writes
and reads, a response-size limit and bounded immutable-request caching.

Operations:

- `route_selection`: explicit selected services, preference order and display
  names, plus producer-qualified listed/usual routes. Returns preferred service,
  basis, outlet and selected providers for every route. With `now_utc`, evaluates
  supplied `validity` before intersection and returns aligned `listed_validity`
  and `usual_validity` decisions. Production adapters supply validity on every
  route; legacy already-qualified callers retain their old result shape.
- `route_validity`: original verification, retention flag/allowance, kickoff,
  explicit cutoff, usual-rights end date and supplied clock. Returns usability,
  reason and the exclusive effective cutoff; preserves microseconds. See
  [the rule and examples](../../docs/companion/rules/route-validity.md).
- `sports_day`: UTC instant, reference instant and named zone. Returns local
  date, UTC boundaries and calendar-label index relative to the reference.
- `calendar_shift`: reference instant, named zone and calendar-date offset.
  Returns the shifted sports-day boundary and date.
- `fixture_window`: supplied evaluation time, named zone, kickoff, confirmation,
  normalized `pre`/`in`/`post`/`unknown` phase and nullable accepted-response receipt.
  Returns day index, schedule bucket, window/focus membership and separate
  source-in-progress/receipt-recency facts. No provider-observation freshness is
  inferred. See [the rule and examples](../../docs/companion/rules/fixture-windows.md).
- `espn_status`: each case supplies `id`, `event_status`, `competition_status`,
  `home_score` and `away_score`. A nonempty competition status object takes
  precedence without merging event fields. Returns normalized `state`, placement
  `phase`, original `provider_state`, display `label`, separate `played`, `final`
  and `void` facts, and independently parsed `reported_scores` and gated `scores`.
- `fixture_status`: each case supplies `id`, `phase`, `label`, `home_score` and
  `away_score` from a direct or rehydrated fixture. Optional `state` preserves a
  canonical decoded status after validating its phase; its live label remains
  display text. Absent/null state retains legacy phase/label inference. It returns
  the same status result using the same classifier and score parser. Neither operation infers
  time confirmation, scorer events, shootout/aggregate totals or freshness. See
  [the rule and examples](../../docs/companion/rules/espn-status.md).
- `viewer_eligibility`: ordered league defaults, explicit choices, pauses,
  requested solo and already-selected route/window facts. Returns league
  visibility, resolved solo, strip membership, common fixture eligibility and
  the fixture-free calendar-editorial gate. See the
  [rule contract](../../docs/companion/rules/viewer-eligibility.md).

See [route examples](../../docs/companion/rules/route-examples.json),
[shift examples](../../docs/companion/rules/sports-day-examples.json) and
[containing-day examples](cases/sports_day.json), plus
[eligibility examples](../../docs/companion/rules/eligibility-examples.json) and
[window examples](../../docs/companion/rules/fixture-window-examples.json) and
[status examples](../../docs/companion/rules/espn-status-examples.json). Unknown zones or malformed
input return `{"version":1,"error":"invalid_input"}` without echoing caller
content. One-shot CLI errors exit 65; a server rejects the frame and can handle
the next request. The protocol is internal and separate from the public feed.

The full pinned IANA database includes aliases emitted by browser Intl APIs.
It is shared by VM, compiled CLI and browser. Unsupported zones remain errors;
platform adapters supply the zone and clock. Unknown kickoff semantics are not
implemented by inventing a confirmed timestamp in this protocol.

## Evidence and historical spike

The P2 verifier runs independent examples, frozen captured-route comparisons,
VM/CLI/browser parity, strict CSP on the compiled core, actual generated-page
startup and service changes, and missing/corrupt-asset fallback. Host timing
samples do not certify physical-device performance. All executable examples
and consumer sources are fingerprinted in its report.

`verify.py` preserves the original P0 helpers but refuses its old benchmark on
the migrated website: the former baseline no longer has an independent calendar
owner. Retained [P0 evidence](../../docs/companion/p0-evidence.md) remains
historical evidence. Use `companion_p2_verify.py` for the current code.
