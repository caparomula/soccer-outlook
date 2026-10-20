# Engineering and verification

This guide explains how to develop the companion app without moving Soccer Outlook's existing mistakes into a new interface. It applies Pit Dash's strongest practices to the boundaries, data sources, and product described in the [architecture](architecture.md) and [specification](specification.md).

These are proposed development standards; the companion packages, rule checks, and mobile tests do not exist yet.

## Place each decision with its owner

Before changing behavior, identify who owns the decision and search for every existing implementation, including Python, browser code, tooling, and tests. Fix the owner, then have consumers read its result. A widget can arrange a match's information or route a tap; it cannot independently decide that a match is watchable, live, expired, or a top pick.

Ownership applies to facts as well as algorithms. Schema versions, stored preference keys, channel aliases, country restrictions, defaults, score weights, freshness limits, and numbers quoted in help each need one authoritative definition. Generated configuration and typed accessors distribute those definitions; copying them into another language does not.

The intended shared Dart domain core and browser bridge have a feasibility gate in [P0](implementation-plan.md). During migration, a temporary second implementation must have a named owner, parity cases, and a removal milestone. A passing parity test demonstrates agreement for those cases; it does not turn two implementations into one owner. Do not create permanent duplicate eligibility logic as a shortcut around the bridge.

Make consolidation and intentional behavior changes distinguishable in review. First prove that a rule has moved without changing its answers. Then change the rule and its documented examples. An independent test oracle may restate a specification to check an implementation; fixture builders and production tooling must use the actual owner.

Enforce dependency boundaries in CI once the packages exist. Domain code cannot import Flutter, HTTP, database, file-system, or platform plugins. Data adapters translate external representations into app-owned input types. Application code coordinates use cases through interfaces. Presentation consumes view models and issues commands.

## Keep decisions readable and executable

Introduce a small rule catalog as the domain is extracted in P2, rather than writing a speculative encyclopedia before implementation. Start with watchability, time windows, ranking, AI eligibility, and freshness. Each rule needs:

- A stable ID, plain-language explanation, and explicit treatment of missing or contradictory information.
- Language-neutral pseudocode, with a flow diagram only when it clarifies branching.
- Worked examples with independently authored expected answers.
- One implementation owner, its read-through consumers, affected UI surfaces, and checking tests.
- A review date and an owner fingerprint after the rule has been read against the implementation.

IDs remain stable when titles change; retired IDs are not reused. Public decision-making APIs in a covered area cite their rule ID. A helper that is not a domain decision explains that distinction rather than acquiring an artificial rule.

Other documentation links to the rule rather than restating thresholds. Until an area moves into the catalog, the [current scoring guide](../scoring.md), [schedule documentation](../schedule-data.md), and corresponding source owners remain the references for existing behavior.

The implementation plan must add rule indexing, example execution, ownership coverage, and stale-review checks. These tools are deliverables, not commands that can be run in this repository today. Start with reliable checks for the extracted areas and extend coverage as owners migrate. A fingerprint prompts review; it cannot certify that prose or code is correct.

## Make snapshots and time explicit

Build immutable domain snapshots with an injected clock. A snapshot's decisions refer to one evaluation instant, its own data revisions, and its own preferences. Helpers must not consult the wall clock or global settings behind the caller's back.

Define a snapshot identity that includes every independent input capable of changing its answers: published data and policy revisions, live overlay revision, effective AI selection, preferences, time zone, and evaluation time. Presentation caches also account for locale and presentation settings. An identity is compared for equality; it is not automatically an increasing publication sequence.

Selectors must observe that identity or a deliberately narrower projection with equivalent dependency coverage. Test that advancing time alone, changing a league order, disabling a provider, replacing AI content, or changing time zone refreshes the affected display even when fixture bytes are unchanged.

Avoid rebuilding the entire page for each countdown tick. The session owns the next relevant time transition and can expose a narrow timer view model. Optimization must preserve freshness expiry, day boundaries, and AI expiry. Keep navigation, open sheets, and carousel position separate from data identity so a background update does not reset the reader's place.

## Persist before claiming success

Use a repository interface for network and cache access, with injected HTTP, storage, and clock implementations. UI code must not read an alternate cache path that bypasses validation or choose between fresh and cached records on its own.

Validate a complete publication before replacing the last known good snapshot. Commit its artifacts and active revision atomically; await the durable write before notifying consumers that the new revision is available. A failed fetch, incompatible schema, invalid hash, or interrupted write leaves the prior snapshot usable. A 304 response updates the check history without inventing a new observation time.

Keep preferences and personal AI imports separate from replaceable schedule data. A cache cleanup or feed migration must not silently clear user choices. If a preference save fails, retain the usable session state and explain that it was not saved; do not show a false success.

For SQLite, name columns in every insert and cross-table copy. Never depend on physical column order, including migrations. Use transactions and versioned migrations with tests for fresh install, each supported upgrade path, interrupted operations, differing column order, null fields, and rollback after failure.

Bound caches and diagnostics, and test eviction. Record network attempts separately from source observations: reaching GitHub Pages successfully does not prove that ESPN or a broadcaster supplied current data. Source age, the last successful validation, partial-source failures, and the phone's last refresh are different facts.

Pit Dash's full temporal database, seed distribution, and recorder daemons are not first-release requirements. Preserve enough validated snapshots and sanitized fixtures to reproduce defects; add a larger replay archive only when a concrete use case justifies it.

## Test behavior through its consumers

Use the cheapest test capable of detecting the failure. Pure rules belong in Dart unit tests; adapters need producer/consumer tests; persistence needs real storage tests; subscription and interaction behavior needs widget or integration coverage.

| Risk | Required evidence |
| --- | --- |
| Wrong watchability | Independent country, package, channel, replay, uncertainty, and rights-expiry examples against the domain owner. |
| Browser/mobile disagreement | The same fixture and preferences through the existing browser and Dart core during migration, followed by tests of both adapters using the shared owner. |
| Producer/consumer format drift | JSON emitted by the real Python publisher consumed by Dart and the browser bridge, including incompatible versions and omitted optional fields. |
| Stale live score or headline | Frozen-clock cases at the exact expiry boundary and on both sides, including an unchanged HTTP response carrying old source observations. |
| Preference changes ignored | A widget test changes the actual application setting and verifies the visible result, not just a notifier counter. |
| Too many or missing updates | Assert notification counts, intentional zero notifications, and unchanged-value no-ops. |
| Late response overwrites new state | Controlled completion order for overlapping refreshes, resume, cancellation, and preference changes. |
| Storage corruption or lost choices | Failure injection, transaction rollback, restart, supported migrations, and a cache clear that preserves preferences. |
| A source defect returns | A trimmed, sanitized real response sequence through the production parser and domain factory. |
| Layout or accessibility regression | Representative constraint/content cases, semantics assertions, interaction tests, and device inspection. |

Never derive an expected score by calling the scoring implementation being tested. Do not create all wire-format fixtures with the same encoder as the consumer under test. Keep real producer examples alongside synthetic boundary cases.

For a recorded failure, trim the recording to the smallest useful window and run it through the production path with a controlled clock. Assert independent invariants as well as the user's observed symptom. Small replay windows belong in normal CI; exhaustive multi-hour corpus sweeps are explicit maintenance work, not an automatic dependency of every change.

Test the whole chain from changed input to rendered output at least once for each observable setting. A correct `notifyListeners` call does not help a widget that used a non-subscribing read. Conversely, test intentionally silent setters so a refactor does not introduce notifications during build.

## Measure layout and preserve spatial stability

Use the component's actual constraints, not a device category alone. For text-fit decisions, measure the rendered font, weight, scale, and content with Flutter's text layout tools. Character-count estimates and a single global “phone width” breakpoint cannot determine whether variable network chips fit.

Define each component's information hierarchy before selecting a layout:

1. Essential: teams, match state or kickoff, score when applicable, and understandable viewing availability.
2. Useful: form, record, rating, and network routes.
3. Supplemental: venue, longer explanations, sources, and extended vitals.

These categories guide placement; they are not permission to discard information required by the product specification. Move supplemental material into the existing detail surface when appropriate, and keep the same domain facts available across card roles.

Write down the allowed layout tiers and test each transition. Prefer clear abbreviations or removal of redundant context before wrapping; grow the surface or use its details sheet before shrinking text to illegibility. Layout logic owns geometry, while view models own the meaning and wording.

Coordinate heights and layout choices across the reachable cards in a carousel, not only the current card. Recalculate when width, text scale, or content changes without making the page jump as the reader swipes. Keep the layout computation bounded for a long schedule; avoid repeated intrinsic measurement of the entire list on every frame.

Include long names, multiple broadcasters, no images, missing records, live scores, postponed fixtures, and large accessibility text in the test matrix. Check narrow phones, tablets, landscape, split view, and both light and dark settings where supported.

Use semantic labels and focus order for emblems, score dots, form squares, chips, and carousel items. Color alone must not indicate selection, freshness, or results. Essential actions need touch and assistive-technology access; hover-only disclosure is insufficient. Keep the visual swipe-only carousel preference compatible with semantic scrolling actions.

## Keep copy, help, and diagnostics trustworthy

Centralize user-facing copy in a reviewable catalog with named parameters and distinct plural variants. Compose strings in view models rather than assembling sentences in widgets. Validate missing keys and parameters in release-capable checks, not only debug assertions. The catalog format is a P4 choice; adopting the principle does not require copying Pit Dash's parser.

Use plain American English. Labels should explain the user's state, not expose internal package names or implementation machinery. Avoid canned commentary that makes a routine schedule calculation sound like reporting. A match story is AI content with provenance and freshness, not filler generated by the UI.

Treat in-app help as shipped behavior. Verify every factual claim through the actual rule, view model, and interaction, and change help in the same change as behavior. Prefer a short accurate explanation to a comprehensive but unreliable one. Keep developer-only controls out of consumer help.

Use named subsystem loggers with consistent levels: trace, debug, significant events, recoverable failures, and severe integrity failures. Release logging defaults to useful events rather than polling chatter. Log decisions, state transitions, revision IDs, durations, and structured errors with stack traces; do not log on every frame or countdown tick.

Redact credentials, authorization headers, personal prompt text, imported AI bodies, and sensitive URL parameters. Diagnostic export should be deliberate, bounded, inspectable, and safe to share. Logging and public outcome analysis must not become personal viewing-history collection.

## Keep development and releases reproducible

Pin the chosen Flutter/Dart toolchain and commit application lockfiles when the app is scaffolded. Use per-package analyzer configuration so every source file is analyzed under the intended boundary rules. CI must build generated bridge and contract artifacts from source and detect stale checked-in outputs if any are committed.

Maintain targeted suites during development, then run all affected analyzers, unit tests, contract tests, and integration checks before merging. Changes to shared domain owners require both browser and mobile consumers to be checked. Documentation-only edits need link and factual review, not a full mobile build. Do not report skipped or unavailable device checks as passing.

Test release mode as well as debug mode, including catalog validation, cache startup, link opening, logging defaults, and the absence of embedded secrets. Exercise Android and iOS on actual devices before release; an emulator or WebKit run is useful evidence but does not replace platform verification.

The P6 release record should name the source revision, toolchain, supported OS versions, migrations exercised, device matrix, known limitations, and rollback procedure. Preserve feed compatibility with supported installed versions; a website deploy cannot update an installed mobile binary.

Soccer Outlook keeps its own [zlib license](../../LICENSE). New code and original documentation use this project's ownership and attribution conventions. Preserve third-party code licenses and artwork notices. Pit Dash's BSD license, copyright holders, documentation license, trademarks, and commit-signoff policy are not automatically Soccer Outlook's policies.

## How Pit Dash's practices map to this plan

These references point to the reviewed Pit Dash revision so the rationale does not change underneath this plan. They are inspiration for original Soccer Outlook design, not a claim that its tooling has already been copied.

| Pit Dash practice | Soccer Outlook adoption | First gate | Reference |
| --- | --- | --- | --- |
| Four layers and one immutable domain construction path | Separate source adapters, pure decisions, session orchestration, and rendering; use one shared domain implementation after bridge validation. | P0–P2 | [Architecture](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/README.md#architecture), [ownership guidance](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/AGENTS.md). |
| Rules with owners, worked examples, and review checks | Start with extracted Soccer rules and expand coverage; Pit Dash itself introduces its catalog area by area. | P2 | [Rule catalog](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/rules/README.md). |
| Complete snapshot identity and injected time | Include Soccer-specific preference, publication, live-overlay, AI, and time dependencies; do not copy Pit Dash's team-oriented identity tuple. | P2–P3 | [Selector and snapshot guidance](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/AGENTS.md), [data layer](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/DATA_LAYER.md). |
| Durable cache, explicit migrations, separate observation and validation | Atomic last-good publications and independent preferences, without requiring a full temporal database. | P1–P3 | [Cache design](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/CACHE_DB.md), [source health](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/SOURCE_HEALTH.md). |
| Production-path replay with independent invariants | Small sanitized source windows in normal CI; optional larger replay analysis. | P2–P5 | [Replay validation](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/REPLAY_VALIDATION.md). |
| Measured responsive layout and coordinated surfaces | Constraint-driven match cards, stable carousel heights, tested content tiers, and accessible equivalents. | P4 | [Layout guidance](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/AGENTS.md). |
| Copy and help are part of the product | Central catalog and verified help, with release-mode validation. | P4–P5 | [Copy system](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/COPY.md). |
| Useful logs and reproducible releases | Named, redacted diagnostics and evidence tied to a release revision. | P3–P6 | [Logging](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/docs/LOGGING.md), [review guide](https://github.com/caparomula/pit-dash/blob/865fa263062b0870b9ae71555132d4ace894dccf/CODE_REVIEW_PROMPT.md). |

## About this document

This proposal was written on October 20, 2026, from Soccer Outlook `cb0475e` and Pit Dash `865fa263062b0870b9ae71555132d4ace894dccf`. The [implementation plan](implementation-plan.md) assigns delivery and verification gates. These standards become enforceable as the named checks are implemented; this document does not claim that the companion app or those checks already exist.
