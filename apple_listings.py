"""Read live and upcoming match cards on Apple TV's public US MLS channel page.

The server-rendered US storefront supplies each match's full names, UTC kickoff and broadcast
state. Editorial stories and replay cards are deliberately excluded; no Apple account is used.
"""
import re
from urllib.parse import urlsplit, urlunsplit

from provider_utils import (JsonScript, candidates, confirm, fetch_document, kickoff, name,
                            report, unavailable)

SCHEDULE_URL = "https://tv.apple.com/us/channel/mls/tvs.sbd.7000"
ALIASES = {"orlando city": "orlando city sc", "atlanta united": "atlanta united fc",
           "minnesota united": "minnesota united fc", "houston dynamo": "houston dynamo fc",
           "vancouver whitecaps fc": "vancouver whitecaps", "los angeles football club": "lafc"}


def club(value):
    folded = name(value)
    return ALIASES.get(folded, folded)


def parse_schedule(page):
    payload = JsonScript("serialized-server-data").read(page)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Apple server data missing")
    pages = [row.get("data") for row in payload["data"] if isinstance(row, dict) and
             isinstance(row.get("intent"), dict) and
             row["intent"].get("$kind") == "ChannelPageIntent" and
             row["intent"].get("storefront") == "us" and
             row["intent"].get("id") == "tvs.sbd.7000"]
    if len(pages) != 1 or not isinstance(pages[0], dict):
        raise ValueError("Apple US MLS page missing or ambiguous")
    data = pages[0]
    if data.get("canonicalURL") != SCHEDULE_URL or not isinstance(data.get("shelves"), list):
        raise ValueError("Unexpected Apple storefront")
    listings = {}
    for shelf in data["shelves"]:
        if not isinstance(shelf, dict) or shelf.get("$type") != "sportsCardLockup":
            continue
        for item in shelf.get("items", []):
            if not isinstance(item, dict) or item.get("$kind") != "SportingEventLockup":
                continue
            title, event_id = item.get("ariaLabel"), item.get("id")
            if not isinstance(event_id, str) or not re.fullmatch(r"umc\.cse\.[a-z0-9]+", event_id):
                continue
            values = listings.setdefault(event_id, set())
            if not isinstance(title, str) or item.get("broadcastState") not in ("upcoming", "live"):
                values.add(None)
                continue
            teams = re.fullmatch(r"(.{1,100}) vs\. (.{1,100})", title)
            badge, action = item.get("badge"), item.get("contextAction")
            if not teams or not isinstance(badge, dict) or not isinstance(action, dict):
                values.add(None)
                continue
            utc = kickoff(badge.get("isoDatetime"))
            url = action.get("url")
            if utc is None or not isinstance(url, str) or len(url) > 1024:
                values.add(None)
                continue
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "tv.apple.com" or
                    not re.fullmatch(r"/us/sporting-event/[a-z0-9-]+/umc\.cse\.[a-z0-9]+", parsed.path) or
                    parsed.path.rsplit("/", 1)[-1] != event_id):
                values.add(None)
                continue
            value = (club(teams[1]), club(teams[2]), utc,
                     urlunsplit(("https", "tv.apple.com", parsed.path, "", "")))
            # Identical repeated cards are normal; conflicting cards for one ID are not.
            values.add(value)
    return [item for values in listings.values() if len(values) == 1 and None not in values for item in values]


def enrich(builder, matches, fetch):
    games = candidates(builder, matches, "usa.1", "Apple TV")
    result = report(games, SCHEDULE_URL)
    if not games:
        return result
    try:
        listings = parse_schedule(fetch_document(fetch, SCHEDULE_URL, result))
    except (ValueError, UnicodeError, RecursionError, TypeError):
        unavailable(result, games)
        return result
    for match in games:
        found = [row for row in listings if row[:3] == (club(match.home.name), club(match.away.name), match.utc)]
        if len(found) == 1:
            confirm(builder, match, "Apple TV", "Apple TV", found[0][3], result)
    return result
