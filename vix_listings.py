"""Read ViX's public US sports listings without logging in or requesting video streams.

Liga MX identity and exact kickoff are required. Announced premium events can establish
future coverage before a playback link is rendered; they retain the sports-page source.
The US page's event names use two established club aliases, recorded explicitly below.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import unicodedata

from provider_utils import (candidates, confirm, fetch_document, observe, reject,
                            report, unavailable)


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
    announced: bool = False


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
        availability = event.get("vodAvailability")
        blocked = isinstance(availability, dict) and availability.get("isBlocked") is True
        starts = _kickoff(playback.get("scheduledStartTime"))
        ends = _kickoff(playback.get("scheduledEndTime"))
        announced = (isinstance(badges, list) and "PREMIUM" in badges and
                     isinstance(availability, dict) and
                     availability.get("__typename") == "ContentAvailability" and blocked and
                     availability.get("reason") == "BEFORE_START_DATE" and
                     starts is not None and ends is not None and starts <= kickoff < ends)
        # A region/rights block is never excused by an anchor or premium badge.
        allowed = not blocked or announced
        linked = (isinstance(badges, list) and any(b in ("PREMIUM", "FREE") for b in badges)
                  and (path in parser.links or "https://vix.com" + path in parser.links))
        url = ("https://vix.com" + path if linked else SCHEDULE_URL if announced else "") if allowed else ""
        # An unusable duplicate still counts toward ambiguity rather than confirming its sibling.
        listings.append(Listing(_name(teams[0]), _name(teams[1]), kickoff, url, announced))
    return listings


def enrich(builder, matches, fetch):
    """Discover ViX listings without inferring any linear-network simulcast."""
    games = candidates(builder, matches, "mex.1", "ViX")
    result = report(games, SCHEDULE_URL)
    if not games:
        return result
    try:
        listings = parse_schedule(fetch_document(fetch, SCHEDULE_URL, result))
    except (ValueError, UnicodeError, RecursionError):
        unavailable(result, games)
        return result
    observe(result, listings, SCHEDULE_URL, complete=False)
    for match in games:
        identity = (_name(match.home.name), _name(match.away.name), match.utc)
        found = [item for item in listings if (item.home, item.away, item.utc) == identity]
        if len(found) != 1 or not found[0].url:
            if found:
                reject(result, "ambiguous_event" if len(found) != 1 else "invalid_event", match=match)
            continue
        if match.state != "pre" and found[0].announced:
            reject(result, "future_announcement_for_live_match", match=match)
            continue
        confirm(builder, match, "ViX", "ViX", found[0].url, result,
                listing_state="announced" if found[0].announced else "linked")
    return result
