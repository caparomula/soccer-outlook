"""Public broadcaster listings need exact identity, US availability and a live event."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock

import build
import peacock_listings
import vix_listings
from tests.test_discovery import fixture


FIXTURES = Path(__file__).parent / "fixtures"
VIX_SAMPLE = (FIXTURES / "vix-liga-mx.html").read_bytes()
PEACOCK_SAMPLE = (FIXTURES / "peacock-sports.html").read_bytes()


def match(provider="ViX", **changes):
    game = fixture(league="mex.1", channel="", kickoff="2026-10-10T23:00:00+00:00")
    game.home.name = "Querétaro" if provider == "ViX" else "Guadalajara"
    game.away.name = "Atlante"
    game.rule = build.map_outlet(provider, "mex.1")
    game.service, game.basis, game.outlet = build.evaluate([], game.rule, build.OWNER)
    return replace(game, **changes)


def payload(sample):
    return json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                                sample.decode(), flags=re.S).group(1))


def page(data, links=""):
    return (links + '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>').encode()


def vix_page(data):
    links = "".join(re.findall(r'<a href="[^"]+">.*?</a>', VIX_SAMPLE.decode()))
    return page(data, links)


def peacock_case():
    """Synthetic Chivas event using the recorded public live-sports schema, not a real listing."""
    data = payload(PEACOCK_SAMPLE)
    assets = next(iter(data["props"]["apolloState"]["data"]["ROOT_QUERY"].values()))["assets"]
    event = assets[0]
    event.update(title="Chivas vs. Atlante (Español)", genres=["Liga MX", "Soccer"],
                 url="/sports/chivas-vs.-atlante/12345678-1234-1234-1234-123456789abc")
    event["eventDetails"]["eventDisplayStartDate"] = 1791673200000
    event["displayStartTime"] = 1791673200000
    return data, event, assets


class ViXListings(unittest.TestCase):
    def test_recorded_us_live_events_confirm_exact_kickoff_not_pregame(self):
        game = match()
        fetch = Mock(return_value=VIX_SAMPLE)
        report = vix_listings.enrich(build, [game], fetch)
        fetch.assert_called_once_with(vix_listings.SCHEDULE_URL)
        self.assertEqual(report["confirmed"], 1)
        self.assertEqual([outlet.label for outlet in game.outlets], ["ViX"])
        self.assertEqual(game.broadcast_source, "ViX")
        self.assertEqual(game.broadcast_url, "https://vix.com/live/transmission-matchid-2641263")
        self.assertEqual(build.evaluate(game.outlets, game.rule, {"vix"}), ("vix", "listed", "ViX"))
        self.assertEqual(report["per_match"][game.id], {"status": "confirmed", "url": game.broadcast_url})
        pregame = match(utc=match().utc - timedelta(minutes=15))
        self.assertEqual(vix_listings.enrich(build, [pregame], lambda _: VIX_SAMPLE)["confirmed"], 0)

    def test_recorded_chivas_and_santos_aliases_only(self):
        for home, away, hours in (("Atlas", "Guadalajara", 2), ("Atlético de San Luis", "Santos", 24)):
            game = match(utc=match().utc + timedelta(hours=hours))
            game.home.name, game.away.name = home, away
            self.assertEqual(vix_listings.enrich(build, [game], lambda _: VIX_SAMPLE)["confirmed"], 1)

    def test_both_clubs_home_assignment_and_full_time_must_agree(self):
        for target in ("home", "away"):
            game = match()
            getattr(game, target).name = "Other team"
            self.assertEqual(vix_listings.enrich(build, [game], lambda _: VIX_SAMPLE)["confirmed"], 0)
        game = match(); game.home, game.away = game.away, game.home
        self.assertEqual(vix_listings.enrich(build, [game], lambda _: VIX_SAMPLE)["confirmed"], 0)
        game = match(utc=match().utc + timedelta(days=1))
        self.assertEqual(vix_listings.enrich(build, [game], lambda _: VIX_SAMPLE)["confirmed"], 0)

    def test_country_naive_time_replay_other_league_and_missing_live_link_do_not_confirm(self):
        for case in ("country", "naive", "replay", "league", "link", "badge"):
            with self.subTest(case=case):
                data = payload(VIX_SAMPLE)
                table = data["props"]["apolloState"]
                event = table["SportsEvent:transmission:matchid:2641263"]
                if case == "country": data["props"]["initialState"]["requestCountryCode"] = "MX"
                if case == "naive": event["playbackData"]["kickoffDate"] = "2026-10-10T23:00:00"
                if case == "replay": event["playbackData"]["__typename"] = "VideoPlaybackData"
                if case == "league": table["SportsTournament:199"]["name"] = "Liga MX Women"
                if case == "badge": event["badges"] = []
                response = page(data) if case == "link" else vix_page(data)
                game = match()
                self.assertEqual(vix_listings.enrich(build, [game], lambda _: response)["confirmed"], 0)
                self.assertEqual(game.rule.label, "ViX")

    def test_conflicting_duplicate_without_public_link_still_blocks_confirmation(self):
        data = payload(VIX_SAMPLE)
        table = data["props"]["apolloState"]
        duplicate = copy.deepcopy(table["SportsEvent:transmission:matchid:2641263"])
        duplicate["id"] = "transmission:matchid:99999"
        table["SportsEvent:transmission:matchid:99999"] = duplicate
        self.assertEqual(vix_listings.enrich(build, [match()], lambda _: vix_page(data))["confirmed"], 0)


class PeacockListings(unittest.TestCase):
    def test_recorded_page_does_not_turn_premier_league_event_into_liga_mx(self):
        self.assertEqual(peacock_listings.parse_schedule(PEACOCK_SAMPLE), [])
        game = match("Peacock")
        result = peacock_listings.enrich(build, [game], lambda _: PEACOCK_SAMPLE)
        self.assertEqual(result["per_match"][game.id], {"status": "unlisted", "url": peacock_listings.SCHEDULE_URL})

    def test_exact_live_direct_to_consumer_event_confirms_only_peacock(self):
        data, _, _ = peacock_case()
        game = match("Peacock")
        fetch = Mock(return_value=page(data))
        result = peacock_listings.enrich(build, [game], fetch)
        fetch.assert_called_once_with(peacock_listings.SCHEDULE_URL)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual([outlet.label for outlet in game.outlets], ["Peacock"])
        self.assertEqual(build.evaluate(game.outlets, game.rule, {"peacock"}), ("peacock", "listed", "Peacock"))

    def test_replay_unavailable_wrong_sport_time_team_and_untrusted_url_do_not_confirm(self):
        cases = ["replay", "vod", "unavailable", "not_d2c", "sport", "league", "time", "pregame",
                 "team", "external", "script", "query"]
        for case in cases:
            with self.subTest(case=case):
                data, event, _ = peacock_case()
                if case == "replay": event["eventDetails"]["eventStage"] = "REPLAY"
                if case == "vod": event["type"] = "ASSET/VIDEO"
                if case == "unavailable": event["available"] = False
                if case == "not_d2c": event["contentSegments"] = ["TVE"]
                if case == "sport": event["genres"] = ["Liga MX", "Football"]
                if case == "league": event["genres"] = ["Premier League", "Soccer"]
                if case == "time": event["eventDetails"]["eventDisplayStartDate"] += 60000
                if case == "pregame":
                    event["eventDetails"]["eventDisplayStartDate"] -= 900000
                    event["displayStartTime"] -= 900000
                if case == "team": event["title"] = "Chivas vs. Other team"
                if case == "external": event["url"] = "https://example.com/sports/chivas"
                if case == "script": event["url"] = "javascript:alert(1)"
                if case == "query": event["url"] += "?redirect=elsewhere"
                self.assertEqual(peacock_listings.enrich(build, [match("Peacock")], lambda _: page(data))["confirmed"], 0)

    def test_duplicate_unavailable_event_still_blocks_confirmation(self):
        data, event, assets = peacock_case()
        duplicate = copy.deepcopy(event)
        duplicate["available"] = False
        assets.append(duplicate)
        self.assertEqual(peacock_listings.enrich(build, [match("Peacock")], lambda _: page(data))["confirmed"], 0)


class OptionalListingFailures(unittest.TestCase):
    def test_missing_or_malformed_public_schedule_keeps_rights_and_reports_unavailable(self):
        for provider, module in (("ViX", vix_listings), ("Peacock", peacock_listings)):
            for response in (None, b"blocked", b"\xff", page([]), page({"props": []}),
                             page({}) + page({}), b"x" * (module.MAX_PAGE_BYTES + 1)):
                with self.subTest(provider=provider, response_type=type(response)):
                    game = match(provider)
                    report = module.enrich(build, [game], lambda _: response)
                    self.assertEqual(report["failed"], 1)
                    self.assertEqual(report["confirmed"], 0)
                    self.assertEqual(game.rule.label, provider)
                    self.assertEqual(report["per_match"][game.id]["status"], "unavailable")
            fetch = Mock(side_effect=OSError("offline"))
            self.assertEqual(module.enrich(build, [match(provider)], fetch)["failed"], 1)

    def test_no_request_for_non_candidates_and_confirmed_outlets_are_untouched(self):
        for provider, module in (("ViX", vix_listings), ("Peacock", peacock_listings)):
            for changes in ({"state": "post"}, {"time_valid": False}, {"status": "postponed"},
                            {"rule": None}, {"league": "usa.1"},
                            {"outlets": [build.map_outlet("ESPN+", "mex.1")]}):
                with self.subTest(provider=provider, changes=changes):
                    fetch = Mock()
                    game = match(provider, **changes)
                    self.assertEqual(module.enrich(build, [game], fetch)["requested"], 0)
                    fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
