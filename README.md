# Soccer Outlook

A week of soccer at a glance, judged against the streaming services you have.

**Live page:** https://caparomula.github.io/soccer-outlook/

The page opens with the day's storylines, researched on the web and written by Claude. It groups matches by where you are in the day (live now, this morning, this afternoon, this evening, tonight, tomorrow, later this week), says which of your services carries each one, and gives a short forecast of the day and the week ahead, also written by Claude. Each match shows every US broadcaster ESPN lists, team form and table position, and a details panel with scorers, venue, a calendar link and the league table.

## Your lineup

Tap **Lineup** at the top right of the page (it stays there as you scroll) and tap the services you have and the competitions you want. Every match is then judged against your lineup; the choice stays in your browser. **Clear all** deselects every service so you can build a lineup from nothing, and **Reset to the default** brings back the owner's lineup, `OWNER` in `build.py`.

## How it runs

`build.py` is a single Python script with no dependencies beyond the standard library and `curl`; `story.py` adds the Anthropic Python SDK. It fetches eleven days of fixtures and the current standings from ESPN's public scoreboard API, maps each listed broadcaster to the streaming services that carry it, applies each competition's usual home when channels are not posted yet (both from `rights.toml`, below), and writes one HTML file. The page's own script does the rest in the browser: bucketing by the viewer's clock, the lineup, filters, the countdown and live scores.

The workflow in `.github/workflows/refresh.yml` runs the script three times a day and publishes the page to the `gh-pages` branch, which GitHub Pages serves. It can also be run by hand from the Actions tab (**Refresh outlook**, then **Run workflow**), and it runs on every change to `build.py`, `rights.toml` or the tests.

To test and build locally:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --warnings mapping-report.txt
```

Add `--date YYYY-MM-DD` to build as of another day. Open the page with `#at-YYYYMMDD-HHMM` on the URL to preview it as of another local time.

## Live scores

The page is rebuilt three times a day, but scores don't wait for a rebuild. From 15 minutes before a kickoff until ESPN calls the match over, the viewer's browser asks ESPN's public scoreboard for that competition's day once a minute and updates the score, the clock, half time and full time, the scorers, the "Live now" group, the countdown band, the picks and the forecast. A match that finished since the last rebuild is asked about once when the page opens, so "Earlier today" carries its result.

- **Loading stays fast.** The requests start after the page has drawn, run in parallel (one per competition and day, not one per match), and each is abandoned after 8 seconds. Nothing waits on them.
- **Failing quietly.** A request that fails or times out leaves the page as it was built. After a failure the page waits longer before trying again, doubling up to ten minutes, and the line under the forecast says when the scores were last checked.
- **Not wasteful.** Nothing is asked while the tab is hidden, while no match is near its kickoff, or for competitions switched off in the filters.
- **Testing.** Served from `localhost`, the page accepts `?scoresbase=http://localhost:PORT/some/path/` to read scoreboards from a local stand-in for ESPN instead.

## Keeping the broadcast facts right

Which services carry which channels, the names ESPN uses for each channel, and where each competition usually lives are all in `rights.toml`, each with its source and the date it was last checked. These facts change with every season's rights deals, and sometimes mid-season with a carriage dispute, and ESPN's data shows none of that. Several guards keep a stale or mistaken fact from quietly misleading the page:

- **Tests before every publish.** `tests/test_rights.py` checks that the file agrees with itself (every service names real channels, no two channels claim one ESPN name, every usual home is carried by some service and has a season end, every fact has a source and date, no misspelt keys) and repeats each mistake the page has actually made, from USA Network's "USA Net" to the Bundesliga's move off ESPN. The workflow runs them before building; if they fail, the last good page stays up and GitHub emails about the failed run.
- **Competitions ESPN doesn't list.** ESPN posts no broadcasters at all for some competitions (Liga MX, the Primeira Liga, the Brasileirão, Argentina, the Saudi Pro League) and few for others, so for them `rights.toml` is the only basis. Where one service carries every match, it is the usual home (the Primeira Liga on beIN Sports Connect). Where rights go club by club, `by_home_team` names the usual home for each home club (Liga MX: ViX for TelevisaUnivision's 14 clubs, Peacock for Chivas, FOX for Juárez, Santos and Tijuana); a club it doesn't name claims nothing and is reported. Where no service has every match (Ligue 1, the Saudi Pro League), the page shows a hint instead of a claim.
- **Usual homes lapse.** Each competition's usual home is for one season and lapses after its `until` date. After that the page claims no usual home for it until someone confirms the new season's, because rights change at the turn of a season and a stale claim is worse than none.
- **Unrecognized channels say so.** A broadcaster name ESPN uses that isn't in the file shows as "not recognized", not as "not in your lineup", and the match stays visible under **On my services** because it might be on one of them.
- **A report, as a GitHub issue.** Each build lists what it couldn't map or vouch for: unknown broadcaster names, a usual home that disagrees with ESPN's listings or lapses within 30 days, and facts unchecked for 180 days. While that list has anything in it, the workflow keeps one issue labelled `mapping` open, mentions you on it, comments when the list changes, and closes it when a build comes back clean.

**To fix an issue:** open the repository in Claude Code and ask it to resolve the open mapping issue. Each item needs some research on the web, then an edit to `rights.toml` with the source and today's date, then `python3 -m unittest`. Pushing to main rebuilds the page and, once the report is clean, closes the issue.

## Storylines

With every scheduled build, `story.py` asks Claude to research the day's most interesting matches on the web and write a headline, a short lede and a one- or two-sentence note per match, and, in the same call, the forecast below them. The page shows the storylines at the top, under each match and on the cards, with links to the pages they came from.

- **What Claude gets.** `build.py --facts` writes the notable matches of today and tomorrow, the biggest of the week, and the day's notable results so far, with only what ESPN reports: kickoff, competition, venue, table position, form, top scorer, scorers and where the match can be watched. For the forecast it also gets the week by day and competition: how many matches, how many on the owner's services, how many are over and the first kickoff. Competitions the page hides by default are left out. Claude searches and reads the web for the rest.
- **Keeping it honest.** Claude is told to state only what it read during the run. The script keeps only source links that its own searches and page reads returned, drops any note left without one, and drops notes about matches that aren't in the facts. The page labels the storylines as written by Claude and shows them only on the day they were written for: from midnight until the morning's run replaces them, it shows none rather than yesterday's preview of matches that are over.
- **The forecast.** Claude writes a label for the kind of day (it heads the page, beside the date), a sentence or two on the day's shape and one on tomorrow and the week ahead, from the schedule rather than the web, so it carries no links. Between those two the page keeps its own line of what is live and still to come on your services, which changes by the minute. The story records the lineup the forecast was written for; a viewer with another lineup, or a story without a forecast, gets the forecast the page composes itself, and so does everyone from midnight until the morning's run. An open tab drops a story whose day is over and looks for a newer one every ten minutes, so it picks up the midday and evening updates without a reload.
- **When it runs.** The early-morning build researches the day from scratch (up to 12 searches and 6 page reads). The midday and evening builds update that story for the moment: they get the earlier story and its sources, search only for what may have changed (results, team news, confirmed lineups, injuries), keep the notes that still hold and rewrite the rest, with up to 5 searches and 2 page reads. A build after a code push republishes the current story without calling Claude, since the news hasn't changed. A manual run from the Actions tab (**Refresh outlook**, then **Run workflow**) chooses: update, write from scratch, or keep.
- **Model and effort.** Claude Opus 5.5 at medium effort, its own default, for both the morning's full research and the updates. To change them without editing code, set the repository variables `STORY_MODEL` (`claude-opus-5-5` or `claude-sonnet-5-5`), `STORY_EFFORT` and `STORY_REFRESH_EFFORT` (`low`, `medium`, `high`, `xhigh` or `max`) under Settings, then Secrets and variables, then Actions, then Variables. The **Compare storyline models** workflow in the Actions tab writes today's storylines once per model and effort you list, publishes nothing, and lays the results side by side with their cost, time and sources, so the choice can be made on this page's own evidence.
- **Cost.** Each run logs its token use, searches, time and an estimated cost at list prices on the run's summary page. The first full run, at high effort, cost about $0.72, and the first at medium about $0.46; the forecast adds a cent or two, and an update is designed to cost roughly half a full run. Expect about $1.00 to $1.40 a day, or $30 to $42 a month, at the default settings, and set the API workspace's spend limit above that, or a run that hits it will publish without new storylines.
- **Setup.** Add an Anthropic API key as the repository secret `ANTHROPIC_API_KEY` (Settings, then Secrets and variables, then Actions). Without it, or if a call fails, the page is published as usual without storylines. Delete the secret to turn storylines off.

## Notes

- Broadcast assignments come from ESPN and can change on the day. The facts in `rights.toml` reflect the 2026–27 season: Fox One carries FOX, FS1 and FS2 but not Fox Soccer Plus; ESPN Unlimited carries every ESPN network and ESPN+; TNT and TBS matches stream on HBO Max; CBS matches stream on Paramount+. Fubo means its Pro plan (FOX, FS1, FS2, ESPN, ESPN2, ABC, CBS, CBS Sports Network, NBC, USA Network, Telemundo, beIN Sports), which has no TNT, TBS, Univision or TUDN; ESPNU and Universo need its Elite plan, and ESPN Deportes, Fox Deportes and Fox Soccer Plus its International Sports Plus add-on. From 2026–27 most Bundesliga matches stream free on Fandango, with about 30 a season on USA Network. Liga MX is sold by home club; FOX took the Concacaf Nations League in English from 2026–27, and Paramount+ has every Concacaf Champions Cup match from 2027.
- Team and league images are linked from ESPN's image server, not copied into this repository.
- The page asks search engines not to index it.
