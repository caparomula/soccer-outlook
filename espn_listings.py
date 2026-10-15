"""Discover US broadcasts in Watch ESPN's public airing schedule.

Watch dates are Eastern calendar days; programme starts can precede kickoff. Accept only
explicit channels, known competitions and exact team identities (including reviewed aliases).
An absent airing is not proof that a match is unavailable, and a replay is never a live match.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

from provider_utils import WrongCountryError, confirm, fetch_document, observe, reject, report, unavailable


SOCCER_ID = "119cfa41-71d4-39bf-a790-6273a52b0259"
SCHEDULE_URL = "https://www.espn.com/watch/schedule/_/type/{kind}/categoryId/" + SOCCER_ID
# This is the date URL emitted by Watch's own soccer filter. Adding endDate currently
# returns an empty schedule even when the linked startDate page contains airings.
DATED_URL = SCHEDULE_URL + "/startDate/{day}"
SCHEDULE_ZONE = ZoneInfo("America/New_York")
PREGAME = timedelta(minutes=15)
MAX_PAGE_BYTES = 4_000_000
PAYLOAD = "window['__espnfitt__']="
LEAGUES = {
    "esp.1": {"Spanish LALIGA", "LALIGA", "LaLiga"},
    "eng.fa": {"English FA Cup", "FA Cup"},
    "esp.copa_del_rey": {"Spanish Copa del Rey", "Copa del Rey"},
    "ned.1": {"Dutch Eredivisie", "Eredivisie"},
    "usa.usl.1": {"USL Championship"},
    "usa.usl.l1": {"USL League One"},
}
CHANNELS = {name.casefold(): name for name in (
    "ESPN+", "ESPN", "ESPN2", "ESPNU", "ESPNEWS", "ESPN Deportes", "ESPN Unlimited")}
CHANNELS["espn on abc"] = "ABC"

# Explicit Watch/scoreboard names and ESPN team IDs observed in the 2026-10-15 captures.
# Keep these competition-scoped; stripping arbitrary FC/CF/RC words is unsafe for clubs
# with similar names. Unknown names continue to require exact normalized equality.
TEAM_ALIASES = {
    "esp.1": {
        "90": ("Deportivo", "Deportivo A Coruña"),
        "1538": ("Levante", "Levante UD"),
        "88": ("Espanyol", "RCD Espanyol"),
        "1068": ("Atlético Madrid", "Atlético de Madrid"),
        "102": ("Villarreal", "Villarreal CF"),
        "3751": ("Elche", "Elche CF"),
        "244": ("Real Betis",),
        "83": ("Barcelona", "FC Barcelona"),
        "94": ("Valencia", "Valencia CF"),
        "93": ("Athletic Club",),
    },
    "usa.usl.1": {
        "17827": ("Pittsburgh Riverhounds", "Pittsburgh Riverhounds SC"),
        "20687": ("Oakland Roots", "Oakland Roots SC"),
        "21370": ("Monterey Bay", "Monterey Bay FC"),
        "21822": ("Lexington", "Lexington Sporting Club"),
    },
    "usa.usl.l1": {
        "21821": ("One Knoxville", "One Knoxville SC"),
        "19964": ("Greenville Triumph", "Greenville Triumph SC"),
        "131580": ("Fort Wayne", "Fort Wayne FC"),
        "20000": ("Chattanooga Red Wolves", "Chattanooga Red Wolves SC"),
    },
    "ned.1": {
        "148": ("PSV Eindhoven", "PSV"),
        "142": ("Feyenoord Rotterdam", "Feyenoord"),
        "2726": ("ADO Den Haag",),
        "3735": ("Telstar",),
    },
}


def _name(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _team_key(league, value, identity=""):
    normalized = _name(value)
    for team_id, aliases in TEAM_ALIASES.get(league, {}).items():
        if normalized in {_name(alias) for alias in aliases}:
            # A conflicting explicit ID must not be rescued by a familiar club name.
            return "id:" + team_id if not identity or str(identity) == team_id else "conflicting-id:" + str(identity)
    return "name:" + normalized


def _teams(title, league):
    # These are observed language/matchday wrappers, not generic title cleanup. Thus
    # Highlights, Replay, women's and reserve-team labels remain part of the identity.
    title = re.sub(r"^En Español-\s*", "", title)
    title = re.sub(r" \(Matchday #\d{1,2}\)$", "", title)
    names = re.split(r"\s+vs\.?\s+", title, flags=re.IGNORECASE)
    if len(names) != 2 or not all(name.strip() for name in names):
        return frozenset()
    return frozenset(_team_key(league, name) for name in names)


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class Listing:
    teams: frozenset
    utc: datetime
    league: str
    channels: tuple
    url: str
    identity: str = ""


def parse_schedule(page, expected_day=None):
    """Read the US schedule's airing groups; malformed/non-US responses raise ValueError."""
    if not isinstance(page, (bytes, str)) or len(page) > MAX_PAGE_BYTES:
        raise ValueError("Watch ESPN schedule is missing or too large")
    if isinstance(page, bytes):
        page = page.decode("utf-8")
    if page.count(PAYLOAD) != 1:
        raise ValueError("Watch ESPN schedule has no unique JSON payload")
    data = json.JSONDecoder().raw_decode(page.split(PAYLOAD, 1)[1])[0]
    source = data.get("page", {}) if isinstance(data, dict) else {}
    content = source.get("content", {}) if isinstance(source, dict) else {}
    watch = content.get("watch", {}) if isinstance(content, dict) else {}
    personal = watch.get("p13n") if isinstance(watch, dict) else None
    country = personal.get("countryCode") if isinstance(personal, dict) else None
    if isinstance(country, str) and country.strip() and country.strip().casefold() != "us":
        raise WrongCountryError("Watch ESPN explicitly returned a non-US schedule")
    if (not isinstance(source, dict) or source.get("subType") != "schedule" or not isinstance(watch, dict) or
            not isinstance(personal, dict) or country != "us" or
            not isinstance(watch.get("arngs"), list)):
        raise ValueError("Watch ESPN response is not a US airing schedule")
    if expected_day is not None:
        filters = watch.get("fltrs", {}).get("sptFlts", []) if isinstance(watch.get("fltrs", {}), dict) else []
        soccer_links = [item.get("url", "") for item in filters if isinstance(item, dict) and item.get("value") == SOCCER_ID] if isinstance(filters, list) else []
        expected_path = DATED_URL.format(kind="upcoming", day=expected_day).removeprefix("https://www.espn.com")
        if soccer_links and any(link != expected_path for link in soccer_links):
            raise ValueError("Watch ESPN returned a different schedule date")
    listings = []
    groups = watch["arngs"]
    if len(groups) > 100:
        raise ValueError("Watch ESPN schedule has too many groups")
    for group in groups:
        if not isinstance(group, dict) or group.get("nme") != "Soccer":
            continue
        # ESPN nests professional competitions beneath Soccer, rather than directly under arngs.
        subgroups = group.get("sctgys", [])
        if not isinstance(subgroups, list) or len(subgroups) > 100:
            raise ValueError("Watch ESPN soccer groups are malformed")
        for subgroup in subgroups:
            if not isinstance(subgroup, dict):
                continue
            name = subgroup.get("nme")
            league = next((key for key, names in LEAGUES.items() if isinstance(name, str) and name in names), "")
            airings = subgroup.get("arngs", [])
            if not isinstance(airings, list) or len(airings) > 1000:
                raise ValueError("Watch ESPN airings are malformed")
            if not league:
                continue
            for event in airings:
                if not isinstance(event, dict) or event.get("tp") not in ("live", "upcoming"):
                    continue
                start = _time(event.get("stme"))
                title = event.get("nme")
                categories = event.get("sctgys")
                sports = event.get("ctgys")
                if (start is None or not isinstance(title, str) or len(title) > 512 or
                        not isinstance(categories, list) or
                        not any(isinstance(c, dict) and isinstance(c.get("name"), str) and
                                c["name"] in LEAGUES[league] for c in categories) or
                        not isinstance(sports, list) or
                        not any(isinstance(c, dict) and c.get("id") == SOCCER_ID for c in sports)):
                    continue
                teams = _teams(title, league)
                if len(teams) != 2:
                    continue
                identity = event.get("id", "")
                href = event.get("hrf", "")
                valid_id = isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", identity)
                url = ("https://www.espn.com" + href if valid_id and
                       href == "/watch/player/_/id/" + identity else "")
                broadcasts = event.get("bcsts", [])
                channels = tuple(sorted({CHANNELS[name.casefold()] for b in broadcasts
                                         if isinstance(b, dict) and isinstance(name := b.get("nme"), str)
                                         and name.casefold() in CHANNELS})) if isinstance(broadcasts, list) else ()
                # Invalid channels/links still count toward duplicate ambiguity.
                listings.append(Listing(teams, start, league, channels, url, identity if valid_id else ""))
    return listings


def candidate_urls(match):
    """Use the Eastern airing date, including a previous day only across pregame midnight."""
    if match.state == "in":
        return (SCHEDULE_URL.format(kind="live"),)
    days = {match.utc.astimezone(SCHEDULE_ZONE).date(),
            (match.utc - PREGAME).astimezone(SCHEDULE_ZONE).date()}
    return tuple(DATED_URL.format(kind="upcoming", day=day.strftime("%Y%m%d")) for day in sorted(days))


def enrich(builder, matches, fetch):
    """Discover compatible simulcasts without changing kickoff or erasing other outlets."""
    games = [match for match in matches if match.league in LEAGUES and match.time_valid and
             match.state in ("pre", "in") and match.status.lower() not in builder.VOID_STATUSES and
             match.utc.utcoffset() is not None]
    result = report(games, SCHEDULE_URL.format(kind="upcoming"))
    result["timezone"] = "America/New_York"
    urls_by_id = {game.id: candidate_urls(game) for game in games}
    schedules, wrong_country = {}, set()
    for url in sorted({url for urls in urls_by_id.values() for url in urls}):
        page = fetch_document(fetch, url, result)
        try:
            listings = parse_schedule(page, url.rsplit("/startDate/", 1)[1] if "/startDate/" in url else None)
        except WrongCountryError:
            unavailable(result, [], reason="wrong_country", url=url)
            schedules[url] = None
            wrong_country.add(url)
            continue
        except (ValueError, UnicodeError, RecursionError):
            unavailable(result, [], reason="unrecognized_or_unavailable_schedule", url=url)
            schedules[url] = None
            continue
        schedules[url] = listings
        observe(result, listings, url, complete=False)
        if not listings:
            reject(result, "no_supported_airings")
    for game in games:
        urls = urls_by_id[game.id]
        country_url = next((url for url in urls if url in wrong_country), None)
        if country_url:
            # A missing or malformed secondary page cannot turn an explicit country rejection
            # into an outage eligible for cached US evidence. A new US confirmation may still win.
            result["per_match"][game.id] = {"status": "unavailable", "url": country_url, "reason": "wrong_country"}
        available = [schedules[url] for url in urls if schedules[url] is not None]
        if not available:
            if not country_url:
                result["per_match"][game.id] = {"status": "unavailable", "url": urls[0],
                                                 "reason": "unrecognized_or_unavailable_schedule"}
            continue
        teams = frozenset(_team_key(game.league, team.name, team.id) for team in (game.home, game.away))
        entries = {item for page_entries in available for item in page_entries}
        identities = {}
        for item in entries:
            identities.setdefault(item.identity, set()).add(item)
        conflicting_ids = {identity for identity, records in identities.items() if len(records) > 1}
        found = [item for item in entries if item.teams == teams and item.league == game.league and
                 timedelta(0) <= game.utc - item.utc <= PREGAME]
        # The same airing can occur on overlapping pages. Deduplicate identical evidence,
        # but reject an ID reused for contradictory identity/channel/time information.
        unique = set(found)
        if (not unique or not all(item.channels and item.url and item.identity for item in unique) or
                any(item.identity in conflicting_ids for item in unique)):
            if not country_url:
                result["per_match"][game.id]["url"] = urls[0]
            reject(result, "invalid_or_conflicting_airings" if unique else "not_seen_in_partial_catalogue",
                   match=None if country_url else game)
            continue
        channels = sorted({channel for item in unique for channel in item.channels})
        if not all(builder.map_outlet(channel, game.league).known for channel in channels):
            reject(result, "unmapped_channel", match=None if country_url else game)
            continue
        # Each channel keeps its own observed programme time and player link. Calling
        # confirm repeatedly merges routes and counts a match only once.
        for listing in sorted(unique, key=lambda item: (item.utc, item.identity)):
            confirm(builder, game, listing.channels, "Watch ESPN", listing.url, result,
                    source_event_id=listing.identity, broadcast_start=listing.utc.isoformat())
    return result
