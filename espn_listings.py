"""Verify usual ESPN coverage against Watch ESPN's public US airing schedule.

The scoreboard and Watch schedule are separate ESPN products. Read only the latter's embedded
JSON, never execute its scripts, and require an explicit event-local channel, competition,
both club names and exact UTC start. A missing airing is not proof that a match is unavailable.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import unicodedata


SOCCER_ID = "119cfa41-71d4-39bf-a790-6273a52b0259"
SCHEDULE_URL = "https://www.espn.com/watch/schedule/_/type/{kind}/categoryId/" + SOCCER_ID
DATED_URL = SCHEDULE_URL + "/startDate/{day}/endDate/{day}"
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


def _name(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


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


def parse_schedule(page):
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
    if (not isinstance(source, dict) or source.get("subType") != "schedule" or not isinstance(watch, dict) or
            not isinstance(watch.get("p13n"), dict) or watch["p13n"].get("countryCode") != "us" or
            not isinstance(watch.get("arngs"), list)):
        raise ValueError("Watch ESPN response is not a US airing schedule")
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
                teams = re.split(r"\s+vs\.?\s+", title, flags=re.IGNORECASE)
                if len(teams) != 2 or not all(team.strip() for team in teams):
                    continue
                teams = frozenset(_name(team) for team in teams)
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
                listings.append(Listing(teams, start, league, channels, url))
    return listings


def enrich(builder, matches, fetch):
    """Fetch each needed UTC day once, preserving existing listings and uncertain source failures."""
    batches = {}
    for match in matches:
        if (match.outlets or not match.rule or match.rule.label != "ESPN+" or
                match.league not in LEAGUES or not match.time_valid or
                match.state not in ("pre", "in") or match.status.lower() in builder.VOID_STATUSES or
                match.utc.utcoffset() is None):
            continue
        url = (SCHEDULE_URL.format(kind="live") if match.state == "in" else
               DATED_URL.format(kind="upcoming", day=match.utc.astimezone(timezone.utc).strftime("%Y%m%d")))
        batches.setdefault(url, []).append(match)
    report = {"requested": 0, "confirmed": 0, "unmatched": 0, "failed": 0, "per_match": {}}
    for url, games in sorted(batches.items()):
        report["requested"] += 1
        try:
            page = fetch(url)
        except Exception:
            page = None
        try:
            listings = parse_schedule(page)
        except (ValueError, UnicodeError, RecursionError):
            report["failed"] += 1
            report["unmatched"] += len(games)
            report["per_match"].update({game.id: {"status": "unavailable", "url": url} for game in games})
            continue
        for game in games:
            report["per_match"][game.id] = {"status": "unlisted", "url": url}
            teams = frozenset((_name(game.home.name), _name(game.away.name)))
            found = [item for item in listings if (item.teams, item.utc, item.league) == (teams, game.utc, game.league)]
            if len(found) != 1 or not found[0].channels or not found[0].url:
                report["unmatched"] += 1
                continue
            listing = found[0]
            outlets = [builder.map_outlet(channel, game.league) for channel in listing.channels]
            if not all(outlet.known for outlet in outlets):
                report["unmatched"] += 1
                continue
            game.outlets, game.rule, game.hint = outlets, None, ""
            game.broadcast_source, game.broadcast_url = "Watch ESPN", listing.url
            game.service, game.basis, game.outlet = builder.evaluate(outlets, None, builder.OWNER)
            report["confirmed"] += 1
            report["per_match"][game.id] = {"status": "confirmed", "url": listing.url}
    return report
