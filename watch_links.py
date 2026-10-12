"""Public match destinations already verified by broadcaster-listing adapters.

A channel listing is not itself a streaming link, and a TV channel entitlement does not
grant access to a separate streaming subscription. Keep both the destination and its
supported viewing routes explicit. These links open an event page or player; the service
still handles sign-in and playback availability.
"""
import re
from urllib.parse import urlsplit


UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
# source: (host, exact event-path shape, label, confirmed channels, eligible services)
DESTINATIONS = {
    "Apple TV": (
        "tv.apple.com", r"/us/sporting-event/[a-z0-9-]+/umc\.cse\.[a-z0-9]+",
        "Open on Apple TV", {"Apple TV"}, {"apple"}),
    "Watch ESPN": (
        "www.espn.com", r"/watch/player/_/id/" + UUID,
        "Open on ESPN",
        {"ESPN+", "ESPN", "ESPN2", "ESPNU", "ESPNEWS", "ESPN Deportes", "ESPN Unlimited", "ABC"},
        {"espn", "espnplus", "yttv", "yttvsports", "yttvspanishplus", "yttvspanish", "fubo", "cable"}),
    "ViX": (
        "vix.com", r"/live/transmission-matchid-\d{1,20}",
        "Open on ViX", {"ViX"}, {"vix"}),
    "Peacock": (
        "www.peacocktv.com", r"/sports/[a-z0-9.-]+/" + UUID,
        "Open on Peacock", {"Peacock"}, {"peacock"}),
    "beIN Sports Connect": (
        "watch.beinsports-apps.com", r"/upcoming-events/events/[a-z0-9-]+",
        "Open on beIN Sports", {"beIN Sports Connect"}, {"bein"}),
}
YOUTUBE_TV_SERVICES = {"yttv", "yttvsports", "yttvsportsplus", "yttvspanishplus", "yttvspanish"}


def match_links(match):
    """Return viewing destinations, tagged with the services that can use each one.

    ``via`` is deliberately independent of the visitor's selected filters; the browser
    intersects it with the current selection. Usual-coverage rules, source articles and
    generic sports hubs never become match links. YouTube TV has no verified public match
    route here, so its clearly labelled service link opens the app's home instead.
    """
    links = _verified_match_links(match)
    youtube = sorted({sid for outlet in match.outlets if outlet.known
                      for sid in outlet.via if sid in YOUTUBE_TV_SERVICES})
    if youtube:
        links.append(dict(url="https://tv.youtube.com/", label="Open YouTube TV", via=youtube, kind="service"))
    return links


def service_links(match):
    """Choose one destination for each eligible provider, independent of saved filters.

    A YouTube TV chip opens YouTube TV, even when its channel also has a verified ESPN
    player. Other providers use an eligible match destination when one is available.
    """
    links = {}
    for link in match_links(match):
        for service in link["via"]:
            preferred = "service" if service in YOUTUBE_TV_SERVICES else "match"
            if service not in links or link["kind"] == preferred:
                links[service] = {key: link[key] for key in ("url", "label", "kind")}
    return links


def _verified_match_links(match):
    """Keep source evidence separate from supported event-page/player destinations."""
    destination = DESTINATIONS.get(match.broadcast_source)
    url = match.broadcast_url
    if not destination or not isinstance(url, str) or len(url) > 1024 or any(c.isspace() or ord(c) < 32 for c in url):
        return []
    host, path, label, channels, services = destination
    try:
        parsed = urlsplit(url)
    except ValueError:
        return []
    if (parsed.scheme != "https" or parsed.netloc != host or parsed.query or parsed.fragment or
            not re.fullmatch(path, parsed.path)):
        return []
    via = sorted({sid for outlet in match.outlets if outlet.known and outlet.label in channels
                  for sid in outlet.via if sid in services})
    return [dict(url=url, label=label, via=via, kind="match")] if via else []
