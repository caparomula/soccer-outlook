# Development and maintenance

Soccer Outlook is a static site. Python fetches fixtures and builds the page; JavaScript applies the visitor's preferences and updates live scores. AI ratings are optional and published separately as `story.json`.

For how the page works, see the [user guide](user-guide.md). For the ranking formula and default league order, see [scoring](scoring.md).

## Run it locally

The schedule builder needs **Python 3.11 or newer**, `curl`, and network access to ESPN. It uses Python's standard library; no API key or Python package installation is needed to build the schedule or run unit tests.

From the repository root:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --facts work/facts.json --warnings work/mapping-report.txt
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open <http://127.0.0.1:8000/>. Stop the server with Ctrl+C. Serving the directory over HTTP lets the page load `story.json` if one exists beside `index.html`.

By default, the builder fetches six dates: yesterday, today and the following four days. The extra dates support results and a page left open across a day change. The visible schedule covers today and the following three days; see the user guide for the 4 a.m. day boundary. The AI's corresponding window uses Eastern time, while the browser groups matches using the visitor's local time.

Useful build options:

| Option | Purpose |
| --- | --- |
| `--facts work/facts.json` | Save the fixture and team data used by `story.py`. This does not call an AI model. |
| `--warnings work/mapping-report.txt` | Save broadcast-mapping warnings; the file is empty when the report is clean. |
| `--date YYYY-MM-DD` | Choose the Eastern date used to fetch fixtures and evaluate rights. It does not change the build timestamp or the browser's clock. |
| `--days-back N --days-ahead N` | Change the dates fetched. This does not change the browser's display window. |
| `--no-logos` | Build without team or league images. |
| `--embed-images --logos logos.json` | Embed images instead of linking to ESPN; cache downloaded images in the named file. |
| `--fragment` | Write the page body for a host that supplies its own document wrapper. |

For a browser preview at a chosen local time, append a fragment such as `#at-20261009-1300` to the page URL. The fixtures still come from the build; the fragment does not fetch a historical schedule. Use `python3 build.py --help` for all options.

## Where to make changes

| File or directory | Responsibility |
| --- | --- |
| [`build.py`](../build.py) | Fetch and interpret ESPN data, validate configuration, calculate the Outlook score and generate HTML. Also defines tracked leagues, default services and featured teams. |
| [`rights.toml`](../rights.toml) | Broadcaster aliases, services, simulcasts and each competition's usual coverage, with sources and check dates. |
| [`settings.toml`](../settings.toml) | AI configuration, score weights and golden-dot thresholds. |
| [`web/page.html`](../web/page.html) | Page structure. |
| [`web/styles.css`](../web/styles.css) | Layout, themes and responsive styles. |
| [`web/app.js`](../web/app.js) | Filters, saved preferences, match cards, scoring in the browser, details overlays and live updates. |
| [`story.py`](../story.py) | Generate, validate and reuse optional AI ratings and text. |
| [`providers.py`](../providers.py) | Supported models, provider requests, configuration validation and price estimates. |
| [`tests/`](../tests/) | Unit tests and the browser test harness. |
| [`.github/workflows/`](../.github/workflows/) | Automated builds, browser checks and optional AI comparisons. |
| [`.github/scripts/`](../.github/scripts/) | Mapping-issue maintenance and AI comparison reports. |

The generator inlines the three `web/` assets into the HTML. Rebuild after editing them. Deployment consists of `index.html`, optional `story.json` and a `.nojekyll` file; there is no application server or JavaScript bundler. Generated files in `site/` and test artifacts in `work/` are ignored by Git.

## Test changes

Run the unit tests from the repository root:

```sh
python3 -m unittest -v
```

They cover rights and settings validation, ESPN parsing, scoring, rendering, provider responses and AI reuse/failure behavior. Tests use fixtures and mocked requests; they do not spend API credits. Most rendering and scoring tests use fixed settings so changing a valid production weight does not change their expected results. Separate tests validate the real configuration files.

For browser checks, create a virtual environment and install the test dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m tests.browser
```

On Linux, missing system libraries can be installed with `.venv/bin/python -m playwright install --with-deps chromium`. The harness uses Playwright's Chromium by default; set `BROWSER_EXECUTABLE` to an existing browser executable only when you intend to override it.

Browser tests use a synthetic schedule, a fixed clock and local responses for ESPN and AI data. They check filters, persistence, rankings, live transitions, details dialogs, keyboard navigation and responsive layouts. Screenshots go to `work/browser/`; pass `--artifacts PATH` to choose another directory. External fonts are replaced with consistent fallback fonts. These tests cover Chromium, not Safari or Firefox, and do not verify ESPN's current network availability or the accuracy of real AI output.

For a refactor that should preserve output exactly:

```sh
.venv/bin/python -m tests.browser --baseline-build /path/to/baseline/build.py
```

Use a complete checkout of the baseline revision so its supporting files and assets are available. Both generators receive the same fixture data and build time. The harness first requires byte-identical document and fragment HTML, then compares screenshots across 12 viewport/theme/UI combinations. Pixel comparisons allow antialiasing differences using pixelmatch's threshold of 0.1; any remaining differing pixel fails and produces a diff image. The ordinary behavior suite primarily exercises the current version. This comparison is deliberately unsuitable for an intentional visual change.

## Configure AI ratings and text

The current [`settings.toml`](../settings.toml) configuration enables the **ratings** design with `gpt-6.1-sol` at `low` effort. It requests scores without web research. The optional overview and card blurbs are disabled, so a page without AI prose is expected.

The `[ai]` table controls this behavior:

| Setting | Effect |
| --- | --- |
| `enabled = false` | Disable AI calls and prevent the browser from requesting AI data. Picks still use the Outlook score and league priority. |
| `design = "ratings"` | Rate upcoming fixtures within the display window. Optionally add separately researched overview text or card blurbs. |
| `design = "full"` | Ask Claude for researched overview and league context, plus ratings and blurbs for upcoming fixtures. Requires a supported Claude Sonnet or Opus model. |
| `model`, `effort` | Choose the main model and its reasoning effort. |
| `overview_model`, `overview_effort` | In the ratings design, add one researched overview covering the available slate across services and competitions. This overview is the same for every visitor. Leave both out to disable it. |
| `blurbs_model`, `blurbs_effort` | In the ratings design, research blurbs for six leading fixtures using the default league order. Individual visitors' filtered picks can include other matches without blurbs. Leave both out to disable this step. |

Supported model names and effort values are listed in `providers.py`; choose them in `settings.toml`. Add each configured provider's key as a repository Actions secret under **Settings → Secrets and variables → Actions**:

| Provider | Secret |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GEMINI_API_KEY` |

For local generation, provide the appropriate key as an environment variable with the same name. OpenAI and Google use standard-library HTTPS requests. Claude also needs the Anthropic SDK:

```sh
.venv/bin/python -m pip install 'anthropic>=1.11,<2'
```

After building `work/facts.json`, this command generates AI data with the configured model and **makes a paid API request** when a valid key is available:

```sh
python3 story.py --facts work/facts.json --out site/story.json --mode daily --usage-out work/usage.json
```

Use `.venv/bin/python` instead of `python3` when the model needs the SDK installed in that environment. To allow reuse, add `--previous PATH` pointing to the earlier `story.json`. Running without that argument gives the script no previous result to reuse.

### Generation modes and reuse

| Mode | Behavior |
| --- | --- |
| `daily` | Generate once for the facts' date and current AI configuration; reuse a matching result from that day. Used by scheduled builds. |
| `keep` | Copy the previous result without an API request, even if it is from an earlier date. If the previous result uses a different AI design, model or effort, generate with the new configuration instead. Used for ordinary code pushes. This is also the script's default mode. |
| `refresh` | In the full design, update today's research and re-rate fixtures within 24 hours or missing a rating/blurb. Without today's result, start a full run. In the ratings design, generate fresh ratings. |
| `full` | In the full design, research and rate from scratch. In the ratings design, generate fresh ratings. |
| `auto` | In the full design, reuse a result less than three hours old, otherwise refresh; generate from scratch if none exists for today. In the ratings design, behave like `daily`. |
| `ratings` | Request scores only. Optional overview/blurbs configured for the ratings design run afterwards. |

The settings take precedence over the mode's research design. `--ignore-switch` bypasses the `[ai]` table for comparison runs; it can therefore call an API even when production AI is disabled. Normal builds should not use it.

Changing the configured model, effort, design or optional text models triggers new generation on the next build when the previous story records a different configuration. Changing scoring weights alone does not require a new AI call. The workflow also detects a published page with AI disabled, so turning AI back on requests the day's result.

If a provider key is missing or generation fails, `story.py` reuses a matching result from the same day when available. Otherwise it writes no new result, and the schedule remains usable with the Outlook score. Deleting a key is therefore not a reliable way to turn existing AI content off; use `enabled = false`.

AI-off runs write nothing, rather than deleting an existing output file. The deployment workflow builds in a fresh directory. For a clean local preview, remove an old `site/story.json` when you want to test its absence; an AI-off page ignores that file regardless.

The browser fetches AI data every ten minutes while visible. It rejects results older than 30 hours or past their explicit `focus_until` horizon, currently 24 hours after the facts were built. Copying an old result with `keep` does not renew that horizon.

### What validation guarantees

Ratings must contain valid scores for known fixture IDs. Missing ratings get one further request for the missing fixtures; the full design also retries missing card blurbs. Full-design research can request a repair for missing overview or league text. These retries improve coverage but do not guarantee that every match or league receives text. Valid partial results can still be published.

Researched text must cite sources returned by the model's tools. Full-design league and match context may instead use supplied ESPN facts, explicitly labelled as such. Ratings-design optional overview and blurbs require searched sources; known dead links are removed. A site's refusal to answer automated requests is treated differently from a confirmed dead link. Source checks establish provenance, not that every sentence correctly summarizes its source.

## Automated builds and publishing

[`Refresh outlook`](../.github/workflows/refresh.yml) runs at **08:50, 16:50 and 22:50 UTC** each day. That is 4:50 a.m., 12:50 p.m. and 6:50 p.m. during Eastern daylight time, an hour earlier during Eastern standard time. GitHub can delay scheduled runs.

The workflow also runs on relevant changes to `main`, and can be started from **Actions → Refresh outlook → Run workflow**. Its manual `storylines` choice selects `refresh`, `full` or `keep`; the configured AI design still applies. Documentation-only changes do not trigger this workflow.

Each run:

1. Runs unit and browser checks.
2. Builds the HTML, AI facts and mapping report.
3. Rejects a page smaller than 200,000 bytes or with fewer than 50 match rows.
4. Generates or reuses optional AI data. Scheduled runs use `daily`; ordinary pushes use `keep`, with the configuration-change exceptions above.
5. Replaces the `gh-pages` branch with a single commit containing the generated site.
6. Opens, updates or closes the broadcast-mapping issue as needed.

GitHub Pages serves the `gh-pages` branch. When configuring a fork, set Pages to **Deploy from a branch**, choose **gh-pages**, and use the root directory after the first successful publish. The workflow requests `contents: write` and `issues: write`; no separate deployment token is configured beyond GitHub's supplied token.

A failing unit test, browser assertion, invalid configuration or incomplete build stops publication and leaves the existing site in place. The builder also refuses a run with no fixtures or failures from more than half the scoreboard requests. Smaller gaps can publish and are disclosed on the page.

There are two intentional exceptions: if Chromium cannot be installed or started, the refresh workflow publishes with a warning; and AI generation or mapping-issue failures do not block the schedule. The separate [`Browser checks`](../.github/workflows/browser.yml) workflow runs on relevant pushes and pull requests and saves screenshots even when checks fail. It does not have the refresh workflow's browser-setup bypass.

### Costs and comparison workflows

AI generation reports token use, elapsed time and estimated cost in the Actions summary. `--usage-out PATH` saves a machine-readable report. Prices come from `providers.py` and are estimates, not invoices; provider search charges may be incomplete when an API does not report them.

Two manually invoked workflows compare models without publishing a page:

- [`Compare storyline models`](../.github/workflows/compare-storylines.yml) compares Claude configurations using either the full design or scores only. Each listed configuration makes paid requests; there is no dollar-budget input.
- [`Compare AI providers`](../.github/workflows/compare-providers.yml) compares ratings, researched blurbs and overviews across providers. Enter `none` for a task to skip it. Its budget stops a task from starting when estimated total spending would exceed the limit; it is not a provider-enforced spending cap. Results appear in the summary and a JSON artifact.

Use the run reports to judge cost on the current slate. Fixture counts, output lengths, retries and enabled research steps all affect spending.

## Maintain broadcast mappings

[`rights.toml`](../rights.toml) is the source for channel aliases, service carriage, simulcasts and usual coverage. A usual home is a fallback when ESPN has not listed channels; it is not a confirmed match listing. Club-specific rights belong in `by_home_team`, and uncertain coverage belongs in a hint rather than a service claim.

To update a mapping:

1. Check the current rights or carriage agreement against a reliable source.
2. Edit the relevant entry, including its source, `checked` date and any season end date.
3. Run `python3 -m unittest -v`.
4. Build with `--warnings work/mapping-report.txt` and review the report.

Validation catches unknown keys, invalid service/channel references, ambiguous aliases and missing required metadata. Each build also reports unmapped ESPN names, missing club mappings, suspicious disagreement with listed coverage, usual homes nearing or past expiry, and facts unchecked for more than 180 days. A usual home lapses after its `until` date; the page then stops relying on it.

After publication, [the mapping script](../.github/scripts/mapping-issue.sh) maintains an issue labelled `mapping`: it mentions the repository owner on creation, updates the report and comments when it changes, and closes the issue when a successful build produces a clean report. A warning is a request to investigate, not proof that the underlying rights have changed.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Local build cannot fetch fixtures | Confirm that `curl` can reach ESPN from this machine. Unit and browser tests can still run without ESPN access, but they cannot produce a current real schedule. |
| No AI prose | Expected with the current ratings-only settings. If overview or blurbs are enabled, check their provider key and generation log for missing or rejected sources. |
| No AI ratings | Check `[ai] enabled`, the correct provider secret, whether generation returned ratings, and the result's freshness. Picks still use the Outlook score. |
| A local `story.py` run does nothing | Its default mode is `keep`. Use `daily` or another generation mode, and supply `--previous` if reuse is wanted. |
| A card has no blurb | Optional text may be disabled, the match may not be one of the six researched candidates, or its text may have failed validation. Cards show match facts in its place. |
| Published page has not changed | Check **Refresh outlook** and then the Pages deployment. A failed build preserves the prior site; documentation-only commits do not rebuild it. |
| Browser checks cannot start | Install Playwright's Chromium and its system dependencies, or deliberately set `BROWSER_EXECUTABLE`. Exit code 77 means Chromium could not start, not that the assertions passed. |
| A fixture is missing | Check enabled services and leagues, the displayed date window, known coverage and the page's data details for incomplete ESPN requests. |

Return to the [project overview](../README.md).
