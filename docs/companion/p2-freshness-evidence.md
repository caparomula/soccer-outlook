# P2 response-freshness review checkpoint

The user approved a three-minute response timeout on October 23, 2026. This
increment implements original per-fixture receipt provenance and bounded
current-live presentation. It follows local commit `6f04518`; the new increment
remains uncommitted for review. No provider/AI request, deployment or push was
performed. Distribution remains gated.

The [provenance rule](rules/source-provenance.md) has 16 independent lifecycle
examples. The [freshness rule](rules/live-response-freshness.md) has eight groups
with 21 literal decisions, authored before implementation. The
[validation record](evidence/p2-freshness-validation-2026-10-23.json) binds the
tested files, reports and logs.

## Owners and behavior

`build.fetch_scoreboards` records receipt immediately after each worker's
transport returns, before parsing and ordered main-thread validation. Receipt
and event follow the existing merge winner together. `Match.source_received_at`
survives context, HTML, schedule fragments, status and scoreboard-vitals export.
Unknown legacy/direct receipts remain null. League validation, publication and
standings provenance remain distinct; neither source observation nor provider
update time is invented.

Pure `outlook_core.evaluateSourceFreshness` owns the 180-second exclusive
deadline. The versioned `live_freshness` operation serves the native publisher
adapter and browser. The browser evaluates an actual supplied wall-clock instant;
calendar previews cannot freeze this decision. A missing, future or expired
receipt cannot support a current-live claim. Original source status, score,
clock, rating values and fixture-window arithmetic remain intact.

The browser labels retained in-progress reports **Last known**, moves them out
of the Live now presentation group and disables live-only destinations. It
reevaluates at the exact deadline and on resume, without waiting for another
poll. Ordinary viewing routes remain available. Static HTML retains live-only
destination metadata without emitting an anchor whose lifetime it cannot
enforce. Accepted response receipts survive overlay reapplication; missing,
failed, malformed or cancelled updates do not renew them.

Semantic status/freshness changes now update all surfaces once per accepted
scoreboard response. An
unrelated pending league response cannot prolong a Live badge, stale player
link or carousel grouping after an accepted final. The existing finished-opening
selection rule runs at that point. Receipt-only updates do not redraw; clock/scorer updates retain
keyboard focus; batch completion does not repeat an already-consumed redraw.

## Validation and review

| Check | Evidence |
| --- | --- |
| Python | 805 tests pass in the isolated companion-only Python 3.13 environment; 25 focused provenance/freshness tests and nine workflow checks pass. No global or user site packages are used. |
| Shared core | Recompiled with pinned Dart 3.13.3, with the rule/owner unchanged. All 21 literal freshness decisions agree across VM, compiled CLI, Chromium and WebKit; all targets reject 15 malformed frames and recover. |
| Generated pages | 23 checks per engine cover freshness, response batching, memoization, last-known headlines/grouping and canceled navigation, using freshly rendered external-runtime pages with blocked network fallback. |
| Browser regressions | 112 full Chromium checks, 11 schedule checks, 46 focused checks across Chromium/WebKit, and one external-runtime capture check pass: 170 checks. |
| Existing rule proofs | Fixture-window and route-validity proofs pass all four targets; all 453 frozen window answers remain intact. |
| Offline builds | Full replay consumes 375 retained requests for 360 fixtures. Only 1,080 legacy status/vitals receipt fields and the derived revision migrate. Lightweight replay consumes 349 requests, advances publication 1 to 2 and preserves every artifact byte. |
| Runtime budget | The compiled browser runtime remains 198,552 gzip bytes against 204,800. |

All 15 review items A1–F2 are addressed. Every behavior regression was confirmed
failing before its fix; F2 documents and names the unchanged synthetic receipt
default. No sentence in either rule document or either independent example file
changed. Source/receipt acceptance is atomic for malformed scorer data, flags
come from retained facts, and freshness caches facts/deadlines while rechecking
all rows after a backward clock step. `beforeunload` recovers its timers when the
page remains open; only `pagehide` latches suspension.

Chromium measurements used three real 25-event responses on a 300-row page:
25 rebuilds became one; median main-thread task time fell from 2,516.32 ms to
138.772 ms. Across 500 advancing-clock refreshes of the retained 360-row page,
mean time fell from 1.1852 ms to 0.0204 ms, with 500 core-query batches becoming
zero. The final median was below the approximately 0.1 ms clock resolution;
the reported mean includes all 500 calls. These are local measurements, not cross-machine performance budgets.

Independent review passed 11 Chromium checks (including three additional edge
probes); a separate review passed three more. The
[independent browser review](evidence/p2-freshness-review-2026-10-23.json) binds
the final adapter and regression sources. The local per-item report and verbatim
command output, including failures and harness corrections, are retained at
`work/freshness-review-2026-10-23/report.md` and `transcript.md`.

Reports: [core parity](evidence/p2-freshness-core-2026-10-23.json),
[offline replay](evidence/p2-freshness-replay-2026-10-23.json), and the validation
record above. CI runs the new checks with dependencies installed beforehand;
hosted Actions were not executed here.

## Limits and next work

This timeout bounds response recency, not the age of the underlying provider
facts. The old capture predates worker receipt tracking, so replay keeps those
receipts unknown instead of manufacturing timestamps. Its fixtures contain no
live-only destination; synthetic browser cases establish that behavior. The old
capture is explicitly reconciled as legacy evidence. Current unknown-receipt
outputs are compared against that reference (including the already introduced
route-lifetime metadata), and deliberately fail strict nonlegacy reconciliation.
New captures must supply a recorded worker receipt for every raw fixture;
worker-recorded replay permits no receipt migration.

Full source qualification, ranking/selection ownership and editorial lifetimes
remain P2 work. Native persistence, identity/correction ordering, physical-device
acceptance and distribution are separate later gates. This checkpoint adds no
polling-cadence change, live data permission or production-readiness claim.

## Regenerate the retained review evidence

Run the commands in [Development](../development.md#test-changes) with the pinned
runtime, using new output directories so prior attempts remain available. This
workspace used isolated `work/review-fixes-unit-2026-10-23` and
`work/review-fixes-clean-2026-10-23` environments in place of `.venv`; the full
commands and output are in the transcript. The retained offline drivers are:

```sh
PUB_CACHE="$PWD/work/pub-cache" work/review-fixes-unit-2026-10-23/bin/python work/freshness-review-2026-10-23/replay/run.py --out work/freshness-review-2026-10-23/replay/run-2
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright" work/review-fixes-clean-2026-10-23/bin/python work/freshness-review-2026-10-23/presentation/review_group_a-final.py
work/review-fixes-unit-2026-10-23/bin/python work/freshness-review-2026-10-23/finalize.py
```

The finalizer verifies final source hashes, unchanged pre-existing files, rule
files, test counts and successful reports before copying the core/replay/review
reports and generating the validation record under `docs/companion/evidence/`.
Those JSON files are generated, never manually amended. Local retained captures
and drivers under `work/` are required to reproduce this particular historical
replay; the committed tests and public verifier tools use synthetic inputs.
