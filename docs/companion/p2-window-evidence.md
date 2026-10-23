# P2 fixture-window acceptance

The bounded `TIME-WINDOW-1` increment passed local acceptance on October 23,
2026. One pure Dart owner now computes fixture focus and schedule placement from
normalized source state, kickoff, time confirmation, original accepted-response
receipt, named zone and an explicit evaluation clock. This extends the
[route-validity checkpoint](p2-validity-evidence.md); P2 and full TIME-2 status
and freshness work remain incomplete.

The [rule](rules/fixture-windows.md),
[independent examples](rules/fixture-window-examples.json) and
[validation record](evidence/p2-window-validation-2026-10-23.json) define the
boundary and bind the tested sources, runtime, reports and logs.

## Shared owner and consumer changes

[`evaluateFixtureWindow`](../../packages/outlook_core/lib/src/fixture_window.dart)
returns day index, schedule bucket, window/focus membership and separate
source-in-progress/receipt-recency facts. `buildOutlook` now computes window
membership from explicit fixture facts before viewer eligibility; callers can no
longer inject an unrelated `inWindow` boolean into that composed snapshot. The
standalone viewer-eligibility operation still accepts a window decision.

The version 1 `fixture_window` operation serves the VM, compiled publisher CLI
and browser. The Python `fixture_windows` adapter maps explicit aware times and
checks response identities without a local rule fallback. Existing Python
static grouping, source collection and AI-generation windows remain unchanged;
this increment does not claim those distinct policies have migrated.

The browser batches decisions and replaces `inFocus` and `bucketOf` with
read-through calls. Its cache key includes every supplied fact, clock and zone.
A replacement DOM row receives its own projection, and failed batches install
none of the pending results. Viewer eligibility consumes the shared
`in_window`; existing focus and bucket consumers use the same owner while
retaining their additional ranking, editorial and surface-selection rules.

## Preserved behavior and intentional corrections

The extraction preserves the inclusive kickoff-plus-125-minute focus boundary,
exclusive 155-minute same-sports-day allowance for source-in-progress fixtures,
exclusive 24-hour focus horizon, and the previous-three/next-three sports-day
schedule window. It preserves the existing TBD placeholder and cross-04:00
placement distinctions. The named `live` bucket remains placement: scheduled
matches can be awaiting a score there without inventing progress, a score,
a clock or a live-only viewing link.

There are two intentional clock corrections. Receipt age now uses the same
supplied evaluation clock as kickoff arithmetic, and a receipt later than that
clock cannot count as recent. Exact ten-minute age remains stale. The frozen
corpus contains three examples affected by those corrections; their old answers
remain intact beside the separately authored corrected expectations.

`source_in_progress` records what the accepted source said. A recent receipt is
not evidence that the provider recently observed the score. This family leaves
status/score facts and live-only destination policy unchanged when placement
expires. It does not extrapolate them or reset their ages.

## Evidence

| Check | Result and scope |
| --- | --- |
| Core | 127 fresh Dart tests pass, including 18 independently authored window groups with 38 literal decisions and five direct/composition tests. Core/bridge analysis is clean; formatter checks 17 files without changes. |
| VM protocol | 78 independent example groups; 34 malformed semantic frames and two malformed transport frames rejected with recovery. A valid 1,000-case batch succeeds; one-shot invalid input exits 65. |
| Four-target parity | VM, compiled CLI, Chromium and WebKit pass 20 request groups: 38 literal decisions, 453 preserved legacy decisions and three declared clock corrections. Each target rejects 13 additional malformed window requests. |
| Generated pages | Five lifecycle groups pass in each engine: exact 125-minute placement with pending-score/link separation; receipt/snapshot carry through row/outlet replacement and exact ten-minute expiry; pinned clocks and future receipts; visibility resume; 04:00/TBD/grace behavior. Production renderer and external SRI runtime are used. |
| Adapter review | Seven Python adapter tests pass, including actual compiled-CLI coverage and adversarial adapter validation. Nineteen independent Node orchestration probes cover all input invalidations, cache reuse, row replacement, 2,501-row batching and failure atomicity. The Node probes use a mock core and make no domain/browser-parity claim. |
| Regressions | 737 Python tests; 112 Chromium checks, 40 WebKit smoke checks and 11 schedule-refresh checks per engine, totaling 174 browser regressions. Route/calendar, eligibility and route-validity verifiers pass on this runtime. |
| Offline builds | Full and lightweight builds replay 360 captured fixtures, preserving original ages and required catalog/schedule/policy bytes. Both publications pass the independent Dart reader; revisions advance from 1 to 2. |
| Budgets | Browser core: 195,621 gzip bytes against 204,800, an increase of 850 with 9,179 bytes remaining. Replayed pages: 2,764,834 bytes against 3,300,000. All 448 embedded logos remain. |

Reports: [core](evidence/p2-window-core-2026-10-23.json),
[protocol and browser](evidence/p2-window-2026-10-23.json),
[Python adapter](evidence/p2-window-python-2026-10-23.json),
[adapter review](evidence/p2-window-adapters-2026-10-23.json),
[route/calendar](evidence/p2-window-route-calendar-2026-10-23.json),
[eligibility](evidence/p2-window-eligibility-2026-10-23.json),
[route validity](evidence/p2-window-validity-2026-10-23.json), and
[build replay](evidence/p2-window-replay-2026-10-23.json).

The portable [frozen baseline](../../tests/contracts/p2/legacy-fixture-windows-2026-10-23.json)
contains 360 captured fixtures and 96 added boundary cases. Its
[original function fragment](../../tests/contracts/p2/legacy-fixture-windows-functions-2026-10-23.js)
and the exact pre-migration compiled calendar runtime were preserved before
replacement. CI checks the frozen answers and independent examples without
requiring ignored local capture files. The final validation additionally binds
the original capture, runner, start snapshot and archived runtime.

The first generated-page verifier attempt stopped on an ambiguous heading
locator; the correction selects the heading label. Its failed log and trace
remain alongside the successful second attempt. No production fix was needed
for that harness failure. The final record verifies the corrected verifier's
source hashes and five integrity/mutation tests.

## Timing and evidence limits

The core compares microseconds. Browser inputs and clocks have millisecond
precision. Browser proofs explicitly recompute through existing lifecycle
triggers at boundary instants; this increment adds no dedicated window-deadline
timer. The existing minute render, preference/hash changes, accepted-update
render paths, schedule replacement and visibility/resume behavior remain.
An unchanged-score receipt can wait for the existing next recomputation.

Strict CSP is exercised on the protocol page. The generated website retains its
existing inline script/style model. Synthetic page inputs and destinations test
consumer behavior; no provider destination is opened. The real-fixture replay
establishes migration parity, not independent coverage of every boundary or a
new live-source freshness claim. No physical-device or release-performance
acceptance is inferred from these host checks.

## Preservation and remaining work

The 215-file dirty starting state and the prior compiled runtime were archived
and verified. HEAD remains `6200140feb9b3fe63b2609d09340355c6458edd0` on
`companion/implementation`, with an empty index. No commit, push, deployment,
remote workflow, new source collection or live AI call occurred. Compact
reports are retained beside this document; local traces and build artifacts
remain under ignored `work/`.

The next P2 increment is the shared ESPN status/result decoder, followed by
source-observation freshness, remaining source qualification, ranking and final
surface/editorial rules. The 360 normalized replay fixtures contain no exceptional
terminal or TBD cases, so decoder acceptance will require independent postponed,
cancelled, abandoned, unfamiliar-status and result-shape cases. Live-overlay
identity/kickoff reconciliation and durable persistence remain P3 work; the
native product and device acceptance remain later stages.

The [source-permission requirements](../data-licensing.md) remain open. The user
has authorized local engineering while feed delivery and distribution stay
gated; these technical results do not grant those permissions.
