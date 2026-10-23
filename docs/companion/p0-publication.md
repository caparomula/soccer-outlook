# P0 real publication and default-view evidence

October 20, 2026. This closes the initial real-publication capture gap in P0.
It is one part of the [combined P0 evidence](p0-evidence.md); it does not validate
the draft companion feed or resolve the published source discrepancies below.
No production renderer, parser or exporter changed.

The [capture tool](../../tool/companion_publication_capture/README.md) fetched
the [published website](https://caparomula.github.io/soccer-outlook/) and five
associated public resources at **2:53 p.m. Eastern**. The compact
[historical record](../../tool/companion_publication_capture/captures/2026-10-20.json)
contains exact source URLs, response statuses, request/receipt times, ETags,
byte counts and SHA-256 values. Full responses and browser output remain in
ignored `work/companion-publication/`; retaining that directory enables exact
offline replay after the website changes.

## Captured publication

The HTML was built at **11:22:45 a.m. Eastern**. `schedule.json`,
`schedule-context.json` and `coverage.json` have that same publication timestamp
with fractional-second precision. Standings retain their earlier
**10:36:35 a.m. Eastern** observation timestamp.

| Published resource | Status | Bytes | Observed role |
| --- | ---: | ---: | --- |
| HTML | 200 | 2,613,030 | Embedded renderer, defaults, styles and 360 fixture rows |
| `schedule.json` | 200 | 2,273,178 | Version 1 legacy rendered-row update transport |
| `schedule-context.json` | 200 | 902,289 | Internal dataclass-shaped public build cache and standings |
| `coverage.json` | 200 | 21,247 | Fixture/broadcast source diagnostics and comparison |
| `story.json` | 200 | 126,677 | Existing automatic editorial and 194 ratings |
| `shared-ai.json` | 404 | 9,379 | Captured absence; the response body is a Pages error page |

HTML, schedule and context all contain the same **360 unique fixture IDs** and
runtime revision
`b58f6da3002b7c2e261cee17af83598be5b8334380f5f0219f0b919f41d48952`.
Every allowlisted legacy fact agrees between HTML and schedule rows. The
context comparison checks complete ID agreement and representative scores and
evidence; it is not a full semantic context-to-HTML reconciliation.

The corpus includes 166 completed, 192 pre-match and two in-progress fixtures.
The publisher assigns 204 listed-channel routes, 23 usual routes and 133
fixtures without a default service. The report preserves all fixture IDs,
published defaults, source diagnostics, ten representative normalized legacy
records, their evidence and score shapes, and populated-standings league IDs.
It deliberately excludes complete HTML, full tables, images and editorial prose.
The normalization is an inspection allowlist, not the proposed v1 representation.

## Actual browser outputs

The tool replays the original public bytes and status codes in fresh contexts
at the HTML publication instant, with locale `en-US`. All other requests are
aborted, including provider endpoints, fonts and badges. The isolated contexts
have no user browser state, imports, credentials or saved preferences.
Loading captured `story.json` does not make an AI request.

The matrix covers Chromium 151.0.7922.34 and WebKit 26.5, each at Eastern
1280/390 pixels and Pacific 390 pixels. All six runs complete without page
errors and agree on:

- **44 upcoming default matches**, with nine today, 13 tomorrow, 18 on day two
  and four on day three.
- **88 schedule members** including 44 results in folded groups. Forty-four
  rows are rendered outside folded groups; this is distinct from both the
  entire 360-fixture publication and the 110 rows parked outside the window.
- Opening carousel selection **401915404**, Sabah FK–Borussia Dortmund.
- Picks **401915399**, Paris Saint-Germain–Barcelona; **401915312**,
  Bayern Munich–Arsenal; **401915313**, Real Madrid–RB Leipzig, in kickoff order.
- AI text remains off and the headline section hidden, while automatic
  ratings still contribute. Paris Saint-Germain–Barcelona displays a
  **91.1 pick score and five of six dots**.

The Pacific grouping moves result **401874771** from day minus two to day
minus three without changing the selected fixture set. Eastern has 12 and 28
results in those groups; Pacific has 11 and 29. This preserves a real local
calendar case for eventual core parity, beyond the synthetic time corpus.

The JavaScript-disabled pass records the actual static fallback: its tally is
also 44, but all **360 rows** remain visible in Eastern calendar-date groups,
without carousel/picks/rating controls. That is the currently emitted fallback
behavior, not a claim of default-view equivalence. A future shared default
projection needs a deliberate decision about which fallback behavior to retain.

## Existing discrepancies and contract gaps

1. **Complete fixture collection is not complete broadcast evidence.** The
   publication reports 306/306 usable fixture requests and a complete schedule.
   The ESPN+ broadcaster lookup is unavailable after three failed requests;
   TUDN is partial; Peacock, Fandango and beIN report empty listings with unknown
   horizons. These facts cannot become either guaranteed entitlement or a
   definitive no-broadcast result in the companion feed.
2. **Independent source comparison remains incomplete.** It is explicitly
   observation-only, with two failed requests and four Liga MX kickoff
   conflicts. Monterrey–Guadalajara differs by 60 minutes; Tigres–León,
   Pumas–Tigres and Pachuca–Cruz Azul differ by ten minutes. This capture records
   those existing diagnostics; it does not adjudicate the upstream times.
3. **Legacy score strings need explicit decoding.** For pre-match
   Paris Saint-Germain–Barcelona the context contains home/away `"0"` strings;
   both static and scripted browser rows show empty, hidden scores. A proposed
   numeric nullable score must not treat the cache's placeholders as observed
   0–0 play. Completed samples also preserve real string zero scores.
4. **Current formats are not the draft feed.** There is no companion manifest,
   immutable object transaction, policy compatibility ID or normalized fixture
   status here. The runtime hash identifies matching legacy renderer inputs,
   not the proposed publication revision. HTML `data-outlook-parts` contains
   rounded percentages, while the proposed facts carry original normalized
   components and raw inputs.
5. **Evidence is incomplete for future semantics.** Real broadcast records
   include source/country/check times but do not themselves carry all proposed
   fixture bindings, retention expiry and source date scopes. The successful
   fixture-request count alone does not prove every viewer-local window.
   Populated table presence does not establish source form ordering.
6. **This is a fixed default snapshot.** It contains no unknown kickoff sample,
   complete-empty collection, successful shared import, or visible live match
   on the default lineup. It does not exercise personal preferences, timeout,
   live-to-final transition, source refresh, DST transition or native clients.
   Those cases remain synthetic or separate gates. The frozen publication
   instant isolates domain comparison and is not a claim about the site's
   live output three hours later when bytes were retrieved.

No discrepancy above is silently repaired by this capture. P1 still needs
production exporter/independent-parser round trips, complete route and score
semantics, exact source coverage and publication lifetime checks. P2 still needs
old-versus-new Python default/service and browser decisions over the retained
facts. Current DOM outputs supply migration observations, not their own
correctness oracle.

## Verification and reproduction

The six browser captures and one static pass were recorded against hash-checked
raw responses. All six offline capture tests pass. They cover the observed IDs/picks/defaults,
source-health limitations, local result regrouping, actual string-score versus
display behavior, unknown-kickoff minimization and corruption rejection.

For this prepared checkout:

```sh
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright" \
work/companion-venv/bin/python tool/companion_publication_capture/capture.py \
  --output tool/companion_publication_capture/captures/2026-10-20.json

work/companion-venv/bin/python -m unittest tests.test_companion_publication_capture -v
```

The first command replays retained raw data. Add `--fetch` and choose a new
output filename to make a new, explicitly dated public capture; the website may
have changed. Nothing is published by either command.
