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

Paramount+ tests construct small responses following its observed US live/upcoming catalogue structure. Synthetic variants across the tests exercise wrong teams, times, countries, replay types, duplicate listings and unavailable sources.
