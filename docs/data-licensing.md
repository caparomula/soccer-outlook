# Data licensing options

Soccer Outlook needs permission for the way it obtains and presents data, not just a working endpoint. This report identifies official permission-request routes and paid suppliers worth evaluating for the website and proposed companion app. It separates licensed display from an openly downloadable feed and permanent public history.

Official pages were checked on October 20, 2026. This is procurement research, not a legal clearance or a complete audit of every source. No companies were contacted, accounts opened or subscriptions purchased. The owner is open to higher pricing when needed; coverage and usable rights take priority over the lowest subscription price.

**Scope update, October 23, 2026:** the companion is currently being built for personal use. This research remains a reference for future source or distribution decisions; inquiries, quotes and permission responses are not prerequisites for its local development.

## Recommended shortlist

When supplier evaluation resumes, start with these inquiries in parallel:

1. **Gracenote On Sports:** the closest documented fit to the whole discovery problem. It combines sports and video metadata, linear/streaming availability and viewing links. Ask for an end-to-end quote and a demonstration of US package distinctions.
2. **Sportmonks:** the strongest affordable lead with explicit permission to distribute and store API data, plus fixture-specific TV stations. First verify Liga MX coverage, US station completeness and the exact scope for our public JSON/history and native apps.
3. **SportsDataIO and Sportradar:** competing commercial quotes for fixture/live data with documented broadcast/channel fields. Sportradar has a particularly clear published license for display on named properties. Neither has yet demonstrated the precise package mappings Soccer Outlook needs.
4. **TV Media:** a separately licensed US TV-schedule layer if the sports supplier's broadcast information is inadequate. Its website/app display grant is useful, but its standard retention restrictions conflict with the current permanent public archive.

Request two prices from each suitable vendor: **display in our own website/apps through an approved delivery system**, and **that display plus an unauthenticated, publicly downloadable JSON feed and public historical archive**. Public accessibility does not grant downstream recipients unrestricted reuse or sublicensing rights. The second option can require a materially different agreement. The [inquiry drafts](data-permission-requests.md) make that distinction explicit.

No checked product establishes the complete combination of fixture identity, US airing, replay status, named streaming package and individual playback entitlement. Exact package selection must remain a separately tested requirement. A vendor's total league count is not a substitute for a field-level coverage sample.

## The rights and data we need

| Concern | What must be established |
| --- | --- |
| Fixture and score data | Current Liga MX, MLS, NWSL and configured European/international competitions; stable identities, corrections, TBD/postponed status and live/final scores. |
| US broadcast data | A particular fixture on a particular US network, with language, airing time, simulcasts, streaming availability and live/repeat distinction. |
| Package facts | YouTube TV main versus Spanish Plus, individual FOX channels versus FOX One, local affiliates and standalone-app entitlements. A network listing alone cannot answer these. |
| Public display | The website and both native apps, including worldwide accessibility of US-focused listings and the existing optional donation link. |
| Collection and delivery | Approved endpoints, server/direct-device access, credentials, polling limits and serving data to our own clients. |
| Public redistribution | The proposed unauthenticated JSON feed and currently downloadable schedule/history data, distinct from display inside an approved product. |
| Storage and termination | Temporary/offline caching, private analysis, public history, deletion requirements and what may remain after cancellation. |
| AI and derived outputs | Sending selected inputs to a hosted model for inference; publishing commentary, ratings and historical analyses; no assumption that a display license covers these uses. |
| Artwork | Separate permission for team/league/network emblems and player images; an image URL in a paid response is not necessarily a license. |

The existing [history branch](history.md#storage-and-access) stores source-derived inputs and results indefinitely. Ordinary Git deletion does not withdraw existing clones or copies. A license requiring deletion cannot be reconciled with that architecture merely by hiding a link. Confirm archival rights before adding a supplier's data, or redesign storage with the owner's agreement.

## Paid options and verified terms

Prices below are public subscription rates observed during this research, not quotes for all required leagues, app properties or redistribution rights. Confirm taxes, currency, minimum term, endpoint access, usage allowances and any custom permission in writing.

### Sportmonks

**Fit:** fixtures, live scores, standings and a documented [TV Stations by Fixture ID endpoint](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/tv-stations/get-tv-stations-by-fixture-id). Station data can include country relationships. The [coverage page](https://www.sportmonks.com/football-api/coverage/) confirms MLS and major European leagues; Liga MX was not established from the fetched listing and needs an explicit current answer. Do not infer absence or availability from the league-count headline.

**Price:** [monthly plans](https://www.sportmonks.com/football-api/plans-pricing/) show Starter **€29 for five leagues**, Growth **€99 for 30**, and Pro **€249 for 120**, excluding VAT. Lower advertised monthly equivalents require annual billing. Enterprise is quoted. A 30-league subscription would not by itself cover all 34 competitions currently configured here.

**Rights:** the [terms](https://www.sportmonks.com/terms-of-service/) say “distribution, transfer, and storage of data provided by our services is allowed,” while resale requires consent. They permit revenue-generating applications built using the data. Pricing is per domain, and logos/photos require separate rights. The page separately restricts reuse of website materials; the agreement should expressly cover the licensed API data and our intended outputs.

**Ask before selecting:** permission for one website plus iOS/Android, a free public machine-readable feed, indefinite public history, post-termination retention and AI inference. Obtain US samples for Liga MX, Spanish simulcasts and streaming-only matches. The TV-station endpoint does not establish package carriage, replay labels or household entitlement.

**Contact:** [Contact & Support → Sales](https://www.sportmonks.com/contact-support/), **sales@sportmonks.com**.

### Gracenote

**Fit:** [On Sports](https://gracenote.com/products/on-sports/) combines sports/video metadata and IDs, schedules, and links to live events or recordings across linear and streaming services. [Streaming and Linear Availability](https://gracenote.com/products/streaming-and-linear-availability/) describes viewing options, deep links and national/local lineups across cable, broadcast, satellite and virtual MVPD services. [Sports Data](https://gracenote.com/products/sports-data/) adds scores, standings, statistics and shared IDs relating sporting events to television programming.

**Price:** custom quote; no current public production price was verified.

**Rights:** these are commercial discovery products, but no public production agreement checked here establishes our public-feed, history or AI permissions. The old [OnConnect trial terms](https://developer.tmsapi.com/page/API_Terms_of_Use) permit internal evaluation/testing, not production publication. Those are legacy trial terms, not a description of the negotiated commercial license.

**Ask before selecting:** contracted coverage for our leagues and US outlets; exact YouTube TV packages and streaming-app relationships; replay versus real live event; links that open a match rather than a service homepage; permitted caching, public distribution, archive and AI use. The [legacy developer gateway](https://developer.tmsapi.com/) directs current product documentation to [the new portal](https://devportal.gracenote.com/), so confirm the offered product/version rather than implementing an old example blindly.

**Contact:** the **Sales Inquiry / Get in touch** form on the product pages or [Gracenote's contact form](https://gracenote.com/#contact-us-form).

### TV Media

**Fit:** [TV Listings API](https://www.tvmedia.ca/digital-tv-listing-products/tv-listing-api/) provides US/Canadian lineups and sports searches by team, league or event. [Documentation](https://developer.tvmedia.ca/docs) includes live/repeat metadata, airing times and station/channel identity. Listings are advertised as updated every two hours; that is a guide refresh cadence, not a live-score service.

**Price:** [plans](https://developer.tvmedia.ca/pricing-plans) list **US$100/month Intro** with 1,000 calls, **US$300/month Standard** with 5,000, and **Pro from US$750/month** with 20,000. Sports/all-endpoint access is advertised for Standard on the product page. Confirm the exact plan and future horizon: checked pages differ in wording about seven versus fourteen days. Cache shared queries on the server if permitted; do not assume these allowances support per-visitor polling.

**Rights:** [terms](https://developer.tvmedia.ca/tos) expressly allow display “on and through your Service,” including websites/apps, subject to approval and conditions. Standard terms prohibit copying/storing/archiving except a limited, secure performance cache of at most 30 days, unless written permission is given. They also restrict use outside the approved service and unauthorized competing services. A TV guide's suitability needs their approval, not our interpretation of a subscription tier. A separate written agreement can override conflicting default terms.

**Ask before selecting:** public JSON and historical archive exceptions, AI use, streaming-only event coverage, named package tiers, event-to-fixture identifiers, artwork and attribution. A postal-code lineup is not proof of a viewer's subscription. Their personal XMLTV product is not a substitute for a public-app license.

**Contact:** [TV Media contact](https://www.tvmedia.ca/contact-us/), **info@tvmedia.ca**, or [developer contact](https://developer.tvmedia.ca/contact).

### Sportradar

**Fit:** the [Soccer API](https://developer.sportradar.com/soccer/reference/soccer-api-overview) covers schedules, scores, standings and statistics. Some match endpoints include channel availability depending on coverage. MLS appears in the documented coverage; obtain the current Liga MX and US-channel matrix rather than assuming every field exists for every competition.

**Price:** the [Sports Data API product](https://sportradar.com/media-tech/data-content/sports-data-api/) offers a custom quote and a 30-day trial. A trial is for evaluation, not evidence of permission to publish.

**Rights:** the [US master terms](https://developer.sportradar.com/sportradar-updates/page/terms-and-conditions), displayed as updated August 5, 2026, grant use/distribution/display for sports information on named **Properties** (§2.2). The order form must identify the website and mobile applications (§2.3). Restrictions on making products available to third parties mean this is not a blanket public-API license. Archiving language does not clearly authorize our public Git history. Third-party marks are excluded (§2.9), and a powered-by attribution is required (§2.12).

**Ask before selecting:** public feed versus approved app delivery, public/private archive and termination rights, AI processing/derived ratings, channel completeness and package mapping. Have the actual order form and license reviewed; a product demo does not settle these questions.

**Contact:** [Sportradar sales](https://sportradar.com/contact/).

### SportsDataIO

**Fit:** its [soccer product](https://sportsdata.io/soccer-api) explicitly lists MLS and Liga MX. [Coverage documentation](https://sportsdata.io/developers/coverages/soccer) includes broadcast information with game-day data. This is a useful lead for a combined schedule/score/channel supply, but the current contracted coverage and US-language/package detail require samples.

**Price:** production quote; no verified public price for this scope. The separate personal-use Discovery Lab is not established as a public-site license.

**Rights:** the [general terms](https://sportsdata.io/terms-of-service) provide a limited, non-transferable, non-sublicensable license. The commercial agreement must identify our permitted public uses. The [soccer data dictionary](https://sportsdata.io/developers/data-dictionary/soccer) explicitly says certain Wikipedia logo/wordmark URLs are not licensed for public/commercial use; some headshots require a separate license.

**Ask before selecting:** display rights for all properties, raw delivery/history, AI inference and actual US broadcast coverage. Do not treat an included image URL as cleared artwork.

**Contact:** [Contact form](https://sportsdata.io/contact-us), selecting **Talk to sales about licensing and pricing**.

### Other comparisons

| Supplier | Verified offering and public price | Permission/fit limitation | Official route |
| --- | --- | --- | --- |
| **Stats Perform / Opta** | [Opta Data](https://www.statsperform.com/products/opta-data/) supplies rich live/historical sports data and predictions; custom quote. | Strong enrichment option. Exact Liga MX/MLS and US broadcast/package coverage was not verified. Its [website terms](https://www.statsperform.com/terms-and-conditions/) are not the paid feed agreement; obtain a product-specific license. | [Product/pricing contact](https://www.statsperform.com/contact/) |
| **football-data.org** | [Pricing](https://www.football-data.org/pricing): €12/month live scores for 12 competitions; €49 Standard/30, €99 Advanced/50, €199 Pro/100. VAT may apply. [Coverage](https://www.football-data.org/coverage) places MLS in tiers 2–4 and Liga MX in 3–4; confirm the plan mapping. | [Registration terms](https://www.football-data.org/client/register) require attribution and prohibit continued reference to obtained data on a customer's site/service after cancellation (§9.1). Permanent public history needs an exception. No US broadcast endpoint verified. | **daniel@football-data.org**, published on [pricing](https://www.football-data.org/pricing) and [About](https://www.football-data.org/about) |
| **API-Football / API-Sports** | [Pricing](https://www.api-football.com/pricing): $19/month Pro/7,500 requests daily; $29 Ultra/75,000; $39 Mega/150,000, with currency to confirm. [Coverage](https://www.api-football.com/coverage) lists Liga MX, MLS and major Europe. | [Terms](https://www.api-football.com/terms) explicitly say they do not provide a license for use/publication; users must obtain required permissions from the relevant authorities. Payment alone therefore does not resolve this project's rights question. Raw resale and artwork are separately restricted. | [Official dashboard support](https://dashboard.api-football.com/) |
| **Goalserve** | [Football prices](https://www.goalserve.com/en/sport-data-feeds/football-api/prices): $150/month live scores, $150 fixtures/results, $300 full stats without odds, $550 full soccer; confirm currency and a firm quote. | [Public terms](https://www.goalserve.com/en/terms-and-conditions) did not establish a clear API-data redistribution grant. Liga MX, US broadcast fields and archive/AI rights require confirmation. | [Contact form](https://www.goalserve.com/en/contact-us), **support@goalserve.com** |

An inexpensive API can be technically useful without supplying the permission we are trying to obtain. API-Football is the clearest example in this comparison; its stated position should be resolved before treating it as a replacement for restrictive direct-source access.

## Asking the existing sources for permission

No dedicated public schedule-data license was located for these sources in this targeted review. The following are verified official permission or referral routes. A support contact, video-licensing team or corporate distribution desk is not represented as having granted metadata rights.

| Source | Official route | What to request / limitation |
| --- | --- | --- |
| **ESPN / Watch ESPN** | [ESPN Fan Support request](https://support.espn.com/hc/en-us/requests/new?ticket_form_id=360000010431). Disney's [ESPN/ABC Sports licensing FAQ](https://www.disneystudiolicensing.com/who-do-i-contact-regarding-licensing-materials-from-espn-or-abc-sports/) names ESPN Content Rights Management and **footage@espn.com**. | Ask for digital schedule/API data licensing or legal referral. The named rights team's published remit is footage/library material; do not ask for a video license or assume it controls fixture data. |
| **FOX Sports** | [FOX support](https://help.fox.com/s/contactsupport); the [sports terms](https://www.foxsports.com/terms-of-use) direct terms questions to its help site. | Ask who can authorize automated schedule/scoreboard access and third-party display. This is a support/terms escalation, not a verified data-sales desk. |
| **TUDN / ViX** | [TelevisaUnivision corporate contact](https://corporate.televisaunivision.com/contact/) lists **DistributionMarketing@univision.net**. [ViX support request](https://ayuda.vix.com/hc/en-us/requests/new) is a fallback. | Ask the distribution team to identify metadata/API licensing for TUDN and ViX; television distribution and schedule-data rights are different. |
| **Paramount+** | [Paramount+ support](https://help.paramountplus.com/s/contact-us). [Terms §4.2](https://www.viacomcbs.legal/us/en/cbsi/terms-of-use) invite content-licensing inquiries. | The terms' corporate contact link returned 404; the support page returned a JavaScript shell, so form operation was not verified. Request a content/data licensing referral, not playback assistance. |
| **Peacock** | Official [contact instructions](https://www.peacocktv.com/help/article/contact-us-in-customer-service) lead to [Get in Touch](https://www.peacocktv.com/help/get-in-touch). | Signed-in email/chat support can route to Peacock/NBCUniversal legal or digital partnerships. No schedule-specific licensing desk verified. |
| **Fandango at Home** | [Streaming support](https://streamsupport.fandango.com/s/), linked from the current service page. | Ask specifically for sports-schedule metadata permission. This is the streaming service's portal, not Fandango movie-ticket support. |
| **Apple TV** | [Apple Rights and Permissions request form](https://www.apple.com/legal/contact/rights-permissions.html). | A real rights-request route, primarily for trademarks/copyrighted material. Ask explicitly about automated Apple TV/MLS metadata access and referral to Apple Media Services if needed. Artwork approval would not approve extraction. |
| **beIN Sports / beIN Connect US** | The official [US contact article](https://www.beinsports.com/en-us/soccer/articles/contact-us-2023-06-08) lists **beINUSprogramming@bein.com** for programming/schedule inquiries and **generalinfo@bein.com**. | Ask about a schedule feed and the authorized licensing team. Programming support's title alone does not prove authority to license redistribution. |

The [ESPN-linked Disney terms](https://disneytermsofuse.com/english/) and [FOX Sports terms](https://www.foxsports.com/terms-of-use) restrict automated extraction/reuse. Request written authorization for the actual endpoints and uses, not a generic acknowledgement that the site is public. A press desk, DMCA notice address or advertising sales form is not a suitable substitute for rights approval. The [request drafts](data-permission-requests.md) include referral language and a complete project description.

League artwork is a separate workstream. The [artwork inventory](../web/logos/README.md) records official origins, not grants of permission. Ask the respective league/mark owner for usage guidelines or use a permitted text/neutral presentation. A data supplier that disclaims logo rights cannot clear them by supplying the file URL.

## What changes in the companion plan

The current personal-use roadmap begins with technical feasibility and local/private feed development. Source permissions are no longer a P0 gate. Keep data adapters replaceable; a future licensed replacement may require server-held credentials and a different transport. Revisit the delivery, retention and distribution choices below before expanding the companion to public use.

The existing GitHub publisher can still be useful if the chosen agreement permits the feed we publish. If a contract permits approved website/app display but prohibits a general public feed, credentials cannot simply be embedded in JavaScript or the mobile binary. Evaluate a backend and appropriate access controls, or obtain a broader feed license. A hidden URL is not access control, and moving collection to a server does not independently create permission.

Keep private analytical storage and a public downloadable archive as separate choices. Our own code/ratings, licensed source inputs and AI-derived outputs need distinct retention rules based on the agreement. Do not assume derived outputs are unrestricted merely because we calculated them.

The manual AI workflow exports facts for a visitor to paste into a service of their choice. That is distinct from our own hosted API inference: the visitor's service may have different retention or training settings. Request permission for that export explicitly; if the license excludes it, revise the prompt-export feature for licensed fields instead of promising controls we cannot enforce.

Nothing in this research changes running collectors, deletes existing history or purchases a feed. Before a supplier switch, test the sample matrix in the inquiry draft, identify source-ID migration needs and have the actual permission/license reviewed. Record operational requirements without committing confidential agreements to this public repository.

## When pursuing permissions or quotes

Send the prepared requests only after the owner chooses recipients and supplies the sender/contracting details. Prioritize **Gracenote, Sportmonks, SportsDataIO and Sportradar**; add **TV Media** if the combined offers cannot demonstrate US airing/package detail. Direct-source referrals can proceed alongside those quotes, especially ESPN, FOX, Apple and beIN, without making app development depend on an unanswered consumer-support ticket.

Compare offers on rights and tested coverage first, then total cost: licensed competitions, website/app properties, setup fees, update volume, archival/AI permissions, required infrastructure and attribution. There is no need to subscribe to every supplier; the goal is one adequate source or a clearly joined sports-plus-TV pair.

## About this document

- Research date: October 20, 2026. Prices and terms can change; the executed agreement controls.
- Evidence: official product, pricing, documentation, terms and contact pages linked beside each finding. Unverified features and broken/dynamic contact pages are identified explicitly.
- Related: [inquiry drafts](data-permission-requests.md), [current source behavior](schedule-data.md), [historical storage](history.md), [companion plan](companion/implementation-plan.md).
- Have qualified counsel review the selected agreement's scope and upstream rights; this comparison does not determine enforceability or establish legal clearance.
