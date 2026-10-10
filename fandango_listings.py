"""Check Fandango's public Bundesliga schedule through its anonymous browse API.

This is the same unauthenticated navigation bootstrap and page request used by the public site.
Its short-lived browse token is fetched each time, never stored or supplied as a credential.
Only the live match row qualifies, with a full UTC start time; replays and goal shows do not.
"""
import re
from urllib.parse import urlencode

from provider_utils import (candidates, confirm, fetch_document, kickoff, name, report,
                            unavailable, xml)

SCHEDULE_URL = "https://athome.fandango.com/content/browse/uxpage/Bundesliga/405"
API_URL = "https://apicache.vudu.com/api2/"
NAV_URL = API_URL + "?_type=uxNavRequest&clientType=web&domain=vudu"
# Explicit differences between ESPN and this broadcaster, never substring matching.
ALIASES = {"fc koln": "fc cologne", "1. fc koln": "fc cologne", "sport-club freiburg": "sc freiburg",
           "fc schalke 04": "schalke 04", "fc union berlin": "1. fc union berlin",
           "sv werder bremen": "werder bremen", "sc paderborn 07": "sc paderborn 07",
           "fsv mainz 05": "mainz", "bayer 04 leverkusen": "bayer leverkusen",
           "hamburger sv": "hamburg sv", "fc bayern munich": "bayern munich"}


def club(value):
    folded = name(value)
    return ALIASES.get(folded, folded)


def parse_schedule(page):
    root = xml(page, "uxPage")
    if root.findtext("label") != "Bundesliga" or root.findtext("uxPageId") != "405":
        raise ValueError("Unexpected Fandango page")
    rows = [row for row in root.findall("rows/uxRow")
            if row.findtext("label") == "Live & Upcoming Bundesliga Matches"]
    if len(rows) != 1:
        raise ValueError("Live Bundesliga row missing or ambiguous")
    listings = []
    for item in rows[0].findall("elements/uxElement"):
        if item.findtext("elementType") != "content" or item.findtext("elementSubType") != "program":
            continue
        teams = re.fullmatch(r"(.{1,100}) (?:v\.|vs\.) (.{1,100})", item.findtext("label", ""))
        parts = [part.strip().split("=", 1) for part in item.findtext("description", "").split("|")]
        if any(len(part) != 2 for part in parts):
            continue
        fields = {key.strip(): value.strip() for key, value in parts if key}
        if len(fields) != len(parts):
            continue
        if not teams or any(fields.get(k) != v for k, v in
                            (("streamType", "live"), ("eventType", "match"), ("genre", "Sports"))):
            continue
        # Alternate feeds are not evidence for the regular match broadcast.
        if ":" in teams[1] or ":" in teams[2]:
            continue
        utc = kickoff(fields.get("startTime"))
        if utc is not None:
            listings.append((club(teams[1]), club(teams[2]), utc))
    complete = rows[0].findtext("elements/moreBelow") == "false"
    return listings, complete


def enrich(builder, matches, fetch):
    games = candidates(builder, matches, "ger.1", "Fandango")
    result = report(games, SCHEDULE_URL)
    if not games:
        return result
    try:
        nav = xml(fetch_document(fetch, NAV_URL, result), "uxNavResponse")
        token = nav.findtext("zToken", "")
        if not token or len(token) > 2048:
            raise ValueError("Anonymous browse token missing")
        url = API_URL + "?" + urlencode(dict(_type="uxPageGet", pageId="405", elementCount="30", rowCount="5", zToken=token))
        listings, complete = parse_schedule(fetch_document(fetch, url, result))
    except (ValueError, UnicodeError, RecursionError, TypeError):
        unavailable(result, games)
        return result
    for match in games:
        identity = (club(match.home.name), club(match.away.name), match.utc)
        if listings.count(identity) == 1:
            confirm(builder, match, "Fandango", "Fandango", SCHEDULE_URL, result)
        elif not complete:
            result["per_match"][match.id]["status"] = "unavailable"
    return result
