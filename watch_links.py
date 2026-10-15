"""Public match destinations already verified by broadcaster-listing adapters.

A channel listing is not itself a streaming link, and a TV channel entitlement does not
grant access to a separate streaming subscription. Keep both the destination and its
supported viewing routes explicit. These links open an event page or player; the service
still handles sign-in and playback availability.
"""
import re
from pathlib import Path
import tomllib
from urllib.parse import parse_qsl, urlsplit


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
with Path(__file__).with_name("watch-links.toml").open("rb") as _file:
    YOUTUBE_TV_LINKS = tomllib.load(_file).get("youtube_tv", {})


def match_links(match):
    """Return viewing destinations, tagged with the services that can use each one.

    ``via`` is deliberately independent of the visitor's selected filters; the browser
    intersects it with the current selection. Usual-coverage rules, source articles and
    generic sports hubs never become match links. Supplied YouTube TV watch URLs are
    bound to one fixture and channel; they are offered only while that fixture is live.
    """
    links = _verified_match_links(match)
    return links + _youtube_tv_links(match)


def service_links(match, channel=None):
    """Choose one destination for each eligible provider, independent of saved filters.

    A supplied YouTube TV watch URL takes precedence over a compatible broadcaster
    player. Without one, an eligible exact match destination can still be used. When
    a channel is supplied, both the destination and provider must support that channel.
    """
    links = {}
    eligible = {sid for outlet in match.outlets if outlet.known and outlet.label == channel for sid in outlet.via}
    for link in match_links(match):
        if channel is not None and channel not in link["channels"]:
            continue
        for service in link["via"]:
            if channel is not None and service not in eligible:
                continue
            links[service] = {key: value for key, value in link.items() if key not in ("via", "channels")}
    return links


def _youtube_tv_links(match):
    """Use only a supplied fixture/channel association, never guess a watch ID.

    A /watch/ URL can identify a linear stream rather than an archived event. Keep
    the association in the page for live updates, but require live state at rendering.
    """
    entry = YOUTUBE_TV_LINKS.get(match.id, {})
    url = entry.get("url", "")
    if not isinstance(url, str) or len(url) > 1024 or any(c.isspace() or ord(c) < 32 for c in url):
        return []
    try:
        parsed = urlsplit(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return []
    if (parsed.scheme != "https" or parsed.netloc != "tv.youtube.com" or parsed.fragment or
            not re.fullmatch(r"/watch/[A-Za-z0-9_-]{11}", parsed.path) or
            (query and (len(query) != 1 or query[0][0] != "vp" or
                        not re.fullmatch(r"[A-Za-z0-9_+/=-]{1,256}", query[0][1])))):
        return []
    via = sorted({sid for outlet in match.outlets if outlet.known and outlet.label == entry.get("channel")
                  for sid in outlet.via if sid in YOUTUBE_TV_SERVICES})
    return [dict(url=url, label="Open on YouTube TV", via=via, channels=[entry["channel"]],
                 kind="match", live_only=True)] if via else []


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
    listed = sorted({outlet.label for outlet in match.outlets if outlet.known and outlet.label in channels})
    return [dict(url=url, label=label, via=via, channels=listed, kind="match")] if via else []
