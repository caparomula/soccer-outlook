"""Producer facts and original-time regression cases, independent expected values."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import companion_export as export
from tests import test_render
from tests.test_discovery import fixture
from tests.test_shared_ai import envelope as shared_envelope

try:
    import companion_contract as contract
except ModuleNotFoundError as error:
    if error.name not in ("jsonschema", "referencing"):
        raise
    contract = None

NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def report(leagues=("eng.1",)):
    return dict(complete=True, source_scopes=[dict(source="espn", league_id=league,
        date_zone="America/New_York", requested_dates=["2026-10-20"], covered_dates=["2026-10-20"],
        complete=True, attempted_at="2026-10-20T17:59:00Z", validated_at="2026-10-20T18:00:00Z")
        for league in leagues])


@unittest.skipUnless(contract, "install tests/requirements-companion.txt")
class CompanionExport(unittest.TestCase):
    def test_raw_numeric_zero_survives_source_interpretation_and_feed(self):
        event = test_render.Markup.event()
        event["date"] = "2026-10-20T19:00:00Z"
        competition = event["competitions"][0]
        competition["date"] = event["date"]
        competition["status"] = {"type": {"state": "in"}, "displayClock": "12'"}
        for item in competition["competitors"]:
            item["score"] = 0 if item["homeAway"] == "home" else None
        match = build.interpret("eng.1", event)
        self.assertEqual(match.home.score, "0")
        self.assertEqual(match.away.score, "")
        scores = self.artifacts([match])["schedule"]["fixtures"][0]["status"]["scores"]["match"]
        self.assertEqual(scores, {"home": 0, "away": None})

    def artifacts(self, matches=None, **kwargs):
        matches = [fixture("123456", kickoff="2026-10-20T19:00:00Z")] if matches is None else matches
        scope = report(sorted({m.league for m in matches}) or ["eng.1"])
        for entry in scope["source_scopes"]:
            days = sorted({m.utc.astimezone(build.ET).date().isoformat() for m in matches if m.league == entry["league_id"]}) or ["2026-10-20"]
            entry["requested_dates"] = days
            entry["covered_dates"] = days
        with patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
            value = export.export_artifacts(build, matches, NOW, kwargs.pop("report", scope), **kwargs)
        contract.validate_artifacts(value)
        return value

    def test_complete_empty_has_scope_and_calendar_horizon(self):
        schedule = self.artifacts([])["schedule"]
        self.assertEqual(schedule["fixtures"], [])
        self.assertEqual(schedule["confirmed_horizon"], {"start":"2026-10-20T04:00:00Z", "end_exclusive":"2026-10-21T04:00:00Z"})

    def test_defaults_are_published_but_fixture_entitlements_are_unfiltered(self):
        value = self.artifacts([fixture(channel="Universo")])
        route = value["schedule"]["fixtures"][0]["routes"][0]
        self.assertNotIn("yttv", route["eligible_service_plan_ids"])
        self.assertIn("yttvspanishplus", route["eligible_service_plan_ids"])
        self.assertIn("yttv", route["excluded_service_plan_ids"])
        self.assertEqual(value["policy"]["defaults"]["enabled_service_plan_ids"], build.OWNER)
        self.assertNotIn("selected_service_plan_id", value["schedule"]["fixtures"][0])

    def test_pregame_zero_missing_and_actual_zero_stay_distinct(self):
        pre = fixture("pre", home=replace(fixture().home, score="0"))
        played = fixture("played", state="in", home=replace(pre.home, score="0"), away=replace(pre.away, score=""))
        schedules = self.artifacts([pre, played])["schedule"]["fixtures"]
        self.assertEqual(schedules[0]["status"]["scores"]["match"], {"home":None,"away":None})
        self.assertEqual(schedules[1]["status"]["scores"]["match"], {"home":0,"away":None})
        self.assertIsNone(schedules[1]["status"]["observation"]["observed_at"])
        self.assertIsNone(schedules[1]["status"]["observation"]["received_at"])

    def test_unknown_kickoff_is_not_midnight_or_a_countdown(self):
        timing = self.artifacts([fixture(time_valid=False)])["schedule"]["fixtures"][0]["timing"]
        self.assertFalse(timing["confirmed"])
        self.assertIsNone(timing["kickoff"])
        self.assertEqual(timing["scheduled_date"],"2026-10-09")
        self.assertIsNone(timing["scheduled_date_zone"])

    def test_original_standings_receipt_and_source_reported_form_are_preserved(self):
        value = self.artifacts([fixture(home=replace(fixture().home, form="WDLWW",rank=2,size=20))],
                               standings_at=NOW-timedelta(hours=5))
        vitals = value["schedule"]["team_vitals"][0]
        self.assertEqual(vitals["standings_received_at"], "2026-10-20T13:00:00Z")
        self.assertEqual(vitals["form"], ["W", "W", "L", "D", "W"])
        self.assertEqual(vitals["raw_form"], "WDLWW")
        self.assertEqual(vitals["form_order"], "oldest_to_newest")
        self.assertEqual(vitals["form_provenance"], {
            "source_order": "newest_to_oldest", "order_basis": "espn-dated-comparison-2026-10-20",
            "values_verified": False, "as_of": None, "competition_scope": None})
        self.assertEqual(vitals["rank"]["table_size"], 20)

    def test_missing_or_unrecognized_form_does_not_invent_result_slots(self):
        for raw in ("", "WD?WW", "WWDLWW"):
            with self.subTest(raw=raw):
                vitals = self.artifacts([fixture(home=replace(fixture().home, form=raw))])["schedule"]["team_vitals"][0]
                self.assertEqual(vitals["form"], [])
                self.assertEqual(vitals["raw_form"], raw or None)
                self.assertIsNone(vitals["form_provenance"]["source_order"])

    def test_partial_fixture_collection_or_missing_scope_is_refused(self):
        for broken in [dict(complete=False),dict(complete=True),report()]:
            if broken.get("source_scopes"):
                broken["source_scopes"][0]["covered_dates"]=[]
            with self.assertRaises(ValueError):self.artifacts(report=broken)

    def test_partial_broadcast_is_visible_without_discarding_fixtures(self):
        r=report();r["broadcast_health"]={"Example":{"status":"partial","failed":1}}
        s=self.artifacts(report=r)["schedule"]
        self.assertEqual(len(s["fixtures"]),1)
        self.assertEqual(s["broadcast_sources"][0]["state"],"partial")
        self.assertIsNone(s["broadcast_sources"][0]["observed_at"])

    def test_retained_listing_has_original_age_and_fixed_minimum_expiry(self):
        m=fixture(kickoff="2026-10-20T19:00:00Z",broadcast_evidence=[dict(channel="USA Network",source="ESPN",url="https://example.com/match",kind="match_listing",country="US",checked_at="2026-10-20T15:00:00Z",retained=True)])
        route=self.artifacts([m])["schedule"]["fixtures"][0]["routes"][0]
        self.assertEqual(route["verified_at"],"2026-10-20T15:00:00Z")
        self.assertEqual(route["expires_at"],"2026-10-20T21:00:00Z")

    def test_replay_and_foreign_listing_never_gain_eligible_routes(self):
        for kind,country in [("replay","US"),("match_listing","GB")]:
            m=fixture(broadcast_evidence=[dict(channel="USA Network",source="source",url="https://example.com/match",kind=kind,country=country,checked_at="2026-10-20T17:00:00Z")])
            s=self.artifacts([m])["schedule"]
            self.assertEqual(s["fixtures"][0]["routes"][0]["eligible_service_plan_ids"],[])
            self.assertEqual(s["evidence"][0]["verdict"],"rejected")

    def test_canonical_outlook_uses_existing_owner(self):
        m=fixture(score=150,win_balance=0.8,goal_line=3)
        outlook=self.artifacts([m])["schedule"]["fixtures"][0]["outlook"]
        # 50*1 + 25*.8 + 10*.5 (missing) + 15*.5.
        self.assertEqual(outlook["total"],82.5)
        self.assertEqual(outlook["components"],dict(stature=1,close=.8,stakes=None,goals=.5))
        self.assertEqual(outlook["missing_inputs"],["stakes"])

    def test_no_source_or_settings_mutation_and_deterministic_revision(self):
        m=fixture();before=deepcopy(m)
        a=self.artifacts([m]);b=self.artifacts([m])
        self.assertEqual(a,b);self.assertEqual(m,before)

    def editorial(self, value, *, automatic=False, now=NOW):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"optional.json";path.write_text(json.dumps(value))
            result=export.add_editorial(self.artifacts(),published_at=now,
                **{("automatic_path" if automatic else "shared_path"):path})
        contract.validate_artifacts(result)
        return result["editorial"]

    def test_shared_original_expiry_and_prompt_survive_republication(self):
        value=shared_envelope(NOW)
        first=self.editorial(value);later=self.editorial(value,now=NOW+timedelta(hours=1))
        self.assertEqual(first,later)
        self.assertIsNone(first["shared"]["published_at"])
        self.assertEqual(first["shared"]["prompted_at"],"2026-10-20T18:00:00Z")
        self.assertEqual(first["shared"]["expires_at"],"2026-10-21T18:00:00Z")

    def test_shared_rescheduled_binding_or_bad_optional_input_is_isolated(self):
        value=shared_envelope(NOW);value["fixtures"][0]["kickoff"]="2026-10-20T20:00:00Z"
        for broken in (value,{"personal_ai":"private"}):
            editorial=self.editorial(broken)
            self.assertIsNone(editorial["shared"]);self.assertTrue(editorial["diagnostics"])
            self.assertNotIn("private",json.dumps(editorial))

    def test_automatic_legacy_unbound_is_not_rebound_to_current_schedule(self):
        value=dict(generated_at="2026-10-20T17:00:00Z",focus_until="2026-10-21T17:00:00Z",
                   rankings={"123456":dict(popularity=80,gameplay=60,impact=70,score=72)},
                   rating_weights=dict(popularity=.5,gameplay=.35,impact=.15))
        editorial=self.editorial(value,automatic=True)
        self.assertIsNone(editorial["automatic"])
        self.assertIn("original binding",editorial["diagnostics"][0])

    def test_automatic_bound_entry_preserves_older_expiry(self):
        value=dict(generated_at="2026-10-20T17:00:00Z",focus_until="2026-10-21T17:00:00Z",
                   rankings={"123456":dict(popularity=80,gameplay=60,impact=70,score=71.5,
                       companion_origin=dict(generated_at="2026-10-20T12:00:00Z",expires_at="2026-10-21T12:00:00Z",
                         fixture_bindings=[dict(id="123456",league_id="eng.1",kickoff_utc="2026-10-20T19:00:00Z")]))},
                   rating_weights=dict(popularity=.5,gameplay=.35,impact=.15))
        editorial=self.editorial(value,automatic=True)
        self.assertEqual(editorial["automatic"]["matches"][0]["expires_at"],"2026-10-21T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
