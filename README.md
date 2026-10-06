# Soccer Outlook

A week of soccer at a glance, judged against the streaming services you have.

**Live page:** https://caparomula.github.io/soccer-outlook/

The page groups matches by where you are in the day (live now, this morning, this afternoon, this evening, tonight, tomorrow, later this week), says which of your services carries each one, and writes a short forecast of the day. Each match shows every US broadcaster ESPN lists, team form and table position, and a details panel with scorers, venue, a calendar link and the league table.

## Your lineup

Open **Lineup & filters** on the page and tap the services you have. Every match is then judged against your lineup; the choice stays in your browser. The default lineup is `OWNER` in `build.py`.

## How it runs

`build.py` is a single Python script with no dependencies beyond the standard library and `curl`. It fetches eleven days of fixtures and the current standings from ESPN's public scoreboard API, maps each listed broadcaster to the streaming services that carry it (`OUTLETS`), applies each league's usual home when channels are not posted yet (`LEAGUES`), and writes one HTML file. The page's own script does the rest in the browser: bucketing by the viewer's clock, the lineup, filters and the countdown.

The workflow in `.github/workflows/refresh.yml` runs the script three times a day and publishes the page to the `gh-pages` branch, which GitHub Pages serves. It can also be run by hand from the Actions tab (**Refresh outlook**, then **Run workflow**), and it runs on every change to `build.py`.

To build locally:

```sh
python3 build.py --out site/index.html
```

Add `--date YYYY-MM-DD` to build as of another day. Open the page with `#at-YYYYMMDD-HHMM` on the URL to preview it as of another local time.

## Notes

- Broadcast assignments come from ESPN and can change on the day. The rights notes in `OUTLETS` reflect the 2026–27 season: Fox One carries FOX, FS1 and FS2 but not Fox Soccer Plus; ESPN Unlimited carries every ESPN network and ESPN+; TNT and TBS matches stream on HBO Max; CBS matches stream on Paramount+.
- Team and league images are linked from ESPN's image server, not copied into this repository.
- The page asks search engines not to index it.
