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


def eligible(builder, match):
    """A public listing may supplement any valid fixture, regardless of its current outlets."""
    return (match.state in ("pre", "in") and match.time_valid and isinstance(match.utc, datetime) and
            match.utc.utcoffset() is not None and match.status.lower() not in builder.VOID_STATUSES)


def candidates(builder, matches, league, rule=None):
    # ``rule`` remains accepted for existing adapters. A usual-rights rule is not a
    # prerequisite: it would miss simulcasts and matches with no current fallback.
    return [m for m in matches if m.league == league and eligible(builder, m)]


def _horizon(times):
    times = [time.astimezone(timezone.utc) for time in times
             if isinstance(time, datetime) and time.utcoffset() is not None]
    return {"start": min(times).isoformat(), "end": max(times).isoformat()} if times else None


def _diagnostics(result):
    return result.setdefault("diagnostics", dict(candidate_matches=len(result.get("per_match", {})),
        requested_horizon=None, observed_horizon=None, parsed_pages=0, observed_listings=0,
        requests=[], observations=[], reasons={}))


def report(matches, url):
    matches = list(matches)
    result = dict(requested=0, confirmed=0, unmatched=len(matches), failed=0,
                  per_match={m.id: dict(status="unlisted", url=url) for m in matches})
    diagnostics = _diagnostics(result)
    diagnostics["requested_horizon"] = _horizon(m.utc for m in matches if m.time_valid)
    return result


def _public_url(url):
    """Never persist anonymous bootstrap tokens or URL credentials in public diagnostics."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not any(word in key.casefold() for word in ("token", "secret", "password", "authorization", "api_key", "apikey"))]
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), ""))


def reject(result, reason, count=1, match=None):
    """Count a bounded machine-readable reason without changing coverage evidence."""
    if not isinstance(reason, str) or not reason or len(reason) > 100:
        raise ValueError("Invalid broadcaster diagnostic reason")
    reasons = _diagnostics(result)["reasons"]
    reasons[reason] = reasons.get(reason, 0) + max(0, int(count))
    if match is not None:
        item = result.setdefault("per_match", {}).setdefault(match.id, {"status": "unlisted"})
        if item.get("status") != "confirmed":
            item["reason"] = reason


def fetch_document(fetch, url, result):
    result["requested"] += 1
    entry = {"url": _public_url(url), "status": "fetched"}
    diagnostics = _diagnostics(result)
    diagnostics["requests"].append(entry)
    try:
        page = fetch(url)
        if not isinstance(page, (bytes, str)):
            entry["status"] = "unavailable"
            reject(result, "fetch_failed")
        else:
            entry["bytes"] = len(page)
        return page
    except Exception as exc:
        entry.update(status="unavailable", error=type(exc).__name__)
        reject(result, "fetch_failed")
        return None


def _listing_time(listing):
    if isinstance(listing, datetime):
        return listing
    if isinstance(listing, dict):
        values = [listing.get(key) for key in ("utc", "kickoff", "start", "time", "date")]
    elif isinstance(listing, (tuple, list)):
        values = listing
    else:
        values = [getattr(listing, key, None) for key in ("utc", "kickoff", "start", "time", "date")]
    return next((value for value in values if isinstance(value, datetime) and value.utcoffset() is not None), None)


def observe(result, listings, url, complete=False):
    """Record what a parser actually saw, without claiming its date range is complete.

    ``complete`` describes source pagination only. Even a fully read catalogue is
    not proof that a missing fixture has no broadcast, or that every fixture is present.
    """
    listings = list(listings)
    diagnostics = _diagnostics(result)
    horizon = _horizon(_listing_time(item) for item in listings)
    diagnostics["parsed_pages"] += 1
    diagnostics["observed_listings"] += len(listings)
    diagnostics["observations"].append(dict(url=_public_url(url), listings=len(listings),
                                              horizon=horizon, pagination_complete=bool(complete)))
    existing = diagnostics["observed_horizon"]
    bounds = [kickoff(value) for item in (existing, horizon) if item for value in item.values()]
    diagnostics["observed_horizon"] = _horizon(bounds)
    if not listings:
        reject(result, "empty_schedule")
    return listings


def unavailable(result, matches, reason="fetch_or_parse_failed", url=None):
    result["failed"] += 1
    requests = _diagnostics(result)["requests"]
    if reason != "fetch_failed" or not requests or requests[-1].get("status") != "unavailable":
        reject(result, reason)
    for match in matches:
        item = result.setdefault("per_match", {}).setdefault(match.id, {})
        # A failed secondary page must not discard a confirmation from another page.
        if item.get("status") != "confirmed":
            item.update(status="unavailable", reason=reason)
            if url:
                item["url"] = _public_url(url)


def confirm(builder, match, channel, provider, url, result, **evidence):
    """Merge explicit match listings; never promote a package or an inferred usual home."""
    channels = [channel] if isinstance(channel, str) else list(channel)
    outlets = [builder.map_outlet(value, match.league) for value in channels]
    outlets = [outlet for outlet in outlets if outlet.known]
    if not outlets:
        reject(result, "unrecognized_channel", match=match)
        return False
    existing = {outlet.label.casefold() for outlet in match.outlets}
    ledger = getattr(match, "broadcast_evidence", None)
    if ledger is None:
        ledger = match.broadcast_evidence = []
    for outlet in outlets:
        if outlet.label.casefold() not in existing:
            match.outlets.append(outlet)
            existing.add(outlet.label.casefold())
        item = dict(evidence, channel=outlet.label, source=provider, url=url,
                    country="US", kind="match_listing", checked_at="")
        if item not in ledger:
            ledger.append(item)
    match.rule, match.hint = None, ""
    # These fields are retained for older clients. The ledger is authoritative when
    # different channels were verified by different sources.
    match.broadcast_source, match.broadcast_url = provider, url
    match.service, match.basis, match.outlet = builder.evaluate(match.outlets, None, builder.OWNER)
    previous = result.setdefault("per_match", {}).get(match.id, {})
    if previous.get("status") != "confirmed":
        result["confirmed"] += 1
        result["unmatched"] = max(0, result["unmatched"] - 1)
    labels = sorted(set(previous.get("channels", [])) | {outlet.label for outlet in outlets})
    result["per_match"][match.id] = dict(status="confirmed", url=url, channels=labels)
    return True


def finish_report(result):
    """Summarize source health separately from whether any match was confirmed."""
    diagnostics = _diagnostics(result)
    requested = diagnostics.get("requested_horizon")
    observed = diagnostics.get("observed_horizon")
    relation = "unknown"
    if requested and observed:
        if observed["end"] < requested["start"] or observed["start"] > requested["end"]:
            relation = "outside"
        elif observed["start"] <= requested["start"] and observed["end"] >= requested["end"]:
            relation = "contains_requested"
        else:
            relation = "overlaps"
    diagnostics["horizon_relation"] = relation
    if not result.get("requested"):
        status = "not_checked"
    elif not diagnostics["parsed_pages"]:
        status = "unavailable" if result.get("failed") else "unobserved"
    elif result.get("failed"):
        status = "partial"
    elif not diagnostics["observed_listings"]:
        status = "empty"
    elif relation == "outside":
        status = "outside_requested_horizon"
    else:
        status = "observed"
    diagnostics["status"] = status
    # An observed horizon describes only records seen. Missing entries remain
    # unresolved even when they fall within those bounds.
    for item in result.get("per_match", {}).values():
        if item.get("status") == "unlisted" and not item.get("reason"):
            item["reason"] = "outside_requested_horizon" if status == "outside_requested_horizon" else "no_matching_listing"
    return result
