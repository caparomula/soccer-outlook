"""An optional broadcaster source may confirm a fixture, but must never invent its coverage."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import build
import fox_listings
from tests.test_discovery import fixture


SAMPLE = (Path(__file__).parent / "fixtures" / "fox-liga-mx.html").read_bytes()


def match(**changes):
    game = fixture(league="mex.1", channel="", kickoff="2026-10-10T23:00:00+00:00")
    game.home.name = "FC Juárez"
    game.away.name = "Tijuana"
    game.rule = build.map_outlet("FOX Sports networks", "mex.1")
    game.service, game.basis, game.outlet = build.evaluate([], game.rule, build.OWNER)
    game.hint = "A usual broadcaster is not a match listing."
    return replace(game, **changes)


def payload():
    parser = fox_listings._Payload()
    parser.feed(SAMPLE.decode())
    return json.loads("".join(parser.parts))


def page(table):
    return ('<footer>FS1 FOX Fox Deportes</footer><script type="application/json" '
            'id="__NUXT_DATA__">' + json.dumps(table) + '</script>').encode()


class FOXListings(unittest.TestCase):
    def test_recorded_event_confirms_exact_channel_and_existing_service_routes(self):
        game = match()
        fetch = Mock(return_value=SAMPLE)
        report = fox_listings.enrich(build, [game], fetch)
        self.assertEqual({key: report[key] for key in ("requested", "confirmed", "unmatched", "failed")},
                         {"requested": 1, "confirmed": 1, "unmatched": 0, "failed": 0})
        fetch.assert_called_once_with("https://www.foxsports.com/soccer/liga-mx/scores?date=2026-10-10")
        self.assertEqual([outlet.label for outlet in game.outlets], ["FS2"])
        self.assertIsNone(game.rule)
        self.assertEqual(game.hint, "")
        self.assertEqual(game.broadcast_source, "FOX Sports")
        self.assertEqual(game.broadcast_url,
                         "https://www.foxsports.com/soccer/liga-mx-fc-juarez-vs-tijuana-oct-10-2026-game-boxscore-878141")
        self.assertEqual(report["per_match"][game.id], {"status": "confirmed", "url": game.broadcast_url})
        self.assertEqual(build.evaluate(game.outlets, game.rule, {"fox"}), ("fox", "listed", "FS2"))
        self.assertEqual(build.evaluate(game.outlets, game.rule, {"fubo"}), ("fubo", "listed", "FS2"))

    def test_absent_or_unsupported_event_channel_is_not_replaced_by_footer_networks(self):
        for channel in ("", "FOX Sports networks", "TUDN", "FS2 or FS1", ["FS2"], None):
            with self.subTest(channel=channel):
                table = payload()
                table[table[0]["tvStation"]] = channel
                game = match()
                report = fox_listings.enrich(build, [game], lambda _: page(table))
                self.assertEqual(report["confirmed"], 0)
                self.assertEqual(game.outlets, [])
                self.assertEqual(game.rule.label, "FOX Sports networks")
                self.assertEqual(report["per_match"][game.id],
                                 {"status": "unlisted", "url": fox_listings.SCORES_URL.format(day="2026-10-10")})

    def test_both_clubs_home_assignment_and_exact_aware_time_must_agree(self):
        cases = [
            ("eventTime", "2026-10-11T23:00:00Z"),
            ("eventTime", "2026-10-10T23:01:00Z"),
            ("eventTime", "2026-10-10T23:00:00"),
            ("eventTime", "invalid"),
            ("isTba", True),
            ("league", "MLS"),
            ("contentUri", "soccer/mls/events/878141"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                table = payload()
                table[table[0][key]] = value
                self.assertEqual(fox_listings.enrich(build, [match()], lambda _: page(table))["confirmed"], 0)
        for club in ("home", "away"):
            game = match()
            getattr(game, club).name = "Another club"
            self.assertEqual(fox_listings.enrich(build, [game], lambda _: SAMPLE)["confirmed"], 0)
        game = match()
        game.home, game.away = game.away, game.home
        self.assertEqual(fox_listings.enrich(build, [game], lambda _: SAMPLE)["confirmed"], 0)

    def test_offset_timestamp_matches_same_instant(self):
        table = payload()
        table[table[0]["eventTime"]] = "2026-10-10T19:00:00-04:00"
        self.assertEqual(fox_listings.enrich(build, [match()], lambda _: page(table))["confirmed"], 1)

    def test_duplicate_or_conflicting_event_cannot_confirm_coverage(self):
        for channel in ("FS2", "FS1", "unsupported", None):
            with self.subTest(channel=channel):
                table = payload()
                duplicate = dict(table[0])
                duplicate["tvStation"] = len(table)
                table.extend([channel, duplicate])
                report = fox_listings.enrich(build, [match()], lambda _: page(table))
                self.assertEqual((report["confirmed"], report["unmatched"]), (0, 1))

    def test_only_direct_official_matching_event_links_are_accepted(self):
        for url in ("https://evil.example/soccer/game-boxscore-878141",
                    "//evil.example/soccer/game-boxscore-878141", "javascript:alert(1)",
                    "https://www.foxsports.com.evil.example/soccer/game-boxscore-878141",
                    "/soccer/game-boxscore-999", "/soccer/game-boxscore-878141?redirect=elsewhere"):
            with self.subTest(url=url):
                table = payload()
                entity = table[table[0]["entityLink"]]
                table[entity["webUrl"]] = url
                self.assertEqual(fox_listings.enrich(build, [match()], lambda _: page(table))["confirmed"], 0)

    def test_no_fetch_for_espn_listed_or_ineligible_matches(self):
        games = [
            match(outlets=[build.map_outlet("FS1", "mex.1")]),
            match(league="eng.1"), match(rule=None), match(rule=build.map_outlet("ViX", "mex.1")),
            match(time_valid=False), match(state="post"), match(status="Postponed"),
            match(utc=datetime(2026, 10, 10, 23)),
        ]
        fetch = Mock(return_value=SAMPLE)
        self.assertEqual(fox_listings.enrich(build, games, fetch),
                         {"requested": 0, "confirmed": 0, "unmatched": 0, "failed": 0, "per_match": {}})
        fetch.assert_not_called()
        self.assertEqual(games[0].outlets[0].label, "FS1")

    def test_fetch_once_per_needed_eastern_day_including_after_utc_midnight(self):
        games = [match(), match(utc=datetime(2026, 10, 11, 1, tzinfo=timezone.utc)),
                 match(utc=datetime(2026, 10, 11, 23, tzinfo=timezone.utc))]
        fetch = Mock(return_value=SAMPLE)
        report = fox_listings.enrich(build, games, fetch)
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         [fox_listings.SCORES_URL.format(day="2026-10-10"),
                          fox_listings.SCORES_URL.format(day="2026-10-11")])
        self.assertEqual((report["requested"], report["confirmed"], report["unmatched"]), (2, 1, 2))

    def test_unavailable_malformed_or_unbounded_source_preserves_original_listing(self):
        bad_pages = [None, b"unavailable", b"\xff", b"<![spam]>", page({}), page([None] * 20_001),
                     b"x" * (fox_listings.MAX_PAGE_BYTES + 1), SAMPLE + SAMPLE,
                     b'<script type="application/json" id="__NUXT_DATA__">invalid</script>']
        for value in bad_pages:
            with self.subTest(value_type=type(value).__name__, size=len(value or b"")):
                game = match()
                original = (game.rule, game.hint, game.service, game.basis, game.outlet)
                report = fox_listings.enrich(build, [game], lambda _: value)
                self.assertEqual((report["confirmed"], report["failed"], report["unmatched"]), (0, 1, 1))
                self.assertEqual((game.rule, game.hint, game.service, game.basis, game.outlet), original)
                self.assertEqual(report["per_match"][game.id],
                                 {"status": "unavailable", "url": fox_listings.SCORES_URL.format(day="2026-10-10")})
        report = fox_listings.enrich(build, [match()], Mock(side_effect=OSError("offline")))
        self.assertEqual(report["failed"], 1)

    def test_invalid_references_do_not_use_python_negative_or_boolean_indexes(self):
        for bad_ref in (-1, True, 999999, "FS2", None):
            with self.subTest(reference=bad_ref):
                table = payload()
                table[0]["tvStation"] = bad_ref
                self.assertEqual(fox_listings.enrich(build, [match()], lambda _: page(table))["confirmed"], 0)


if __name__ == "__main__":
    unittest.main()
