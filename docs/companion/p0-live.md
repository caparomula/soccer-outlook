# P0 foreground live transport findings

**Status: native transport/plugin feasibility passed; production live semantics
and source permission remain open.** Recorded October 20, 2026. The initial host
check was followed by dedicated [iOS](p0-ios.md) and [Android](p0-android.md)
debug shells. Each made one public GET and exercised local TLS plus real OS
lifecycle events. Both disposable targets were shut down after extraction.
No production Python/JavaScript path was changed.

## Existing behavior and boundaries

The website's [transport](../../web/live.js) groups requests by ESPN league and
scoreboard date. Its baseline is a 60-second polling interval, an eight-second
request timeout, and a window from 15 minutes before kickoff through four hours
after kickoff. A successful final-score catch-up is allowed until 30 hours after
kickoff. Failed catch-up requests do not consume that opportunity. All failed
batches use exponential backoff capped at ten minutes; requests within a batch
currently run together.

The browser skips new polls while hidden, and cancels/invalidates its active
batch on `beforeunload`/`pagehide`. It resumes after `pageshow`. This is a browser
baseline, not proof of native lifecycle behavior. In particular, the native
requirement to cancel on pause and reject a response for an obsolete
identity/kickoff is broader than copying these event handlers.

[Browser interpretation](../../web/app.js) currently lives in `liveStatus`,
`liveGoals` and `applyEvent`; Python has a separate `interpret`/`called_off`
implementation in [build.py](../../build.py). Their status choices belong in the
future shared pure source decoder. The spike only preserves raw wire fields, so
it does not introduce a third production decision owner.

## Reproducible artifacts and observed host result

[The probe](../../tool/companion_live_probe/README.md) contains a dependency-free
Dart HTTP operation, a cancellable session, deterministic offline checks, and a
small manual Flutter entrypoint and a reproducible automated native harness.

The [minimized real capture](../../tool/companion_live_probe/captures/2026-10-20-host.json)
comes from one successful credential-free GET to the UEFA Champions League
scoreboard for `20261020`. An initial sandbox attempt failed at DNS lookup;
the subsequent permitted network request completed. No automatic retries or
further host scoreboard requests were made. The later native requests are
accounted for separately below.

| Measurement | Observed value |
| --- | --- |
| Runtime | Dart 3.13.3 on macOS, not iOS or Android |
| Request began | `2026-10-20T18:38:19.904247Z` |
| Response received | `2026-10-20T18:38:20.108066Z` |
| HTTP status | 200 |
| Elapsed / body | 204 ms / 118,528 bytes |
| Scoreboard scope | 9 events; first 3 retained |
| HTTP cache metadata | `Date: Tue, 20 Oct 2026 18:38:20 GMT`, `Cache-Control: max-age=8` |
| Browser CORS header | `Access-Control-Allow-Origin: *` |
| Score observation timestamp | Unknown; receipt time is not substituted |

The retained events preserve ESPN event/competition/team IDs, kickoff, raw
status and scores:

| Event ID | Fixture | Kickoff UTC | Captured state / score |
| --- | --- | --- | --- |
| `401915403` | Fenerbahce–Slavia Prague | October 20, 16:45 | `post`, full time, 2–2 |
| `401915404` | Sabah FK–Borussia Dortmund | October 20, 16:45 | `post`, full time, 1–2 |
| `401915402` | AS Roma–Slovan Bratislava | October 20, 19:00 | `pre`, scheduled; wire placeholders 0–0 |

The pregame zeroes are wire evidence, not a played score. This response contains
final and scheduled examples; it does **not** prove a live-to-final transition
or an in-progress clock update. HTTP `Date` and cache lifetime describe transport
metadata, not when ESPN last verified an individual score. The capture contains
no cookies, tokens, betting links, editorial copy or personal preferences.

## Native continuation and limits

The offline Dart check passes for identity/status preservation, zero versus
missing scores, sanitized field retention, invalid date rejection, single-flight
checks, cancellation, resume, late-response rejection, synthetic timeout cleanup
and disposal. Static analysis passes for the library, CLI and tests. The Flutter
entrypoint was initially analyzed only. The continuation built and executed
the automated harness on iOS 26.5 and Android 16/API 36. Compiled deployment
floors are iOS 15 and Android API 24; those older runtimes were not executed.

Both targets passed native directory discovery, IANA zone lookup and SQLite
transaction/close/reopen. Both used normal system trust for the public GET,
then a scoped test certificate for an in-app stalled TLS server. They each
timed out, recovered, cancelled a pending request on OS backgrounding and
accepted exactly one replacement request after OS resume.

| Platform | Public request | Final local stalled timeout | Lifecycle |
| --- | --- | --- | --- |
| iOS 26.5 simulator | HTTP 200, 189 ms, 118,530 bytes | 8,021 ms | Old operation rejected; two requests total |
| Android 16/API 36 emulator | HTTP 200, 3,410 ms, 118,530 bytes | 8,013 ms | Old operation rejected; two requests total |

Initial harness/controller failures remain in the public-run records. Clean
local-only reruns completed the timeout/lifecycle evidence without another
public request. The [independent verifier](../../tool/companion_live_probe/verify_native_evidence.py)
checks both parts, event order and native identity. Platform reports retain
the raw minimized records, summary hashes, environment and reproduction steps.

| Required evidence | Current result |
| --- | --- |
| Public host transport | One successful Dart/macOS request |
| iOS foreground HTTPS, lifecycle and resume | Passed on the dedicated simulator |
| Android foreground HTTPS, lifecycle and resume | Passed on the dedicated emulator |
| Cancelled/obsolete batch cannot overwrite new result | Deterministic late-response test and native cancellation/resume pass |
| Real stalled-connection timeout | Passed on both native targets, followed by successful recovery |
| Missing scores and wire identity/status | Synthetic checks plus minimized real capture |
| Source score freshness and stale presentation | Timestamp uncertainty recorded; production policy/UI pending |
| Changed kickoff/identity and aggregate/live reconciliation | Not implemented by this transport spike; P3 acceptance work |
| In-progress, live-to-final and later correction sequence | Not captured by native runs; required before P2/P3 live behavior adoption |
| Physical devices, minimum-OS execution and release performance | Pending later device acceptance |

This closes the bounded native transport/plugin feasibility check. It does
not certify a production live adapter: the retained native wire corpus is
scheduled/final only, score observation time is unknown, and no source-age
threshold, stale UI or live correction ordering has been implemented. The
published-site capture includes in-progress row facts, but is not a temporal
live-response sequence. Preserve these separate P2/P3 acceptance obligations.

## Source-usage decision still required

Checked October 20, 2026: [ESPN Fan Support's terms page](https://support.espn.com/hc/en-us/articles/360035445091-Terms-of-Use)
directs readers to Disney's terms. The [US Disney terms](https://disneytermsofuse.com/english/),
dated May 24, 2024, include ESPN-branded products. Section 2.A describes limited
personal, noncommercial use; section 2.B.x restricts automated access and
extraction. A working public endpoint and permissive CORS header do not establish
permission for a distributed third-party companion. This investigation found no
current official grant or published service-level/rate-limit commitment for the
specific `site.api.espn.com` scoreboard path.

The user explicitly chose **"Leave distribution gated; continue local
engineering"**. Source permission therefore remains a distribution gate.
Before distributing direct ESPN access as a supported client capability,
record applicable permission/licensing or revise the source and its operating
limits. A hosted proxy is not an automatic workaround. This decision is
recorded in [P0 engineering decisions](p0-decisions.md).

## Next bounded checkpoint

Proceed with the local P1 exporter. P2/P3 must add the shared source decoder,
freshness and ordering rules, and bounded real in-progress/transition evidence
before adopting production live behavior. Preserve uncertainty when source
timestamps are absent; never promote receipt time to source observation.
Distribution stays gated while that engineering continues.
