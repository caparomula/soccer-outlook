"""Match-specific Watch listings supplement ESPN scoreboards without asserting league-wide rights."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import build
import broadcast_listings
import espn_listings as source
from tests.test_discovery import fixture


SAMPLE = (Path(__file__).parent / "fixtures" / "espn-watch-schedule.html").read_bytes()
CURRENT = (Path(__file__).parent / "fixtures" / "espn-watch-20261017.html").read_bytes()


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
    def check_with_prior_us_listing(self, game, current_fetch, prior_page=SAMPLE):
        now = game.utc - timedelta(hours=1)
        prior_game = deepcopy(game)
        broadcast_listings.enrich(build, [prior_game], lambda _: prior_page, now - timedelta(hours=1))
        self.assertTrue(prior_game.outlets)
        record = asdict(prior_game)
        record["utc"] = prior_game.utc.isoformat()
        reports = broadcast_listings.enrich(build, [game], current_fetch, now)
        retained = broadcast_listings.retain_recent_evidence(build, [game], [record], now)
        return reports["ESPN+"], retained

    def test_recorded_us_airing_confirms_explicit_network(self):
        game = match()
        fetch = Mock(return_value=SAMPLE)
        report = source.enrich(build, [game], fetch)
        self.assertEqual(report["confirmed"], 1)
        fetch.assert_called_once_with(source.DATED_URL.format(kind="upcoming", day="20261010"))
        self.assertEqual([outlet.label for outlet in game.outlets], ["ESPN+"])
        self.assertIsNone(game.rule)
        self.assertEqual(game.broadcast_source, "Watch ESPN")
        self.assertEqual(game.broadcast_url, "https://www.espn.com/watch/player/_/id/4c56bbec-c7ea-471d-a225-87b7cdcc3fd2")
        self.assertEqual(report["per_match"][game.id]["status"], "confirmed")
        self.assertEqual(report["per_match"][game.id]["url"], game.broadcast_url)
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

    def test_contradictory_airing_ids_are_rejected_but_identical_copies_are_deduplicated(self):
        for channel in ("ESPN2", "unknown"):
            data = payload()
            duplicate = deepcopy(event(data))
            duplicate["bcsts"] = [{"nme": channel}]
            data["page"]["content"]["watch"]["arngs"][0]["sctgys"][0]["arngs"].append(duplicate)
            self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)

        for key, value in (("nme", "Another Team vs. Orange County SC"),
                           ("stme", "2026-10-12T01:00:00Z")):
            data = payload()
            duplicate = deepcopy(event(data))
            duplicate[key] = value
            data["page"]["content"]["watch"]["arngs"][0]["sctgys"][0]["arngs"].append(duplicate)
            self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)
        data = payload()
        data["page"]["content"]["watch"]["arngs"][0]["sctgys"][0]["arngs"].append(deepcopy(event(data)))
        report = source.enrich(build, [match()], lambda _: page(data))
        self.assertEqual(report["confirmed"], 1)

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

    def test_explicit_wrong_country_blocks_cached_us_evidence_but_outages_do_not(self):
        foreign = payload()
        foreign["page"]["content"]["watch"]["p13n"]["countryCode"] = "gb"
        with self.assertRaises(source.WrongCountryError):
            source.parse_schedule(page(foreign))
        missing_country = payload()
        missing_country["page"]["content"]["watch"]["p13n"] = {}
        for body, expected in ((page(foreign), 0), (None, 1), (b"unavailable", 1), (page(missing_country), 1)):
            with self.subTest(body=body[:30] if body else body):
                game = match()
                report, retained = self.check_with_prior_us_listing(game, lambda _: body)
                self.assertEqual(retained["retained_channels"], expected)
                self.assertEqual(bool(game.outlets), bool(expected))
                self.assertEqual(report["per_match"][game.id]["reason"],
                                 "unrecognized_or_unavailable_schedule" if expected else "wrong_country")

    def test_overlap_country_rejection_survives_other_failed_or_unlisted_pages(self):
        valid = payload()
        event(valid)["stme"] = "2026-10-11T03:55:00Z"
        foreign = deepcopy(valid)
        foreign["page"]["content"]["watch"]["p13n"]["countryCode"] = "mx"
        empty = deepcopy(valid)
        empty["page"]["content"]["watch"]["arngs"] = []
        invalid = deepcopy(valid)
        event(invalid)["hrf"] = "/watch/player/_/id/wrong-event"
        for other in (None, page(empty), page(invalid)):
            for country_index in (0, 1):
                with self.subTest(other=bool(other), country_index=country_index):
                    game = match(utc=datetime(2026, 10, 11, 4, 5, tzinfo=timezone.utc))
                    urls = source.candidate_urls(game)
                    fetch = lambda url: page(foreign) if url == urls[country_index] else other
                    report, retained = self.check_with_prior_us_listing(game, fetch, page(valid))
                    self.assertEqual(retained["retained_channels"], 0)
                    self.assertFalse(game.outlets)
                    self.assertEqual(report["per_match"][game.id],
                                     {"status": "unavailable", "reason": "wrong_country", "url": urls[country_index]})

    def test_overlap_can_still_confirm_a_separate_valid_us_listing(self):
        valid = payload()
        event(valid)["stme"] = "2026-10-11T03:55:00Z"
        foreign = deepcopy(valid)
        foreign["page"]["content"]["watch"]["p13n"]["countryCode"] = "gb"
        for country_index in (0, 1):
            game = match(utc=datetime(2026, 10, 11, 4, 5, tzinfo=timezone.utc))
            urls = source.candidate_urls(game)
            fetch = lambda url: page(foreign) if url == urls[country_index] else page(valid)
            report, retained = self.check_with_prior_us_listing(game, fetch, page(valid))
            self.assertEqual(report["per_match"][game.id]["status"], "confirmed")
            self.assertEqual(report["diagnostics"]["reasons"]["wrong_country"], 1)
            self.assertEqual(retained["retained_channels"], 0)
            self.assertEqual([outlet.label for outlet in game.outlets], ["ESPN+"])
            self.assertFalse(any(e.get("retained") for e in game.broadcast_evidence))

    def test_absence_is_unlisted_not_an_unavailable_source(self):
        data = payload()
        data["page"]["content"]["watch"]["arngs"] = []
        game = match()
        report = source.enrich(build, [game], lambda _: page(data))
        self.assertEqual((report["failed"], report["unmatched"]), (0, 1))
        self.assertEqual(report["per_match"][game.id]["status"], "unlisted")

    def test_only_eligible_matches_are_checked_once_per_eastern_date(self):
        games = [match(time_valid=False), match(state="post"), match(status="Postponed"),
                 match(league="eng.1"), match(utc=datetime(2026, 10, 11, 1))]
        fetch = Mock(return_value=SAMPLE)
        self.assertEqual(source.enrich(build, games, fetch)["requested"], 0)
        fetch.assert_not_called()
        games = [match(), match(id="second"), match(id="live", state="in")]
        report = source.enrich(build, games, fetch)
        self.assertEqual(report["requested"], 2)
        self.assertEqual({c.args[0] for c in fetch.call_args_list}, {
            source.DATED_URL.format(kind="upcoming", day="20261010"), source.SCHEDULE_URL.format(kind="live")})

    def test_current_recorded_aliases_affixes_and_simulcasts_preserve_channel_routes(self):
        game = fixture(league="esp.1", channel="", kickoff="2026-10-17T12:00:00+00:00")
        game.home.name, game.home.id = "Espanyol", "88"
        game.away.name, game.away.id = "Atlético Madrid", "1068"
        original_kickoff = game.utc
        report = source.enrich(build, [game], lambda _: CURRENT)
        self.assertEqual((report["confirmed"], report["unmatched"], report["failed"]), (1, 0, 0))
        self.assertEqual({o.label for o in game.outlets}, {"ESPN+", "ESPN Deportes"})
        self.assertEqual(game.utc, original_kickoff)
        self.assertEqual(len(game.broadcast_evidence), 3)
        self.assertEqual({e["broadcast_start"] for e in game.broadcast_evidence},
                         {"2026-10-17T11:50:00+00:00", "2026-10-17T11:55:00+00:00"})
        self.assertEqual(report["diagnostics"]["observed_listings"], 5)
        self.assertEqual(report["timezone"], "America/New_York")
        self.assertEqual(build.evaluate(game.outlets, None, {"yttvsportsplus"})[0], "")
        self.assertEqual(build.evaluate(game.outlets, None, {"yttv"})[0], "yttv")

    def test_alias_ids_are_competition_scoped_and_never_override_a_conflicting_id(self):
        game = fixture(league="ned.1", channel="", kickoff="2026-10-17T14:30:00+00:00")
        game.home.name, game.home.id = "ADO Den Haag", "2726"
        game.away.name, game.away.id = "PSV Eindhoven", "148"
        self.assertEqual(source.enrich(build, [game], lambda _: CURRENT)["confirmed"], 1)
        game.away.id = "9999"
        self.assertEqual(source.enrich(build, [game], lambda _: CURRENT)["confirmed"], 0)
        game.away.id, game.away.name = "148", "PSV Eindhoven Women"
        self.assertEqual(source.enrich(build, [game], lambda _: CURRENT)["confirmed"], 0)

    def test_programme_pregame_window_is_bounded_and_does_not_accept_late_airings(self):
        for minutes, expected in [(0, 1), (5, 1), (15, 1), (16, 0), (-1, 0), (1440, 0)]:
            with self.subTest(minutes=minutes):
                data = payload()
                event(data)["stme"] = (match().utc - timedelta(minutes=minutes)).isoformat()
                self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], expected)
        for title in ["Replay: El Paso Locomotive FC vs. Orange County SC",
                      "El Paso Locomotive FC vs. Orange County SC (Replay)",
                      "El Paso Locomotive FC Women vs. Orange County SC Women"]:
            data = payload()
            event(data)["nme"] = title
            self.assertEqual(source.enrich(build, [match()], lambda _: page(data))["confirmed"], 0)

    def test_late_us_matches_use_the_previous_utc_day_and_dst_aware_eastern_dates(self):
        game = match(utc=datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc))
        game.home.name, game.away.name, game.away.id = "Las Vegas Lights FC", "Lexington", "21822"
        fetch = Mock(return_value=CURRENT)
        self.assertEqual(source.enrich(build, [game], fetch)["confirmed"], 1)
        fetch.assert_called_once_with(source.DATED_URL.format(kind="upcoming", day="20261017"))
        for utc, day in [(datetime(2026, 7, 2, 4, 30, tzinfo=timezone.utc), "20260702"),
                         (datetime(2026, 12, 2, 4, 30, tzinfo=timezone.utc), "20261201")]:
            self.assertEqual(source.candidate_urls(match(utc=utc)),
                             (source.DATED_URL.format(kind="upcoming", day=day),))

    def test_local_midnight_overlap_deduplicates_evidence_and_survives_one_failed_page(self):
        game = match(utc=datetime(2026, 10, 11, 4, 5, tzinfo=timezone.utc))
        data = payload()
        event(data)["stme"] = "2026-10-11T03:55:00Z"
        urls = source.candidate_urls(game)
        self.assertEqual(len(urls), 2)
        for fail_second in [False, True]:
            with self.subTest(fail_second=fail_second):
                current = match(utc=game.utc)
                fetch = Mock(side_effect=lambda url: None if fail_second and url == urls[1] else page(data))
                report = source.enrich(build, [current], fetch)
                self.assertEqual((report["requested"], report["confirmed"], report["unmatched"]), (2, 1, 0))
                self.assertEqual(report["failed"], int(fail_second))
                self.assertEqual(len(current.broadcast_evidence), 1)

    def test_alternative_routes_are_checked_without_rule_and_do_not_erase_existing_outlets(self):
        game = match(outlets=[build.map_outlet("CBS Sports Network", "usa.usl.1")], rule=None)
        source.enrich(build, [game], lambda _: SAMPLE)
        self.assertEqual({o.label for o in game.outlets}, {"CBS Sports Network", "ESPN+"})
        self.assertEqual(source.enrich(build, [match(rule=None)], lambda _: SAMPLE)["confirmed"], 1)

    def test_page_date_must_agree_with_its_own_source_link_when_present(self):
        with self.assertRaisesRegex(ValueError, "different schedule date"):
            source.parse_schedule(CURRENT, expected_day="20261018")
        self.assertEqual(len(source.parse_schedule(CURRENT, expected_day="20261017")), 5)


if __name__ == "__main__":
    unittest.main()
