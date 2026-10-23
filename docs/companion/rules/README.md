# Shared decision rules

This catalog begins P2 on October 20, 2026. Its implemented families select among
**already-qualified** viewing routes, calculate local sports-day boundaries and
project saved league choices into viewer eligibility, enforce qualified route
lifetimes, place normalized fixtures into focus and schedule windows, and decode
bounded source status/result facts, and expire current-live claims using original
response receipt. Full source qualification, source observation freshness, recommendation and
editorial lifetime decisions remain P2 work.

| Stable ID | Rule | First migration boundary |
| --- | --- | --- |
| WATCH-ROUTES-1 | [Qualified viewing-route selection](watch-routes.md) | Preferred listed/usual service and ordered selected providers |
| WATCH-VALIDITY-1 | [Qualified route temporal validity](route-validity.md) | Retained evidence deadlines, explicit expiry and inclusive Eastern usual-rights end dates |
| TIME-1 | [Local sports-day calendar](sports-day.md) | Containing sports day, calendar-day index and shifted heading boundary |
| TIME-WINDOW-1 | [Fixture focus and schedule placement](fixture-windows.md) | Supplied-clock focus/bucket decisions and bounded accepted live-receipt age |
| STATUS-ESPN-1 | [Source status and reported match scores](espn-status.md) | Shared raw/normalized status decoding, score applicability and final-result verdict |
| SOURCE-PROVENANCE-1 | [Accepted response receipt](source-provenance.md) | Per-fixture publisher/browser receipt preserved through context, HTML, feed and overlay reuse |
| LIVE-FRESHNESS-1 | [Current-live response timeout](live-response-freshness.md) | Shared three-minute deadline, last-known presentation and live-only destination gate |
| VIEWER-ELIGIBILITY-1 | [League choices and fixture visibility](viewer-eligibility.md) | Follow/pause/solo projection, strip membership, common fixture and calendar-editorial gates |

The IDs are stable; retired IDs will not be reused. TIME-1 retains the ID already
used by the P0 calendar spike and [product requirement](../specification.md).
The input/output examples in [route-examples.json](route-examples.json) and
[sports-day-examples.json](sports-day-examples.json) were written from the rules
and calendar arithmetic before replacing consumers. They are fixed expected
answers, not output captured from either implementation. P0's independent
[calendar examples](../../../tool/outlook_bridge/cases/sports_day.json) remain
additional evidence.

[Eligibility examples](eligibility-examples.json) independently specify the
second family before its consumer replacement. Eligibility consumes the window
classification supplied by TIME-WINDOW-1; it does not repeat time-bucket policy.

[Validity examples](route-validity-examples.json) were authored before the
temporal consumers changed. They cover exact microsecond cutoffs, unchanged
original ages, Eastern midnight and the existing evaluation-date rights policy.
The validity gate runs before provider intersection and viewer eligibility.

[Window examples](fixture-window-examples.json) were authored before replacing
the browser's focus and bucket decisions. They preserve existing placement
boundaries and distinguish the supplied-clock and future-receipt corrections.
Receipt recency does not establish provider observation freshness. The subsequent
status family adds an explicit unknown placement state without treating it as live.

[Status examples](espn-status-examples.json) independently specify raw-source
and normalized-fixture seams before replacing their consumers. Ordinary captured
results establish parity; separate literals cover exceptional/conflicting status
signals, missing status, score objects and numeric zero. This bounded decoder
does not map shootout/aggregate fields or establish observation freshness.

[Provenance examples](source-provenance-examples.json) distinguish response
receipt from validation and publication. [Freshness examples](live-response-freshness-examples.json)
independently specify the user-approved timeout, including exact microsecond
boundaries, missing/future receipts and unchanged observations on reuse. These
rules preserve unknown source age and do not extrapolate score or clock.

[Later-family boundaries](later-families.md) records behavior inspected while
choosing the first extraction. Its league projection is now covered by
VIEWER-ELIGIBILITY-1; its focus/bucket projection is now covered by TIME-WINDOW-1,
and its exceptional-status findings by STATUS-ESPN-1.
The remaining notes separate current behavior from defects or changes
that need their own tests. Those notes are not a second production owner and do
not authorize broadening this extraction.

Domain rules live in pure [`outlook_core`](../../../packages/outlook_core/).
Source status decoding lives in the narrow pure
[`outlook_data` decoder](../../../packages/outlook_data/lib/espn_decoder.dart).
The compiled browser
bridge and publisher CLI are read-through adapters. Migration evidence must name
the implemented symbols, consumers removed, tests run and remaining consumers;
this catalog alone does not certify that migration happened. Catalog updated
October 23, 2026. No owner fingerprint is stamped until the review/stamping
workflow exists, as required by [engineering guidance](../engineering.md).
