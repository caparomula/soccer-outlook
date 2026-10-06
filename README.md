# Soccer Outlook

A week of soccer at a glance, judged against the streaming services you have.

**Live page:** https://caparomula.github.io/soccer-outlook/

The page opens with the day's storylines, researched on the web and written by Claude. It groups matches by where you are in the day (live now, this morning, this afternoon, this evening, tonight, tomorrow, later this week), says which of your services carries each one, and writes a short forecast of the day. Each match shows every US broadcaster ESPN lists, team form and table position, and a details panel with scorers, venue, a calendar link and the league table.

## Your lineup

Open **Lineup & filters** on the page and tap the services you have. Every match is then judged against your lineup; the choice stays in your browser. The default lineup is `OWNER` in `build.py`.

## How it runs

`build.py` is a single Python script with no dependencies beyond the standard library and `curl`; `story.py` adds the Anthropic Python SDK. It fetches eleven days of fixtures and the current standings from ESPN's public scoreboard API, maps each listed broadcaster to the streaming services that carry it (`OUTLETS`), applies each league's usual home when channels are not posted yet (`LEAGUES`), and writes one HTML file. The page's own script does the rest in the browser: bucketing by the viewer's clock, the lineup, filters, the countdown and live scores.

The workflow in `.github/workflows/refresh.yml` runs the script three times a day and publishes the page to the `gh-pages` branch, which GitHub Pages serves. It can also be run by hand from the Actions tab (**Refresh outlook**, then **Run workflow**), and it runs on every change to `build.py`.

To build locally:

```sh
python3 build.py --out site/index.html
```

Add `--date YYYY-MM-DD` to build as of another day. Open the page with `#at-YYYYMMDD-HHMM` on the URL to preview it as of another local time.

## Live scores

The page is rebuilt three times a day, but scores don't wait for a rebuild. From 15 minutes before a kickoff until ESPN calls the match over, the viewer's browser asks ESPN's public scoreboard for that competition's day once a minute and updates the score, the clock, half time and full time, the scorers, the "Live now" group, the countdown band, the picks and the forecast. A match that finished since the last rebuild is asked about once when the page opens, so "Earlier today" carries its result.

- **Loading stays fast.** The requests start after the page has drawn, run in parallel (one per competition and day, not one per match), and each is abandoned after 8 seconds. Nothing waits on them.
- **Failing quietly.** A request that fails or times out leaves the page as it was built. After a failure the page waits longer before trying again, doubling up to ten minutes, and the line under the forecast says when the scores were last checked.
- **Not wasteful.** Nothing is asked while the tab is hidden, while no match is near its kickoff, or for competitions switched off in the filters.
- **Testing.** Served from `localhost`, the page accepts `?scoresbase=http://localhost:PORT/some/path/` to read scoreboards from a local stand-in for ESPN instead.

## Storylines

Each morning `story.py` asks Claude Opus 5.5 to research the day's most interesting matches on the web and write a headline, a short lede and a one- or two-sentence note per match. The page shows them at the top, under each match and on the cards, with links to the pages they came from.

- **What Claude gets.** `build.py --facts` writes the notable matches of today and tomorrow, and the biggest of the week, with only what ESPN reports: kickoff, competition, venue, table position, form, top scorer and where the match can be watched. Claude searches and reads the web for the rest, with up to 12 searches and 6 full-page reads per run.
- **Keeping it honest.** Claude is told to state only what it read during the run. The script keeps only source links that its own searches and page reads returned, drops any note left without one, and drops notes about matches that aren't in the facts. The page labels the storylines as written by Claude and hides them once they are more than 30 hours old.
- **When it runs.** The early-morning run writes fresh storylines; the midday and evening runs carry them forward. A manual run from the Actions tab with **Write fresh storylines** ticked writes new ones on demand.
- **Cost.** Each run logs its token use, searches and an estimated cost at list prices on the run's summary page. Expect a few tens of cents to about a dollar a day.
- **Setup.** Add an Anthropic API key as the repository secret `ANTHROPIC_API_KEY` (Settings, then Secrets and variables, then Actions). Without it, or if a call fails, the page is published as usual without storylines. Delete the secret to turn storylines off.

## Notes

- Broadcast assignments come from ESPN and can change on the day. The rights notes in `OUTLETS` reflect the 2026–27 season: Fox One carries FOX, FS1 and FS2 but not Fox Soccer Plus; ESPN Unlimited carries every ESPN network and ESPN+; TNT and TBS matches stream on HBO Max; CBS matches stream on Paramount+. Fubo means its Pro plan (FOX, FS1, FS2, ESPN, ESPN2, ABC, CBS, CBS Sports Network, NBC, USA Network, Telemundo, beIN Sports), which has no TNT, TBS, Univision or TUDN; ESPNU and Universo need its Elite plan, and ESPN Deportes, Fox Deportes and Fox Soccer Plus its International Sports Plus add-on.
- Team and league images are linked from ESPN's image server, not copied into this repository.
- The page asks search engines not to index it.
