# VIEWER-ELIGIBILITY-1: League choices and fixture visibility

Project the viewer's saved league choices, temporary strip view and already
selected viewing routes into one set of league and fixture decisions. A pause
or solo view does not rewrite saved choices. A league without fixtures remains
part of the preference catalog and can qualify for calendar-only editorial.

This is a bounded extraction of PREF-1/PREF-3 and the shared league/service gate
in WATCH-1 and AI-2 of [the specification](../specification.md). It does not own
route qualification, status, schedule-window classification, favorite-team
scoring, ranking, headline expiry or final editorial selection. P2 remains
incomplete after this family.

## Decision inputs and outputs

The version 1 bridge operation is `viewer_eligibility`. Its request contains
`cases`, and each case has:

| Field | Meaning |
| --- | --- |
| `id` | Case identity, echoed in the result |
| `leagues` | Ordered catalog of `{id, default_followed}`; include known leagues without fixtures |
| `preferences.choices` | Explicit boolean choice by league ID; absence follows that league's current default |
| `preferences.paused` | IDs hidden in the ordinary strip view |
| `preferences.solo_id` | Requested temporary solo league, or `null` |
| `has_selected_services` | Whether the resolved selected-service set is nonempty |
| `fixtures` | Ordered `{id, league_id, has_route, in_window}` records |

The caller supplies the effective league priority order; the rule does not
calculate scores or reconstruct saved ordering. `has_route` is the result of the
existing selected-route owner, not an inference from a provider or team name.
`in_window` means [TIME-WINDOW-1](fixture-windows.md) includes the fixture in the
displayed schedule, including folded history. The rule accepts that decision;
it does not inspect kickoff, TBD status, live state, replay age or a clock.

Each result has `id`, `active_solo_id`, nullable `solo_reset_reason`,
`strip_league_ids`, `leagues` and `fixtures`. Each league result is
`{id, followed, hidden_by_strip, visible, calendar_editorial_eligible}`. Each
fixture result is `{id, eligible, schedule_eligible}`. League and fixture results
retain their corresponding input orders; strip IDs retain catalog order.

The executable [examples](eligibility-examples.json) use the existing bridge
envelope: `{version: 1, operation: "viewer_eligibility", cases: [...]}` returns
`{version: 1, results: [...]}`. Catalog and fixture IDs must be unique nonempty
strings; flags and explicit choices must be booleans. Invalid wire shapes are
boundary errors. Unknown preference IDs are accepted but ignored by this
projection, without deleting them from caller-owned storage. Unknown fixture
league IDs produce false fixture decisions.

## Decision order

1. A known league is `followed` according to its explicit choice when present;
   otherwise use `default_followed`. Explicit false is not an absent choice.
2. A fixture supplies strip evidence only when its league is known and followed,
   `has_selected_services`, `has_route` and `in_window` are all true. A league
   with any such fixture enters `strip_league_ids`. Pauses and solo do not remove
   strip controls, so hidden leagues remain reachable.
3. Validate the requested solo. `null` means no solo and no reset reason. An
   unknown ID resets with `unknown`; a known unfollowed league resets with
   `disabled`; a followed league outside the strip resets with
   `no_eligible_fixture`. This precedence is fixed. A valid solo becomes
   `active_solo_id`. An invalid solo returns the ordinary paused view.
4. With an active solo, `hidden_by_strip` is true for every other league and
   false for its target. Without solo, use membership in `preferences.paused`.
   A valid solo temporarily shows even a paused target. Its saved pause remains
   intact and applies again when solo ends.
5. A league is `visible` when followed and not hidden by the strip. This is
   independent of selected services and fixture presence.
6. `calendar_editorial_eligible` is `visible && has_selected_services`. No
   matching fixture is required. This is only the league gate; verified event
   periods, source support and headline selection remain with their owners.
7. A fixture is `eligible` when its known league is visible,
   `has_selected_services` is true and `has_route` is true.
   `schedule_eligible` additionally requires `in_window`.

The current `buildOutlook` snapshot composes this projection with route selection,
calendar labels and TIME-WINDOW-1. It derives `has_route` from its route decision
and `in_window` from its fixture-window decision over explicit normalized state,
kickoff, confirmation, receipt, zone and evaluation time. The standalone
eligibility operation still accepts those two decisions as inputs; it does not
repeat either family's rules.

## Independently authored examples

The example expectations were written before core or consumer replacement and
are literal decisions, not outputs captured from either implementation. The
synthetic catalog normally contains default-on `a`, default-off `b`, and
default-on `c`, which has no fixtures.

| Example | Expected decision |
| --- | --- |
| Published defaults | `a` and `c` are visible; only fixture-bearing `a` enters the strip |
| Explicit choices survive an empty week | Explicit `a: false, b: true` applies both with no fixtures and when fixtures return |
| Defaults change | Explicit `a: true, b: false` overrides opposite new defaults |
| Paused leagues | Paused `a` stays in the strip; paused fixture-free `c` loses calendar editorial eligibility |
| Solo a paused league | `a` becomes visible alone, while its underlying pause remains unchanged; ending solo restores the paused example |
| Disabled or unknown solo | Reset with the specific reason and evaluate ordinary pauses |
| Solo without a route | Reset; a featured team cannot supply missing access |
| Solo outside the window | Reset; the fixture may pass the base filter while failing schedule inclusion |
| Empty services with a contradictory route | No strip evidence, fixture eligibility or calendar editorial eligibility; followed league visibility remains intact |
| Unknown fixture league | Deny the fixture even when its route and window flags are true |
| Unknown saved IDs | Ignore their projection without rewriting saved preferences |
| All leagues disabled | No visible leagues, strip controls or eligible fixtures |
| Folded history | An included historical fixture still supplies a strip control |
| Supplied league order | Return strip controls in that order, without changing membership |

No favorite input exists in this contract. Existing publisher featured-team
facts affect scoring elsewhere and do not bypass a disabled league or absent
selected route.

## Consumer boundaries

The browser's `passes` and `editorialPasses` share fixture `eligible`. Schedule
rows additionally use the existing window classification represented by
`schedule_eligible`. Carousel, picks and match-linked editorial retain their
own status, time, candidate and ranking rules after the common base gate.

Elsewhere needs league `visible` for matches without selected routes. Live
polling also uses league visibility with its existing time/status checks;
neither consumer should acquire the fixture route requirement accidentally.
`personalLeagueEligible` uses `calendar_editorial_eligible`, including known
fixture-free leagues. It must not use strip membership or a map built only from
current DOM pills.

Python's default viewer projection uses the same catalog defaults with empty
explicit choices and no paused or solo view. This replaces duplicate
`shown_by_default` and static pill enabled-state decisions. Static HTML still
renders all fetched rows for its existing fallback; this extraction does not
filter those sections or the public discovery pages.

## Adapter responsibilities and intentional defensive changes

Raw local-storage migration remains in the adapter. Existing `on`/`off` lists
are read in that order, so off wins an overlap. A valid new empty object means
no explicit choices and suppresses fallback to the old off-list format. The
adapter maps the browser's empty solo string to `null`. Storage failure handling,
atomic writes and UI commands are outside this pure projection. Following or
unfollowing clears that league's pause; reset clears choices, pauses and solo;
bulk select/clear covers the full catalog. These existing commands remain
responsible for updating their own inputs.

The examples marked `intentional_delta` isolate defensive changes from ordinary
legacy parity: reject unknown or disabled solo targets, reject solo without
eligible strip evidence before evaluating visibility, deny unknown fixture
leagues, and deny contradictory positive route evidence when no services are
selected. Normal UI solo targets already originate in the eligible strip and
the legacy draw clears expired solo before filtering. These examples are not a
claim of a reproduced normal-path product defect. No ordinary valid-input
behavior change is intended.
