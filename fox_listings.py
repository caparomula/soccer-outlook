"""Confirm FOX-owned US networks from the public, dated television schedule.

The scoreboard provides club identities and remains a fallback for explicit FOX channels.
Other networks in FOX's television guide are observations, not first-party confirmations.
FOX's Nuxt payload is a JSON reference table, not executable JavaScript; only the small set of
event fields below is read. If its shape changes, the existing uncertain listing survives.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlencode
from zoneinfo import ZoneInfo

from provider_utils import document, report, fetch_document, unavailable, observe, reject, confirm


SCORES_URL = "https://www.foxsports.com/soccer/liga-mx/scores?date={day}"
TV_URL = "https://www.foxsports.com/event-schedule"
TV_API = "https://api.foxsports.com/bifrost/v1/topevents/broadcast/eventschedule"
ET = ZoneInfo("America/New_York")
MAX_PAGE_BYTES = 2_000_000
CHANNELS = {"fox": "FOX", "fs1": "FS1", "fs2": "FS2", "fox deportes": "Fox Deportes"}
TV_CHANNELS = {"FOX": "FOX", "FS1": "FS1", "FS2": "FS2", "FOXDEP": "Fox Deportes"}
TIME_TOLERANCE = timedelta(minutes=15)
ALIASES = {"tigres uanl": "tigres", "atletico de san luis": "atletico san luis",
           "santos": "santos laguna", "pumas": "pumas unam", "club tijuana": "tijuana"}


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
    event_id: str = ""
    home_uri: str = ""
    away_uri: str = ""


@dataclass(frozen=True)
class Broadcast:
    home: str
    away: str
    utc: datetime
    channels: tuple[str, ...]
    raw_channels: tuple[str, ...]
    url: str
    event_id: str


def _name(value):
    """Fold accents and whitespace, without guessing abbreviated or renamed clubs."""
    value = unicodedata.normalize("NFKD", value).casefold()
    folded = " ".join("".join(c for c in value if not unicodedata.combining(c)).split())
    return ALIASES.get(folded, folded)


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
                                _source_url(text(link, "webUrl"), identity.group(1)),
                                identity.group(1), home_uri, away_uri))
    return listings


def schedule_api_key(page):
    """Read the public site's current client configuration without executing JavaScript."""
    text = document(page)
    configs = re.findall(r"\bbifrost:\{([^{}]{1,4000})\}", text)
    if len(configs) != 1:
        raise ValueError("FOX public schedule configuration missing or ambiguous")
    keys = re.findall(r'\bapiKey:"([A-Za-z0-9_-]{16,200})"', configs[0])
    paths = re.findall(r'\bapiPath:"([^"]+)"', configs[0])
    if len(keys) != 1 or paths != ["https://api.foxsports.com/bifrost/v1/"]:
        raise ValueError("FOX public schedule endpoint changed")
    return keys[0]


def parse_tv_schedule(page, day, scoreboard):
    """Join dated broadcast events to independently named scoreboard teams by publisher IDs.

    FOX mixes foreign, shorthand, and third-party codes into its guide. Only explicit
    FOX-owned US channels are eligible for confirmation; all other codes stay observations.
    """
    payload = json.loads(document(page))
    if not isinstance(payload, dict) or payload.get("title") != "DAILY SPORTS TV SCHEDULE":
        raise ValueError("FOX television schedule changed")
    events, filters = payload.get("events"), payload.get("filters")
    if not isinstance(events, list) or len(events) > 2000 or not isinstance(filters, list):
        raise ValueError("FOX television schedule is not bounded")
    selected = [item for group in filters if isinstance(group, dict) and group.get("id") == "dateFilter"
                for item in group.get("items", []) if isinstance(item, dict) and item.get("selected") is True]
    midnight = datetime.fromisoformat(day).replace(tzinfo=ET).astimezone(timezone.utc)
    if (len(selected) != 1 or selected[0].get("parameters") != {"date": day} or
            _kickoff(selected[0].get("date")) != midnight):
        raise ValueError("FOX television schedule returned a different day")
    listings = []
    for event in events:
        if not isinstance(event, dict) or event.get("template") != "broadcast":
            continue
        if event.get("filterValue") != {"eventFilter": "Soccer - Liga MX"}:
            continue
        # This feed describes live/upcoming fixtures, not channel playback sessions.
        # Matching original kickoff below prevents a repeat airing being marked live.
        title = event.get("title", "")
        if (type(event.get("eventStatus")) is not int or event["eventStatus"] not in (1, 2) or
                not isinstance(title, str) or re.search(r"\b(replay|repeat|encore|highlights)\b", title, re.I) or
                any(event.get(key) is True for key in ("isReplay", "isRepeat", "replay"))):
            continue
        utc = _kickoff(event.get("eventTime"))
        link = event.get("entityLink")
        if utc is None or utc.astimezone(ET).date().isoformat() != day or not isinstance(link, dict):
            continue
        content = link.get("contentUri")
        identity = re.fullmatch(r"soccer/liga_mx/events/(\d+)", content or "")
        tokens = link.get("layout", {}).get("tokens", {})
        if (not identity or not isinstance(tokens, dict) or tokens.get("eventUri") != content or
                tokens.get("id") != identity[1] or link.get("contentType") != "event"):
            continue
        found = [item for item in scoreboard if item.event_id == identity[1] and
                 item.home_uri == tokens.get("homeUri") and item.away_uri == tokens.get("awayUri")]
        if len(found) != 1 or abs(found[0].utc - utc) > TIME_TOLERANCE:
            continue
        codes = event.get("tvStations")
        if not isinstance(codes, list) or len(codes) > 20 or any(not isinstance(code, str) or len(code) > 50 for code in codes):
            codes = []
        listings.append(Broadcast(found[0].home, found[0].away, utc,
                                  tuple(sorted({TV_CHANNELS[code] for code in codes if code in TV_CHANNELS})),
                                  tuple(codes), _source_url(link.get("webUrl"), identity[1]), identity[1]))
    return listings


def enrich(builder, matches, fetch):
    """Supplement every eligible Liga MX fixture, preserving existing outlet evidence.

    The TV guide is bounded by Eastern dates and checked against FOX scoreboard IDs.
    A small airtime tolerance never changes the canonical kickoff and never permits
    more than one candidate or a replay. Missing/secondary channels remain unresolved.
    """
    candidates = [match for match in matches if match.league == "mex.1" and
                  match.time_valid and match.utc.utcoffset() is not None and
                  match.state in ("pre", "in") and match.status.lower() not in builder.VOID_STATUSES]
    result = report(candidates, TV_URL)
    if not candidates:
        return result
    days = {}
    for match in candidates:
        days.setdefault(match.utc.astimezone(ET).date().isoformat(), []).append(match)
    api_key, config_checked = None, False
    for day, day_matches in sorted(days.items()):
        score_url = SCORES_URL.format(day=day)
        tv_url = TV_URL + "?date=" + day
        try:
            scores = parse_schedule(fetch_document(fetch, score_url, result))
            observe(result, scores, score_url)
        except (ValueError, UnicodeError, RecursionError):
            unavailable(result, day_matches, reason="scoreboard_unavailable", url=score_url)
            continue
        if not config_checked:
            config_checked = True
            try:
                api_key = schedule_api_key(fetch_document(fetch, TV_URL, result))
            except (ValueError, UnicodeError, RecursionError):
                unavailable(result, candidates, reason="tv_configuration_unavailable", url=TV_URL)
        broadcasts = []
        if api_key:
            api_url = TV_API + "?" + urlencode({"date": day.replace("-", ""), "apikey": api_key})
            try:
                broadcasts = parse_tv_schedule(fetch_document(fetch, api_url, result), day, scores)
                observe(result, broadcasts, tv_url, complete=True)
            except (ValueError, UnicodeError, RecursionError, AttributeError, TypeError):
                unavailable(result, day_matches, reason="tv_schedule_unavailable", url=tv_url)
        for match in day_matches:
            home, away = _name(match.home.name), _name(match.away.name)
            found = [item for item in broadcasts if item.home == home and item.away == away and
                     abs(item.utc - match.utc) <= TIME_TOLERANCE]
            compared_time = found[0].utc if len(found) == 1 else match.utc
            competing = {other.id for other in day_matches if _name(other.home.name) == home and
                         _name(other.away.name) == away and abs(other.utc - compared_time) <= TIME_TOLERANCE}
            if len(found) > 1 or len(competing) > 1:
                reject(result, "ambiguous_event", match=match)
                continue
            if found:
                item = found[0]
                result["per_match"][match.id]["observed_channels"] = list(item.raw_channels)
                if item.channels and item.url:
                    confirm(builder, match, item.channels, "FOX Sports", item.url, result,
                            source_event_id=item.event_id, broadcast_start=item.utc.isoformat(),
                            kickoff_difference_seconds=int((item.utc - match.utc).total_seconds()),
                            raw_channels=list(item.raw_channels), authority="primary")
                    continue
                reject(result, "secondary_or_unknown_channels" if item.raw_channels else "no_channels", match=match)
            # The dated scoreboard remains useful when it explicitly names a FOX channel.
            # This fallback still requires exact kickoff and unique publisher identity.
            exact = [item for item in scores if (item.home, item.away, item.utc) == (home, away, match.utc)]
            if len(exact) != 1:
                reject(result, "event_not_found" if not exact else "ambiguous_event", match=match)
                continue
            item = exact[0]
            if item.channel and item.url:
                confirm(builder, match, item.channel, "FOX Sports", item.url, result,
                        source_event_id=item.event_id, broadcast_start=item.utc.isoformat(), authority="primary")
            elif not found:
                reject(result, "no_verified_us_channels", match=match)
    return result
