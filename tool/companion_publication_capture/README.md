# P0 published-output capture

This development-only tool observes the existing public website. It does not
implement the proposed companion parser/exporter or recompute domain decisions.
The checked-in JSON is a minimized historical evidence record, not a feed.

With Python 3.11+, Playwright and its Chromium/WebKit browsers installed:

```sh
python tool/companion_publication_capture/capture.py \
  --fetch --output tool/companion_publication_capture/captures/YYYY-MM-DD.json
```

`--fetch` makes exactly six bounded public GETs to the fixed Soccer Outlook
origin: HTML, schedule, context, coverage, automatic editorial and shared
editorial. It sends no cookies or credentials and follows no redirects.
The raw bytes, HTTP status, selected cache headers, byte counts, SHA-256 values
and browser outputs remain in ignored `work/companion-publication/`. A 404 for an
optional editorial resource is preserved rather than turned into empty content.

Omit `--fetch` to replay the existing hash-verified raw capture. A later public
fetch may see a new publication and therefore cannot reproduce these exact
historical bytes; retain the ignored raw directory when exact replay matters.

Each browser context is new, with no user browser data or preferences. The
evaluation clock is fixed to the captured HTML publication time. Requests for
the six captured paths are fulfilled from their original bytes and statuses;
every other browser request is aborted. No server, provider access, AI call,
account sign-in or external mutation is involved. `story.json` is an already
published resource, and its original age is preserved.

The matrix covers Chromium and WebKit in Eastern at 1280/390 pixels, and
Pacific at 390 pixels. A JavaScript-disabled Chromium pass records emitted
static fallback output. Browser outputs distinguish schedule membership,
folded groups, actual rendered row visibility and parked rows.
`nonhidden_fixture_ids` alone is not a visibility assertion for static HTML.

The compact report keeps all fixture/decision IDs, ten representative facts,
score/evidence samples, original editorial metadata, source diagnostics and
raw/output hashes. It omits complete HTML, full standings, image assets and
editorial prose. No new production code imports this tool.

Run the offline checks:

```sh
python -m unittest tests.test_companion_publication_capture -v
```

See [the P0 findings](../../docs/companion/p0-publication.md) for results and
limits. Browser platform builds, actual phone performance, companion v1
round trips and source authority are separate gates.
