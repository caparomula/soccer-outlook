# TIME-1: Local sports-day calendar

A sports day starts at 04:00 in the supplied IANA time zone. The interval is
inclusive at its start and exclusive at the next actual local boundary. Return
its local calendar date plus UTC start/end instants. Use local calendar
operations, not 24-hour durations.

This extends the existing P0 `SportsDayCalendar` ownership to the website's
`sportsDayStart`, `dayIndex` and `dayDate` heading boundary, plus the boundary
calculation used by Python's `story_state.window_end`. The source collection
calendar remains a separate publisher concern. Bucket selection, live freshness,
status interpretation, TBD fixtures and recommendation windows do not move in
this extraction.

## Decision inputs and outputs

`containing` receives a UTC instant and named time zone. It returns a date label
and UTC boundary pair. `index` compares two sports-day date labels by calendar
days. `shift` receives a reference UTC instant, zone and integer day offset and
returns the shifted local 04:00 boundary. Time-zone database data must be pinned
and identical in VM, compiled CLI and compiled browser execution. The shared
owner uses the package's full database (`latest_all`) so supported browser/ICU
zone aliases such as `Asia/Calcutta`, `Asia/Katmandu` and `Europe/Kiev` are not
rejected merely because the smaller database omits them. `UTC` is explicitly
supported. This compatibility correction is distinct from changing the 04:00
rule, and its compiled browser size must still pass the existing budget. No decision
reads a global clock or the machine's default zone.

```text
containing(instant, zone):
    local = instant expressed in zone
    label = local calendar date
    if local clock is before 04:00: label = preceding calendar date
    start = actual 04:00 boundary for label in zone
    if normalization placed start after instant:
        step to preceding actual boundary
    end = next actual 04:00 boundary
    return label, start as UTC, end as UTC

index(day, reference):
    return difference in calendar labels, not UTC boundary elapsed hours / 24

shift(referenceInstant, zone, offset):
    day = containing(referenceInstant, zone)
    shiftedLabel = day.label plus offset calendar dates
    return actual 04:00 boundary for shiftedLabel in zone
```

A zero shift means the current sports-day start, not the reference instant. The
heading date is derived from the shifted sports day, never from its first match.
Unknown zones and non-UTC/invalid instants are explicit input errors; do not fall
back silently to UTC or the host zone. A fixture with unknown kickoff cannot be
passed as an invented instant merely to obtain a heading.

## Independently authored examples

New fixed examples are in [sports-day-examples.json](sports-day-examples.json).
The [P0 cases](../../../tool/outlook_bridge/cases/sports_day.json) additionally
cover repeated autumn hours, half-hour offsets, London, Lord Howe, leap/year
boundaries and three-day indexing through DST.

| Case | Expected result; tempting wrong answer |
| --- | --- |
| New York, October 21, 03:59:59 local | October 20 sports day; midnight does not start October 21 |
| Same clock at 04:00 | October 21 sports day; start is inclusive |
| Same UTC instant, New York versus Los Angeles | Can produce different sports-day labels; source Eastern date does not decide viewer grouping |
| Shift March 7, 2026 forward one New York sports day | March 8 boundary is 08:00Z, only 23 elapsed hours after March 7's 09:00Z boundary |
| Shift October 31, 2026 forward one New York sports day | November 1 boundary is 09:00Z, 25 elapsed hours after October 31's 08:00Z boundary |
| March 7 to March 10 in New York | Index 3 although the elapsed boundary duration is 71 hours |
| Shift January 1, 2027 backward three UTC sports days | December 29, 2026 at 04:00Z; calendar arithmetic crosses years |

When a jurisdiction skips an entire local date, `shift` returns the actual date
and boundary obtained by local calendar normalization, never a fictitious date
label. Shifting Apia's December 29, 2011 sports day forward one calendar label
therefore returns December 31 at `2011-12-30T14:00:00Z`; December 30 did not exist
there. The [calendar tests](../../../packages/outlook_core/test/sports_day_test.dart)
cover this deliberate historical correction, supported aliases, and containing
lookup around the skipped date. These examples do not establish exhaustive
historical time-zone support. Current product verification centers on
contemporary supported viewer zones and recorded DST boundaries.

## Ownership and review

Owner: [`SportsDayCalendar`](../../../packages/outlook_core/lib/src/sports_day.dart)
and its calendar result type. Read-through consumers: compiled browser bridge,
publisher CLI and native callers. Browser grouping helpers and
[`story_state.window_end`](../../../story_state.py) obtain boundaries from the
shared owner; later bucket/status decisions consume them without recreating local
boundary arithmetic. The publisher's recommendation-window length and AI
eligibility rules remain in their existing owners for later P2 migration.

Python microsecond inputs are truncated to the bridge's millisecond precision,
never rounded across 04:00. October 21, 2026 at 03:59:59.999999 New York time is
still the October 20 sports day, so a four-day shift ends at October 24, 08:00Z.
At 04:00:00.000001 it is October 21, so the same shift ends at October 25, 08:00Z.
The [publisher-adapter tests](../../../tests/test_outlook_runtime.py) fix those
expected UTC boundaries independently.

Checks: fixed examples in VM, browser and compiled CLI, existing P0 calendar
cases, and affected schedule/carousel browser regressions. Review date: October
20, 2026. Fingerprint pending the explicit stamping workflow. Source-fetch date
coverage, live freshness and TIME-2 status behavior remain outside this rule.
