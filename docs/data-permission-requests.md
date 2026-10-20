# Data permission and quotation requests

These drafts explain Soccer Outlook accurately and separate ordinary app display from broader data distribution. Use them with the official routes in [Data licensing research](data-licensing.md). They have not been sent, and no provider has approved the project.

Replace the bracketed contact details before sending. Do not include API keys, account passwords, private AI responses or subscriber information. A support reply should identify the person authorized to grant the requested rights; a helpful technical answer is not necessarily a license.

## Project description to attach

> Soccer Outlook is an independent soccer discovery website at https://caparomula.github.io/soccer-outlook/, with source code at https://github.com/caparomula/soccer-outlook. It helps people find upcoming matches on their selected US television and streaming services. The site is free, has an optional donation link and has no advertising. An iOS/Android companion is being planned. The website is publicly accessible worldwide; the broadcast information describes US availability.
>
> We display fixtures, kickoff times, status and scores, team records/form, standings, venue, available player statistics, network listings and qualified viewing links. We calculate our own recommendation scores and may supply selected facts to an external AI service to produce match commentary and ratings. We do not show match video, bypass subscriptions or claim to verify an individual viewer's account.
>
> There is also a manual workflow in which a visitor copies a prompt containing selected facts into a chat service of their choice. Please distinguish permission for that user export from our own API inference; we cannot assume that every visitor's AI account has our API's retention or training settings.
>
> The current implementation collects public source data in GitHub Actions, publishes schedule facts in rendered pages and browser-update files, and stores selected match inputs/results in a public GitHub history branch. Full builds run three times daily, lighter schedule updates twice hourly, and open browsers check relevant live scoreboards approximately once a minute. Actual request volume depends on league/date scope and concurrent visitors; we can provide measured volume before finalizing an agreement and adjust acquisition to agreed limits.
>
> The proposed companion would use a versioned schedule feed, cached last-good data and a foreground live-score path. The architecture can change to meet an agreed license; permission for public machine-readable delivery is a separate question from permission to display information in our app.

If a recipient needs an audience estimate, supply measured traffic and a clearly labelled forecast. Do not invent user counts to fit a pricing tier. The current code license is zlib; that does not purport to license any third-party data or marks.

## Request permission from an existing source

**Subject:** Soccer Outlook — permission for schedule data use and app display

> Hello,
>
> I maintain Soccer Outlook, an independent, free soccer schedule and viewing guide. The attached project description explains the current website and planned companion app.
>
> I would like written permission to use [specific source, endpoints/pages and fields] for this project. If your team does not handle data permissions, would you please refer this request to the appropriate data-licensing or business-development contact? I am requesting schedule/statistical data permission, not video or footage rights.
>
> Please distinguish which of these uses you can authorize, and any applicable fee or separate agreement:
>
> 1. Automated access using an approved API or another approved method, including reasonable polling for live status and scores.
> 2. Public display of selected factual information in our website and iOS/Android apps, with source attribution and links you require.
> 3. Server-side processing and delivery to our own clients; please specify whether direct client requests are permitted or whether credentials and requests must remain on a server.
> 4. Temporary and offline caching, including the permitted duration and deletion requirements.
> 5. Retention of historical inputs/results for retrospective analysis, distinguishing private storage from our existing public GitHub archive and its downloadable files.
> 6. Distribution of normalized facts in a public, machine-readable JSON feed for the companion. If that requires a different license, please quote it separately from public app display through a restricted backend.
> 7. Supplying selected factual inputs to a hosted AI service for inference rather than training our own model, and publishing the resulting original commentary and ratings. Separately, please confirm whether visitors may export those facts through the manual prompt workflow. Identify restrictions on AI providers, their training/retention settings, subcontractors and derived outputs.
> 8. Team/league emblems or provider marks, only if you can separately authorize them; a data-only permission would still be useful.
>
> Please also state territorial, competition, season, audience, rate-limit, attribution and commercial-use restrictions. The website is free with optional donations, publicly accessible worldwide, and focused on US broadcast availability. Are the requested rights yours to grant, or are approvals from a league, data supplier or other party also required?
>
> We would appreciate the applicable license or written authorization, including its duration, permitted endpoints and what happens to cached data, published history and derived ratings when it ends. Please identify whether it can address the existing website/archive as well as future use.
>
> Thank you,
> [Name / contracting party]
> [Contact information]

Ask for the narrowest useful grant first. A company may be able to authorize factual display without granting logos, public raw data or perpetual archival rights. Record those distinctions instead of treating one answer as permission for everything.

## Request a licensed data quotation

**Subject:** Soccer Outlook — soccer and US broadcast data licensing quote

> Hello,
>
> I am evaluating a licensed data source for Soccer Outlook, a free public soccer viewing guide and planned iOS/Android companion. The attached description covers our present architecture and intended uses. We are willing to consider an appropriate commercial license rather than relying on a consumer subscription or evaluation account.
>
> Our initial competition priorities are Liga MX, MLS, NWSL, UEFA Champions League, Premier League, La Liga, Serie A, Bundesliga, Europa League, Concacaf Champions Cup, FA Cup, Ligue 1 and Copa del Rey. Additional configured competitions can follow. Please return a competition-by-field coverage matrix rather than a general count of leagues.
>
> We need stable fixture/team IDs, confirmed or TBD kickoff, postponements/cancellations, live/final status and scores, standings and recent form. Venue, leading players, attendance and market inputs would be useful; please identify add-on products and separately priced fields.
>
> US broadcast discovery is particularly important. Can your product identify specific fixture airings, network, country, language, live/replay status, simulcasts and streaming availability? Does it distinguish service packages such as YouTube TV main versus Spanish Plus, local affiliates, standalone subscriptions and TV-provider app access? Which of those are verified data fields versus customer-maintained mappings?
>
> Please quote the following options separately:
>
> **A. Public app display:** our website and native apps display selected results, with an approved backend, agreed temporary/offline cache and private analytical history. No general-purpose public raw-data API is required under this option.
>
> **B. Public data delivery and archive:** the same experience, plus an openly downloadable normalized JSON feed and public historical records in GitHub. Please explicitly identify whether this is redistribution, syndication or another product tier, and whether immutable Git history is permitted.
>
> Under each option, please confirm AI-inference/derived-output rights, permitted territories, whether our free/donation-supported status affects the license, required attribution, logo rights, minimum term, setup costs, taxes, usage limits, overages, data-retention limits and post-termination obligations. Do your rights cover our proposed end-user use, or must we obtain separate permissions from underlying rights holders?
>
> Before purchase, could you provide a licensed evaluation sample for the cases below, with its evaluation-use limits and an example production license? Please also describe correction latency, source provenance, historical availability, service levels and fixture-ID migration support.
>
> Thank you,
> [Name / contracting party]
> [Contact information]

## Ask for samples that exercise the actual problems

Use a current, mutually agreed sample period. The following are required capabilities, not claims that a named vendor already supplies them:

| Sample | What it must establish |
| --- | --- |
| Liga MX match on TUDN and another match on FOX-owned networks | Match identity, explicit US network and airing time; no generic “FOX Sports” guess. |
| Universo airing alongside a separate Paramount+ route | Network, service and package remain distinct; one does not imply access to the other. |
| YouTube TV main versus Spanish Plus | Actual package distinction, or an explicit acknowledgement that we must maintain it ourselves. |
| A live linear channel carrying a replay | Broadcast-on-now is distinct from the fixture being in progress. |
| A match with two languages or simulcasts | Multiple valid routes are retained rather than selecting only one channel. |
| An MLS streaming-only match | A linear EPG's absence is not interpreted as no coverage. |
| Late-night kickoff, DST transition, TBD and postponement | UTC, source time zone, event date and status retain their meanings. |
| A source outage or late correction | Missing/unknown, stale and definitively unavailable are distinguishable; corrections have provenance. |
| Women's competition and a less prominent configured league | The promised fields and live coverage apply beyond the flagship men's leagues. |

For a sports feed plus a separate TV feed, request an explicit event-ID bridge or enough licensed identifiers to join safely. Team-name similarity alone is insufficient. Evaluate provider-package metadata separately from the person's actual subscription entitlement.

## Record the result

For each source or vendor, record the legal entity granting permission, scope, approved product/domain/app IDs, covered fields and competitions, contract/version/date, attribution, rate limits, caching and archival limits, AI restrictions, logo rights, renewal and termination rules, and the evidence location. Keep signed agreements and private correspondence outside the public repository; commit only an approved summary of operational requirements.

Do not purchase, accept terms on the owner's behalf, send these messages or switch production sources merely because these drafts exist. Those are separate actions. This research prepares concrete requests; it does not establish permission.

## About this document

- Drafted October 20, 2026 for the current Soccer Outlook website and proposed companion.
- Related: [source and vendor research](data-licensing.md), [schedule behavior](schedule-data.md), [public history](history.md), [companion architecture](companion/architecture.md).
