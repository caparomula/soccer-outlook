"""Provider orchestration must preserve uncertainty and expose its evidence to the visitor."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import broadcast_listings
import build
from tests.test_discovery import fixture


NOW = datetime(2026, 10, 10, 21, tzinfo=timezone.utc)


class BroadcastChecks(unittest.TestCase):
    def test_malformed_extreme_source_times_do_not_abort_the_build(self):
        from espn_listings import _time
        from fox_listings import _kickoff
        from paramount_listings import _iso_time
        from provider_utils import kickoff
        from vix_listings import _kickoff as vix_time
        for parse in (_time, _kickoff, _iso_time, kickoff, vix_time):
            with self.subTest(parser=parse.__module__):
                self.assertIsNone(parse("9999-12-31T23:59:59-01:00"))
                self.assertIsNone(parse("0001-01-01T00:00:00+01:00"))
        self.assertIsNone(_iso_time("0001-01-01T00:00:00+00:00"))

    def test_every_configured_rights_fallback_has_an_adapter(self):
        channels = set()
        for rights in build.RIGHTS.leagues.values():
            if rights.usual:
                channels.update(rights.usual.by_home.values())
                if rights.usual.channel:
                    channels.add(rights.usual.channel)
        self.assertFalse(channels - broadcast_listings.PROVIDERS.keys())

    def test_existing_listings_are_never_sent_for_supplemental_verification(self):
        match = fixture()
        with patch.object(broadcast_listings, "import_module") as loader:
            self.assertEqual(broadcast_listings.enrich(build, [match], lambda _: b"", NOW), {})
        loader.assert_not_called()
        self.assertEqual(match.broadcast_check, "")

    def test_failed_and_unlisted_checks_preserve_rights_and_explain_them_in_details(self):
        for status in ("unavailable", "unlisted"):
            match = fixture(channel=None, rule=build.Outlet("ESPN+", ["espn", "espnplus"]))
            url = "https://www.espn.com/watch/schedule"
            def enrich(builder, matches, fetch):
                return dict(requested=1, confirmed=0, unmatched=1, failed=int(status == "unavailable"),
                            per_match={match.id: dict(status=status, url=url)})
            with patch.object(broadcast_listings, "import_module", return_value=SimpleNamespace(enrich=enrich)):
                report = broadcast_listings.enrich(build, [match], lambda _: None, NOW)
            self.assertEqual(match.broadcast_check, status)
            self.assertEqual(match.broadcast_checked_at, NOW.isoformat())
            self.assertEqual(match.rule.label, "ESPN+")
            self.assertFalse(match.outlets)
            html = build.detail_html(match, {})
            self.assertIn('href="' + url + '"', html)
            self.assertIn("Coverage shown is the usual arrangement", html)
            self.assertNotIn("broadcast listing</a>", html)
            self.assertEqual(report["ESPN+"]["confirmed"], 0)

    def test_confirmed_source_is_visible_in_all_rendered_details(self):
        match = fixture(channel=None, rule=build.Outlet("FOX Sports networks", ["fox"]))
        url = "https://www.foxsports.com/soccer/a-vs-b-game-boxscore-123"
        def enrich(builder, matches, fetch):
            match.outlets = [builder.map_outlet("FS2", match.league)]
            match.rule = None
            match.broadcast_source, match.broadcast_url = "FOX Sports", url
            return dict(requested=1, confirmed=1, unmatched=0, failed=0,
                        per_match={match.id: dict(status="confirmed", url=url)})
        with patch.object(broadcast_listings, "import_module", return_value=SimpleNamespace(enrich=enrich)):
            broadcast_listings.enrich(build, [match], lambda _: b"", NOW)
        self.assertEqual(match.broadcast_check, "confirmed")
        self.assertIn('href="' + url + '"', build.row_html(match, {}))
        self.assertIn("FOX Sports broadcast listing", build.detail_html(match, {}))
        self.assertNotIn("Coverage shown is the usual arrangement", build.detail_html(match, {}))

    def test_skipped_fixtures_are_not_reported_as_checked(self):
        match = fixture(channel=None, rule=build.Outlet("ESPN+", ["espn"]))
        def enrich(*args):
            return dict(requested=0, confirmed=0, unmatched=0, failed=0, per_match={})
        with patch.object(broadcast_listings, "import_module", return_value=SimpleNamespace(enrich=enrich)):
            broadcast_listings.enrich(build, [match], lambda _: None, NOW)
        self.assertEqual(match.broadcast_check, "not_checked")
        self.assertEqual(match.broadcast_checked_at, "")
        self.assertIn("has not been verified", build.detail_html(match, {}))
