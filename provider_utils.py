"""Small shared checks for optional public broadcaster listings."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import unicodedata
import xml.etree.ElementTree as ET


MAX_BYTES = 2_000_000


def name(value):
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def kickoff(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def document(page):
    if not isinstance(page, (bytes, str)) or len(page) > MAX_BYTES:
        raise ValueError("Broadcaster document missing or too large")
    return page.decode("utf-8") if isinstance(page, bytes) else page


class JsonScript(HTMLParser):
    def __init__(self, script_id):
        super().__init__(convert_charrefs=False)
        self.script_id, self.active, self.count, self.parts = script_id, False, 0, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == self.script_id:
            self.count += 1
            self.active = attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def read(self, page):
        self.feed(document(page))
        if self.count != 1 or not self.parts:
            raise ValueError("Broadcaster JSON payload missing or ambiguous")
        return json.loads("".join(self.parts))


def xml(page, kind):
    text = document(page)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations unsupported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("Malformed broadcaster XML") from exc
    if root.tag != "note" or root.get("type") != kind:
        raise ValueError("Unexpected broadcaster XML response")
    return root


def candidates(builder, matches, league, rule):
    return [m for m in matches if m.league == league and not m.outlets and m.rule and
            m.rule.label == rule and m.time_valid and m.utc.utcoffset() is not None and
            m.status.lower() not in builder.VOID_STATUSES]


def report(matches, url):
    return dict(requested=0, confirmed=0, unmatched=len(matches), failed=0,
                per_match={m.id: dict(status="unlisted", url=url) for m in matches})


def fetch_document(fetch, url, result):
    result["requested"] += 1
    try:
        return fetch(url)
    except Exception:
        return None


def unavailable(result, matches):
    result["failed"] += 1
    for m in matches:
        result["per_match"][m.id]["status"] = "unavailable"


def confirm(builder, match, channel, provider, url, result):
    outlet = builder.map_outlet(channel, match.league)
    if not outlet.known:
        return
    match.outlets, match.rule, match.hint = [outlet], None, ""
    match.broadcast_source, match.broadcast_url = provider, url
    match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
    result["confirmed"] += 1
    result["unmatched"] -= 1
    result["per_match"][match.id] = dict(status="confirmed", url=url)
