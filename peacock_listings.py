"""Read the publicly rendered Peacock US sports calendar for usual Chivas coverage.

An upcoming/live, directly available soccer event must match both clubs and the exact
kickoff. A rights page, replay, or a pregame show's start time cannot confirm a fixture.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import urlsplit


SCHEDULE_URL = "https://www.peacocktv.com/sports"
MAX_PAGE_BYTES = 2_000_000


class _Payload(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.count = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NEXT_DATA__":
            self.count += 1
            self.active = attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


@dataclass(frozen=True)
class Listing:
    home: str
    away: str
    utc: datetime
    url: str


def _name(value):
    folded = unicodedata.normalize("NFKD", value).casefold()
    folded = " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())
    return {"chivas": "guadalajara", "chivas de guadalajara": "guadalajara",
            "santos laguna": "santos"}.get(folded, folded)


def _source_url(value):
    if not isinstance(value, str) or len(value) > 512:
        return ""
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.peacocktv.com" + value
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "www.peacocktv.com" or parsed.query or parsed.fragment or
            not re.fullmatch(r"/sports/[a-z0-9.-]+/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", parsed.path)):
        return ""
    return value


def parse_schedule(page):
    if not isinstance(page, (bytes, str)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("Peacock schedule is missing or too large")
    parser = _Payload()
    parser.feed(page.decode("utf-8") if isinstance(page, bytes) else page)
    if parser.count != 1 or not parser.parts:
        raise ValueError("Peacock schedule has no unique JSON payload")
    payload = json.loads("".join(parser.parts))
    root = payload
    for key in ("props", "apolloState", "data", "ROOT_QUERY"):
        root = root.get(key) if isinstance(root, dict) else None
    if not isinstance(root, dict) or len(root) > 20_000:
        raise ValueError("Peacock schedule payload changed")
    collections = [v for k, v in root.items() if k.startswith("fetchCollection(")
                   and isinstance(v, dict) and isinstance(v.get("assets"), list)]
    if not collections:
        raise ValueError("Peacock schedule has no event collections")
    listings = []
    for collection in collections:
        for event in collection["assets"]:
            if (not isinstance(event, dict) or event.get("type") != "ASSET/SLE" or
                    event.get("classification") != "SPORTS" or event.get("mediaType") != "SLE"):
                continue
            genres, details = event.get("genres"), event.get("eventDetails")
            if (not isinstance(genres, list) or "Soccer" not in genres or
                    not any(g in ("Liga MX", "Liga MX Soccer") for g in genres) or
                    not isinstance(details, dict) or details.get("eventStage") not in ("UPCOMING", "LIVE")):
                continue
            name, timestamp = event.get("title"), details.get("eventDisplayStartDate")
            if (not isinstance(name, str) or len(name) > 250 or type(timestamp) is not int or
                    not 946684800000 <= timestamp < 4102444800000 or event.get("displayStartTime") != timestamp):
                continue
            teams = re.split(r" (?:vs\.|v\.) ", re.sub(r" \(Español\)$", "", name))
            if len(teams) != 2 or not all(team.strip() for team in teams):
                continue
            segments = event.get("contentSegments")
            available = event.get("available") is True and isinstance(segments, list) and "D2C" in segments
            url = _source_url(event.get("url")) if available else ""
            # Keep unusable duplicates: ambiguous listings cannot certify availability.
            listings.append(Listing(_name(teams[0]), _name(teams[1]),
                                    datetime.fromtimestamp(timestamp / 1000, timezone.utc), url))
    return listings


def enrich(builder, matches, fetch):
    """Confirm missing Peacock listings in place; ``fetch(url)`` returns bytes or None."""
    candidates = [m for m in matches if m.league == "mex.1" and not m.outlets and m.rule
                  and m.rule.label == "Peacock" and m.time_valid and m.state in ("pre", "in")
                  and m.status.lower() not in builder.VOID_STATUSES and m.utc.utcoffset() is not None]
    report = {"requested": 0, "confirmed": 0, "unmatched": 0, "failed": 0, "per_match": {}}
    if not candidates:
        return report
    report["requested"] = 1
    try:
        page = fetch(SCHEDULE_URL)
    except Exception:
        page = None
    try:
        listings = parse_schedule(page)
    except (ValueError, UnicodeError, RecursionError):
        report["failed"] = 1
        report["unmatched"] = len(candidates)
        report["per_match"] = {m.id: {"status": "unavailable", "url": SCHEDULE_URL} for m in candidates}
        return report
    for match in candidates:
        identity = (_name(match.home.name), _name(match.away.name), match.utc)
        found = [item for item in listings if (item.home, item.away, item.utc) == identity]
        if len(found) != 1 or not found[0].url:
            report["unmatched"] += 1
            report["per_match"][match.id] = {"status": "unlisted", "url": SCHEDULE_URL}
            continue
        outlet = builder.map_outlet("Peacock", match.league)
        if not outlet.known:
            report["unmatched"] += 1
            report["per_match"][match.id] = {"status": "unavailable", "url": SCHEDULE_URL}
            continue
        match.outlets = [outlet]
        match.rule = None
        match.hint = ""
        match.broadcast_source = "Peacock"
        match.broadcast_url = found[0].url
        match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
        report["confirmed"] += 1
        report["per_match"][match.id] = {"status": "confirmed", "url": match.broadcast_url}
    return report
