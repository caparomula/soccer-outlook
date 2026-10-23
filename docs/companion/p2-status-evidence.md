# P2 status/result code-review checkpoint

The bounded `STATUS-ESPN-1` increment is ready for code review after local
validation on October 23, 2026. One pure Dart data decoder now supplies ESPN
status and ordinary match-score meaning to the publisher and browser. This
extends the [fixture-window checkpoint](p2-window-evidence.md); P2 and full
TIME-2 observation-freshness work remain incomplete.

The [rule](rules/espn-status.md),
[independent examples](rules/espn-status-examples.json) and
[validation record](evidence/p2-status-validation-2026-10-23.json) define the
boundary and bind the tested sources, runtime, reports and logs.

## Owner and consumers

The narrow [`espn_decoder.dart`](../../packages/outlook_data/lib/espn_decoder.dart)
entry point exports immutable source facts without HTTP, storage, platform or
clock dependencies. `decodeEspnStatus` selects the competition status object,
falling back to the event object without merging fields. `decodeFixtureStatus`
handles previously extracted or saved facts. Both share classification and
score parsing. The version 1 `espn_status` and `fixture_status` bridge operations
serve the VM, compiled publisher CLI and browser.

The shared result distinguishes canonical status, placement phase, original
provider phase, display label, played/final/void judgments, parsed reported
scores and scores suitable for ordinary live/final presentation. A `post`
placement alone cannot establish a final result. Unknown remains unknown and
can use estimated schedule placement without implying confirmed progress.

Python extraction, static HTML, companion feed export, discovery and metrics
read those shared facts. Metrics retains raw status/score evidence separately;
its version fingerprint now includes the shared source owner. Existing source
qualification, rating formulas, kickoff confirmation and goal-event parsing
retain their separate owners. The shared played judgment gates scorer
presentation, and only a shared live result activates a live-only destination.
Independently valid viewing routes remain available when progress is unknown.

Browser hydration batches normalized rows and validates every response before
installing any pending result. Live updates call the raw decoder; a status-kind
change within `post` still updates cards and result claims. Schedule replacement
uses the same normalized seam, preserving accepted newer live observations under
the existing receipt/carry behavior. It does not implement P3 correction ordering.

## Saved facts and review repairs

Canonical state is carried separately from display text in `data-status-kind`
and the internal Python `Match.status_binding`. A binding retains the decoded
state and original provider phase while its public phase/label still match.
Score changes are reparsed; phase or label changes invalidate reuse. Private
projection caches include every supplied fact and return fresh decoded values.
New context JSON preserves the small binding; legacy contexts infer only their
available normalized facts and cannot recover a lost original provider phase.
Read-through export leaves the caller's public dataclass fields unchanged.

Independent review reproduced a lossy round trip: an accepted live observation
with display clock `FT` or `Postponed` could become unknown or postponed after
hydration. The optional canonical-state seam now validates phase consistency
and treats the bound label as display. Independent examples were extended
before implementing that repair. Tests also reproduced and repaired static
live-only links on contradictory legacy rows, scorer visibility on void/unknown
records, stale metrics provenance, and mutation of a public binding during
export. The reports retain the failing-first logs and resolved finding.

The Python status adapter retains only request keys at most 16 KiB in its bounded
LRU. Larger valid frames still go unchanged to the bridge without caching; no
source fields are silently filtered. Runtime source fingerprints now include
the data decoder, so a stale compiled owner is rejected.

## Preserved answers and intentional corrections

The frozen baseline contains 360 retained real fixtures and nine synthetic
legacy defects. The ordinary captured cases retain their answers. The synthetic
corrections cover cancellation abbreviations, pre-phase postponement, abandonment,
unfamiliar or empty terminal status, absent status, object/zero scores,
out-of-contract scores and contradictory final/cancelled identity. Original
answers remain evidence of previous behavior, not the correctness oracle.

The independent 18-group corpus contains 67 literal decisions covering those
cases plus metadata precedence, contradictory completion flags, malformed source
values, score boundaries, halftime, display text and canonical-state persistence.
The decoder deliberately recognizes a bounded name/token table. Unfamiliar
explicit names remain unknown; this is not coverage of every ESPN status variant.

## Validation

| Check | Result and scope |
| --- | --- |
| Dart packages | 49 data tests and 128 core tests pass; three analyzers are clean; 25-file formatting check makes no changes. Data includes 18 independent status groups plus direct decoder and existing feed-reader tests. |
| VM protocol | 96 independent groups, 51 malformed semantic frames and two malformed transport frames, with recovery; valid 1,000-case window/status batches; invalid one-shot input exits 65. |
| Four-target parity | VM, compiled CLI, Chromium and WebKit pass 21 request groups: 67 literal decisions, 360 preserved legacy decisions, nine declared corrections and the unknown-window seam. Each target rejects eight additional malformed requests. |
| Generated pages | Five lifecycle groups per engine cover static void/unknown/final/TBD, raw live updates, both final/void replacement directions, unknown-route preservation and no-JavaScript fallback. Scorer gates, legacy markup cleanup and raw live display-label collisions run through the production renderer and SRI runtime. |
| Python consumers | 160 focused tests pass, including 17 status-adapter methods and the independent literal corpus. Regressions cover export immutability, JSON context, mutation invalidation, raw metrics evidence and cache bounds. Retained failing-first logs document export immutability, context binding, metrics fingerprint and cache-bound defects. |
| Independent browser adapter review | 23 extracted production-function probes use the actual compiled runtime. Batch alignment/atomicity, same-phase status changes, score/scorer clearing, receipt carry and the resolved display-label defect pass. These probes are separate from actual browser rendering. |
| Regressions | 760 Python tests; 112 Chromium tests, 40 WebKit smoke tests and 11 schedule-refresh tests per engine, totaling 174 browser regressions. Existing route/calendar, eligibility, route-validity and fixture-window verifiers pass on the rebuilt runtime. |
| Offline builds | Full and lightweight builds replay 360 captured fixtures, preserving original ages and required catalog/schedule/policy bytes. Both pass the independent Dart reader; publication revisions advance from 1 to 2. |
| Budgets | Browser runtime: 198,162 gzip bytes against 204,800, an increase of 2,541 with 6,638 bytes remaining. Replayed page: 2,777,522 bytes against 3,300,000. All 448 embedded logos remain. |

Reports: [core](evidence/p2-status-core-2026-10-23.json),
[protocol and browser](evidence/p2-status-2026-10-23.json),
[Python](evidence/p2-status-python-2026-10-23.json),
[independent web review](evidence/p2-status-web-review-2026-10-23.json),
[route/calendar](evidence/p2-status-route-calendar-2026-10-23.json),
[eligibility](evidence/p2-status-eligibility-2026-10-23.json),
[route validity](evidence/p2-status-validity-2026-10-23.json),
[fixture windows](evidence/p2-status-windows-2026-10-23.json) and
[build replay](evidence/p2-status-replay-2026-10-23.json).

The portable [baseline](../../tests/contracts/p2/legacy-espn-status-2026-10-23.json)
and [original browser fragment](../../tests/contracts/p2/legacy-espn-status-functions-2026-10-23.js)
are hash-bound before migration. Final validation also binds the retained source
ledger, capture runner, original dirty snapshot and prior compiled runtime.
Five verifier integrity/mutation tests check the portable proof. An intermediate
page-verifier attempt failed before browser startup because its restored test
fixture still held outlet dictionaries. The failed log is retained; the fixture
now reconstructs outlets as the real context reader does. The final successful
attempt uses the corrected verifier. Earlier candidate passes are historical;
the final acceptance record binds the repaired sources and runtime.

## Limits and next review

Strict CSP is tested on the protocol page; the generated website retains its
existing inline script/style model. Synthetic inputs test behavior without
opening provider destinations. The real capture contains scheduled, live and
ordinary final records, with string scores; it has no exceptional terminal,
TBD, object-score, shootout/aggregate or conflicting-status examples. Literal
and generated-page cases supply those bounded decoder checks. This work neither
adds shootout/aggregate mapping nor changes scorer parsing.

Receipt time remains distinct from provider observation time. No source freshness
is inferred from receipt, display clock, kickoff or successful decoding. Existing
minute/lifecycle triggers remain; there is no new deadline timer or native
live-overlay reconciliation. Native product integration, persistence, physical
and minimum-OS devices, and release performance are later gates.

The 234-file dirty starting state and prior runtime were archived and verified.
HEAD remains `6200140feb9b3fe63b2609d09340355c6458edd0` on
`companion/implementation`, with an empty index. No commit, push, deployment,
remote workflow, new source collection or live AI call occurred. Compact reports
are retained here; local traces and build artifacts remain under ignored `work/`.

This checkpoint is ready for code review before continuing with source-observation
freshness, remaining source qualification, ranking, carousel/pick selection and
editorial lifetimes. The [implementation plan](implementation-plan.md) records
the remaining P2/P3 boundaries. The user's authorization covers local engineering;
[source permission](../data-licensing.md), feed delivery and distribution remain
gated.
