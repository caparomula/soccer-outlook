#!/usr/bin/env python3
"""Capture one local production build and independently reconcile its exact inputs.

--fetch performs the existing public source GETs, never AI generation or deployment.
--reconcile is offline, including optional browser capture. The separate
companion_collection_replay.py checks the lightweight branch without networking.
Raw responses and generated pages belong in a fresh ignored work directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build
from companion_contract import decode, validate_publication
from tool.companion_feed_verify import read as dart_read
from tool.companion_publication_capture.capture import (
    PublicationHTML, row_fact, browser_capture, load_browser_resources,
)

MAX_RESPONSE = 8 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_CALLS = 500
ET = ZoneInfo("America/New_York")


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() is not None, "Naive evidence timestamp")
    return result.astimezone(timezone.utc)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def source_hashes():
    paths = [*ROOT.glob("*.py"), *ROOT.glob("web/*"), *ROOT.glob("contracts/outlook/**/*.json"),
             *ROOT.glob("packages/outlook_data/lib/**/*.dart"),
             *ROOT.glob("tool/companion_feed_reader/lib/**/*.dart"),
             *ROOT.glob("tool/companion_feed_reader/bin/**/*.dart"), Path(__file__).resolve()]
    paths += [ROOT / name for name in ("settings.toml", "rights.toml", "watch-links.toml")]
    return {str(path.relative_to(ROOT)): digest(path.read_bytes())
            for path in sorted(set(paths)) if path.is_file()}


def quality_record(quality):
    return dict(sources=quality.sources, skipped_events=quality.skipped_events,
                excluded_events=quality.excluded_events, source_times=[
                    dict(league=league, date=day, **{key: stamp(value) if value else None
                         for key, value in timing.items()})
                    for (league, day), timing in sorted(quality.source_times.items())])


class RecordingTransport:
    """Bound the existing transport call surface; record outcomes without retries of our own.

    The existing transport owns up to three HTTP attempts. It returns only a successful
    body or None, so failed response bodies/statuses and individual retries are unknown.
    The byte limit is checked on return, not an assertion of bounded curl allocation.
    """
    def __init__(self, directory, original):
        self.directory, self.original = directory, original
        self.records, self.total = [], 0
        self.lock = threading.Lock()
        (directory / "responses").mkdir(parents=True)
        save(directory / "requests.json", [])

    def __call__(self, url, timeout=40, attempts=3):
        parts = urlsplit(url)
        require(parts.scheme == "https" and parts.hostname and not parts.username and not parts.password,
                "Capture accepts only public HTTPS requests without URL credentials")
        with self.lock:
            require(len(self.records) < MAX_CALLS, "Capture transport call limit")
            record = dict(id=len(self.records) + 1, url=url, attempted_at=stamp(datetime.now(timezone.utc)),
                          timeout_seconds=timeout, maximum_http_attempts=attempts, outcome="pending")
            self.records.append(record)
        try:
            raw = self.original(url, timeout=timeout, attempts=attempts)
            record["received_at"] = stamp(datetime.now(timezone.utc))
            if raw is None:
                record["outcome"] = "unavailable"
                return None
            require(isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE, "Captured response exceeds byte limit")
            with self.lock:
                self.total += len(raw)
                require(self.total <= MAX_TOTAL, "Capture total byte limit")
            path = f"responses/{record['id']:04d}.bin"
            (self.directory / path).write_bytes(raw)
            record.update(outcome="body", path=path, bytes=len(raw), sha256=digest(raw))
            return raw
        except BaseException as error:
            record.update(outcome="error", error=type(error).__name__, received_at=stamp(datetime.now(timezone.utc)))
            raise
        finally:
            with self.lock:
                save(self.directory / "requests.json", self.records)


def capture(output, workers):
    require(not output.exists(), "Use a fresh --out directory; prior evidence is never overwritten")
    require(1 <= workers <= 6, "Workers must be between one and six")
    output.mkdir(parents=True)
    before = source_hashes()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = dict(version=1, mode="live_local_build", started_at=stamp(datetime.now(timezone.utc)),
                    producer_revision=revision, source_hashes_before=before, completed=False,
                    limitations=["Transport retains successful bodies or unavailable outcomes; HTTP status and individual curl retries are not exposed.",
                                 "Provider observation timestamps are not invented from capture receipt time.",
                                 "No AI generation, account access, assets download or deployment."])
    save(output / "capture.json", metadata)
    transport = RecordingTransport(output, build.curl_bytes)
    qualities, requested = [], {}
    original_quality, original_fetch = build.BuildQuality, build.fetch_scoreboards

    class CapturedQuality(original_quality):
        def __init__(self):
            super().__init__()
            qualities.append(self)

    def fetch(days, count, quality):
        requested.update(leagues=list(build.LEAGUES), dates=[day.isoformat() for day in days])
        return original_fetch(days, count, quality)

    site = output / "full"
    argv = ["build.py", "--out", str(site / "index.html"), "--workers", str(workers),
            "--facts", str(site / "facts.json"), "--metrics", str(site / "metrics.json"),
            "--report", str(site / "report.json"), "--companion-feed", "--companion-previous-api", "none",
            "--companion-producer-revision", revision]
    metadata["build_arguments"] = argv[1:]
    try:
        with patch.object(build, "curl_bytes", transport), patch.object(build, "BuildQuality", CapturedQuality), \
                patch.object(build, "fetch_scoreboards", fetch), patch.object(sys, "argv", argv), \
                patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
            result = build.main()
        metadata["build_exit_code"] = result
        metadata["completed"] = result == 0
    except BaseException as error:
        metadata["exception"] = type(error).__name__
        raise
    finally:
        metadata.update(finished_at=stamp(datetime.now(timezone.utc)), request_scope=requested,
                        source_hashes_after=source_hashes(), request_count=len(transport.records),
                        response_bytes=transport.total)
        save(output / "quality.json", quality_record(qualities[-1]) if qualities else {})
        metadata["sources_unchanged"] = before == metadata["source_hashes_after"]
        save(output / "capture.json", metadata)
    require(result == 0, "Build failed; original request and quality evidence remains in the capture directory")
    require(metadata["sources_unchanged"], "Sources changed during capture; retain this run and recapture against stable sources")
    return metadata


def load_requests(output):
    records = json.loads((output / "requests.json").read_text())
    require(isinstance(records, list) and len(records) <= MAX_CALLS, "Invalid request ledger")
    values, total = {}, 0
    for record in records:
        require(record["outcome"] in ("body", "unavailable"), "Capture contains unfinished or failed calls")
        require(parse(record["attempted_at"]) <= parse(record["received_at"]), "Request times are reversed")
        if record["outcome"] != "body":
            continue
        require(re.fullmatch(r"responses/[0-9]{4}\.bin", record["path"]), "Invalid response path")
        path = output / record["path"]
        require(not path.is_symlink(), "Response symlink is not allowed")
        raw = path.read_bytes()
        require(len(raw) <= MAX_RESPONSE and len(raw) == record["bytes"] and digest(raw) == record["sha256"],
                "Captured response bytes changed")
        total += len(raw)
        require(total <= MAX_TOTAL, "Capture total byte limit")
        values[record["id"]] = raw
    require(len({row["id"] for row in records}) == len(records), "Duplicate request identities")
    return records, values


def scoreboard_identity(url):
    parts = urlsplit(url)
    match = re.fullmatch(r"/apis/site/v2/sports/soccer/([^/]+)/scoreboard", parts.path)
    if parts.netloc != "site.api.espn.com" or not match:
        return None
    query = parse_qs(parts.query)
    require(query.get("limit") == ["200"] and len(query.get("dates", [])) == 1, "Unexpected scoreboard query")
    day = datetime.strptime(query["dates"][0], "%Y%m%d").date().isoformat()
    return match[1], day


def raw_fixtures(records, responses, requested):
    """Independent input identity/scope check; no production fetch/parser calls."""
    expected = {(league, day) for league in requested["leagues"] for day in requested["dates"]}
    seen, fixtures, exclusions, request_by_pair = set(), {}, [], {}
    for record in records:
        pair = scoreboard_identity(record["url"])
        if pair is None:
            continue
        require(pair in expected and pair not in seen, "Unexpected or duplicate scoreboard request")
        seen.add(pair)
        require(record["outcome"] == "body", "Required scoreboard unavailable")
        data = decode(responses[record["id"]])
        require(isinstance(data, dict) and isinstance(data.get("events"), list), "Scoreboard lacks events list")
        league, day = pair
        slugs = {item.get("slug") for item in data.get("leagues", []) if isinstance(item, dict) and item.get("slug")}
        require(not slugs or slugs == {league}, "Scoreboard competition contradicts request")
        claimed = data.get("day")
        claimed = claimed.get("date") if isinstance(claimed, dict) else claimed
        if isinstance(claimed, str) and re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", claimed):
            require(datetime.strptime(claimed.replace("-", ""), "%Y%m%d").date().isoformat() == day,
                    "Scoreboard date contradicts request")
        require(len(data["events"]) < 200, "Scoreboard result limit reached")
        for key in ("total", "totalEvents", "eventCount", "count"):
            require(type(data.get(key)) is not int or data[key] <= len(data["events"]), "Scoreboard omits advertised events")
        for event in data["events"]:
            require(isinstance(event, dict) and type(event.get("id")) in (str, int), "Invalid raw event identity")
            identity = str(event["id"])
            require(identity and identity not in fixtures, "Duplicate raw event identity")
            require(parse(event["date"]).astimezone(ET).date().isoformat() == day, "Raw fixture falls outside requested date")
            competitions = event.get("competitions")
            require(isinstance(competitions, list) and competitions and isinstance(competitions[0], dict), "Missing raw competition")
            comp = competitions[0]
            excluded = (league == "esp.copa_del_rey" and (event.get("season") or {}).get("slug") == "qualifying-round") or (
                league == "concacaf.nations.league" and not str((comp.get("group") or {}).get("name") or "").startswith("League A"))
            if excluded:
                exclusions.append(dict(id=identity, league=league, reason="existing competition scope exclusion"))
                continue
            fixtures[identity] = dict(league=league, date=day, event=event, competition=comp, request=record)
        request_by_pair[pair] = record
    require(seen == expected, "Actual scoreboard scope differs from the requested Cartesian product")
    return fixtures, exclusions, request_by_pair


def unique_rows(rows):
    result = {}
    for row in rows:
        require(row["data-id"] not in result, "Duplicate rendered fixture identity")
        result[row["data-id"]] = row_fact(row)
    return result


def actual_score(raw):
    if type(raw) not in (str, int, float) or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value) if math.isfinite(value) and value.is_integer() and 0 <= value <= 999 else None


def verify_scope(metadata, quality, schedule, requests_by_pair):
    require(quality["skipped_events"] == [], "Unparsed required events remain")
    requested = metadata["request_scope"]
    expected_pairs = {(lg, day) for lg in requested["leagues"] for day in requested["dates"]}
    sources = {(row["league"], row["date"]): row for row in quality["sources"]}
    require(len(sources) == len(quality["sources"]) and set(sources) == expected_pairs,
            "Build quality source identity set differs from actual requests")
    require(all(row["ok"] is True for row in sources.values()), "Required source quality is incomplete")
    times = {(row["league"], row["date"]): row for row in quality["source_times"]}
    require(set(times) == expected_pairs, "Source timing coverage is incomplete")
    scopes = {row["league_id"]: row for row in schedule["source_scopes"]}
    require(len(scopes) == len(schedule["source_scopes"]) and set(scopes) == set(requested["leagues"]),
            "Feed source league scope differs from actual requests")
    for league, scope in scopes.items():
        require(scope["source"] == "espn" and scope["date_zone"] == "America/New_York" and scope["complete"] is True,
                "Feed source scope semantics differ from acquisition")
        require(scope["requested_dates"] == requested["dates"] == scope["covered_dates"],
                "Feed date scope differs from actual requests")
        entries = [times[(league, day)] for day in requested["dates"]]
        require(parse(scope["attempted_at"]) == min(parse(row["attempted_at"]) for row in entries), "Feed source attempt time changed")
        require(parse(scope["validated_at"]) == max(parse(row["validated_at"]) for row in entries), "Feed source validation time changed")
        for row in entries:
            transport = requests_by_pair[(league, row["date"])]
            require(parse(row["attempted_at"]) <= parse(transport["attempted_at"]) <= parse(transport["received_at"]) <= parse(row["validated_at"]),
                    "Source validation predates its actual transport receipt")
    days = [datetime.strptime(day, "%Y-%m-%d").date() for day in requested["dates"]]
    require(days and all(b-a == timedelta(days=1) for a, b in zip(days, days[1:])), "Capture requested dates are not contiguous")
    horizon = dict(start=stamp(datetime.combine(days[0], datetime.min.time(), ET)),
                   end_exclusive=stamp(datetime.combine(days[-1] + timedelta(days=1), datetime.min.time(), ET)))
    require(schedule["confirmed_horizon"] == horizon, "Feed horizon differs from actual calendar scope")


def verify_facts(raw, artifacts, context, html_rows, schedule_rows):
    """Compare provider/renderer facts directly, not through exporter helpers."""
    feed = {row["id"].removeprefix("espn:fixture:"): row for row in artifacts["schedule"]["fixtures"]}
    cached = {str(row["id"]): row for row in context["matches"]}
    require(len(feed) == len(artifacts["schedule"]["fixtures"]) and len(cached) == len(context["matches"]), "Duplicate output fixture identity")
    require(set(raw) == set(feed) == set(cached) == set(html_rows) == set(schedule_rows), "Raw/context/rendered/feed fixture universes differ")
    teams = {row["id"]: row for row in artifacts["catalog"]["teams"]}
    networks = {row["id"]: row["name"] for row in artifacts["catalog"]["networks"]}
    evidence = {row["id"]: row for row in artifacts["schedule"]["evidence"]}
    vitals = {row["id"]: row for row in artifacts["schedule"]["team_vitals"]}
    destinations = {row["id"]: row for row in artifacts["schedule"]["destinations"]}
    scopes = {row["league_id"]: row for row in artifacts["schedule"]["source_scopes"]}
    default_leagues = set(artifacts["policy"]["defaults"]["enabled_league_ids"])
    no_service, disabled, exceptional = [], [], []
    score_counts = Counter()
    for identity, source in raw.items():
        f, m, rendered = feed[identity], cached[identity], html_rows[identity]
        require(rendered == schedule_rows[identity], "Static and refreshed row facts differ")
        require(f["league_id"] == source["league"] == rendered["league"], "Fixture competition changed")
        event, comp = source["event"], source["competition"]
        competitors = {row.get("homeAway"): row for row in comp["competitors"] if isinstance(row, dict)}
        for side in ("home", "away"):
            team = competitors[side]["team"]
            require(f[side + "_team_id"] == "espn:team:" + str(team["id"]), "Source team identity changed")
            name = team.get("displayName") or team.get("name") or "?"
            require(teams[f[side + "_team_id"]]["name"] == name == rendered[side], "Source team name changed or catalog overwrote it")
            vital = vitals[f["context"][side + "_vitals_id"]]
            raw_form = (competitors[side].get("form") or "")[-5:]
            require((vital.get("raw_form") or "") == raw_form == rendered["vitals"][side + "_form"], "Source form string changed")
            require(parse(vital["standings_received_at"]) == parse(context["standings_at"]), "Standings age changed")
            require(vital["observation"]["observed_at"] is None, "Unknown provider vital observation time was invented")
            rank = vital["rank"]
            require((rank["position"] if rank else None) == rendered["vitals"][side + "_rank"], "Rendered rank differs from feed")
        confirmed = bool(comp.get("timeValid", True))
        kickoff = stamp(parse(event["date"])) if confirmed else None
        require(f["timing"]["confirmed"] == confirmed and f["timing"]["kickoff"] == kickoff == rendered["kickoff"], "Source kickoff or confirmation changed")
        status = comp.get("status") or event.get("status") or {}
        typ = status.get("type") or {}
        state = typ.get("state") or "pre"
        require(f["status"]["provider_state"] == state == rendered["state"], "Provider match state changed")
        require(f["status"]["provider_detail"] == m["status"], "Legacy provider detail changed")
        description = str(typ.get("description") or "").lower()
        if any(word in description for word in ("postpon", "cancel", "abandon", "suspend")):
            exceptional.append(dict(id=identity, description=description, exported_state=f["status"]["state"]))
        # Independently use explicit legacy void labels. Exceptional raw status coverage
        # is separately retained; this does not prove the future shared status decoder.
        void = state == "post" and str(m["status"]).lower() in ("canceled", "cancelled", "postponed")
        for side in ("home", "away"):
            expected = actual_score(competitors[side].get("score")) if state in ("in", "post") and not void else None
            require(f["status"]["scores"]["match"][side] == expected, "Raw source score differs from feed")
            score_counts["unknown" if expected is None else "zero" if expected == 0 else "positive"] += 1
        obs = f["status"]["observation"]
        require(obs["observed_at"] is None and obs["provider_updated_at"] is None, "Unknown provider status time was invented")
        require(obs["received_at"] == scopes[source["league"]]["validated_at"], "Collection validation time changed")
        require(math.isclose(f["outlook"]["total"], rendered["outlook_total"], abs_tol=.0001), "Rendered canonical Outlook total differs")
        require({key: None if value is None else round(value * 100) for key, value in f["outlook"]["components"].items()} == rendered["outlook_parts_percent"],
                "Rendered canonical Outlook components differ")
        outlet_facts = {row["l"]: row for row in rendered["outlets"] if not row.get("u")}
        if rendered["usual_rule"]:
            outlet_facts[rendered["usual_rule"]["l"]] = rendered["usual_rule"]
        require(set(outlet_facts) == {networks[row["network_id"]] for row in f["routes"]}, "Rendered and feed network universe differs")
        for route in f["routes"]:
            outlet = outlet_facts[networks[route["network_id"]]]
            eligible = outlet["v"] if route["basis"] != "unknown" else []
            require(route["eligible_service_plan_ids"] == eligible, "Rendered service plans differ from qualified route")
            for evidence_id in route["evidence_ids"]:
                item = evidence[evidence_id]
                require(item["verdict"] == "accepted" and item["classification"] == "live_match" and item["country"] == "US", "Route grants access from unqualified evidence")
                require(item["fixture_id"] == f["id"] and item["kickoff"] == f["timing"]["kickoff"], "Route evidence fixture binding changed")
                require(parse(item["observed_at"]) <= parse(context["generated_at"]) or item["observed_at"] == scopes[source["league"]]["validated_at"], "Route verification time was renewed")
            for destination_id in route["destination_ids"]:
                destination = destinations[destination_id]
                require(destination["fixture_id"] == f["id"] and route["network_id"] in destination["network_ids"], "Destination binding changed")
        if not m["service"]:
            no_service.append(identity)
        if source["league"] not in default_leagues:
            disabled.append(identity)
        actual_destinations = {(networks[network_id], service, destination["url"], destination["kind"], destination["live_only"])
            for destination_id in f["links"]["destination_ids"]
            for destination in [destinations[destination_id]]
            for network_id in destination["network_ids"] for service in destination["service_plan_ids"]
            if service in outlet_facts[networks[network_id]]["v"]}
        rendered_destinations = {(network, service, destination["url"], destination["kind"], bool(destination.get("live_only")))
            for network, services in rendered["watch_links"].items() for service, destination in services.items()}
        require(actual_destinations == rendered_destinations, "Rendered and feed destination/plan associations differ")
    return dict(fixtures=len(feed), without_owner_service=no_service, default_off_competition=disabled,
                score_observations=dict(score_counts), exceptional_provider_statuses=exceptional,
                source_status_scope="Provider state, legacy detail and numeric scores; not proof of the future shared status decoder")


def reconcile(output, dart, browsers=()):
    metadata = json.loads((output / "capture.json").read_text())
    require(metadata["completed"] and metadata["sources_unchanged"], "Capture did not complete against stable sources")
    records, responses = load_requests(output)
    raw, excluded, requests_by_pair = raw_fixtures(records, responses, metadata["request_scope"])
    quality = json.loads((output / "quality.json").read_text())
    require(len(excluded) == quality["excluded_events"], "Deliberate competition exclusion count differs")
    site = output / "full"
    manifest, artifacts = validate_publication(site / "api/v1")
    context = decode((site / "schedule-context.json").read_bytes())
    refreshed = decode((site / "schedule.json").read_bytes())
    html = PublicationHTML((site / "index.html").read_text())
    require(refreshed["complete"] is True, "Browser schedule is incomplete")
    require(parse(html.app["data-built"]) == parse(context["generated_at"]).replace(microsecond=0), "Static page build time differs")
    require(refreshed["generated_at"] == context["generated_at"], "Browser/context build instants differ")
    verify_scope(metadata, quality, artifacts["schedule"], requests_by_pair)
    facts = verify_facts(raw, artifacts, context, unique_rows(html.rows), unique_rows(PublicationHTML("".join(refreshed["rows"])).rows))
    parsed = dart_read(dart, site / "api/v1")
    require(parsed["source_scopes"] == artifacts["schedule"]["source_scopes"], "Dart source scopes differ")
    expected = [dict(id=row["id"], home_team_id=row["home_team_id"], away_team_id=row["away_team_id"],
                     timing=row["timing"], status=row["status"], outlook=row["outlook"], routes=[
                         {key: route[key] for key in ("id", "network_id", "basis", "eligible_service_plan_ids", "excluded_service_plan_ids")}
                         for route in row["routes"]]) for row in artifacts["schedule"]["fixtures"]]
    require(parsed["fixtures"] == expected, "Dart factual projection differs from validated producer artifact")
    browser_results = []
    if browsers:
        statuses, payloads = load_browser_resources(site)
        for browser in browsers:
            for zone, width in (("America/New_York", 1280), ("America/New_York", 390), ("America/Los_Angeles", 390)):
                result = browser_capture(output, statuses, payloads, context["generated_at"], browser, zone, width)
                require(not result["page_errors"], "Browser reported script errors")
                attached = {row["id"] for row in result["row_outputs"]}
                require(attached <= set(raw), "Browser DOM introduced an unknown fixture")
                missing = set(raw) - attached
                filtered = set(facts["without_owner_service"]) | set(facts["default_off_competition"])
                require(missing <= filtered, "Browser detached a fixture that passes default filters")
                feed_by_id = {row["id"].removeprefix("espn:fixture:"): row for row in artifacts["schedule"]["fixtures"]}
                for row in result["row_outputs"]:
                    scores = feed_by_id[row["id"]]["status"]["scores"]["match"]
                    require(len(row["scores"]) == 2, "Browser row score shape changed")
                    for side, visible in zip(("home", "away"), row["scores"]):
                        require(visible["hidden"] == (scores[side] is None), "Browser score visibility differs from source facts")
                        if scores[side] is not None:
                            require(actual_score(visible["value"]) == scores[side], "Browser numeric score differs from source facts")
                result["unfiltered_fixture_count"] = len(raw)
                result["attached_fixture_count"] = len(attached)
                result["detached_by_default_filters"] = sorted(missing)
                result["dom_scope_note"] = "The legacy renderer detaches in-window rows excluded by default services/competitions; static HTML, schedule rows and feed retain the entire unfiltered source universe."
                browser_results.append({key: value for key, value in result.items() if key != "row_outputs"})
    report = dict(version=1, mode="collection_reconciled_offline", acquisition_mode=metadata["mode"], publication_revision=manifest["publication_revision"],
                  capture_sha256=digest((output / "capture.json").read_bytes()),
                  producer_revision=metadata["producer_revision"], source_hashes=metadata["source_hashes_after"],
                  reconciler_source_hashes=source_hashes(), requested_scope=metadata["request_scope"],
                  required_requests=len(requests_by_pair), all_transport_calls=len(records),
                  unavailable_optional_calls=sum(row["outcome"] == "unavailable" for row in records),
                  explicit_competition_exclusions=excluded, confirmed_horizon=artifacts["schedule"]["confirmed_horizon"],
                  facts=facts, dart_reader_accepted=True, editorial=parsed["editorial"], browsers=browser_results,
                  output_hashes={str(path.relative_to(site)): digest(path.read_bytes()) for path in sorted(site.rglob("*")) if path.is_file()},
                  limits=metadata["limitations"] + ["A fresh capture proves this requested source interval, not all provider fixtures or future availability.",
                     "Form text is compared with raw source. Chronological form ordering requires separate independent evidence.",
                     "Broadcast route qualification is reconciled with captured evidence and rendered channel/plan facts; subscriber authentication and source usage permission remain outside this check."])
    save(output / "reconciliation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--browsers", nargs="*", choices=("chromium", "webkit"), default=[])
    args = parser.parse_args()
    output = args.out.resolve()
    if args.fetch:
        metadata = capture(output, args.workers)
        print(json.dumps({"capture": str(output), "completed": metadata["completed"], "calls": metadata["request_count"]}))
    else:
        report = reconcile(output, args.dart, args.browsers)
        print(json.dumps({"report": str(output / "reconciliation.json"), "fixtures": report["facts"]["fixtures"],
                          "required_requests": report["required_requests"], "dart_reader_accepted": True,
                          "browser_runs": len(report["browsers"])}))


if __name__ == "__main__":
    main()
