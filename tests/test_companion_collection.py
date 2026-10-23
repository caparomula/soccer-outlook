"""Independent raw-input and rendered-output rejection cases for P1 evidence."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
from tests import test_render
try:
    import companion_export
    from tool import companion_collection_capture as capture
except ModuleNotFoundError as error:
    if error.name not in ("jsonschema", "referencing"):
        raise
    capture = None


@unittest.skipUnless(capture, "install tests/requirements-companion.txt")
class CollectionReconciliation(unittest.TestCase):
    def sample(self):
        event = test_render.Markup.event()
        event["date"] = "2026-10-20T19:00:00Z"
        comp = event["competitions"][0]
        comp["status"] = {"type": {"state": "post", "description": "Full Time"}}
        comp["geoBroadcasts"] = [{"media": {"shortName": "USA Network"}, "region": "us"}]
        for row in comp["competitors"]:
            row["score"] = 0
            row["form"] = "WDLWW"
        record = dict(id=1, outcome="body", url=build.ESPN.format(league="eng.1", day="20261020"),
            attempted_at="2026-10-20T18:00:00Z", received_at="2026-10-20T18:00:01Z")
        responses = {1: json.dumps({"events": [event], "leagues": [{"slug": "eng.1"}]}).encode()}
        request_scope = dict(leagues=["eng.1"], dates=["2026-10-20"])
        raw, excluded, pairs = capture.raw_fixtures([record], responses, request_scope)
        scope = dict(source="espn", league_id="eng.1", date_zone="America/New_York",
                     requested_dates=["2026-10-20"], covered_dates=["2026-10-20"], complete=True,
                     attempted_at="2026-10-20T17:59:59Z", validated_at="2026-10-20T18:00:02Z")
        now = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
        with patch.dict(build.STANDINGS, {}, clear=True):
            match = build.interpret("eng.1", event)
            match.source_received_at = "2026-10-20T18:00:01.500000Z"
            artifacts = companion_export.export_artifacts(build, [match], now, dict(complete=True, source_scopes=[scope]))
        context = dict(matches=[asdict(match)], generated_at=capture.stamp(now), standings_at=capture.stamp(now))
        rows = capture.unique_rows(capture.PublicationHTML(build.row_html(match, {})).rows)
        quality = dict(sources=[dict(league="eng.1", date="2026-10-20", ok=True)], skipped_events=[],
                       event_receipts=[dict(league="eng.1", fixture_id=match.id, received_at=match.source_received_at)],
                       source_times=[dict(league="eng.1", date="2026-10-20", attempted_at=scope["attempted_at"], validated_at=scope["validated_at"])])
        return raw, artifacts, context, rows, request_scope, quality, pairs, responses, record

    def test_raw_numeric_zero_and_rendered_facts_reconcile(self):
        raw, artifacts, context, rows, *_ = self.sample()
        result = capture.verify_facts(raw, artifacts, context, rows, rows)
        self.assertEqual(result["score_observations"], {"zero": 2})
        self.assertEqual(result["fixtures"], 1)

    def test_fixture_receipt_round_trip_and_transport_bounds_are_checked(self):
        raw, artifacts, context, rows, *_ = self.sample()
        received = "2026-10-20T18:00:01.500000Z"
        context["matches"][0]["source_received_at"] = received
        rows[next(iter(rows))]["source_received_at"] = received
        artifacts["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = received
        for vital in artifacts["schedule"]["team_vitals"]:
            vital["observation"]["received_at"] = received
        self.assertEqual(capture.verify_facts(raw, artifacts, context, rows, rows)["receipt_mode"], "original_fixture_receipt")
        with self.assertRaisesRegex(ValueError, "Legacy receipt mode"):
            capture.verify_facts(raw, artifacts, context, rows, rows, legacy_receipts=True)
        artifacts["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = "2026-10-20T18:00:02Z"
        with self.assertRaisesRegex(ValueError, "Fixture source receipt changed"):
            capture.verify_facts(raw, artifacts, context, rows, rows)

    def test_old_scope_receipt_requires_explicit_legacy_verification(self):
        raw, artifacts, context, rows, *_ = self.sample()
        context["matches"][0].pop("source_received_at", None)
        rows[next(iter(rows))]["source_received_at"] = None
        legacy = artifacts["schedule"]["source_scopes"][0]["validated_at"]
        artifacts["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = legacy
        for vital in artifacts["schedule"]["team_vitals"]:
            vital["observation"]["received_at"] = legacy
        with self.assertRaisesRegex(ValueError, "Missing recorded worker receipt"):
            capture.verify_facts(raw, artifacts, context, rows, rows)
        result = capture.verify_facts(raw, artifacts, context, rows, rows, legacy_receipts=True)
        self.assertEqual(result["receipt_mode"], "legacy_scope_validation")

    def test_nonlegacy_null_receipts_are_not_verified_provenance(self):
        raw, artifacts, context, rows, *_ = self.sample()
        context["matches"][0]["source_received_at"] = None
        rows[next(iter(rows))]["source_received_at"] = None
        artifacts["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = None
        for vital in artifacts["schedule"]["team_vitals"]:
            vital["observation"]["received_at"] = None
        with self.assertRaisesRegex(ValueError, "Missing recorded worker receipt"):
            capture.verify_facts(raw, artifacts, context, rows, rows)

    def test_reconcile_rejects_deleted_worker_receipt_even_when_outputs_are_unknown(self):
        raw, artifacts, context, rows, requested, quality, _, responses, record = self.sample()
        # Unknown historical output is legal feed data, but cannot prove that an
        # acquired fixture has its recorded receipt. Deleting that evidence must fail.
        context["matches"][0]["source_received_at"] = None
        artifacts["schedule"]["fixtures"][0]["status"]["observation"]["received_at"] = None
        for vital in artifacts["schedule"]["team_vitals"]:
            vital["observation"]["received_at"] = None
        quality["event_receipts"].pop()
        quality["excluded_events"] = 0
        match = build.interpret("eng.1", next(iter(raw.values()))["event"])
        row = build.row_html(match, {})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            site = directory / "full"
            site.mkdir()
            capture.save(directory / "capture.json", dict(completed=True, sources_unchanged=True,
                         request_scope=requested))
            capture.save(directory / "quality.json", quality)
            (directory / "responses").mkdir()
            body = responses[1]
            (directory / "responses/0001.bin").write_bytes(body)
            capture.save(directory / "requests.json", [dict(record, path="responses/0001.bin",
                         bytes=len(body), sha256=capture.digest(body))])
            (site / "schedule-context.json").write_text(json.dumps(context, default=capture.stamp))
            capture.save(site / "schedule.json", dict(complete=True, generated_at=context["generated_at"], rows=[row]))
            (site / "index.html").write_text('<main id="app" data-built="' + context["generated_at"] + '">' + row + '</main>')
            with patch.object(capture, "validate_publication", return_value=({}, artifacts)), \
                    patch.object(capture, "dart_read", side_effect=AssertionError("Missing receipt escaped reconciliation")) as reader:
                with self.assertRaisesRegex(ValueError, "Missing recorded worker receipt"):
                    capture.reconcile(directory, "unused-dart")
            reader.assert_not_called()

    def test_missing_owner_excluded_rendered_fixture_is_rejected(self):
        raw, artifacts, context, rows, *_ = self.sample()
        with self.assertRaisesRegex(ValueError, "universes"):
            capture.verify_facts(raw, artifacts, context, {}, rows)

    def test_source_score_loss_is_not_hidden_by_matching_renderer(self):
        raw, artifacts, context, rows, *_ = self.sample()
        artifacts["schedule"]["fixtures"][0]["status"]["scores"]["match"]["home"] = None
        with self.assertRaisesRegex(ValueError, "source score"):
            capture.verify_facts(raw, artifacts, context, rows, rows)

    def test_wrong_rendered_service_association_is_rejected(self):
        raw, artifacts, context, rows, *_ = self.sample()
        route = artifacts["schedule"]["fixtures"][0]["routes"][0]
        route["eligible_service_plan_ids"] = []
        with self.assertRaisesRegex(ValueError, "service plans"):
            capture.verify_facts(raw, artifacts, context, rows, rows)

    def test_requested_scope_and_receipt_order_are_independent(self):
        _, artifacts, _, _, requested, quality, pairs, *_ = self.sample()
        metadata = dict(request_scope=requested)
        capture.verify_scope(metadata, quality, artifacts["schedule"], pairs)
        quality["source_times"][0]["validated_at"] = "2026-10-20T18:00:00Z"
        artifacts["schedule"]["source_scopes"][0]["validated_at"] = "2026-10-20T18:00:00Z"
        with self.assertRaisesRegex(ValueError, "receipt"):
            capture.verify_scope(metadata, quality, artifacts["schedule"], pairs)

    def test_wrong_request_date_and_missing_request_are_rejected(self):
        *_, requested, quality, pairs, responses, record = self.sample()
        wrong = deepcopy(record)
        wrong["url"] = build.ESPN.format(league="eng.1", day="20261021")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            capture.raw_fixtures([wrong], responses, requested)
        with self.assertRaisesRegex(ValueError, "Cartesian"):
            capture.raw_fixtures([], {}, requested)

    def test_raw_kickoff_outside_requested_day_is_rejected(self):
        *_, requested, quality, pairs, responses, record = self.sample()
        data = json.loads(responses[1])
        data["events"][0]["date"] = "2026-10-21T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "outside"):
            capture.raw_fixtures([record], {1: json.dumps(data).encode()}, requested)

    def test_capture_preserves_unavailable_calls_and_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            transport = capture.RecordingTransport(directory, lambda *args, **kwargs: b'{"events":[]}')
            self.assertEqual(transport("https://example.org/source"), b'{"events":[]}')
            records, values = capture.load_requests(directory)
            self.assertEqual(values[1], b'{"events":[]}')
            (directory / records[0]["path"]).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                capture.load_requests(directory)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            transport = capture.RecordingTransport(directory, lambda *args, **kwargs: None)
            self.assertIsNone(transport("https://example.org/unavailable"))
            records, values = capture.load_requests(directory)
            self.assertEqual(records[0]["outcome"], "unavailable")
            self.assertEqual(values, {})

    def test_failed_build_evidence_is_retained_and_reuse_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new"
            with patch.object(build, "main", return_value=3), patch.object(capture, "source_hashes", return_value={}):
                with self.assertRaisesRegex(ValueError, "Build failed"):
                    capture.capture(output, 1)
            metadata = json.loads((output / "capture.json").read_text())
            self.assertFalse(metadata["completed"])
            self.assertEqual(metadata["build_exit_code"], 3)
            self.assertTrue((output / "requests.json").exists())
            with self.assertRaisesRegex(ValueError, "fresh"):
                capture.capture(output, 1)


if __name__ == "__main__":
    unittest.main()
