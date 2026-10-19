"""The archive preserves prediction evidence and never treats late data as forecasts."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import history
from tests.test_shared_ai import envelope


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def metrics():
    return {
        "version": 1, "observed_at": history.iso(NOW), "source_revision": "abc123",
        "settings": {"blend": {"ai": 70, "outlook": 30, "interest": 95, "league_priority": 5},
                     "outlook": {"missing": 0.5, "weights": {"stature": 40, "close": 25, "stakes": 15, "tv": 10, "goals": 10}},
                     "ai": {"enabled": True, "model": "gpt-6.1-sol", "effort": "low", "design": "ratings"}},
        "league_order": ["eng.1", "esp.1", "ita.1"], "default_services": ["youtube_tv"], "default_leagues": ["eng.1"],
        "matches": [{"id": "123456", "league_id": "eng.1", "competition": "Premier League",
                     "kickoff_utc": history.iso(NOW + timedelta(hours=1)), "state": "pre", "status": "Scheduled",
                     "time_confirmed": True, "forecast_eligible": True, "home": {"name": "Home", "rank": 1},
                     "away": {"name": "Away", "rank": 2}, "stage": "", "note": "", "venue": "Stadium",
                     "coverage": {"listed": [{"channel": "USA Network"}], "usual": None},
                     "outlook": {"score": 60, "parts": {"stature": 0.7, "close": 0.5, "stakes": 0.8, "tv": 0.5, "goals": 0.5},
                                 "inputs": {"stature": 105, "draw_probability": None, "goal_line": None, "odds": {}},
                                 "missing_components": ["close", "goals"]},
                     "observed": {"attendance": None, "attendance_raw": 0, "home_score": 0, "away_score": 0, "goals": []}}],
    }


def story():
    return {"version": 1, "generated_at": history.iso(NOW - timedelta(minutes=30)), "date": "2026-10-15",
            "focus_until": history.iso(NOW + timedelta(days=1)), "model": "gpt-6.1-sol-2026", "requested_model": "gpt-6.1-sol",
            "effort": "low", "kind": "ratings", "rankings": {"123456": {"popularity": 80, "gameplay": 60, "impact": 50, "score": 61}}}


class History(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_preserves_all_metrics_missing_values_zero_results_and_source_provenance(self):
        supplied = metrics()
        value = history.snapshot(supplied, story(), captured_at=NOW)
        match = value["matches"][0]
        self.assertEqual(match["outlook"], supplied["matches"][0]["outlook"])
        self.assertEqual(value["source_revision"], "abc123")
        self.assertIsNone(match["observed"]["attendance"])
        self.assertEqual(match["observed"]["attendance_raw"], 0)
        self.assertEqual(match["observed"]["home_score"], 0)
        self.assertIsNone(match["observed"]["viewership"])
        self.assertEqual(match["ai"]["automatic"]["popularity"], 80)
        self.assertEqual(match["ai"]["automatic"]["gameplay"], 60)
        self.assertEqual(match["ai"]["automatic"]["impact"], 50)
        self.assertTrue(match["ai"]["automatic"]["eligible_at_capture"])
        self.assertEqual(value["settings"]["ai_rating_weights"], {"popularity": 0.5, "gameplay": 0.35, "impact": 0.15})
        self.assertEqual(value["settings"]["ai_score_calibration"], "watch-appeal-v2")
        self.assertEqual(value["ai_sources"]["automatic"]["rating_weights"], history.LEGACY_AI_WEIGHTS)
        self.assertEqual(value["ai_sources"]["automatic"]["score_calibration"], "legacy-v1")
        self.assertTrue(match["ai"]["automatic"]["score_matches_components"])
        self.assertEqual(value["ai_sources"]["automatic"]["model"], "gpt-6.1-sol-2026")
        self.assertEqual(value["ai_sources"]["automatic"]["requested_model"], "gpt-6.1-sol")
        self.assertEqual(value["ai_sources"]["automatic"]["timestamp_scope"], "story_bundle")
        self.assertIsNone(value["ai_sources"]["automatic"]["rating_generated_at"])
        self.assertNotIn("viewership", supplied["matches"][0]["observed"], "Do not mutate the caller's source")

    def test_current_calibration_preserves_new_inputs_and_published_score(self):
        supplied, ai = metrics(), story()
        supplied["settings"]["outlook_calibration"] = "watch-appeal-v2"
        supplied["settings"]["outlook"]["weights"] = {"stature": 50, "close": 25, "stakes": 10, "goals": 15}
        outlook = supplied["matches"][0]["outlook"]
        del outlook["parts"]["tv"]
        outlook["inputs"].update(home_win_probability=0.4, away_win_probability=0.4, win_balance=1.0)
        ai.update(rating_weights=dict(history.WEIGHTS), score_calibration="watch-appeal-v2")
        ai["rankings"]["123456"]["score"] = 68.5
        original = deepcopy((supplied, ai))
        value = history.snapshot(supplied, ai, captured_at=NOW)
        self.assertEqual(value["matches"][0]["outlook"], outlook)
        self.assertEqual(value["ai_sources"]["automatic"]["rating_weights"], dict(history.WEIGHTS))
        self.assertEqual(value["ai_sources"]["automatic"]["score_calibration"], "watch-appeal-v2")
        self.assertEqual(value["matches"][0]["ai"]["automatic"]["score"], 68.5)
        self.assertTrue(value["matches"][0]["ai"]["automatic"]["score_matches_components"])
        self.assertEqual((supplied, ai), original)
        # A mismatched published total is recorded, never silently repaired.
        ai["rankings"]["123456"]["score"] = 61
        value = history.snapshot(supplied, ai, captured_at=NOW)
        self.assertEqual(value["matches"][0]["ai"]["automatic"]["score"], 61)
        self.assertFalse(value["matches"][0]["ai"]["automatic"]["score_matches_components"])

    def test_incomplete_calibration_weights_are_rejected_without_reweighting_old_data(self):
        supplied = metrics()
        supplied["matches"][0]["outlook"]["parts"].pop("tv")
        with self.assertRaisesRegex(ValueError, "recorded weights"):
            history.validate_metrics(supplied)
        for weights in ({"popularity": 1}, {"popularity": True, "gameplay": 0, "impact": 0},
                        {"popularity": 0.5, "gameplay": 0.35, "impact": 0.4}):
            with self.subTest(weights=weights):
                ai = dict(story(), rating_weights=weights, score_calibration="watch-appeal-v2")
                value = history.snapshot(metrics(), ai, captured_at=NOW)
                self.assertEqual(value["ai_sources"]["automatic"]["status"], "invalid")
                self.assertIsNone(value["matches"][0]["ai"]["automatic"])
        for metadata in ({"rating_weights": dict(history.WEIGHTS)}, {"score_calibration": "watch-appeal-v2"}):
            value = history.snapshot(metrics(), dict(story(), **metadata), captured_at=NOW)
            self.assertEqual(value["ai_sources"]["automatic"]["status"], "invalid")

    def test_mixed_effort_archive_preserves_each_rating_and_research_generation(self):
        supplied, ai = metrics(), story()
        second = deepcopy(supplied["matches"][0])
        second["id"] = "123457"
        supplied["matches"].append(second)
        original_time = history.iso(NOW - timedelta(hours=2))
        new_time = history.iso(NOW - timedelta(minutes=5))
        ai.update(effort="mixed", requested_effort="high", generated_at=new_time,
                  research_provenance={"model": "same-model", "effort": "max", "generated_at": original_time,
                                       "timestamp_scope": "story_bundle"})
        ai["rankings"]["123456"]["provenance"] = {
            "model": "same-model", "effort": "max", "generated_at": original_time, "timestamp_scope": "story_bundle"}
        ai["rankings"]["123457"] = dict(ai["rankings"]["123456"], provenance={
            "model": "same-model", "effort": "high", "generated_at": new_time, "timestamp_scope": "rating_response"})
        value = history.snapshot(supplied, ai, captured_at=NOW)
        automatic = value["ai_sources"]["automatic"]
        self.assertEqual(automatic["effort"], "mixed")
        self.assertEqual(automatic["requested_effort"], "high")
        self.assertEqual(automatic["research_provenance"], ai["research_provenance"])
        old, new = [match["ai"]["automatic"] for match in value["matches"]]
        self.assertEqual(old["provenance"], ai["rankings"]["123456"]["provenance"])
        self.assertEqual(new["provenance"], ai["rankings"]["123457"]["provenance"])
        self.assertTrue(old["eligible_at_capture"])
        self.assertTrue(new["eligible_at_capture"])
        self.assertEqual(value["observed_at"], supplied["observed_at"])
        self.assertEqual(value["matches"][0]["observed"]["home_score"], 0)
        # Even an old generation timestamp cannot turn a rating first archived
        # after kickoff into an independently observed pre-match prediction.
        late = history.snapshot(supplied, ai, captured_at=NOW + timedelta(hours=2))
        self.assertFalse(late["matches"][0]["ai"]["automatic"]["eligible_at_capture"])
        self.assertFalse(late["matches"][1]["ai"]["automatic"]["eligible_at_capture"])

    def test_generation_provenance_strips_private_fields_and_ignores_malformed_metadata(self):
        ai = story()
        provenance = {"model": "same-model", "effort": "max", "generated_at": ai["generated_at"],
                      "prompt": "PRIVATE PROMPT", "research": "RESEARCH PROSE"}
        ai["research_provenance"] = deepcopy(provenance)
        ai["rankings"]["123456"]["provenance"] = deepcopy(provenance)
        value = history.snapshot(metrics(), ai, captured_at=NOW)
        self.assertEqual(set(value["ai_sources"]["automatic"]["research_provenance"]),
                         {"model", "effort", "generated_at"})
        self.assertEqual(set(value["matches"][0]["ai"]["automatic"]["provenance"]),
                         {"model", "effort", "generated_at"})
        self.assertNotIn("PRIVATE PROMPT", json.dumps(value))
        self.assertNotIn("RESEARCH PROSE", json.dumps(value))
        for scope in ("story_bundle", "rating_response", "research_response", "unrecognized", ["rating_response"]):
            ai["research_provenance"]["timestamp_scope"] = scope
            ai["rankings"]["123456"]["provenance"]["timestamp_scope"] = scope
            archived = history.snapshot(metrics(), ai, captured_at=NOW)
            for item in (archived["ai_sources"]["automatic"]["research_provenance"],
                         archived["matches"][0]["ai"]["automatic"]["provenance"]):
                if scope in ("story_bundle", "rating_response", "research_response"):
                    self.assertEqual(item["timestamp_scope"], scope)
                else:
                    self.assertNotIn("timestamp_scope", item)
        for invalid in ({"model": "same-model", "effort": "max", "generated_at": "yesterday"},
                        {"model": "same-model", "effort": ["max"], "generated_at": ai["generated_at"]},
                        {"model": "", "effort": "max", "generated_at": ai["generated_at"]}):
            with self.subTest(invalid=invalid):
                ai["research_provenance"] = ai["rankings"]["123456"]["provenance"] = invalid
                value = history.snapshot(metrics(), ai, captured_at=NOW)
                self.assertNotIn("research_provenance", value["ai_sources"]["automatic"])
                self.assertNotIn("provenance", value["matches"][0]["ai"]["automatic"])
                self.assertEqual(value["matches"][0]["ai"]["automatic"]["score"], 61)
                self.assertEqual(value["ai_sources"]["automatic"]["timestamp_scope"], "story_bundle")

    def test_default_derived_scores_use_full_order_and_leave_shared_separate(self):
        supplied = metrics()
        shared = envelope(NOW)
        shared["response"]["matches"][0]["score"] = 100
        value = history.snapshot(supplied, story(), shared, captured_at=NOW)
        match = value["matches"][0]
        default = match["automatic_default"]
        self.assertEqual(default["league_rank"], 1)
        self.assertEqual(default["league_priority"], 100)
        self.assertAlmostEqual(default["interest"], 60.7)
        self.assertAlmostEqual(default["pick"], 62.665)
        self.assertTrue(default["forecast_eligible"])
        self.assertEqual(match["ai"]["shared"]["score"], 100)
        self.assertTrue(match["ai"]["shared"]["eligible_at_capture"])
        supplied["matches"][0]["league_id"] = "ita.1"
        match = history.snapshot(supplied, story(), captured_at=NOW)["matches"][0]
        self.assertEqual(match["automatic_default"]["league_priority"], 0)
        self.assertEqual(match["automatic_default"]["league_rank"], 3)

    def test_late_capture_does_not_relabel_outlook_but_excludes_newly_observed_ai(self):
        value = history.snapshot(metrics(), story(), captured_at=NOW + timedelta(hours=2))
        match = value["matches"][0]
        self.assertTrue(match["forecast_eligible"], "Outlook inputs were observed pre-kickoff")
        self.assertFalse(match["ai"]["automatic"]["eligible_at_capture"])
        self.assertFalse(match["automatic_default"]["forecast_eligible"])
        self.assertEqual(match["ai"]["automatic"]["score"], 61, "Retain late ratings as observations")

    def test_live_final_uncertain_and_past_fixtures_are_not_prematch_forecasts(self):
        for changes in ({"state": "in"}, {"state": "post"}, {"time_confirmed": False},
                        {"forecast_eligible": False}, {"kickoff_utc": history.iso(NOW)}):
            with self.subTest(changes=changes):
                supplied = metrics()
                supplied["matches"][0].update(changes)
                match = history.snapshot(supplied, story(), captured_at=NOW)["matches"][0]
                self.assertFalse(match["forecast_eligible"])
                self.assertFalse(match["ai"]["automatic"]["eligible_at_capture"])

    def test_postmatch_results_and_attendance_preserve_values_without_forecast_claims(self):
        supplied = metrics()
        supplied["matches"][0].update(state="post", forecast_eligible=False)
        supplied["matches"][0]["observed"].update(attendance=52710, attendance_raw=52710, home_score=2, away_score=0,
                                                   goals=[{"minute": 89, "team": "home", "player": "A scorer"}])
        match = history.snapshot(supplied, captured_at=NOW)["matches"][0]
        self.assertEqual(match["observed"]["attendance"], 52710)
        self.assertEqual(match["observed"]["away_score"], 0)
        self.assertEqual(match["observed"]["goals"][0]["minute"], 89)
        self.assertFalse(match["forecast_eligible"])

    def test_stale_future_and_disabled_automatic_ratings_are_retained_but_not_used(self):
        for kind in ("stale", "future", "disabled"):
            with self.subTest(kind=kind):
                supplied, ai = metrics(), story()
                if kind == "stale":
                    ai["generated_at"] = history.iso(NOW - timedelta(hours=31))
                elif kind == "future":
                    ai["generated_at"] = history.iso(NOW + timedelta(seconds=1))
                else:
                    supplied["settings"]["ai"]["enabled"] = False
                value = history.snapshot(supplied, ai, captured_at=NOW)
                match = value["matches"][0]
                self.assertFalse(match["ai"]["automatic"]["eligible_at_capture"])
                self.assertFalse(match["automatic_default"]["ai_used"])
                self.assertEqual(match["automatic_default"]["interest"], 60)

    def test_invalid_automatic_entries_and_unmatched_ids_cannot_pollute_scores(self):
        ai = story()
        ai["rankings"]["unknown"] = deepcopy(ai["rankings"]["123456"])
        ai["rankings"]["123456"]["popularity"] = True
        value = history.snapshot(metrics(), ai, captured_at=NOW)
        self.assertIsNone(value["matches"][0]["ai"]["automatic"])
        self.assertEqual(value["ai_sources"]["automatic"]["rejected_ratings"], 1)
        ai["generated_at"] = "yesterday"
        value = history.snapshot(metrics(), ai, captured_at=NOW)
        self.assertEqual(value["ai_sources"]["automatic"]["status"], "invalid")
        self.assertIsNone(value["matches"][0]["ai"]["automatic"])

    def test_shared_validity_expiry_and_fixture_identity_are_checked(self):
        for kind in ("stale", "future", "mismatch", "private"):
            with self.subTest(kind=kind):
                shared = envelope(NOW)
                if kind in ("stale", "future"):
                    shifted = NOW + timedelta(hours=-24 if kind == "stale" else 1)
                    shared = envelope(shifted)
                    shared["fixtures"][0]["kickoff"] = metrics()["matches"][0]["kickoff_utc"]
                elif kind == "mismatch":
                    shared["fixtures"][0]["kickoff"] = history.iso(NOW + timedelta(hours=2))
                else:
                    shared["preferences"] = "PRIVATE PREFERENCE"
                value = history.snapshot(metrics(), shared=shared, captured_at=NOW)
                match = value["matches"][0]
                if kind in ("stale", "future"):
                    self.assertEqual(value["ai_sources"]["shared"]["status"], kind)
                    self.assertFalse(match["ai"]["shared"]["eligible_at_capture"])
                else:
                    self.assertIsNone(match["ai"]["shared"])
                self.assertNotIn("PRIVATE PREFERENCE", json.dumps(value))

    def test_no_prompts_blurbs_or_unexpected_ai_fields_are_archived(self):
        ai = story()
        ai.update(prompt="PRIVATE PROMPT", lede="PUBLIC PROSE", preferences="PRIVATE PREFERENCES")
        ai["rankings"]["123456"].update(blurb="MATCH PROSE", sources=[{"url": "https://example.com"}])
        shared = envelope(NOW)
        shared["response"]["matches"][0]["blurb"] = "SHARED MATCH PROSE"
        encoded = json.dumps(history.snapshot(metrics(), ai, shared, captured_at=NOW))
        for text in ("PRIVATE PROMPT", "PUBLIC PROSE", "PRIVATE PREFERENCES", "MATCH PROSE", "SHARED MATCH PROSE"):
            self.assertNotIn(text, encoded)

    def test_actual_build_metrics_are_accepted_without_losing_score_evidence(self):
        import build
        import metrics as metric_export
        from tests.test_render import Markup
        event = Markup.event()
        match = build.interpret("eng.1", event)
        observed = match.utc - timedelta(hours=1)
        exported = metric_export.snapshot(build, [match], {"eng.1": {match.id: event}}, observed)
        archived = history.snapshot(exported, captured_at=observed)
        self.assertEqual(archived["matches"][0]["outlook"], exported["matches"][0]["outlook"])
        self.assertEqual(archived["settings"]["ai"]["enabled"], build.SETTINGS.ai)
        self.assertTrue(archived["matches"][0]["automatic_default"]["forecast_eligible"])

    def test_later_external_observations_are_separate_from_old_match_forecasts(self):
        observed = [{"id": "old-match-attendance", "match_id": "old-match", "metric": "attendance", "value": 42000,
                     "unit": "people", "scope": "Official attendance at the stadium",
                     "source_url": "https://example.com/official-report", "observed_at": history.iso(NOW)}]
        value = history.snapshot(metrics(), captured_at=NOW, external_observations=observed)
        self.assertEqual(value["external_observations"], observed)
        self.assertIsNone(value["matches"][0]["observed"]["attendance"])
        observed[0]["value"] = 1
        self.assertEqual(value["external_observations"][0]["value"], 42000, "Copy input observations")

    def test_identical_retry_preserves_original_capture_even_after_kickoff_and_changed_inputs_fail(self):
        path = self.root / "snapshots" / "run.json"
        first = history.snapshot(metrics(), story(), captured_at=NOW)
        self.assertTrue(history.write_snapshot(path, first))
        raw = path.read_bytes()
        retry = history.snapshot(metrics(), story(), captured_at=NOW + timedelta(hours=2))
        self.assertFalse(history.write_snapshot(path, retry))
        self.assertEqual(path.read_bytes(), raw)
        changed = metrics()
        changed["matches"][0]["outlook"]["score"] = 61
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            history.write_snapshot(path, history.snapshot(changed, story(), captured_at=NOW))
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(list(path.parent.glob(".snapshot-*")), [])

    def test_failed_atomic_creation_leaves_no_partial_snapshot_or_temporary(self):
        path = self.root / "run.json"
        with patch.object(history.os, "link", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                history.write_snapshot(path, history.snapshot(metrics(), captured_at=NOW))
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_gzip_snapshots_are_deterministic_bounded_and_idempotent(self):
        value = history.snapshot(metrics(), story(), captured_at=NOW)
        path = self.root / "run.json.gz"
        self.assertTrue(history.write_snapshot(path, value))
        original = path.read_bytes()
        self.assertEqual(original, gzip.compress(history.canonical(value), mtime=0))
        self.assertEqual(history.read_json(path), value)
        self.assertFalse(history.write_snapshot(path, history.snapshot(metrics(), story(), captured_at=NOW + timedelta(hours=2))))
        self.assertEqual(path.read_bytes(), original)
        with patch.object(history, "MAX_INPUT_BYTES", len(original) + 10):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                history.read_json(path)
        path.write_bytes(original[:-5])
        with self.assertRaisesRegex(ValueError, "truncated gzip"):
            history.read_json(path)

    def test_incomplete_or_duplicate_metrics_fail(self):
        for change in (lambda value: value.update(version=True), lambda value: value.pop("observed_at"),
                       lambda value: value["matches"].append(deepcopy(value["matches"][0])),
                       lambda value: value["matches"][0]["outlook"]["parts"].pop("goals"),
                       lambda value: value["matches"][0]["outlook"]["inputs"].update(goal_line=float("nan")),
                       lambda value: value.update(observed_at="2026-10-15T12:00:00")):
            supplied = metrics()
            change(supplied)
            with self.assertRaises(ValueError):
                history.snapshot(supplied, captured_at=NOW)

    def test_cli_allows_missing_optional_sources_and_reports_invalid_ones(self):
        source = self.root / "metrics.json"
        source.write_bytes(history.canonical(metrics()))
        invalid = self.root / "bad-story.json"
        invalid.write_text('{"version":1,"version":1}')
        target = self.root / "snapshot.json"
        with redirect_stderr(io.StringIO()) as errors, redirect_stdout(io.StringIO()), patch.object(history, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            self.assertEqual(history.main(["--metrics", str(source), "--story", str(invalid),
                                           "--shared", str(self.root / "missing.json"), "--out", str(target)]), 0)
        archived = history.read_json(target)
        self.assertEqual(archived["ai_sources"]["automatic"]["status"], "invalid")
        self.assertEqual(archived["ai_sources"]["shared"]["status"], "missing")
        self.assertIn("invalid optional automatic", errors.getvalue())
        source.write_text("{}")
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(history.main(["--metrics", str(source), "--out", str(self.root / "failure.json")]), 1)
        self.assertFalse((self.root / "failure.json").exists())

    def test_cli_validates_and_archives_sourced_observations_for_older_matches(self):
        source = self.root / "metrics.json"
        source.write_bytes(history.canonical(metrics()))
        observation = {"id": "old-attendance", "match_id": "older-match", "metric": "attendance", "value": 42000,
                       "unit": "people", "scope": "Official stadium attendance", "observed_at": history.iso(NOW),
                       "source_url": "https://example.com/official-report"}
        extra = self.root / "observations.json"
        extra.write_bytes(history.canonical({"version": 1, "observations": [observation]}))
        target = self.root / "snapshot.json.gz"
        with redirect_stdout(io.StringIO()), patch.object(history, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            self.assertEqual(history.main(["--metrics", str(source), "--observations", str(extra), "--out", str(target)]), 0)
        self.assertEqual(history.read_json(target)["external_observations"], [observation])
        observation["source_url"] = "http://example.com/untrusted"
        extra.write_bytes(history.canonical({"version": 1, "observations": [observation]}))
        with redirect_stderr(io.StringIO()), patch.object(history, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            self.assertEqual(history.main(["--metrics", str(source), "--observations", str(extra),
                                           "--out", str(self.root / "invalid.json.gz")]), 1)
        self.assertFalse((self.root / "invalid.json.gz").exists())


if __name__ == "__main__":
    unittest.main()
