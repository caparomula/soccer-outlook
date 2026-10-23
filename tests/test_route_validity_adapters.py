"""Actual publisher adapters preserve original route lifetimes and clock inputs."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import broadcast_listings
import build
import companion_export
import companion_contract
import outlook_runtime
import schedule_data
from tests.test_discovery import fixture


NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def evidence(**changes):
    value = dict(channel="USA Network", source="ESPN", url="https://example.org/listing",
                 country="US", kind="match_listing", checked_at="2026-10-20T17:00:00Z", retained=True)
    value.update(changes)
    return value


class RowFacts(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.rows = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "li" and "row" in values.get("class", "").split():
            self.rows.append(values)


class RouteLifetimeAdapters(unittest.TestCase):
    def match(self, **changes):
        return fixture(kickoff="2026-10-20T19:00:00Z", broadcast_evidence=[evidence()], **changes)

    def export(self, match, now=NOW):
        report = dict(complete=True, source_scopes=[dict(source="espn", league_id=match.league,
            date_zone="America/New_York", requested_dates=["2026-10-20"], covered_dates=["2026-10-20"],
            complete=True, attempted_at=now, validated_at=now)])
        with patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
            return companion_export.export_artifacts(build, [match], now, report)["schedule"]

    def retain(self, now, *, expires_at=None, checked_at="2026-10-20T12:00:00Z", max_age_hours=6):
        match = self.match()
        previous = asdict(match)
        previous["utc"] = match.utc.isoformat()
        previous["broadcast_evidence"] = [evidence(checked_at=checked_at)]
        if expires_at is not None:
            previous["broadcast_evidence"][0]["expires_at"] = expires_at
        match.outlets = []
        match.broadcast_evidence = []
        match.broadcast_checks = [dict(source="ESPN", status="unavailable", source_health="unavailable")]
        summary = broadcast_listings.retain_recent_evidence(build, [match], [previous], now,
                                                          max_age_hours=max_age_hours)
        return match, summary

    def test_retention_uses_exclusive_original_cutoff_and_keeps_microseconds(self):
        before = NOW - timedelta(microseconds=1)
        match, result = self.retain(before)
        self.assertEqual(result["retained_channels"], 1)
        self.assertEqual(schedule_data.timestamp(match.broadcast_evidence[0]["expires_at"]), NOW)
        self.assertEqual(self.retain(NOW)[1]["retained_channels"], 0)
        self.assertEqual(self.retain(NOW, max_age_hours=99)[1]["retained_channels"], 0)
        self.assertEqual(self.retain(NOW, checked_at="2026-10-20T18:00:00Z", max_age_hours=-1)[1]["retained_channels"], 0)

    def test_explicit_earlier_expiry_survives_repeated_retention(self):
        expiry = "2026-10-20T18:05:00.123456Z"
        match, result = self.retain(NOW, checked_at="2026-10-20T17:00:00Z", expires_at=expiry)
        self.assertEqual(result["retained_channels"], 1)
        self.assertEqual(schedule_data.timestamp(match.broadcast_evidence[0]["expires_at"]), schedule_data.timestamp(expiry))
        previous = asdict(match)
        previous["utc"] = match.utc.isoformat()
        current = self.match()
        current.outlets, current.broadcast_evidence = [], []
        current.broadcast_checks = deepcopy(match.broadcast_checks)
        result = broadcast_listings.retain_recent_evidence(build, [current], [previous], schedule_data.timestamp(expiry))
        self.assertEqual(result["retained_channels"], 0)

    def test_duplicate_channel_retention_keeps_earliest_cutoff_in_either_order(self):
        originals = [evidence(checked_at="2026-10-20T17:00:00Z", expires_at="2026-10-20T23:00:00Z"),
                     evidence(checked_at="2026-10-20T16:00:00Z", expires_at="2026-10-20T18:05:00Z")]
        for records in (originals, list(reversed(originals))):
            for now, expected in ((NOW, 1), (NOW + timedelta(minutes=5), 0),
                                  (NOW + timedelta(minutes=6), 0)):
                with self.subTest(records=records, now=now):
                    prior = asdict(self.match())
                    prior["broadcast_evidence"] = deepcopy(records)
                    prior["utc"] = prior["utc"].isoformat()
                    current = self.match()
                    current.outlets, current.broadcast_evidence = [], []
                    current.broadcast_checks = [dict(source="ESPN", status="unavailable", source_health="unavailable")]
                    result = broadcast_listings.retain_recent_evidence(build, [current], [prior], now)
                    self.assertEqual(result["retained_channels"], expected)
                    if expected:
                        facts = build.route_validity_facts(current, current.outlets[0])
                        self.assertEqual(facts["verified_at"], "2026-10-20T16:00:00Z")
                        self.assertEqual(facts["expires_at"], "2026-10-20T18:05:00Z")
                        self.assertEqual(len(current.broadcast_evidence), 2)
                        reused = asdict(current)
                        reused["utc"] = reused["utc"].isoformat()
                        current.outlets, current.broadcast_evidence = [], []
                        self.assertEqual(broadcast_listings.retain_recent_evidence(build, [current], [reused],
                            NOW + timedelta(minutes=5))["retained_channels"], 0)

    def test_definitively_rejected_source_cannot_poison_an_independent_retention(self):
        prior = asdict(self.match())
        prior["utc"] = prior["utc"].isoformat()
        prior["broadcast_evidence"] = [evidence(source="Rejected source", expires_at=NOW.isoformat()), evidence()]
        current = self.match()
        current.outlets, current.broadcast_evidence = [], []
        current.broadcast_checks = [dict(source="Rejected source", status="unlisted", reason="wrong_country"),
                                    dict(source="ESPN", status="unavailable", source_health="unavailable")]
        self.assertEqual(broadcast_listings.retain_recent_evidence(build, [current], [prior], NOW)["retained_channels"], 1)
        self.assertEqual([value["source"] for value in current.broadcast_evidence], ["ESPN"])

    def test_outage_does_not_renew_malformed_retained_claim_from_fresh_record(self):
        for changes in ({"checked_at": ""}, {"url": "https:///listing"},
                        {"url": "https://user@example.org/listing"}, {"url": "https://[broken/listing"}):
            for rejected in (False, True):
                with self.subTest(changes=changes, rejected=rejected):
                    prior = asdict(self.match())
                    prior["utc"] = prior["utc"].isoformat()
                    prior["broadcast_evidence"] = [evidence(source="Malformed source", **changes),
                                                   evidence(retained=False), evidence(channel="ESPN+", retained=False)]
                    current = self.match()
                    current.outlets, current.broadcast_evidence = [], []
                    current.broadcast_checks = [dict(source="ESPN", status="unavailable", source_health="unavailable"),
                        dict(source="Malformed source", status="unlisted" if rejected else "unavailable",
                             reason="wrong_country" if rejected else "fetch_failed")]
                    summary = broadcast_listings.retain_recent_evidence(build, [current], [prior], NOW)
                    self.assertEqual(summary["retained_channels"], 2 if rejected else 1)
                    self.assertEqual({outlet.label for outlet in current.outlets},
                                     {"USA Network", "ESPN+"} if rejected else {"ESPN+"})

    def test_duplicate_evidence_references_preserve_distinct_original_cutoffs(self):
        prior = asdict(self.match())
        prior["utc"] = prior["utc"].isoformat()
        prior["broadcast_evidence"] = [evidence(expires_at="2026-10-20T23:00:00Z"),
                                       evidence(expires_at="2026-10-20T18:05:00Z")]
        current = self.match()
        current.outlets, current.broadcast_evidence = [], []
        current.broadcast_checks = [dict(source="ESPN", status="unavailable", source_health="unavailable")]
        self.assertEqual(broadcast_listings.retain_recent_evidence(build, [current], [prior], NOW)["retained_channels"], 1)
        schedule = self.export(current)
        companion_contract.validate("schedule", schedule)
        route = schedule["fixtures"][0]["routes"][0]
        self.assertEqual(len(route["evidence_ids"]), 1)
        self.assertEqual(route["verified_at"], "2026-10-20T17:00:00Z")
        self.assertEqual(route["expires_at"], "2026-10-20T18:05:00Z")

    def test_malformed_retained_claim_does_not_borrow_fresh_age_in_page_or_feed(self):
        for changes in ({"checked_at": ""}, {"url": "https:///listing"},
                        {"url": "https://user@example.org/listing"}):
            with self.subTest(changes=changes):
                match = self.match()
                match.broadcast_evidence = [evidence(**changes), evidence(retained=False)]
                match.outlets.append(build.map_outlet("ESPN+", match.league))
                match.broadcast_evidence.append(evidence(channel="ESPN+", retained=False))
                result = build.evaluate_match_routes(match, NOW)
                self.assertEqual(result["listed_providers"][0], [])
                self.assertTrue(result["listed_providers"][1])
                self.assertIsNone(build.route_validity_facts(match, match.outlets[0])["verified_at"])
                schedule = self.export(match)
                companion_contract.validate("schedule", schedule)
                route, fresh = schedule["fixtures"][0]["routes"]
                self.assertEqual(route["eligible_service_plan_ids"], [])
                self.assertEqual(route["basis"], "unknown")
                self.assertFalse(route["retained"])
                self.assertIsNone(route["verified_at"])
                self.assertIsNone(route["expires_at"])
                self.assertTrue(route["notes"])
                self.assertTrue(fresh["eligible_service_plan_ids"])

    def test_retention_rejects_malformed_evidence_urls(self):
        for url in ("https:///listing", "https://user@example.org/listing"):
            with self.subTest(url=url):
                prior = asdict(self.match())
                prior["utc"] = prior["utc"].isoformat()
                prior["broadcast_evidence"] = [evidence(url=url)]
                current = self.match()
                current.outlets, current.broadcast_evidence = [], []
                current.broadcast_checks = [dict(source="ESPN", status="unavailable", source_health="unavailable")]
                self.assertEqual(broadcast_listings.retain_recent_evidence(build, [current], [prior], NOW)["retained_channels"], 0)

    def test_subsecond_custom_allowance_truncates_without_extending_evidence(self):
        self.assertEqual(self.retain(NOW, checked_at="2026-10-20T18:00:00Z", max_age_hours=0.5 / 3600)[1]["retained_channels"], 0)

    def test_page_and_feed_share_conservative_original_times_and_explicit_bound(self):
        match = self.match()
        match.broadcast_evidence += [evidence(checked_at="2026-10-20T16:30:00Z",
                                             expires_at="2026-10-20T18:15:00.123456Z")]
        build.evaluate_match_routes(match, NOW)
        row = RowFacts(build.row_html(match, {}, now=NOW)).rows[0]
        timing = json.loads(row["data-o"])[0]["t"]
        route = self.export(match)["fixtures"][0]["routes"][0]
        self.assertEqual(timing["verified_at"], "2026-10-20T16:30:00Z")
        self.assertEqual(schedule_data.timestamp(timing["expires_at"]), schedule_data.timestamp("2026-10-20T18:15:00.123456Z"))
        self.assertEqual(schedule_data.timestamp(route["expires_at"]), schedule_data.timestamp(timing["expires_at"]))
        self.assertEqual(route["verified_at"], timing["verified_at"])

    def test_expiring_one_route_preserves_independent_fresh_listing(self):
        match = self.match()
        match.broadcast_evidence[0]["expires_at"] = NOW.isoformat()
        match.outlets.append(build.map_outlet("ESPN+", match.league))
        match.broadcast_evidence.append(evidence(channel="ESPN+", retained=False))
        result = build.evaluate_match_routes(match, NOW)
        self.assertEqual(result["listed_providers"][0], [])
        self.assertTrue(result["listed_providers"][1])
        self.assertEqual(match.outlet, "ESPN+")
        routes = self.export(match)["fixtures"][0]["routes"]
        self.assertEqual(routes[0]["eligible_service_plan_ids"], [])
        self.assertTrue(routes[1]["eligible_service_plan_ids"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "facts.json"
            build.write_facts(path, [match], NOW, NOW.date())
            offered = json.loads(path.read_text())["next_24_hours"][0]
            self.assertEqual(offered["broadcasters"], ["ESPN+"])

    def test_expired_route_does_not_render_a_static_playback_link(self):
        match = self.match(broadcast_source="Watch ESPN",
                           broadcast_url="https://www.espn.com/watch/player/_/id/12345678-1234-1234-1234-123456789abc")
        match.outlets = [build.map_outlet("ESPN+", match.league)]
        match.broadcast_evidence = [evidence(channel="ESPN+", expires_at=NOW.isoformat())]
        build.evaluate_match_routes(match, NOW)
        markup = build.pills_html(match)
        self.assertNotIn('class="network__link"', markup)
        self.assertIn("This listing has expired.", markup)

    def test_historical_date_cannot_revive_expired_usual_rights_at_evaluation(self):
        until = build.RIGHTS.leagues["esp.1"].usual.until
        after = datetime(until.year, until.month, until.day, 12, tzinfo=build.ET) + timedelta(days=1)
        with patch.object(build, "TODAY", until):
            usual = build.usual_home("esp.1")
            self.assertIsNotNone(usual)  # Keep --date source qualification.
            match = fixture(league="esp.1", channel=None, rule=usual, kickoff=after.isoformat())
            result = build.evaluate_match_routes(match, after)
            self.assertIsNone(result["service_id"])
            self.assertEqual(result["usual_providers"], [])
            self.assertEqual(build.route_validity_facts(match, usual, usual=True)["rights_until"], until.isoformat())

    def test_facts_and_schedule_use_supplied_clock_without_renewing_evidence(self):
        match = self.match()
        match.broadcast_evidence[0]["expires_at"] = NOW.isoformat()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build.write_facts(directory / "facts.json", [match], NOW, NOW.astimezone(build.ET).date())
            self.assertNotIn(match.id, [value["id"] for value in json.loads((directory / "facts.json").read_text())["next_24_hours"]])
            snapshot = schedule_data.publish(build, directory, [match], {}, NOW, dict(complete=True))
            row = RowFacts(snapshot["rows"][0]).rows[0]
            self.assertEqual(row["data-svc"], "none")
            self.assertEqual(json.loads(row["data-o"])[0]["t"]["expires_at"], "2026-10-20T18:00:00Z")
            context = json.loads((directory / "schedule-context.json").read_text())
            self.assertEqual(context["matches"][0]["broadcast_evidence"], match.broadcast_evidence)

    def test_validity_cache_includes_evaluation_time_and_returns_independent_values(self):
        match = self.match()
        match.broadcast_evidence[0]["expires_at"] = "2026-10-20T18:01:00Z"
        facts = build.route_validity_facts(match, match.outlets[0])
        first = outlook_runtime.route_validity(facts, NOW)
        self.assertTrue(first["usable"])
        first["usable"] = False
        self.assertTrue(outlook_runtime.route_validity(facts, NOW)["usable"])
        self.assertFalse(outlook_runtime.route_validity(facts, NOW + timedelta(minutes=1))["usable"])

    def test_evidence_url_rules_preserve_existing_acceptance(self):
        accepted = ("https://example.org/listing", "HTTPS://example.org/listing?q=1#section",
                    "https://[::1]/listing")
        rejected = (None, 7, "http://example.org/listing", "https:///listing",
                    "https://user@example.org/listing", "https://user:secret@example.org/listing",
                    "https://example.org/a b", "https://example.org/\nlisting",
                    "https://example.org/\x7flisting", "https://[broken/listing")
        for value in accepted + rejected:
            with self.subTest(url=value):
                self.assertEqual(companion_export.https(value), value if value in accepted else None)

    def test_evidence_qualification_owner_changes_schedule_revision(self):
        root = Path(build.__file__).resolve().parent
        original = Path.read_bytes
        before = schedule_data.runtime_revision(root)
        def changed(path):
            contents = original(path)
            return contents + b"\n# changed source qualification\n" if path == root / "provider_utils.py" else contents
        with patch.object(Path, "read_bytes", changed):
            self.assertNotEqual(schedule_data.runtime_revision(root), before)


if __name__ == "__main__":
    unittest.main()
