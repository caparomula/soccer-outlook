# SOURCE-PROVENANCE-1: Preserve the accepted response receipt

Keep the receipt of each fixture's accepted source response separate from source
observation, source update, collection validation and publication. This is
provenance plumbing for TIME-2 and DATA-1, not a freshness policy or a new shared
domain operation. It does not classify a score as fresh or stale, decide whether
a match is live now, change status labels, or change polling and ranking.

The [independent examples](source-provenance-examples.json) describe publisher
and browser lifecycle inputs and literal expected facts. They are integration
scenarios, not executable bridge envelopes. They were authored before the
implementation; expected timestamps must not be obtained from the new owner.

## Meanings and representation

| Fact | Meaning in this increment |
| --- | --- |
| `source_received_at` | UTC receipt instant of the source response supplying this accepted fixture; nullable |
| `observed_at` | Actual source observation instant; remains unknown for the current ESPN records |
| `provider_updated_at` | Provider's data-change instant; remains unknown for the current ESPN records |
| Collection `validated_at` | Completion of the existing collection validation; distinct from response receipt |
| Publication `generated_at` | When the output was produced; never a substitute for receipt or observation |

The internal publisher field is `Match.source_received_at`, a nullable UTC
timestamp string. JSON context preserves it. A known value is exposed in HTML
as `data-source-received-at`; a missing/empty attribute carries no known receipt.
The existing public feed's applicable `observation.received_at` fields consume
that per-fixture receipt. No public schema version or freshness value changes.
The fixture status and scoreboard-derived team vitals share the receipt of
their accepted scoreboard response. Separately sourced standings retain their
existing separate receipt provenance.

Known receipts use the existing UTC contract spelling
`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`: an optional fraction has one through six decimal
digits, and the calendar date and time must be valid. Preserve the original
instant and supplied precision. Publisher boundaries reject a malformed
non-null receipt rather than replacing it with a clock or silently exporting
an invented value. Null is the internal unknown value; missing/empty HTML
attributes mean unknown for compatibility. A known browser attribute must
follow the same UTC grammar. A future but structurally valid instant remains
that supplied fact: this increment neither clamps it nor classifies its age.

A directly constructed or legacy `Match` without the field has an unknown
receipt. Do not substitute its league's latest validation, context timestamp,
render time, publication time, file modification time or current clock. Missing
source observation/update instants remain null even when receipt is known.

## Publisher collection

The worker records its UTC receipt immediately after `curl_bytes` returns and
before parsing or handing the result to the ordered collection consumer. Keep
this timestamp alongside that response. A slow earlier job or delayed consumer
must not move another response's receipt to the later validation instant.

Apply the existing scope checks, event admission and merge order unchanged.
Only when an event becomes the selected entry in the merged fixture map does
its response receipt become that entry's receipt. Store event and receipt as
one logical acceptance. If a later admitted duplicate wins under the existing
merge order, both the selected event and its receipt change together, even if
the selected response happened to arrive earlier. This rule does not introduce
latest-receipt-wins ordering. An ignored duplicate, rejected event, invalid
response or failed request cannot replace the selected event's receipt.

The later collection validation timestamp remains accurate as a validation
fact. Two response bodies received at 17:00 and 17:03 and validated at 17:05
therefore produce two different fixture receipts, neither equal to 17:05.

Saved context, static HTML, schedule fragments and feed republishing retain
the accepted receipt. Reloading, rendering or publishing the same facts does
not count as another source response. A real newly accepted response may have
a newer receipt even when its score is unchanged; this never establishes a
newer source observation or provider update.

## Browser collection and reuse

Capture one receipt per successful scoreboard body after the JSON body has
been parsed. Pass that response's receipt to each accepted event application.
All accepted events from one body share that receipt; a later response gets
its own. Do not sample a new receipt separately for each row while applying a
batch.

A missing fixture in a successful response does not receive a new receipt.
Neither do a failed request, invalid body, rejected event application or a
cancelled/obsolete response. An accepted event must carry its receipt through
the row's `data-source-received-at` value. Hydrating a saved row preserves its
known receipt, including a null receipt in legacy markup.

When the existing overlay reconciliation retains a newer accepted live event
over a schedule replacement, retain that event's original receipt as well.
Reapplying it to a replacement row is not another fetch. This increment does
not change the existing overlay eligibility, identity, generation or ordering
rules. It also does not migrate the existing `_seen` placement/reconciliation
clock: `source_received_at` is an explicit provenance field, and the two must
not acquire new interchangeable meanings incidentally.

## Boundaries and acceptance

No source observation or provider update timestamp is inferred from a receipt,
successful request, source clock text, kickoff or publication. No elapsed-time
threshold, freshness label, score expiry, link eligibility or polling cadence
is introduced. Existing values such as the fixture window's ten-minute receipt
check remain owned by their existing rule and do not become source freshness.

Tests must retain distinct worker receipt and main-thread validation instants;
exercise duplicate winners, rejected/missing/failed events and legacy missing
receipts; verify context/HTML/feed round trips and republishing; and prove that
browser snapshot reuse does not renew an accepted overlay's receipt. Browser
fixtures and requests remain synthetic and network-blocked. The examples are
not proof of a real provider observation time or production freshness policy.
