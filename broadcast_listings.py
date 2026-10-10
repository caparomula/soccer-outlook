"""Check each usual-coverage provider without making its availability a build requirement."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module


# Every current rights.toml fallback has a provider here. These URLs also give a reader
# a direct way to check when a public listing cannot be verified automatically.
PROVIDERS = {
    "FOX Sports networks": ("fox_listings", "FOX Sports", "https://www.foxsports.com/soccer/liga-mx/scores"),
    "ESPN+": ("espn_listings", "ESPN", "https://www.espn.com/watch/schedule"),
    "Paramount+": ("paramount_listings", "Paramount+", "https://www.paramountplus.com/sports/"),
    "ViX": ("vix_listings", "ViX", "https://vix.com/es-es/deportes"),
    "Peacock": ("peacock_listings", "Peacock", "https://www.peacocktv.com/sports"),
    "Apple TV": ("apple_listings", "Apple TV", "https://tv.apple.com/us/channel/mls-season-pass/tvs.sbd.7000"),
    "Fandango": ("fandango_listings", "Fandango", "https://athome.fandango.com/content/browse/uxpage/Bundesliga/405"),
    "beIN Sports Connect": ("bein_listings", "beIN Sports", "https://watch.beinsports-apps.com/upcoming-events"),
}


def enrich(builder, matches, fetch, built_at):
    """Run independent public schedule checks and retain their result on each match.

    Confirmed channels are applied by the provider adapters, using the normal service mapper.
    An absent or unreadable listing never erases the rights fallback or an ESPN listing.
    """
    groups = {}
    for match in matches:
        if match.outlets or not match.rule:
            continue
        groups.setdefault(match.rule.label, []).append(match)

    def check(item):
        channel, group = item
        provider = PROVIDERS.get(channel)
        if not provider:
            return channel, {"requested": 0, "confirmed": 0, "unmatched": len(group), "failed": 0,
                             "per_match": {}}, "", ""
        module, name, url = provider
        # Import mistakes are implementation errors, so do not silently turn them into
        # a successful build. Each adapter handles its optional source's transport/format errors.
        report = import_module(module).enrich(builder, group, fetch)
        return channel, report, name, url

    reports = {}
    with ThreadPoolExecutor(max_workers=min(4, len(groups) or 1)) as pool:
        for channel, report, name, url in pool.map(check, groups.items()):
            reports[channel] = report
            outcomes = report.get("per_match", {})
            for match in groups[channel]:
                result = outcomes.get(match.id, {})
                status = result.get("status", "not_checked")
                if status not in ("confirmed", "unlisted", "unavailable", "not_checked"):
                    raise ValueError("Unrecognized broadcaster-check outcome")
                match.broadcast_check = status
                match.broadcast_check_source = name
                match.broadcast_check_url = result.get("url") or url
                match.broadcast_checked_at = built_at.isoformat() if status != "not_checked" else ""
    return reports
