# Match history and retrospective analysis

Every production refresh saves the evidence behind the ratings, then later refreshes record the available results for those same fixtures. The aim is to compare our assessments with actual outcomes without reconstructing a prediction after the match has happened.

This is a record of public match data. It does not track what visitors watched, collect their filters or upload personal AI imports. Publicly shared AI recommendations can be included because they are already part of the site's published data.

## What is recorded

Each snapshot includes every match processed for the schedule, including disabled leagues, unavailable broadcasts and finished matches. Events rejected as unreadable or deliberately excluded by the schedule parser are not archived as matches; their counts remain in the source-health report. It preserves:

| Data | Contents |
| --- | --- |
| Fixture identity | ESPN match and league IDs, teams, kickoff, status, stage and venue. |
| Outlook score | The total, all five component values, which components were missing, and their inputs: competition tier, occasion score, team/table facts, first-listed odds, implied draw chance, goal line and broadcast coverage. |
| Automatic AI rating | Popularity, expected gameplay, competitive impact and their weighted total, when supplied, with the model and generation metadata. |
| Shared AI rating | An available published rating, its original prompt time and its blend weight. Shared imports supply a total score; their format does not provide the three automatic AI dimensions. |
| Ranking context | Scoring settings, source revision and file fingerprints, default services, league order and default league selection. Derived scores use this default order, not visitors' saved preferences. |
| Observed outcome | Match status, score, available goal events, reported attendance and venue capacity when supplied. Occupancy is attendance divided by positive capacity when both are present; the original attendance field and venue details are also retained. |
| Provenance | Observation time, build start time, source URLs, source-health report and broadcaster verification details. |
| Sourced measurements | Optional attendance, capacity, audience, video-view, search-interest or news-count observations supplied separately with their definitions and sources. |

The AI popularity rating is an assessment of audience appeal, not a numerical attendance or viewership forecast. Likewise, the Outlook TV component measures the type of listed outlet, not how many people watched. Those distinctions matter when evaluating the score.

An absent or zero-placeholder attendance value becomes `null`, with `attendance_status: "not_reported"`; it does not mean an empty stadium. The regular television and streaming audience fields remain `null` because the current feeds do not provide those figures. Separately sourced observations can be attached as described below; this archive does not infer an audience count from a popularity rating.

## Timing and completeness

New snapshots are appended, never substituted for an earlier observation. The record starts when this feature is deployed. The archive does not claim to recover ratings that were not previously saved.

For pre-match analysis, use an observation made before the recorded kickoff while the fixture was still scheduled and its time was confirmed. `forecast_eligible` marks this for the build metrics. AI ratings have a separate eligibility flag based on when they were captured: an AI response captured after kickoff is not accepted as a pre-match prediction, even if the build fetched its fixture earlier. The recorded story timestamp describes a bundle that may contain reused ratings; it does not establish a precise generation time for each rating. A shared response's timestamp is its original prompt time. Both are retained without treating them as proof of a pre-match capture.

Join observations using the league and ESPN fixture IDs, then compare the last eligible pre-match snapshot with later reported outcomes. Preserve kickoff changes when doing this; a rescheduled match must not inherit a misleading cutoff. Do not use recalculated post-match Outlook scores as though they were earlier predictions. The archive keeps both so their timing remains visible.

Normal builds run three times a day and fetch a limited date window. They can miss a newly added match's pre-kickoff state or an attendance figure published after the fixture leaves that window. Failed source requests are recorded in the build report. Browser live-score updates are not sent back to this archive. Consequently, the record is a series of observations, not a guaranteed complete play-by-play feed or final statistical database.

For attendance comparisons, account for competition, venue capacity and match context where data allows. Television audiences need comparable territory, language, distribution and measurement definitions; attendance and TV audiences should not be combined as interchangeable outcomes. The archive and CSV report provide the evidence for analysis. They do not automatically recalibrate scores or publish accuracy claims.

## Add sourced measurements

Optional `content/match-observations.json` supplies measurements that the regular feed does not provide. Create it when there is a real observation to record; an absent file is valid. Its envelope is:

```json
{
  "version": 1,
  "observations": []
}
```

Each observation requires these fields:

| Field | Meaning |
| --- | --- |
| `id` | A unique stable ID for this observation, using letters, digits, underscores or hyphens. |
| `match_id` | The ESPN fixture ID, as a string. It can refer to an older fixture outside the current build window. |
| `metric`, `value`, `unit` | A supported metric below, a nonnegative value and its exact unit. Counts must be whole numbers. |
| `observed_at` | When the measurement was recorded, as an ISO timestamp with a timezone. It cannot be in the future. |
| `source_url` | The public HTTPS source supporting the measurement. |
| `scope` | What was measured: territory, platform/channel, language and audience definition, or the video's publisher, search query or news query. |

Optional fields are `league_id`, `source_title`, `source_published_at`, `window_start`, `window_end` and `notes`. A measurement window requires both endpoints and must end no later than the observation time. Video, search and news measurements require a window. `source_published_at`, if supplied, cannot follow `observed_at`.

| Metric | Unit | Comparison to make later |
| --- | --- | --- |
| `attendance` | `people` | Compare popularity with reported attendance, within similar competitions and venues. |
| `stadium_capacity` | `seats` | Combine with attendance to calculate occupancy; account for match-specific capacity restrictions. |
| `average_viewers`, `peak_viewers`, `unique_viewers` | `viewers` | Keep each audience definition separate and compare like territories and distribution. |
| `highlights_views` | `views` | Use official videos measured at consistent intervals, such as 24 hours and seven days after publication. |
| `search_interest` | `index` | Preserve the query, geography, time window and normalization group for the 0–100 index. |
| `news_articles` | `articles` | Preserve the search terms, publication set, deduplication method and time window. |

These are supported inputs, not automatic integrations. None is treated as an interchangeable audience total or silently added to the Outlook score. The archive calculates occupancy when the feed supplies both attendance and positive capacity. A ratio above one is preserved rather than capped, so inconsistent venue figures remain visible. Video, search and news comparisons still need consistent definitions and suitable baselines; these counts are not automatically normalized.

The next refresh validates the file and saves its entries in the snapshot's `external_observations` array. It preserves observations for older fixtures so late audience reports can be joined to their earlier forecasts. Missing files produce an empty list; invalid entries stop archiving and publication. Validation checks structure and consistency, not whether the cited source is accurate. Use distinct observation IDs for different channels, measurement windows or definitions, and keep notes when correcting a value. Earlier snapshots retain the previous version.

## Storage and access

Production history is stored on the repository's [`match-history` branch](https://github.com/caparomula/soccer-outlook/tree/match-history), under:

```text
snapshots/YYYY/MM/<UTC timestamp>-<source revision>-<run ID>-<attempt>.json.gz
```

Each file is a complete JSON snapshot compressed with gzip, rather than a list of changes. Ordinary Git history preserves every append. The workflow uses no force push and has no retention expiry for this branch. The generated site's `gh-pages` branch and its 14-day Actions artifacts are separate; replacing or expiring those does not discard the history. A failed archive write stops site publication.

To get a local copy without switching your working branch:

```sh
git clone --single-branch --branch match-history \
  https://github.com/caparomula/soccer-outlook.git ../soccer-outlook-history
```

The workflow's `generated-site` artifact also contains `work/metrics.json` and `work/history-snapshot.json.gz` for inspecting an individual run, including available files from a failed publication. Archive existence proves capture, not successful Pages deployment; consult the associated workflow run to establish publication.

## Generate a snapshot locally

```sh
python3 build.py --out site/index.html --facts work/facts.json --metrics work/metrics.json
python3 history.py --metrics work/metrics.json --story site/story.json \
  --shared site/shared-ai.json --observations content/match-observations.json \
  --out work/history-preview.json
```

The build needs the normal public data sources. `history.py` makes no network requests or paid AI calls. Missing optional AI files are allowed; available ratings are recorded separately from the fixture metrics. A `.json.gz` output filename enables gzip compression; plain `.json` is useful for inspection. The output is immutable: writing the same payload again is allowed, but a different payload cannot replace an existing snapshot. Use a new output filename for a later capture. These local commands do not push history to GitHub.

## Export a retrospective comparison

After cloning the history branch as above, run:

```sh
python3 history_report.py --archive ../soccer-outlook-history --out work/match-history.csv
```

The report reads `.json` and `.json.gz` files under `snapshots/` recursively and writes one row per league and fixture. It freezes the latest eligible pre-kickoff observations for the latest known kickoff, keeping the AI dimensions, Outlook components, totals and provenance separate from subsequent results. A rescheduled fixture without a forecast for its new kickoff has blank prediction fields. Post-match recalculations never fill those fields.

Outcome columns include the reported score, attendance, capacity and occupancy where available. To put attendance in context, the report also looks for earlier matches with the same home-team ESPN ID, league and venue whose completed attendance was already observed before the target kickoff. It records how many qualify and their median attendance. An attendance-to-baseline ratio requires at least three such matches; a smaller sample's median is descriptive only. Current and future matches cannot enter that baseline.

Missing values remain blank in the CSV. Early reports will have little history and many empty outcome or baseline fields. Separately sourced external observations remain in the JSON archive for analysis by their metric and scope; the CSV does not merge television, streaming, video or search measurements into one audience figure. This command makes no network requests and does not modify the snapshots or the ranking formula.

See [How matches are ranked](scoring.md) for the formulas, and [Development and maintenance](development.md#automated-builds-and-publishing) for the production workflow.
