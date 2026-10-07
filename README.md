# Soccer Outlook

A week of soccer at a glance, judged against the streaming services you have.

**Live page:** https://caparomula.github.io/soccer-outlook/

The page focuses on the rolling next 24 hours, including live matches. Claude researches storylines and short-range context; later fixtures stay in a collapsed section. Match counts, cards and service totals use the same 24-hour window. Each match shows every US broadcaster ESPN lists, team form and table position, and a details panel with scorers, venue, a calendar link and the league table.

## Your lineup

Tap **Lineup** at the top right of the page (it stays there as you scroll) and tap the services you have and the competitions you want. Every match is then judged against your lineup; the choice stays in your browser. **Clear all** deselects every service so you can build a lineup from nothing, and **Reset to the default** brings back the owner's lineup, `OWNER` in `build.py`.

## How it runs

`build.py` uses Python 3.11 or newer and `curl`; `story.py` adds the Anthropic Python SDK. It fetches eleven days of fixtures and the current standings from ESPN's public scoreboard API, maps each listed broadcaster to the streaming services that carry it, applies each competition's usual home when channels are not posted yet (both from `rights.toml`, below), and writes one HTML file. The page's own script does the rest in the browser: bucketing by the viewer's clock, the lineup, filters, the countdown and live scores.

Edit the page structure in `web/page.html`, the styles in `web/styles.css`, and browser behavior in `web/app.js`. The generator reads these files relative to `build.py` and inlines them into the generated HTML, including with `--fragment`. The small document wrapper and reset styles remain in `build.py`. Rebuild after editing an asset; deployment still consists of the generated HTML and optional `story.json`.

The workflow in `.github/workflows/refresh.yml` runs the script three times a day and publishes the page to the `gh-pages` branch, which GitHub Pages serves. It can also be run by hand from the Actions tab (**Refresh outlook**, then **Run workflow**), and it runs on changes to the generator, story script, rights data, `web/`, tests or workflow scripts.

To test and build locally:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --warnings work/mapping-report.txt
```

Add `--date YYYY-MM-DD` to build as of another day. Open the page with `#at-YYYYMMDD-HHMM` on the URL to preview it as of another local time.

### Browser checks

The optional browser tests use Playwright and Chromium. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m tests.browser
```

On Linux, Playwright may also need system libraries (`python -m playwright install --with-deps chromium`). Set `BROWSER_EXECUTABLE` to use an existing Chromium binary; a `chromium` on PATH is used automatically. These dependencies are only for testing.

The checks use a synthetic schedule, a fixed clock and local scoreboard responses; they need no ESPN access or API key. They cover lineup persistence/reset, the midnight and 4 a.m. boundaries, live-to-final score updates, failed requests, factual summaries, simultaneous kickoffs, uncertain coverage, and the rolling 24-hour cutoff, later-story fallback, and phrase-level filtering of Claude's text in light and dark themes. Match-column alignment is checked at five widths using long team names and different broadcasters. Desktop/mobile screenshots in light/dark themes, with the lineup and match details open, are saved under `work/browser/`. External fonts are replaced with the same fallback fonts for reproducibility. `.github/workflows/browser.yml` runs these checks on relevant pushes and pull requests and saves the screenshots.

For a before/after comparison, pass `--baseline-build /path/to/original/build.py` to the browser command. Keep its `rights.toml` beside it, along with `web/` for revisions that use extracted assets. Both generators receive identical fixture data and build time. The comparison requires byte-identical document and fragment HTML and compares screenshot pixels in all 12 viewport/theme/UI combinations, then runs the behavior checks against both versions. Pixel comparisons use pixelmatch's standard antialiasing detection and perceptual threshold of 0.1, since Chromium can paint a few edge pixels differently even for identical pages; any remaining differing pixel fails the check and produces a diff image. Use this when restructuring code without intentional output changes; ordinary browser runs exercise the current version only.

## Live scores

The page is rebuilt three times a day, but scores don't wait for a rebuild. From 15 minutes before a kickoff until ESPN calls the match over, the viewer's browser asks ESPN's public scoreboard for that competition's day once a minute and updates the score, the clock, half time and full time, the scorers, the "Live now" group, the countdown band, the picks and the schedule summary. A match that finished since the last rebuild is asked about once when the page opens, so "Earlier today" carries its result.

- **Loading stays fast.** The requests start after the page has drawn, run in parallel (one per competition and day, not one per match), and each is abandoned after 8 seconds. Nothing waits on them.
- **Failing quietly.** A request that fails or times out leaves the page as it was built. After a failure the page waits longer before trying again, doubling up to ten minutes, and the line under the forecast says when the scores were last checked.
- **Not wasteful.** Nothing is asked while the tab is hidden, while no match is near its kickoff, or for competitions switched off in the filters.
- **Testing.** Served from `localhost`, the page accepts `?scoresbase=http://localhost:PORT/some/path/` to read scoreboards from a local stand-in for ESPN instead.

## Keeping the broadcast facts right

Which services carry which channels, the names ESPN uses for each channel, and where each competition usually lives are all in `rights.toml`, each with its source and the date it was last checked. These facts change with every season's rights deals, and sometimes mid-season with a carriage dispute, and ESPN's data shows none of that. Several guards keep a stale or mistaken fact from quietly misleading the page:

- **Tests before every publish.** `tests/test_rights.py` checks that the file agrees with itself (every service names real channels, no two channels claim one ESPN name, every usual home is carried by some service and has a season end, every fact has a source and date, no misspelt keys) and repeats each mistake the page has actually made, from USA Network's "USA Net" to the Bundesliga's move off ESPN. The workflow runs them before building; if they fail, the last good page stays up and GitHub emails about the failed run.
- **Competitions ESPN doesn't list.** ESPN posts no broadcasters at all for some competitions (Liga MX, the Primeira Liga, the Brasileirão, Argentina, the Saudi Pro League) and few for others, so for them `rights.toml` is the only basis. Where one service carries every match, it is the usual home (the Primeira Liga on beIN Sports Connect). Where rights go club by club, `by_home_team` names the usual home for each home club (Liga MX: ViX for TelevisaUnivision's 14 clubs, Peacock for Chivas, FOX for Juárez, Santos and Tijuana); a club it doesn't name claims nothing and is reported. Where no service has every match (Ligue 1, the Saudi Pro League), the page shows a hint instead of a claim.
- **Usual homes lapse.** Each competition's usual home is for one season and lapses after its `until` date. After that the page claims no usual home for it until someone confirms the new season's, because rights change at the turn of a season and a stale claim is worse than none.
- **On my services means available.** Only matches with listed or established usual coverage on a selected service appear in this view. Wholly unconfirmed coverage, unrecognized channels and usual coverage on an unselected service stay under **Everything**. When the next 24 hours has no viewable matches, an explanation appears above the collapsed later schedule.
- **A report, as a GitHub issue.** Each build lists what it couldn't map or vouch for: unknown broadcaster names, a usual home that disagrees with ESPN's listings or lapses within 30 days, and facts unchecked for 180 days. While that list has anything in it, the workflow keeps one issue labelled `mapping` open, mentions you on it, comments when the list changes, and closes it when a build comes back clean.

**To fix an issue:** open the repository in Claude Code and ask it to resolve the open mapping issue. Each item needs some research on the web, then an edit to `rights.toml` with the source and today's date, then `python3 -m unittest`. Pushing to main rebuilds the page and, once the report is clean, closes the issue.

## Storylines

With every scheduled build, `story.py` asks Claude for one short, interest-rated paragraph per league with upcoming viewable fixtures, plus fixture notes and a forecast. The browser displays one paragraph, followed by a time-of-day outlook (This morning, This afternoon, This evening or Tonight), then the headline match. Claude also rates the full upcoming slate in the same call.

- **What Claude gets.** `build.py --facts` supplies upcoming fixtures with a known route through at least one service, including listed channels and the competition's established usual home. Matches with wholly unconfirmed coverage remain under Everything but are not candidates for news. The default lineup and competitions guide the opening; other service choices can reveal different researched items.
- **One blurb per league.** Every league with viewable upcoming fixtures gets its nearest available window in `league_candidates`, including leagues whose next games are several days away. Claude writes a sourced paragraph of at most 450 characters and scores its news interest from 0–100. The browser filters by services and competitions, then chooses the highest-interest blurb within 24 hours. If none qualifies, it chooses the highest-interest blurb in the nearest later 24-hour window. Later alternatives are retained even when near-term news exists for a different lineup. Missing or invalid paragraphs trigger one repair request alongside any missing fixture ratings.
- **Ranked recommendations.** A separate candidate list contains every upcoming fixture, including those with unconfirmed broadcasts. Claude assigns independent 0–100 scores for popularity, expected gameplay and competitive impact, using supplied form, tables and stage plus verified research. The combined score uses fixed weights of 25%, 35% and 40%. These are editorial ratings, not outcome probabilities. Scores use an absolute scale across days and competitions; an ordinary fixture does not gain points because the rest of the slate is weak. Missing or invalid scores are rejected and an incomplete response gets one repair request.
- **The headline match and standouts.** Next up selects the highest-rated match available on the visitor's selected services and competitions within 24 hours. If none is available, it uses the first available 24-hour window further ahead. Without ratings it falls back to kickoff order. Worth scheduling around includes only available upcoming fixtures scoring at least 80/100, including later dates; there is no minimum count or relative promotion. Both sections respond immediately to filters and completed matches leave them. Wholly unconfirmed broadcasts never qualify as viewable recommendations.
- **Match context, nearby first.** Every item must directly explain an upcoming fixture: stakes, player availability, selection supported by reporting, a relevant matchup, or a scheduling change. An international break can qualify through its effect on a listed fixture; general club news, financial investigations and unrelated managerial controversy do not. When fresh reporting is unavailable, Claude can explain a matchup using the supplied ESPN table, form or stage facts and cite the supplied match link. It must still provide useful context for each league without inventing breaking news or merely repeating kickoff times and channels.
- **Tagged phrases and filters.** League blurbs and forecast items contain explicit text segments with fixture references. Team, league and broadcaster tags come from the referenced schedule rows. A blurb disappears when none of its matches has coverage on the selected services and competitions. Within a mixed blurb, excluded phrases are dimmed: removing Apple TV can dim only “MLS” in “MLS and the Premier League.” Neutral connecting words remain unchanged. “Everything” widens the schedule but news still follows the selected services. Match notes follow the same availability rule. Unknown fixture references invalidate an item, and each item must cite a verified research source or a supplied ESPN match source.

- **Freshness.** New stories remain eligible through their explicit 24-hour horizon, including across midnight. Completed or elapsed fixtures leave the opening commentary. The page checks for updated story data every ten minutes. Old untagged opening text stays hidden until a research run produces the new format; existing match notes can still appear on their fixture rows.
- **The schedule summary.** The browser separately shows factual live/upcoming counts and coverage for the next 24 hours, with the next kickoff and simultaneous matches grouped together. Listed coverage, usual coverage awaiting a listing, unknown coverage, hidden competitions, pending scores and incomplete ESPN requests are distinguished. A later kickoff is explicitly labelled “Beyond 24 hours.” No browser-generated commentary invents a quiet day, an international break or a pick of the week.

- **Model and effort.** Claude Opus 5.5 at medium effort, its own default, for both the morning's full research and the updates. To change them without editing code, set the repository variables `STORY_MODEL` (`claude-opus-5-5` or `claude-sonnet-5-5`), `STORY_EFFORT` and `STORY_REFRESH_EFFORT` (`low`, `medium`, `high`, `xhigh` or `max`) under Settings, then Secrets and variables, then Actions, then Variables. The **Compare storyline models** workflow in the Actions tab writes today's storylines once per model and effort you list, publishes nothing, and lays the results side by side with their cost, time and sources, so the choice can be made on this page's own evidence.
- **Cost.** Each run logs its token use, searches, time and an estimated cost at list prices on the run's summary page. Rating every upcoming fixture adds input and output tokens, so cost varies with the slate and any repair request. Use those reports to set the API workspace's spend limit; a run that hits it publishes without new storylines or ratings.
- **Setup.** Add an Anthropic API key as the repository secret `ANTHROPIC_API_KEY` (Settings, then Secrets and variables, then Actions). Without it, or if a call fails, the page is published as usual without storylines. Delete the secret to turn storylines off.

## Notes

- Broadcast assignments come from ESPN and can change on the day. The facts in `rights.toml` reflect the 2026–27 season: Fox One carries FOX, FS1 and FS2 but not Fox Soccer Plus; ESPN Unlimited carries every ESPN network and ESPN+; TNT and TBS matches stream on HBO Max; CBS matches stream on Paramount+. Fubo means its Pro plan (FOX, FS1, FS2, ESPN, ESPN2, ABC, CBS, CBS Sports Network, NBC, USA Network, Telemundo, beIN Sports), which has no TNT, TBS, Univision or TUDN; ESPNU and Universo need its Elite plan, and ESPN Deportes, Fox Deportes and Fox Soccer Plus its International Sports Plus add-on. From 2026–27 most Bundesliga matches stream free on Fandango, with about 30 a season on USA Network. Liga MX is sold by home club; FOX took the Concacaf Nations League in English from 2026–27, and Paramount+ has every Concacaf Champions Cup match from 2027.
- Team and league images are linked from ESPN's image server, not copied into this repository.
- The page asks search engines not to index it.
