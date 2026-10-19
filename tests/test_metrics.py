"""Historical observations must preserve the evidence without rewriting predictions."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

import build
import metrics
from tests import test_render


class MetricCapture(unittest.TestCase):
    def setUp(self):
        self.event = test_render.Markup.event()
        self.match = build.interpret("eng.1", self.event)
        self.before = self.match.utc - timedelta(hours=2)

    def capture(self, matches=None, when=None):
        return metrics.snapshot(build, matches if matches is not None else [self.match],
                                {"eng.1": {self.match.id: self.event}}, when or self.before,
                                source_revision="test-revision")

    def test_all_parts_raw_inputs_and_missing_values_survive_json(self):
        comp = self.event["competitions"][0]
        comp["odds"] = [{"provider": {"id": "1", "name": "Test bookmaker"},
                         "homeTeamOdds": {"moneyLine": 100}, "awayTeamOdds": {"moneyLine": 200},
                         "drawOdds": {"moneyLine": 300}, "overUnder": 3.5}]
        comp["geoBroadcasts"] = [{"media": {"shortName": "ABC"}, "region": "us"}]
        self.match = build.interpret("eng.1", self.event)
        before = deepcopy(self.event)
        snapshot = json.loads(json.dumps(self.capture(), allow_nan=False))
        row = snapshot["matches"][0]
        self.assertEqual(row["outlook"]["inputs"]["odds"], comp["odds"][0])
        self.assertEqual(row["outlook"]["inputs"]["draw_probability"], 0.25)
        self.assertEqual(row["outlook"]["inputs"]["home_win_probability"], 0.5)
        self.assertAlmostEqual(row["outlook"]["inputs"]["away_win_probability"], 1 / 3)
        self.assertAlmostEqual(row["outlook"]["inputs"]["win_balance"], 0.8)
        self.assertAlmostEqual(row["outlook"]["parts"]["close"], 0.8)
        self.assertNotIn("tv", row["outlook"]["parts"])
        self.assertEqual(row["outlook"]["parts"]["goals"], 0.75)
        self.assertEqual(set(row["outlook"]["parts"]), set(build.OUTLOOK_PARTS))
        self.assertEqual(row["outlook"]["score"], build.outlook_score(build.outlook_parts(self.match)))
        self.assertEqual(row["coverage"]["espn_listings"], comp["geoBroadcasts"])
        self.assertEqual(snapshot["settings"]["outlook"]["weights"], build.SETTINGS.outlook["weights"])
        self.assertEqual(snapshot["settings"]["outlook_calibration"], "watch-appeal-v2")
        self.assertEqual(snapshot["settings"]["dots"], {"scale": 6, "score_max": 100, "rounding": "nearest_half_up"})
        self.assertEqual(snapshot["league_order"], list(build.LEAGUES))
        self.assertEqual(self.event, before)

    def test_attendance_unknown_is_never_zero_and_audience_is_missing(self):
        comp = self.event["competitions"][0]
        for value in (None, 0, "0", True, -10, "unreported", 30.5):
            with self.subTest(value=value):
                comp["attendance"] = value
                row = self.capture()["matches"][0]
                self.assertIsNone(row["observed"]["attendance"])
                self.assertEqual(row["observed"]["attendance_raw"], value)
                self.assertIsNone(row["observed"]["viewership"])
        comp["attendance"] = "42500"
        comp["venue"] = {"fullName": "Test stadium", "capacity": 50000}
        row = self.capture()["matches"][0]
        self.assertEqual(row["observed"]["attendance"], 42500)
        self.assertEqual(row["observed"]["attendance_status"], "reported")
        self.assertEqual(row["observed"]["stadium_capacity"], 50000)
        self.assertEqual(row["observed"]["occupancy"], 0.85)

    def test_only_confirmed_future_pregame_observations_are_forecasts(self):
        scenarios = [({}, self.before, True), ({}, self.match.utc, False),
                     ({"state": "in"}, self.before, False), ({"state": "post"}, self.before, False),
                     ({"time_valid": False}, self.before, False),
                     ({"status": "Postponed"}, self.before, False)]
        for changes, when, eligible in scenarios:
            with self.subTest(changes=changes, when=when):
                row = self.capture([replace(self.match, **changes)], when)["matches"][0]
                self.assertEqual(row["forecast_eligible"], eligible)
        with self.assertRaises(ValueError):
            self.capture(when=datetime(2026, 10, 7))

    def test_finished_unavailable_and_disabled_matches_are_retained(self):
        fixtures = [self.match, replace(self.match, id="post", state="post"),
                    replace(self.match, id="disabled", league="eng.2", service="", outlets=[])]
        rows = {row["id"]: row for row in self.capture(fixtures)["matches"]}
        self.assertEqual(set(rows), {self.match.id, "post", "disabled"})
        self.assertFalse(rows["post"]["forecast_eligible"])
        self.assertFalse(rows["disabled"]["coverage"]["default_competition"])
        self.assertEqual(rows["disabled"]["coverage"]["listed"], [])
        self.assertNotIn("tv", rows["disabled"]["outlook"]["parts"])
        self.assertNotIn("tv", rows["disabled"]["outlook"]["missing_components"])

    def test_draw_only_odds_are_retained_without_inventing_win_balance(self):
        self.event["competitions"][0]["odds"] = [{"drawOdds": {"moneyLine": 250}}]
        self.match = build.interpret("eng.1", self.event)
        row = self.capture()["matches"][0]["outlook"]
        self.assertAlmostEqual(row["inputs"]["draw_probability"], 1 / 3.5)
        for key in ("home_win_probability", "away_win_probability", "win_balance"):
            self.assertIsNone(row["inputs"][key])
        self.assertIsNone(row["parts"]["close"])
        self.assertIn("close", row["missing_components"])

    def test_final_zero_score_survives_and_void_placeholder_does_not(self):
        comp = self.event["competitions"][0]
        for competitor in comp["competitors"]:
            competitor["score"] = 2 if competitor["homeAway"] == "home" else 0
        finished = replace(self.match, state="post", status="FT")
        result = self.capture([finished])["matches"][0]["observed"]
        self.assertEqual((result["home_score"], result["away_score"]), (2, 0))
        self.assertTrue(result["score_is_final"])
        self.event["status"] = {"type": {"state": "pre", "description": "Postponed"}}
        row = self.capture()["matches"][0]
        self.assertFalse(row["forecast_eligible"])
        result = self.capture([replace(finished, status="Postponed")])["matches"][0]["observed"]
        self.assertIsNone(result["away_score"])
        self.assertFalse(result["score_is_final"])


if __name__ == "__main__":
    unittest.main()
