# How matches are ranked

Soccer Outlook combines a match's appeal with your league preferences to recommend what to watch. The score is an editorial aid, not a prediction of the result or a probability. This guide describes the defaults in [settings.toml](../settings.toml); the owner can change those settings, and each visitor can change league priority in **Settings → Leagues**.

## Choosing matches and displaying them

All recommendations follow the same lineup rules as the schedule. Temporarily hiding a league from the strip also removes its recommendations. A match needs coverage on a selected service; a recognized usual home can qualify even before ESPN lists its channel.

- **Match carousel:** browse the filtered schedule, including the previous three sports days. Recent matches and results come first in kickoff order, then live matches in pick-score order, then upcoming matches in kickoff order. Pick score breaks ties between simultaneous kickoffs. Matches without a confirmed time follow the timed matches on their sports day.
- **Starting card:** open on the highest-scoring live match, or the earliest confirmed upcoming kickoff when none is live. If only results remain, open on the latest one. A scheduled kickoff passing is not enough to call a match live: its status remains pending until ESPN reports it. The default card follows the current live or next kickoff as the schedule changes. While browsing away from that card, your chosen match stays selected through updates and filter changes as long as it remains eligible. Returning to the starting card resumes following; a newly finished or removed selection returns there too.
- **Top picks:** choose up to three other upcoming matches by pick score, then display them in kickoff order. All live matches are excluded; when none is live, all matches at the earliest confirmed kickoff are excluded instead. These exclusions stay the same while you browse the carousel, so swiping does not reshuffle the picks. The heading names the days covered by the picks, and each card shows its match day. There is no minimum score and no extra preference for the next 24 hours.

The match carousel comes first, followed by Top picks, league headlines and the schedule. Recent matches and results are to the left of the starting card: Yesterday, then the two preceding weekdays as you go back. An overdue fixture whose result is not yet reported keeps its pending status; browsing it does not imply it is live or finished. Headings group today's upcoming matches into morning (4 am–noon), afternoon (noon–5 pm), evening (5–9 pm) and tonight (9 pm–4 am), followed by Tomorrow and weekday headings. Today’s unconfirmed times have their own group. A heading stays in place while you browse its matches and slides away when the next group arrives. Swipe horizontally to browse; keyboard users can focus the carousel and use the left/right arrow keys, Home or End. Scrolling back to the starting card resumes following it. Match stories and sources remain in **More**; the gold rating dots open the rating breakdown separately.

The recommendation window is today and the three following days, with each day starting at 4 am in the visitor's time zone. Finished matches cannot enter Top picks. The daily AI ratings use the equivalent window in Eastern time, so the edges can differ for visitors elsewhere. A match with a time still to be confirmed can enter Top picks, but cannot be the next confirmed kickoff.

With automatic recommendations, if any remaining candidate has a valid AI rating, the Top picks section uses only rated candidates. This keeps a match scored by Outlook alone from gaining an advantage simply because its AI rating is missing. If no candidate has an AI rating, the Outlook score and league priority supply the ranking instead.

While a personal or shared AI response is active for at least one eligible match, all eligible candidates can compete. Matches outside those responses use their automatic AI rating when available, or the Outlook score otherwise, with league priority still applied. This lets newly enabled matches enter Top picks without requiring another import first. Fewer than three eligible candidates produce fewer cards; no candidates hide the section.

## The pick score

For automatic AI ratings, the current weights are:

```text
match interest = 70% AI rating + 30% Outlook score
pick score    = 95% match interest + 5% league priority
```

For example, an AI rating of 60 and an Outlook score of 50 give match interest of 57. With league priority 80, the pick score is `0.95 × 57 + 0.05 × 80 = 58.15`, displayed as 58.2 in its tooltip.

The weights are proportions: each pair in `[blend]` is divided by its sum, so it need not add up to 100. Without an AI rating, match interest is the Outlook score alone; on an older page without an Outlook score, an available AI rating can stand alone. Neither available means no pick score. Turning automatic AI off leaves personal and shared responses, Outlook and league priority available.

For each fixture, a valid personal rating takes precedence over a shared rating, then the automatic AI rating. **Imported rating influence** replaces the 70/30 interest split for your personally imported matches: an influence of 50% means equal parts imported rating and Outlook score. Shared ratings use the blend their publisher saved. Neither changes the 95/5 interest/league-priority split; your league order still applies.

Personal and shared ratings reflect the preferences in the prompt that produced them, so they need not match the site's standard rating dimensions below. Both expire 24 hours after their original prompt; publishing does not renew them. **Restore site recommendations** removes your personal import and returns to shared or automatic recommendations without deleting the shared defaults. See [Use your own AI for blurbs](user-guide.md#use-your-own-ai-for-blurbs).

Turning off **Settings → AI → Show AI headlines and match blurbs** hides text only. It does not disable AI ratings or change any of these scoring rules.

The browser ranks by the unrounded blend. Tooltips and dots use the score rounded to one decimal. A tooltip identifies the rating model and lists the score's parts.

## Choosing the lead headline

Headlines have their own editorial interest ratings, separate from match scores. The AI is asked to use the strongest researched angle from the match blurbs, include the competition's name naturally, and omit routine schedule announcements. A raw interest rating below 60 is never displayed.

Among eligible headlines, the nearest rolling 24-hour window comes first. Within that window:

```text
headline selection score = 80% editorial interest + 20% league priority
```

League priority uses the same saved order and 0–100 scale described below. These headline weights are independent of the match weights in `settings.toml`. Match-linked headlines use their earliest referenced kickoff and disappear when any referenced match finishes or fails the filters. Sourced calendar notices use their verified event period; an ongoing event is current, while a Saturday event stays in Saturday's window. Undated notices are hidden until a fresh AI response supplies dates. Completed rounds do not remain eligible as recap headlines.

## AI rating

Automatic league headlines carry their own authored title and editorial interest score. Their carousel cards show the supporting paragraph directly beneath the league emblem and headline. The automatic configuration uses `claude-opus-5-5` at `high` effort in the `full` design. It combines researched context with supplied fixture facts to generate daily ratings, headlines and match blurbs. Personal or shared chat responses can still take precedence. The optional `ratings` design instead requests scores from supplied facts without research.

[story.py](../story.py) requires three integer ratings from 0 to 100 and calculates their weighted total:

| Dimension | Weight | What it asks the model to judge |
| --- | ---: | --- |
| Popularity | 50% | Audience appeal, including worldwide anticipation |
| Expected gameplay | 35% | Football quality and competitiveness, without predicting a result |
| Competitive impact | 15% | Stakes supported by the table or competition stage |

Popularity is a judgment of audience appeal, not a forecast of attendance or a measured television or streaming audience. The Outlook score's occasion component below is another estimate of appeal. The project does not currently predict a number of spectators or viewers.

The prompt rates each dimension independently on an absolute scale. Popularity of 80 means a major draw; 90–100 means a globally anticipated elite matchup, at any stage. Gameplay of 80 means excellent expected quality with credible competition; 90–100 means exceptional quality between closely matched teams. Impact of 80 means major title, survival or knockout consequences; 90–100 means a decisive title or elimination match. An early tournament stage can limit impact without capping popularity or gameplay. A quiet slate is not a reason to inflate ratings, and no named match receives a special bonus.

Invalid or missing components cause that rating to be rejected. These weights are fixed in `story.py`, separate from the adjustable blend in `settings.toml`. Personal and shared imports supply a single score, so their totals are not recalculated using these component weights.

When the formula changes, a refresh can recalculate existing automatic totals from their saved component ratings without another AI request. The publication revision advances so open browsers receive the new totals; the original text, component judgments, generation time and expiry stay the same. Updated prompt guidance applies when the AI next generates ratings.

The automatic AI rating request does not include the Outlook score or its betting inputs. The two assessments contribute different information; agreement between them is not a guarantee of a good match.

## Outlook score

This is a calculation from fixture data in [build.py](../build.py). ESPN supplies the team, table and market inputs. Each component runs from 0 to 1; their weighted mean is multiplied by 100 and rounded to one decimal. Broadcast coverage determines whether a match passes your service filters, but does not increase or reduce its rating.

| Component | Default weight | Calculation with the current settings |
| --- | ---: | --- |
| Occasion (`stature`) | 50% | Competition tier and named marquee teams, divided by 150 and capped at 1. |
| Evenly matched (`close`) | 25% | Balance between the teams' implied win chances: `1 − abs(home − away) / (home + away)`. Equal chances give 1; a larger gap lowers the value. |
| Stakes | 10% | A recognized knockout stage's value; otherwise a calculation from both teams' table positions. |
| Goals expected | 15% | The market's over/under line: 0 at 2 goals or below, 1 at 4 or above, proportional between. |

Missing data uses `[outlook] missing`, currently **0.5**, while retaining that component's weight. It is a midpoint assumption, not an omitted component: it can raise or lower the total compared with the available evidence alone. Evenness requires valid win odds for both teams; a draw price alone cannot supply it. Older saved schedules without those win odds have missing evenness until refreshed.

The occasion calculation starts at 100, 60 or 30 for competition tiers 1, 2 or 3. It adds 25 per marquee club, or 30 per marquee national team and another 45 for the United States in the configured national-team competitions. UEFA Nations League groups use their own base values. The lists and exceptions live in `build.py`; they are editorial choices. Teams are identified by ESPN IDs, with readable names beside them, so a spelling or display-name change does not silently change a score. A team without a recognized ID receives no team-specific bonus.

For league-table stakes, convert each team's position to a value from 1 (first) to 0 (last), within its own table. Use the larger of the lower team's value and `0.6 × (1 − the higher team's value)`. This rewards meetings near the top and, more modestly, near the bottom. A recognized knockout stage takes precedence: final 1, semifinal 0.9, quarterfinal 0.8, round of 16 or playoff 0.7. The longest matching stage name wins, so “semifinal” is not mistaken for “final.”

Odds come from the first entry in ESPN's scoreboard odds list; the code does not check the provider's name. American moneylines are converted to implied probabilities: a positive price `x` gives `100 / (x + 100)`, and a negative price gives `abs(x) / (abs(x) + 100)`. Evenness compares the two win probabilities proportionally, so a common proportional bookmaker margin cancels out; other pricing biases can remain. It does not use the draw price: high-scoring teams can be evenly matched even when a draw is unlikely. Market prices are still only a proxy for competitiveness, not a calibrated estimate of match quality. Odds are used for scoring and are not displayed.

The parser first looks for a complete pair of current prices in `moneyline.home.close.odds` and `moneyline.away.close.odds`, then ESPN's older `homeTeamOdds.moneyLine` and `awayTeamOdds.moneyLine` fields. If neither pair is complete, it can use both teams' opening prices from `moneyline.home.open.odds` and `moneyline.away.open.odds`. It never combines a current price with an opening price, or substitutes a spread price for a win price.

## League priority

The first league receives 100, the last 0, with equal steps between:

```text
priority = 100 × (number of leagues − rank) / (number of leagues − 1)
```

Here rank starts at 1. The denominator uses the **full league list**, including disabled leagues and leagues with no fixtures in the fetched schedule. Filtering does not recalculate those intervals. Reordering enabled leagues changes their positions in that full order, which the browser saves.

At the default 5% weight, moving a league from last to first adds five pick-score points. This is a small preference boost, not a strict tie breaker: it can change the order of matches with different interest scores.

Without a saved order, the browser uses `LEAGUES` in `build.py`. That configured order applies in both AI designs; an AI response does not override it. The first 13 competitions below are enabled by default. All others start disabled, including lower-division domestic leagues and national-team competitions; a US national-team match does not bypass that setting. You can enable these competitions in **Settings → Leagues**. Disabled choices appear alphabetically there, but retain the positions below for scoring until the visitor changes the order.

| Rank | Competition | Rank | Competition |
| ---: | --- | ---: | --- |
| 1 | Liga MX | 18 | Women's friendly |
| 2 | Champions League | 19 | UEFA Nations League |
| 3 | Premier League | 20 | Carabao Cup |
| 4 | MLS | 21 | Conference League |
| 5 | La Liga | 22 | U.S. Open Cup |
| 6 | NWSL | 23 | Women's Champions League |
| 7 | Serie A | 24 | Women's Super League |
| 8 | Bundesliga | 25 | Coppa Italia |
| 9 | Europa League | 26 | DFB-Pokal |
| 10 | Concacaf Champions Cup | 27 | Scottish Premiership |
| 11 | FA Cup | 28 | Eredivisie |
| 12 | Ligue 1 | 29 | Saudi Pro League |
| 13 | Copa del Rey | 30 | Africa Cup of Nations |
| 14 | Championship (England) | 31 | Primeira Liga |
| 15 | USL Championship | 32 | Liga Profesional (Argentina) |
| 16 | Men's friendly | 33 | Brasileirão |
| 17 | Concacaf Nations League | 34 | USL League One |

The owner chose this starting preference for a US audience, informed by audience reporting reviewed on 12 October 2026. It is an editorial order, not a statistically established popularity table. Published figures cover different channels, time periods and kinds of matches; streaming audiences are often unavailable. In particular, playoff or final audiences cannot be compared directly with a regular-season average. Existing saved selections and orders take precedence; **Reset to defaults** restores the current configuration.

The sources behind that choice are retained here so an owner can revisit it:

- **Leading US television audiences:** [Nielsen's 2026 report, page 8, figure 1.4](https://www.nielsen.com/wp-content/uploads/sites/2/2026/05/913-World-Cup-2026-GRWM-d06.pdf) ranks Liga MX, Champions League, Premier League, NWSL and La Liga in that order for 2025, based on **each competition's ten largest US telecasts**, including English and Spanish coverage. This is neither total annual viewing nor a comprehensive streaming comparison. It supports the leading group, not every position in the default order.
- **Premier League and Liga MX:** [NBC's 2025–26 season report](https://www.nbcsports.com/pressbox/press-releases/nbc-sports-caps-2025-26-premier-league-season-highlighted-by-memorable-moments-fantastic-finishes-viewership-milestones) and [Liga MX reporting citing Sports Business Journal](https://www.goal.com/en/lists/liga-mx-remains-the-most-watched-soccer-league-on-u-s-television/blt2eb5436af5948721) provide further audience figures, with different periods and broadcaster coverage.
- **MLS and La Liga:** [MLS's playoff report](https://www.mlssoccer.com/news/mls-sees-strong-playoff-viewership-heading-into-mls-cup-presented-by-audi) and [La Liga's ESPN season report](https://www.laliga.com/en-US/news/laliga-scores-best-season-ever-in-us-on-espn-platforms). MLS reports audiences across its distribution without identifying them as US-only; those figures cannot establish its US position relative to La Liga or the other competitions. MLS's Apple streaming audience is also poorly represented by linear television rankings.
- **NWSL:** [The league's postseason report](https://www.nwslsoccer.com/news/2025-nwsl-championship-postseason-records) shows substantial television interest. Its championship and playoff audiences do not establish a regular-season ranking against men's leagues.

Lower positions rely more on editorial judgment about access and the teams involved because comparable US audience data is sparse. Nielsen measurement changes in 2025 also complicate comparisons with earlier seasons. Reorder leagues to suit your own interests rather than treating this default as authoritative.

## Retaining the evidence behind scores

Production refreshes save all three automatic AI dimensions, all four Outlook components, their raw inputs and the configuration used to combine them. Historical snapshots retain the formula used when they were recorded, including the former TV component and draw-based evenness; they are not rewritten to look like current predictions. Repeated snapshots preserve what was known before kickoff and what the feed reported later, including results and attendance when available. They cover all parsed schedule matches, independent of visitor filters. The saved default pick calculation uses the site's league order, not an individual's browser preferences.

This gives future analysis separate quantities to test: for example, whether popularity scores rank reported attendance sensibly, or whether the expected-gameplay rating relates to match outcomes. Attendance is not viewership, and a high-scoring game does not by itself demonstrate entertaining football. The feed does not supply television or streaming audience measurements; missing values stay missing. See [Match history and retrospective analysis](history.md) for the stored data, timestamps and comparison limits.

## What the golden dots mean

Golden dots show the pick score on a proportional six-dot scale: **round(score × 6 / 100)** to the nearest whole number, with exact halves rounded up. The displayed score is already rounded to one decimal. The same rule applies to component ratings.

| Pick score, rounded to one decimal | Dots |
| --- | ---: |
| 0 | 0, shown as `0/6` |
| 25 | 2 |
| 50 | 3 |
| 75 | 5 |
| 80.5 | 5 |
| 91.6 | 5 |
| 91.7 or more | 6 |

Changing league priority can change a match's dots. Hover over them for the rating breakdown, or click or tap to open it in a dialog. Keyboard users can focus the dots and press Enter or Space; screen readers receive the score and dot count. They are separate from the colored squares showing each team's recent results.

The **Rating breakdown** opened from the golden dots shows the numeric pick score, blend weights, the three automatic AI dimensions when available, and the four Outlook inputs. Components use a 0–100 display scale and the same proportional rounding, with distinct colors and text labels. Missing Outlook inputs show **Not available** rather than the midpoint assumption used in the total. Personal and shared imports have no component dimensions in their response format; the breakdown says they are unavailable and does not borrow automatic AI components. Match stories and their sources are available separately through **More**.

See the [development guide](development.md) for changing settings and running the checks, or return to the [project overview](../README.md).
