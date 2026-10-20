# Companion data contract

The companion needs public match data it can understand without parsing a webpage. This document defines the proposed contract between Soccer Outlook's existing publishing pipeline and its clients. It is for the people implementing the Python publisher, Dart repositories, browser adapter, and their compatibility tests.

This is a design specification; the feed and schemas described here have not been implemented.

## What changes

Keep the existing website and its refresh mechanism working while adding a separate, versioned feed. Today's [`schedule.json`](../schedule-data.md#refreshes-and-retained-evidence) contains rendered HTML rows and a renderer/configuration hash. `schedule-context.json` is an internal build cache whose standings expire after 30 hours. Neither is a native application API, and the companion must not depend on their incidental structure.

The new feed publishes source facts, normalized broadcast routes, canonical component scores, policy parameters, and editorial content. It never publishes a particular visitor's filtered schedule or final personalized picks. Those decisions belong to the shared domain implementation described in [Architecture](architecture.md).

The existing owners remain authoritative during this work:

| Information | Current owner | Feed responsibility |
| --- | --- | --- |
| Fixtures, team facts, standings, Outlook components and total | `build.py` | Export typed records without changing their meaning. |
| Channel aliases, plan carriage, usual coverage | `rights.toml` and its loader | Export stable IDs and normalized routes with their qualifications. |
| Broadcast evidence and source diagnostics | `broadcast_listings.py`, individual adapters, `schedule_data.py` | Preserve identity, country, package, replay checks, provenance, and original observation times. |
| Viewing destinations | `watch_links.py`, `watch-links.toml` | Export verified routes, not guessed player URLs. |
| Automatic AI component aggregation and provenance | `story.py`, `story_state.py` | Preserve original judgments, calculated totals, and expiry independently of publication time. |
| Published defaults and scoring configuration | `build.py`, `settings.toml` | Export their values and revision; do not maintain another hand-edited list in Dart. |

The publisher continues calculating the Outlook score. Dart consumes its total and components; it does not implement that formula again. The shared Dart core owns personal eligibility, AI precedence and expiry, the preference blend, recommendation selection, and rating-dot rounding.

## Publication envelope

The proposed endpoint is `api/v1/manifest.json`, relative to the existing GitHub Pages site. The path is a design choice, not a currently available endpoint.

A manifest names immutable, content-addressed artifacts for:

- **Catalog:** leagues, teams, networks, service plans, assets, and relationship IDs.
- **Schedule:** fixtures, standings context, broadcast routes and evidence, Outlook scores, and source coverage.
- **Policy:** defaults, personalized scoring parameters, supported time windows, and freshness policy identifiers and values.
- **Editorial:** automatic and shared AI records, when available.

Use a relative artifact path such as `objects/<sha256>.json`. Each descriptor carries its schema version, SHA-256 digest, uncompressed byte count, and content revision. Artifact references must stay on the configured publication origin and inside its API directory. They must not redirect the client to arbitrary endpoints.

Every envelope has `schema_version`, a stable `kind`, and a `content_revision`. The manifest also carries a monotonically increasing `publication_revision`, `published_at`, and `producer_revision` (the source commit). Publication ordering is determined by the revision, not by device time, HTTP ETags, or an AI generation timestamp.

This abbreviated manifest illustrates the shape. Digest placeholders and omitted descriptors make it documentation, not a valid test fixture:

```json
{
  "schema_version": 1,
  "kind": "outlook_manifest",
  "content_revision": "publication-42",
  "publication_revision": 42,
  "published_at": "2026-10-20T16:00:00Z",
  "producer_revision": "source-commit-id",
  "artifacts": {
    "catalog": {
      "path": "objects/<catalog-sha256>.json",
      "sha256": "<catalog-sha256>",
      "bytes": 42000,
      "schema_version": 1,
      "content_revision": "catalog-7"
    },
    "schedule": {
      "path": "objects/<schedule-sha256>.json",
      "sha256": "<schedule-sha256>",
      "bytes": 280000,
      "schema_version": 1,
      "content_revision": "schedule-42"
    },
    "policy": {
      "path": "objects/<policy-sha256>.json",
      "sha256": "<policy-sha256>",
      "bytes": 6000,
      "schema_version": 1,
      "content_revision": "policy-3"
    },
    "editorial": null
  }
}
```

Before this contract ships, commit JSON Schemas and complete representative fixtures beside the publisher tests. Schemas must specify required fields, bounds, enums, and nullability; this document defines their semantics.

### Atomic publication and reading

The serialized publisher builds and validates all referenced artifacts first, then publishes them with the manifest in one Pages deployment. It derives the next publication revision from durable published state and retains the existing check that a superseded source commit cannot publish. A rollback publishes a new, higher revision pointing to the previously validated content; it does not rewind the revision counter.

Clients fetch the manifest conditionally, then fetch only changed artifacts. They verify decoded size, digest, schema, IDs, and references before committing a new snapshot in one local transaction. They notify observers only after that transaction succeeds. A failed download or mismatched catalog must not create a frame containing half of the old snapshot and half of the new one.

An older publication revision is ignored. An equal revision with identical manifest content does not replace the required snapshot; it may update request-check metadata and retry a pending optional editorial download. The same revision with different content is an integrity failure. A higher revision may replace the active snapshot only after validation and a second ordering check inside the commit transaction. This prevents an older concurrent download from winning merely because it finishes last. Hashes verify downloaded content against its manifest; they do not authenticate a source independently of the configured HTTPS origin.

The catalog, schedule, and policy form the required transaction. Editorial is optional and cannot prevent a valid schedule update. The manifest must explicitly provide either its descriptor or `editorial: null`. Null is an intentional withdrawal and clears the previous published editorial content when that manifest is accepted; it does not erase a personal import. A missing manifest field is invalid, not another spelling of null.

When a descriptor exists but its download or validation fails, retain prior accepted editorial only while its original validity, policy compatibility and references still hold. Install a successful editorial replacement atomically and validate its fixtures and leagues against the active catalog and schedule. Within the editorial artifact, automatic and shared slots each explicitly contain records or null, so one origin can be withdrawn independently. A valid empty headline list is intentional content, not a fetch failure. No retry extends any editorial lifetime.

Inside the editorial commit transaction, check that the active manifest still expects the exact descriptor being installed. Discard superseded downloads, including a response that arrives after a newer manifest sets editorial to null. Fixture-reference validity alone cannot authorize restoring withdrawn content.

Keep current and previous complete publications on the server, plus all objects referenced by manifests published within the previous seven days. This proposed retention period is an operational policy, not a promise that a week-old schedule is fresh. A client that encounters a collected object re-fetches the latest manifest once and retries that publication, rather than indefinitely retrying the old path. Installed clients retain their own last valid snapshot.

A complete empty schedule is valid. Incomplete required fixture collection cannot replace the last complete published schedule with an apparently complete empty one. Broadcast-source failures may still allow publication, with their coverage limitations preserved.

## Identity and basic types

IDs are opaque, stable strings. Display names, translated names, badge URLs, kickoff times, and positions in arrays are never identities.

| Entity | Proposed identity |
| --- | --- |
| Fixture | Namespaced source ID, initially `espn:fixture:<event-id>`. |
| League | Existing stable competition key, namespaced only if necessary to resolve a demonstrated collision. |
| Team | Namespaced provider ID, with explicit aliases for verified cross-source identities. |
| Service plan | Existing saved-selection key from `rights.toml`; preserve its meaning. |
| Network | New stable catalog ID mapped from canonical names and aliases in `rights.toml`. |
| Editorial item | Origin plus response/request ID plus item ID, independent of its displayed title. |

Keep original source IDs in `source_refs` so adapters can join new observations without fuzzy name matching. A team changing its name does not become another team. Two similarly named teams do not merge without verified identity evidence. A postponed kickoff changes fixture facts, not the fixture ID.

Retired entitlement IDs are never reused for a different package. Catalog migrations can explicitly map an old ID to an equivalent new one, but cannot silently upgrade a user's entitlement. An unknown saved service ID is retained for migration or explanation and grants no access.

Dates use ISO `YYYY-MM-DD`; instants use RFC 3339 UTC with `Z`. Time zones use IANA identifiers. All numbers are finite; absent quantities are `null`, not zero. Scores are typed numeric values with explicit shootout/aggregate fields where supplied, not strings containing presentation markup.

The first schema must set conservative validation limits, including a 64 KiB manifest, 8 MiB per decoded artifact, at most 3,000 fixtures, bounded text and arrays, and no duplicate object keys. These are client safety limits owned by the schema/parser, not mutable server instructions. Limits must be checked before allocating unbounded structures and tested against a representative full publication.

## Schedule records

Each fixture record includes the following groups:

| Group | Required meaning |
| --- | --- |
| Identity | Fixture ID, competition ID, home and away team IDs, source references. |
| Timing | Confirmed kickoff instant or `null`; confirmation flag; source-provided scheduled date and its time-zone context when kickoff is unconfirmed. |
| Status | Normalized state, original provider state, observation time, score, and optional period/clock. Distinguish scheduled, live, finished, postponed, cancelled, abandoned, and unknown. |
| Context | Stage, group/conference, venue and place when known, notes, team form and table context. |
| Coverage | Recognized networks, normalized service-plan routes, evidence references, usual-coverage route when valid, and unresolved channel observations. |
| Rating | Canonical Outlook total, components, raw inputs, assumptions, weights, and calibration ID. |
| Links | Typed match page, calendar source fields, table reference, and verified viewing destinations. |

Team form is an ordered sequence of result values with its direction specified once in the schema. Rank must include its competition/group and table size; a number without its table context is not a meaningful standing. Team name, emblem, hometown, leaders, record, and other vitals retain their source and observation context. Missing star-player information stays missing.

An unconfirmed kickoff is not a synthetic midnight timestamp. It cannot win “next confirmed kickoff,” acquire a countdown, or become live when an invented time passes. Its supplied date may support an explicitly time-unconfirmed schedule group. A date without a known zone cannot be converted into the visitor's local date as though it were an instant.

The schedule envelope declares the exact requested and successfully covered date scopes by competition/source, plus the UTC horizon covered by confirmed fixtures. The producer must fetch enough boundary dates to support the product's local sports-day window in supported time zones. Until coverage is verified, a client outside that horizon shows a coverage limitation rather than treating missing dates as days with no matches. Calendar dates across DST changes are not fixed 24-hour durations.

## Broadcast routes and evidence

A fixture's channel listing and a user's package are separate facts. The producer normalizes their relationships; the domain intersects eligible routes with the user's selected plans.

Each route contains:

- Network ID and eligible service-plan IDs.
- Country, initially `US`, and route scope such as linear channel, provider stream, or separate subscription.
- Basis: dated match listing, valid usual coverage, or unknown.
- Applicable package requirements and explicit exclusions, with human-readable explanatory notes.
- Evidence IDs and original verification time; a retained-evidence flag and fixed expiry when applicable.
- Any qualified viewing destinations.

Evidence includes source ID, public source URL, fixture/team/kickoff identity as verified, observation time, source event/airing IDs if supplied, country, package, and live-versus-replay classification. Rejected or ambiguous observations can appear in diagnostics, but never as an eligible route.

Usual coverage is a distinct, weaker basis and retains its rights validity period. It must not be upgraded to “listed” by the client. Existing behavior permits a recognized valid usual home to qualify; the companion preserves that behavior and label. An unknown broadcaster, missing listing, or generic provider logo grants nothing.

Preserve these distinctions in fixtures and tests:

- YouTube TV's main plan is not Spanish Plus; a Universo listing cannot be satisfied by the main plan alone.
- Access through a TV provider is not a paid FOX One subscription.
- Channel carriage does not establish access to a separate Paramount+, Peacock, or other standalone application.
- A linear channel showing a replay is not proof that the underlying fixture is live.
- A positive listing survives another source's absence, but not a relevant explicit country, identity, package, or replay rejection.

The publisher retains recently verified evidence only under the existing bounded retention policy. Its original observation and expiry remain unchanged on reuse. A successful app refresh cannot renew broadcaster verification.

The application describes these routes as known coverage for selected packages. It cannot certify the user's account, ZIP-code affiliate, blackout exception, or ability to play a particular stream.

## Scores and editorial content

The feed separates quantities that have different owners:

| Quantity | Published representation | Client action |
| --- | --- | --- |
| Outlook score | Total, four components, raw inputs, missing-input flags, effective weights, calibration ID. | Use the published calculation and show its explanation. |
| Automatic AI rating | Component judgments, aggregate total, aggregation weights, model/effort provenance, original generation time. | Validate and use under shared precedence/freshness rules. |
| Personal or shared rating | Imported total, prompt/request identity, original prompt time, saved influence weight. | Do not invent component breakdowns. |
| Final pick score | Not a canonical feed field. | Calculate from eligible rating, Outlook, and the user's complete league order. |
| Rating dots | Not a canonical feed field. | Apply the single shared rounding rule to the displayed score. |

The policy artifact supplies effective blending values, default league and service order, default enabled selections, AI text default, window parameters, and versioned freshness values. Rules remain code with stable rule IDs; policy supplies values rather than executable expressions. Its declared `policy_id` must be supported by the client. Unsupported policy semantics require a compatible client, rather than falling back to guessed weights.

Editorial records contain origin (`automatic` or `shared`), response/request ID, original generation or prompt timestamp, publication/update timestamp, expiry, referenced fixture and league IDs, text, source links, and interest score. Headlines and match stories are different records. A calendar headline also needs its sourced event period; an undated calendar claim does not become eligible simply because it was recently fetched.

Automatic output retains its original `generated_at` and `focus_until` constraints; personal/shared output retains its original prompt time and 24-hour maximum validity. An editorial publication revision can change after a recalculation without renewing the underlying research. Converting current formats to this contract must preserve that distinction exactly.

Personal AI responses stay in the user's local store and are not uploaded by reading this public feed. Their schema is validated through the same domain import boundary used by the browser. The AI text toggle applies to both headlines and match blurbs; it is independent of whether valid AI ratings participate in ranking.

Text is plain text, not HTML. Source URLs are validated HTTPS links. Unknown referenced fixtures cannot produce recommendations, and a headline that loses required eligible match references must disappear instead of referring to filtered-out or completed matches.

## Assets and destinations

Catalog assets carry an asset ID, HTTPS URL or same-origin asset path, media type, dimensions when known, attribution where needed, and optional known contrast treatment. Clients use a consistent transparent emblem presentation with an accessible text fallback. Asset failure cannot remove a fixture, its league, or its filter control. Do not infer a team ID from its badge URL.

Viewing links have a kind (`match`, `channel`, or `service_home`), supported network and service-plan IDs, country, fixture binding when present, and conditions such as `live_only`. A service homepage is never labeled as an exact-match link. Supplied YouTube TV links retain their fixture/channel binding and live-only condition; the client cannot manufacture watch IDs.

External URLs must not contain account tokens or use arbitrary app-launch schemes. Open validated HTTPS destinations using the operating system's normal link handling; the provider owns login and playback. Calendar entries are generated locally from confirmed fixture fields, with stable fixture-based IDs. Table navigation refers to the relevant league rather than leaking a website DOM selector into the app model.

## Freshness and live updates

Freshness is not one timestamp. Keep these separate:

| Timestamp or state | What it tells the client |
| --- | --- |
| Publication time/revision | When this bundle was published and its ordering. |
| Attempted/checked time | When a request was made or the source successfully validated its response. |
| Observed content time | When the fixture, score, standings, or evidence was actually observed. |
| Provider update time | When the provider says its data changed, if available. |
| Original verification/generation and expiry | Whether retained evidence or AI remains valid. |
| Coverage state and scope | Whether the source actually checked the relevant competition and dates. |

An HTTP 200 or 304 may validate cached content without making the underlying match facts newer. Reused standings keep their original observation time. Source health distinguishes unsupported, not checked, empty, partial, unavailable, and successfully observed coverage. Independent comparisons identify their limited supported scope and never imply every league was checked.

The current GitHub publication cadence is approximately twice hourly, subject to job delays. The browser also polls ESPN directly for live scores. A native application that reads only the aggregate feed cannot offer equivalent live freshness.

The first implementation phase therefore validates a foreground, read-only ESPN live adapter on the target platforms. It emits typed deltas for known fixture IDs only:

```text
LiveDelta {
  fixtureId, sourceRef, expectedTeams, expectedKickoff,
  requestGeneration, observedAt, providerUpdatedAt?,
  status, scores, period?, clock?, scoringEvents?
}
```

The adapter does not create fixtures, change entitlements, infer rights, or promote a broadcast replay to a live match. A shared reconciliation owner checks identity, fixture revision, request generation, and observation ordering before applying a delta. A later aggregate publication must not overwrite a newer valid live observation merely because its manifest revision is higher. A changed kickoff or conflicting identity invalidates incompatible overlays. Terminal-state corrections require explicit source evidence and tests rather than a permanent “finished can never change” shortcut.

On suspension, cancel or invalidate in-flight requests. On resume, re-evaluate freshness and refresh; do not advance a match clock or claim a fresh live score based solely on elapsed wall time. Background polling and push alerts are not promised. If the live adapter cannot be supported reliably, the implementation plan must surface that gap before live-feature acceptance; a half-hour feed is not a silent substitute.

## Compatibility, cache, and verification

Within schema version 1, additive optional fields are allowed. Required-field removal, changed meaning, reinterpreting an existing entitlement ID, or new policy semantics require an explicit compatibility change. An ordinary sourced carriage change can update routes within the existing contract; it does not by itself require a new schema. Unknown optional diagnostic labels may be retained. An unknown status cannot establish live or completed play; an unknown coverage value cannot grant access. These are independent judgments: valid coverage does not disappear solely because match progress is unknown. Unsupported required schemas reject the publication without erasing the last compatible snapshot.

Clients distinguish “no compatible data yet,” “using saved data,” “refresh failed,” and “valid empty schedule.” A saved snapshot may remain useful offline after its transport freshness threshold, but AI and evidence expiry still apply. Stale live observations must be visibly described as last known, and no stale timestamp is renewed just by reading the database.

The local database tracks the accepted manifest, artifact revisions, source observation times, and live overlays separately. Schema migrations are transactional, preserve saved preferences and personal AI input, and use explicit columns. The network cache is replaceable; user preferences are not. Corruption recovery must not reset the user's lineup as a side effect of discarding invalid feed data.

Before shipping the feed, the following become automated contract tests:

1. A real Python-generated publication decodes through the production Dart parser and round-trips without losing identity, missingness, timestamps, or evidence qualifications.
2. The same normalized fixture and preference cases produce equivalent browser and native domain decisions.
3. Hash mismatch, oversized payload, unsupported schema, duplicate IDs, broken references, malformed dates, invalid URLs, and non-finite ratings fail before snapshot installation.
4. Complete empty versus incomplete source response, partial broadcast failures, retained evidence expiry, and explicit negative checks have distinct expected outcomes.
5. Mixed publication downloads, rollback, concurrent refresh completion, database migration failure, and app suspension cannot regress accepted data or expose a partial snapshot.
6. Original AI timestamps survive republishing and recalculation; stale or filtered stories disappear while valid schedule data remains usable.
7. Live deltas cannot cross fixture identities, overwrite fresher observations, revive cancelled requests, or change package access.
8. Local sports-day boundaries, DST transitions, unknown kickoff times, and coverage-horizon edges use independently authored expected examples.

See [Engineering practices](engineering.md) for ownership and test discipline, and [Implementation plan](implementation-plan.md) for the delivery gates.

## About this document

Status: proposed contract for the first companion release. No public API or compatibility guarantee is created by this document alone.

Baseline: Soccer Outlook `cb0475e`, reviewed 20 October 2026. Current implementation references are [`schedule_data.py`](../../schedule_data.py), [`build.py`](../../build.py), [`rights.toml`](../../rights.toml), [`watch_links.py`](../../watch_links.py), [`story_state.py`](../../story_state.py), and [`web/live.js`](../../web/live.js). Existing source behavior is described in [Schedule data and coverage](../schedule-data.md) and [Scoring](../scoring.md).
