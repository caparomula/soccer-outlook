# Development and maintenance

Soccer Outlook is a static site. Python fetches fixtures and builds the page; JavaScript applies the visitor's preferences and updates live scores. Automatic AI ratings are optional and stored separately as `story.json`. Visitors can import personal AI recommendations through a browser-only copy-and-paste workflow. Authorized maintainers can publish those recommendations as shared defaults through GitHub's API, without an application server.

For how the page works, see the [user guide](user-guide.md). For the ranking formula and default league order, see [scoring](scoring.md).

## Run it locally

The schedule builder needs **Python 3.11 or newer**, `curl`, and network access to ESPN. It also requests public broadcaster schedules to verify usual coverage; an unavailable broadcaster source leaves that coverage unconfirmed. It uses Python's standard library; no API key or Python package installation is needed to build the schedule or run unit tests.

From the repository root:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --facts work/facts.json --warnings work/mapping-report.txt
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open <http://127.0.0.1:8000/>. Stop the server with Ctrl+C. Serving the directory over HTTP lets the page load `story.json` if one exists beside `index.html`.

By default, the builder fetches six Eastern calendar dates: yesterday, today and the following four days. Before 4 a.m. Eastern, it also fetches the preceding date, because the display still treats the previous evening as today and needs another day's results for Yesterday. The forward buffer supports a page left open across a day change. The visible schedule covers today and the following three days; see the user guide for the 4 a.m. day boundary. The AI's corresponding window uses Eastern time, while the browser groups matches using the visitor's local time.

Useful build options:

| Option | Purpose |
| --- | --- |
| `--facts work/facts.json` | Save the fixture and team data used by `story.py`. This does not call an AI model. |
| `--warnings work/mapping-report.txt` | Save broadcast-mapping warnings; the file is empty when the report is clean. |
| `--report work/build-report.json` | Save source-health and fixture counts used to decide whether the build can be published. |
| `--site-url https://example.com/soccer/` | Set the public directory URL used by the canonical link, sharing metadata and sitemap. Defaults to the published Soccer Outlook URL. |
| `--date YYYY-MM-DD` | Choose the Eastern date used to fetch fixtures, evaluate rights and date the public static schedules. The configured calendar range is exact, without the extra date before 4 a.m. It does not change the build timestamp or the browser's clock. |
| `--days-back N --days-ahead N` | Change the dates fetched. Builds before 4 a.m. Eastern fetch one additional prior date unless `--date` is set. This does not change the browser's display window. |
| `--no-logos` | Build without team or league images. |
| `--embed-images --logos logos.json` | Embed images instead of linking to ESPN; cache downloaded images in the named file. |
| `--fragment` | Write the page body for a host that supplies its own document wrapper. |

For a browser preview at a chosen local time, append a fragment such as `#at-20261009-1300` to the page URL. The fixtures still come from the build; the fragment does not fetch a historical schedule. Use `python3 build.py --help` for all options.

## Where to make changes

| File or directory | Responsibility |
| --- | --- |
| [`build.py`](../build.py) | Fetch and interpret ESPN data, validate configuration, calculate the Outlook score and generate HTML. Also defines tracked leagues, their default selection and order, default services, and teams receiving an occasion-score bonus. |
| [`discovery.py`](../discovery.py) | Generate public league and service schedule pages, their links, and search-discovery metadata. |
| [`broadcast_listings.py`](../broadcast_listings.py) and the provider `*_listings.py` modules | Check public broadcaster schedules for usual coverage and Liga MX simulcasts, apply confirmed listings, and retain source/check results. |
| [`watch_links.py`](../watch_links.py) | Validate broadcaster and manually supplied viewing URLs, then map them to eligible providers. |
| [`watch-links.toml`](../watch-links.toml) | Manually supplied YouTube TV watch URLs, keyed by ESPN fixture ID and matched to a confirmed channel. |
| [`rights.toml`](../rights.toml) | Broadcaster aliases, services, simulcasts and each competition's usual coverage, with sources and check dates. |
| [`settings.toml`](../settings.toml) | AI configuration, score weights and golden-dot thresholds. |
| [`web/page.html`](../web/page.html) | Page structure. |
| [`web/styles.css`](../web/styles.css) | Layout, themes and responsive styles. |
| [`web/app.js`](../web/app.js) | Filters, saved preferences, match cards, scoring in the browser, details overlays and applying live updates. |
| [`web/live.js`](../web/live.js) | Live-score requests, catch-up checks, timeouts and retry scheduling. |
| [`web/personal-ai.js`](../web/personal-ai.js) | Personal AI prompt and response format, validation and import freshness. |
| [`web/github-publish.js`](../web/github-publish.js) | Optional browser connection to GitHub and publication of accepted recommendations. |
| `content/shared-ai.json` | Optional public recommendation defaults, created by an authorized maintainer's first publication and copied into the generated site. |
| [`web/favicon.svg`](../web/favicon.svg), [`web/favicon.png`](../web/favicon.png) | Editable soccer-ball artwork and its 64 × 64 PNG browser icon. |
| [`web/logos/`](../web/logos/README.md) | Compact official league artwork used instead of unsuitable ESPN wordmarks, with source attribution. |
| [`story.py`](../story.py) | Generate, validate and reuse optional AI ratings and text. |
| [`story_state.py`](../story_state.py) | Pure rules for the ratings window, missing ratings and reusable results. |
| [`providers.py`](../providers.py) | Supported models, provider requests, configuration validation and price estimates. |
| [`tests/`](../tests/) | Unit tests and the browser test harness. |
| [`.github/workflows/`](../.github/workflows/) | Automated builds, browser checks and optional AI comparisons. |
| [`.github/scripts/`](../.github/scripts/) | Mapping-issue maintenance and AI comparison reports. |

The generator inlines the HTML, CSS and JavaScript assets into the page. Rebuild after editing them. Full builds copy `web/favicon.png` beside the HTML; the PNG works in browsers that do not support SVG favicons. If the SVG artwork changes, re-export the PNG at 64 × 64 with a transparent background. Fragment builds leave the icon to the host page.

Deployment consists of `index.html`, `favicon.png`, `sitemap.xml`, the `premier-league/`, `mls/` and `paramount-plus/` schedule pages, optional `shared-ai.json` and `story.json`, an optional IndexNow key file and a `.nojekyll` file; there is no application server or JavaScript bundler. Generated files in `site/` and test artifacts in `work/` are ignored by Git.

Full pages allow search indexing and include a descriptive title, summary, canonical URL and sharing metadata in the document head. Each build writes three focused league/service schedules and a sitemap listing the homepage and those pages, dated with the schedule rebuild time. These public schedules use Eastern time and do not apply personal browser filters. Fragment builds leave search metadata and discovery files to the host. The workflows use the repository's GitHub Pages URL by default; for a custom domain, set the repository Actions variable `SITE_URL` to its public directory URL. This keeps generation and IndexNow submission aligned. Use `--site-url` for other public deployments so their canonical links point to the correct site.

Optional repository Actions variables `GOOGLE_SITE_VERIFICATION` and `BING_SITE_VERIFICATION` supply the public content tokens for Google and Bing's verification meta tags. The builder also accepts these names as environment variables for local builds. Their values are intentionally published; use repository **Variables**, not provider-key secrets. Unset variables produce no verification tags. The stable public IndexNow ownership token comes from [`web/indexnow-key.txt`](../web/indexnow-key.txt); a full build writes the corresponding key file into `site/`. See [Search and discovery](discovery.md) for account setup and submission steps.

An optional `INDEXNOW_KEY` repository variable overrides that token in both workflows; the builder and notification script also accept it as an environment variable. Use the same value for generation and notification. Normal use requires no override.

Indexing and search placement are decided by search engines. A `robots.txt` file only controls crawling when served at the domain root; a file under `/soccer-outlook/` would not do so. The current domain has no blocking robots rules.

## Test changes

Run the unit tests from the repository root:

```sh
python3 -m unittest -v
```

They cover rights and settings validation, ESPN parsing, source-health publication checks, scoring, rendering, provider responses and AI reuse/failure behavior. Tests use fixtures and mocked requests; they do not spend API credits. Small sanitized ESPN examples in [`tests/fixtures/`](../tests/fixtures/) preserve real IDs and feed shapes alongside the synthetic cases. Most rendering and scoring tests use fixed settings so changing a valid production weight does not change their expected results. Separate tests validate the real configuration files.

For browser checks, create a virtual environment and install the test dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements-browser.txt
.venv/bin/python -m playwright install chromium webkit
.venv/bin/python -m tests.browser
.venv/bin/python -m tests.browser --browser webkit --smoke
```

On Linux, missing system libraries can be installed with `.venv/bin/python -m playwright install --with-deps chromium webkit`. This may require administrator access. The harness uses Playwright's Chromium by default; set `BROWSER_EXECUTABLE` to an existing compatible browser executable only when you intend to override it.

Browser tests use a synthetic schedule, a fixed clock and local responses for ESPN and AI data. They check filters, persistence, rankings, live transitions, returning from background tabs, details dialogs, keyboard navigation and responsive layouts. Screenshots go to `work/browser/`; pass `--artifacts PATH` to choose another directory. External fonts are replaced with consistent fallback fonts. CI runs the full suite in Chromium and a focused smoke suite in WebKit, Safari's browser engine. WebKit coverage does not replace testing Safari itself on Apple devices. These tests do not verify ESPN's current network availability or the accuracy of real AI output.

For a refactor that should preserve output exactly:

```sh
.venv/bin/python -m tests.browser --baseline-build /path/to/baseline/build.py
```

Use a complete checkout of the baseline revision so its supporting files and assets are available. Both generators receive the same fixture data and build time. The harness first requires byte-identical document and fragment HTML, then compares screenshots across 12 viewport/theme/UI combinations. Pixel comparisons allow antialiasing differences using pixelmatch's threshold of 0.1; any remaining differing pixel fails and produces a diff image. The ordinary behavior suite primarily exercises the current version. This comparison is deliberately unsuitable for an intentional visual change.

## Personal AI imports

**AI settings** lets a visitor generate a prompt, run it in a chat of their choice, and paste the response back. This path makes no AI API requests, needs no AI provider credentials and does not modify `story.json`. Applying stays local unless the visitor connects the optional GitHub publishing control. The [user guide](user-guide.md#use-your-own-ai-for-blurbs) explains the controls.

The prompt includes every enabled and unpaused league, league priority and user preferences, plus current filter-eligible live and upcoming fixtures with team facts and known coverage. It can be generated with no fixtures when services and leagues are selected, allowing the chat to research calendar developments. An empty schedule must not be interpreted as a break or the start of a season.

New prompts request one JSON code block, formatted with two-space indentation, using response version 2. This replaces the mandatory overview paragraph with optional headline candidates:

| Field | Contract |
| --- | --- |
| `version`, `request_id` | `2` and the unchanged ID from the generated prompt. |
| `headlines` | An array, possibly empty, with at most one candidate per exported league. Each candidate has `league_id`, `headline` (at most 80 characters, aiming for 3–8 words), an optional `lede` (at most 240 characters, possibly empty), `interest` (0–100), `match_ids`, `sources` and `expires_at`. Referenced matches must be supplied fixtures from that league. |
| `matches` | Every exported fixture exactly once, with its unchanged `id`, a `score` from 0 to 100, a `blurb` of at most 400 characters and `sources`. The blurb may be empty when no useful supported angle exists. |

Headline interest is an absolute editorial assessment, not a rank within the response or the match's pick score. Candidates below 60 are not displayed. Starting from the current time, the browser groups stories into successive 24-hour intervals by their earliest referenced kickoff. It prefers the earliest interval, then higher interest, with league priority breaking ties. A calendar notice occupies the current interval. The leading headline is visible; others sit behind **More headlines**, and **Read more** reveals any lede. Match-linked headlines require all referenced fixtures to remain eligible, unfinished and unchanged in kickoff. A notice without match references requires a research source, an enabled and unpaused league, and at least one selected service.

Each headline's `expires_at` must be an ISO timestamp after prompt creation and no later than the exported `response_expires_at` deadline, 24 hours later. Each source list allows up to five objects with `title` and HTTPS `url`; sources can be empty for text based only on supplied facts, but not for a calendar notice without fixtures. URL validation does not verify the cited page or the AI's interpretation of it. Plain JSON or one JSON code block is accepted; responses over 256 KB are rejected. An invalid response leaves the previous import intact. Imported data cannot alter fixtures, coverage, kickoff times, scores or live states.

The manual prompt asks for researched player, coach, tactical, rivalry or competitive-stakes angles when the chosen chat has web search. Headlines may connect a league's relevant stories, including a verified break or resumption; routine news should be omitted. It discourages unrelated club news, schedule repetition and generic filler. Users must enable search in their chat for current reporting. Manual imports do not run the same prompt, research tools or source-provenance checks as `story.py`; the published generation path is unchanged and its prose remains off by default.

Imports and personal preferences are stored locally. The whole import expires 24 hours after prompt creation, and headlines may expire sooner. Saved version 1 prompts and responses remain supported. Their overview is displayed under **Match outlook**, collapsed behind **Read more**; ratings, match blurbs and expiry are preserved. Automatically published legacy overviews use the same compact display. **Match outlook** is a section label, not a fabricated headline. Version 2's authored headlines are unchanged.

**AI settings** identifies saved older-format prompts or responses and directs the reader to **Create prompt**. A fresh chat response supplies the version 2 headline fields. The app does not migrate paragraphs into headlines or make new AI calls on its own.

Matching personal ratings take precedence over shared defaults, which take precedence over automatic `story.json` ratings while valid. The visitor's **Imported rating influence** controls personal ratings' blend with the Outlook score; league-priority weighting stays unchanged. Current filters apply to both personal and shared text. A personal response's empty headline list is respected rather than filled from shared content. Restoring site recommendations clears the personal result without resetting the lineup, preference text or shared defaults.

### Publishing shared defaults through GitHub

The collapsible **Publish site defaults** control uses a fine-grained personal access token restricted to this repository with **Contents: read/write**. See the [token setup instructions](user-guide.md#publish-recommendations-for-everyone). The browser checks the repository's `permissions.push` before enabling publishing. The token stays in memory and is sent only to GitHub's API; it is not saved in local storage or included in the prompt or published content. Reloading requires reconnection. This is token authentication, not an OAuth login or a connection to GitHub Copilot.

When connected, **Apply & publish** first accepts the response locally and then publishes it; **Publish saved response** publishes the accepted result without requiring another import. Publication reads the current `content/shared-ai.json` SHA, then updates that file on `main` through the GitHub Contents API. The SHA guards against concurrent edits. A rejected or failed publication must not discard the local import or be reported as a completed deployment.

The shared file contains a version-1 envelope with `created_at` (the original timestamp in milliseconds), `weight` (the publisher's current blend), `leagues` (IDs), `fixtures` (`id`, `kickoff`, `league_id`) and the validated version-1 or version-2 `response`. The export omits the full prompt, preference text, broadcaster selections and credentials. Text and source links in the accepted response are public. Publishing or rebuilding must not extend the original 24-hour lifetime, and individual headlines can expire earlier. The browser validates the envelope with limits of 512 KiB overall and 256 KiB for its response, then uses the saved blend for shared ratings.

The refresh workflow watches `content/shared-ai.json`; a commit triggers the normal checks, build and Pages deployment. The source file on `main` is authoritative. Full builds copy fresh shared data to `site/shared-ai.json`; missing, invalid or expired input is omitted without blocking the schedule. Do not publish directly to `gh-pages`, because the next refresh replaces that branch. An accepted API update means the commit exists, not that Pages has finished deploying it. The browser checks for shared updates every ten minutes while visible, applies each reader's filters and falls back to automatic ratings as shared recommendations expire or no longer match fixtures.

For a fork, update the fixed `REPOSITORY`, `BRANCH` and `PATH` in `web/github-publish.js`, the matching `data-repository`, `data-branch` and `data-path` on the publishing section in `web/page.html`, and the section's repository and token-creation links. A mismatch disables publishing. If you change the content path, update `SHARED_AI_SOURCE` in `build.py` and the refresh workflow's path filter too. The credentials never determine which repository or file the UI can write.

GitHub's [REST API supports browser CORS](https://docs.github.com/en/rest/using-the-rest-api/using-cors-and-jsonp-to-make-cross-origin-requests), so this route needs no backend. The [Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents) accepts a repository-scoped token with Contents write permission; editing the shared JSON does not require Workflows or Actions write access. Do not put a shared owner token or OAuth client secret into the generated page.

### A future GitHub Copilot connection

The same prompt and response contract could support a direct connection through the [official GitHub Copilot SDK](https://github.com/github/copilot-sdk). Its [OAuth authentication](https://github.com/github/copilot-sdk/blob/main/docs/auth/authenticate.md#github-oauth-app) supports acting for a signed-in user with a Copilot subscription. A multi-user implementation must associate each request with that user's authorization rather than a shared owner account.

That connection is **not implemented**. The current GitHub Pages deployment cannot run the SDK: it would need separately hosted backend code, a configured GitHub OAuth application and session handling. A backend should return the same validated recommendation format; manual imports can continue to work independently.

## Configure published AI ratings and text

The current [`settings.toml`](../settings.toml) configuration enables the **ratings** design with `gpt-6.1-sol` at `low` effort. It requests scores without web research. The optional automatically generated overview and card blurbs are disabled; manual personal or shared responses can still supply prose.

The `[ai]` table controls this behavior:

| Setting | Effect |
| --- | --- |
| `enabled = false` | Disable site-generated AI calls and prevent the browser from requesting `story.json`. Personal imports and shared defaults remain available; otherwise picks use the Outlook score and league priority. |
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
| `daily` | Generate for the facts' date and current AI configuration. In the ratings design, retain accepted same-day ratings, add missing or newly in-window fixtures, and retry missing optional text. In the full design, reuse a matching result from that day. Used by scheduled builds. |
| `keep` | Copy the previous result without an API request, even if it is from an earlier date. If the previous result uses a different AI design, model or effort, generate with the new configuration instead. Used for ordinary code pushes. This is also the script's default mode. |
| `refresh` | In the full design, update today's research and re-rate fixtures within 24 hours or missing a rating/blurb. Without today's result, start a full run. In the ratings design, generate fresh ratings. |
| `full` | In the full design, research and rate from scratch. In the ratings design, generate fresh ratings. |
| `auto` | In the full design, reuse a result less than three hours old, otherwise refresh; generate from scratch if none exists for today. In the ratings design, behave like `daily`. |
| `ratings` | Request scores only. Optional overview/blurbs configured for the ratings design run afterwards. |

The settings take precedence over the mode's research design. `--ignore-switch` bypasses the `[ai]` table for comparison runs; it can therefore call an API even when production AI is disabled. Normal builds should not use it.

The workflow also uses two preparation options that make no API calls: `--fallback-only` saves a compatible, fresh same-day result before setup, and `--needs-sdk` prints `anthropic` only when the planned work may need that SDK. Both take the same facts, previous result and mode arguments as generation.

Changing the configured model, effort, design or optional text models triggers new generation on the next build when the previous story records a different configuration. Changing scoring weights alone does not require a new AI call. The workflow also detects a published page with AI disabled, so turning AI back on requests the day's result.

If a provider key is missing or generation fails, `story.py` reuses a matching result from the same day when available. Otherwise it writes no new result, and the schedule remains usable with the Outlook score. Deleting a key is therefore not a reliable way to turn existing AI content off; use `enabled = false`.

AI-off runs write nothing, rather than deleting an existing output file. The deployment workflow builds in a fresh directory. For a clean local preview, remove an old `site/story.json` when you want to test its absence; an AI-off page ignores that file regardless.

The browser fetches AI data every ten minutes while visible. It rejects results older than 30 hours or past their explicit `focus_until` horizon, currently 24 hours after the facts were built. Copying an old result with `keep` does not renew that horizon.

Validated ratings are saved atomically before optional text generation. Each text task then enriches a copy, so its failure cannot discard the ratings. A later `daily` run can retry missing text without requesting the accepted ratings again. Explicit `refresh`, `full` and `ratings` runs still request fresh ratings in the ratings design.

### What validation guarantees

Ratings must contain valid scores for known fixture IDs. An incomplete response gets one further request for missing fixtures; the full design also retries missing card blurbs. An ambiguous timeout or connection failure is not automatically resubmitted in that run, because the provider may already have completed and billed the work. Full-design research can request a repair for missing overview or league text. These retries improve coverage but do not guarantee that every match or league receives text. Valid partial results can still be published.

Researched text must cite sources returned by the model's tools. Full-design league and match context may instead use supplied ESPN facts, explicitly labelled as such. Ratings-design optional overview and blurbs require searched sources; known dead links are removed. A site's refusal to answer automated requests is treated differently from a confirmed dead link. Source checks establish provenance, not that every sentence correctly summarizes its source.

## Automated builds and publishing

Before rendering, the builder checks the public schedules behind every configured usual-coverage route: FOX Sports, ESPN+, Paramount+, ViX, Peacock, Apple TV, Fandango and beIN Sports Connect. It fetches only providers needed by the current fixtures, reuses each provider's page where possible, and checks independent providers in parallel. Liga MX also gets a TUDN check before those fallbacks, including fixtures that ESPN lists only on ViX. TUDN's current-round results page supplies match URLs; matching US event metadata supplies the actual television channels. This can make a match available through YouTube TV without assuming every ViX match airs on TUDN. The parser currently verifies Apertura catalogue ID `199`; review that ID when the season changes. Fixtures outside the supported catalogue or current round retain their other listings or usual coverage. These are public schedule requests, with no AI calls or broadcaster account credentials.

Adapters require match-specific evidence: competition, both teams, a dated kickoff or narrowly matched live-broadcast start, and an explicit channel or service. Replays and general league rights do not confirm a live listing. Some providers start their broadcast a few minutes before the match; this never changes ESPN's kickoff time. Existing ESPN listings are preserved; a verified TUDN simulcast can add viewing options. The TUDN check also covers recent results, and never changes ESPN's score, kickoff or match status. Missing, conflicting, changed-format or unreachable supplemental data leaves the usual coverage intact, with a source link and explanation in Details. A provider failure does not make the ESPN fixture feed unusable.

`build-report.json` records `broadcast_checks` by provider or usual channel, including request, confirmation and failure counts and per-match source URLs/status. Confirmed source URLs are also included in the match facts supplied to optional AI generation. Check those records before changing an adapter: an empty or unavailable provider page must not be treated as a confirmed listing. Parser tests use small recorded or synthetic responses and make no live requests.

Viewing links use these existing verified URLs without additional network requests. `watch_links.py` requires an appropriate confirmed outlet, source name and allowlisted HTTPS match-page path. It excludes generic sports hubs and boxscores from exact-match links. `service_links()` maps each eligible provider to its destination; the builder embeds that map in the row's `data-watch` attribute. The provider chip becomes a native link only when its selected service has an eligible destination and coverage is listed. Static markup and the browser use the same map, and featured cards clone the chip. A supported ESPN player can serve a compatible YouTube TV plan; its link title names ESPN, and the viewer may need to link their accounts. Providers without a supported destination remain plain labels.

[`watch-links.toml`](../watch-links.toml) holds manually supplied YouTube TV links. Match each entry to the ESPN fixture ID and its confirmed broadcast channel, and preserve the full `https://tv.youtube.com/watch/…` URL, including any `vp` query parameter. A watch ID can represent a linear channel rather than a single event, so these links are marked `live_only` and enabled only when ESPN's match state is `in`. They are disabled before kickoff and after the final whistle, including when the browser receives a live-score update.

Never reuse an ID for another fixture merely because both air on the same channel. The initial entry is the viewer-supplied Coventry City–Newcastle United link for fixture `401879267` on USA Network. There is no automatic public source of YouTube TV program IDs in this project and no homepage fallback.

Viewing links do not change match availability, scores or live status. They do not guarantee playback, and a finished match does not imply an available replay.

[`Refresh outlook`](../.github/workflows/refresh.yml) runs at **09:50, 16:50 and 22:50 UTC** each day. That is 5:50 a.m., 12:50 p.m. and 6:50 p.m. during Eastern daylight time, or 4:50 a.m., 11:50 a.m. and 5:50 p.m. during standard time. The morning run stays after the 4 a.m. day boundary in both seasons. GitHub can delay scheduled runs.

The workflow also runs on relevant changes to `main`, and can be started from **Actions → Refresh outlook → Run workflow**. Its manual `storylines` choice selects `refresh`, `full` or `keep`; the configured AI design still applies. Documentation-only changes do not trigger this workflow.

Each run:

1. Runs unit and browser checks.
2. Builds the HTML, AI facts, mapping report and source-health report, and copies any fresh shared recommendations.
3. Checks source health and verifies that the page's match count agrees with the report and that the document is complete. A genuinely small or empty schedule can pass.
4. Generates or reuses optional AI data. Scheduled runs use `daily`; ordinary pushes use `keep`, with the configuration-change exceptions above.
5. Replaces the `gh-pages` branch with a single commit containing the generated site.
6. Opens, updates or closes the broadcast-mapping issue as needed.
7. Retains generated files and reports in a `generated-site` Actions artifact for 14 days, including any files available after a failed run.

GitHub Pages serves the `gh-pages` branch. When configuring a fork, set Pages to **Deploy from a branch**, choose **gh-pages**, and use the root directory after the first successful publish. The workflow requests `contents: write` and `issues: write`; no separate deployment token is configured beyond GitHub's supplied token.

A failing unit test, browser assertion, invalid configuration or unusable source data stops publication and leaves the existing site in place. The builder rejects runs with no successful scoreboard responses or failures from more than half the requested scoreboards. It also rejects an empty schedule when failed sources or unreadable events could explain the emptiness. Successful responses containing no events are valid. Smaller gaps can publish and are disclosed on the page and in the source-health report.

There are two intentional exceptions: if Chromium cannot be installed or started, the refresh workflow publishes with a warning; and AI generation or mapping-issue failures do not block the schedule. Before AI setup, it saves a compatible, fresh result from the same day as a fallback. The Anthropic SDK is installed only if the planned work needs it. The separate [`Browser checks`](../.github/workflows/browser.yml) workflow runs unit tests, Chromium checks and WebKit smoke checks on relevant pushes and pull requests, including changes to AI and maintenance scripts. It saves screenshots even when checks fail and does not have the refresh workflow's browser-setup bypass.

The `generated-site` artifact includes the homepage and focused schedules, favicon, sitemap, any IndexNow key file, available AI output, source facts, warnings, health report and usage report. Download it from a workflow run to inspect the exact inputs and outputs behind a page. To restore an earlier page, use that artifact's `site/` contents in a new `gh-pages` commit, add `.nojekyll`, and push the branch; the next scheduled build will replace it. Check the source commit and build time before restoring, since old fixtures and ratings will still expire normally.

The [Notify search engines](../.github/workflows/discovery.yml) workflow notifies IndexNow only after a successful production Pages deployment, using [`.github/scripts/indexnow.py`](../.github/scripts/indexnow.py). It reads the published sitemap and verifies the public key file before submitting URLs. It makes no AI calls; failures are visible in its Actions log and do not block or undo the site deployment. See the [discovery guide](discovery.md) for the public token file and separate search-engine verification steps.

### Costs and comparison workflows

AI generation reports token use, elapsed time and estimated cost in the Actions summary. `--usage-out PATH` saves a machine-readable report. Prices come from `providers.py` and are estimates, not invoices; provider search charges may be incomplete when an API does not report them. Interrupted requests can have unknown usage: `unknown_requests` records those requests and `cost_complete: false` marks an incomplete cost estimate.

Two manually invoked workflows compare models without publishing a page:

- [`Compare storyline models`](../.github/workflows/compare-storylines.yml) compares Claude configurations using either the full design or scores only. Each listed configuration makes paid requests; there is no dollar-budget input.
- [`Compare AI providers`](../.github/workflows/compare-providers.yml) compares ratings, researched blurbs and overviews across providers. Enter `none` for a task to skip it. Its budget stops a task from starting when estimated total spending would exceed the limit. An ambiguous failure marks the cost incomplete and stops remaining paid tasks, since the provider may already have charged for the interrupted request. This is not a provider-enforced spending cap. Results appear in the summary and a JSON artifact.

Use the run reports to judge cost on the current slate. Fixture counts, output lengths, retries and enabled research steps all affect spending.

## Maintain broadcast mappings

[`rights.toml`](../rights.toml) is the source for channel aliases, service carriage, simulcasts and usual coverage. A usual home is a fallback when ESPN has not listed channels; it is not a confirmed match listing. Club-specific rights belong in `by_home_team`, keyed by quoted ESPN team IDs; comments name the clubs for maintainers. Use the home competitor's `team.id` from ESPN rather than its display name, which can change. Uncertain coverage belongs in a hint rather than a service claim.

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
| No AI prose | Automatic prose is off by default. For personal or shared responses, check expiry, filters and headline interest; a response can legitimately have no headlines or blurbs. For enabled automatic prose, check the provider key and generation log. |
| No AI ratings | Check `[ai] enabled`, the correct provider secret, whether generation returned ratings, and the result's freshness. Picks still use the Outlook score. |
| A local `story.py` run does nothing | Its default mode is `keep`. Use `daily` or another generation mode, and supply `--previous` if reuse is wanted. |
| A card has no blurb | A personal or shared response may not cover this fixture, may have expired, or may leave its blurb empty. Automatic text may be disabled, the match may not be one of the six researched candidates, or its text may have failed validation. Cards show match facts in its place. |
| A pasted AI response is rejected | Use the complete JSON response for the current prompt, including every requested match ID and its unchanged request ID. Follow the reported error; create a new prompt if the old one has expired. |
| I still see an overview instead of a headline | An older personal, shared or automatic response can contain a paragraph, now collapsed under **Match outlook → Read more**. Use **Create prompt** and obtain a fresh response for authored headline fields; copying an old prompt or reapplying its response retains the old format. |
| GitHub publishing fails | Check token expiry, the selected repository and Contents write permission. A concurrent edit requires reviewing the latest file before retrying. Your local response is retained; use **Publish saved response** once the issue is resolved. |
| Published page has not changed | Check **Refresh outlook** and then the Pages deployment. A failed build preserves the prior site; documentation-only commits do not rebuild it. |
| Browser checks cannot start | Install the selected Playwright browser and its system dependencies, or deliberately set `BROWSER_EXECUTABLE`. Exit code 77 means the browser could not start, not that the assertions passed. |
| A fixture is missing | Check enabled services and leagues, the displayed date window, known coverage and the page's data details for incomplete ESPN requests. |

Return to the [project overview](../README.md).
