# Soccer Outlook

A soccer schedule that helps you choose what to watch on your US streaming services and TV channels.

**[Open Soccer Outlook](https://caparomula.github.io/soccer-outlook/)**

The page shows **today and the next three days**. Choose your services and leagues in **Lineup**; the schedule and featured matches follow those choices. Preferences stay in your browser, with no account required.

- **Live now / Next up:** the highest-scoring available live match, or the next scheduled kickoff.
- **Top three:** up to three other recommended matches, selected by interest and shown in kickoff order.
- **Daily schedule:** kickoff times, broadcasters, team form, standings and match details. A day changes at 4 am in your time zone so late games stay with their evening.

ESPN supplies fixtures and live scores. The project maps US broadcast coverage in [rights.toml](rights.toml). Its recommendations combine an AI rating, an Outlook score calculated from ESPN data, and your league preferences. The current configuration uses OpenAI's `gpt-6.1-sol` for daily ratings; **AI overviews and match blurbs are turned off**.

## Guides

| I want to… | Read |
| --- | --- |
| Choose services, save preferences, or understand the cards | [Using Soccer Outlook](docs/user-guide.md) |
| Understand the golden dots, ranking formula, or default league order | [How recommendations work](docs/scoring.md) |
| Run locally, change settings, maintain coverage, or deploy | [Development and maintenance](docs/development.md) |

## Run locally

You need **Python 3.11 or newer**, **curl**, and access to ESPN's public APIs. From the repository root:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --facts work/facts.json --warnings work/mapping-report.txt
python3 -m http.server 8000 --directory site
```

Open `http://localhost:8000` in your browser. Building the schedule needs no API key and makes no AI calls. Without a current `site/story.json`, recommendations use the Outlook score and league priority. See the [development guide](docs/development.md) for optional AI generation and browser tests.

Page structure, styles and browser behavior live in [web/](web/). Rebuild after editing them: the generator embeds those files in the HTML. [settings.toml](settings.toml) controls AI and scoring; [build.py](build.py) defines tracked leagues and the default lineup.

## Updates and coverage

GitHub Actions normally rebuilds the published schedule three times a day. While the page is open, the browser also checks live scores about once a minute. Broadcast listings can change; **usually** means an established rights arrangement rather than a confirmed listing for that match. Check the broadcaster's app before kickoff.

The site allows search indexing and publishes a [sitemap](https://caparomula.github.io/soccer-outlook/sitemap.xml) to help search engines discover it. Images normally load from ESPN, and fonts load from Google Fonts.

If you find the page useful, you can [buy the owner a coffee](https://www.buymeacoffee.com/caparomula).

## License

The code, documentation and original artwork are available under the [zlib License](LICENSE), copyright © 2026 caparomula. Copying, modification and redistribution are allowed, including commercial use, provided that:

- You do not claim you wrote the original software.
- You clearly mark altered source versions as modified.
- You keep the license notice in source distributions.

Credit in product documentation is appreciated but not required. Third-party match data, team and league badges, and fonts are not covered by this license; their owners' terms still apply.
