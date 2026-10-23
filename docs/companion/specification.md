# Companion app specification

The companion app helps a person answer two questions: “What can I watch now?” and “Which upcoming matches are worth my time?” This document describes the experience to build and the observable behavior that must be tested. It is intended for developers, designers and anyone reviewing a release.

This is a proposed specification; the companion app has not been implemented.

Start with the [planning guide](README.md) for the document map. The [architecture](architecture.md) assigns responsibilities, the [data contract](data-contract.md) defines the information the app consumes, and the [implementation plan](implementation-plan.md) sequences the work.

## Purpose and first release

Build a Flutter app for iPhone, iPad, Android phones and Android tablets, while retaining the existing website. It should feel familiar to someone using Soccer Outlook today, with the same availability decisions and recommendations for the same facts, preferences and evaluation time.

The first release includes local preferences, live and upcoming matches, recent results, Top picks, optional league headlines and match stories, match/team/league details, viewing links, and a usable saved schedule when the network is unavailable. People can import recommendations from a chat AI using the existing copy-and-paste approach.

The app does not play video, verify subscriptions, require an account, or automatically connect to an AI subscription. Buying an app subscription is not part of this plan. Shared AI publication remains a maintainer function on the website. Cross-device synchronization, notifications, home-screen widgets and additional AI authentication integrations are later work.

## Requirements and acceptance

Requirement identifiers are stable. When behavior changes, update its requirement and acceptance examples together; do not reuse an identifier for a different purpose. Implementation owners and test locations belong in the [engineering guide](engineering.md), rather than being invented here before code exists.

### Lineup and league preferences

**PREF-1 — Start from published defaults.** A first launch uses the published service selections, league selections and orders. Their current source is `OWNER` and `LEAGUES` in [build.py](../../build.py), with channel and package facts in [rights.toml](../../rights.toml). The app must not maintain another handwritten copy of those lists. A later default change must not overwrite a choice the user already made.

**PREF-2 — Make selection and priority understandable.** Settings has Lineup, Leagues and AI tabs. Enabled and disabled items occupy separate areas. Enabled items can be reordered; disabled items are alphabetical. Dragging is one way to move or reorder an item, with equivalent tap and accessibility actions. Service order chooses the preferred eligible provider; league order supplies the preference contribution to recommendations.

Each selection group has Select all and Clear all. Reset to defaults restores both filter groups and both orders and clears temporary hidden leagues, without deleting an AI response or changing its text-display preference. Switching Settings tabs or dismissing and reopening Settings must not accidentally submit or erase an AI draft.

**PREF-3 — Preserve choices across changing schedules.** League choices and priority survive periods with no fixtures. The quick league strip includes only enabled leagues with matches on selected services inside the displayed schedule, including folded results. Hiding a league in the strip leaves its control available while it has matching fixtures; disabling it in Settings removes it. Hidden state must be recognizable through shape or a labelled state as well as color.

A long press can temporarily show one league. Tapping that league again, an accessible restore action or Escape restores the previous view. Solo mode does not replace saved preferences and ends if its league no longer has eligible matches. Merely switching Settings tabs does not end it.

**PREF-4 — Save locally and report persistence failures.** Accepted preference changes and personal AI responses survive an app restart. Saving a multi-field change is atomic: another screen must not observe half of a reset or reorder operation. If storage fails, the app must not claim the change was saved. Preferences are device-local in the first release, and the UI explains that deleting app data removes them.

| Acceptance example | Expected result |
| --- | --- |
| A disabled league disappears from this week's feed and returns next week | It remains disabled. |
| The user changes a default-enabled league to disabled, then a new default revision arrives | The user's choice wins. |
| All services are cleared | The app explains that no services are selected and offers Settings; it does not invent a viewing route. |
| A user with assistive technology cannot drag pills | Enable, disable and reorder remain possible through labelled actions. |
| The app closes after a successful Reset to defaults | The next launch restores the complete reset, including both orders. |

### Availability and viewing routes

**WATCH-1 — Apply the same filters everywhere.** The main schedule, opening match carousel, Top picks and match-linked editorial content use the same availability and competition decisions. A match passes the service filter when a published, currently valid route belongs to a selected service: either a confirmed US listing or an explicitly qualified usual-rights arrangement. The latter remains visibly labelled **usually**; it must not be presented as a confirmed listing.

This preserves the site's current behavior. “Show confirmed listings only” would be a separate product change, not an incidental consequence of building the app. Matches with no known route through selected services are omitted from the main schedule. Missing evidence means unknown, not proof of no broadcast.

**WATCH-2 — Preserve country, package and replay distinctions.** A provider name, installed app or generic network family does not establish access. Package/add-on mappings, explicit channel membership, country restrictions, replay rejection, evidence validity and source identity must survive publication and filtering. AI output cannot create a fixture or establish viewing rights.

The app may explain a saved route, but cannot verify a person's account, local affiliate, blackout or a specific device's playback entitlement. Source diagnostics must distinguish unsupported checks, failed checks, incomplete catalogues and successful checks with no match.

**WATCH-3 — Show the network and explain its providers.** Match cards show network pills once. Tapping a pill opens the eligible selected providers and any supported viewing links. Provider order determines the preferred route. A match link must be distinguished from a service homepage; a homepage must not be labelled as opening the match. A manually supplied YouTube TV match URL is available only while the fixture is live under the link's validity policy.

| Acceptance example | Expected result |
| --- | --- |
| Only YouTube TV main is selected and Universo is the sole valid listing | The match fails the service filter; adding the relevant Spanish package can make it eligible. |
| The same match has a separate Paramount+ listing and Paramount+ is selected | It can qualify through that independent route. |
| FS1 is selected and the listing says FS2 | FS1 does not grant access. |
| A television guide calls a replay “live TV” | It is not promoted to a live fixture or a confirmed live-match airing. |
| A saved listing passes its original retention deadline during an outage | Repeated refresh attempts do not extend its validity. |
| A broadcaster lookup finds no match but another source has a valid listing | The positive listing is not erased merely by the absence. |

### Schedule and time

**TIME-1 — Use local sports days.** Display today and the next three sports days, with the previous three available for browsing. A sports day starts at 4 am in the device's time zone. Calculate day boundaries as local calendar operations, including daylight-saving transitions; do not assume every day lasts 24 hours. Kickoff is an absolute instant displayed with the device's clock convention. Eastern time is available as the secondary reference, as on the website.

The published feed must cover this window for supported device time zones. If an old or incomplete snapshot does not cover the requested days, say that coverage is unavailable rather than asserting there are no matches.

**TIME-2 — Keep status separate from the clock and TV programming.** Confirmed kickoff, time TBD, awaiting a status update, in progress, completed, postponed and cancelled are distinguishable where supplied. Passing kickoff alone does not confirm play has started. A stale in-progress observation must not indefinitely claim that a match is live now. Scores retain their observation time; the app must not extrapolate a score or match clock.

The approved response timeout is three minutes, expiring at exactly 180 seconds
after the accepted fixture response was received. Missing, future or expired
receipts cannot support a current-live claim or live-only destination. Keep the
reported score and clock with a **Last known** qualifier. Source observation
and provider update times remain unknown when the provider does not establish
them; response recency does not establish their age. See
[LIVE-FRESHNESS-1](rules/live-response-freshness.md).

**TIME-3 — Make browsing stable.** Upcoming day sections are open and empty sections are omitted. Earlier results and previous days are initially folded. The current schedule heading remains visible while its section scrolls, displaced by the following heading. A data update must not reset a person's reading position or move focus to the top.

| Acceptance example | Expected result |
| --- | --- |
| A Saturday fixture kicks off at 12:30 am local time | It belongs to Friday's sports day. |
| The device crosses 4 am while the app remains open | Friendly day labels and window membership update without a restart. |
| The device changes time zone or crosses a daylight-saving boundary | Kickoff instants remain unchanged; local labels and day grouping are recomputed. |
| Kickoff has passed but no source confirms progress | The card says the status is pending rather than inventing a live score. |
| A fixture has a known date but time TBD | It remains discoverable without being chosen as the next confirmed kickoff. |

### Home screen and recommendations

**HOME-1 — Keep the page order.** The home screen contains the opening match carousel, Top picks, optional league headlines, then the daily schedule. Sections with no eligible content are omitted, apart from a concise explanation when the main schedule is empty. A generic overview paragraph or placeholder headline must not occupy the space intended for matches.

**HOME-2 — Lead with what is live or next.** The opening carousel starts with the highest-ranked eligible live match, or the earliest confirmed upcoming kickoff when none is live. If only results remain, it starts with the latest result. Other eligible fixtures are browsable to either side, including at most three previous sports days. Friendly group labels travel with their groups: Live now, This morning, This afternoon, This evening, Tonight, Tomorrow, then weekdays. Pending times remain identifiable.

The carousel has no automatic rotation or visible manual navigation controls. Swiping, keyboard navigation and assistive-technology scroll actions provide equivalent access. When the user browses away from the starting match, preserve their selected fixture through updates if it remains eligible, except when it newly finishes: then return to the current opening match. Removing or filtering out the selection also returns there. Deliberately browsing an already-finished result remains possible. Returning to the starting match resumes following live/next changes.

**HOME-3 — Select Top picks by interest; display them by kickoff.** Choose up to three other upcoming matches within the displayed forward window, using the current [recommendation rules](../scoring.md). Exclude all live fixtures; if none is live, exclude all fixtures at the earliest confirmed kickoff. Those exclusions are independent of which carousel slide the person is reading.

Preserve the existing distinction between automatic-only recommendations and an active personal/shared response: automatic-only selection uses rated candidates when any are available, while an active personal/shared response allows other eligible matches to compete using their available fallback scores. A missing score is not zero. Fewer candidates produce fewer cards, and no candidates hide the section.

The heading names the first and last friendly match days, using one day when they agree. Each card centers its day between the rating and league emblem. No fixed “must watch” threshold is added.

**HOME-4 — Explain ratings without clutter.** Golden dots use the site's proportional six-dot rounding rule. Tapping, focusing or hovering over the dots opens the rating breakdown separately from More. Explain the total, blend weights, available components and provenance. Missing inputs or unavailable imported components must not be portrayed as measured values or borrowed from another rating source. Popularity is an estimate of audience appeal, not measured viewership.

| Acceptance example | Expected result |
| --- | --- |
| Two eligible games are live | The strongest starts the carousel; neither appears in Top picks. |
| Only eligible recent results remain | The latest result is the opening card. |
| A browsed live fixture newly finishes | The carousel returns to its current opening match; the result remains browsable. |
| Nothing is live and three games share the next kickoff | All three are excluded from Top picks, even after the user swipes to a later fixture. |
| The three highest-ranked picks fall on different days | Selection follows rating; display follows kickoff, and the heading describes the covered days. |
| A displayed score is 80.5 | It produces five of six dots; rounding, not ceiling, is used. |
| A personal import provides only a total score | Its breakdown does not display automatic AI dimensions as if they came from that import. |

### Editorial content and personal AI

**AI-1 — Make text optional and provenance clear.** AI headlines and match blurbs start off. One saved switch controls both; switching text off does not discard responses or disable AI ratings. Identify personal, shared and automatic content so people can understand why devices differ.

For each match, valid personal recommendations take precedence over shared recommendations, then automatic recommendations. Preserve the existing headline-list behavior: a current-format personal response with an intentionally empty list can intentionally suppress headlines. Restore site recommendations removes the personal override without deleting preferences or published content.

**AI-2 — Show useful upcoming-match journalism.** Headlines naturally name their competition, carry its emblem and show their supporting paragraph in a carousel. Prefer the nearest eligible rolling 24-hour window, then rank by editorial interest and league priority under [the scoring policy](../scoring.md). Routine filler, generic football news and completed-match recaps do not qualify. When no headline qualifies, omit the entire area.

A match-linked headline remains eligible only while all referenced fixtures pass filters, retain their referenced kickoff and remain upcoming or live. A sourced calendar notice can qualify without a fixture when its league is enabled and visible, at least one service is selected, and its verified event period is upcoming or ongoing. An empty schedule alone is not evidence of an international break.

Match-specific stories and sources appear in More, not below card bodies or in a second schedule introduction. Story generation is allowed to return no useful text; the app does not manufacture an intelligent-sounding fallback.

**AI-3 — Preserve the manual workflow and original expiry.** Settings can generate and copy a prompt from eligible schedule facts, leagues, priority and personal preferences. It asks for a formatted JSON response, then validates the pasted response against its original request. Rejection leaves the last accepted response intact and explains the actionable error.

New prompts use the existing version 2 response format described in [Personal AI imports](../development.md#personal-ai-imports). A version 1 response cannot satisfy a new version 2 prompt. Supported legacy shared responses can still supply their valid ratings and match stories until expiry; their overview paragraphs must not become invented headlines. A native install does not automatically gain access to responses stored in a browser.

Accepted personal responses persist locally. Original prompt time determines their lifetime; applying or publishing again does not renew it. A shorter headline expiry still applies. Imported influence remains adjustable without another AI request. The user must explicitly copy a prompt to another service; the app does not silently transmit preferences to an AI provider.

| Acceptance example | Expected result |
| --- | --- |
| AI text has never been enabled | No headline or match story is displayed, although valid AI ratings can influence picks. |
| A headline references one eligible game and one finished game | That headline is omitted. |
| There are no matches today but a qualifying headline concerns tomorrow | Tomorrow's headline can appear; no “This morning” story is invented. |
| A response is pasted again after its original expiry | It does not receive another 24 hours. |
| A personal response has an empty current-format headline list | Shared headlines do not silently override that deliberate choice. |
| AI text is enabled but the response contains no useful story | The match remains usable with its schedule facts and rating. |

### Cards, details and accessibility

**UI-1 — Share information across card roles.** Opening, Top picks and schedule cards use one match presentation model, with role-specific layout. They expose the same underlying scores, team records, five-game form, venue, network evidence and supplementary facts where available. Schedule rows stack teams; wider layouts place supplemental information and right-aligned network pills beside them. Narrow layouts move content below only when measured constraints require it.

Time sits above the league emblem in schedule rows. League names need not repeat the emblem in every card; an accessible name and league-details action remain available. Team marks use transparent backgrounds and selective contrast treatment; already bright marks retain their color. Missing or failed images have legible text alternatives.

**UI-2 — Keep details in sheets or popups.** A prominent More action at the bottom right opens match details without expanding the schedule row. Pointer hover can preview the same content. More includes available match story, sources, team summaries, venue, broadcast evidence, ESPN match link, calendar action and league-table navigation. Rating components stay in the separate rating popup.

Team names or badges open team vitals, and league emblems open league vitals. Missing facts are omitted or explicitly unavailable. The calendar action is available only for a confirmed kickoff and creates a draft requiring the user's own confirmation.

**UI-3 — Lay out content using actual constraints.** Preserve readable type and touch targets as names, networks, font scale, orientation and window size vary. Measure content rather than assuming a phone model or estimating text width from character count. Cards in a carousel share the space required by their reachable contents at the current width and font scale, so swiping does not move the following section. Preserve natural scrolling when content cannot fit.

**UI-4 — Support more than touch.** All actions have meaningful semantics, focus order and visible focus where relevant. Screen readers announce fixture identity, status, time, score, rating and selection state. Color is not the sole signal for selection, form or freshness. Sheets restore focus on close, background updates preserve focus and reading position, and reduced-motion settings are respected.

Acceptance includes long club names, multiple networks, no badge, narrow portrait and tablet split view, large accessibility text, dark mode contrast, VoiceOver/TalkBack, keyboard-only use and an update while a sheet is open. The same fixture's facts must agree across all roles.

### Freshness, offline operation and privacy

**DATA-1 — Open from saved data and refresh honestly.** After a successful fetch, a later launch can display the last accepted schedule without a network connection. Distinguish its observation age from the time the app last checked for updates. Mark stale or incomplete data clearly without replacing the saved schedule with a blank screen.

Refresh on foreground entry and manual request. Regular checks follow the published source policy and stop when the app is not visible. A failed, partial or incompatible response must not destroy the last good snapshot. A valid complete empty response is different from a failed request and can replace it.

**DATA-2 — Treat live updates as a separate capability.** The website currently obtains near-minute scores through direct foreground ESPN checks, while published fixture and broadcast updates normally arrive every half hour. Loading the published feed more often cannot make its contents newer. The architecture must supply and validate the foreground live-score path before describing the app as a live-score companion. If that path is unavailable, retain the last score with its observation time and explain the limitation.

No first-release promise depends on continuous background polling, guaranteed notification delivery or an operating system keeping the app alive. Returning from suspension rechecks current data; late replies from a previous refresh must not roll back newer scores or preferences.

**DATA-3 — Keep private data local.** Lineup, ordering, preference text and personal AI responses stay on the device unless the person deliberately copies or shares them. No individual watching history, analytics account or viewing tracker is required. Public match metrics can continue to be archived by the existing build pipeline; that is separate from measuring a person's behavior.

Exclude downloaded caches and private prompt/response records from automatic cloud backup by default. Document any platform backup behavior for ordinary preferences; it must not be presented as app-provided cross-device synchronization.

| Acceptance example | Expected result |
| --- | --- |
| First launch has no connection and no cache | A useful unavailable state offers Retry; it does not pretend the schedule is empty. |
| A populated app starts offline | Saved data appears with its age, preferences intact and expired AI content excluded. |
| A fresh HTTP response contains old source observations | The display does not reset the underlying evidence's age. |
| A newer live score arrives before an older schedule refresh completes | The old refresh does not roll the score backward. |
| The device is offline past the published schedule window | The app distinguishes missing coverage from no scheduled games. |
| A complete validated update has no fixtures | It can replace the old schedule and show the appropriate empty state. |

## Release acceptance

A release is ready only when the implementation plan's gates pass and the requirements above have named automated or manual checks. Use identical published facts, saved preferences, time zone and clock to compare the website and companion decisions; visual differences are expected, contradictory eligibility or ranking is not.

Release testing must include both mobile platforms on real devices, foreground/background transitions, offline restart, failed persistence, an upgrade retaining preferences, malformed publication rejection, expiry boundaries, source outages, large text and assistive technology. Native signing, distribution accounts, supported OS versions and package versions are recorded before release; this document does not claim those arrangements already exist.

User-facing help ships with the behavior it describes. Terms such as **usually**, **last checked**, **observed**, and **Your AI** need short explanations at the point where they help a viewing decision. Internal architecture terminology belongs in developer documents, not the home screen.

## About this document

- Status: proposed first-release product specification.
- Reviewed: 20 October 2026.
- Existing behavior baseline: Soccer Outlook `cb0475e1fbcb4fffe7cf69827c0fd998dd90b29a`.
- Current references: [user guide](../user-guide.md), [scoring](../scoring.md), [schedule coverage](../schedule-data.md), [browser application](../../web/app.js), [live transport](../../web/live.js) and [schedule transport](../../web/schedule.js).
- Architecture, implementation and engineering decisions are maintained in the linked companion documents. Improvements to freshness and native accessibility here are proposed acceptance requirements, not claims about features already shipped.
