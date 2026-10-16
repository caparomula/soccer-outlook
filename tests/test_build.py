"""Publication depends on usable ESPN sources, not on how busy the soccer calendar is."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import build
from tests.page_fixture import BUILT_AT, TODAY
from tests import test_render


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = json.loads((FIXTURES / "espn-scoreboards.json").read_text())["scoreboards"]


class FetchDates(unittest.TestCase):
    def dates(self, timestamp, days_back=3, days_ahead=4, fixed_date=None):
        built_at = datetime.fromisoformat(timestamp)
        today = fixed_date or built_at.astimezone(build.ET).date()
        return build.scoreboard_dates(today, built_at, days_back, days_ahead, fixed_date=fixed_date is not None)

    def test_pre_dawn_build_keeps_three_previous_sports_days_and_forward_buffer(self):
        days = self.dates("2026-10-12T05:14:22+00:00")
        self.assertEqual(days, [date(2026, 10, 8) + timedelta(days=i) for i in range(9)])
        # The buffer remains after 4 am Eastern: Pacific viewers are still on the prior sports day.
        self.assertEqual(self.dates("2026-10-12T08:00:00+00:00"), days)
        self.assertEqual(self.dates("2026-10-12T07:59:59+00:00"), days)

    def test_before_midnight_eastern_does_not_use_the_utc_date(self):
        days = self.dates("2026-10-12T02:00:00+00:00")
        self.assertEqual((days[0], days[-1], len(days)), (date(2026, 10, 7), date(2026, 10, 15), 9))

    def test_lookback_crosses_month_and_year_boundaries(self):
        for timestamp, first in (("2027-01-01T05:30:00+00:00", date(2026, 12, 28)),
                                 ("2026-03-01T05:30:00+00:00", date(2026, 2, 25))):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(self.dates(timestamp)[0], first)

    def test_dst_changes_preserve_the_local_sports_day_buffer(self):
        for timestamp, first, count in (
                ("2026-03-08T06:30:00+00:00", date(2026, 3, 4), 9),  # 1:30 EST
                ("2026-03-08T07:30:00+00:00", date(2026, 3, 4), 9),  # 3:30 EDT
                ("2026-03-08T08:00:00+00:00", date(2026, 3, 4), 9),  # 4:00 EDT
                ("2026-11-01T05:30:00+00:00", date(2026, 10, 28), 9),  # first 1:30
                ("2026-11-01T06:30:00+00:00", date(2026, 10, 28), 9),  # repeated 1:30
                ("2026-11-01T09:00:00+00:00", date(2026, 10, 28), 9)):  # 4:00 EST
            with self.subTest(timestamp=timestamp):
                days = self.dates(timestamp)
                self.assertEqual((days[0], len(days)), (first, count))

    def test_three_previous_local_sports_days_fit_even_after_eastern_rollover(self):
        # Date-line extremes and both DST changes exercise the history boundary independently
        # of the fetch algorithm. Every instant in the three local sports days must be covered.
        for calendar_day in (date(2026, 10, 16), date(2026, 3, 8), date(2026, 11, 1)):
            for hour in (0, 6, 9, 12, 18, 23):
                built_at = datetime.combine(calendar_day, time(hour), timezone.utc)
                fetched = self.dates(built_at.isoformat())
                for zone in ("Etc/GMT+12", "Pacific/Honolulu", "America/Los_Angeles",
                             "America/New_York", "Europe/London", "Asia/Tokyo", "Pacific/Kiritimati"):
                    with self.subTest(now=built_at, zone=zone):
                        local_now = built_at.astimezone(ZoneInfo(zone))
                        sports_date = local_now.date() - timedelta(days=local_now.hour < 4)
                        for ago in (1, 2, 3):
                            day = sports_date - timedelta(days=ago)
                            first = datetime.combine(day, time(4), ZoneInfo(zone))
                            last = datetime.combine(day + timedelta(days=1), time(4), ZoneInfo(zone)) - timedelta(seconds=1)
                            self.assertIn(first.astimezone(build.ET).date(), fetched)
                            self.assertIn(last.astimezone(build.ET).date(), fetched)
                        self.assertEqual(fetched[-1], built_at.astimezone(build.ET).date() + timedelta(days=4))

    def test_explicit_date_has_the_same_calendar_range_at_every_hour(self):
        expected = [date(2026, 10, 9) + timedelta(days=i) for i in range(8)]
        for timestamp in ("2027-01-01T06:00:00+00:00", "2027-01-01T16:00:00+00:00"):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(self.dates(timestamp, fixed_date=date(2026, 10, 12)), expected)

    def test_custom_range_adds_only_one_earlier_date(self):
        days = self.dates("2026-10-12T05:00:00+00:00", days_back=2, days_ahead=0)
        self.assertEqual(days, [date(2026, 10, 9) + timedelta(days=i) for i in range(4)])


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

    def test_us_national_team_score_bonus_uses_ids_not_labels(self):
        for team_id in ("660", "2765"):
            match = build.interpret("fifa.friendly.w", test_render.Markup.event(home="USA renamed", home_id=team_id))
            self.assertEqual(match.score, 165)  # tier 60 + two marquee nations 60 + US bonus 45
        unrelated = build.interpret("fifa.friendly.w", test_render.Markup.event(home="United States", home_id="99999999"))
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
            merged, _, failed = build.fetch_scoreboards([date(2026, 10, 10)], 2, quality)
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

    def test_explicit_wrong_league_or_requested_date_is_not_a_successful_empty_day(self):
        for metadata in ({"leagues": [{"slug": "mex.1"}]}, {"day": {"date": "2026-10-11"}}):
            quality, merged, failed = self.fetch({"eng.1": {"events": [], **metadata}})
            self.assertEqual(len(failed), 1)
            self.assertEqual(merged["eng.1"], {})
            self.assertFalse(quality.report(0)["complete"])

    def test_eastern_date_accepts_after_utc_midnight_and_rejects_another_day(self):
        for instant, accepted in (("2026-10-11T03:05:00Z", True),
                                  ("2026-10-11T04:05:00Z", False),
                                  ("2026-10-10T23:05:00-04:00", True)):
            with self.subTest(instant=instant):
                event = dict(test_render.Markup.event(), date=instant)
                quality, merged, failed = self.fetch({"mex.1": {"events": [event], "leagues": [{"slug": "mex.1"}]}})
                self.assertEqual(bool(merged["mex.1"]), accepted)
                self.assertEqual(bool(failed), not accepted)
                self.assertEqual(quality.report(len(merged["mex.1"]))["complete"], accepted)

    def test_incomplete_result_limit_preserves_usable_events_but_reports_source_failure(self):
        for data in ({"events": [test_render.Markup.event()], "totalEvents": 2},
                     {"events": [dict(test_render.Markup.event(), id=str(i)) for i in range(200)]}):
            quality, merged, failed = self.fetch({"eng.1": data})
            self.assertTrue(merged["eng.1"])
            self.assertEqual(len(failed), 1)
            self.assertIn("limit", quality.sources[0]["reason"])
            self.assertFalse(quality.report(len(merged["eng.1"]))["complete"])


class BuildCommand(unittest.TestCase):
    def run_build(self, payload, directory, extra_args=()):
        argv = ["build.py", "--no-logos", "--days-back", "0", "--days-ahead", "0",
                "--out", str(directory / "index.html"), "--report", str(directory / "report.json"), *extra_args]
        with patch("sys.argv", argv), patch.dict(build.LEAGUES, {"eng.1": build.LEAGUES["eng.1"]}, clear=True), \
                patch.dict(build.STANDINGS, {}, clear=True), patch.object(build, "fetch_standings", return_value={}), \
                patch.object(build, "curl_bytes", return_value=json.dumps(payload).encode()), \
                patch.object(build, "audit", return_value=[]), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            return build.main()

    def test_pre_dawn_build_fetches_previous_evening_without_changing_rights_date(self):
        built_at = datetime(2026, 10, 12, 5, 14, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary, patch.object(build, "datetime", wraps=datetime) as clock, \
                patch.object(build, "fetch_scoreboards", wraps=build.fetch_scoreboards) as fetch, \
                patch.object(build, "TODAY"):
            clock.now.return_value = built_at
            self.assertEqual(self.run_build({"events": []}, Path(temporary)), 0)
            self.assertEqual(fetch.call_args.args[0], [date(2026, 10, 11), date(2026, 10, 12)])
            self.assertEqual(build.TODAY, date(2026, 10, 12))

    def test_explicit_date_build_uses_only_the_configured_calendar_range_before_dawn(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(build, "datetime", wraps=datetime) as clock, \
                patch.object(build, "fetch_scoreboards", wraps=build.fetch_scoreboards) as fetch, \
                patch.object(build, "TODAY"):
            clock.now.return_value = datetime(2027, 1, 1, 6, tzinfo=timezone.utc)
            self.assertEqual(self.run_build({"events": []}, Path(temporary), ["--date", "2026-10-12"]), 0)
            self.assertEqual(fetch.call_args.args[0], [date(2026, 10, 12)])
            self.assertEqual(build.TODAY, date(2026, 10, 12))

    def test_complete_zero_or_one_fixture_build_publishes_and_reports_volume(self):
        for events in ([], [test_render.Markup.event()]):
            with self.subTest(count=len(events)), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.assertEqual(self.run_build({"events": events}, directory, ["--date", "2026-10-10"]), 0)
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
