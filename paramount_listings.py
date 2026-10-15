"""Discover Paramount+ coverage in its public US live-event listings.

The sports page's own carousel endpoint supplies event-local teams and times. A true
game kickoff must agree exactly. Where Paramount supplies only live broadcast airtime,
allow at most fifteen minutes of pregame coverage; never change ESPN's kickoff.
No login, playback token, or subscriber endpoint is used.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

from provider_utils import (WrongCountryError, confirm, fetch_document, observe, reject,
                            report as source_report, unavailable)


SCHEDULE_URL = "https://www.paramountplus.com/sports/"
EVENTS_URL = ("https://www.paramountplus.com/carousels/collections/live-and-upcoming/xhr/"
              "multichannel-events/offset/{offset}/limit/100/?timezone=America%2FNew_York"
              "&title=Live-%26-Upcoming&sportsDataVariant=true")
MAX_PAGE_BYTES = 2_000_000
MAX_EVENTS = 1000
ET = ZoneInfo("America/New_York")
# Slugs identify competitions, not rights. An individual event is still required.
LEAGUE_SLUGS = {
    "sco.1": "scottish-professional-football-league",
    "ita.1": "serie-a",
    "eng.w.1": "barclays-womens-super-league",
    "uefa.champions": "uefa-champions-league",
    "uefa.europa": "uefa-europa-league",
    "uefa.europa.conf": "uefa-europa-conference-league",
    "uefa.wchampions": "uefa-womens-champions-league",
    "eng.league_cup": "efl-cup",
    "concacaf.champions": "concacaf-champions-cup",
}
# Checked 10 October 2026: no Coppa Italia entry appears in the public sports
# navigation or live catalogue, and /shows/coppa-italia/ returns 404. Do not invent
# a competition slug or treat this missing mapping as proof of no broadcast.
UNVERIFIED_LEAGUES = {"ita.coppa_italia"}
# Explicit differences between titles and gameData in the 15 October US catalogue.
# Never remove arbitrary club prefixes or use substring matching to infer identity.
NAME_ALIASES = {"afc bournemouth": "bournemouth", "sk sturm graz": "sturm graz",
                "celta de vigo": "celta vigo", "olympique marseille": "marseille",
                "olympiakos piraeus": "olympiacos", "jagiellonia białystok": "jagiellonia bialystok"}


@dataclass(frozen=True)
class Listing:
    home: str
    away: str
    utc: datetime
    slug: str
    kickoff: bool
    url: str


def _text(value):
    return value if isinstance(value, str) and 0 < len(value) <= 512 else ""


def _name(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    # A period is typographic punctuation in names such as St. Johnstone.
    folded = " ".join("".join(c for c in value if not unicodedata.combining(c) and c != ".").split())
    return NAME_ALIASES.get(folded, folded)


def _iso_time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            return None
        utc = parsed.astimezone(timezone.utc)
        # Match the supported range of the catalogue's numeric timestamps, and
        # avoid overflow when malformed years are converted to Eastern dates.
        return utc if 2000 <= utc.year < 2100 else None
    except (ValueError, OverflowError):
        return None


def _milliseconds(value):
    if type(value) is not int or not 946684800000 <= value <= 4102444800000:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def _page_text(page):
    if not isinstance(page, (str, bytes)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("Paramount+ response is missing or too large")
    return page.decode("utf-8") if isinstance(page, bytes) else page


def is_us_page(page):
    """Do not treat a redirected international catalogue as US viewing evidence."""
    page = _page_text(page)
    regions = re.findall(r"CBS\.Registry\.region\s*=\s*\{([^{}]*)\}", page)
    if len(regions) == 1:
        country = re.search(r"\bproperty\s*:\s*(['\"])([A-Za-z]{2})\1", regions[0])
        locale = re.search(r"\blocale\s*:\s*(['\"])[A-Za-z]{2}-([A-Za-z]{2})\1", regions[0])
        if ((country and country[2].upper() != "US") or (locale and locale[2].lower() != "us") or
                re.search(r"\binternational\s*:\s*true\b", regions[0])):
            raise WrongCountryError("Paramount+ catalogue names a non-US region")
    return len(regions) == 1 and all(re.search(pattern, regions[0]) for pattern in (
        r"\bprefix\s*:\s*(['\"])\1\s*,",
        r"\blocale\s*:\s*(['\"])en-us\1\s*,",
        r"\bproperty\s*:\s*(['\"])US\1\s*,",
        r"\binternational\s*:\s*false\s*(?:,|$)",
    ))


def _event_url(item, slug):
    """Validate the observed live route independently of a generic sign-up href."""
    identity = _text(item.get("content_id"))
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", identity):
        return ""
    path = "/live-tv/stream/" + slug + "/" + identity
    return "https://www.paramountplus.com" + path if item.get("url") == path else ""


def parse_schedule(page):
    """Return validated listings, item count and total for one public carousel page."""
    payload = json.loads(_page_text(page))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("Paramount+ schedule request was unsuccessful")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Paramount+ schedule has no result")
    items, total = result.get("data"), result.get("total")
    if (not isinstance(items, list) or len(items) > 100 or type(total) is not int or
            not 0 <= total <= MAX_EVENTS or len(items) > total):
        raise ValueError("Paramount+ schedule has invalid pagination")
    listings = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Paramount+ schedule contains a malformed item")
        slug = _text(item.get("channelSlug"))
        if (slug not in LEAGUE_SLUGS.values() or item.get("contentType") != "event" or
                item.get("streamType") != "mpx_live" or
                not (item.get("isUpcoming") is True or item.get("isLive") is True)):
            continue
        title = _text(item.get("title"))
        teams = re.fullmatch(r"(.+?) vs\. (.+)", title)
        if not teams:
            continue
        home, away = (_name(value) for value in teams.groups())
        if not home or not away or home == away:
            continue
        game = item.get("gameData")
        has_kickoff = game is not None or "gameStartTimestamp" in item
        kickoff = None
        valid = True
        if has_kickoff:
            if not isinstance(game, dict):
                valid = False
            else:
                kickoff = _iso_time(game.get("scheduledTime"))
                home_team, away_team = game.get("homeTeam"), game.get("awayTeam")
                if (game.get("sportName") != "SOCCER" or not isinstance(home_team, dict) or
                        not isinstance(away_team, dict) or
                        _name(_text(home_team.get("mediumName"))) != home or
                        _name(_text(away_team.get("mediumName"))) != away or kickoff is None):
                    valid = False
                if "gameStartTimestamp" in item and _milliseconds(item["gameStartTimestamp"]) != kickoff:
                    valid = False
            # Keep malformed same-day duplicates for ambiguity detection only. Their
            # empty URL prevents them from confirming coverage, even via airtime.
            if kickoff is None:
                kickoff = _milliseconds(item.get("startTimestamp"))
        else:
            # startTimestamp is the advertised programme start. streamStartTimestamp
            # can be an earlier stream-opening time and must not be substituted.
            kickoff = _milliseconds(item.get("startTimestamp"))
        if kickoff is None:
            continue
        expected_href = "/shows/" + slug
        event_url = _event_url(item, slug)
        href = _text(item.get("href"))
        # Live catalogue cards use '/' as their logged-out sign-up target. Their
        # separately published event route still identifies the actual airing.
        valid = valid and (href.rstrip("/") == expected_href or
                           (href == "/" and bool(event_url)))
        listings.append(Listing(home, away, kickoff, slug, has_kickoff,
                                (event_url or SCHEDULE_URL) if valid else ""))
    return listings, len(items), total


def _matches(listing, match):
    if (listing.slug != LEAGUE_SLUGS.get(match.league) or
            (listing.home, listing.away) != (_name(match.home.name), _name(match.away.name))):
        return False
    if listing.kickoff:
        return listing.utc == match.utc
    return (listing.utc.astimezone(ET).date() == match.utc.astimezone(ET).date() and
            timedelta(0) <= match.utc - listing.utc <= timedelta(minutes=15))


def enrich(builder, matches, fetch):
    """Check supported competitions, including possible missing simulcasts."""
    candidates = [match for match in matches if (
        (match.league in LEAGUE_SLUGS or (match.rule and match.rule.label == "Paramount+")) and
        match.time_valid and match.state in ("pre", "in") and
        match.status.lower() not in builder.VOID_STATUSES and match.utc.utcoffset() is not None)]
    report = source_report(candidates, SCHEDULE_URL)
    if not candidates:
        return report

    def request(url):
        return fetch_document(fetch, url, report)

    try:
        if not is_us_page(request(SCHEDULE_URL)):
            raise ValueError("Paramount+ page is not the public US catalogue")
        listings, offset, expected_total = [], 0, None
        while True:
            page_url = EVENTS_URL.format(offset=offset)
            page_listings, count, total = parse_schedule(request(page_url))
            if expected_total is not None and total != expected_total:
                raise ValueError("Paramount+ schedule changed during pagination")
            expected_total = total
            listings.extend(page_listings)
            offset += count
            observe(report, page_listings, page_url, complete=offset == total)
            if offset == total:
                break
            if not count or offset > total:
                raise ValueError("Paramount+ schedule pagination is incomplete")
    except WrongCountryError:
        unavailable(report, candidates, reason="wrong_country")
        return report
    except Exception:
        unavailable(report, candidates)
        return report

    for match in candidates:
        # Repeated or conflicting same-day entries for the same pair are ambiguous.
        found = [listing for listing in listings if (
            listing.slug == LEAGUE_SLUGS.get(match.league) and
            (listing.home, listing.away) == (_name(match.home.name), _name(match.away.name)) and
            listing.utc.astimezone(ET).date() == match.utc.astimezone(ET).date())]
        if match.league not in LEAGUE_SLUGS:
            # An unmapped new competition must not be described as absent from the source.
            report["per_match"][match.id]["status"] = "unavailable"
            reject(report, "unsupported_competition", match=match)
            continue
        if len(found) != 1 or not found[0].url or not _matches(found[0], match):
            if found:
                reason = "ambiguous_event" if len(found) != 1 else (
                    "invalid_event" if not found[0].url else "kickoff_mismatch")
                reject(report, reason, match=match)
            continue
        listing = found[0]
        confirm(builder, match, "Paramount+", "Paramount+", listing.url, report)
    return report
