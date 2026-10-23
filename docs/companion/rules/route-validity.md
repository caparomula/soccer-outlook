# WATCH-VALIDITY-1: Temporal validity of qualified viewing routes

Decide whether an already-qualified listed or usual route remains usable at an
explicit evaluation instant. The same decision precedes provider intersection
and route preference in the publisher and browser. A retained observation keeps
its original age and cutoff when copied, downloaded or served again.

This is a bounded part of WATCH-1 through WATCH-3 in
[the specification](../specification.md) and the route evidence requirements in
[the data contract](../data-contract.md). It does not qualify sources, reconcile
rejections, establish package membership, infer live versus replay coverage,
validate destinations, determine match status or create a usual route. Those
owners remain upstream. P2 remains incomplete after this family.

## Explicit inputs and outputs

The version 1 `route_validity` operation accepts `cases`. Every case contains
all fields below; a nullable fact is explicitly `null`, not an invented date.

| Field | Meaning |
| --- | --- |
| `id` | Case identity, echoed in the result |
| `now_utc` | Evaluation instant supplied by the caller |
| `basis` | `listed`, `rule` for a qualified usual route, or `none` for unknown evidence |
| `retained` | Whether this listed route uses retained evidence |
| `verified_at` | Original verification instant, or `null` |
| `expires_at` | An already-supplied cutoff that may shorten validity, or `null` |
| `kickoff_utc` | Confirmed fixture kickoff, or `null` |
| `rights_until` | Last inclusive Eastern civil date for a usual route, or `null` |
| `retention_seconds` | Integer retention allowance from 0 through 21600 seconds |

UTC instants use a `Z` suffix and zero through six fractional second digits.
Dates use a real Gregorian `YYYY-MM-DD` date. Invalid shapes, non-UTC instants,
impossible dates, unsupported basis values and out-of-range retention allowances
are protocol errors; they are not usable-route decisions. Original microseconds
must survive parsing, arithmetic, comparison and serialization in VM, native
and compiled browser execution.

Each result is `{id, usable, reason, expires_at}`. `reason` is `null` for a usable
route. `expires_at` is the effective cutoff, including when it has passed; it is
`null` if no cutoff applies or required validity inputs cannot establish one.
Returned UTC values use three fractional digits when microseconds are divisible
by 1000, and six otherwise, matching the bridge's existing UTC representation.

The independently authored [examples](route-validity-examples.json) contain
literal expected responses in the existing envelope:
`{version: 1, operation: "route_validity", cases: [...]}` returns
`{version: 1, results: [...]}`. Expectations were authored before replacement of
the core or consumers and are not captured implementation outputs.

## Decision order and reasons

The order below also fixes the diagnostic when several facts are contradictory.

1. `basis: none` returns false with `unknown_basis`, regardless of other facts.
   Missing evidence establishes no viewing route and is not proof that nobody
   broadcasts the fixture.
2. A retained route with basis other than `listed` returns false with
   `invalid_retained_basis`.
3. A retained listed route requires `verified_at`. Absence returns false with
   `missing_verification`; verification later than `now_utc` returns false with
   `future_verification`. Verification exactly at evaluation time is allowed.
4. A retained listed route also requires `kickoff_utc`; absence returns false
   with `missing_kickoff`. Its cutoff is the earliest of original verification
   plus `retention_seconds`, kickoff plus four hours, and an explicit
   `expires_at` when supplied. An earlier supplied cutoff cannot be extended.
5. A usual route requires `rights_until`; absence returns false with
   `missing_rights_until`. Its cutoff is the start of the following civil date
   in `America/New_York`, shortened by an explicit `expires_at` when supplied.
   This uses midnight, not the four-o'clock sports-day boundary. Daylight-saving
   transitions are resolved by that named zone.
6. A fresh listed route uses its explicit `expires_at` when supplied; otherwise
   it has no temporal cutoff in this family. There is no new universal fresh
   listing TTL. Its `verified_at` and kickoff do not create retention limits.
7. Every cutoff is end-exclusive: `now_utc < expires_at` is usable;
   `now_utc >= expires_at` returns false with `expired`. A route without a
   cutoff is usable after the preceding checks. No required-input error returns
   a provisional expiry; its `expires_at` remains `null`.

The usual route's `checked` or `verified_at` describes verification, not an
effective contractual start. This extraction preserves the current Eastern
evaluation-date `until` rule. It does not compare fixture kickoff with the rights
period or add a start-date gate. A future fixture beyond `rights_until` can
therefore still have a usual route while the evaluation date is in range. A
different policy would need an explicit product decision and separate examples.

## Composition and adapter responsibilities

Browser `data-o` and `data-r` records carry a `t` object with the seven validity
fields other than `id` and `now_utc`: `basis`, `retained`, `verified_at`,
`expires_at`, `kickoff_utc`, `rights_until` and `retention_seconds`. The route
selection input passes this object as `validity`, with a top-level `now_utc`.
A listed candidate's validity basis must be `listed` or `none`; a usual
candidate's must be `rule` or `none`. A conflicting location/basis is an invalid
protocol input, not a route the preference algorithm can silently reinterpret.

Only temporally usable candidates reach the existing provider intersection and
preference rule. An expired candidate supplies no selected providers or access.
A second independently qualified, unexpired candidate can still win. An
explicitly supplied, valid usual candidate can be considered after listed
candidates fail. This owner never synthesizes that fallback when none was
supplied. Existing production suppression of a usual route when listed outlets
exist is outside this extraction.

Clock advancement must refresh validity before downstream fixture eligibility,
strip controls, recommendations and route disclosures use the result. Cache
keys or expiry scheduling must include the effective temporal decision. Serving
the same snapshot again must not rewrite `verified_at`, extend `expires_at`, or
use the receipt time as evidence freshness.

The final publisher page, facts and export use their supplied `built_at` as this
evaluation instant. The existing `--date` option may influence earlier fixture
collection and usual-route qualification, but cannot revive coverage already
expired at `built_at`. This final gate uses one caller-supplied clock; it does
not sample a second current time inside the rule.

Retained-source admission still checks fixture and team identity, exact kickoff,
country, source outcome, live-listing classification and replay/rejection facts
before this temporal owner runs. An unrelated missing source does not negate a
qualified independent positive route; an explicit rejection does not become a
transient outage that can revive old evidence. This operation receives qualified
facts and cannot reconstruct those decisions from absent diagnostic records.

Current producer aggregation is preserved: a channel's verification is the
oldest accepted observation, and its retained flag is true if any contributing
evidence is retained. The adapter must not pick a newer observation to extend
that route's cutoff. Stable fixture/network route identity and evidence links
remain producer responsibilities; validity does not issue new identities.

Destination URL qualification and live-only link visibility remain with their
existing owners. Temporal validity is necessary for a route, not proof that a
URL is safe, that a selected package includes it, or that a replay is live.

## Intentional change and preserved uncertainty

The legacy Python retention admission accepts an observation exactly six hours
old, while its exported cutoff already represents that six-hour endpoint. This
family makes the endpoint consistently unusable. The one-microsecond-before,
at, and after examples isolate that change; browser expiry must also stop access
without waiting for a newly downloaded snapshot.

The Python retention adapter clamps its legacy `max_age_hours` option to zero
through six hours and rounds down to whole seconds for this protocol. The
production six-hour allowance is unchanged. A custom subsecond allowance may
therefore expire earlier by less than one second; it can never extend retention.

Missing verification and kickoff on retained routes, unknown basis, and invalid
retained usual routes fail closed. These synthetic defensive examples are
separate from a claim that normal producers currently emit those contradictions.
Fresh listing age policy, contractual start dates, fixture-period rights,
cross-source rejection reconciliation and full source/destination qualification
remain outside this increment. They must not be inferred from these examples.
