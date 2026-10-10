"""Read ViX's public US sports listings without logging in or requesting video streams.

Only a rendered live-event link, Liga MX identity and exact kickoff can confirm coverage.
The US page's event names use two established club aliases, recorded explicitly below.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import unicodedata


SCHEDULE_URL = "https://vix.com/es-es/deportes"
MAX_PAGE_BYTES = 2_000_000
ALIASES = {"chivas": "guadalajara", "santos laguna": "santos"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.count = 0
        self.parts = []
        self.links = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NEXT_DATA__":
            self.count += 1
            self.active = attrs.get("type") == "application/json"
        if tag == "a" and isinstance(attrs.get("href"), str):
            self.links.add(attrs["href"])

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
    return ALIASES.get(folded, folded)


def _kickoff(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def parse_schedule(page):
    if not isinstance(page, (bytes, str)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("ViX schedule is missing or too large")
    parser = _Page()
    parser.feed(page.decode("utf-8") if isinstance(page, bytes) else page)
    if parser.count != 1 or not parser.parts:
        raise ValueError("ViX schedule has no unique JSON payload")
    payload = json.loads("".join(parser.parts))
    props = payload.get("props", {}) if isinstance(payload, dict) else {}
    if not isinstance(props, dict):
        raise ValueError("ViX page properties changed")
    state, table = props.get("initialState"), props.get("apolloState")
    if (not isinstance(state, dict) or state.get("requestCountryCode") != "US" or
            not isinstance(table, dict) or len(table) > 20_000):
        raise ValueError("ViX schedule is not a bounded US listing")

    listings = []
    for event in table.values():
        if not isinstance(event, dict) or event.get("__typename") != "SportsEvent":
            continue
        tournament_ref = event.get("tournament")
        if not isinstance(tournament_ref, dict) or not isinstance(tournament_ref.get("__ref"), str):
            continue
        tournament = table.get(tournament_ref["__ref"])
        if (not isinstance(tournament, dict) or tournament.get("__typename") != "SportsTournament" or
                tournament.get("name") not in ("Liga MX (Apertura)", "Liga MX (Clausura)", "Liga MX")):
            continue
        name, identity, playback = event.get("name"), event.get("id"), event.get("playbackData")
        if (not isinstance(name, str) or len(name) > 250 or
                not isinstance(identity, str) or not re.fullmatch(r"transmission:matchid:\d{1,20}", identity) or
                not isinstance(playback, dict) or playback.get("__typename") != "LiveEventPlaybackData"):
            continue
        teams = name.split(" vs. ")
        kickoff = _kickoff(playback.get("kickoffDate"))
        if len(teams) != 2 or not all(team.strip() for team in teams) or kickoff is None:
            continue
        path = "/live/" + identity.replace(":", "-")
        badges = event.get("badges")
        published = (isinstance(badges, list) and any(b in ("PREMIUM", "FREE") for b in badges)
                     and (path in parser.links or "https://vix.com" + path in parser.links))
        # An unusable duplicate still counts toward ambiguity rather than confirming its sibling.
        listings.append(Listing(_name(teams[0]), _name(teams[1]), kickoff,
                                "https://vix.com" + path if published else ""))
    return listings


def enrich(builder, matches, fetch):
    """Confirm missing ViX listings in place; ``fetch(url)`` returns bytes or None."""
    candidates = [m for m in matches if m.league == "mex.1" and not m.outlets and m.rule
                  and m.rule.label == "ViX" and m.time_valid and m.state in ("pre", "in")
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
        outlet = builder.map_outlet("ViX", match.league)
        if not outlet.known:
            report["unmatched"] += 1
            report["per_match"][match.id] = {"status": "unavailable", "url": SCHEDULE_URL}
            continue
        match.outlets = [outlet]
        match.rule = None
        match.hint = ""
        match.broadcast_source = "ViX"
        match.broadcast_url = found[0].url
        match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
        report["confirmed"] += 1
        report["per_match"][match.id] = {"status": "confirmed", "url": match.broadcast_url}
    return report
