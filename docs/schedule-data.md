# Schedule data and coverage

Soccer Outlook combines fixture data, broadcast listings and service-plan mappings. These answer different questions: when a match takes place, which US networks list it, and which selected plans carry those networks. A channel listing does not verify a viewer's account, local affiliate, blackout restrictions or access to a separate streaming app.

## Discovering broadcasts

ESPN supplies the fixture list and scores. Supplemental adapters check supported competitions on TUDN, FOX, Watch ESPN, Paramount+, ViX, Peacock, Apple TV, Fandango and beIN Connect. They also inspect fixtures that already have a channel, since a missing simulcast may be the viewer's available route. Supported competition sets are explicit in `broadcast_listings.py` and each adapter; this is not universal coverage of every broadcaster.

Confirmed channels are merged. A missing listing never erases another source's positive evidence or becomes proof that no US broadcast exists. A usual-rights arrangement remains weaker than a dated match listing.

The adapters require appropriate competition, team, date, country and live-event evidence. They do not use AI prose to decide availability. Some specific matching rules are:

- TUDN combines results and upcoming widgets with bounded, validated match-page candidates. Its results page alone may still show the previous round. A candidate URL confirms nothing until the page's event identity, US stream metadata and television coverage agree.
- FOX's dated TV schedule is joined to scoreboard event/team IDs. Only explicit FOX-owned US channels are promoted. Third-party and ambiguous codes are retained as observations; `NBCU`, for example, cannot identify a particular network. Fox Deportes does not grant YouTube TV main-plan access.
- Watch ESPN uses the site's Eastern-date links. Reviewed team aliases and compatible simulcasts are supported. A documented pregame window can precede kickoff; it does not move the fixture's kickoff.
- Apple supports both observed MLS schedule shelf formats. Paramount handles live-event URLs separately from show links. ViX can confirm an explicitly announced US premium event before its player link becomes active.

A homepage carousel can cover fewer dates or events than the app requests. Its absence of a match is not a complete search. A program replay, a foreign channel, a similarly named club or a generic provider logo cannot confirm a live US airing.

## Reading diagnostics

Every full or lightweight build writes `coverage.json` beside the page. The full `build-report.json` is retained in the Actions artifact. The run summary shows source counts.

| Field | Meaning |
| --- | --- |
| `fixture_sources_complete` | The ESPN requests and interpreted fixture records passed the builder's checks. It does not assert complete broadcast discovery. |
| `broadcasts` | Per-source requests, confirmations, unresolved candidates, failures, parsed pages, requested/observed dates and reason counts. |
| `fixture_comparison` | The independently checked competition/date scope and any omissions, time conflicts, ambiguous identities or unavailable sources. |

An observed date range describes entries actually seen, not proof that every intervening event was supplied. `observed` means the parser saw listings; `outside_requested_horizon`, `empty`, `partial`, `unavailable` and `not_checked` require different investigation. A source with no confirmations may legitimately offer only selected matches.

Each match's archived metrics also retain all lookup outcomes and a channel-specific evidence list. It records the source, public URL, observation time, and available source identity/airing details. Request diagnostics remove client keys and tokens. The AI facts remain concise; internal request logs are not inserted into research prompts.

## Independent fixture checks

The first independent comparison covers **Liga MX**, using FOX's dated scoreboards and verified team IDs. Other competitions are explicitly reported as unsupported. It checks the selected source date and reports matches absent from one source, kickoff differences and ambiguous identities. ESPN responses are separately checked for explicit league/date contradictions and result-limit exhaustion.

Comparisons are diagnostic: they do not invent fixtures, change kickoff times, infer broadcast rights or set live status. For example, FOX's five-minute difference for Tijuana–Puebla is reported while ESPN's kickoff remains intact. Empty, stale or incompletely parsed secondary responses cannot establish a definite omission. New competition comparisons need verified identity mappings and representative real response tests.

## Refreshes and retained evidence

Full builds run three times daily. The **Refresh schedule** workflow runs at minutes 17 and 47 each hour, reusing standings and existing AI output. It makes no AI requests. GitHub may delay these jobs. Both workflows share a serialized queue that retains pending runs, so a schedule tick does not displace a queued full refresh. They check the current `main` revision before building, generating AI, archiving and publishing; a superseded run stops instead of replacing the newer site.

The lightweight job requires a compatible `schedule-context.json` with standings less than 30 hours old. Reuse does not renew the standings timestamp. Missing, stale or incompatible context skips publication until a full build succeeds. Incomplete ESPN fixture responses stop a lightweight build before it replaces the page. Full builds also keep the last published page and context when compatible prior context exists and the new fixture responses are incomplete. This prevents a partial full build from deleting the cache needed by later lightweight updates. A first build without usable prior context retains the builder’s existing source-health policy.

Previously verified broadcast evidence can bridge a source outage for **at most six hours from its original verification**, only for the same fixture, teams and kickoff. Current channel mappings are reapplied. Explicit conflicting, ambiguous, country, package, replay or identity rejections prevent retention; a partial source failure does not overrule such a rejection. Reuse does not reset the expiry. Details identifies retained evidence and its original observation time.

Open pages poll `schedule.json` every five minutes while visible. A validated snapshot replaces fixture rows, applies kickoff/channel changes and adds or removes matches while preserving preferences, AI input and newer live scores. Failures retain the currently displayed schedule and back off up to 30 minutes. Complete empty schedules are valid. Snapshots from a different renderer/configuration require a page reload. Standings in an already-open page remain from its full page load.

## Running and testing

```sh
python3 build.py --out site/index.html --report work/build-report.json
python3 build.py --lightweight --out site/index.html --report work/build-report.json
python3 -m unittest
.venv/bin/python -m tests.browser_schedule
```

Both build commands avoid AI generation; `story.py` is the separate daily AI step. `--previous-context PATH` can read context from another output directory. The generated context contains public schedule facts and standings, with no account credentials. Do not edit generated files to correct rights: change the sourced mapping or adapter and rebuild.

Tests include recorded upcoming/live payloads, stale rounds, date boundaries, partial catalogues, simulcasts, package exclusions, wrong countries, replays, source failures, safe evidence retention and browser updates. The public-source checks also need occasional live verification: offline tests cannot detect a website redesign until a representative new capture is added.

Return to [development and maintenance](development.md) or the [project overview](../README.md).
