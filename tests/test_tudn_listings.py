"""TUDN's exact US match metadata adds simulcasts without inventing rights."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import build
import tudn_listings as tudn
from tests.test_discovery import fixture


FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = (FIXTURES / "tudn-results.html").read_bytes()
MATCH = (FIXTURES / "tudn-match.html").read_bytes()
URL = "https://www.tudn.com/futbol/liga-mx/america-vs-monterrey-liga-mx-ap-2026-10-10"


def game(**changes):
    result = fixture(league="mex.1", channel="", kickoff="2026-10-11T03:10:00Z")
    result.home.name, result.away.name = "América", "Monterrey"
    result.rule = build.map_outlet("ViX", result.league)
    return replace(result, **changes)


def payload(sample, key):
    return next(obj for obj in tudn._objects(sample) if key in obj)


def page(obj):
    data = "42:" + json.dumps(["$", "$L1", None, obj]) + "\n"
    return ("<script>self.__next_f.push(" + json.dumps([1, data]) + ")</script>").encode()


def enrich(match=None, schedule=RESULTS, detail=MATCH):
    target = game() if match is None else match
    fetch = Mock(side_effect=lambda url: schedule if url == tudn.SCHEDULE_URL else detail)
    result = tudn.enrich(build, [target], fetch)
    return target, result, fetch


class TUDNListings(unittest.TestCase):
    def test_recorded_us_match_makes_youtube_main_viewable(self):
        target, result, fetch = enrich()
        self.assertEqual((result["requested"], result["confirmed"], result["unmatched"]), (2, 1, 0))
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [tudn.SCHEDULE_URL, URL])
        self.assertEqual([outlet.label for outlet in target.outlets], ["TUDN", "Univision"])
        self.assertEqual(build.evaluate(target.outlets, target.rule, {"yttv"})[:2], ("yttv", "listed"))
        self.assertEqual(target.broadcast_source, "TUDN")
        self.assertEqual(result["per_match"][target.id]["status"], "confirmed")
        self.assertEqual(result["per_match"][target.id]["url"], URL)
        self.assertEqual(result["per_match"][target.id]["channels"], ["TUDN", "Univision"])
        self.assertIsNone(target.rule)

    def test_existing_vix_and_espn_outlets_are_preserved_without_duplicates(self):
        for labels in (("ViX",), ("TUDN", "ViX"), ("ESPN Deportes",)):
            with self.subTest(labels=labels):
                target = game(outlets=[build.map_outlet(label, "mex.1") for label in labels], rule=None)
                target, result, _ = enrich(target)
                actual = [outlet.label for outlet in target.outlets]
                self.assertEqual(set(actual), set(labels) | {"TUDN", "Univision"})
                self.assertEqual(len(actual), len(set(actual)))
                self.assertEqual(result["confirmed"], 1)

    def test_live_and_finished_matches_are_eligible(self):
        for state in ("pre", "in", "post"):
            target, result, _ = enrich(game(state=state))
            self.assertEqual(result["confirmed"], 1)
            self.assertEqual(target.state, state)

    def test_exact_clubs_home_assignment_and_kickoff_required_for_confirmation(self):
        targets = [game(utc=game().utc + timedelta(minutes=1)), game(utc=game().utc + timedelta(days=1))]
        wrong_home = game(); wrong_home.home.name = "Atlas"; targets.append(wrong_home)
        wrong_away = game(); wrong_away.away.name = "Toluca"; targets.append(wrong_away)
        reversed_teams = game(); reversed_teams.home, reversed_teams.away = reversed_teams.away, reversed_teams.home
        targets.append(reversed_teams)
        for target in targets:
            with self.subTest(target=target):
                _, result, fetch = enrich(target)
                self.assertEqual(result["confirmed"], 0)
                self.assertLessEqual(fetch.call_count, 3)
                self.assertEqual(target.rule.label, "ViX")

    def test_unlisted_vix_match_does_not_imply_tudn(self):
        target = game(utc=tudn.kickoff("2026-10-10T23:00:00Z"))
        target.home.name, target.away.name = "Querétaro", "Atlante"
        # An unrelated valid TUDN match page cannot confirm the discovered ViX-only match.
        _, result, _ = enrich(target)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(target.rule.label, "ViX")

    def test_schedule_requires_us_scope_and_unambiguous_exact_event(self):
        for case in ("mx", "competition", "uri", "url", "duplicate", "league", "naive", "replay"):
            with self.subTest(case=case):
                data = payload(RESULTS, "queryResponse")
                events = data["queryResponse"]["getMatches"]["events"]
                event = next(e for e in events if e["key"] == "2641268")
                if case == "mx": data["isMx"] = True
                if case == "competition": data["competitionKey"] = "other"
                if case == "uri": data["uri"] = "https://www.tudn.com/mx/futbol/liga-mx"
                if case == "url": event["relativeUrl"] = "https://evil.example" + event["relativeUrl"]
                if case == "duplicate":
                    duplicate = copy.deepcopy(event); duplicate["key"] = "9999"; events.append(duplicate)
                if case == "league": event["leagueName"] = "Liga MX Femenil"
                if case == "naive": event["date"] = "2026-10-11T03:10:00"
                if case == "replay": event["status"] = "replay"
                if case in ("mx", "competition", "uri"):
                    with self.assertRaises(ValueError): tudn.parse_schedule(page(data))
                else:
                    found = [item for item in tudn.parse_schedule(page(data))
                             if (item.home, item.away, item.utc) == ("america", "monterrey", game().utc)]
                    if case == "duplicate": self.assertEqual(len(found), 2)
                    elif case == "url": self.assertFalse(found[0].url)
                    else: self.assertEqual(found, [])

    def test_identical_responsive_duplicate_scorecards_are_allowed(self):
        data = payload(RESULTS, "queryResponse")
        events = data["queryResponse"]["getMatches"]["events"]
        events.extend(copy.deepcopy(events))
        self.assertEqual(enrich(schedule=page(data))[1]["confirmed"], 1)

    def test_detail_requires_matching_us_live_event_and_tv_channels(self):
        cases = ("mx", "competition", "home", "away", "id", "uri", "league", "opening_id", "kickoff", "status",
                 "config_home", "config_away", "config_league", "origin", "replay", "stream_id", "stream_uri",
                 "auth", "mvpd", "ad_live", "ad_event", "unknown", "empty", "wrong_type", "coverage", "duplicate")
        for case in cases:
            with self.subTest(case=case):
                data = payload(MATCH, "livestreamConfig")
                config = data["livestreamConfig"]
                stream = config["livestreams"][0]
                opening = data["matchOpeningRef"]
                event = opening["response"]["data"]["matchOpening"]
                if case == "mx": data["isMx"] = True
                if case == "competition": data["leagueId"] = "other"
                if case in ("home", "away"): data[case + "Team"]["name"] = "Another Club"
                if case == "id": data["matchId"] = "9999"
                if case == "uri": data["pageUri"] += "?redirect=elsewhere"
                if case == "league": data["leagueName"] = "Liga MX Women"
                if case == "opening_id": opening["variables"]["matchId"] = "9999"
                if case == "kickoff": event["date"] = "2026-10-11T03:00:00Z"
                if case == "status": event["status"] = "postponed"
                if case == "config_home": config["homeTeamName"] = "Another Club"
                if case == "config_away": config["awayTeamName"] = "Another Club"
                if case == "config_league": config["leagueName"] = "Liga MX Women"
                if case == "origin": stream["mcpOrigin"] = "MX"
                if case == "replay": stream["videoType"] = "replay"
                if case == "stream_id": stream["livestreamId"] = 9999
                if case == "stream_uri": stream["uri"] = "https://evil.example/match"
                if case == "auth": stream["isAuth"] = False
                if case == "mvpd": stream["placeholderId"] = "vix"
                if case == "ad_live": stream["adTargeting"]["stream_type"] = "replay"
                if case == "ad_event": stream["adTargeting"]["video_type"] = "highlights"
                if case == "unknown": stream["channels"].append("canal5")
                if case == "empty": stream["channels"] = []
                if case == "wrong_type": stream["channels"] = "udn"
                if case == "coverage": config["televisionCoverage"] = "univision"
                if case == "duplicate":
                    other = copy.deepcopy(data); other["matchId"] = "9999"; data = [data, other]
                target, result, _ = enrich(detail=page(data))
                self.assertEqual(result["confirmed"], 0)
                self.assertEqual(target.rule.label, "ViX")

    def test_failed_or_malformed_sources_leave_prior_coverage_intact(self):
        for bad in (None, b"blocked", b"\xff", b"x" * 2_000_001,
                    b'<script>self.__next_f.push([1,"42:{bad}\\n"])</script>'):
            for position in ("schedule", "detail"):
                with self.subTest(position=position, size=len(bad or b"")):
                    target = game(outlets=[build.map_outlet("ViX", "mex.1")], rule=None)
                    _, result, _ = enrich(target, **{position: bad})
                    self.assertEqual(result["confirmed"], 0)
                    self.assertEqual(result["failed"], 1)
                    self.assertEqual([outlet.label for outlet in target.outlets], ["ViX"])
        fetch = Mock(side_effect=OSError("offline"))
        self.assertEqual(tudn.enrich(build, [game()], fetch)["failed"], 1)

    def test_cancelled_tbd_and_other_competitions_do_not_request(self):
        for target in (game(status="postponed"), game(time_valid=False), game(league="eng.1"),
                       game(utc=game().utc.replace(tzinfo=None))):
            fetch = Mock()
            self.assertEqual(tudn.enrich(build, [target], fetch)["requested"], 0)
            fetch.assert_not_called()

    def test_request_budget_and_detail_cache(self):
        fetch = Mock(side_effect=[RESULTS, MATCH])
        first, second = game(id="first"), game(id="second")
        result = tudn.enrich(build, [first, second], fetch)
        self.assertEqual((fetch.call_count, result["confirmed"]), (2, 2))
        with patch.object(tudn, "MAX_MATCH_REQUESTS", 0):
            _, result, fetch = enrich()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["per_match"]["match"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
