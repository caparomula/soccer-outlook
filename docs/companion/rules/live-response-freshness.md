# LIVE-FRESHNESS-1: Bound current-live claims by response receipt

The user selected a three-minute response timeout on October 23, 2026. An
accepted in-progress response can support a current-live claim for less than
180 seconds after its original receipt. At exactly 180 seconds it is expired.
This is a response-recency policy, not proof of when the provider observed or
updated the score. Those source timestamps remain independently unknown.

This bounded TIME-2 rule composes with [STATUS-ESPN-1](espn-status.md) and
[TIME-WINDOW-1](fixture-windows.md). It neither reinterprets status nor changes
the existing window arithmetic. [SOURCE-PROVENANCE-1](source-provenance.md)
supplies the accepted response receipt without renewing it on reuse.

## Inputs, owner and protocol

The pure core owns the fixed version 1 timeout of 180 seconds. Publisher and
browser adapters consume its decision. No caller supplies another default or
handwritten comparison, and no system clock is read inside the rule.

The version 1 `live_freshness` operation takes complete envelopes:

```text
{version: 1, operation: "live_freshness", cases: [
  {id, now_utc, source_state, received_at}
]}
```

Every field is required. `received_at` is nullable; the other timestamp is a
required UTC instant. `source_state` is the already decoded placement phase
`pre`, `in`, `post` or `unknown`, not provider display text. UTC grammar and
calendar validation use the existing bridge boundary and preserve up to six
fractional digits. Invalid states, timestamps and envelopes are protocol
errors. Existing request-size, batch-size, duplicate-ID and recovery rules
continue to apply.

Each result is:

```text
{id, live_now, last_known, reason, expires_at, next_transition_at}
```

Output timestamps use the bridge's existing UTC ISO representation.
`last_known` describes a previously accepted in-progress report that cannot
currently support a live claim; it does not mean a score is necessarily present.
The decision does not change, fill or erase scores. `expires_at` exposes the
accepted response's exclusive end where calculable. `next_transition_at` is a
future instant at which this rule's result can change without another response.

## Decisions

Apply these checks in order:

1. For any phase other than `in`, return `live_now: false`, `last_known: false`,
   `reason: not_in_progress` and null deadlines. A newly received scheduled,
   final, exceptional or unknown status does not become live.
2. For `in` with no receipt, return `live_now: false`, `last_known: true`,
   `reason: missing_receipt` and null deadlines. Publication or render time is
   not a fallback receipt.
3. For `in` with a receipt after evaluation, return `live_now: false`,
   `last_known: true`, `reason: future_receipt`. Expiry is receipt plus 180
   seconds; the next transition is the supplied receipt instant. A future
   receipt does not establish current recency before the clock reaches it.
4. For `in` with `received_at <= now_utc < received_at + 180 seconds`, return
   `live_now: true`, `last_known: false`, `reason: null`. Both deadlines are
   receipt plus 180 seconds.
5. Otherwise return `live_now: false`, `last_known: true`, `reason: expired`,
   the original expiry, and no next transition. The report remains expired
   until a new accepted response supplies another receipt.

Comparison and arithmetic retain microsecond precision across VM, native and
browser targets. The independent [literal examples](live-response-freshness-examples.json)
were authored before core implementation and consumer replacement. They cover
both sides of each cutoff, unknown/future receipts, non-progress states and
repeated evaluation of the same accepted receipt.

## Presentation composition

Keep canonical source status, accepted score and reported clock unchanged. For
an in-progress report with `last_known: true`, keep any known score and clock
visible with a clear **Last known** qualifier. Never advance the clock or infer
a final result from age. A missing score remains missing.

Only `live_now: true` supports unqualified Live/Live now claims, current-live
badges, current-live summary counts, carousel live grouping and live-only
viewing destinations. The same decision must govern schedule rows, expanded
details, carousel cards, picks and link menus. Expiry does not remove otherwise
valid viewing routes or change service/league eligibility.

An in-progress fixture whose window bucket is `live` but whose current-live
decision is false uses its calendar-day presentation bucket: `today` for index
zero, the existing previous-day buckets for negative indices, and the existing
future-day buckets for positive indices. Use `today`, not `earlier`, for a
same-day last-known report so that loss of response recency does not imply a
completed result. Keep the pure window decision, focus arithmetic and rating
values intact. Other surface-specific candidate rules remain their owners;
this increment adds no new ranking formula or editorial lifetime policy.

Scheduled/unknown fixtures retain their existing pending/awaiting-status
presentation and window placement; passing kickoff still cannot confirm live
play. Finished or exceptional status keeps its decoder meaning. This timeout
does not turn an old final into an in-progress or unknown result.

## Reevaluation, reuse and limits

The application reevaluates at the earliest relevant `next_transition_at`, on
accepted responses, schedule installation and foreground/resume. A still-open
page must stop its current-live claim at the boundary without depending on a
later poll or user action. Timer delays and suspension require evaluation at
the actual current instant when execution resumes; missed wakeups do not renew
the receipt. The pure operation remains deterministic for its supplied clock.
The production browser samples one actual UTC wall-clock instant per freshness
batch, matching the clock used to record response receipt. The `#at-...`
calendar preview does not pin this safety decision or prolong current-live
claims; existing schedule-window and route-preview clocks remain unchanged.
If the wall clock moves behind a supplied receipt, the future-receipt rule
applies until the wall clock reaches that receipt again.

Reload, redraw, republish, successful responses omitting the fixture, failed or
cancelled requests, and reapplication of a retained overlay do not renew the
accepted fixture receipt. A newly accepted response can restore current-live
presentation even if its score is unchanged. Poll interval, network timeout,
retry/backoff, request eligibility and source permission are unchanged by this
three-minute presentation timeout.

Acceptance requires core boundary tests and VM/native/browser parity, plus
production-page checks for cutoff without another fetch, retained score/clock,
live-only link removal and restoration, missing/future receipts, cached reload,
snapshot overlay reuse and resume. This local evidence does not establish
provider observation freshness, live correction ordering, native persistence,
device acceptance or distribution permission.
