"""Original scoreboard receipts stay distinct from validation and publication."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import build
import companion_export
import schedule_data
from tests import test_render
from tests.test_discovery import fixture
from tool import companion_collection_replay as replay
from tool import companion_collection_capture as capture
from tool.companion_collection_capture import quality_record


NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)
FIRST = "2026-10-23T17:00:01.123456Z"
SECOND = "2026-10-23T17:02:03.654321Z"
EXAMPLES = {case["id"]: case for case in json.loads((Path(__file__).resolve().parents[1] /
    "docs/companion/rules/source-provenance-examples.json").read_text())["cases"]}


def event(identity="first", day="2026-10-23"):
    value = test_render.Markup.event()
    value.update(id=identity, date=day + "T19:00:00Z")
    return value


def artifacts(matches, published=NOW):
    days = sorted({match.utc.date().isoformat() for match in matches})
    scope = dict(source="espn", league_id="eng.1", date_zone="America/New_York",
                 requested_dates=days, covered_dates=days, complete=True,
                 attempted_at="2026-10-23T16:59:00Z", validated_at="2026-10-23T17:59:00Z")
    with patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
        return companion_export.export_artifacts(build, matches, published,
                                                dict(complete=True, source_scopes=[scope]))


class SourceProvenance(unittest.TestCase):
    def collect(self, payloads, receipts=None, validated=NOW):
        """Real worker executor; distinct worker/main clocks expose late validation."""
        receipts = receipts or {"20261023": FIRST, "20261024": SECOND}
        worker = threading.local()
        calls = []
        real_loads = json.loads
        bodies = {key: value if value is None or isinstance(value, bytes) else json.dumps(value).encode()
                  for key, value in payloads.items()}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = getattr(worker, "receipt", validated)
                if hasattr(worker, "day"):
                    calls.append((worker.day, "clock"))
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        def transport(url):
            day = parse_qs(urlsplit(url).query)["dates"][0]
            worker.day = day
            worker.receipt = datetime.fromisoformat(receipts[day].replace("Z", "+00:00"))
            calls.append((day, "transport_return"))
            return bodies[day]

        def decode(raw):
            calls.append((getattr(worker, "day", None), "decode"))
            return real_loads(raw)

        quality = build.BuildQuality()
        days = [date.fromisoformat(key) for key in payloads]
        with patch.dict(build.LEAGUES, {"eng.1": build.LEAGUES["eng.1"]}, clear=True), \
                patch.object(build, "datetime", Clock), patch.object(build, "curl_bytes", transport), \
                patch.object(build.json, "loads", decode):
            result = build.fetch_scoreboards(days, 1, quality)
        self.assertEqual(len(result), 3, "The public fetch tuple remains unchanged")
        merged, _, failed = result
        return quality, merged, failed, calls

    def test_collection_json_shim_also_accepts_main_thread_decoding(self):
        original_fetch = build.fetch_scoreboards

        def decode_on_main(*args):
            self.assertEqual(json.loads('{"main": true}'), {"main": True})
            return original_fetch(*args)

        with patch.object(build, "fetch_scoreboards", decode_on_main):
            _, _, _, calls = self.collect({"20261023": {"events": [event()]}})
        self.assertIn((None, "decode"), calls)

    def test_each_winning_fixture_keeps_its_worker_receipt_before_decode(self):
        one, two = event(), event("second", "2026-10-24")
        original = deepcopy([one, two])
        quality, merged, failed, calls = self.collect({
            "20261023": {"events": [one]}, "20261024": {"events": [two]}})
        self.assertFalse(failed)
        matches = build.interpret_all(merged, quality)
        self.assertEqual({match.id: getattr(match, "source_received_at", None) for match in matches},
                         {"first": FIRST, "second": SECOND})
        self.assertEqual([one, two], original, "Acquisition cannot annotate provider payloads")
        for day in ("20261023", "20261024"):
            returned = calls.index((day, "transport_return"))
            self.assertEqual(calls[returned + 1:returned + 3], [(day, "clock"), (day, "decode")])
        self.assertEqual(quality.companion_scopes(NOW)[0]["validated_at"], "2026-10-23T18:00:00Z")
        value = artifacts(matches)["schedule"]
        self.assertEqual([row["status"]["observation"]["received_at"] for row in value["fixtures"]], [FIRST, SECOND])
        for row in value["fixtures"]:
            self.assertIsNone(row["status"]["observation"]["observed_at"])
            self.assertIsNone(row["status"]["observation"]["provider_updated_at"])

    def test_later_winning_duplicate_uses_its_own_receipt(self):
        case = EXAMPLES["publisher-selected-duplicate-keeps-its-response"]
        candidates = case["input"]["admitted_in_merge_order"]
        expected = case["expected"]["source_received_at"]
        quality, merged, _, _ = self.collect({
            "20261023": {"events": [event("same")]},
            "20261024": {"events": [event("same", "2026-10-24")]}},
            {"20261023": candidates[0]["received_at"], "20261024": candidates[1]["received_at"]})
        matches = build.interpret_all(merged, quality)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].utc.date(), date(2026, 10, 24))
        self.assertEqual(getattr(matches[0], "source_received_at", None), expected)

    def test_independent_same_response_examples_share_original_receipt(self):
        case = EXAMPLES["publisher-distinct-response-receipts"]
        responses = case["input"]["responses"]
        payloads = {row["requested_date"].replace("-", ""): {"events": [event(identity, row["requested_date"])
                    for identity in row["accepted_fixture_ids"]]} for row in responses}
        receipts = {row["requested_date"].replace("-", ""): row["received_at"] for row in responses}
        quality, merged, _, _ = self.collect(payloads, receipts,
            datetime.fromisoformat(case["input"]["validated_at"].replace("Z", "+00:00")))
        matches = build.interpret_all(merged, quality)
        self.assertEqual({match.id: match.source_received_at for match in matches}, case["expected"]["fixture_receipts"])
        self.assertEqual(quality.companion_scopes(NOW)[0]["validated_at"], case["expected"]["validated_at"])

    def test_failed_missing_and_out_of_scope_sources_cannot_renew_a_fixture(self):
        for later in (None, b'{"events":', {"error": "unavailable"}, {"events": []},
                      {"events": [event("first", "2026-10-23")]}):
            with self.subTest(later=later):
                quality, merged, _, _ = self.collect({"20261023": {"events": [event()]}, "20261024": later})
                matches = build.interpret_all(merged, quality)
                self.assertEqual(len(matches), 1)
                self.assertEqual(getattr(matches[0], "source_received_at", None), FIRST)

    def test_legacy_and_direct_matches_remain_unknown_despite_new_validation(self):
        source = event()
        source["source_received_at"] = FIRST
        source["observed_at"] = FIRST
        source["provider_updated_at"] = FIRST
        match = build.interpret("eng.1", source)
        self.assertIsNone(getattr(match, "source_received_at", None))
        observed = artifacts([match])["schedule"]["fixtures"][0]["status"]["observation"]
        self.assertEqual(observed, dict(source="espn", observed_at=None, provider_updated_at=None, received_at=None))
        self.assertNotIn("data-source-received-at", build.row_html(match, {}, now=NOW))
        record = asdict(match)
        record.pop("source_received_at", None)
        record["home"], record["away"] = build.Team(**record["home"]), build.Team(**record["away"])
        restored = build.Match(**record)
        self.assertIsNone(getattr(restored, "source_received_at", None))

    def test_context_html_feed_and_republication_preserve_receipt_microseconds(self):
        match = fixture(kickoff="2026-10-23T19:00:00Z")
        match.source_received_at = FIRST
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            schedule_data.publish(build, root, [match], {}, NOW, {"complete": True})
            record = json.loads((root / "schedule-context.json").read_text())["matches"][0]
        self.assertEqual(record.get("source_received_at"), FIRST)
        record["utc"] = datetime.fromisoformat(record["utc"])
        record["home"], record["away"] = build.Team(**record["home"]), build.Team(**record["away"])
        record["outlets"] = [build.Outlet(**outlet) for outlet in record["outlets"]]
        restored = build.Match(**record)
        later = NOW.replace(hour=20)
        for copy in (match, restored, replace(restored)):
            self.assertIn('data-source-received-at="' + FIRST + '"', build.row_html(copy, {}, now=later))
            result = artifacts([copy], published=later)["schedule"]
            self.assertEqual(result["fixtures"][0]["status"]["observation"]["received_at"], FIRST)
            self.assertEqual([v["observation"]["received_at"] for v in result["team_vitals"]], [FIRST, FIRST])
            self.assertEqual([v["standings_received_at"] for v in result["team_vitals"]],
                             ["2026-10-23T20:00:00Z", "2026-10-23T20:00:00Z"])

    def test_malformed_nonnull_receipts_fail_without_substituting_publication_time(self):
        for bad in EXAMPLES["invalid-explicit-receipt-cannot-fall-back"]["input"]["invalid_values"] + [{}, 1]:
            with self.subTest(receipt=bad):
                match = fixture(kickoff="2026-10-23T19:00:00Z")
                match.source_received_at = bad
                with self.assertRaisesRegex(ValueError, "source.*receipt|receipt.*UTC"):
                    build.row_html(match, {}, now=NOW)
                with self.assertRaisesRegex(ValueError, "source.*receipt|receipt.*UTC"):
                    artifacts([match])

    def test_future_receipt_is_a_fact_not_a_freshness_verdict(self):
        match = fixture(kickoff="2026-10-23T19:00:00Z")
        match.source_received_at = "2026-10-24T00:00:00Z"
        observed = artifacts([match])["schedule"]["fixtures"][0]["status"]["observation"]
        self.assertEqual(observed["received_at"], "2026-10-24T00:00:00Z")
        self.assertEqual(set(observed), {"source", "observed_at", "provider_updated_at", "received_at"})


class ReplayProvenance(unittest.TestCase):
    def run_offline_replay(self, previous, current, quality):
        """Exercise replay's provenance gate with a synthetic completed build."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "capture/full"
            original.mkdir(parents=True)
            capture.save(original.parent / "capture.json", dict(completed=True, sources_unchanged=True,
                         producer_revision="synthetic-offline"))
            capture.save(original.parent / "quality.json", dict(source_times=[], **quality))
            capture.save(original / "schedule-context.json", dict(generated_at="2026-10-23T18:00:00Z",
                         standings_at="2026-10-23T17:00:00Z"))
            with patch.object(replay, "load_requests", return_value=([], {})), \
                    patch.object(replay, "validate_publication", side_effect=[
                        ({"publication_revision": 1}, previous), ({"publication_revision": 2}, current)]), \
                    patch.object(replay, "source_hashes", return_value={}), \
                    patch.object(replay.build, "main", return_value=0), \
                    patch.object(replay, "dart_read", return_value={"publication_revision": 2}):
                return replay.replay(original.parent, directory / "replay", "unused-dart")

    def test_capture_and_replay_share_one_datetime_receipt_parser(self):
        self.assertIs(getattr(capture, "captured_event_receipts", None), replay.captured_event_receipts)

    def test_nonlegacy_replay_rejects_original_receipt_mismatch_even_if_it_is_validation_time(self):
        match = fixture(kickoff="2026-10-23T19:00:00Z")
        match.source_received_at = FIRST
        current = artifacts([match])
        previous = deepcopy(current)
        previous["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = "2026-10-23T17:59:00Z"
        del previous["schedule"]["content_revision"]
        previous["schedule"]["content_revision"] = companion_export.revision("schedule", previous["schedule"])
        quality = {"event_receipts": [dict(league="eng.1", fixture_id=match.id, received_at=FIRST)]}
        with self.assertRaisesRegex(ValueError, "recorded worker receipts"):
            self.run_offline_replay(previous, current, quality)

    def test_recorded_receipts_replay_preserves_known_feed_without_migration(self):
        match = fixture(kickoff="2026-10-23T19:00:00Z")
        match.source_received_at = FIRST
        current = artifacts([match])
        quality = {"event_receipts": [dict(league="eng.1", fixture_id=match.id, received_at=FIRST)]}
        report = self.run_offline_replay(current, current, quality)
        self.assertTrue(report["completed"])
        self.assertEqual(report["receipt_provenance"], "captured_worker_receipts")
        self.assertEqual(report["receipt_migration_changes"], [])
        self.assertTrue(report["unchanged_required_artifacts"])

    def test_receipt_migration_rejects_arbitrary_original_receipts(self):
        current = artifacts([fixture(kickoff="2026-10-23T19:00:00Z")])
        for target in ("status", "home", "away"):
            with self.subTest(target=target):
                previous = deepcopy(current)
                schedule = previous["schedule"]
                if target == "status":
                    observation = schedule["fixtures"][0]["status"]["observation"]
                else:
                    identity = schedule["fixtures"][0]["context"][target + "_vitals_id"]
                    observation = next(row for row in schedule["team_vitals"] if row["id"] == identity)["observation"]
                observation["received_at"] = SECOND
                del schedule["content_revision"]
                schedule["content_revision"] = companion_export.revision("schedule", schedule)
                with self.assertRaisesRegex(ValueError, "original league validation"):
                    replay.compare_replay_artifacts(previous, current, {})

    def test_legacy_replay_reports_original_validation_receipt_migration(self):
        current = artifacts([fixture(kickoff="2026-10-23T19:00:00Z")])
        previous = deepcopy(current)
        schedule = previous["schedule"]
        schedule["fixtures"][0]["status"]["observation"]["received_at"] = "2026-10-23T17:59:00Z"
        for vital in schedule["team_vitals"]:
            vital["observation"]["received_at"] = "2026-10-23T17:59:00Z"
        del schedule["content_revision"]
        schedule["content_revision"] = companion_export.revision("schedule", schedule)
        report = self.run_offline_replay(previous, current, {})
        self.assertTrue(report["completed"])
        self.assertEqual(report["receipt_provenance"], "legacy_worker_receipts_unknown")
        self.assertEqual(len(report["receipt_migration_changes"]), 4)
        self.assertFalse(report["unchanged_required_artifacts"])

    def test_schedule_revision_uses_the_exporter_canonical_owner(self):
        current = artifacts([fixture(kickoff="2026-10-23T19:00:00Z")])
        with patch.object(companion_export, "revision", wraps=companion_export.revision) as revision:
            self.assertEqual(replay.compare_replay_artifacts(current, current, {}), [])
        revision.assert_called_once()
        self.assertEqual(revision.call_args.args[0], "schedule")

    def test_capture_round_trip_preserves_worker_receipts_without_legacy_fallback(self):
        quality = build.BuildQuality()
        quality.event_receipts[("eng.1", "first")] = datetime.fromisoformat(FIRST.replace("Z", "+00:00"))
        recorded = quality_record(quality)
        self.assertEqual(recorded["event_receipts"], [dict(league="eng.1", fixture_id="first", received_at=FIRST)])
        self.assertEqual(replay.captured_event_receipts(recorded), quality.event_receipts)
        self.assertEqual(replay.captured_event_receipts({"source_times": [{"validated_at": FIRST}]}), {})
        recorded["event_receipts"] *= 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            replay.captured_event_receipts(recorded)

    def test_legacy_replay_permits_only_exact_receipt_fields_and_derived_revision(self):
        current = artifacts([fixture(kickoff="2026-10-23T19:00:00Z")])
        previous = deepcopy(current)
        schedule = previous["schedule"]
        schedule["fixtures"][0]["status"]["observation"]["received_at"] = "2026-10-23T17:59:00Z"
        for vital in schedule["team_vitals"]:
            vital["observation"]["received_at"] = "2026-10-23T17:59:00Z"
        del schedule["content_revision"]
        schedule["content_revision"] = companion_export.revision("schedule", schedule)
        changes = replay.compare_replay_artifacts(previous, current, {})
        self.assertEqual(len(changes), 4)
        self.assertEqual(sum(change["path"].endswith("/observation/received_at") for change in changes), 3)
        self.assertEqual(changes[-1]["path"], "schedule/content_revision")
        self.assertEqual(replay.compare_replay_artifacts(current, current, {}), [])
        for mutate in (
            lambda value: value["schedule"]["fixtures"][0]["status"].update(state="live"),
            lambda value: value["schedule"]["team_vitals"][0].update(standings_received_at=FIRST),
            lambda value: value["schedule"]["fixtures"][0]["status"]["observation"].update(observed_at=FIRST),
            lambda value: value["policy"]["freshness"].update(retained_evidence_max_seconds=1),
        ):
            changed = deepcopy(current)
            mutate(changed)
            with self.assertRaisesRegex(ValueError, "Replay changed"):
                replay.compare_replay_artifacts(previous, changed, {})

    def test_known_receipt_replay_never_uses_replay_clock(self):
        match = fixture(kickoff="2026-10-23T19:00:00Z")
        match.source_received_at = FIRST
        current = artifacts([match])
        receipts = {("eng.1", match.id): datetime.fromisoformat(FIRST.replace("Z", "+00:00"))}
        self.assertEqual(replay.compare_replay_artifacts(current, current, receipts), [])
        with self.assertRaisesRegex(ValueError, "original league validation"):
            replay.compare_replay_artifacts(current, current, {})

