# Public schedule response samples

These files retain a small set of actual ESPN response fields, captured on 9 October 2026. Each JSON file records its source URL. Tests read them offline; no API keys or live requests are needed.

- `espn-scoreboards.json` contains Arsenal–Leeds and América–Monterrey from the 10 October scoreboards. It preserves IDs, display names, kickoff/status objects, broadcast aliases, records, and numeric odds. Other events, editorial content, betting links, and tracking metadata were removed. The remaining values and shapes are unchanged.
- `espn-mex-teams.json` preserves the 18 Liga MX team IDs, display names and abbreviations used to check club-specific broadcast mappings.

These complement the synthetic fixtures rather than serving as a complete ESPN schema. When an upstream shape or identity changes, add a small representative sample and its capture date. Never copy credentials or unrelated response content into a fixture.

## Broadcaster listings

These reduced samples were captured on 10 October 2026. They preserve the event fields and response structure used by the parsers, while omitting unrelated page content. The provider modules contain the public source URLs; tests read the fixtures offline.

- `fox-liga-mx.html`: FOX's Liga MX scores page, with the Juárez–Tijuana event and its FS2 listing. Footer network names are retained to test that they cannot confirm coverage.
- `espn-watch-schedule.html`: ESPN's US Watch schedule, including the ESPN+ listing for El Paso–Orange County.
- `vix-liga-mx.html`: ViX's US sports catalogue, live-event links and Liga MX event records.
- `peacock-sports.html`: Peacock's sports calendar, with an actual Hull City–Everton event. The positive Chivas examples in the tests are synthetic; this sample verifies the public response structure, not Liga MX coverage.
- `apple-mls.json`: the US Apple TV MLS page's serialized event data for New England–Seattle.
- `fandango-bundesliga.xml`: Fandango's public Bundesliga catalogue, including its live-match event rows. It contains no anonymous session token.
- `bein-catalogue.html` and `bein-event.html`: beIN Sports Connect's upcoming-event links and Benfica–Vitória event page, including the broadcast start with its UTC offset.

Paramount+ tests combine a reduced current live response with synthetic responses following its observed US live/upcoming catalogue structure. Synthetic variants across the tests exercise wrong teams, times, countries, replay types, duplicate listings and unavailable sources.

The TUDN samples were captured on 12 October 2026. They retain reduced public Next.js component data, without player credentials or stream URLs:

- `tudn-results.html`: [TUDN's US Liga MX results](https://www.tudn.com/futbol/liga-mx/resultados), retaining the América–Monterrey and Querétaro–Atlante identities, dates and match-page links from the Apertura catalogue (`competitionKey: "199"`).
- `tudn-match.html`: [América–Monterrey's match page](https://www.tudn.com/futbol/liga-mx/america-vs-monterrey-liga-mx-ap-2026-10-10), retaining the exact kickoff, event ID, US scope and authenticated live-event channel metadata for TUDN and Univision. This confirms that specific simulcast; it does not imply that every ViX fixture airs on those channels.

`fox-fixtures-20261016.html` was captured on 15 October 2026 from [FOX's dated Liga MX scoreboard](https://www.foxsports.com/soccer/liga-mx/scores?date=2026-10-16). It preserves the selected date/segment and three upcoming fixture identities, including FOX event/team IDs and kickoff times. The Nuxt reference table was reduced and reindexed without changing those values; broadcast fields, advertising and unrelated cards were removed. Fixture-comparison tests use this sample only to check schedule identity and time, never to establish a US viewing entitlement.

The October 15 repairs add these reduced real-response samples:

- `apple-mls-schedule-20261015.json`: the current MLS `gameScheduleLockup` shelf.
- `paramount-live-20261015.json`: live soccer entries with separate event URLs and generic show links.
- `vix-future-20261015.json`: a US premium future event whose player link is not active yet.
- `espn-watch-20261017.html`: source-linked Eastern-date Watch schedule, with pregame starts and Spanish simulcasts.

The JSON captures include source/date provenance. [Liga MX discovery captures](ligamx-discovery.md) describes the additional TUDN and FOX fixtures. These preserve public schedule facts, not subscriber credentials or playback tokens.
