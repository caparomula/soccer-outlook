"""Confirm Portuguese matches against beIN's public US Connect live-event listings.

The US Connect home links to this event catalogue. Its typed live-event entries supply teams
and competition, and each event page supplies a full date and UTC offset. beIN starts these
streams five minutes before kickoff; only an exact start or that observed five-minute lead
is accepted. English and Spanish streams of the same match are one coverage confirmation.
"""
from datetime import timedelta
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from provider_utils import (candidates, confirm, document, fetch_document, kickoff, name,
                            report, unavailable)

SCHEDULE_URL = "https://watch.beinsports-apps.com/upcoming-events"
MAX_PAGES = 5
ET = ZoneInfo("America/New_York")
ALIASES = {"vitoria guimaraes": "vitoria de guimaraes", "porto": "fc porto",
           "famalicao": "fc famalicao", "nacional": "c.d. nacional"}


def club(value):
    folded = name(value)
    return ALIASES.get(folded, folded)


def eastern_day(value):
    try:
        return value.astimezone(ET).date()
    except (ValueError, OverflowError):
        return None


def teams(title):
    if not isinstance(title, str):
        return None
    match = re.fullmatch(r"(.{1,100}) vs (.{1,100}) - Liga de Portugal Round #\s*\d+", title.strip())
    return (club(match[1]), club(match[2])) if match else None


def event_url(value):
    if not isinstance(value, str) or len(value) > 1024:
        return ""
    parsed = urlsplit(value)
    return value if (parsed.scheme == "https" and parsed.netloc == "watch.beinsports-apps.com" and
                     not parsed.query and not parsed.fragment and
                     re.fullmatch(r"/upcoming-events/events/[a-z0-9-]+", parsed.path)) else ""


class _Catalogue(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items, self.next_pages, self.recognized = {}, set(), False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and attrs.get("data-item-type") == "live_event":
            self.recognized = True
        if tag != "a":
            return
        href = attrs.get("href", "")
        if re.fullmatch(r"/upcoming-events\?html=1&page=[2-6]", href):
            self.next_pages.add(urljoin(SCHEDULE_URL, href))
        url = event_url(href)
        if not url:
            return
        try:
            props = json.loads(attrs.get("data-track-event-properties", ""))
        except (ValueError, TypeError):
            return
        if not isinstance(props, dict) or props.get("type") != "live_event":
            return
        pair = teams(props.get("label"))
        if pair:
            self.items.setdefault(url, set()).add(pair)


def parse_catalogue(page):
    parser = _Catalogue()
    parser.feed(document(page))
    if not parser.recognized:
        raise ValueError("beIN live-event catalogue missing")
    if len(parser.next_pages) > 1:
        raise ValueError("Ambiguous beIN pagination")
    return parser.items, next(iter(parser.next_pages), "")


class _Event(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical, self.starts, self.headings = [], [], []
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical.append(attrs.get("href"))
        if attrs.get("id") == "live-status-text":
            self.starts.append(kickoff(attrs.get("data-live-event-scheduled-at")))
        if tag == "h1":
            self.in_heading = True
            self.headings.append("")

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False

    def handle_data(self, data):
        if self.in_heading:
            self.headings[-1] += data


def parse_event(page, url):
    parser = _Event()
    parser.feed(document(page))
    parser.headings = [heading.strip() for heading in parser.headings if heading.strip()]
    if (parser.canonical != [url] or len(parser.starts) != 1 or parser.starts[0] is None or
            len(parser.headings) != 1 or not teams(parser.headings[0])):
        raise ValueError("beIN dated live event missing or ambiguous")
    return (*teams(parser.headings[0]), parser.starts[0], url)


def enrich(builder, matches, fetch):
    games = candidates(builder, matches, "por.1", "beIN Sports Connect")
    result = report(games, SCHEDULE_URL)
    if not games:
        return result
    all_items, seen, url, complete = {}, set(), SCHEDULE_URL, False
    while url and len(seen) < MAX_PAGES:
        if url in seen:
            break
        seen.add(url)
        try:
            items, next_url = parse_catalogue(fetch_document(fetch, url, result))
        except (ValueError, UnicodeError, RecursionError):
            unavailable(result, games)
            return result
        for item_url, identities in items.items():
            all_items.setdefault(item_url, set()).update(identities)
        if not next_url:
            complete = True
        url = next_url
    event_cache = {}
    for match in games:
        pair = (club(match.home.name), club(match.away.name))
        urls = [item_url for item_url, identities in all_items.items() if identities == {pair}]
        same_day, failed = [], False
        for item_url in urls:
            if item_url not in event_cache:
                try:
                    event_cache[item_url] = parse_event(fetch_document(fetch, item_url, result), item_url)
                except (ValueError, UnicodeError, RecursionError):
                    event_cache[item_url] = None
                    result["failed"] += 1
            event = event_cache[item_url]
            if event is None:
                failed = True
            elif event[:2] == pair and eastern_day(event[2]) is not None and eastern_day(event[2]) == eastern_day(match.utc):
                same_day.append(event)
        # Distinct language streams may confirm the same start, but conflicting starts cannot.
        if (same_day and not failed and len({event[2] for event in same_day}) == 1 and
                match.utc - same_day[0][2] in (timedelta(0), timedelta(minutes=5))):
            confirm(builder, match, "beIN Sports Connect", "beIN Sports Connect", same_day[0][3], result)
        elif failed or not complete:
            result["per_match"][match.id]["status"] = "unavailable"
    return result
