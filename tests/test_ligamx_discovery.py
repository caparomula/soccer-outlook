"""Captured upcoming listings exercise discovery without weakening rights or replay checks."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import Mock, patch

import build
import fox_listings as fox
import tudn_listings as tudn
from tests.test_discovery import fixture
from tests.test_tudn_listings import page


FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = b'<script>window.__NUXT__.config={public:{bifrost:{apiKey:"fixturePublicClientKeyOnly123",apiPath:"https://api.foxsports.com/bifrost/v1/"}}}</script>'


def match(home, away, utc, identity="match", **changes):
    game = fixture(league="mex.1", channel="", kickoff=utc)
    game.home.name, game.away.name, game.id = home, away, identity
    return replace(game, **changes)


def tudn_capture():
    pages = {tudn.SCHEDULE_URL: (FIXTURES / "tudn-results.html").read_bytes(),
             tudn.LANDING_URL: (FIXTURES / "tudn-upcoming-landing.html").read_bytes()}
    matches = []
    for path in sorted(FIXTURES.glob("tudn-upcoming-2*.html")):
        source = path.read_bytes()
        obj = next(obj for obj in tudn._objects(source) if "livestreamConfig" in obj)
        opening = obj["matchOpeningRef"]["response"]["data"]["matchOpening"]
        pages[obj["pageUri"]] = source
        matches.append(match(obj["homeTeam"]["name"], obj["awayTeam"]["name"], opening["date"], obj["matchId"]))
    return pages, matches


class UpcomingTUDNDiscovery(unittest.TestCase):
    def test_past_results_round_does_not_hide_four_verified_upcoming_simulcasts(self):
        pages, matches = tudn_capture()
        fetch = Mock(side_effect=pages.get)
        result = tudn.enrich(build, matches, fetch)
        self.assertEqual((result["requested"], result["confirmed"], result["unmatched"]), (11, 4, 5))
        self.assertEqual({game.id for game in matches if game.outlets}, {"2641274", "2641277", "2641279", "2641280"})
        self.assertTrue(all(build.evaluate(game.outlets, None, {"yttv"})[0] == "yttv" for game in matches if game.outlets))
        self.assertEqual(result["diagnostics"]["reasons"]["results_horizon_before_candidates"], 9)
        self.assertEqual(result["diagnostics"]["observed_horizon"]["end"], "2026-10-19T01:00:00+00:00")
        self.assertEqual(len(fetch.call_args_list), len({call.args[0] for call in fetch.call_args_list}))
        for game in matches:
            if game.outlets:
                self.assertEqual(game.broadcast_evidence[0]["country"], "US")
                self.assertEqual(game.broadcast_evidence[0]["source_event_id"], game.id)

    def test_landing_widget_has_its_own_strict_us_competition_scope(self):
        capture = (FIXTURES / "tudn-upcoming-landing.html").read_bytes()
        source = next(obj for obj in tudn._objects(capture) if "queryRef" in obj)
        self.assertEqual({item.event_id for item in tudn.parse_landing(capture)}, {"2641273", "2641274"})
        for key, value in (("isMx", True), ("competitionKey", "other"), ("uri", "https://www.tudn.com/mx/futbol/liga-mx")):
            changed = copy.deepcopy(source); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): tudn.parse_landing(page(changed))
        changed = copy.deepcopy(source); changed["queryRef"]["variables"]["isMx"] = True
        with self.assertRaises(ValueError): tudn.parse_landing(page(changed))

    def test_candidate_pages_do_not_confirm_wrong_clubs_time_region_or_replay(self):
        pages, matches = tudn_capture()
        target = next(game for game in matches if game.id == "2641277")
        url = tudn.candidate_url(target)
        original = next(obj for obj in tudn._objects(pages[url]) if "livestreamConfig" in obj)
        for case in ("home", "away", "kickoff", "id", "region", "replay", "coverage"):
            obj = copy.deepcopy(original)
            if case in ("home", "away"): obj[case + "Team"]["name"] = "Another Club"
            if case == "kickoff": obj["matchOpeningRef"]["response"]["data"]["matchOpening"]["date"] = "2026-10-18T01:05:00Z"
            if case == "id": obj["matchId"] = "99999"
            if case == "region": obj["livestreamConfig"]["livestreams"][0]["mcpOrigin"] = "MX"
            if case == "replay": obj["livestreamConfig"]["livestreams"][0]["videoType"] = "replay"
            if case == "coverage": obj["livestreamConfig"]["televisionCoverage"] = "vix"
            with self.subTest(case=case):
                self.assertFalse(tudn.discover_match(page(obj), target, url)[1])

    def test_unknown_club_never_generates_an_arbitrary_candidate_path(self):
        self.assertEqual(tudn.candidate_url(match("Unknown FC", "León", "2026-10-18T01:00:00Z")), "")

    def test_discovery_failure_never_confirms_a_guessed_candidate(self):
        pages, matches = tudn_capture()
        pages[tudn.SCHEDULE_URL] = b"unavailable"
        pages[tudn.LANDING_URL] = b"unavailable"
        fetch = Mock(side_effect=pages.get)
        result = tudn.enrich(build, matches, fetch)
        self.assertEqual((result["confirmed"], fetch.call_count), (0, 2))

    def test_request_budget_is_shared_by_discovered_and_candidate_pages(self):
        pages, matches = tudn_capture()
        with patch.object(tudn, "MAX_MATCH_REQUESTS", 1):
            result = tudn.enrich(build, matches, pages.get)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["diagnostics"]["reasons"]["request_budget_exhausted"], 8)


class FOXTelevisionDiscovery(unittest.TestCase):
    def setUp(self):
        self.scores = (FIXTURES / "fox-scoreboard-2026-10-16.html").read_bytes()
        self.payload = json.loads((FIXTURES / "fox-tv-20261016.json").read_bytes())
        self.game = match("Necaxa", "Atlas", "2026-10-17T01:00:00Z")

    def fetch(self, url):
        if url == fox.TV_URL: return CONFIG
        if url == fox.SCORES_URL.format(day="2026-10-16"): return self.scores
        if url.startswith(fox.TV_API + "?"):
            self.assertEqual(parse_qs(urlsplit(url).query)["date"], ["20261016"])
            return json.dumps(self.payload).encode()
        return None

    def test_actual_tv_schedule_recovers_fox_deportes_without_assuming_main_plan(self):
        result = fox.enrich(build, [self.game], self.fetch)
        self.assertEqual((result["requested"], result["confirmed"], result["failed"]), (3, 1, 0))
        self.assertEqual([outlet.label for outlet in self.game.outlets], ["Fox Deportes"])
        self.assertEqual(build.evaluate(self.game.outlets, None, {"yttv"}), ("", "none", ""))
        self.assertEqual(build.evaluate(self.game.outlets, None, {"yttvspanishplus"})[1], "listed")
        self.assertEqual(self.game.broadcast_evidence[0]["raw_channels"], ["FOXDEP"])
        self.assertNotIn("fixturePublicClientKeyOnly123", json.dumps(result))

    def test_secondary_unknown_and_foreign_codes_are_observations_only(self):
        for channels in (["TUDN"], ["ViX+"], ["NBCU"], ["TSN", "RDS"], ["FUBO"], ["FOXDEP Mexico"]):
            with self.subTest(channels=channels):
                self.payload["events"][0]["tvStations"] = channels
                result = fox.enrich(build, [self.game], self.fetch)
                self.assertEqual(result["confirmed"], 0)
                self.assertEqual(self.game.outlets, [])
                self.assertEqual(result["per_match"][self.game.id]["observed_channels"], channels)

    def test_current_us_outlets_survive_and_explicit_simulcasts_merge(self):
        self.game.outlets = [build.map_outlet("ViX", "mex.1")]
        self.payload["events"][0]["tvStations"] = ["FS1", "FOXDEP", "FS1", "NBCU"]
        result = fox.enrich(build, [self.game], self.fetch)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual({outlet.label for outlet in self.game.outlets}, {"ViX", "FS1", "Fox Deportes"})

    def test_five_minute_airtime_difference_is_recorded_without_changing_kickoff(self):
        self.game.utc += timedelta(minutes=5)
        before = self.game.utc
        result = fox.enrich(build, [self.game], self.fetch)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(self.game.utc, before)
        self.assertEqual(self.game.broadcast_evidence[0]["kickoff_difference_seconds"], -300)

    def test_broadcast_time_tolerance_does_not_accept_another_game_or_replay(self):
        for case in ("late", "other_day", "replay", "replay_flag", "id", "home", "away", "league", "duplicate", "foreign_url"):
            payload = copy.deepcopy(self.payload)
            event = self.payload["events"][0]
            if case == "late": event["eventTime"] = "2026-10-17T01:16:00Z"
            if case == "other_day": event["eventTime"] = "2026-10-18T01:00:00Z"
            if case == "replay": event["title"] += " Replay"
            if case == "replay_flag": event["isReplay"] = True
            if case == "id": event["entityLink"]["layout"]["tokens"]["id"] = "99999"
            if case in ("home", "away"): event["entityLink"]["layout"]["tokens"][case + "Uri"] = "soccer/all/teams/999"
            if case == "league": event["filterValue"]["eventFilter"] = "Soccer - Liga MX Women"
            if case == "duplicate": self.payload["events"].append(copy.deepcopy(event))
            if case == "foreign_url": event["entityLink"]["webUrl"] = "https://evil.example/game"
            with self.subTest(case=case):
                self.assertEqual(fox.enrich(build, [self.game], self.fetch)["confirmed"], 0)
                self.assertEqual(self.game.outlets, [])
            self.payload = payload

    def test_ignored_date_selector_is_rejected(self):
        self.payload["filters"][0]["items"][0]["parameters"]["date"] = "2026-10-15"
        result = fox.enrich(build, [self.game], self.fetch)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["per_match"][self.game.id]["status"], "unavailable")

    def test_same_clubs_with_competing_fixture_ids_are_ambiguous(self):
        other = copy.deepcopy(self.game); other.id = "other"
        for offset in (0, 10):
            self.game.utc -= timedelta(minutes=offset)
            other.utc += timedelta(minutes=offset)
            result = fox.enrich(build, [self.game, other], self.fetch)
            self.assertEqual(result["confirmed"], 0)
            self.assertEqual(result["diagnostics"]["reasons"]["ambiguous_event"], 2)

    def test_runtime_config_can_only_select_the_known_public_api(self):
        self.assertEqual(fox.schedule_api_key(CONFIG), "fixturePublicClientKeyOnly123")
        for source in (CONFIG + CONFIG, CONFIG.replace(b"api.foxsports.com", b"evil.example"), b"no config"):
            with self.assertRaises(ValueError): fox.schedule_api_key(source)


if __name__ == "__main__":
    unittest.main()
