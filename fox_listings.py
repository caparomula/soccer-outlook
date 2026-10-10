"""Confirm missing Liga MX listings against FOX Sports' public, dated scoreboards.

This optional source only supplements ESPN when the home-club rights already point to FOX.
It never infers coverage from page navigation, network logos, or a league's usual broadcaster.
FOX's Nuxt payload is a JSON reference table, not executable JavaScript; only the small set of
event fields below is read. If its shape changes, the existing uncertain listing survives.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


SCORES_URL = "https://www.foxsports.com/soccer/liga-mx/scores?date={day}"
ET = ZoneInfo("America/New_York")
MAX_PAGE_BYTES = 2_000_000
CHANNELS = {"fox": "FOX", "fs1": "FS1", "fs2": "FS2", "fox deportes": "Fox Deportes"}


class _Payload(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.count = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NUXT_DATA__":
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
    channel: str
    url: str


def _name(value):
    """Fold accents and whitespace, without guessing abbreviated or renamed clubs."""
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _kickoff(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _source_url(value, event_id):
    if not isinstance(value, str) or len(value) > 512:
        return ""
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.foxsports.com" + value
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "www.foxsports.com" or
            parsed.query or parsed.fragment or
            not re.fullmatch(r"/soccer/[a-z0-9-]+-game-boxscore-" + event_id, parsed.path)):
        return ""
    return value


def parse_schedule(page):
    """Read event-local names, kickoff, channel and link; malformed documents raise ValueError.

    Keep identity-valid entries even when their channel or source link is unusable. Such entries
    must still count toward ambiguity, so a conflicting duplicate cannot confirm a match.
    """
    if not isinstance(page, (bytes, str)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("FOX schedule is missing or too large")
    if isinstance(page, bytes):
        page = page.decode("utf-8")
    parser = _Payload()
    try:
        parser.feed(page)
    except AssertionError as exc:
        raise ValueError("FOX schedule contains malformed markup") from exc
    if parser.count != 1 or not parser.parts:
        raise ValueError("FOX schedule has no unique JSON payload")
    table = json.loads("".join(parser.parts))
    if not isinstance(table, list) or len(table) > 20_000:
        raise ValueError("FOX schedule payload is not a bounded reference table")

    def ref(value):
        return table[value] if type(value) is int and 0 <= value < len(table) else None

    def field(obj, key):
        return ref(obj.get(key)) if isinstance(obj, dict) else None

    def text(obj, key):
        value = field(obj, key)
        return value if isinstance(value, str) and len(value) <= 512 else ""

    listings = []
    for event in table:
        if text(event, "template") != "scores-team":
            continue
        content = text(event, "contentUri")
        identity = re.fullmatch(r"soccer/liga_mx/events/(\d+)", content)
        if not identity or text(event, "league") != "LIGA MX":
            continue
        kickoff = _kickoff(text(event, "eventTime"))
        if kickoff is None or field(event, "isTba") is not False:
            continue
        link = field(event, "entityLink")
        tokens = field(field(link, "layout"), "tokens")
        teams = [field(event, "upperTeam"), field(event, "lowerTeam")]
        home_uri, away_uri = text(tokens, "homeUri"), text(tokens, "awayUri")
        if not home_uri or not away_uri or home_uri == away_uri:
            continue
        home = [text(team, "longName") for team in teams if text(team, "uri") == home_uri]
        away = [text(team, "longName") for team in teams if text(team, "uri") == away_uri]
        if len(home) != 1 or len(away) != 1 or not home[0] or not away[0]:
            continue
        if text(link, "contentUri") != content or text(tokens, "eventUri") != content:
            continue
        channel = CHANNELS.get(text(event, "tvStation").strip().casefold(), "")
        listings.append(Listing(_name(home[0]), _name(away[0]), kickoff, channel,
                                _source_url(text(link, "webUrl"), identity.group(1))))
    return listings


def enrich(builder, matches, fetch):
    """Update confirmed matches in place and return fetch/match counts; source failure is optional.

    ``fetch(url)`` returns bytes or None. Callers own its timeout/retry policy. One request per
    Eastern date is sufficient; FOX sometimes returns a different day's default scoreboard,
    which cannot match because both clubs and the full UTC kickoff must agree.
    """
    candidates = {}
    for match in matches:
        if (match.league != "mex.1" or match.outlets or not match.rule or
                match.rule.label != "FOX Sports networks" or not match.time_valid or
                match.state not in ("pre", "in") or
                match.status.lower() in builder.VOID_STATUSES or
                match.utc.utcoffset() is None):
            continue
        day = match.utc.astimezone(ET).date().isoformat()
        candidates.setdefault(day, []).append(match)

    report = {"requested": 0, "confirmed": 0, "unmatched": 0, "failed": 0, "per_match": {}}
    for day, day_matches in sorted(candidates.items()):
        source_url = SCORES_URL.format(day=day)
        report["requested"] += 1
        try:
            page = fetch(source_url)
        except Exception:
            page = None  # Optional source transport failures must not discard the ESPN schedule.
        try:
            listings = parse_schedule(page)
        except (ValueError, UnicodeError, RecursionError):
            report["failed"] += 1
            report["unmatched"] += len(day_matches)
            for match in day_matches:
                report["per_match"][match.id] = {"status": "unavailable", "url": source_url}
            continue
        for match in day_matches:
            identity = (_name(match.home.name), _name(match.away.name), match.utc)
            found = [item for item in listings if (item.home, item.away, item.utc) == identity]
            if len(found) != 1 or not found[0].channel or not found[0].url:
                report["unmatched"] += 1
                report["per_match"][match.id] = {"status": "unlisted", "url": source_url}
                continue
            listing = found[0]
            outlet = builder.map_outlet(listing.channel, match.league)
            if not outlet.known:
                report["unmatched"] += 1
                report["per_match"][match.id] = {"status": "unlisted", "url": source_url}
                continue
            match.outlets = [outlet]
            match.rule = None
            match.hint = ""
            match.broadcast_source = "FOX Sports"
            match.broadcast_url = listing.url
            match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
            report["confirmed"] += 1
            report["per_match"][match.id] = {"status": "confirmed", "url": listing.url}
    return report
