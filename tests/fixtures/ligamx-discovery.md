# Liga MX discovery captures

These reduced public responses were retrieved on October 15, 2026. They preserve the
source's event IDs, club identities, kickoff times and channel assertions. Advertising,
images, unrelated competitions and unused display fields were removed.

- `tudn-upcoming-landing.html`: `https://www.tudn.com/futbol/liga-mx`. Its US
  `ScoreCellQuery` supplies two forthcoming round-12 fixtures while the results page
  still serves round 11.
- `tudn-upcoming-2641272.html` through `tudn-upcoming-2641280.html`: the nine
  Liga MX match pages for October 16–18. Each capture retains its exact public
  `pageUri`. Only IDs 2641274, 2641277, 2641279 and 2641280 contain matching US
  authenticated live-stream and television channel metadata.
- `fox-scoreboard-2026-10-16.html` and `fox-scoreboard-2026-10-17.html`:
  `https://www.foxsports.com/soccer/liga-mx/scores?date=YYYY-MM-DD`. Club IDs and
  event IDs remain in FOX's original Nuxt reference-table format; `tvStation` is blank.
- `fox-tv-20261016.json` through `fox-tv-20261018.json`: the public client data used
  by `https://www.foxsports.com/event-schedule?date=YYYY-MM-DD`, from
  `https://api.foxsports.com/bifrost/v1/topevents/broadcast/eventschedule?date=YYYYMMDD`.
  The public client key is omitted. Each capture retains its selected date metadata.

The FOX guide includes primary FOX listings, third-party listings and ambiguous codes.
Tests only promote FOX-owned US channels. In particular, `FOXDEP` confirms Fox Deportes
without granting access through YouTube TV's main plan; `NBCU` does not identify a
specific channel. Tijuana's five-minute kickoff discrepancy must not change the ESPN
kickoff or allow a replay to be treated as a live fixture.
