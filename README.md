# Soccer Outlook

A soccer schedule that helps you choose what to watch on your US streaming services and TV channels.

**[Open Soccer Outlook](https://caparomula.github.io/soccer-outlook/)**

The page shows **today and the next three days**, with the previous three days available to browse. Open the **Settings** gear: choose services in **Lineup** and competitions in **Leagues**. The schedule and featured matches follow those choices. Preferences stay in your browser, with no account required.

Choose subscriptions and channels you can actually watch. FOX One's paid subscription and individual FOX channels have separate filters, so a TV-provider login is not treated as full FOX One access. The [user guide](docs/user-guide.md#fox-one-and-tv-provider-access) explains the choices.

- **Match carousel:** swipe through your filtered schedule, starting with the highest-rated live match or the next confirmed kickoff. Its cards use the same layout as Top picks. Recent matches and results are to the left; time-group headings help you find what is on this morning, tonight or later in the week. Keyboard users can focus the carousel and use the arrow keys, Home or End.
- **Top picks:** up to three other recommended matches, selected by interest and shown in kickoff order. League headlines follow these cards, then the schedule.
- **Daily schedule:** kickoff times, broadcasters, team form, standings and match details. A day changes at 4 am in your time zone so late games stay with their evening.
- **Team vitals:** tap a team name or badge for its record, recent form, home ground, home city and leading contributors, where ESPN provides them.
- **Viewing links:** hover over or tap a network pill to see your providers and any supported match links. Links follow your selected services; manually supplied YouTube TV links are available only while the match is live.
- **Your own AI:** open **Settings → AI** to copy a prompt into your preferred chat, then paste its response back for personal headlines, match blurbs and ratings. Headlines and blurbs start hidden; one switch enables both. Add your interests to the prompt and choose how much its ratings influence recommendations. No AI account connection is required. Repository maintainers can also [publish a response as the site's shared defaults](docs/user-guide.md#publish-recommendations-for-everyone).

ESPN supplies fixtures and live scores. The builder also checks supported broadcasters' public schedules, including simulcasts that may be missing from ESPN even when another channel is already listed. Confirmed listings replace the uncertainty label; **More** links to the source or explains why coverage remains unconfirmed. The project maps US broadcast coverage in [rights.toml](rights.toml). Its recommendations combine an AI rating, an Outlook score calculated from fixture data, and your league preferences. The site uses Claude Opus 5.5 at high effort for daily researched headlines, match blurbs and ratings. The browser's text-display switch still starts off. Personal imports take precedence over shared recommendations; both expire within 24 hours of their original prompt. Applying a response stays local unless you connect GitHub publishing in **Settings → AI**.

## Guides

| I want to… | Read |
| --- | --- |
| Choose services, save preferences, or understand the cards | [Using Soccer Outlook](docs/user-guide.md) |
| Understand the golden dots, ranking formula, or default league order | [How recommendations work](docs/scoring.md) |
| Compare past ratings and their inputs with recorded match outcomes | [Match history and retrospective analysis](docs/history.md) |
| Run locally, change settings, maintain coverage, or deploy | [Development and maintenance](docs/development.md) |
| Understand schedule sources, coverage gaps and refreshes | [Schedule data and coverage](docs/schedule-data.md) |
| Compare licensed data suppliers or request source permission | [Data licensing options and inquiry drafts](docs/data-licensing.md) |
| Register with search engines or help people find the app | [Search and discovery](docs/discovery.md) |
| Review the proposed mobile companion and its development stages | [Companion app plan and specifications](docs/companion/README.md) |

## Run locally

You need **Python 3.11 or newer**, **curl**, and access to ESPN's public APIs. From the repository root:

```sh
python3 -m unittest -v
python3 build.py --out site/index.html --facts work/facts.json --warnings work/mapping-report.txt
python3 -m http.server 8000 --directory site
```

Open `http://localhost:8000` in your browser. Building the schedule needs no API key and makes no AI calls. Without current personal, shared or automatic AI ratings, recommendations use the Outlook score and league priority. See the [development guide](docs/development.md) for optional AI generation and browser tests.

Page structure, styles and browser behavior live in [web/](web/). Rebuild after editing them: the generator embeds those files in the HTML. [settings.toml](settings.toml) controls AI and scoring; [build.py](build.py) defines tracked leagues and the default lineup.

## Updates and coverage

GitHub Actions runs full builds three times a day and lightweight fixture/broadcast refreshes every half hour. Lightweight runs reuse standings and existing AI output. Open pages check for schedule updates every five minutes and live scores about once a minute; GitHub can delay scheduled jobs. Broadcast listings can change; **usually** means an established rights arrangement rather than a confirmed listing for that match. Check the broadcaster's app before kickoff.

Each successful refresh saves match metrics and available AI ratings to a permanent `match-history` branch before publishing. These snapshots preserve the score components, inputs, configuration and later match results for retrospective analysis. Reported attendance is retained when available; television and streaming audience figures are not supplied by the current feeds. This records public match data, not visitors' viewing activity. See the [history guide](docs/history.md) for the limits and how to retrieve it.

The site allows search indexing and publishes a [sitemap](https://caparomula.github.io/soccer-outlook/sitemap.xml). Its [Premier League](https://caparomula.github.io/soccer-outlook/premier-league/), [MLS](https://caparomula.github.io/soccer-outlook/mls/) and [Paramount+](https://caparomula.github.io/soccer-outlook/paramount-plus/) schedule pages offer direct routes into the app. See [Search and discovery](docs/discovery.md) for search-engine registration and update notifications. Images normally load from ESPN, and fonts load from Google Fonts.

If you find the page useful, you can [buy the owner a coffee](https://www.buymeacoffee.com/caparomula).

## License

The code, documentation and original artwork are available under the [zlib License](LICENSE), copyright © 2026 caparomula. Copying, modification and redistribution are allowed, including commercial use, provided that:

- You do not claim you wrote the original software.
- You clearly mark altered source versions as modified.
- You keep the license notice in source distributions.

Credit in product documentation is appreciated but not required. Third-party match data, team and league badges, and fonts are not covered by this license; their owners' terms still apply.
