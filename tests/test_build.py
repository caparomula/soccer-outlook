"""Publication depends on usable ESPN sources, not on how busy the soccer calendar is."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import build
from tests.page_fixture import BUILT_AT, TODAY
from tests import test_render


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = json.loads((FIXTURES / "espn-scoreboards.json").read_text())["scoreboards"]


class Calendar(unittest.TestCase):
    def test_tbd_has_no_timed_export(self):
        event = test_render.Markup.event()
        event["competitions"][0]["timeValid"] = False
        match = build.interpret("fifa.friendly.w", event)
        row = build.row_html(match, {})
        self.assertIn('t--tbd">TBD', row)
        self.assertNotIn("Add to calendar", row)
        self.assertEqual(build.gcal_link(match), "")

    def test_export_names_listed_channels_independent_of_the_owner_service(self):
        event = test_render.Markup.event()
        event["competitions"][0]["geoBroadcasts"] = [{"media": {"shortName": "ESPN+"}, "region": "us"}]
        match = build.interpret("fifa.friendly.w", event)
        original = build.gcal_link(match)
        visitor = replace(match, service="espnplus")
        self.assertEqual(original, build.gcal_link(visitor))
        query = parse_qs(urlparse(original).query)
        self.assertEqual(query["dates"], ["20261010T183000Z/20261010T203000Z"])
        self.assertEqual(query["details"], ["Women's friendly. Listed channels: ESPN+."])
        self.assertNotIn("ESPN Unlimited", query["details"][0])

    def test_export_preserves_uncertainty_about_the_broadcaster(self):
        with patch.object(build, "TODAY", TODAY):
            match = build.interpret("esp.1", test_render.Markup.event())
        text = parse_qs(urlparse(build.gcal_link(match)).query)["details"][0]
        self.assertIn("Usually on ESPN+; match listing not confirmed.", text)


class RecordedESPN(unittest.TestCase):
    def test_sampled_scoreboards_parse_with_actual_names_aliases_and_odds(self):
        samples = {sample["league"]: sample["data"]["events"][0] for sample in SAMPLES}
        arsenal = build.interpret("eng.1", samples["eng.1"])
        self.assertEqual((arsenal.home.id, arsenal.home.name, arsenal.away.name), ("359", "Arsenal", "Leeds United"))
        self.assertEqual([outlet.label for outlet in arsenal.outlets], ["USA Network", "Universo"])
        self.assertAlmostEqual(arsenal.draw, 0.2)
        self.assertEqual(arsenal.goal_line, 2.5)
        with patch.object(build, "TODAY", TODAY):
            america = build.interpret("mex.1", samples["mex.1"])
        self.assertEqual((america.home.id, america.home.name, america.score), ("227", "América", 85))
        self.assertEqual(america.rule.label, "ViX")

    def test_display_name_changes_do_not_change_scoring_or_home_rights(self):
        event = deepcopy(next(sample["data"]["events"][0] for sample in SAMPLES if sample["league"] == "mex.1"))
        with patch.object(build, "TODAY", TODAY):
            before = build.interpret("mex.1", event)
            home = next(team for team in event["competitions"][0]["competitors"] if team["homeAway"] == "home")
            home["team"]["displayName"] = "Club América renamed"
            after = build.interpret("mex.1", event)
            self.assertEqual((after.score, after.rule), (before.score, before.rule))
            home["team"]["displayName"] = "América"
            home["team"]["id"] = "99999999"
            unrelated = build.interpret("mex.1", event)
        self.assertEqual(unrelated.score, 60)
        self.assertIsNone(unrelated.rule)

    def test_featured_national_teams_are_ids_not_labels(self):
        for team_id in ("660", "2765"):
            match = build.interpret("fifa.friendly.w", test_render.Markup.event(home="USA renamed", home_id=team_id))
            self.assertTrue(build.featured(match))
            self.assertEqual(match.score, 165)  # tier 60 + two marquee nations 60 + US bonus 45
        unrelated = build.interpret("fifa.friendly.w", test_render.Markup.event(home="United States", home_id="99999999"))
        self.assertFalse(build.featured(unrelated))
        self.assertEqual(unrelated.score, 90)

    def test_non_utc_offset_is_normalized_before_export(self):
        event = test_render.Markup.event()
        event["date"] = "2026-10-10T14:30:00-04:00"
        match = build.interpret("fifa.friendly.w", event)
        self.assertEqual(match.utc.isoformat(), "2026-10-10T18:30:00+00:00")
        self.assertIn('data-utc="2026-10-10T18:30:00Z"', build.row_html(match, {}))


class SourceQuality(unittest.TestCase):
    def fetch(self, answers):
        quality = build.BuildQuality()
        leagues = {key: dict(name=key, tier=1, short=key.upper()) for key in answers}

        def request(url):
            data = answers[url.split("/soccer/")[1].split("/")[0]]
            return json.dumps(data).encode() if data is not None else None

        with patch.dict(build.LEAGUES, leagues, clear=True), patch.object(build, "curl_bytes", request):
            merged, _, failed = build.fetch_scoreboards([TODAY], 2, quality)
        return quality, merged, failed

    def test_error_objects_are_failed_sources_not_empty_days(self):
        quality, merged, failed = self.fetch({"good": {"events": []}, "error": {"error": "unavailable"},
                                             "null": {"events": None}, "network": None})
        self.assertEqual(quality.report(1)["sources"]["successful"], 1)
        self.assertEqual({league for league, _ in failed}, {"error", "null", "network"})
        self.assertFalse(quality.report(1)["publishable"])
        self.assertEqual(merged["good"], {})

    def test_bad_list_entries_are_reported_without_losing_good_events(self):
        quality, merged, failed = self.fetch({"eng.1": {"events": [None, {"name": "No ID"}, test_render.Markup.event()]}})
        self.assertFalse(failed)
        self.assertEqual(list(merged["eng.1"]), ["9"])
        self.assertEqual(len(quality.skipped_events), 2)
        report = quality.report(1)
        self.assertTrue(report["publishable"])
        self.assertFalse(report["complete"])

    def test_missing_competition_cannot_abort_the_other_matches(self):
        broken = test_render.Markup.event()
        broken.update(id="broken", competitions=[])
        quality, merged, _ = self.fetch({"eng.1": {"events": [broken, test_render.Markup.event()]}})
        with patch("sys.stderr", io.StringIO()):
            matches = build.interpret_all(merged, quality)
        self.assertEqual([match.id for match in matches], ["9"])
        self.assertEqual(quality.skipped_events[0]["id"], "broken")
        page = build.build_page(matches, {}, BUILT_AT, [], TODAY, quality.skipped_events)
        self.assertIn('data-incomplete="1"', page)
        self.assertIn("1 unreadable fixture record from Premier League", page)

    def test_empty_healthy_slate_is_allowed_but_all_malformed_events_are_not(self):
        quality, _, _ = self.fetch({"eng.1": {"events": []}})
        self.assertTrue(quality.report(0)["publishable"])
        self.assertTrue(quality.report(0)["complete"])
        broken = test_render.Markup.event()
        broken["competitions"] = []
        quality, merged, _ = self.fetch({"eng.1": {"events": [broken]}})
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(build.interpret_all(merged, quality), [])
        self.assertFalse(quality.report(0)["publishable"])

    def test_intentionally_excluded_rounds_can_leave_a_valid_empty_slate(self):
        event = test_render.Markup.event()
        event["season"] = {"slug": "qualifying-round"}
        quality, merged, _ = self.fetch({"esp.copa_del_rey": {"events": [event]}})
        self.assertEqual(build.interpret_all(merged, quality), [])
        self.assertEqual(quality.report(0)["excluded_events"], 1)
        self.assertTrue(quality.report(0)["publishable"])

    def test_partial_response_threshold_is_based_on_requests(self):
        quality, _, _ = self.fetch({"a": {"events": []}, "b": {"events": []}, "c": None, "d": None})
        self.assertTrue(quality.report(1)["publishable"])
        self.assertFalse(quality.report(0)["publishable"])


class BuildCommand(unittest.TestCase):
    def run_build(self, payload, directory):
        argv = ["build.py", "--no-logos", "--days-back", "0", "--days-ahead", "0",
                "--out", str(directory / "index.html"), "--report", str(directory / "report.json")]
        with patch("sys.argv", argv), patch.dict(build.LEAGUES, {"eng.1": build.LEAGUES["eng.1"]}, clear=True), \
                patch.dict(build.STANDINGS, {}, clear=True), patch.object(build, "fetch_standings", return_value={}), \
                patch.object(build, "curl_bytes", return_value=json.dumps(payload).encode()), \
                patch.object(build, "audit", return_value=[]), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            return build.main()

    def test_complete_zero_or_one_fixture_build_publishes_and_reports_volume(self):
        for events in ([], [test_render.Markup.event()]):
            with self.subTest(count=len(events)), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.assertEqual(self.run_build({"events": events}, directory), 0)
                report = json.loads((directory / "report.json").read_text())
                self.assertEqual((report["fixtures"], report["complete"], report["publishable"]), (len(events), True, True))
                page = (directory / "index.html").read_text()
                self.assertEqual(page.count('<li class="row '), len(events))
                if not events:
                    self.assertIn("No matches are scheduled in the fetched date range.", page)

    def test_failed_sources_report_failure_and_preserve_existing_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "index.html").write_text("previous good page")
            self.assertNotEqual(self.run_build({"error": "unavailable"}, directory), 0)
            self.assertFalse(json.loads((directory / "report.json").read_text())["publishable"])
            self.assertEqual((directory / "index.html").read_text(), "previous good page")


if __name__ == "__main__":
    unittest.main()
