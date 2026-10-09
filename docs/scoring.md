# How matches are ranked

Soccer Outlook combines a match's appeal with your league preferences to recommend what to watch. The score is an editorial aid, not a prediction of the result or a probability. This guide describes the defaults in [settings.toml](../settings.toml); the owner can change those settings, and each visitor can change league priority in Lineup.

## Choosing matches and displaying them

All recommendations follow the same lineup rules as the schedule. Temporarily hiding a league from the strip also removes its recommendations. A match needs coverage on a selected service; a recognized usual home can qualify even before ESPN lists its channel.

- **Live now:** when ESPN reports eligible matches in progress, the top card shows the one with the highest pick score. A scheduled kickoff passing is not enough to call a match live.
- **Next up:** if none is live, the top card shows the soonest eligible kickoff with a confirmed time. Pick score breaks a tie between simultaneous kickoffs. A match can remain here as “Awaiting score” after its kickoff passes.
- **Top three:** choose up to three remaining matches by pick score, then display them in kickoff order. The top card's match is excluded. There is no minimum score and no extra preference for the next 24 hours.

The candidate window is today and the three following days, with each day starting at 4 am in the visitor's time zone. Finished matches are excluded. The daily AI ratings use the equivalent window in Eastern time, so the edges can differ for visitors elsewhere. A match with a time still to be confirmed can enter the top three, but not Next up.

If any remaining candidate has a valid AI rating, the top three use only rated candidates. This keeps a match scored by Outlook alone from gaining an advantage simply because its AI rating is missing. If no candidate has an AI rating, the Outlook score and league priority supply the ranking instead. Fewer than three eligible candidates produce fewer cards; no candidates hide the section.

## The pick score

With the current weights:

```text
match interest = 70% AI rating + 30% Outlook score
pick score    = 95% match interest + 5% league priority
```

For example, an AI rating of 60 and an Outlook score of 50 give match interest of 57. With league priority 80, the pick score is `0.95 × 57 + 0.05 × 80 = 58.15`, displayed as 58.2 in its tooltip.

The weights are proportions: each pair in `[blend]` is divided by its sum, so it need not add up to 100. Without an AI rating, match interest is the Outlook score alone; on an older page without an Outlook score, an available AI rating can stand alone. Neither available means no pick score. Turning AI off leaves Outlook and league priority active.

The browser ranks by the unrounded blend. Tooltips and dots use the score rounded to one decimal. A tooltip identifies the rating model and lists the score's parts.

## AI rating

The current configuration uses `gpt-6.1-sol` at `low` effort in the `ratings` design. It rates supplied fixture facts without web research. Overview and match-blurb generation are optional and currently off. The `full` design instead uses Claude's researched context alongside those facts.

[story.py](../story.py) requires three integer ratings from 0 to 100 and calculates their weighted total:

| Dimension | Weight | What it asks the model to judge |
| --- | ---: | --- |
| Popularity | 25% | Audience appeal |
| Expected gameplay | 35% | Football quality and competitiveness, without predicting a result |
| Competitive impact | 40% | Stakes supported by the table or competition stage |

The prompt uses a common scale across days and competitions: 40 is routine, 60 notably appealing, 80 exceptional, and 95 a rare global event or decisive final. A quiet slate is not a reason to inflate ratings. Invalid or missing components cause that rating to be rejected. These weights are fixed in `story.py`, separate from the adjustable blend in `settings.toml`.

The AI does not receive the Outlook score or its betting inputs. The two assessments contribute different information; agreement between them is not a guarantee of a good match.

## Outlook score

This is a calculation from ESPN's data in [build.py](../build.py). Each component runs from 0 to 1; their weighted mean is multiplied by 100 and rounded to one decimal.

| Component | Default weight | Calculation with the current settings |
| --- | ---: | --- |
| Occasion (`stature`) | 40% | Competition tier and named marquee teams, divided by 150 and capped at 1. |
| Evenly matched (`close`) | 25% | The market's implied draw chance: 0 at 10% or below, 1 at 30% or above, proportional between. |
| Stakes | 15% | A recognized knockout stage's value; otherwise a calculation from both teams' table positions. |
| TV | 10% | 1 for a listed broadcast network, 0.5 for a listed cable channel, 0 for other listed outlets. |
| Goals expected | 10% | The market's over/under line: 0 at 2 goals or below, 1 at 4 or above, proportional between. |

Missing data uses `[outlook] missing`, currently **0.5**, while retaining that component's weight. It is a midpoint assumption, not an omitted component: it can raise or lower the total compared with the available evidence alone. A match with no broadcaster listed has missing TV data; one listed only on a streaming service has TV value 0. Usual coverage does not replace that missing TV input.

The occasion calculation starts at 100, 60 or 30 for competition tiers 1, 2 or 3. It adds 25 per named marquee club, or 30 per named marquee national team and another 45 for the United States in the configured national-team competitions. UEFA Nations League groups use their own base values. The lists and exceptions live beside `LEAGUES` in `build.py`; they are editorial choices.

For league-table stakes, convert each team's position to a value from 1 (first) to 0 (last), within its own table. Use the larger of the lower team's value and `0.6 × (1 − the higher team's value)`. This rewards meetings near the top and, more modestly, near the bottom. A recognized knockout stage takes precedence: final 1, semifinal 0.9, quarterfinal 0.8, round of 16 or playoff 0.7. The longest matching stage name wins, so “semifinal” is not mistaken for “final.”

Odds come from the first entry in ESPN's scoreboard odds list; the code does not check the provider's name. The draw chance includes the bookmaker's margin. It is a rough indicator of competitiveness, not a calibrated estimate of match quality. Odds are used for scoring and are not displayed.

## League priority

The first league receives 100, the last 0, with equal steps between:

```text
priority = 100 × (number of leagues − rank) / (number of leagues − 1)
```

Here rank starts at 1. The denominator uses the **full league list**, including disabled leagues and leagues with no fixtures in the fetched schedule. Filtering does not recalculate those intervals. Reordering enabled leagues changes their positions in that full order, which the browser saves.

At the default 5% weight, moving a league from last to first adds five pick-score points. This is a small preference boost, not a strict tie breaker: it can change the order of matches with different interest scores.

Without a saved order, the browser uses an order supplied by the full AI design when available, followed by any omitted leagues in the built-in order. The current ratings-only design supplies no such order, so the default is `LEAGUES` in `build.py`:

| Rank | Competition | Rank | Competition |
| ---: | --- | ---: | --- |
| 1 | Champions League | 18 | Carabao Cup |
| 2 | Premier League | 19 | USL Championship |
| 3 | Liga MX | 20 | Championship |
| 4 | MLS | 21 | Conference League |
| 5 | La Liga | 22 | U.S. Open Cup |
| 6 | Men's friendly | 23 | Women's Champions League |
| 7 | NWSL | 24 | Women's Super League |
| 8 | Concacaf Nations League | 25 | Coppa Italia |
| 9 | Serie A | 26 | DFB-Pokal |
| 10 | Bundesliga | 27 | Scottish Premiership |
| 11 | Europa League | 28 | Eredivisie |
| 12 | Concacaf Champions Cup | 29 | Saudi Pro League |
| 13 | Women's friendly | 30 | Africa Cup of Nations |
| 14 | FA Cup | 31 | Primeira Liga |
| 15 | UEFA Nations League | 32 | Liga Profesional (Argentina) |
| 16 | Ligue 1 | 33 | Brasileirão |
| 17 | Copa del Rey | 34 | USL League One |

This is a starting preference for a US audience, informed by audience reporting reviewed on 8 October 2026. It is not a statistically established popularity table. Published figures cover different channels, time periods and kinds of matches; streaming audiences are often unavailable. In particular, playoff or final audiences cannot be compared directly with a regular-season average.

The sources behind that choice are retained here so an owner can revisit it:

- **Champions League, Premier League and Liga MX:** [CBS coverage reported by Awful Announcing](https://awfulannouncing.com/cbs/uefa-champions-league-final-sets-viewership-record-club-soccer.html), [NBC's Premier League season report](https://www.nbcsports.com/pressbox/press-releases/nbc-sports-caps-2025-26-premier-league-season-highlighted-by-memorable-moments-fantastic-finishes-viewership-milestones), [Liga MX reporting citing Sports Business Journal](https://www.goal.com/en/lists/liga-mx-remains-the-most-watched-soccer-league-on-u-s-television/blt2eb5436af5948721), and [Nielsen's total-viewing figures reported by SVG](https://www.sportsvideo.org/2026/04/30/nielsen-u-s-viewers-spent-79-8-billion-minutes-watching-soccer-in-2025/). Total viewing and per-match averages answer different questions; neither determines this top-three order on its own.
- **MLS and La Liga:** [MLS's playoff report](https://www.mlssoccer.com/news/mls-sees-strong-playoff-viewership-heading-into-mls-cup-presented-by-audi) and [La Liga's ESPN season report](https://www.laliga.com/en-US/news/laliga-scores-best-season-ever-in-us-on-espn-platforms). These support substantial US interest but do not offer a like-for-like comparison of their regular seasons across all services.
- **Women's soccer and national teams:** [NWSL postseason reporting](https://www.nwslsoccer.com/news/2025-nwsl-championship-postseason-records) and [USMNT friendly audiences](https://awfulannouncing.com/ratings/usmnt-germany-record-friendly-viewership-turner-tbs.html). National-team opponents and event importance make audiences particularly variable.

Lower positions rely more on editorial judgment about access and the teams involved because comparable US audience data is sparse. Nielsen measurement changes in 2025 also complicate comparisons with earlier seasons. Reorder leagues to suit your own interests rather than treating this default as authoritative.

## What the golden dots mean

Dots summarize the pick score using fixed thresholds in `[dots]`, not percentiles of that day's matches:

| Pick score, rounded to one decimal | Dots |
| --- | ---: |
| Below 35 | 1 |
| 35 to below 45 | 2 |
| 45 to below 55 | 3 |
| 55 to below 68 | 4 |
| 68 or more | 5 |

Changing league priority can change a match's dots. Hover over them for the exact score and its components; screen readers receive the score and dot count. They are separate from the colored squares showing each team's recent results.

See the [development guide](development.md) for changing settings and running the checks, or return to the [project overview](../README.md).
