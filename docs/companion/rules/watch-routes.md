# WATCH-ROUTES-1: Qualified viewing-route selection

Given published route facts and an explicit selected-service set, select the
preferred listed route; only if none is available, select a qualified usual
route. Return every selected provider for each network in viewer preference
order. Preserve the distinction between listed and usual coverage.

This is an extraction of `build.py::evaluate`, `owner_pill_service` and
`web/app.js::evaluateRow`/`firstHave` plus provider-list intersection. The caller
has already resolved channel aliases, country, package requirements, exclusions,
source identity and evidence validity. This rule does not create eligibility
from a network name or provider brand. It implements part of WATCH-1 and WATCH-3.
[WATCH-VALIDITY-1](route-validity.md) now supplies the temporal gate before this
intersection and preference rule when original validity facts and an explicit
clock are provided. Full source qualification remains upstream; the legacy
already-qualified API does not infer missing validity facts.

## Decision inputs and outputs

Inputs are an ordered list of listed outlets, an optional usual outlet, a
complete unique service preference order, service display names and selected
service IDs. Each outlet has its canonical label, the explicitly qualified
service IDs that carry it, and whether its feed is Spanish. An outlet's position
is a stable final tie-breaker. These facts must be independent of the viewer's
selection so that changing preferences requires no source fetch.

The preferred-route result is `{service_id, basis, outlet}`. `basis` is `listed`,
`rule` (the existing runtime spelling for usual coverage), or `none`. The absent
result uses `null` for `service_id` and an empty string for `outlet`; adapters map
that absence to Python's empty string or the browser's existing `none` marker.
The provider result is an
ordered list of qualified selected service IDs for each outlet, including the
usual outlet when present. A usual route does not become a listed route because
its selected service is preferred.

Service ID membership is exact. The caller resolves an absent saved selection
to the published defaults before invoking the rule. An explicit empty selection
stays empty. The caller also removes obsolete saved service IDs and completes a
partial saved order using catalog order. Neither operation is inferred from
outlet names or from an installed provider application.

## Language-neutral algorithm

```text
providers(outlet):
    if outlet is explicitly unknown: return empty
    retain outlet.service_ids that occur in selected_service_ids
    order them by their position in service_order

best = none
for each listed outlet in input order:
    for each service in providers(outlet):
        key = (position of service in service_order,
               outlet.label differs from service_names[service],
               outlet.is_spanish)
        if best is absent or key is lexicographically smaller:
            best = (key, service, "listed", outlet.label)
if best exists: return its service, basis and outlet
if a usual outlet exists and providers(usual) is nonempty:
    return its first provider, "rule", usual.label
return null, "none", ""
```

A direct-service outlet takes precedence over another network on the same
service, then English takes precedence over Spanish. Viewer service preference
takes precedence over both. Exactly equal keys keep the first listed outlet.
Changing iteration order or sorting equal outlets by label would change that
tie-break. The same provider selection is used in schedule, carousel and pick
network disclosures; each card must not repeat a smaller intersection.

## Independently authored examples

The complete executable fixtures are [route-examples.json](route-examples.json).
All names except the explicitly identified package example are synthetic; the
tests establish ordering and membership, not current provider entitlements.

| Case | Inputs | Expected; tempting wrong answer |
| --- | --- | --- |
| Explicit empty lineup | A qualified listed service exists; selection is empty | No preferred route and no providers; do not substitute owner defaults |
| Package exclusion | Universo route qualifies only `yttvspanishplus`; only `yttv` is selected | No route; the main plan's shared brand does not grant the add-on |
| Added qualified package | The same route; both IDs selected | Spanish Plus qualifies; main plan remains absent from providers |
| Listed before usual | Less-preferred selected service has a listed outlet; preferred service has usual coverage | The listed service wins; service order does not upgrade usual coverage |
| Service order first | Preferred service carries a Spanish network; next service carries a direct English outlet | Preferred service wins |
| Direct outlet first | One service carries a direct Spanish outlet and a different English network | Direct outlet wins; language is the later tie-break |
| Language next | One service carries two non-direct outlets, Spanish first and English second | English wins |
| Equal keys | Two equally ranked English outlets on one service | First input outlet wins; do not alphabetize |
| Usual fallback | No selected listed provider; a qualified usual route has two selected providers | First provider in service order wins, with basis `rule` |
| Separate subscription | A listed standalone route exists; only a provider-authentication service is selected | No route; provider access is not a standalone subscription |

## Missing or contradictory facts

An unknown channel has no qualified service IDs and grants nothing. This
extraction also accepts an explicit `unknown` flag. A true flag suppresses every
provider even if malformed input supplies service IDs. The two JSON cases marked
`intentional_delta` record this defensive change separately from legacy parity;
ordinary producer output already has no services for an unknown channel. An absent
usual route is not replaced by a league guess. Names are used only for the
documented direct-outlet preference, never for entitlement inference.

The normalized feed boundary must reject an unknown catalog service or
contradictory qualified/excluded membership before invoking this rule; the
[feed reader](../../../packages/outlook_data/lib/src/feed_reader.dart) already
rejects eligible/excluded overlap. This extraction accepts already-qualified
facts and must not silently add a different evidence policy. Duplicate provider
IDs are malformed producer input, not multiple provider choices.

The current companion feed does not yet preserve all Spanish/direct-label
preference facts as an explicit domain mapping. The website/publisher extraction
must pass the existing outlet metadata unchanged; a future feed-to-domain
adapter must preserve it before claiming identical route selection. Retained
route expiry, usual-rights validity and relevant negative evidence must be
enforced by the later validity family, including while no new feed arrives.

## Ownership and review

Implementation owner: [`selectRoute`](../../../packages/outlook_core/lib/src/routes.dart)
in pure `outlook_core`. Read-through consumers: publisher CLI and compiled browser bridge. Existing Python callers
include fixture interpretation, broadcaster confirmation/retention, public
discovery pages and static provider markup; browser callers include preferred
route styling and all provider disclosures. Migration must inventory these
callers rather than remove only the main-page duplicate.

Checks: the independent JSON examples, legacy/new comparison on real captured
outlets and altered preferences, CLI/browser/VM agreement, and affected provider
disclosure/browser tests. Review date: October 20, 2026. Fingerprint pending the
explicit stamping workflow. Evidence/source validity, destination selection and
league visibility are not owned by this first rule.
