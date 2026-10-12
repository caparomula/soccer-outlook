"""Confirm Liga MX simulcasts from TUDN's public US match pages.

The results page discovers event URLs; only the corresponding US live-event metadata
can confirm a channel. A ViX listing or a team's usual rights never implies TUDN.
"""
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

from provider_utils import document, kickoff, name, fetch_document, report, unavailable


SCHEDULE_URL = "https://www.tudn.com/futbol/liga-mx/resultados"
BASE_URL = "https://www.tudn.com"
MAX_MATCH_REQUESTS = 40
COMPETITION_ID = "199"  # TUDN's verified Liga MX Apertura catalogue.
CHANNELS = {"udn": "TUDN", "univision": "Univision", "unimas": "UniMás"}
LEAGUES = {"Liga MX (Apertura)", "Liga MX (Clausura)", "Liga MX (AP)", "Liga MX (CL)"}
ALIASES = {"tigres uanl": "tigres", "atletico de san luis": "atletico san luis",
           "santos": "santos laguna", "unam": "pumas unam", "tijuana": "club tijuana"}


def _name(value):
    folded = name(value)
    return ALIASES.get(folded, folded)


def _text(value):
    return value if isinstance(value, str) and 0 < len(value) <= 512 else ""


def _url(value):
    value = _text(value)
    if value.startswith("/") and not value.startswith("//"):
        value = BASE_URL + value
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "www.tudn.com" or parsed.query or parsed.fragment or
            not re.fullmatch(r"/futbol/liga-mx/[a-z0-9-]+-vs-[a-z0-9-]+-liga-mx-(?:ap|cl)-\d{4}-\d{2}-\d{2}", parsed.path)):
        return ""
    return value


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active, self.parts = True, []

    def handle_endtag(self, tag):
        if tag == "script":
            self.scripts.append("".join(self.parts))
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def _objects(page):
    """Read JSON arguments in Next's public flight data; never execute scripts."""
    parser = _Scripts()
    parser.feed(document(page))
    chunks = []
    for script in parser.scripts:
        matched = re.fullmatch(r"self\.__next_f\.push\((\[.*\])\);?", script.strip(), re.S)
        if not matched:
            continue
        value = json.loads(matched[1])
        if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
            chunks.append(value[1])
    if not chunks:
        raise ValueError("TUDN page has no public match payload")
    roots = []
    for line in "".join(chunks).splitlines():
        identity, sep, value = line.partition(":")
        if sep and re.fullmatch(r"[0-9a-f]+", identity) and value.startswith(("[", "{")):
            roots.append(json.loads(value))
    stack = [(root, 0) for root in roots]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > 80 or count > 100_000:
            raise ValueError("TUDN payload exceeds traversal limit")
        if isinstance(value, dict):
            yield value
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)


@dataclass(frozen=True)
class Listing:
    home: str
    away: str
    utc: datetime
    event_id: str
    url: str


def parse_schedule(page):
    listings = set()
    found = False
    for obj in _objects(page):
        if "queryResponse" not in obj or "competitionKey" not in obj:
            continue
        if (obj.get("isMx") is not False or obj.get("competitionKey") != COMPETITION_ID or
                obj.get("uri") != BASE_URL + "/futbol/liga-mx"):
            raise ValueError("TUDN results are not US Liga MX listings")
        response = obj.get("queryResponse")
        matches = response.get("getMatches") if isinstance(response, dict) else None
        events = matches.get("events") if isinstance(matches, dict) else None
        if not isinstance(events, list) or len(events) > 500:
            raise ValueError("TUDN match list changed")
        found = True
        for event in events:
            if (not isinstance(event, dict) or event.get("type") != "scorecell" or
                    event.get("leagueName") not in LEAGUES or event.get("status") not in ("pre", "in", "post")):
                continue
            home, away = event.get("home"), event.get("away")
            if not isinstance(home, dict) or not isinstance(away, dict):
                continue
            home, away = _text(home.get("fullName")), _text(away.get("fullName"))
            utc, event_id = kickoff(event.get("date")), _text(event.get("key"))
            if not home or not away or utc is None or not re.fullmatch(r"\d{1,20}", event_id):
                continue
            # Invalid URLs still participate in duplicate/ambiguity checks.
            listings.add(Listing(_name(home), _name(away), utc, event_id, _url(event.get("relativeUrl"))))
    if not found:
        raise ValueError("TUDN page has no Liga MX results component")
    return list(listings)


def parse_match(page, listing):
    """Return channels only when page, event ID, clubs, kickoff and US stream agree."""
    records = [obj for obj in _objects(page) if "livestreamConfig" in obj and "matchOpeningRef" in obj]
    unique = {json.dumps(obj, sort_keys=True): obj for obj in records}
    if len(unique) != 1:
        raise ValueError("TUDN match component missing or ambiguous")
    obj = next(iter(unique.values()))
    if (obj.get("isMx") is not False or obj.get("leagueId") != COMPETITION_ID or
            obj.get("leagueName") not in LEAGUES or obj.get("matchId") != listing.event_id or _url(obj.get("pageUri")) != listing.url):
        return ()
    for key, expected in (("homeTeam", listing.home), ("awayTeam", listing.away)):
        team = obj.get(key)
        if not isinstance(team, dict) or _name(_text(team.get("name"))) != expected:
            return ()
    opening = obj.get("matchOpeningRef")
    if not isinstance(opening, dict) or opening.get("variables") != {"matchId": listing.event_id}:
        return ()
    response = opening.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    event = data.get("matchOpening") if isinstance(data, dict) else None
    if (not isinstance(event, dict) or event.get("leagueName") not in LEAGUES or
            kickoff(event.get("date")) != listing.utc or _url(event.get("url")) != listing.url or
            event.get("status") not in ("pre", "in", "post")):
        return ()
    config = obj.get("livestreamConfig")
    if (not isinstance(config, dict) or config.get("leagueName") != "Liga MX" or
            _name(_text(config.get("homeTeamName"))) != listing.home or
            _name(_text(config.get("awayTeamName"))) != listing.away):
        return ()
    streams = config.get("livestreams")
    if not isinstance(streams, list) or len(streams) > 20:
        return ()
    channels = set()
    for stream in streams:
        if not isinstance(stream, dict):
            return ()
        targeting = stream.get("adTargeting")
        if (stream.get("mcpOrigin") != "US" or stream.get("videoType") != "livestream" or
                stream.get("isAuth") is not True or stream.get("placeholderId") != "mvpd" or
                type(stream.get("livestreamId")) is not int or str(stream["livestreamId"]) != listing.event_id or
                _url(stream.get("uri")) != listing.url or not isinstance(targeting, dict) or
                targeting.get("stream_type") != "live" or targeting.get("video_type") != "liveevent"):
            return ()
        codes = stream.get("channels")
        if (not isinstance(codes, list) or not codes or len(codes) > 10 or
                any(not isinstance(code, str) or code not in CHANNELS for code in codes)):
            return ()
        channels.update(CHANNELS[code] for code in codes)
    # Stream channels must also match the page's explicit television coverage.
    coverage = config.get("televisionCoverage")
    if not isinstance(coverage, str):
        return ()
    codes = [value.strip() for value in coverage.split(",")]
    if any(value not in CHANNELS for value in codes) or channels != {CHANNELS[value] for value in codes}:
        return ()
    return tuple(sorted(channels))


def enrich(builder, matches, fetch):
    """Supplement all eligible Liga MX fixtures, including existing ViX listings and results."""
    candidates = [m for m in matches if m.league == "mex.1" and m.time_valid and
                  m.utc.utcoffset() is not None and m.state in ("pre", "in", "post") and
                  m.status.lower() not in builder.VOID_STATUSES]
    result = report(candidates, SCHEDULE_URL)
    if not candidates:
        return result
    try:
        listings = parse_schedule(fetch_document(fetch, SCHEDULE_URL, result))
    except (ValueError, UnicodeError, RecursionError):
        unavailable(result, candidates)
        return result
    pages = {}
    for match in candidates:
        identity = (_name(match.home.name), _name(match.away.name), match.utc)
        found = [item for item in listings if (item.home, item.away, item.utc) == identity]
        if len(found) != 1 or not found[0].url:
            continue
        listing = found[0]
        if listing.url not in pages:
            if len(pages) >= MAX_MATCH_REQUESTS:
                result["per_match"][match.id]["status"] = "unavailable"
                continue
            pages[listing.url] = fetch_document(fetch, listing.url, result)
        try:
            channels = parse_match(pages[listing.url], listing)
        except (ValueError, UnicodeError, RecursionError):
            unavailable(result, [match])
            continue
        if not channels:
            continue
        additions = [builder.map_outlet(channel, match.league) for channel in channels]
        if not all(outlet.known for outlet in additions):
            continue
        existing = {outlet.label for outlet in match.outlets}
        match.outlets.extend(outlet for outlet in additions if outlet.label not in existing)
        match.rule, match.hint = None, ""
        match.broadcast_source, match.broadcast_url = "TUDN", listing.url
        match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
        result["confirmed"] += 1
        result["unmatched"] -= 1
        result["per_match"][match.id] = dict(status="confirmed", url=listing.url)
    return result
