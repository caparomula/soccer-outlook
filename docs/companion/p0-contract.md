# P0 contract draft and decisions

October 20, 2026. This is the initial schema review slice of P0; the
[combined checkpoint](p0-evidence.md) records stage status. The website still
publishes its existing formats. No application
consumes this draft, and no public feed has been deployed.

This document preserves the P0 review checkpoint. The subsequent
[P1 evidence](p1-evidence.md) records the exporter, independent reader, network
registry, durable publication tests and original editorial conversion now
implemented locally. Its remaining work supersedes the P0 task list below;
P0 captures and measurements have not been relabeled as P1 evidence.

The [schemas and complete example publication](../../contracts/outlook/v1/README.md)
make the proposed [data contract](data-contract.md) concrete enough to inspect.
They separate a required catalog/schedule/policy transaction from independently
withdrawable optional editorial content. The manifest and every artifact have
an explicit kind, schema version and content revision. Manifest ordering uses
an integer publication revision with a JavaScript-safe upper bound.

## Evidence from the current owners

This draft was checked against the local producer, not inferred from rendered
HTML:

| Current representation | Draft boundary |
| --- | --- |
| `build.py::Match` has a mandatory `utc` plus `time_valid`. | Unconfirmed kickoff is null; a source date and its possibly unknown zone are separate. The placeholder instant must not leak into the feed. |
| `Team.score` is a string and missing values are empty strings. | Numeric match scores distinguish zero from null; shootout and aggregate values have separate nullable pairs. `scores.match` is the source's current match score, including extra time where applicable, excluding the separately supplied shootout tally. |
| `Match.state` is `pre`, `in` or `post`, with a display status string. | Normalized status is explicit; original provider state remains available. The future shared decoder still needs to establish the mapping. |
| `schedule_data.publish` emits HTML rows and internal dataclass records. | Neither is reused as the companion API. No `row_html`, selected owner service, final pick score or rating dots belongs in the factual artifact. |
| `build.py::outlook_parts/outlook_score` calculate four components with missing values retaining their weights. | Total, nullable components on the 0–1 scale, raw inputs, missing-input labels, weights and calibration identity are factual publisher output. Clients must not recalculate this formula. |
| `story.py::WEIGHTS` and `story_state.COMPONENTS` use popularity, gameplay and impact. | Automatic component fields preserve those names. Imported totals do not acquire invented component values. Editorial story interest and match rating total have separate fields. |
| `rights.toml` uses stable service keys including `yttv` and `yttvspanishplus`. | Those saved keys remain plan IDs. Example network IDs are proposals, not an approved permanent registry. |
| `broadcast_listings.retain_recent_evidence` keeps original checks for at most six hours and also limits retention relative to kickoff. | Retained routes require original verification and a fixed expiry. The exporter must calculate the exact minimum of applicable source/fixture limits; the six-hour policy value alone is insufficient. |

The synthetic examples contain no captured personal inputs and make no claim
that the example matches, listings or fixtures happened. The manifest's producer
revision identifies the reviewed baseline; it does not claim that commit emitted
these draft bytes.

## Draft representation decisions

IDs are opaque bounded strings. Relationships refer to IDs rather than names,
URLs or array positions. The catalog keeps source aliases; the schedule states
its catalog revision and policy ID. Evidence binds a fixture, its teams, its
kickoff when confirmed, source IDs, country, package, original observation time,
classification and verdict. Replay or rejected evidence can remain diagnostic
data, but cannot justify an eligible route.

`eligible_service_plan_ids` means that any listed, valid selected plan can
satisfy that published route. Package descriptions and exclusions are
explanatory facts; clients must not interpret their prose as executable
entitlement rules. More complex conjunctions require a reviewed contract
extension, not a guessed consumer rule. Unknown saved plans grant no access.
The Universo case preserves the existing main-plan/Spanish-Plus distinction;
it does not assert that buying an add-on without its prerequisites is possible.

The schedule records exact requested and covered source calendar dates and
their zone. Its `confirmed_horizon` is the interval of verified fixture
collection coverage, not the first and last kickoff in its list. A complete
empty collection can therefore have a horizon. Unverified coverage is null.
Date scopes do not prove coverage for every viewer's local sports-day window.
An incomplete required fixture collection cannot be installed; a partial
broadcast lookup can coexist with a complete fixture collection.

Team form is declared oldest to newest once in the schema. The P1 exporter
must verify ESPN's source ordering before conversion. Rank includes table size
and group, and every vitals record includes a source and observation context.
No example fabricates missing team vitals.

The draft editorial artifact has explicit automatic and shared slots, each
records-or-null. Each slot retains original generation or prompt time, its own
publication time and original expiry. Individual records have their own expiry
and explicit `fixture_refs`: each names the fixture ID and the original
referenced kickoff, including null when unknown. An unchanged fixture ID after
a reschedule cannot preserve an old editorial binding. A harmless schedule
revision that preserves those facts does not invalidate it. Calendar-only
headlines require both an event period and at least one source link. A valid
shared slot with an empty headlines list demonstrates deliberate empty content.
Personal imports remain outside this public format.

## Policy identifiers and compatibility

The sole example policy is `outlook-policy-p0-draft-1`. A client must explicitly
support a policy's semantics before installation. The draft ID is not a
production compatibility promise and must not become an implicit fallback.

| Draft rule ID | Intended responsibility |
| --- | --- |
| `watch-routes-v1-draft` | Intersect valid, country-qualified routes with selected plans; preserve listed/usual and explicit rejections. |
| `local-sports-day-v1-draft` | Named-zone calendar days at the supplied boundary; current example values are 4 a.m., three past and three future days. |
| `personal-ranking-v1-draft` | Existing AI/Outlook/league-order blend and candidate distinctions; no scoring implementation is supplied here. |
| `editorial-lifetime-v1-draft` | Original generation/prompt lifetime, focus window, per-record expiry and provenance. |
| `six-dot-rounding-v1-draft` | Existing six-dot rounding, owned by the eventual shared core. |
| `source-freshness-v1-draft` | Separate source observation, validation, request and publication ages. Live freshness thresholds remain unresolved. |

The example blend values come from `settings.toml`, the automatic maximum from
`story_state.MAX_AGE_HOURS`, and the shared maximum from the existing 24-hour
import limit. The example defaults deliberately describe its tiny synthetic
catalog; they must never replace exported `OWNER`, `LEAGUES` or the complete
service order. Real policy/defaults generation remains P1 work.

Schema version 1 permits additive optional fields. The schemas therefore accept
unknown properties, while explicitly reserving known personal-output field
names such as `viewer_preferences`, `personal_ai`, `final_pick_score` and
`rating_dots`. This is an ownership check, not a guarantee that arbitrary data
contains no secrets. Producer allowlisting and source URL review are still
required.

Required removal, changed meanings, reused entitlement IDs and new policy
semantics need explicit compatibility changes. Unknown provider statuses map
to `unknown` while preserving original text; they do not establish play or
invalidate independently valid routes. Unknown coverage maps to `unknown`
with no eligible plans. Unknown enum strings are not silently interpreted.
Optional diagnostic labels may be retained.

## Validation evidence and limits

[The draft test harness](../../tests/test_companion_contracts.py) checks:

- Draft 2020-12 schema validity and all checked-in synthetic examples.
- Required editorial null versus omission, known personal-output exclusions and
  additive fields.
- Real SHA-256 descriptors, byte counts, content revision agreement, corruption,
  source and catalog references, duplicate fixture IDs, and unsafe object paths.
- Required collection completeness, package exclusions, replay/identity/country
  evidence binding, status uncertainty, date validity and score missingness.
- Original editorial kickoff and league binding across schedule revisions,
  sourced calendar-only claims, table-league references and migration targets.
- 64 KiB manifests, 8 MiB decoded artifacts, 3,000 fixture bounds, duplicate JSON
  keys and non-finite numbers.

The harness is test code. It does not create a second production rule owner.
It registers actual date-time validation explicitly because the base
`jsonschema` installation otherwise treats that optional format as unchecked.
Fixture expected meanings are written independently of `build.py`; no
production encoder generated this corpus.

JSON Schema cannot enforce digest equality, uniqueness by an ID member,
cross-artifact identity, time ordering, policy support, or transactional
installation by itself. The harness covers representative structural/reference
requirements, not every semantic obligation. A production parser must also
bound transport/decompression and nesting before constructing unbounded
objects, validate actual IANA zones, validate URL origins and redirects, reject
credential-bearing URLs, and reject unsupported policies. Size caps alone do
not prove those protections.

## Open work before a production contract

1. Review network IDs, source alias migration, plan retirement mappings and
   every qualified route against the real exporter. Review package conjunctions
   if the existing plan IDs cannot express them without inventing access.
2. Capture complete real publications, including populated standings/vitals,
   destinations, automatic editorial and a full schedule near the size limit.
   The small synthetic corpus is not a measured production sizing test.
3. Define exact status/result decoder semantics, field provenance for imported
   source dates, normalized form order, missing scores and corrected finals.
   No decoder or producer was changed in this slice.
4. Complete production editorial semantic validation: original/focus/per-item
   lifetime ordering, automatic component/total relationships, fixture bindings,
   and legacy-format conversion. The harness checks kickoff and league bindings;
   the production consumer is still pending. No re-publication may extend life.
5. Approve live observation freshness, request cadence and supported-zone
   coverage values rather than copying arbitrary thresholds into the policy.
6. Implement the P1 exporter and independent production Dart parser, then
   replace synthetic-only evidence with producer/consumer round trips. Hashes
   alone do not authenticate the configured HTTPS origin.
7. Prove publication ordering, concurrent refreshes, optional editorial races,
   durable revision allocation, object retention and rollback in P1/P3.
   These schemas do not implement or test a snapshot database.

This draft supplies only the schema portion of P0. The
[combined evidence](p0-evidence.md) records the broader spike, platform
transport proof, ownership review and compatibility decisions. P1 must still
prove production exporter/consumer round trips.
