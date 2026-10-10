# Using Soccer Outlook

[Open the site](https://caparomula.github.io/soccer-outlook/) · [Project home](../README.md)

Soccer Outlook helps you find upcoming matches available through your US streaming services and TV channels. Start with **Lineup** in the bar beneath the title.

## Choose what you can watch

In **Lineup**, tap a broadcaster or league to enable or disable it. Enabled and disabled choices have separate areas. You can also drag a pill between them; on a touchscreen, use its dotted handle.

Each group has **Select all** and **Clear all** buttons. Disabled choices are alphabetical. Drag enabled choices to change their order, or focus a pill and press **Alt + ↑** or **Alt + ↓**.

The two orders have different purposes:

- **Broadcasters:** when several selected services carry a match, the first one in your order is shown as the preferred way to watch.
- **Leagues:** higher leagues get a small preference bonus when the page chooses recommendations. The league strip follows this order too.

The schedule includes matches with a listed broadcaster or an established usual home on a selected service. Matches with no known route through your services are omitted. The **Elsewhere in the next 24 hours** section can show notable matches on other services, with listed broadcasters and your competition filters still applied.

### FOX One and TV-provider access

Choose **FOX One (paid subscription)** for a separate FOX One subscription. Having the app on an Apple TV, or signing in through a TV provider, does not establish access to every FOX channel.

For TV-provider access, select the channels you can actually watch through the TV service or app you use: **FOX**, **FS1**, **FS2**, **FOX Deportes** and **Big Ten Network**. They are independent choices: FS1 does not unlock a match listed on FS2. A listing that only says “FOX Sports networks” cannot establish access through an individual channel until the broadcaster confirms which channel carries it.

The generic **Cable or live-TV bundle** option leaves FOX channels to these separate choices. **Fubo** still includes its documented Pro-plan channels, and **Local channels (antenna)** includes broadcast FOX. See [FOX One's subscription information](https://www.fox.com/foxone/) and [Verizon's package-specific Fios lineups](https://www.verizon.com/home/fios-tv/channel-lineup/). A provider name alone is not enough to identify your package or diagnose a playback error.

Channel access describes the broadcast, not a guarantee that every device or app can play it. [FOX notes that some TV programming is unavailable in its apps because of licensing restrictions](https://help.fox.com/s/article/Why-don-t-I-see-a-program-in-the-FOX-Sports-App-that-s-airing-on-one-of-the-FOX-Sports-or-FOX-Entertainment-channels-I-receive-on-my-TV). A Fios lineup can include FS2 without establishing that a particular FS2 match will play in FOX One. If the app is your only way to watch and it denies that match, leave FS2 off until you confirm a usable route; the page cannot check your account's playback authorization.

The older, ambiguous **Fox One** selection is cleared when you first load the updated page. All other saved choices stay intact. Select the paid subscription or your available channels once; the page then remembers those choices normally.

### Quickly hide a league

The bar shows emblems for your enabled leagues. Tap one to hide that league's matches; tap it again to show them. A hidden league's emblem is dimmed and slashed, and a short notice offers **Undo**. The strip scrolls sideways if it does not fit.

Hiding keeps the league in the strip and remembers the choice for your next visit. Disabling it in **Lineup** removes it from the strip. If you enable it again, it starts visible.

With a keyboard, Tab enters the strip; arrow keys, Home and End move between leagues, and Enter or Space toggles the focused league. If ESPN has no emblem for a league, the strip shows a short name instead.

### Focus on one league

Press and hold a league emblem to show only that league, still using your selected broadcasters. Tap or hold the same emblem again to restore your previous selection. You can also choose **Undo** or press **Escape**. Pressing another emblem switches the solo view to that league.

With a keyboard, **Shift + Enter** or **Shift + Space** starts or ends the solo view. Your saved selections and hidden leagues stay intact, even if the solo league was previously hidden. Reloading the page or changing your league or broadcaster selections in **Lineup** ends the solo view; simply opening Lineup or reordering visible leagues does not.

### Saved preferences and defaults

Services, league selections, ordering and hidden leagues are saved in this browser's local storage. They do not synchronize between devices or browsers. Clearing site data removes them; private browsing may discard them when the session ends.

Until you reset, a league you have explicitly enabled or disabled keeps that choice when it returns to a later schedule; a league you have never changed uses its default.

**Reset to defaults** restores the default services and league selections, clears hidden leagues, and resets both orders.

The default services are HBO Max, Paramount+, ESPN Unlimited, Apple TV, USA Network, Prime Video, Netflix and Disney+. FOX access is off by default until you select a subscription or channel.

All tracked competitions are enabled by default except Women's friendly, USL Championship, USL League One, NWSL, Eredivisie, Ligue 1, Conference League and Europa League. US national-team matches are an exception to a competition's default-off setting. Explicitly disabling or hiding that competition hides those matches too. Leagues without fixtures in the fetched schedule may not appear in the panel.

## Read the schedule

The page shows **today plus three later days**, rather than a rolling 72-hour period. On Thursday, that means Thursday through Sunday. Upcoming day sections are open; today's earlier matches and yesterday's results are folded below them. Empty day sections are omitted.

A day starts at **4 am in your device's time zone**. A 12:30 am Saturday kickoff therefore belongs to Friday's evening. Until 4 am Saturday, the page still calls that period **Today** and labels it with Friday's date.

Kickoff times use your device's time zone and clock format. When your time zone differs from Eastern, the page also shows Eastern time underneath. **TBD** means ESPN has not confirmed a kickoff time. If kickoff passes without a score update, the page says it is awaiting the score; it does not assume the match has started.

The header count and **Your lineup** totals cover the displayed days. **Elsewhere** and the factual summary in the footer use the next 24 hours.

### Featured matches

**Live now** shows the highest-scoring match that passes your filters and that ESPN confirms is in progress. When none is live, **Next up** shows the earliest confirmed kickoff within the displayed days, with a countdown. Simultaneous kickoffs are separated by pick score.

**Top three** chooses the highest-scoring remaining matches across the displayed days, then presents them in kickoff order. It never repeats the top card's match. It can show one or two matches, or disappear when none qualify. If any remaining candidate has a current AI rating, only rated candidates compete for this section; otherwise it uses the Outlook score and league priority.

The current site uses AI ratings but no AI-written overview or match blurbs. A featured card without a blurb shows extra match facts and links instead. [How recommendations work](scoring.md) explains the scores and configurable alternatives.

### What the colors and symbols mean

| On the page | Meaning |
| --- | --- |
| One to five golden dots | The match's pick score: more dots mean greater recommended interest, not a predicted result. Hover for the exact score and its components. |
| Up to five small squares beside a team | Recent form: green is a win, grey a draw, red a loss. The tooltip shows the W/D/L sequence. |
| A record such as `6–2–1` | Season wins, draws and losses, in that order. |
| Colored broadcaster pill | That channel is available through one of your selected services. |
| Grey broadcaster pill | That channel is outside your selected services; another listed channel may still carry the match for you. |
| Dashed broadcaster pill / **usually** | The competition's established usual coverage, without a confirmed match listing yet. |
| Two colored strips at the bottom of a featured card | The home and away teams' colors. They are decorative, not another rating. |

League emblems identify competitions; badges beside team names identify the teams. Dark emblems and badges receive a contrast adjustment for the dark theme, while already bright images retain their colors.

### Match details

Hover over **Details** for a floating preview. Click, tap or press Enter to open a dialog. Close it with **Close**, Escape, or a click outside it. Neither view expands the schedule row.

Details can include season records, leading scorers, venue information and links, depending on what ESPN supplies. Featured cards without a blurb show these facts and links directly. **Add to calendar** opens a Google Calendar event draft for a confirmed kickoff; it is unavailable while the time is TBD. The event names the broadcast outlet rather than assuming which subscription you use. **League table** opens the page's standings. The schedule remains usable when an optional fact or image is missing.

When ESPN has no broadcaster listed, the build checks the usual broadcaster's own public schedule. A matching live broadcast replaces **usually** with the listed channel or service. **Details** links to that listing. If no matching listing can be confirmed, the usual coverage remains, and Details explains whether the schedule was checked without a match or could not be verified automatically. A missing listing is not proof that a match will be unavailable.

## Freshness and missing matches

The schedule normally rebuilds three times a day. Live scores are checked roughly once a minute while the tab is visible and a match is near kickoff or in progress. Returning to a tab also checks unfinished matches that kicked off within the past 30 hours, so a score seen earlier can catch up to the final result. If a request fails, the last displayed score remains until a later successful check. A live-score update does not fetch new fixtures or broadcaster assignments; those need a page rebuild and reload.

Open **Schedule and data details** in the footer for the build time, latest score check, coverage counts and any incomplete-data notice. The page warns when its build is more than 30 hours old.

If a match is missing, check its league in **Lineup** and in the league strip, check your selected services, and check whether it falls within the displayed days. A known league rights deal does not always establish coverage for every fixture. Broadcast assignments can change, so confirm the listing in the broadcaster's app before kickoff.

For maintainers, [Development and maintenance](development.md) explains the data sources, update schedule and coverage report.
