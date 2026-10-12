"""Match destinations must agree with verified source URLs and streaming entitlements."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_youtube_watch_url_keeps_query_and_requires_exact_fixture_channel_and_plan(self):
        match = fixture("401879267", channel="USA Network")
        link = watch_links.match_links(match)[0]
        self.assertEqual(link["url"], "https://tv.youtube.com/watch/Qi_C4zyXBIg?vp=0gEEEgIwAQ%3D%3D")
        self.assertEqual(link["via"], ["yttv", "yttvsports"])
        self.assertTrue(link["live_only"])
        self.assertEqual(link["kind"], "match")
        # The same teams/channel in another fixture must not inherit this watch ID.
        self.assertEqual(watch_links.match_links(fixture("another", channel="USA Network")), [])
        for channel in ("TUDN", "Universo", "Peacock", "Paramount+"):
            with self.subTest(channel=channel):
                self.assertEqual(watch_links.match_links(fixture("401879267", channel=channel)), [])
        match.outlets[0].via = ["yttvspanishplus", "cable"]
        self.assertEqual(watch_links.match_links(match)[0]["via"], ["yttvspanishplus"])
        match.outlets[0].via = ["cable"]
        self.assertEqual(watch_links.match_links(match), [])

    def test_youtube_url_requires_confirmed_channel_access(self):
        match = fixture("401879267", channel="USA Network")
        match.outlets[0].known = False
        self.assertEqual(watch_links.match_links(match), [])
        match.outlets[0].known = True
        match.rule, match.outlets = match.outlets[0], []
        self.assertEqual(watch_links.match_links(match), [])

    def test_youtube_url_is_only_clickable_while_live_in_static_markup(self):
        match = fixture("401879267", channel="USA Network", service="yttv", basis="listed")
        for state in ("pre", "in", "post"):
            with self.subTest(state=state):
                match.state = state
                # Retain the map so a browser score update can activate/deactivate the link.
                self.assertTrue(build.provider_links(match)["yttv"]["live_only"])
                markup = build.chip_html(match)
                self.assertEqual('<a class="chip' in markup, state == "in")
                self.assertIn("YouTube TV (main plan)", markup)

    def test_youtube_rejects_non_watch_urls_and_unexpected_query_parameters(self):
        good = "https://tv.youtube.com/watch/Qi_C4zyXBIg?vp=0gEEEgIwAQ%3D%3D"
        bad = ("https://tv.youtube.com/", good.replace("https:", "http:"),
               good.replace("tv.youtube.com", "tv.youtube.com.evil.example"),
               good.replace("tv.youtube.com", "attacker@tv.youtube.com"),
               good.replace("Qi_C4zyXBIg", "bad"), good + "&redirect=https://evil.example",
               good + "&vp=abc", good + "#extra", "\n" + good, None)
        for url in bad:
            with self.subTest(url=url), patch.dict(watch_links.YOUTUBE_TV_LINKS,
                    {"fixture": {"url": url, "channel": "USA Network"}}, clear=True):
                self.assertEqual(watch_links.match_links(fixture("fixture")), [])
        with patch.dict(watch_links.YOUTUBE_TV_LINKS,
                {"fixture": {"url": good.split("?")[0], "channel": "USA Network"}}, clear=True):
            self.assertEqual(watch_links.match_links(fixture("fixture"))[0]["url"], good.split("?")[0])

    def test_no_generic_youtube_homepage_fallback(self):
        match = fixture(channel="TUDN")
        match.outlets.extend([build.map_outlet(channel, match.league) for channel in
                              ("beIN Sports", "ESPN Deportes", "TUDN")])
        self.assertEqual(watch_links.match_links(match), [])
        self.assertEqual(watch_links.service_links(match), {})

    def test_eligible_youtube_plans_can_use_an_exact_espn_player(self):
        links = watch_links.service_links(game("Watch ESPN", "ESPN+", EXAMPLES[1][3]))
        for service in ("yttv", "yttvsports", "espn", "espnplus"):
            self.assertEqual(links[service], {
                "url": EXAMPLES[1][3], "label": "Open on ESPN", "kind": "match"})
        self.assertNotIn("para", links)
        self.assertNotIn("cable", links)
        self.assertNotIn("yttvspanishplus", links)

    def test_supplied_youtube_player_takes_precedence_over_espn_player(self):
        match = fixture("401879267", channel="USA Network", broadcast_source="Watch ESPN",
                        broadcast_url=EXAMPLES[1][3])
        match.outlets.append(build.map_outlet("ESPN+", match.league))
        links = watch_links.service_links(match)
        self.assertEqual(links["yttv"]["url"], watch_links.YOUTUBE_TV_LINKS["401879267"]["url"])
        self.assertEqual(links["espn"]["url"], EXAMPLES[1][3])

    def test_provider_destinations_use_only_supported_match_routes(self):
        for source, channel, service, url in EXAMPLES:
            with self.subTest(source=source):
                match = game(source, channel, url)
                link = watch_links.service_links(match)[service]
                self.assertEqual(link["url"], url)
                self.assertEqual(link["kind"], "match")
                self.assertNotIn("via", link)
                match.rule, match.outlets = match.outlets[0], []
                self.assertEqual(watch_links.service_links(match), {})
        self.assertEqual(watch_links.service_links(fixture(channel="Paramount+")), {})


if __name__ == "__main__":
    unittest.main()
