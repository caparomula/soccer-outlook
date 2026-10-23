# P2 first increment: shared routes and calendar

This is the retained first-increment checkpoint. The subsequent
[viewer-eligibility increment](p2-eligibility-evidence.md) records the next
consumer migration and current validation; the hashes and counts below refer
to the first increment's source state.

October 20, 2026. P2 is in progress. This increment moves qualified-route
preference/provider ordering and sports-day boundaries into one Dart owner,
used by the publisher and browser. It does not complete the remaining domain
migration or implement the mobile app. Distribution remains gated.

## Implemented owners and consumers

The [rule catalog](rules/README.md) defines `WATCH-ROUTES-1` and `TIME-1`, with
independent worked examples written before consumer replacement. Core inputs
and results are immutable. `buildOutlook` supplies a partial route/calendar
snapshot with explicit evaluation time and named zone; it does not yet supply
league visibility, buckets, ratings, picks or editorial decisions.

The publisher's `evaluate`, selected-provider ordering and `owner_pill_service`
now call the shared route owner through [outlook_runtime.py](../../outlook_runtime.py).
Identical normalized requests are cached; a persistent native CLI avoids
starting a process per fixture. The browser evaluates fixture routes in batches
and applies their results to schedule rows and provider disclosures. Its
`sportsDayStart`, `dayIndex` and `dayDate` helpers call the shared calendar.
`story_state.window_end` uses the same shifted boundary; the caller still owns
its window length and AI eligibility. The migrated Python/JavaScript arithmetic
and preference-ranking implementations were removed.

The adapter checks source and artifact hashes before use. Its framed transport
has bounded request/response sizes and one deadline covering nonblocking writes
and reads. Tests exercise a child that stops reading, trickled output, oversized
responses, malformed/missing artifacts and cache isolation. Injected timeout
mutations are rejected. Python microseconds immediately before and after 04:00
retain the correct calendar boundary when mapped to bridge milliseconds.

## Runtime packaging and recovery

[The build tool](../../tool/build_outlook_runtime.py) compiles the pinned Dart
owner into native and browser artifacts. The browser loads one content-addressed
script with SHA-256 integrity, installed beside the page before HTML is written.
A failed request or corrupt script preserves the static schedule and displays a
reload notice. There is no independent rule implementation as a fallback.
Full workflows retain the prior page's referenced core script for cached HTML;
lightweight workflows preserve published assets and require a compatible runtime
revision. Fragment hosts must serve the adjacent script as well.

Inlining the complete runtime initially grew the real captured page above the
existing 3,300,000-byte budget and triggered logo trimming. The final external
script packaging produces a **2,668,713-byte page** with all **448 original logo
rules** and no trimming. The compiled core itself is
1,563,950 bytes, or **191,264 gzip bytes against a 204,800-byte limit**. Only
13,536 gzip bytes remain for future rule additions, so every next family must
remeasure the complete artifact.

The full pinned timezone database is necessary because browser Intl can return
valid aliases omitted by the default database. UTC, Calcutta, Kiev, Katmandu,
GMT and fixed-offset named-zone cases are covered. Chromium's GMT result
`+00:00` is normalized by the browser adapter to its equivalent UTC identifier;
unsupported zones still reject. The core also returns an actual date/boundary
when a historical jurisdiction skips a calendar date.

CI preparation is wired into full/lightweight website builds, browser/unit
checks and both existing provider-comparison workflows. The companion check
workflow includes rule-example paths and the current P2 verifier. These are
local workflow edits; no remote workflow was executed.

## Independent and migration evidence

The [cross-language and generated-page report](evidence/p2-routes-calendar-2026-10-20.json)
records current source and runtime hashes:

- Fifteen independent route cases, eleven shifted-calendar cases and seventeen
  existing containing-day cases pass through VM, compiled CLI and both browsers.
- The frozen 360-fixture source capture supplies 1,440 route comparisons under
  four lineups. These preserve the original Python answers; they are separate
  from independently authored expected results.
- Seventy-two actual generated-page cases cover nine zones and four saved
  lineups across Chromium and WebKit. They check preferred routes, provider
  order, source-field preservation and exactly one verified core-asset request.
- Four failed/corrupt-asset checks preserve static fixture identities and visible
  owner rows. Corrupt code does not execute, and the fallback raises no script
  exception.
- The compiled core passes strict CSP. The generated website retains its
  existing inline presentation scripts/styles; the report does not claim a new
  strict CSP for the entire site.

Host timing samples cover twenty starts and 360-route batches per engine.
Playwright routing disables HTTP caching, and these measurements do not certify
cached-device startup, a complete future snapshot or native performance.

The [offline full/lightweight report](evidence/p2-build-replay-2026-10-20.json)
uses retained raw responses at their original build clock. It makes no source
requests and does not renew observation ages. It compares all 360 fixture
identities and route selections, required feed artifact bytes, source ages,
script installation and logo coverage, then checks the new full context through
the actual lightweight path and independent Dart reader.

## Regression checks

The [validation record](evidence/p2-validation-2026-10-20.json) binds the final
checks to source and log hashes. All **697 Python checks**, **58 core Dart
checks**, **111 Chromium checks**, **39 WebKit smoke checks** and **22 schedule
refresh checks** pass. The core and bridge analyzers are clean. The regular
browser fixtures inline the verified core in its own script element; the
separate 72 generated-page and four failure cases above exercise the production
external-script packaging and integrity check.

Integration review found and corrected an ordering defect: a replacement row
could receive its newer live overlay before its route batch existed. The old
regression could pass because the rejected update left the old scored row in
place. The strengthened check changes the broadcaster and requires installation
of the new schedule timestamp and route while preserving the newer live score.
It failed before repair and passes after deferring disclosure rendering until
the replacement's route decisions are ready.

Additional reviewed corrections include a separator needed by the initial inline
Dart bootstrap, preparation for the two comparison workflows, core analyzer/test
steps and runtime/rule CI triggers, and a total subprocess deadline rather than a
response-only timer. Fragment-output assertions now account for the adjacent
runtime script while still excluding host-owned discovery and shared-AI files.
The final packaging avoids concatenating the Dart bootstrap with presentation
scripts. Existing P1 WebKit focus/provider fixes and their Apple-host offline
coverage limitation remain in the [P1 record](p1-continuation-evidence.md).

## Remaining work and local state

The next P2 families are league/surface eligibility, route-evidence expiry,
shared status decoding/live freshness, ratings, carousel/pick selection and
editorial semantics. Feed-to-domain mapping still needs explicit outlet-language
metadata for exact route tie-breaking. The first owner consumes qualified
publisher facts; it does not infer entitlement from unknown channels or remove
source-evidence validation from Python. Two contradictory unknown-route examples
are identified as defensive behavior changes, not unchanged legacy parity.

P3 still owns durable cache/preferences, coordinated HTTP refresh and live
correction ordering. Physical/minimum-OS tests and release performance remain
later gates. P1's public feed URL and source permission remain gated.

The pre-P2 state is retained in ignored `work/p2-start/`: 142 changed/new files,
archive, tracked diff and hash manifest. All prior work is preserved. These
changes remain local and uncommitted; no pushes, deployments, AI generation,
remote workflow runs or app distribution were performed.
