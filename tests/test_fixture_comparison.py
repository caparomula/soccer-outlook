"""Independent fixture evidence reports discrepancies without changing viewing routes."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import build
import fixture_comparison as comparison
from fox_listings import _Payload
from tests.test_discovery import fixture


SAMPLE = (Path(__file__).parent / "fixtures" / "fox-fixtures-20261016.html").read_bytes()
NOW = datetime(2026, 10, 15, 19, tzinfo=timezone.utc)
DAY = date(2026, 10, 16)


def decoded():
    parser = _Payload()
    parser.feed(SAMPLE.decode())
    table = json.loads("".join(parser.parts))

    def ref(index):
        value = table[index]
        if isinstance(value, dict):
            return {key: ref(pointer) for key, pointer in value.items()}
        if isinstance(value, list):
            return [ref(pointer) for pointer in value]
        return value
    return ref(0)


def encoded(root):
    table = []

    def add(value):
        index = len(table)
        table.append(None)
        if isinstance(value, dict):
            table[index] = {key: add(item) for key, item in value.items()}
        elif isinstance(value, list):
            table[index] = [add(item) for item in value]
        else:
            table[index] = value
        return index
    add(root)
    return ('<script type="application/json" id="__NUXT_DATA__">' + json.dumps(table) + '</script>').encode()


def games():
    rows = comparison.parse_fox(SAMPLE, DAY)["fixtures"]
    result = []
    for row in rows:
        match = fixture(match_id="espn" + row.source_id.rsplit("/", 1)[1], league="mex.1",
                        channel="", kickoff=row.utc.isoformat())
        match.home = build.Team(row.home, "", "", "", id=row.home_id)
        match.away = build.Team(row.away, "", "", "", id=row.away_id)
        result.append(match)
    return result


class IndependentFixtures(unittest.TestCase):
    def compare(self, matches=None, sample=SAMPLE, days=(DAY,), **kwargs):
        return comparison.compare(games() if matches is None else matches, days,
                                  lambda _: sample, observed_at=NOW, **kwargs)

    def test_current_recorded_date_matches_three_scoped_identities_without_mutation(self):
        matches = games()
        original = deepcopy(matches)
        report = self.compare(matches)
        self.assertEqual(report["counts"], dict(matched=3, missing_from_espn=0,
            missing_from_secondary=0, kickoff_conflicts=0, ambiguous=0, unmapped=0, unknown=0))
        self.assertEqual(report["scope"], {"mex.1": "FOX Sports fixtures"})
        self.assertEqual(report["mode"], "observation_only")
        self.assertTrue(report["complete"])
        self.assertEqual(report["sources"][0]["observed_dates"], ["2026-10-16"])
        self.assertEqual(matches, original)
        json.dumps(report)  # Publication must never contain datetimes or dataclasses.

    def test_queries_when_espn_has_no_matches_so_primary_omissions_are_discoverable(self):
        fetch = Mock(return_value=SAMPLE)
        report = comparison.compare([], [DAY], fetch, observed_at=NOW)
        fetch.assert_called_once_with(comparison.SCORES_URL.format(day="2026-10-16"))
        self.assertEqual(len(report["missing_from_espn"]), 3)
        self.assertTrue(all(row["source_id"].startswith("soccer/liga_mx/events/")
                            for row in report["missing_from_espn"]))

    def test_real_five_minute_tijuana_difference_is_a_conflict_not_a_missing_fixture(self):
        matches = games()
        matches[1].utc += timedelta(minutes=5)
        report = self.compare(matches)
        self.assertEqual(len(report["matched"]), 2)
        self.assertEqual(report["kickoff_conflicts"][0]["difference_minutes"], -5)
        self.assertEqual(report["missing_from_secondary"], [])
        self.assertEqual(matches[1].utc.minute, 5)

    def test_date_shift_is_one_conflict_without_false_omission_pair(self):
        matches = games()
        matches[1].utc += timedelta(days=1)
        root = decoded()
        root["scoresData"]["currentSegmentId"] = "c6d20261017"
        root["scoresData"]["parsedSegmentRes"] = {"segment": {"id": "c6d20261017"}, "events": []}
        report = comparison.compare(matches, [DAY, DAY + timedelta(days=1)],
            lambda url: SAMPLE if url.endswith("2026-10-16") else encoded(root), observed_at=NOW)
        self.assertEqual(len(report["kickoff_conflicts"]), 1)
        self.assertEqual(report["kickoff_conflicts"][0]["difference_minutes"], -1440)
        self.assertFalse(report["missing_from_espn"] or report["missing_from_secondary"])

    def test_true_inspected_empty_day_can_report_secondary_absence(self):
        root = decoded()
        root["scoresData"]["parsedSegmentRes"]["events"] = []
        report = self.compare(sample=encoded(root))
        self.assertEqual(len(report["missing_from_secondary"]), 3)
        self.assertTrue(all(item["reason"] == "absent_from_observed_date"
                            for item in report["missing_from_secondary"]))

    def test_ignored_date_or_stale_segment_does_not_establish_absence(self):
        for key in ("selection", "segment"):
            root = decoded()
            if key == "selection":
                root["scoresData"]["currentSegmentId"] = "c6d20261009"
            else:
                root["scoresData"]["parsedSegmentRes"]["segment"]["id"] = "c6d20261009"
            report = self.compare(sample=encoded(root))
            self.assertEqual(report["missing_from_secondary"], [])
            self.assertEqual(len(report["unknown"]), 3)
            self.assertEqual(report["sources"][0]["status"], "outside_source_horizon")
            self.assertFalse(report["complete"])

    def test_invalid_or_unknown_event_does_not_make_other_matches_falsely_missing(self):
        cases = [("template", "broadcast"), ("contentType", "replay"), ("isReplay", True),
                 ("league", "MLS"), ("contentUri", "soccer/mls/events/878147"),
                 ("eventTime", "2026-10-18T01:00:00Z"), ("isTba", True)]
        for key, value in cases:
            with self.subTest(key=key):
                root = decoded()
                root["scoresData"]["parsedSegmentRes"]["events"][0][key] = value
                report = self.compare(sample=encoded(root))
                self.assertEqual(len(report["matched"]), 2)
                self.assertEqual(report["missing_from_secondary"], [])
                self.assertEqual(len(report["unmapped"]), 1)
                self.assertEqual(report["sources"][0]["status"], "incomplete")

    def test_unmapped_source_id_or_conflicting_name_cannot_borrow_a_familiar_club(self):
        for key, value in (("uri", "soccer/all/teams/999999"), ("longName", "Not Necaxa")):
            root = decoded()
            root["scoresData"]["parsedSegmentRes"]["events"][0]["lowerTeam"][key] = value
            report = self.compare(sample=encoded(root))
            self.assertEqual(len(report["matched"]), 2)
            self.assertTrue(report["unmapped"])
            self.assertFalse(report["missing_from_secondary"])

    def test_primary_identity_uses_id_with_only_an_explicit_alias_fallback(self):
        matches = games()
        matches[2].away.name = "Pumas UNAM"
        matches[2].away.id = ""
        self.assertEqual(len(self.compare(matches)["matched"]), 3)
        matches[2].away.id = "unrecognized"
        report = self.compare(matches)
        self.assertEqual(len(report["matched"]), 2)
        self.assertTrue(report["unmapped"])
        self.assertFalse(report["missing_from_espn"])

    def test_unconfirmed_primary_time_does_not_become_an_omission(self):
        matches = games()
        matches[1].time_valid = False
        report = self.compare(matches)
        self.assertEqual(len(report["matched"]), 2)
        self.assertFalse(report["missing_from_espn"])
        self.assertTrue(report["unknown"])

    def test_duplicate_source_or_primary_identity_remains_ambiguous(self):
        root = decoded()
        root["scoresData"]["parsedSegmentRes"]["events"].append(
            deepcopy(root["scoresData"]["parsedSegmentRes"]["events"][0]))
        report = self.compare(sample=encoded(root))
        self.assertEqual(len(report["ambiguous"]), 1)
        matches = games()
        matches.append(replace(matches[0], id="another"))
        report = self.compare(matches)
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertFalse(report["missing_from_espn"] or report["missing_from_secondary"])

    def test_unsafe_source_link_or_foreign_scope_is_not_accepted(self):
        root = decoded()
        root["scoresData"]["parsedSegmentRes"]["events"][0]["entityLink"]["webUrl"] = "https://evil.example/fixture"
        self.assertTrue(self.compare(sample=encoded(root))["unmapped"])
        root["contentUri"] = "soccer/mls/league/5"
        report = self.compare(sample=encoded(root))
        self.assertEqual(report["failed"], 1)
        self.assertFalse(report["missing_from_secondary"])

    def test_transport_malformed_or_oversized_data_is_unknown_not_absent(self):
        for sample in (None, b"unavailable", b"\xff", SAMPLE + SAMPLE,
                       b"x" * (comparison.MAX_PAGE_BYTES + 1)):
            report = self.compare(sample=sample)
            self.assertEqual(report["failed"], 1)
            self.assertEqual(len(report["unknown"]), 3)
            self.assertFalse(report["missing_from_secondary"])
        report = comparison.compare(games(), [DAY], Mock(side_effect=OSError()), observed_at=NOW)
        self.assertEqual(report["failed"], 1)

    def test_completed_or_past_fixture_does_not_get_reported_as_an_upcoming_omission(self):
        self.assertFalse(self.compare([replace(m, state="post") for m in games()])["missing_from_espn"])
        report = comparison.compare([], [DAY], lambda _: SAMPLE, observed_at=NOW + timedelta(days=4))
        self.assertFalse(report["missing_from_espn"])

    def test_scope_and_request_bound_are_explicit(self):
        fetch = Mock(return_value=SAMPLE)
        days = [DAY + timedelta(days=i) for i in range(10)]
        report = comparison.compare([fixture()], days, fetch, observed_at=NOW)
        self.assertEqual(fetch.call_count, comparison.MAX_DATES)
        self.assertEqual(len(report["skipped_dates"]), 4)
        self.assertEqual(report["unsupported_leagues"], ["eng.1"])
        self.assertFalse(report["complete"])

    def test_added_history_does_not_displace_upcoming_comparison_dates(self):
        days = [DAY + timedelta(days=i) for i in range(-4, 5)]
        fetch = Mock(return_value=SAMPLE)
        report = comparison.compare([], days, fetch, observed_at=NOW)
        expected = [DAY + timedelta(days=i) for i in range(-1, 5)]
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         [comparison.SCORES_URL.format(day=day.isoformat()) for day in expected])
        self.assertEqual(fetch.call_count, comparison.MAX_DATES)
        self.assertEqual(report["skipped_dates"], [day.isoformat() for day in days[:3]])

    def test_comparison_date_priority_uses_eastern_date_before_utc_midnight_rollover(self):
        now = datetime(2026, 10, 17, 2, tzinfo=timezone.utc)  # Still October 16 in Eastern time.
        days = [DAY + timedelta(days=i) for i in range(-4, 5)]
        fetch = Mock(return_value=SAMPLE)
        comparison.compare([], days, fetch, observed_at=now)
        self.assertEqual(fetch.call_args_list[0].args[0], comparison.SCORES_URL.format(day="2026-10-15"))
        self.assertEqual(fetch.call_args_list[-1].args[0], comparison.SCORES_URL.format(day="2026-10-20"))


if __name__ == "__main__":
    unittest.main()
