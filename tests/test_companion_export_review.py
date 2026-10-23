"""Independent P1 regressions from exporter review, with explicit expected facts."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import companion_export as exporter
from tests.test_discovery import fixture

try:
    import companion_contract as contract
except ModuleNotFoundError as error:
    if error.name not in ("jsonschema", "referencing"):
        raise
    contract = None


NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def scope(league="eng.1", dates=("2026-10-20",), zone="America/New_York"):
    return dict(source="espn", league_id=league, date_zone=zone,
                requested_dates=list(dates), covered_dates=list(dates), complete=True,
                attempted_at="2026-10-20T17:00:00Z", validated_at="2026-10-20T17:01:00Z")


def evidence(channel="USA Network", **changes):
    return dict(channel=channel, source="Review source", url="https://example.org/event/123",
                country="US", kind="match_listing", checked_at="2026-10-20T16:00:00Z",
                **changes)


@unittest.skipUnless(contract, "install tests/requirements-companion.txt")
class CompanionExportReview(unittest.TestCase):
    def export(self, matches=(), scopes=None):
        report = dict(complete=True, source_scopes=scopes or [scope()])
        with patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
            result = exporter.export_artifacts(build, matches, NOW, report)
        contract.validate_artifacts(result)
        return result

    def test_linear_provider_and_separate_subscription_routes_keep_distinct_scopes(self):
        cases = [("USA Network", "eng.1", "linear_channel"),
                 ("Paramount+", "ita.1", "separate_subscription"),
                 ("FOX One", "eng.1", "separate_subscription"),
                 ("Fox Sports app", "eng.1", "provider_stream"),
                 ("beIN Sports Connect", "por.1", "provider_stream")]
        for channel, league, expected in cases:
            with self.subTest(channel=channel):
                match = fixture(league=league, channel=channel, kickoff="2026-10-20T19:00:00Z")
                route = self.export([match], [scope(league)])["schedule"]["fixtures"][0]["routes"][0]
                self.assertEqual(route["scope"], expected)

    def test_source_event_identity_original_observation_and_airing_detail_survive(self):
        match = fixture(kickoff="2026-10-20T19:00:00Z")
        match.broadcast_evidence = [evidence(source_event_id="listing-123", authority="primary",
                                             broadcast_start="2026-10-20T18:55:00-00:00")]
        result = self.export([match])["schedule"]
        observed = result["evidence"][0]
        self.assertEqual(observed["source_refs"], [{"source": observed["source"], "id": "listing-123"}])
        self.assertEqual(observed["source_event_id"], "listing-123")
        self.assertEqual(observed["broadcast_start"], "2026-10-20T18:55:00-00:00")
        self.assertEqual(observed["authority"], "primary")
        self.assertEqual(observed["observed_at"], "2026-10-20T16:00:00Z")
        self.assertEqual(result["fixtures"][0]["routes"][0]["verified_at"], "2026-10-20T16:00:00Z")

    def test_unconfirmed_placeholder_preserves_calendar_context_without_inventing_instant(self):
        match = fixture(time_valid=False, kickoff="2026-10-21T01:00:00Z")
        timing = self.export([match])["schedule"]["fixtures"][0]["timing"]
        self.assertEqual(timing["scheduled_date"], "2026-10-20")
        self.assertIsNone(timing["scheduled_date_zone"])
        self.assertIsNone(timing["kickoff"])
        self.assertFalse(timing["confirmed"])

    def test_status_receipt_does_not_become_known_source_observation(self):
        match = fixture(state="in", kickoff="2026-10-20T17:00:00Z")
        match.source_received_at = "2026-10-20T17:00:01.123456Z"
        observed = self.export([match])["schedule"]["fixtures"][0]["status"]["observation"]
        self.assertIsNone(observed["observed_at"])
        self.assertIsNone(observed["provider_updated_at"])
        self.assertEqual(observed["received_at"], "2026-10-20T17:00:01.123456Z")

    def test_retained_expiry_is_limited_by_kickoff_even_when_six_hours_is_later(self):
        match = fixture(state="in", kickoff="2026-10-20T15:00:00Z")
        match.broadcast_evidence = [evidence(retained=True)]
        route = self.export([match])["schedule"]["fixtures"][0]["routes"][0]
        self.assertEqual(route["verified_at"], "2026-10-20T16:00:00Z")
        self.assertEqual(route["expires_at"], "2026-10-20T19:00:00Z")

    def test_horizon_uses_calendar_midnights_across_both_dst_transitions(self):
        cases = [("2026-03-08", "2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"),
                 ("2026-11-01", "2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z")]
        for day, start, end in cases:
            with self.subTest(day=day):
                horizon = self.export(scopes=[scope(dates=(day,))])["schedule"]["confirmed_horizon"]
                self.assertEqual(horizon, {"start": start, "end_exclusive": end})

    def test_gapped_or_mixed_zone_scopes_do_not_claim_a_single_contiguous_horizon(self):
        for scopes in ([scope(dates=("2026-10-20", "2026-10-22"))],
                       [scope(), scope("ita.1", zone="Europe/Rome")]):
            with self.subTest(scopes=scopes):
                self.assertIsNone(self.export(scopes=scopes)["schedule"]["confirmed_horizon"])

    def test_unknown_zone_cannot_hide_behind_mixed_scope_horizon_fallback(self):
        with self.assertRaises(ValueError):
            self.export(scopes=[scope(), scope("ita.1", zone="Invalid/Review_Zone")])

    def test_complete_flag_cannot_override_missing_required_collection_dates(self):
        broken = scope(dates=("2026-10-20", "2026-10-21"))
        broken["covered_dates"] = ["2026-10-20"]
        with self.assertRaises(ValueError):
            self.export(scopes=[broken])
        broken = scope()
        broken["complete"] = False
        with self.assertRaises(ValueError):
            self.export(scopes=[broken])

    def test_fixture_outside_its_reported_collection_day_is_not_silently_published(self):
        # 03:00 UTC is still October 19 in the Eastern collection zone.
        outside = fixture(kickoff="2026-10-20T03:00:00Z")
        with self.assertRaises(ValueError):
            self.export([outside])
        boundary = fixture(kickoff="2026-10-20T04:00:00Z")
        self.assertEqual(len(self.export([boundary])["schedule"]["fixtures"]), 1)

    def test_malformed_optional_shared_shapes_cannot_block_required_schedule(self):
        required = self.export([fixture(kickoff="2026-10-20T19:00:00Z")])
        for malformed in ([], None, "malformed", 1, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "shared.json"
                path.write_text(json.dumps(malformed))
                result = exporter.add_editorial(required, shared_path=path, published_at=NOW)
                self.assertEqual(result["schedule"], required["schedule"])
                self.assertIsNone(result["editorial"]["shared"])
                self.assertTrue(result["editorial"]["diagnostics"])
                contract.validate_artifacts(result)

    def editorial(self, value, *, automatic=False, unknown_kickoff=False):
        required = self.export([fixture("review-match", kickoff="2026-10-20T19:00:00Z",
                                        time_valid=not unknown_kickoff)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "editorial.json"
            path.write_text(json.dumps(value))
            result = exporter.add_editorial(required, published_at=NOW,
                **{("automatic_path" if automatic else "shared_path"): path})
        contract.validate_artifacts(result)
        return result["editorial"]

    def automatic(self):
        return dict(generated_at="2026-10-20T17:00:00Z", focus_until="2026-10-21T17:00:00Z",
            rating_weights={"popularity": .5, "gameplay": .35, "impact": .15},
            rankings={"review-match": dict(popularity=80, gameplay=60, impact=70, score=71.5,
                companion_origin=dict(generated_at="2026-10-20T12:00:00Z",
                    prompted_at="2026-10-20T11:59:00Z", expires_at="2026-10-21T12:00:00Z",
                    fixture_bindings=[dict(id="review-match", league_id="eng.1",
                                           kickoff_utc="2026-10-20T19:00:00Z")]))})

    def shared(self):
        return dict(version=1, created_at=1792519200000, weight=70,
            fixtures=[dict(id="review-match", league_id="eng.1", kickoff="2026-10-20T19:00:00Z")],
            response=dict(version=2, request_id="review-shared-request", headlines=[],
                matches=[dict(id="review-match", score=70, blurb="Review text", sources=[])]))

    def test_automatic_original_provenance_and_item_identity_survive_wrapper_refresh(self):
        first_input = self.automatic()
        later_input = deepcopy(first_input)
        later_input["generated_at"] = "2026-10-20T17:30:00Z"
        first = self.editorial(first_input, automatic=True)["automatic"]["matches"][0]
        later = self.editorial(later_input, automatic=True)["automatic"]["matches"][0]
        self.assertEqual(first, later)
        self.assertEqual(first["expires_at"], "2026-10-21T12:00:00Z")
        self.assertEqual(first["provenance"], dict(generated_at="2026-10-20T12:00:00Z",
            prompted_at="2026-10-20T11:59:00Z", expires_at="2026-10-21T12:00:00Z"))

    def test_overage_blurb_with_future_expiry_is_omitted_without_losing_fresh_rating(self):
        value = self.automatic()
        rating = value["rankings"]["review-match"]
        rating["blurb"] = "This text is older than the thirty-hour limit."
        rating["sources"] = []
        rating["companion_blurb_origin"] = dict(generated_at="2026-10-19T10:00:00Z",
            expires_at="2026-10-20T23:00:00Z",
            fixture_bindings=[dict(id="review-match", league_id="eng.1", kickoff_utc="2026-10-20T19:00:00Z")])
        result = self.editorial(value, automatic=True)
        accepted = result["automatic"]["matches"][0]
        self.assertEqual(accepted["rating_total"], 71.5)
        self.assertEqual(accepted["text"], "")
        self.assertEqual(accepted["expires_at"], "2026-10-21T12:00:00Z")
        self.assertTrue(any("blurb omitted" in item for item in result["diagnostics"]))

    def test_missing_original_kickoff_cannot_bind_to_unknown_active_kickoff(self):
        value = self.shared()
        del value["fixtures"][0]["kickoff"]
        self.assertIsNone(self.editorial(value, unknown_kickoff=True)["shared"])
        value["fixtures"][0]["kickoff"] = None
        self.assertIsNotNone(self.editorial(value, unknown_kickoff=True)["shared"])

    def test_unknown_shared_publication_time_stays_null_and_original_prompt_is_preserved(self):
        accepted = self.editorial(self.shared())["shared"]
        self.assertIsNotNone(accepted)
        self.assertIsNone(accepted["published_at"])
        self.assertEqual(accepted["prompted_at"], "2026-10-20T18:00:00Z")
        self.assertEqual(accepted["expires_at"], "2026-10-21T18:00:00Z")


if __name__ == "__main__":
    unittest.main()
