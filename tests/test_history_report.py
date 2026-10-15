"""Retrospective exports must keep future knowledge out of frozen predictions and baselines."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

import history_report as report


KICKOFF = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)


def snapshot(mid="m1", kickoff=KICKOFF, *, observed=None, captured=None, state="pre", rating=60,
             attendance=None, capacity=20000, league="eng.1", home="home1", venue="stadium1"):
    observed = observed or kickoff + timedelta(hours=3 if state == "post" else -2)
    captured = captured or observed + timedelta(minutes=1)
    eligible = state == "pre" and observed < kickoff
    ai_eligible = eligible and captured < kickoff
    return {"version": 1, "observed_at": report.iso(observed), "captured_at": report.iso(captured),
            "source_revision": "revision-" + str(rating),
            "settings": {"blend": {"ai": 70, "outlook": 30, "interest": 95, "league_priority": 5},
                         "outlook": {}, "ai": {"enabled": True}},
            "league_order": [league], "default_services": [], "default_leagues": [league],
            "ai_sources": {"automatic": {"model": "test-model", "requested_model": "test", "effort": "low",
                                           "generated_at": report.iso(observed)}},
            "matches": [{"id": mid, "league_id": league, "competition": "Test league", "kickoff_utc": report.iso(kickoff),
                         "home": {"id": home, "name": "Home FC"}, "away": {"id": "away", "name": "Away FC"},
                         "venue": "Test Stadium", "venue_details": {"id": venue, "capacity": capacity},
                         "state": state, "status": "FT" if state == "post" else "", "time_confirmed": True,
                         "forecast_eligible": eligible, "source_url": "https://www.espn.com/soccer/match/_/gameId/" + mid,
                         "outlook": {"score": rating, "parts": dict(stature=.5, close=None, stakes=.6, tv=.5, goals=.7),
                                     "inputs": {"stature": 75, "draw_probability": None, "goal_line": 3.4}, "missing_components": ["close"]},
                         "ai": {"automatic": {"popularity": rating, "gameplay": rating, "impact": rating, "score": rating,
                                                "eligible_at_capture": ai_eligible}},
                         "automatic_default": {"interest": rating, "pick": rating + 2, "league_priority": 100,
                                               "ai_used": True, "forecast_eligible": ai_eligible},
                         "observed": {"attendance": attendance, "stadium_capacity": capacity,
                                      "score_is_final": state == "post", "home_score": 2 if state == "post" else None,
                                      "away_score": 0 if state == "post" else None}}]}


def external(mid, metric, value, observed, *, league="eng.1", identifier=None):
    return {"id": identifier or mid + "-" + metric, "match_id": mid, "league_id": league,
            "metric": metric, "value": value, "unit": "people" if metric == "attendance" else "seats",
            "observed_at": report.iso(observed), "source_url": "https://league.example/report",
            "scope": "Official match attendance" if metric == "attendance" else "Match venue seated capacity"}


class HistoryReport(unittest.TestCase):
    def row(self, snapshots, mid="m1", league="eng.1"):
        return next(row for row in report.report_rows(snapshots) if row["match_id"] == mid and row["league_id"] == league)

    def test_latest_safe_forecast_is_frozen_and_postmatch_recalculation_is_ignored(self):
        early = snapshot(rating=40, observed=KICKOFF - timedelta(hours=4))
        latest = snapshot(rating=70, observed=KICKOFF - timedelta(hours=1))
        final = snapshot(state="post", rating=99, attendance=15000)
        row = self.row([final, early, latest])
        self.assertEqual(row["outlook_score"], 70)
        self.assertEqual((row["ai_popularity"], row["ai_gameplay"], row["ai_impact"]), (70, 70, 70))
        self.assertEqual(row["automatic_pick"], 72)
        self.assertEqual(row["forecast_observed_at"], latest["observed_at"])
        self.assertEqual(row["source_revision"], "revision-70")
        self.assertEqual((row["home_score"], row["away_score"], row["total_goals"]), (2, 0, 2))
        self.assertEqual(row["raw_stature"], 75)
        self.assertEqual(row["goal_line"], 3.4)
        self.assertIsNone(row["draw_probability"])
        self.assertEqual(row["missing_components"], "close")
        self.assertEqual(row["stadium_occupancy"], .75)

    def test_late_ai_capture_does_not_replace_earlier_safe_ai_and_has_distinct_provenance(self):
        early = snapshot(rating=40, observed=KICKOFF - timedelta(hours=4))
        late = snapshot(rating=90, observed=KICKOFF - timedelta(minutes=1), captured=KICKOFF + timedelta(minutes=2))
        # Even a mistaken flag must not let a post-kickoff archive become an AI forecast.
        late["matches"][0]["ai"]["automatic"]["eligible_at_capture"] = True
        late["matches"][0]["automatic_default"]["forecast_eligible"] = True
        row = self.row([early, late])
        self.assertEqual(row["outlook_score"], 90)
        self.assertEqual(row["ai_score"], 40)
        self.assertEqual(row["blend_outlook_score"], 40)
        self.assertEqual(row["automatic_pick"], 42)
        self.assertEqual(row["ai_forecast_captured_at"], early["captured_at"])
        self.assertEqual(row["blend_source_revision"], "revision-40")
        only_late = self.row([late])
        self.assertIsNone(only_late["ai_score"])
        self.assertIsNone(only_late["automatic_pick"])

    def test_rescheduled_match_requires_a_forecast_for_the_new_kickoff(self):
        early = snapshot(rating=40)
        moved = KICKOFF + timedelta(days=2)
        final = snapshot(kickoff=moved, state="post", attendance=10000)
        row = self.row([early, final])
        self.assertIsNone(row["outlook_score"])
        self.assertIsNone(row["ai_score"])
        corrected = snapshot(kickoff=moved, rating=55)
        self.assertEqual(self.row([early, corrected, final])["outlook_score"], 55)

    def test_live_uncertain_and_ineligible_records_cannot_supply_predictions_or_final_scores(self):
        for change in ({"state": "in"}, {"time_confirmed": False}, {"forecast_eligible": False}):
            with self.subTest(change=change):
                source = snapshot()
                source["matches"][0].update(change)
                row = self.row([source])
                self.assertIsNone(row["outlook_score"])
                self.assertIsNone(row["home_score"])

    def test_missing_attendance_does_not_erase_reported_attendance_and_placeholders_stay_missing(self):
        first = snapshot(state="post", attendance=12500)
        later = snapshot(state="post", attendance=0, observed=KICKOFF + timedelta(days=1))
        row = self.row([first, later])
        self.assertEqual(row["attendance"], 12500)
        self.assertEqual(row["attendance_observed_at"], first["observed_at"])
        self.assertIsNone(self.row([later])["attendance"])
        cancelled = deepcopy(later)
        cancelled["matches"][0]["status"] = "Cancelled"
        cancelled["matches"][0]["observed"]["score_is_final"] = False
        row = self.row([first, cancelled])
        self.assertIsNone(row["home_score"])
        self.assertIsNone(row["attendance"])

    def test_baseline_uses_three_prior_completed_same_home_league_venue_matches_only(self):
        sources = [snapshot("prior" + str(i), KICKOFF - timedelta(days=7 * i), state="post", attendance=value)
                   for i, value in enumerate((10000, 12000, 14000), 1)]
        sources += [snapshot("other-home", KICKOFF - timedelta(days=1), state="post", home="other", attendance=100000),
                    snapshot("other-league", KICKOFF - timedelta(days=1), state="post", league="esp.1", attendance=100000),
                    snapshot("other-venue", KICKOFF - timedelta(days=1), state="post", venue="other", attendance=100000),
                    snapshot("future", KICKOFF + timedelta(days=1), state="post", attendance=100000),
                    snapshot("late-report", KICKOFF - timedelta(days=2), state="post", attendance=100000,
                             observed=KICKOFF + timedelta(days=1)), snapshot(state="post", attendance=18000)]
        row = self.row(sources)
        self.assertEqual(row["baseline_count"], 3)
        self.assertEqual(row["baseline_median_attendance"], 12000)
        self.assertEqual(row["attendance_vs_baseline"], 1.5)
        # A later correction to a prior fixture cannot improve its historical baseline value.
        sources.append(snapshot("prior1", KICKOFF - timedelta(days=7), state="post", attendance=90000,
                                observed=KICKOFF + timedelta(days=1)))
        self.assertEqual(self.row(sources)["baseline_median_attendance"], 12000)
        self.assertIsNone(self.row(sources[1:])["attendance_vs_baseline"])
        self.assertEqual(self.row(sources[1:])["baseline_count"], 2)

    def test_sourced_zero_attendance_and_capacity_keep_provenance_without_pooling_audiences(self):
        source = snapshot(state="post", capacity=None)
        source["external_observations"] = [external("m1", "attendance", 0, KICKOFF + timedelta(hours=2)),
                                            external("m1", "stadium_capacity", 20000, KICKOFF + timedelta(hours=2)),
                                            {"metric": "average_viewers", "value": 700000}]
        row = self.row([source])
        self.assertEqual(row["attendance"], 0)
        self.assertEqual(row["stadium_occupancy"], 0)
        self.assertEqual(row["attendance_source_url"], "https://league.example/report")
        self.assertEqual(row["capacity_scope"], "Match venue seated capacity")
        self.assertNotIn("average_viewers", row)
        self.assertNotIn(700000, row.values())

    def test_external_baseline_requires_recorded_evidence_before_target_kickoff(self):
        prior_kickoff = KICKOFF - timedelta(days=7)
        earlier = snapshot("prior", prior_kickoff, state="post", attendance=None)
        target = snapshot(state="post", attendance=15000)
        target["external_observations"] = [external("prior", "attendance", 12000, prior_kickoff + timedelta(hours=3))]
        self.assertEqual(self.row([earlier, target])["baseline_count"], 0)
        earlier["external_observations"] = deepcopy(target["external_observations"])
        self.assertEqual(self.row([earlier, target])["baseline_count"], 1)

    def test_past_result_archived_after_target_started_cannot_enter_its_baseline(self):
        prior = snapshot("prior", KICKOFF - timedelta(days=1), state="post", attendance=12000,
                         observed=KICKOFF - timedelta(minutes=1), captured=KICKOFF + timedelta(minutes=1))
        target = snapshot(state="post", attendance=15000)
        self.assertEqual(self.row([prior, target])["baseline_count"], 0)

    def test_missing_venue_or_home_identity_does_not_create_a_baseline(self):
        sources = [snapshot("prior", KICKOFF - timedelta(days=1), state="post", attendance=12000),
                   snapshot(state="post", attendance=15000)]
        sources[-1]["matches"][0]["home"]["id"] = ""
        self.assertEqual(self.row(sources)["baseline_count"], 0)
        sources[-1]["matches"][0]["home"]["id"] = "home1"
        sources[-1]["matches"][0]["venue_details"] = {}
        sources[-1]["matches"][0]["venue"] = ""
        self.assertEqual(self.row(sources)["baseline_count"], 0)

    def test_three_sourced_zero_attendances_do_not_divide_by_zero(self):
        sources = []
        for i in range(1, 4):
            kickoff = KICKOFF - timedelta(days=7 * i)
            prior = snapshot("prior" + str(i), kickoff, state="post")
            prior["external_observations"] = [external("prior" + str(i), "attendance", 0, kickoff + timedelta(hours=2))]
            sources.append(prior)
        sources.append(snapshot(state="post", attendance=15000))
        row = self.row(sources)
        self.assertEqual(row["baseline_count"], 3)
        self.assertEqual(row["baseline_median_attendance"], 0)
        self.assertIsNone(row["attendance_vs_baseline"])

    def test_same_id_in_distinct_leagues_is_not_merged_and_ambiguous_external_is_ignored(self):
        english, spanish = snapshot(state="post"), snapshot(state="post", league="esp.1")
        item = external("m1", "attendance", 50000, KICKOFF + timedelta(hours=2))
        item.pop("league_id")
        english["external_observations"] = [item]
        rows = report.report_rows([english, spanish])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["attendance"] is None for row in rows))

    def test_duplicate_input_retries_preserve_first_capture(self):
        first = snapshot()
        first["input_sha256"] = "same-inputs"
        retry = deepcopy(first)
        retry["captured_at"] = report.iso(KICKOFF + timedelta(hours=1))
        row = self.row([retry, first])
        self.assertEqual(row["ai_score"], 60)
        self.assertEqual(row["forecast_captured_at"], first["captured_at"])

    def test_csv_reads_nested_plain_and_compressed_snapshots_and_writes_blank_missing_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "snapshots" / "2026" / "10"
            folder.mkdir(parents=True)
            (folder / "before.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            (folder / "after.json.gz").write_bytes(gzip.compress(json.dumps(snapshot(state="post")).encode()))
            out = root / "report.csv"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(report.main(["--archive", str(root), "--out", str(out)]), 0)
            with out.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["away_score"], "0")
            self.assertEqual(rows[0]["attendance"], "")
            self.assertEqual(rows[0]["outlook_close"], "")
            self.assertEqual(rows[0]["forecast_snapshot"], "snapshots/2026/10/before.json")

    def test_invalid_or_empty_archive_fails_before_replacing_an_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "snapshots").mkdir()
            out = root / "report.csv"
            out.write_text("existing report")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(report.main(["--archive", str(root), "--out", str(out)]), 1)
                (root / "snapshots" / "invalid.json").write_text("{}")
                self.assertEqual(report.main(["--archive", str(root), "--out", str(out)]), 1)
            self.assertEqual(out.read_text(), "existing report")


if __name__ == "__main__":
    unittest.main()
