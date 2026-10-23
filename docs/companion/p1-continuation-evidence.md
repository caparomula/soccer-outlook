# P1 continuation: real collection and browser corrections

October 20, 2026. This continues the [initial P1 checkpoint](p1-evidence.md).
The fresh full-build evidence and offline lightweight replay now exercise the
actual requested source scope, with the website and independent Dart reader
consuming the same facts. Form ordering has bounded independent evidence, with
source accuracy and freshness limits retained explicitly. No source permission
or publication authorization is inferred from these checks.

## Fresh full build and independent reconciliation

The [collection report](evidence/feed-collection-2026-10-20.json) records an
uncommitted build over `9b977bb2b779b55bea84472e81f4ab00da423f1a`, with source
hashes before and after collection. It requested the normal range without a
`--date` override: October 16–24, covering 34 leagues and 306 league/date pairs.
All required responses were usable; there were no skipped events or deliberate
competition exclusions in this capture. It made 375 logical transport calls,
including standings and optional broadcasters/comparison sources; four optional
calls were unavailable. The transport may retry internally, so 375 is not a
claim about individual HTTP attempts.

[`companion_collection_capture.py`](../../tool/companion_collection_capture.py)
retains raw response bytes, digest/size, actual requested URLs, call attempt and
receipt times, and the producer's separate validation times. Reconciliation
checks requested and observed scope against that ledger, including empty dates;
it does not infer completeness from fixture count. The confirmed interval is
October 16 at 04:00 UTC through October 25 at 04:00 UTC, corresponding to the
requested Eastern calendar dates.

All 360 fixture identities agree across raw sources, the builder context,
static HTML, browser schedule rows, required feed artifacts and the independent
Dart reader. Direct checks cover source team identity/name, confirmed kickoff,
provider state, numeric scores, canonical Outlook components, qualified network
routes, service-plan associations, viewing destinations, rank and original ages.
Among the side-specific score observations, 82 are real zero, 278 are positive
and 360 remain null. The capture contains no exceptional postponed/cancelled/
abandoned status; it does not prove the future shared status decoder.

The unfiltered feed retains 133 fixtures without an owner service and 184 in
default-off competitions. These sets overlap. Six offline browser captures
cover Chromium/WebKit, Eastern desktop/mobile and Pacific mobile, with no script
errors and matching attached scores. The default browser DOM detaches 163 rows;
every detachment is accounted for by explicit service/competition defaults.
All 360 remain in raw/static/schedule/feed data. This is factual consistency,
not a migration of viewer eligibility or ranking into Dart.

Raw responses and complete generated output remain in ignored
`work/companion-p1-fresh/`. The report retains source and output hashes and
browser observations. Its `source_hashes` identify collection code;
`reconciler_source_hashes` separately identify the offline checker, including
corrections to its destination-plan comparison and detached-DOM accounting.
A later docstring-only change to that tool is not relabeled as code executed
by the retained run.

## Lightweight replay and publication behavior

The [lightweight report](evidence/feed-lightweight-replay-2026-10-20.json)
records the real lightweight build path consuming 349 retained transport calls.
[`companion_collection_replay.py`](../../tool/companion_collection_replay.py)
forbids network fallback, fixes the original build clock and restores original
source validation times. It preserves the original standings age and required
catalog/schedule/policy bytes while increasing publication revision from 1 to 2.
The independent Dart reader accepts the result. This is an offline branch
replay, not a second collection or renewed source freshness.

The [updated producer/reader report](evidence/feed-continuation-2026-10-20.json)
also passes the eight synthetic edge fixtures, automatic/shared editorial,
republication and required/optional corruption checks with the new form fields.
Original editorial timestamps and artifact bytes remain unchanged on republish.
The older 360-fixture historical projection is still labeled as having synthetic
scopes; the fresh collection above supplies the previously missing real scope
proof without rewriting that earlier evidence.

## Score and form corrections

Independent source review found numeric zero lost by `score or ""` in the
existing Python decoder. The regression failed before the fix and now preserves
numeric `0` as `"0"` through the builder and as `0` in the feed, while missing
scores remain null. This is a source-decoding bug fix, not a scoring change.

The [form corpus and method](../../tests/contracts/form/README.md) retain eleven
public ESPN responses covering sixteen teams in three leagues. Result letters
are independently derived from dated numeric scores. Four complete,
non-palindromic sequences support newest-first; none support oldest-first.
Fifteen of sixteen agree on their four oldest positions under newest-first.
Twelve differ in at least one reported value, so this establishes a bounded
encoding inference, not universally accurate dated history.

The exporter reverses supported source-reported W/D/L strings into oldest-first
order, preserves `raw_form`, and records typed `form_provenance` with
`values_verified: false`, null as-of time and null precise competition scope.
It does not repair discrepancies using another endpoint. Missing or unrecognized
encoding yields an empty normalized sequence. The directly sampled fields have
five characters; shorter fields use the same encoding inference. The existing
builder's five-character projection means future longer upstream values require
decoder review. Website form presentation is unchanged.

## WebKit corrections and verification

The original failures were reproduced before repair. Event traces identified
two product defects: native Tab navigation can skip modal links and escape to
`BODY`, and WebKit can focus a carousel ancestor during a provider link's
mousedown, causing the global focus listener to close the menu before click.

Modal traversal now explicitly visits eligible visible actions in both
directions. Provider disclosure handling preserves native anchor activation
through that pointer/focus sequence, while outside and keyboard dismissal still
work. Lost mouseup, pointer cancellation and blur do not leave the guard stuck.
Tests assert each modal action's focus order and the exact provider destination
and opener isolation, rather than merely checking that focus stays somewhere
inside a dialog.

The Apple TV activation case uses a reserved `.invalid` host only after checking
the exact production href and link attributes, preserving its path/query and
the same anchor. Plain-page probes show macOS WebKit does not open the synthetic
Apple URL as a browser page. Native handoff is a possible explanation, not a
verified outcome. Original-host handoff, sign-in and playback remain outside
offline coverage. Other provider links retain the original popup assertion.

Final browser checks pass: 111 full Chromium checks, 39 WebKit compatibility
checks and 11 schedule checks in each engine, for 172 total. The
[browser correction report](evidence/p1-browser-fix-2026-10-20.json) retains
source hashes, logs and regression evidence. An initial WebKit schedule run
exposed a harness race: the clock could advance while the initial offline
404 request was still pending. The helper now waits for its completion before
installing the next response and advancing time; both schedule suites passed
after that harness-only correction.

The Python suite passes all 675 tests. The Dart parser and local loader pass
23 and 2 tests respectively. Independent review found no blocking defect in
the form or current modal/provider behavior. A synthetic `visibility:hidden`
modal control remains a future hardening case; current generated dialogs do
not contain such controls. No passing suite establishes native-device access
or performance.

## Current boundaries

The feed is a local preview; the workflow export flag remains disabled. No
commits, pushes, remote workflow runs, feed deployment, AI generation or app
distribution were performed. The pre-continuation dirty state is preserved in
`work/p1-continuation-start/` (126 files, hash manifest and archive).

P1's local technical acceptance is complete; its public feed-URL delivery is
still gated. P2 is next and owns shared eligibility/ranking/status/editorial decisions and consumer
migration. P3 still owns HTTP refresh, durable client installation, preferences
and live correction ordering. Complete standings tables are not yet exported;
the feed supplies per-fixture rank/vitals. P0 physical/minimum-OS and release
performance gates remain. Source usage permission and explicit publication
authorization remain prerequisites for a public feed URL.
