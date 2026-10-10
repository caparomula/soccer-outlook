"""A named live broadcast is evidence; league branding and replays are not."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

import build
import paramount_listings as source
from tests.test_discovery import fixture


US_PAGE = b"<script>CBS.Registry.region = {prefix: '', locale: 'en-us', property: 'US', international: false};</script>"


def match(**changes):
    game = fixture(league="sco.1", channel="", kickoff="2026-10-11T11:00:00+00:00")
    game.home.name, game.away.name = "Motherwell", "Celtic"
    game.rule = build.map_outlet("Paramount+", "sco.1")
    game.service, game.basis, game.outlet = build.evaluate([], game.rule, build.OWNER)
    game.hint = "Usual coverage is not a match listing."
    return replace(game, **changes)


def item(**changes):
    # Field names and airtime are from the public US sports carousel, 10 October 2026.
    value = {"title": "Motherwell vs. Celtic", "channelSlug": "scottish-professional-football-league",
             "startTimestamp": 1791716100000, "streamStartTimestamp": 1791714300000,
             "isLive": False, "isUpcoming": True, "contentType": "event", "streamType": "mpx_live",
             "href": "/shows/scottish-professional-football-league"}
    value.update(changes)
    return value


def page(items=None, total=None):
    items = [item()] if items is None else items
    return json.dumps({"success": True, "result": {"data": items,
                                                 "total": len(items) if total is None else total}}).encode()


def fetcher(items=None):
    return Mock(side_effect=[US_PAGE, page(items)])


def exact_item(**changes):
    game = {"sportName": "SOCCER", "scheduledTime": "2026-10-11T11:00:00Z",
            "homeTeam": {"mediumName": "Motherwell"}, "awayTeam": {"mediumName": "Celtic"}}
    return item(gameData=game, gameStartTimestamp=1791716400000, **changes)


class ParamountListings(unittest.TestCase):
    def test_every_usual_paramount_competition_has_an_explicit_source_policy(self):
        configured = {league for league, rights in build.RIGHTS.leagues.items()
                      if rights.usual and rights.usual.channel == "Paramount+"}
        self.assertEqual(configured, set(source.LEAGUE_SLUGS) | source.UNVERIFIED_LEAGUES)
        self.assertFalse(set(source.LEAGUE_SLUGS) & source.UNVERIFIED_LEAGUES)

    def test_public_live_listing_confirms_service_without_changing_kickoff(self):
        game = match()
        fetch = fetcher()
        report = source.enrich(build, [game], fetch)
        self.assertEqual((report["requested"], report["confirmed"], report["unmatched"], report["failed"]),
                         (2, 1, 0, 0))
        self.assertEqual(fetch.call_args_list[0].args[0], source.SCHEDULE_URL)
        self.assertEqual(fetch.call_args_list[1].args[0], source.EVENTS_URL.format(offset=0))
        self.assertEqual(game.utc, datetime(2026, 10, 11, 11, tzinfo=timezone.utc))
        self.assertEqual([outlet.label for outlet in game.outlets], ["Paramount+"])
        self.assertEqual((game.service, game.basis, game.outlet), ("para", "listed", "Paramount+"))
        self.assertIsNone(game.rule)
        self.assertEqual(game.hint, "")
        self.assertEqual(game.broadcast_source, "Paramount+")
        self.assertEqual(game.broadcast_url, source.SCHEDULE_URL)
        self.assertEqual(report["per_match"][game.id]["status"], "confirmed")

    def test_airtime_allows_only_same_day_zero_to_fifteen_minutes_of_pregame(self):
        for delta, expected in [(0, 1), (1, 1), (900, 1), (901, 0), (-1, 0)]:
            with self.subTest(delta=delta):
                data = item(startTimestamp=1791716400000 - delta * 1000)
                self.assertEqual(source.enrich(build, [match()], fetcher([data]))["confirmed"], expected)
        midnight = match(utc=datetime(2026, 10, 11, 4, tzinfo=timezone.utc))
        prior_day = item(startTimestamp=int(midnight.utc.timestamp() * 1000) - 300000)
        self.assertEqual(source.enrich(build, [midnight], fetcher([prior_day]))["confirmed"], 0)

    def test_exact_game_kickoff_is_used_when_present_without_airtime_fallback(self):
        self.assertEqual(source.enrich(build, [match()], fetcher([exact_item()]))["confirmed"], 1)
        for change in ("time", "sport", "home", "timestamp", "invalid", "no_game"):
            data = exact_item()
            if change == "time":
                data["gameData"]["scheduledTime"] = "2026-10-11T11:01:00Z"
                data["gameStartTimestamp"] += 60000
            elif change == "sport": data["gameData"]["sportName"] = "RUGBY"
            elif change == "home": data["gameData"]["homeTeam"]["mediumName"] = "Another club"
            elif change == "timestamp": data["gameStartTimestamp"] += 60000
            elif change == "invalid": data["gameData"]["scheduledTime"] = "2026-10-11T11:00:00"
            elif change == "no_game": data["gameData"] = None
            with self.subTest(change=change):
                self.assertEqual(source.enrich(build, [match()], fetcher([data]))["confirmed"], 0)

    def test_replays_generic_show_pages_wrong_teams_and_other_leagues_do_not_confirm(self):
        for changes in ({"contentType": "show"}, {"streamType": "vod"}, {"isUpcoming": False},
                        {"title": "Motherwell vs. Celtic Highlights"}, {"title": "Celtic vs. Motherwell"},
                        {"title": "Motherwell vs. Another club"}, {"channelSlug": "uefa-champions-league"},
                        {"startTimestamp": True}, {"href": "https://evil.example/match"},
                        {"href": 0}):
            with self.subTest(changes=changes):
                self.assertEqual(source.enrich(build, [match()], fetcher([item(**changes)]))["confirmed"], 0)

    def test_duplicates_cannot_confirm(self):
        for duplicate in (item(), item(href="invalid"), item(startTimestamp=1791716700000),
                          item(gameData={"scheduledTime": "2026-10-11T11:01:00Z"})):
            with self.subTest(duplicate=duplicate):
                self.assertEqual(source.enrich(build, [match()], fetcher([item(), duplicate]))["confirmed"], 0)

    def test_unmapped_competition_is_unavailable_rather_than_claiming_absence(self):
        game = match(league="new.league")
        report = source.enrich(build, [game], fetcher([]))
        self.assertEqual(report["per_match"][game.id]["status"], "unavailable")
        self.assertEqual(report["confirmed"], 0)

    def test_empty_successful_schedule_is_unlisted_not_unavailable(self):
        game = match()
        report = source.enrich(build, [game], fetcher([]))
        self.assertEqual((report["failed"], report["unmatched"]), (0, 1))
        self.assertEqual(report["per_match"][game.id]["status"], "unlisted")
        self.assertEqual(game.rule.label, "Paramount+")

    def test_pagination_fetches_all_pages_before_confirming(self):
        fetch = Mock(side_effect=[US_PAGE, page([item()], total=2), page([item(title="Other vs. Team")], total=2)])
        report = source.enrich(build, [match()], fetch)
        self.assertEqual((report["requested"], report["confirmed"]), (3, 1))
        self.assertEqual(fetch.call_args_list[-1].args[0], source.EVENTS_URL.format(offset=1))

    def test_failed_partial_changing_or_unbounded_schedule_preserves_uncertainty(self):
        cases = [[US_PAGE, None], [US_PAGE, b"{}"], [US_PAGE, b"\xff"],
                 [US_PAGE, page([], total=1001)], [US_PAGE, page([], total=True)],
                 [US_PAGE, page([item()], total=2), None],
                 [US_PAGE, page([item()], total=2), page([], total=3)],
                 [US_PAGE, page([], total=2)], [US_PAGE, page([None])],
                 [US_PAGE, b"x" * (source.MAX_PAGE_BYTES + 1)], [OSError("offline")]]
        for responses in cases:
            with self.subTest(count=len(responses)):
                game = match()
                original = deepcopy((game.rule, game.hint, game.service, game.basis, game.outlet))
                report = source.enrich(build, [game], Mock(side_effect=responses))
                self.assertEqual((report["confirmed"], report["failed"], report["unmatched"]), (0, 1, 1))
                self.assertEqual(report["per_match"][game.id]["status"], "unavailable")
                self.assertEqual((game.rule, game.hint, game.service, game.basis, game.outlet), original)

    def test_only_public_us_catalogue_is_used(self):
        for data in (US_PAGE.replace(b"'US'", b"'CA'"), US_PAGE.replace(b"false", b"true"),
                     US_PAGE.replace(b"prefix: ''", b"prefix: '/gb'"), b"", US_PAGE * 2):
            fetch = Mock(return_value=data)
            self.assertEqual(source.enrich(build, [match()], fetch)["failed"], 1)
            fetch.assert_called_once_with(source.SCHEDULE_URL)

    def test_no_fetch_for_ineligible_matches_or_existing_listings(self):
        games = [match(outlets=[build.map_outlet("Paramount+", "sco.1")]), match(rule=None),
                 match(rule=build.map_outlet("ESPN+", "sco.1")), match(time_valid=False),
                 match(state="post"), match(status="Postponed"), match(utc=datetime(2026, 10, 11, 11))]
        fetch = Mock()
        report = source.enrich(build, games, fetch)
        self.assertEqual(report["requested"], 0)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
