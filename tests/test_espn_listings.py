"""Match-specific Watch listings supplement ESPN scoreboards without asserting league-wide rights."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import build
import espn_listings as source
from tests.test_discovery import fixture


SAMPLE = (Path(__file__).parent / "fixtures" / "espn-watch-schedule.html").read_bytes()


def payload():
    return json.JSONDecoder().raw_decode(SAMPLE.decode().split(source.PAYLOAD)[1])[0]


def page(data):
    return ('<script>' + source.PAYLOAD + json.dumps(data) + ';</script>').encode()


def event(data):
    return data["page"]["content"]["watch"]["arngs"][0]["sctgys"][0]["arngs"][0]


def match(**changes):
    game = fixture(league="usa.usl.1", channel="", kickoff="2026-10-11T01:00:00+00:00")
    game.home.name, game.away.name = "El Paso Locomotive FC", "Orange County SC"
    game.rule = build.map_outlet("ESPN+", game.league)
    game.service, game.basis, game.outlet = build.evaluate([], game.rule, build.OWNER)
    return replace(game, **changes)


class WatchESPNListings(unittest.TestCase):
    def test_recorded_us_airing_confirms_explicit_network(self):
        game = match()
        fetch = Mock(return_value=SAMPLE)
        report = source.enrich(build, [game], fetch)
        self.assertEqual(report["confirmed"], 1)
        fetch.assert_called_once_with(source.DATED_URL.format(kind="upcoming", day="20261011"))
        self.assertEqual([outlet.label for outlet in game.outlets], ["ESPN+"])
        self.assertIsNone(game.rule)
        self.assertEqual(game.broadcast_source, "Watch ESPN")
        self.assertEqual(game.broadcast_url, "https://www.espn.com/watch/player/_/id/4c56bbec-c7ea-471d-a225-87b7cdcc3fd2")
        self.assertEqual(report["per_match"][game.id], {"status": "confirmed", "url": game.broadcast_url})
        self.assertEqual(build.evaluate(game.outlets, None, {"espnplus"}), ("espnplus", "listed", "ESPN+"))

    def test_both_teams_competition_start_and_live_or_upcoming_type_must_match(self):
        cases = [("nme", "El Paso Locomotive FC vs. Another Team"),
                 ("nme", "Highlights: El Paso Locomotive FC vs. Orange County SC"),
                 ("stme", "2026-10-11T01:01:00Z"), ("stme", "2026-10-12T01:00:00Z"),
                 ("stme", "2026-10-11T01:00:00"), ("stme", None),
                 ("tp", "replay"), ("tp", "clip"), ("sctgys", [{"name": "USL League One"}]),
                 ("sctgys", [{"name": []}]), ("ctgys", [{"id": "another-sport"}])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = payload()
                event(data)[key] = value
                report = source.enrich(build, [match()], lambda _: page(data))
                self.assertEqual((report["confirmed"], report["unmatched"]), (0, 1))
        self.assertEqual(source.enrich(build, [match(league="usa.usl.l1")], lambda _: SAMPLE)["confirmed"], 0)

    def test_versus_title_can_reverse_club_order_but_never_guess_names(self):
        data = payload()
        event(data)["nme"] = "Orange County SC vs. El Paso Locomotive FC"
        event(data)["stme"] = "2026-10-10T21:00:00-04:00"
        self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 1)
        event(data)["nme"] = "Orange County vs. El Paso"
        self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)

    def test_network_must_be_event_local_and_known(self):
        for broadcasts in ([], None, [{"nme": "usually ESPN+"}], [{"nme": "ESPN+ or CBS"}], [{"nme": []}]):
            data = payload()
            event(data)["bcsts"] = broadcasts
            report = source.enrich(build, [match()], lambda _: page(data) + b"<footer>ESPN+</footer>")
            self.assertEqual(report["confirmed"], 0)
        data = payload()
        event(data)["bcsts"] = [{"nme": "ESPN2"}, {"nme": "ESPN Deportes"}]
        game = match()
        source.enrich(build, [game], lambda _: page(data))
        self.assertEqual({o.label for o in game.outlets}, {"ESPN2", "ESPN Deportes"})

    def test_player_link_must_be_official_and_match_the_airing_id(self):
        for url in ("https://evil.example/player", "//evil.example/player", "javascript:alert(1)",
                    "/watch/player/_/id/another-event", event(payload())["hrf"] + "?redirect=evil"):
            data = payload()
            event(data)["hrf"] = url
            self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)

    def test_duplicate_airings_remain_ambiguous_even_if_one_has_bad_channel(self):
        for channel in ("ESPN+", "ESPN2", "unknown"):
            data = payload()
            duplicate = deepcopy(event(data))
            duplicate["bcsts"] = [{"nme": channel}]
            data["page"]["content"]["watch"]["arngs"][0]["sctgys"][0]["arngs"].append(duplicate)
            self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)

    def test_public_us_schedule_is_required_and_failures_preserve_uncertainty(self):
        bad = [None, b"unavailable", b"\xff", SAMPLE + SAMPLE, b"x" * (source.MAX_PAGE_BYTES + 1), page([])]
        for path, value in (("p13n", {"countryCode": "gb"}), ("p13n", None), ("arngs", None)):
            data = payload()
            data["page"]["content"]["watch"][path] = value
            bad.append(page(data))
        for body in bad:
            game = match()
            report = source.enrich(build, [game], lambda _: body)
            self.assertEqual((report["failed"], report["unmatched"], report["confirmed"]), (1, 1, 0))
            self.assertEqual(report["per_match"][game.id]["status"], "unavailable")
            self.assertEqual(game.rule.label, "ESPN+")
        fetch = Mock(side_effect=TimeoutError)
        self.assertEqual(source.enrich(build, [match()], fetch)["failed"], 1)

    def test_absence_is_unlisted_not_an_unavailable_source(self):
        data = payload()
        data["page"]["content"]["watch"]["arngs"] = []
        game = match()
        report = source.enrich(build, [game], lambda _: page(data))
        self.assertEqual((report["failed"], report["unmatched"]), (0, 1))
        self.assertEqual(report["per_match"][game.id]["status"], "unlisted")

    def test_only_eligible_unlisted_matches_are_checked_once_per_date(self):
        games = [match(outlets=[build.map_outlet("ESPN+", "usa.usl.1")]), match(rule=None),
                 match(time_valid=False), match(state="post"), match(status="Postponed"),
                 match(league="eng.1"), match(utc=datetime(2026, 10, 11, 1))]
        fetch = Mock(return_value=SAMPLE)
        self.assertEqual(source.enrich(build, games, fetch)["requested"], 0)
        fetch.assert_not_called()
        games = [match(), match(id="second"), match(id="live", state="in")]
        report = source.enrich(build, games, fetch)
        self.assertEqual(report["requested"], 2)
        self.assertEqual({c.args[0] for c in fetch.call_args_list}, {
            source.DATED_URL.format(kind="upcoming", day="20261011"), source.SCHEDULE_URL.format(kind="live")})


if __name__ == "__main__":
    unittest.main()
