"""Supplement ESPN with public US listings and retain each source's coverage evidence."""
from datetime import datetime, timedelta, timezone
from importlib import import_module
from urllib.parse import urlsplit

from provider_utils import eligible, finish_report, kickoff, report as source_report


# The fallback keys stay stable for historical reports. Discovery is by supported
# competition, not by missing outlets: a second source can expose a useful simulcast.
PROVIDERS = {
    "FOX Sports networks": ("fox_listings", "FOX Sports", "https://www.foxsports.com/soccer/liga-mx/scores"),
    "ESPN+": ("espn_listings", "Watch ESPN", "https://www.espn.com/watch/schedule"),
    "Paramount+": ("paramount_listings", "Paramount+", "https://www.paramountplus.com/sports/"),
    "ViX": ("vix_listings", "ViX", "https://vix.com/es-es/deportes"),
    "Peacock": ("peacock_listings", "Peacock", "https://www.peacocktv.com/sports"),
    "Apple TV": ("apple_listings", "Apple TV", "https://tv.apple.com/us/channel/mls-season-pass/tvs.sbd.7000"),
    "Fandango": ("fandango_listings", "Fandango", "https://athome.fandango.com/content/browse/uxpage/Bundesliga/405"),
    "beIN Sports Connect": ("bein_listings", "beIN Sports Connect", "https://watch.beinsports-apps.com/upcoming-events"),
}
PROVIDER_LEAGUES = {
    "FOX Sports networks": {"mex.1"},
    "ESPN+": {"esp.1", "eng.fa", "esp.copa_del_rey", "ned.1", "usa.usl.1", "usa.usl.l1"},
    "Paramount+": {"sco.1", "ita.1", "eng.w.1", "uefa.champions", "uefa.europa", "uefa.europa.conf",
                   "uefa.wchampions", "eng.league_cup", "concacaf.champions", "ita.coppa_italia"},
    "ViX": {"mex.1"}, "Peacock": {"mex.1"}, "Apple TV": {"usa.1"},
    "Fandango": {"ger.1"}, "beIN Sports Connect": {"por.1"},
}


def enrich(builder, matches, fetch, built_at):
    """Check supported competitions without treating absent listings as unavailable TV.

    Adapters run sequentially because a fixture can have multiple sources. Each may
    add explicit channels, but neither an outage nor an empty page may erase evidence.
    Source failures remain optional; import mistakes remain implementation errors.
    """
    matches = list(matches)
    valid = [match for match in matches if eligible(builder, match)]
    for match in valid:
        if not hasattr(match, "broadcast_checks"):
            match.broadcast_checks = []
        if not hasattr(match, "broadcast_evidence"):
            match.broadcast_evidence = []
        recorded = {item.get("channel") for item in match.broadcast_evidence}
        for outlet in match.outlets:
            if outlet.label not in recorded:
                match.broadcast_evidence.append(dict(channel=outlet.label,
                    source=match.broadcast_source or "ESPN", url=match.broadcast_url or match.link,
                    country="US", kind="match_listing", checked_at=built_at.isoformat()))

    def record(group, result, name, url, source_key=""):
        finish_report(result)
        health = result["diagnostics"]["status"]
        for match in group:
            item = result.get("per_match", {}).get(match.id, {})
            status = item.get("status", "not_checked")
            if status not in ("confirmed", "unlisted", "unavailable", "not_checked"):
                raise ValueError("Unrecognized broadcaster-check outcome")
            check = dict(item, status=status, source=name, url=item.get("url") or url,
                         checked_at=built_at.isoformat() if status != "not_checked" else "", source_health=health)
            match.broadcast_checks.append(check)
            for evidence in match.broadcast_evidence:
                if not evidence.get("checked_at"):
                    evidence["checked_at"] = built_at.isoformat()
            # Legacy UI fields describe the best evidence, while the list above
            # retains every unsuccessful lookup as well as every confirmation.
            if match.outlets and status != "confirmed":
                continue
            if match.rule and source_key and match.rule.label != source_key:
                continue
            if status == "not_checked" and match.broadcast_check:
                continue
            match.broadcast_check = status
            match.broadcast_check_source = name
            match.broadcast_check_url = check["url"]
            match.broadcast_checked_at = check["checked_at"]

    reports = {}
    groups = {channel: [match for match in valid if match.league in PROVIDER_LEAGUES[channel]
                       or (match.rule and match.rule.label == channel)] for channel in PROVIDERS}
    liga_mx = [match for match in valid if match.league == "mex.1"]
    if liga_mx:
        provider = import_module("tudn_listings")
        result = provider.enrich(builder, liga_mx, fetch)
        reports["TUDN"] = result
        record(liga_mx, result, "TUDN", provider.SCHEDULE_URL)
    for channel, group in groups.items():
        if not group:
            continue
        module, name, url = PROVIDERS[channel]
        result = import_module(module).enrich(builder, group, fetch)
        reports[channel] = result
        record(group, result, name, url, channel)
    # Preserve explicit diagnostics for newly configured rules until an adapter is added.
    for channel in sorted({match.rule.label for match in valid if match.rule} - PROVIDERS.keys()):
        group = [match for match in valid if match.rule and match.rule.label == channel]
        result = source_report([], "")
        result.update(unmatched=len(group))
        reports[channel] = result
        record(group, result, channel, "")
    return reports


def compact_report(reports):
    """Public diagnostics omit per-match repetition and request bodies/credentials."""
    result = {}
    for source, report in reports.items():
        finish_report(report)
        diagnostics = report["diagnostics"]
        result[source] = {key: report.get(key, 0) for key in ("requested", "confirmed", "unmatched", "failed")}
        result[source].update({key: diagnostics.get(key) for key in (
            "candidate_matches", "status", "requested_horizon", "observed_horizon", "horizon_relation", "parsed_pages",
            "observed_listings", "reasons", "requests", "observations")})
    return result


def _same_team(current, previous):
    if not isinstance(previous, dict):
        return False
    if current.id or previous.get("id"):
        return bool(current.id) and current.id == previous.get("id")
    return current.name == previous.get("name")


def retain_recent_evidence(builder, matches, previous_records, built_at, max_age_hours=6):
    """Bridge brief source outages with bounded, previously verified US match evidence.

    Only an identical fixture qualifies. A clean current absence is not overwritten,
    and neither the original verification time nor its expiry is extended by reuse.
    """
    if isinstance(previous_records, dict):
        records = previous_records
    else:
        records = {record.get("id"): record for record in previous_records if isinstance(record, dict)}
    retained_matches, retained_channels = set(), 0
    now = built_at.astimezone(timezone.utc)
    ttl = timedelta(hours=max(0, min(6, max_age_hours)))
    for match in matches:
        previous = records.get(match.id)
        if (not eligible(builder, match) or match.state not in ("pre", "in") or
                now >= match.utc + timedelta(hours=4) or not isinstance(previous, dict) or
                previous.get("league") != match.league or
                kickoff(previous.get("utc") or previous.get("kickoff_utc")) != match.utc or
                not _same_team(match.home, previous.get("home")) or not _same_team(match.away, previous.get("away"))):
            continue
        labels = {outlet.label for outlet in match.outlets}
        checks = getattr(match, "broadcast_checks", [])
        for evidence in previous.get("broadcast_evidence", []):
            if not isinstance(evidence, dict):
                continue
            checked = kickoff(evidence.get("checked_at"))
            url = evidence.get("url", "")
            if (evidence.get("country") != "US" or evidence.get("kind") != "match_listing" or
                    not isinstance(url, str) or urlsplit(url).scheme != "https" or
                    checked is None or not timedelta(0) <= now - checked <= ttl):
                continue
            source_checks = [item for item in checks if item.get("source") == evidence.get("source")]
            if not source_checks:
                continue
            check = source_checks[-1]
            # A source-wide outage must not erase a fixture-specific rejection.
            # Unfamiliar reasons fail closed until their semantics are reviewed.
            transport_or_catalogue = {
                "", "fetch_failed", "fetch_or_parse_failed", "invalid_schedule", "invalid_catalogue",
                "invalid_browse_schedule", "scoreboard_unavailable", "tv_configuration_unavailable",
                "tv_schedule_unavailable", "results_unavailable", "landing_unavailable", "match_page_fetch_failed",
                "unrecognized_or_unavailable_schedule", "pagination_incomplete", "request_budget_exhausted"}
            not_observed = {"outside_requested_horizon", "not_seen_in_partial_catalogue",
                            "no_matching_listing", "event_not_found"}
            status, reason = check.get("status"), check.get("reason", "")
            uncertain = ((status == "unavailable" and reason in transport_or_catalogue) or
                         (status == "unlisted" and reason in not_observed and
                          (reason == "outside_requested_horizon" or check.get("source_health") in
                           ("partial", "outside_requested_horizon"))))
            if not uncertain:
                continue
            outlet = builder.map_outlet(evidence.get("channel", ""), match.league)
            if not outlet.known or outlet.label in labels:
                continue
            match.outlets.append(outlet)
            labels.add(outlet.label)
            match.broadcast_evidence.append(dict(evidence, retained=True))
            retained_matches.add(match.id)
            retained_channels += 1
        if match.id in retained_matches:
            match.rule, match.hint = None, ""
            match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
    return dict(retained_matches=len(retained_matches), retained_channels=retained_channels)
