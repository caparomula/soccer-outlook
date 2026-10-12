"""Match destinations must agree with verified source URLs and streaming entitlements."""
import json
from pathlib import Path
import unittest

import apple_listings
import bein_listings
import build
import espn_listings
import peacock_listings
import vix_listings
import watch_links
from tests.test_discovery import fixture


FIXTURES = Path(__file__).parent / "fixtures"
UUID = "12345678-1234-1234-1234-123456789abc"
EXAMPLES = (
    ("Apple TV", "Apple TV", "apple", "https://tv.apple.com/us/sporting-event/home-v-away/umc.cse.example"),
    ("Watch ESPN", "ESPN+", "espnplus", "https://www.espn.com/watch/player/_/id/" + UUID),
    ("ViX", "ViX", "vix", "https://vix.com/live/transmission-matchid-2641263"),
    ("Peacock", "Peacock", "peacock", "https://www.peacocktv.com/sports/chivas-vs.-atlante/" + UUID),
    ("beIN Sports Connect", "beIN Sports Connect", "bein",
     "https://watch.beinsports-apps.com/upcoming-events/events/benfica-vs-vitoria-guimaraes-liga-de-portugal-round-8"),
)


def game(source, channel, url):
    return fixture(channel=channel, broadcast_source=source, broadcast_url=url)


def direct_links(match):
    return [link for link in watch_links.match_links(match) if link["kind"] == "match"]


class MatchLinks(unittest.TestCase):
    def test_each_supported_destination_uses_its_verified_url_and_available_routes(self):
        for source, channel, service, url in EXAMPLES:
            with self.subTest(source=source):
                links = direct_links(game(source, channel, url))
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]["url"], url)
                self.assertEqual(links[0]["kind"], "match")
                self.assertIn(service, links[0]["via"])
                self.assertTrue(links[0]["label"].startswith("Open on "))

    def test_actual_recorded_provider_urls_are_supported(self):
        apple = json.loads((FIXTURES / "apple-mls.json").read_text())
        apple_page = '<script id="serialized-server-data" type="application/json">' + json.dumps(apple) + '</script>'
        urls = (
            ("Apple TV", "Apple TV", apple_listings.parse_schedule(apple_page)[0][3]),
            ("Watch ESPN", "ESPN+", espn_listings.parse_schedule((FIXTURES / "espn-watch-schedule.html").read_bytes())[0].url),
            ("ViX", "ViX", vix_listings.parse_schedule((FIXTURES / "vix-liga-mx.html").read_bytes())[0].url),
        )
        for source, channel, url in urls:
            with self.subTest(source=source):
                self.assertTrue(url)
                self.assertEqual(direct_links(game(source, channel, url))[0]["url"], url)
        # The Peacock fixture contains other sports too; its soccer path shape is checked
        # by the adapter itself, rather than pretending a different event is this match.
        self.assertEqual(peacock_listings._source_url(EXAMPLES[3][3]), EXAMPLES[3][3])
        self.assertEqual(bein_listings.event_url(EXAMPLES[4][3]), EXAMPLES[4][3])

    def test_separate_streaming_subscriptions_never_use_tv_channel_access(self):
        for source, channel, service, url in EXAMPLES:
            if source == "Watch ESPN":
                continue
            with self.subTest(source=source):
                match = game(source, channel, url)
                # Even malformed outlet data must not turn TV carriage into a subscription.
                match.outlets[0].via = ["yttv", "yttvsports", "cable", "ota", "free"]
                self.assertEqual(direct_links(match), [])
                match.outlets[0].via.append(service)
                self.assertEqual(direct_links(match)[0]["via"], [service])

    def test_espn_routes_require_matching_channels_and_exclude_antenna(self):
        url = EXAMPLES[1][3]
        abc = game("Watch ESPN", "ABC", url)
        routes = direct_links(abc)[0]["via"]
        self.assertIn("espn", routes)
        self.assertIn("yttv", routes)
        self.assertNotIn("ota", routes)
        self.assertNotIn("espnplus", routes)
        plus = game("Watch ESPN", "ESPN+", url)
        self.assertIn("espnplus", direct_links(plus)[0]["via"])
        self.assertNotIn("cable", direct_links(plus)[0]["via"])
        self.assertNotIn("fubo", direct_links(plus)[0]["via"])
        unrelated = game("Watch ESPN", "TUDN", url)
        self.assertEqual(direct_links(unrelated), [])

    def test_unknown_outlets_and_usual_rules_do_not_grant_links(self):
        for source, channel, service, url in EXAMPLES:
            with self.subTest(source=source):
                match = game(source, channel, url)
                match.outlets[0].known = False
                self.assertEqual(direct_links(match), [])
                match.rule, match.outlets = match.outlets[0], []
                match.rule.known = True
                self.assertEqual(direct_links(match), [])

    def test_generic_hubs_and_evidence_pages_are_not_match_destinations(self):
        examples = (
            ("FOX Sports", "FS2", "https://www.foxsports.com/soccer/america-game-boxscore-123"),
            ("TUDN", "TUDN", "https://www.tudn.com/futbol/liga-mx/america-vs-monterrey-liga-mx-ap-2026-10-10"),
            ("Paramount+", "Paramount+", "https://www.paramountplus.com/sports/"),
            ("Fandango", "Fandango", "https://athome.fandango.com/content/browse/uxpage/Bundesliga/405"),
            ("Apple TV", "Apple TV", apple_listings.SCHEDULE_URL),
            ("Watch ESPN", "ESPN+", "https://www.espn.com/watch/schedule"),
            ("Peacock", "Peacock", "https://www.peacocktv.com/sports"),
            ("ViX", "ViX", vix_listings.SCHEDULE_URL),
            ("beIN Sports Connect", "beIN Sports Connect", bein_listings.SCHEDULE_URL),
        )
        for source, channel, url in examples:
            with self.subTest(source=source, url=url):
                self.assertEqual(direct_links(game(source, channel, url)), [])

    def test_untrusted_destinations_fail_closed(self):
        for source, channel, service, url in EXAMPLES:
            host = url.split('/')[2]
            invalid = (
                None, "", "javascript:alert(1)", url.replace("https:", "http:"),
                url.replace(host, host + ".evil.example"), url.replace(host, "attacker@" + host),
                url.replace(host, host + ":443"), url.replace(host, "[broken"),
                url + "?redirect=https://evil.example", url + "#arbitrary", url + "/extra",
                "\n" + url, url.replace(host, host + "\t"), url + " ", url + "\x00",
                url + "x" * 1025,
            )
            for bad in invalid:
                with self.subTest(source=source, url=bad):
                    self.assertEqual(direct_links(game(source, channel, bad)), [])
            wrong_source = game("Unknown", channel, url)
            self.assertEqual(direct_links(wrong_source), [])

    def test_services_are_deduplicated_without_changing_match_data(self):
        match = game("Watch ESPN", "ESPN", EXAMPLES[1][3])
        match.outlets.extend([build.map_outlet("ESPN Deportes", match.league),
                              build.map_outlet("FS1", match.league)])
        original = [(outlet.label, list(outlet.via)) for outlet in match.outlets]
        link = direct_links(match)[0]
        self.assertEqual(link["via"], sorted(set(link["via"])))
        self.assertNotIn("foxone", link["via"])
        self.assertEqual([(outlet.label, outlet.via) for outlet in match.outlets], original)

    def test_youtube_tv_has_one_honestly_labelled_service_fallback_for_eligible_plans(self):
        match = fixture(channel="TUDN")
        match.outlets.extend([build.map_outlet(channel, match.league) for channel in
                              ("beIN Sports", "ESPN Deportes", "TUDN")])
        self.assertEqual(watch_links.match_links(match), [{
            "url": "https://tv.youtube.com/", "label": "Open YouTube TV", "kind": "service",
            "via": sorted(watch_links.YOUTUBE_TV_SERVICES),
        }])
        for service in watch_links.YOUTUBE_TV_SERVICES:
            with self.subTest(service=service):
                match.outlets[0].via = [service]
                match.outlets = match.outlets[:1]
                self.assertEqual(watch_links.match_links(match)[0]["via"], [service])

    def test_youtube_fallback_requires_confirmed_channel_access(self):
        match = fixture(channel="TUDN")
        match.outlets[0].known = False
        self.assertEqual(watch_links.match_links(match), [])
        match.outlets[0].known = True
        match.rule, match.outlets = match.outlets[0], []
        self.assertEqual(watch_links.match_links(match), [])
        self.assertEqual(watch_links.match_links(fixture(channel="Peacock")), [])

    def test_espn_event_and_youtube_service_fallback_remain_distinct_choices(self):
        links = watch_links.match_links(game("Watch ESPN", "ESPN+", EXAMPLES[1][3]))
        self.assertEqual([link["kind"] for link in links], ["match", "service"])
        self.assertEqual(links[0]["url"], EXAMPLES[1][3])
        self.assertEqual(links[1]["url"], "https://tv.youtube.com/")
        self.assertEqual(links[1]["via"], ["yttv", "yttvsports"])


if __name__ == "__main__":
    unittest.main()
