# STATUS-ESPN-1: Source status and reported match scores

Decode bounded ESPN status/result facts once, before publisher and browser
presentation. This implements part of TIME-2; provider-observation freshness,
live-overlay reconciliation and full P2 remain incomplete. No evaluation clock,
kickoff, receipt time, TV listing or viewing route participates in this decoder.
Passing kickoff cannot create source progress or a result.

## Boundary and protocol

Version 1 exposes two operations with complete request/response envelopes:

* `espn_status`: each case is `{id, event_status, competition_status,
  home_score, away_score}`. Select a nonempty JSON object `competition_status`;
  otherwise select an object `event_status`, including an empty object. Otherwise
  there is no usable status object. Never merge fields between the two objects.
* `fixture_status`: each case is `{id, phase, label, home_score, away_score}`.
  An optional `state` field carries a previously decoded canonical status.
  This is the shared seam for direct or rehydrated publisher `Match` values.
  Its phase and display label are already extracted facts; it uses the same
  classification and score parser, rather than a Python interpretation fallback.

Every listed field except `state` is required; nullable/arbitrary JSON values are allowed for the
source fields. Malformed source values produce unknown facts or absent scores,
not a malformed protocol envelope. Missing/extra case fields, duplicate IDs,
unsupported operations and other framing violations remain protocol errors.
The existing bridge limits still bound request bytes, case count and identity.

For `fixture_status`, absent or null `state` retains the legacy phase/label
classification below. A supplied canonical state instead supplies identity:
`scheduled` requires `pre`, `live` requires `in`, `finished` and each exception
require `post`, and `unknown` requires `post` or `unknown`. A non-null unsupported
state or inconsistent phase yields unknown facts, not a protocol error. In this
explicit-state seam the label is display only and cannot establish or contradict
identity. In particular a live clock text of `FT` or `Postponed` remains live.
Live preserves its bounded nonempty label (otherwise `Live`); all other states
use their canonical labels. Scores retain the same parsing and played gate.

Both operations return `{id, state, phase, provider_state, label, played, final,
void, reported_scores: {home, away}, scores: {home, away}}`.

`state` uses the existing feed vocabulary: `scheduled`, `live`, `finished`,
`postponed`, `cancelled`, `abandoned` or `unknown`. `provider_state` preserves the
selected status's string `type.state`, or the seam's string `phase`; a non-string
or string longer than 100 characters becomes the empty string and supplies no
recognized phase. It is not replaced with an inferred phase.
`phase` is the downstream placement value `pre`, `in`, `post` or `unknown`.

`played` means this observation establishes live or completed match-score
semantics; it is true exactly for `live` or `finished`. `final` is true exactly
for `finished`. `void` is true for postponed, cancelled or abandoned statuses.
Here `void` means an exceptional non-final terminal classification, not a claim
that an abandoned match never began. Reported numeric facts remain separate.

## Recognition and precedence

Recognize phase only from exact `pre`, `in` or `post`; no missing-state default
silently invents `pre`. Trim and compare status identity strings without case
for recognition, while preserving an accepted ordinary live display label.

The supported `type.name` identity table is deliberately bounded:

| Names | Meaning |
| --- | --- |
| `STATUS_SCHEDULED` | scheduled |
| `STATUS_IN_PROGRESS`, `STATUS_FIRST_HALF`, `STATUS_SECOND_HALF` | live |
| `STATUS_HALFTIME` | live, halftime |
| `STATUS_FULL_TIME`, `STATUS_FINAL` | finished |
| `STATUS_CANCELED`, `STATUS_CANCELLED` | cancelled |
| `STATUS_POSTPONED` | postponed |
| `STATUS_ABANDONED` | abandoned |

Recognize the following exact text tokens in `type.description`,
`type.shortDetail` and `type.abbreviation`, or the seam's label:

| Tokens | Meaning |
| --- | --- |
| `Scheduled` | scheduled |
| `In Progress`, `Live`, `First Half`, `Second Half` | live |
| `Halftime`, `HT` | live, halftime |
| `Full Time`, `Full-Time`, `Final`, `FT` | finished |
| `Canceled`, `Cancelled`, `CAN` | cancelled |
| `Postponed`, `PPD` | postponed |
| `Abandoned`, `ABD` | abandoned |

These tokens are supported mappings, not a claim that every alias has appeared
in the retained ESPN capture. Provider numeric status IDs are not decoded.
Text identity/display candidates longer than 100 characters are unusable rather
than truncated. An overlong explicit name is unsupported, so it yields unknown.
This keeps provider phase and clock values within the existing feed bounds.
`type.detail`, display-clock text and arbitrary notes do not establish status
identity. A nonempty unknown `type.name`, or a non-null non-string name, makes
the status unknown. Empty/null names are absent. Unrecognized text labels are
retained only as display candidates; they cannot establish a final result.

Apply these rules in order:

1. Conflicting recognized meanings, or an unsupported explicit name, yield
   `unknown`. Halftime and ordinary live tokens share one meaning and do not
   conflict. An exception and a final marker do conflict.
2. One unambiguous postponed/cancelled/abandoned meaning wins over the generic
   provider phase, including `pre` or `in`. Its placement phase is `post`, while
   the original provider phase remains available.
3. A scheduled/live/finished meaning must agree with provider phase
   `pre`/`in`/`post`. A missing or contrary phase yields `unknown`.
4. With no recognized meaning, plain `pre` establishes scheduled and plain
   `in` establishes live. Plain `post` does not establish a played final result.
5. A boolean `type.completed: true` contradicts scheduled/live; boolean false
   contradicts finished. These contradictions yield unknown. An absent or
   non-boolean completed value supplies no completion fact; completed alone
   never establishes a final. Exception meanings do not depend on this flag.

Unknown results keep placement `post` only when the original phase was exactly
`post`; every other unknown result uses placement `unknown`. In particular,
contradictory or unsupported in-progress identity cannot keep the live phase.
Known scheduled/live/finished results use `pre`/`in`/`post` respectively.

## Labels and scores

Labels are empty for scheduled, `FT` for finished, `Canceled`, `Postponed` or
`Abandoned` for those exceptions, and `Status unknown` for unknown. Unknown
contradictions must not continue displaying a raw `Final` or `Live` label.
Halftime is `HT`. Other live raw observations use the nonempty string
`displayClock`, then nonempty string `type.shortDetail`, then `Live`. The seam
uses its nonempty string live label, then `Live`. Display strings are not parsed
as time and are never advanced. Recognized halftime takes precedence over a
display clock. Neither `FT` nor a live label establishes observation freshness.

Parse each reported match score independently:

* Accept a finite mathematical integer from 0 through 100, or an untrimmed
  string of one to three ASCII decimal digits whose value is in that range.
* For an object, examine `displayValue` using those scalar rules. Do not fall
  back to `value`, recursively unwrap objects, parse a composite score, or use
  truthiness. Numeric zero and string zero remain zero.
* Null/missing values, booleans, fractions, negative or oversized numbers,
  arrays, malformed objects and other strings become null.

`reported_scores` holds the two parsed values regardless of status. `scores`
holds those same values only when `played` is true; otherwise both values are
null. One absent side never creates a zero or erases a known other side. The
0–100 bound matches the existing feed contract; the former browser 999 bound
is deliberately not retained. An abandoned partial score may remain reported
evidence, but this increment does not present it as an ordinary result.

Time confirmation, scorer events, penalty-kick events, shootout and aggregate
mapping are outside this decoder. A `timeValid: false` fixture remains TBD even
if status reports progress or completion. Ordinary match scores are never
reinterpreted as shootout/aggregate totals, and a note such as `aggregate 3-1`
does not manufacture structured results. Existing explicit null shootout and
aggregate exports remain until their raw field mapping has separate evidence.

## Consumer seam and intentional changes

The [fixture-window owner](fixture-windows.md) additionally accepts `unknown`
for the placement seam. It uses the same placement arithmetic as `pre`, while
`source_in_progress` and `recent_live_receipt` stay false. A scheduled or unknown
fixture can therefore occupy estimated live placement without a source-live
claim. The UI must consume normalized status for Live/Final labels, scores and
live-only destinations; a `post` placement alone is insufficient for Final.

Known exceptions leave ordinary upcoming/live placement. Unknown status does
not remove independently valid viewing routes. Browser snapshot validation and
static/rehydrated publisher rows carry the normalized facts consistently. A
previously decoded status must carry its canonical state across serialization
and browser hydration; reclassifying its display label as new identity would
change accepted raw facts. Legacy rows without that state binding still use
phase/label inference. A mutable publisher record must discard a stale state
binding when its bound phase or label changes.
Rating, candidate selection, transport timing and overlay identity/correction
ordering retain their separately documented owners; those policies must not be
reimplemented inside the source decoder.

Intentional corrections include absent status becoming unknown, exceptional
status recognition before abbreviation presentation, abandoned/unfamiliar
terminal status not becoming finished, contradictions failing to unknown,
object/zero score consistency and contract-bound score parsing. The ordinary
captured pre/in/post cases establish extraction parity; independently authored
[examples](espn-status-examples.json) establish these corrections independently
of new decoder or consumer outputs. The capture has no exceptional terminal, TBD or shootout/aggregate
cases, so its parity alone cannot prove those semantics.
