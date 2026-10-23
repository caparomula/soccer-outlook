# Decisions outside the first P2 extraction

The subsequent [viewer eligibility family](viewer-eligibility.md) now owns the
league/service projection described below. The subsequent
[route validity family](route-validity.md) owns qualified-route expiry. The
[fixture-window family](fixture-windows.md) owns focus and schedule buckets over
supplied normalized state and receipt facts. The
[status family](espn-status.md) now decodes bounded source status/result facts.
Full source qualification, source freshness and final surface candidate/ranking rules remain
outside those families.

This source-inspection record was written on October 20, 2026 before the first
consumer migration. It prevents a route/calendar extraction from being described
as complete viewer eligibility. Only [WATCH-ROUTES-1](watch-routes.md) and
[TIME-1](sports-day.md) are the first rule owners. The items below remain in their
existing production owners until a later family gets independent examples and
an explicit consumer migration. The family links above identify subsequent
migrations; the original inspected behavior below remains historical context.

## Evidence validity and viewing destinations

Source adapters and `rights.toml` already distinguish country, exact packages,
channel membership and replay evidence. The shared core must eventually enforce
their published validity constraints between refreshes. A retained listing's
absolute `expires_at` must not move forward when downloaded or reevaluated.
Usual coverage has a rights period and cannot become a dated match listing.
Missing or failed lookup evidence cannot erase an independent positive listing;
a relevant explicit identity/country/package/replay rejection is different from
absence. A rejection concerns the associated route/evidence identity, not every
other route to the same fixture.

The current validated feed rejects eligible/excluded plan overlap. A future
domain mapper must preserve all route/evidence/destination identity bindings,
including feed language and direct-service preference metadata needed by
WATCH-ROUTES-1. A generic provider logo, an AI response, a service homepage or a
similarly named match is never new route evidence.

`watch_links.py` qualifies event destinations independently of selected filters.
The browser and static renderer expose only destinations compatible with that
network and selected service. Usual coverage does not create a match destination.
An exact event URL can remain usable outside live play if its validity says so;
a supplied YouTube TV linear-stream link has `live_only` and appears only when
source status is actually in progress. Kickoff passing or a TV guide describing
"live TV" is insufficient. This first extraction shares the provider
intersection; URL qualification and link validity remain separately owned.

## League choice, temporary hiding and favorites

Current `web/app.js` behavior:

1. An explicit saved on/off choice wins over a league's published default.
2. A followed league can be paused without being unfollowed. The pause survives
   visits. Changing its follow choice clears that pause.
3. A temporary solo view shows one eligible league and hides the others while
   preserving saved choices. The UI creates solo selections from its eligible
   strip and clears solo when that league loses eligible fixtures.
4. The strip includes followed leagues with a selected-service match in any
   displayed bucket, including folded history. Pausing keeps its strip control
   available; disabling it in Settings removes that control.
5. No user favorites feature currently participates in eligibility. Publisher
   featured/marquee-team facts affect base scoring; they do not override disabled
   leagues, missing routes or unavailable packages.

Independent later-family examples should include an explicit disabled league
returning after a fixture-free week; a default change after an explicit choice;
a paused league with only recent results; solo preserving an underlying pause;
solo losing its last matching fixture; and a favored team with no selected route.
Malformed solo state must not become a new method of following an otherwise
disabled league. These are preference and application-state decisions, not part
of source route qualification.

## Schedule, carousel and picks are different projections

All three use the same selected-service and league filter. They do not have
identical time/candidate rules. `bucketOf` first excludes sports-day indices
outside minus three through plus three. Completed fixtures stay in the schedule
and recent carousel but do not compete for Top picks. Source state and estimated
time placement are distinct: a scheduled fixture near/past kickoff can occupy
the live bucket while its card still says status pending.

| Fixture after shared service/league filtering | Schedule | Carousel | Pick candidate before ranking/cohort exclusion |
| --- | --- | --- | --- |
| Confirmed future kickoff within day 0 through 3 | Its day bucket | Upcoming | Yes |
| In-progress fixture in live bucket | Live bucket | Live opening candidates | Initially yes; every live opening-cohort fixture is later excluded |
| Finished today | Folded Earlier today | Recent | No |
| Finished or otherwise retained previous-day fixture, at most three days back | Folded prior day | Recent | No |
| Time TBD today, provider placeholder already passed | Today if the existing placeholder gives index 0 | Upcoming remains discoverable | Current implementation omits it unless the placeholder is future or bucket is live |
| Any fixture outside the minus-three/plus-three sports-day window | Omitted | Omitted | No |
| Finished fixture with a replay currently airing | Recent according to fixture day | Recent | No; an airing does not change fixture state |

Current opening selection takes all eligible actual-live fixtures; otherwise it
takes every fixture at the earliest confirmed kickoff. If only TBD fixtures
remain it takes one as a browse fallback, without claiming a confirmed kickoff.
Top picks exclude that entire opening cohort independently of the slide being
browsed. Ranking, automatic-only rated filtering, active imported-rating
exceptions and final kickoff display ordering remain later-family decisions.

The browser's `personalLookup` has its own forward/live bucket eligibility gate
for manual AI prompts. It is not identical to the pick-candidate gate for TBD
fixtures. Do not replace it incidentally when moving route preference.

## Differences and defects requiring separate evidence

These were found by reading the named production paths. They are not assertions
that the first route/calendar migration has fixed them.

| Finding | Evidence and implication | Required next treatment |
| --- | --- | --- |
| Two clocks affect live placement | Legacy `inFocus` and `bucketOf` accepted `now`, but recent `_seen` tests used `Date.now()`; `#at-...` froze only the first clock | TIME-WINDOW-1 now supplies one evaluation instant and rejects future receipts as recent; receipt age is explicitly distinct from provider observation freshness |
| TBD time has a synthetic instant in the website | Browser `_k` is parsed even when `_tv` is false; `carouselUpcoming` includes TBD day buckets while `upcomingIn` relies on `_k > now` or live bucket | Preserve the current distinction during first extraction; define a date-only rule using independently justified source-date zone before changing it |
| A confirmed-time flag is not source progress | `bucketOf` can place scheduled/pending fixtures in `live` after kickoff for the estimated window | Keep source status separate from group placement and prove no invented clock, score or live-only destination |
| Retained-route expiry was not reevaluated by legacy route intersection | The old `evaluateRow` only saw `o.v` and usual metadata, while the companion route published absolute expiry | Addressed by WATCH-VALIDITY-1 with exact-expiry and browser lifecycle evidence; full source qualification remains upstream |
| Static fallback and dynamic grouping differ | `build_page` initially groups by Eastern calendar dates; the browser uses viewer-local sports days | A calendar-helper extraction does not silently redesign the no-JavaScript fallback; default-view/window projection requires its own migration evidence |
| Exceptional status decoding was incomplete | Legacy `build.called_off` recognized post-state canceled/cancelled/postponed labels, while the feed has more explicit states | STATUS-ESPN-1 now distinguishes exceptional/unknown states and score applicability through independent examples; source freshness and live correction ordering remain separate work |

Useful worked boundaries for that later review are kickoff plus exactly 125
minutes versus one millisecond later; a recent live receipt at exactly ten
minutes; an in-progress game crossing local 04:00; a stale in-progress observation
older than the estimate; a postponed placeholder 0–0; and a known-date/TBD game
whose placeholder lies before the evaluation instant. Expected answers should
come from the [fixture-window rule](fixture-windows.md) for migrated placement,
and from [STATUS-ESPN-1](espn-status.md) for bounded decoding. Source freshness
remains separate work. Intentional
departures from legacy behavior remain identified separately.
