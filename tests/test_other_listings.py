"""Recorded public broadcaster records must not turn guesses or replays into listings."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import apple_listings
import bein_listings
import build
import fandango_listings
from provider_utils import kickoff
from tests.test_discovery import fixture

FIXTURES = Path(__file__).parent / "fixtures"
APPLE = json.loads((FIXTURES / "apple-mls.json").read_text())
FANDANGO = (FIXTURES / "fandango-bundesliga.xml").read_bytes()
BEIN = (FIXTURES / "bein-catalogue.html").read_bytes()
BEIN_EVENT = (FIXTURES / "bein-event.html").read_bytes()
BEIN_URL = "https://watch.beinsports-apps.com/upcoming-events/events/benfica-vs-vitoria-guimaraes-liga-de-portugal-round-8"
NAV = b'<note type="uxNavResponse"><zToken>anonymous-browse-token</zToken></note>'


def apple_page(data=None):
    return ('<script type="application/json" id="serialized-server-data">' +
            json.dumps(APPLE if data is None else data) + '</script>').encode()


def game(provider):
    league, rule, home, away, when = {
        "apple": ("usa.1", "Apple TV", "New England Revolution", "Seattle Sounders FC", "2026-10-10T23:30:00Z"),
        "fandango": ("ger.1", "Fandango", "FC Cologne", "Borussia Mönchengladbach", "2026-10-11T13:30:00Z"),
        "bein": ("por.1", "beIN Sports Connect", "Benfica", "Vitória de Guimaraes", "2026-10-11T17:00:00Z"),
    }[provider]
    match = fixture(league=league, channel="", kickoff=when)
    match.home.name, match.away.name = home, away
    match.rule = build.map_outlet(rule, league)
    match.service, match.basis, match.outlet = build.evaluate([], match.rule, build.OWNER)
    return match


class PublicBroadcasterListings(unittest.TestCase):
    def test_recorded_apple_event_confirms_exact_match_and_keeps_kickoff(self):
        match = game("apple")
        result = apple_listings.enrich(build, [match], lambda _: apple_page())
        self.assertEqual((result["requested"], result["confirmed"], result["unmatched"]), (1, 1, 0))
        self.assertEqual(match.outlets[0].label, "Apple TV")
        self.assertEqual(result["per_match"][match.id]["status"], "confirmed")
        self.assertIsNone(match.rule)
        self.assertIn("/us/sporting-event/", match.broadcast_url)

    def test_apple_requires_us_mls_storefront(self):
        for key, value in (("storefront", "ca"), ("id", "tvs.sbd.4000"), ("$kind", "CollectionPageIntent")):
            data = json.loads(json.dumps(APPLE))
            data["data"][0]["intent"][key] = value
            result = apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))
            self.assertEqual(result["confirmed"], 0)
            self.assertEqual(result["failed"], 1)

    def test_apple_replays_bad_links_wrong_teams_or_naive_times_do_not_confirm(self):
        for key, value in (("broadcastState", "replay"), ("ariaLabel", "Another Club vs. Seattle Sounders FC"),
                           ("badge", {"isoDatetime": "2026-10-10T23:30:00"}),
                           ("contextAction", {"url": "https://evil.example/us/sporting-event/a/umc.cse.a"})):
            data = json.loads(json.dumps(APPLE))
            data["data"][0]["data"]["shelves"][0]["items"][0][key] = value
            self.assertEqual(apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))["confirmed"], 0)

    def test_apple_repeated_identical_cards_allowed_but_conflicting_event_not(self):
        data = json.loads(json.dumps(APPLE))
        items = data["data"][0]["data"]["shelves"][0]["items"]
        items.append(json.loads(json.dumps(items[0])))
        self.assertEqual(apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))["confirmed"], 1)
        items[1]["badge"]["isoDatetime"] = "2026-10-10T23:31:00Z"
        self.assertEqual(apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))["confirmed"], 0)

    def test_apple_invalid_duplicate_event_link_still_counts_as_conflicting_evidence(self):
        data = json.loads(json.dumps(APPLE))
        items = data["data"][0]["data"]["shelves"][0]["items"]
        items.append(json.loads(json.dumps(items[0])))
        items[1]["contextAction"]["url"] = "https://unrelated.example/event"
        result = apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))
        self.assertEqual(result["confirmed"], 0)

    def test_fandango_anonymous_browse_confirms_only_live_match_row(self):
        match, fetch = game("fandango"), Mock(side_effect=[NAV, FANDANGO])
        result = fandango_listings.enrich(build, [match], fetch)
        self.assertEqual((result["requested"], result["confirmed"]), (2, 1))
        self.assertEqual(match.outlets[0].label, "Fandango")
        self.assertIn("zToken=anonymous-browse-token", fetch.call_args_list[1].args[0])
        self.assertEqual(match.broadcast_url, fandango_listings.SCHEDULE_URL)

    def test_fandango_replays_or_nonmatch_feeds_do_not_confirm(self):
        for before, after in ((b"streamType=live", b"streamType=replay"),
                              (b"eventType=match", b"eventType=highlights"),
                              (b"FC K\xc3\xb6ln vs.", b"Tactics Feed: FC K\xc3\xb6ln vs."),
                              (b"2026-10-11T13:30:00Z", b"2026-10-11T13:31:00Z")):
            result = fandango_listings.enrich(build, [game("fandango")], Mock(side_effect=[NAV, FANDANGO.replace(before, after)]))
            self.assertEqual(result["confirmed"], 0)

    def test_fandango_error_or_xml_entities_fail_closed(self):
        for data in (b'<note type="error"/>', b'<!DOCTYPE note [<!ENTITY x "bad">]><note type="uxPage"/>', None):
            result = fandango_listings.enrich(build, [game("fandango")], Mock(side_effect=[NAV, data]))
            self.assertEqual((result["confirmed"], result["failed"]), (0, 1))

    def test_fandango_missing_live_row_is_unavailable_not_empty(self):
        page = FANDANGO.replace(b"Live &amp; Upcoming Bundesliga Matches", b"Bundesliga Replays")
        result = fandango_listings.enrich(build, [game("fandango")], Mock(side_effect=[NAV, page]))
        self.assertEqual(result["per_match"]["match"]["status"], "unavailable")

    def test_bein_dated_live_event_with_five_minute_pregame_confirms(self):
        match, fetch = game("bein"), Mock(side_effect=[BEIN, BEIN_EVENT])
        utc = match.utc
        result = bein_listings.enrich(build, [match], fetch)
        self.assertEqual((result["requested"], result["confirmed"]), (2, 1))
        self.assertEqual(match.utc, utc)
        self.assertEqual(match.outlets[0].label, "beIN Sports Connect")
        self.assertEqual(match.broadcast_url, BEIN_URL)

    def test_bein_start_must_be_exact_or_observed_five_minute_lead(self):
        for start in (b"12:54:00", b"13:01:00", b"11:55:00"):
            result = bein_listings.enrich(build, [game("bein")], Mock(side_effect=[BEIN, BEIN_EVENT.replace(b"12:55:00", start)]))
            self.assertEqual(result["confirmed"], 0)
        result = bein_listings.enrich(build, [game("bein")], Mock(side_effect=[BEIN, BEIN_EVENT.replace(b"12:55:00", b"13:00:00")]))
        self.assertEqual(result["confirmed"], 1)

    def test_bein_conflicting_same_day_start_cannot_be_discarded_by_time_tolerance(self):
        alternate_url = BEIN_URL + "-1"
        index = BEIN + BEIN.replace(BEIN_URL.encode(), alternate_url.encode())
        alternative = BEIN_EVENT.replace(BEIN_URL.encode(), alternate_url.encode()).replace(b"12:55:00", b"12:45:00")
        fetch = Mock(side_effect=[index, BEIN_EVENT, alternative])
        match = game("bein")
        result = bein_listings.enrich(build, [match], fetch)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(match.outlets, [])

    def test_out_of_range_timezone_conversion_is_invalid_instead_of_raising(self):
        self.assertIsNone(kickoff("9999-12-31T23:59:59-01:00"))
        self.assertIsNone(kickoff("0001-01-01T00:00:00+01:00"))
        data = json.loads(json.dumps(APPLE))
        data["data"][0]["data"]["shelves"][0]["items"][0]["badge"]["isoDatetime"] = "9999-12-31T23:59:59-01:00"
        self.assertEqual(apple_listings.enrich(build, [game("apple")], lambda _: apple_page(data))["confirmed"], 0)
        event = BEIN_EVENT.replace(b"2026-10-11 12:55:00 -0400", b"0001-01-01T00:00:00Z")
        self.assertEqual(bein_listings.enrich(build, [game("bein")], Mock(side_effect=[BEIN, event]))["confirmed"], 0)

    def test_bein_requires_live_type_competition_both_names_date_and_source(self):
        variants = [(BEIN.replace(b"live_event", b"video"), BEIN_EVENT),
                    (BEIN.replace(b"Liga de Portugal", b"Ligue 1"), BEIN_EVENT),
                    (BEIN, BEIN_EVENT.replace(b"2026-10-11", b"2025-10-11")),
                    (BEIN, BEIN_EVENT.replace(b" -0400", b"")),
                    (BEIN, BEIN_EVENT.replace(b"Benfica vs", b"Porto vs")),
                    (BEIN, BEIN_EVENT.replace(b'"canonical"', b'"another"'))]
        for index, event in variants:
            result = bein_listings.enrich(build, [game("bein")], Mock(side_effect=[index, event]))
            self.assertEqual(result["confirmed"], 0)

    def test_bein_catalogue_pagination_is_followed_and_cycles_bounded(self):
        more = BEIN + b'<a href="/upcoming-events?html=1&amp;page=2">Next</a>'
        fetch = Mock(side_effect=[more, BEIN, BEIN_EVENT])
        self.assertEqual(bein_listings.enrich(build, [game("bein")], fetch)["confirmed"], 1)
        self.assertEqual(fetch.call_args_list[1].args[0], bein_listings.SCHEDULE_URL + "?html=1&page=2")
        no_match = game("bein")
        no_match.home.name = "Another Club"
        result = bein_listings.enrich(build, [no_match], lambda _: more)
        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["per_match"][no_match.id]["status"], "unavailable")

    def test_all_providers_preserve_espn_listings_and_skip_void_invalid_matches(self):
        for key, module in (("apple", apple_listings), ("bein", bein_listings), ("fandango", fandango_listings)):
            match = game(key)
            match.outlets = [build.map_outlet("FS1", match.league)]
            invalid, void = game(key), game(key)
            invalid.time_valid = False
            void.status = "Postponed"
            fetch = Mock()
            self.assertEqual(module.enrich(build, [match, invalid, void], fetch)["requested"], 0)
            fetch.assert_not_called()
            self.assertEqual(match.outlets[0].label, "FS1")

    def test_all_providers_keep_uncertainty_on_transport_or_shell_failure(self):
        for key, module in (("apple", apple_listings), ("bein", bein_listings), ("fandango", fandango_listings)):
            for fetch in (Mock(side_effect=OSError("offline")), lambda _: b"<html>Loading...</html>"):
                match = game(key)
                original = match.rule
                result = module.enrich(build, [match], fetch)
                self.assertEqual((result["confirmed"], result["failed"]), (0, 1))
                self.assertEqual(result["per_match"][match.id]["status"], "unavailable")
                self.assertEqual(match.rule, original)
                self.assertEqual(match.outlets, [])


if __name__ == "__main__":
    unittest.main()
