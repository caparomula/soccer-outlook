"""Literal status/result expectations across real shared-runtime consumers."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import discovery
import metrics
import outlook_runtime as runtime
from tests import test_companion_export, test_render
from tests.test_discovery import fixture


NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
EXAMPLES = Path(__file__).resolve().parents[1] / "docs/companion/rules/espn-status-examples.json"


def event(status=None, home="0", away="0"):
    value = test_render.Markup.event()
    value["date"] = "2026-10-20T17:00:00Z"
    comp = value["competitions"][0]
    comp["status"] = status if status is not None else {"type": {"state": "in"}, "displayClock": "12'"}
    for side in comp["competitors"]:
        side["score"] = home if side["homeAway"] == "home" else away
    return value


def feed_status(match):
    return test_companion_export.CompanionExport().artifacts([match])["schedule"]["fixtures"][0]["status"]


def observed(match, source):
    return metrics.snapshot(build, [match], {match.league: {match.id: source}}, NOW,
                            source_revision="status-test")["matches"][0]


def round_trip(match, *, old_context=False):
    record = json.loads(json.dumps(asdict(match), default=lambda value: value.isoformat()))
    record["utc"] = datetime.fromisoformat(record["utc"])
    record["home"], record["away"] = build.Team(**record["home"]), build.Team(**record["away"])
    if old_context:
        record.pop("status_binding", None)
    return build.Match(**record)


class StatusConsumers(unittest.TestCase):
    def test_cancellation_alias_does_not_become_final_zero(self):
        source = event({"type": {"state": "post", "description": "Canceled", "shortDetail": "CAN"}})
        match = build.interpret("eng.1", source)
        status = feed_status(match)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["scores"]["match"], {"home": None, "away": None})
        self.assertEqual(status["provider_state"], "post")
        row = build.row_html(match, {}, now=NOW)
        self.assertIn('data-status-kind="cancelled"', row)
        self.assertEqual(row.count('<b class="score" hidden>'), 2)
        record = observed(match, source)
        self.assertFalse(record["observed"]["score_is_final"])
        self.assertIsNone(record["observed"]["home_score"])
        self.assertEqual(record["observed"]["scores_raw"], {"home": "0", "away": "0"})

    def test_unknown_terminal_and_abandoned_are_not_discovery_final(self):
        for description, wanted in (("Unfamiliar terminal", "unknown"), ("Abandoned", "abandoned")):
            with self.subTest(description=description):
                source = event({"type": {"state": "post", "description": description}}, "2", "1")
                match = build.interpret("eng.1", source)
                self.assertEqual(feed_status(match)["state"], wanted)
                page = discovery._match_html(build, match, "Test coverage")
                self.assertNotIn("Final", page)
                self.assertNotIn("2–1", page)
                self.assertIn("Status unknown" if wanted == "unknown" else "Abandoned", page)
                self.assertFalse(observed(match, source)["observed"]["score_is_final"])

    def test_object_and_numeric_scores_have_one_result_meaning(self):
        source = event(home={"displayValue": "2"}, away=0)
        match = build.interpret("eng.1", source)
        self.assertEqual((match.home.score, match.away.score), ("2", "0"))
        self.assertEqual(feed_status(match)["scores"]["match"], {"home": 2, "away": 0})
        record = observed(match, source)
        self.assertEqual((record["observed"]["home_score"], record["observed"]["away_score"]), (2, 0))
        self.assertEqual(record["observed"]["scores_raw"]["home"], {"displayValue": "2"})

    def test_missing_status_does_not_invent_a_scheduled_phase(self):
        source = event({})
        match = build.interpret("eng.1", source)
        self.assertEqual(match.state, "unknown")
        self.assertEqual(match.status, "Status unknown")
        self.assertEqual(feed_status(match)["provider_state"], "")
        self.assertFalse(observed(match, source)["forecast_eligible"])

    def test_mutation_and_dataclass_replace_cannot_reuse_source_projection(self):
        source = event({"type": {"state": "post", "description": "Full Time"}}, "2", "0")
        match = build.interpret("eng.1", source)
        self.assertTrue(build.match_status(match)["final"])
        replacement = replace(match, state="pre", status="")
        self.assertFalse(hasattr(replacement, "_status_projection"))
        self.assertEqual(build.match_status(replacement)["state"], "scheduled")
        self.assertEqual(feed_status(replacement)["scores"]["match"], {"home": None, "away": None})
        match.state, match.status = "in", "15'"
        match.home.score = "3"
        self.assertEqual(build.match_status(match)["scores"], {"home": 3, "away": 0})
        self.assertFalse(build.match_status(match)["final"])
        match.status = "Postponed"
        self.assertTrue(build.match_status(match)["void"])
        self.assertEqual(build.match_status(match)["scores"], {"home": None, "away": None})

    def test_rehydrated_and_direct_matches_use_shared_normalized_facts(self):
        for label, state in (("Status unknown", "unknown"), ("Canceled", "cancelled"), ("Abandoned", "abandoned")):
            with self.subTest(label=label):
                match = fixture(state="post", status=label)
                record = asdict(match)
                record["home"], record["away"] = build.Team(**record["home"]), build.Team(**record["away"])
                rehydrated = build.Match(**record)
                self.assertFalse(hasattr(rehydrated, "_status_projection"))
                self.assertEqual(build.match_status(rehydrated)["state"], state)
                self.assertEqual(build.match_status(rehydrated)["scores"], {"home": None, "away": None})
        source = event({"type": {"state": "pre", "description": "Canceled"}})
        original = build.interpret("eng.1", source)
        self.assertEqual(build.match_status(original)["provider_state"], "pre")
        rehydrated = round_trip(original, old_context=True)
        # Old context carries normalized facts, not the original raw phase.
        # Preserve the supplied phase honestly; never reconstruct absent provenance.
        self.assertEqual(build.match_status(rehydrated)["provider_state"], "post")
        self.assertEqual(build.match_status(rehydrated)["state"], "cancelled")

    def test_metrics_current_match_verdict_wins_over_stale_raw_status(self):
        source = event({"type": {"state": "post", "description": "Full Time"}}, "2", "0")
        match = build.interpret("eng.1", source)
        cancelled = replace(match, status="Canceled")
        result = observed(cancelled, source)
        self.assertFalse(result["observed"]["score_is_final"])
        self.assertIsNone(result["observed"]["home_score"])
        self.assertEqual(result["status_raw"], source["competitions"][0]["status"])
        scheduled = build.interpret("eng.1", event({"type": {"state": "pre", "description": "Scheduled"}}))
        scheduled.utc = NOW.replace(hour=19)
        contradictory = event({"type": {"state": "pre", "description": "Scheduled"}})
        contradictory["status"] = {"type": {"state": "pre", "description": "Postponed"}}
        result = observed(scheduled, contradictory)
        self.assertTrue(result["forecast_eligible"])
        self.assertEqual(result["status_raw"], contradictory["status"])

    def test_known_live_identity_survives_display_collisions_and_score_changes(self):
        for label in ("FT", "Postponed"):
            with self.subTest(label=label):
                source = event({"type": {"state": "in", "name": "STATUS_IN_PROGRESS"},
                                "displayClock": label}, "2", "1")
                original = build.interpret("eng.1", source)
                for match in (original, round_trip(original), replace(original)):
                    with self.subTest(restored=match is not original):
                        self.assertEqual(build.match_status(match)["state"], "live")
                        self.assertFalse(build.match_status(match)["final"])
                        self.assertIn('data-status-kind="live"', build.row_html(match, {}, now=NOW))
                        self.assertEqual(feed_status(match)["scores"]["match"], {"home": 2, "away": 1})
                        self.assertEqual(observed(match, source)["observed"]["home_score"], 2)
                        match.home = replace(match.home, score="3")
                        self.assertEqual(build.match_status(match)["scores"], {"home": 3, "away": 1})
                        self.assertEqual(build.match_status(match)["state"], "live")
                        changed = replace(match, status="Canceled")
                        self.assertEqual(build.match_status(changed)["state"], "cancelled")
                        match.state = "post"
                        self.assertNotEqual(build.match_status(match)["state"], "live")

    def test_new_context_preserves_provider_phase_while_old_context_infers_only_available_facts(self):
        original = build.interpret("eng.1", event({"type": {"state": "pre", "description": "Canceled"}}))
        for match in (round_trip(original), replace(original)):
            self.assertFalse(hasattr(match, "_status_projection"))
            self.assertEqual(build.match_status(match)["provider_state"], "pre")
            self.assertEqual(build.match_status(match)["state"], "cancelled")
            match.home.score = "2"
            self.assertEqual(build.match_status(match)["provider_state"], "pre")
        legacy = round_trip(original, old_context=True)
        self.assertEqual(build.match_status(legacy)["provider_state"], "post")
        self.assertEqual(build.match_status(legacy)["state"], "cancelled")

    def test_tbd_goals_and_structured_extra_scores_are_not_inferred(self):
        source = event({"type": {"state": "in"}, "displayClock": "90'+4'"}, "1", "0")
        source["competitions"][0]["timeValid"] = False
        source["competitions"][0]["notes"] = [{"headline": "aggregate 3-1"}]
        source["competitions"][0]["details"] = [{"scoringPlay": True, "shootout": True}]
        match = build.interpret("eng.1", source)
        self.assertFalse(match.time_valid)
        self.assertEqual(match.goals, [])
        status = feed_status(match)
        self.assertIsNone(status["scores"]["shootout"])
        self.assertIsNone(status["scores"]["aggregate"])

    def test_direct_exception_phase_cannot_activate_static_live_only_link(self):
        match = fixture("401879267", channel="USA Network", service="yttv", basis="listed",
                        state="in", status="Canceled")
        build.evaluate_match_routes(match, NOW)
        self.assertTrue(build.provider_links(match)["USA Network"]["yttv"]["live_only"])
        self.assertNotIn('<a class="network__link', build.pills_html(match))
        match.home.score = "2"
        self.assertEqual(build.match_status(match)["state"], "cancelled")
        match.status = "23'"
        self.assertIn('<a class="network__link', build.pills_html(match))
        match.status = "Final"
        row = build.row_html(match, {}, now=NOW)
        self.assertIn('data-state="unknown" data-status-kind="unknown"', row)
        self.assertNotIn('<a class="network__link', row)

    def test_nonplayed_status_hides_goals_without_deleting_source_events(self):
        for description, shown in (("Unfamiliar terminal", False), ("Canceled", False), ("Full Time", True)):
            with self.subTest(description=description):
                source = event({"type": {"state": "post", "description": description}}, "2", "1")
                source["competitions"][0]["details"] = [{"scoringPlay": True, "team": {"id": "660"},
                    "clock": {"displayValue": "63'"}, "athletesInvolved": [{"shortName": "A. Scorer"}]}]
                match = build.interpret("eng.1", source)
                self.assertEqual(match.goals, [("63'", "660", "A. Scorer", "")])
                row = build.row_html(match, {}, now=NOW)
                self.assertEqual('class="row__goals"' in row, shown)
                self.assertEqual("A. Scorer" in row, shown)

    def test_metrics_fingerprint_tracks_shared_decoder_without_changing_revision(self):
        root = EXAMPLES.parents[3]
        names = set(runtime.source_manifest()) | {
            "outlook_runtime.py", "build.py", "metrics.py", "history.py", "story.py", "settings.toml", "rights.toml"}
        decoder = "packages/outlook_data/lib/src/espn_decoder.dart"
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"GITHUB_SHA": "fixed-revision"}):
            target = Path(temporary)
            for name in names:
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((root / name).read_bytes())
            revision, before = metrics.source_version(target)
            self.assertEqual(revision, "fixed-revision")
            self.assertIn(decoder, before)
            (target / decoder).write_text((target / decoder).read_text() + "\n// provenance probe\n")
            changed_revision, after = metrics.source_version(target)
            self.assertEqual(changed_revision, revision)
            self.assertNotEqual(before[decoder], after[decoder])
            self.assertEqual(before["build.py"], after["build.py"])


class StatusRuntimeAdapters(unittest.TestCase):
    def test_large_source_frames_bypass_cache_without_filtering_original_fields(self):
        runtime._status.cache_clear()
        projection = dict(id="status", state="scheduled", phase="pre", provider_state="pre", label="",
                          played=False, final=False, void=False,
                          reported_scores={"home": None, "away": None}, scores={"home": None, "away": None})
        large = {"type": {"state": "pre"}, "irrelevant_provider_metadata": "x" * 20000}
        small = {"type": {"state": "pre"}}
        with patch.object(runtime, "evaluate", return_value={"version": 1, "results": [projection]}) as evaluate:
            for _ in range(2):
                self.assertEqual(runtime.espn_status(None, large, None, None), projection)
            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(evaluate.call_args.args[0]["cases"][0]["competition_status"], large)
            for _ in range(2):
                self.assertEqual(runtime.espn_status(None, small, None, None), projection)
            self.assertEqual(evaluate.call_count, 3)

    def test_independent_literal_examples_through_compiled_cli(self):
        examples = json.loads(EXAMPLES.read_text())
        self.assertEqual(len(examples["cases"]), 18)
        for group in examples["cases"]:
            operation = group["request"]["operation"]
            adapter = getattr(runtime, operation)
            for case, expected in zip(group["request"]["cases"], group["expected"]["results"]):
                with self.subTest(group=group["id"], case=case["id"]):
                    arguments = {key: value for key, value in case.items() if key != "id"}
                    before = deepcopy(arguments)
                    self.assertEqual(adapter(**arguments), dict(expected, id="status"))
                    self.assertEqual(arguments, before)

    def test_cached_results_and_source_objects_cannot_be_mutated_by_callers(self):
        status = {"type": {"state": "in"}, "displayClock": "12'"}
        result = runtime.espn_status(None, status, 0, None)
        result["scores"]["home"] = 91
        self.assertEqual(runtime.espn_status(None, status, 0, None)["scores"], {"home": 0, "away": None})
        status["type"]["state"] = "pre"
        self.assertEqual(runtime.espn_status(None, status, 0, None)["state"], "scheduled")
        self.assertEqual(runtime.fixture_status("post", "FT", "2", "0")["scores"], {"home": 2, "away": 0})
        self.assertEqual(runtime.fixture_status("post", "Canceled", "2", "0")["scores"], {"home": None, "away": None})

    def test_protocol_identity_mismatch_cannot_attach_to_a_fixture(self):
        runtime._status.cache_clear()
        with patch.object(runtime, "evaluate", return_value={"version": 1, "results": [{"id": "another"}]}):
            with self.assertRaisesRegex(RuntimeError, "status.*identit"):
                runtime.fixture_status("pre", "", None, None)


if __name__ == "__main__":
    unittest.main()
