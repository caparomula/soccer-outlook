"""Provider orchestration must preserve uncertainty and expose its evidence to the visitor."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import broadcast_listings
import build
import provider_utils
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

    def test_competitions_without_a_supported_source_are_not_sent_to_adapters(self):
        match = fixture()
        with patch.object(broadcast_listings, "import_module") as loader:
            self.assertEqual(broadcast_listings.enrich(build, [match], lambda _: b"", NOW), {})
        loader.assert_not_called()
        self.assertEqual(match.broadcast_check, "")

    def test_tudn_confirmation_makes_liga_mx_available_on_youtube_before_usual_checks(self):
        match = fixture(league="mex.1", channel=None, rule=build.map_outlet("ViX", "mex.1"))
        url = "https://www.tudn.com/futbol/liga-mx/america-vs-monterrey-liga-mx-ap-2026-10-10"
        def enrich(builder, matches, fetch):
            match.outlets = [builder.map_outlet("TUDN", match.league)]
            match.rule = None
            match.broadcast_source, match.broadcast_url = "TUDN", url
            return dict(requested=2, confirmed=1, unmatched=0, failed=0,
                        per_match={match.id: dict(status="confirmed", url=url)})
        adapter = SimpleNamespace(enrich=enrich, SCHEDULE_URL="https://www.tudn.com/futbol/liga-mx/resultados")
        def module(name):
            if name == "tudn_listings":
                return adapter
            return SimpleNamespace(enrich=lambda builder, matches, fetch: provider_utils.report(matches, "https://example.com/schedule"))
        with patch.object(broadcast_listings, "import_module", side_effect=module) as loader:
            report = broadcast_listings.enrich(build, [match], lambda _: b"", NOW)
        self.assertEqual([call.args[0] for call in loader.call_args_list],
                         ["tudn_listings", "fox_listings", "vix_listings", "peacock_listings"])
        self.assertEqual(build.evaluate(match.outlets, match.rule, {"yttv"}), ("yttv", "listed", "TUDN"))
        self.assertEqual(match.broadcast_check, "confirmed")
        self.assertEqual(match.broadcast_checked_at, NOW.isoformat())
        self.assertIn("TUDN broadcast listing", build.detail_html(match, {}))
        self.assertEqual(set(report), {"TUDN", "FOX Sports networks", "ViX", "Peacock"})

    def test_missing_tudn_simulcast_preserves_existing_vix_listing(self):
        match = fixture(league="mex.1", channel="ViX")
        schedule = "https://www.tudn.com/futbol/liga-mx/resultados"
        adapter = SimpleNamespace(SCHEDULE_URL=schedule, enrich=lambda *_: dict(
            requested=1, confirmed=0, unmatched=1, failed=0,
            per_match={match.id: dict(status="unlisted", url=schedule)}))
        with patch.object(broadcast_listings, "import_module", return_value=adapter):
            report = broadcast_listings.enrich(build, [match], lambda _: b"", NOW)
        self.assertEqual([outlet.label for outlet in match.outlets], ["ViX"])
        self.assertEqual(build.evaluate(match.outlets, match.rule, {"yttv"}), ("", "none", ""))
        self.assertEqual(match.broadcast_check, "")
        self.assertNotIn("Coverage shown is the usual arrangement", build.detail_html(match, {}))
        self.assertEqual(report["TUDN"]["unmatched"], 1)

    def test_unavailable_tudn_still_checks_vix_without_granting_youtube_access(self):
        match = fixture(league="mex.1", channel=None, rule=build.map_outlet("ViX", "mex.1"))
        def adapter(name, status):
            url = f"https://{name}.example/schedule"
            return SimpleNamespace(SCHEDULE_URL=url, enrich=lambda *_: dict(
                requested=1, confirmed=0, unmatched=1, failed=int(status == "unavailable"),
                per_match={match.id: dict(status=status, url=url)}))
        modules = {"tudn_listings": adapter("tudn", "unavailable"), "vix_listings": adapter("vix", "unlisted"),
                   "fox_listings": adapter("fox", "unlisted"), "peacock_listings": adapter("peacock", "unlisted")}
        with patch.object(broadcast_listings, "import_module", side_effect=modules.__getitem__):
            report = broadcast_listings.enrich(build, [match], lambda _: None, NOW)
        self.assertEqual(set(report), {"TUDN", "ViX", "FOX Sports networks", "Peacock"})
        self.assertEqual(match.rule.label, "ViX")
        self.assertEqual(match.broadcast_check_source, "ViX")
        self.assertEqual(build.evaluate(match.outlets, match.rule, {"yttv"}), ("", "none", ""))

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


class CoverageDiagnostics(unittest.TestCase):
    def test_existing_listings_get_supplemental_checks_and_merge_simulcasts(self):
        match = fixture(league="mex.1", channel="ViX")
        def module(name):
            def enrich(builder, matches, fetch):
                result = provider_utils.report(matches, "https://example.com/schedule")
                if name == "tudn_listings":
                    provider_utils.confirm(builder, matches[0], ["TUDN", "Univision"], "TUDN",
                                           "https://www.tudn.com/fixture", result)
                return result
            return SimpleNamespace(enrich=enrich, SCHEDULE_URL="https://www.tudn.com/schedule")
        with patch.object(broadcast_listings, "import_module", side_effect=module):
            reports = broadcast_listings.enrich(build, [match], lambda _: b"", NOW)
        self.assertEqual([outlet.label for outlet in match.outlets], ["ViX", "TUDN", "Univision"])
        self.assertEqual(len(match.broadcast_checks), 4)
        self.assertEqual([(item["channel"], item["source"]) for item in match.broadcast_evidence],
                         [("ViX", "ESPN"), ("TUDN", "TUDN"), ("Univision", "TUDN")])
        self.assertTrue(all(item["checked_at"] == NOW.isoformat() for item in match.broadcast_evidence))
        self.assertEqual(reports["TUDN"]["confirmed"], 1)
        self.assertEqual(match.broadcast_check, "confirmed")

    def test_confirm_counts_matches_once_and_keeps_prior_positive_evidence(self):
        match = fixture(channel="USA Network")
        result = provider_utils.report([match], "https://example.com")
        provider_utils.confirm(build, match, ["ESPN+", "ESPN Deportes"], "ESPN", "https://www.espn.com/watch", result)
        provider_utils.confirm(build, match, "ESPN+", "ESPN", "https://www.espn.com/watch", result)
        provider_utils.unavailable(result, [match], reason="pagination_failed")
        self.assertEqual([outlet.label for outlet in match.outlets], ["USA Network", "ESPN+", "ESPN Deportes"])
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(result["unmatched"], 0)
        self.assertEqual(result["per_match"][match.id]["status"], "confirmed")
        self.assertEqual(len(match.broadcast_evidence), 2)

    def test_horizon_diagnostics_distinguish_stale_results_from_empty_and_failed_sources(self):
        match = fixture(kickoff="2026-10-17T18:00:00+00:00")
        for records, expected in (([("home", "away", NOW)], "outside_requested_horizon"), ([], "empty")):
            with self.subTest(expected=expected):
                result = provider_utils.report([match], "https://example.com")
                provider_utils.fetch_document(lambda _: b"{}", "https://example.com", result)
                provider_utils.observe(result, records, "https://example.com", complete=True)
                provider_utils.finish_report(result)
                diagnostics = result["diagnostics"]
                self.assertEqual(diagnostics["status"], expected)
                self.assertEqual(diagnostics["parsed_pages"], 1)
                self.assertEqual(diagnostics["observed_listings"], len(records))
                self.assertEqual(diagnostics["requested_horizon"]["start"], match.utc.isoformat())
        result = provider_utils.report([match], "https://example.com")
        provider_utils.fetch_document(lambda _: None, "https://example.com", result)
        provider_utils.unavailable(result, [match], reason="fetch_failed")
        self.assertEqual(provider_utils.finish_report(result)["diagnostics"]["status"], "unavailable")

    def test_diagnostics_do_not_publish_public_client_tokens_or_credentials(self):
        result = provider_utils.report([], "https://example.com")
        secret_url = "https://user:password@example.com/api?apikey=secret&zToken=token&date=20261015"
        provider_utils.fetch_document(lambda _: b"{}", secret_url, result)
        provider_utils.observe(result, [], secret_url)
        compact = broadcast_listings.compact_report({"Example": result})["Example"]
        for item in compact["requests"] + compact["observations"]:
            self.assertEqual(item["url"], "https://example.com/api?date=20261015")
        self.assertNotIn("per_match", compact)

    def test_skipped_invalid_void_and_completed_fixtures_do_not_trigger_requests(self):
        games = [fixture("post", league="mex.1", state="post"),
                 fixture("void", league="mex.1", status="STATUS_CANCELED"),
                 fixture("invalid", league="mex.1", time_valid=False)]
        # Use the exact void-status vocabulary owned by the parser.
        games[1].status = next(iter(build.VOID_STATUSES))
        with patch.object(broadcast_listings, "import_module") as loader:
            self.assertEqual(broadcast_listings.enrich(build, games, lambda _: None, NOW), {})
        loader.assert_not_called()


class RecentEvidence(unittest.TestCase):
    def prior(self):
        match = fixture(league="mex.1", channel="TUDN", kickoff="2026-10-10T22:00:00+00:00")
        record = asdict(match)
        record["utc"] = match.utc.isoformat()
        record["broadcast_evidence"] = [dict(channel="TUDN", source="TUDN", country="US",
            kind="match_listing", url="https://www.tudn.com/fixture", checked_at=(NOW - timedelta(hours=1)).isoformat())]
        return match, record

    def current(self):
        match, record = self.prior()
        match.outlets = []
        match.broadcast_checks = [dict(source="TUDN", status="unavailable", source_health="unavailable")]
        match.broadcast_evidence = []
        return match, record

    def test_brief_outage_retains_only_same_fixture_and_original_verification_time(self):
        match, record = self.current()
        summary = broadcast_listings.retain_recent_evidence(build, [match], [record], NOW)
        self.assertEqual(summary, {"retained_matches": 1, "retained_channels": 1})
        self.assertEqual([outlet.label for outlet in match.outlets], ["TUDN"])
        self.assertEqual(match.broadcast_evidence[0]["checked_at"], record["broadcast_evidence"][0]["checked_at"])
        self.assertTrue(match.broadcast_evidence[0]["retained"])
        self.assertEqual(build.evaluate(match.outlets, None, {"yttv"})[0], "yttv")

    def test_fresh_absence_changed_fixture_expiry_and_non_us_evidence_are_not_retained(self):
        changes = (
            lambda m, r: m.broadcast_checks[0].update(status="unlisted", source_health="observed"),
            lambda m, r: m.broadcast_checks[0].update(status="unlisted", source_health="partial", reason="kickoff_mismatch"),
            lambda m, r: m.broadcast_checks[0].update(status="unlisted", source_health="partial", reason="ambiguous_event"),
            lambda m, r: m.broadcast_checks[0].update(status="unavailable", source_health="partial", reason="invalid_event"),
            lambda m, r: m.broadcast_checks[0].update(status="unlisted", source_health="partial", reason="future_announcement_for_live_match"),
            lambda m, r: m.broadcast_checks[0].update(status="unavailable", source_health="unavailable", reason="wrong_country"),
            lambda m, r: r.update(utc=(m.utc + timedelta(minutes=5)).isoformat()),
            lambda m, r: r["home"].update(id="different"),
            lambda m, r: r.update(league="eng.1"),
            lambda m, r: r["broadcast_evidence"][0].update(checked_at=(NOW - timedelta(hours=7)).isoformat()),
            lambda m, r: r["broadcast_evidence"][0].update(country="MX"),
            lambda m, r: r["broadcast_evidence"][0].update(kind="replay"),
            lambda m, r: setattr(m, "state", "post"),
        )
        for change in changes:
            with self.subTest(change=change):
                match, record = self.current()
                change(match, record)
                summary = broadcast_listings.retain_recent_evidence(build, [match], [record], NOW)
                self.assertEqual(summary["retained_channels"], 0)
                self.assertFalse(match.outlets)

    def test_retention_never_duplicates_a_current_confirmed_channel(self):
        match, record = self.current()
        match.outlets = [build.map_outlet("TUDN", "mex.1")]
        summary = broadcast_listings.retain_recent_evidence(build, [match], [record], NOW)
        self.assertEqual(summary["retained_channels"], 0)
        self.assertEqual(len(match.outlets), 1)

    def test_partial_catalogue_can_bridge_missing_listing_but_not_extend_its_age(self):
        match, record = self.current()
        match.broadcast_checks[0].update(status="unlisted", source_health="partial", reason="not_seen_in_partial_catalogue")
        summary = broadcast_listings.retain_recent_evidence(build, [match], [record], NOW)
        self.assertEqual(summary["retained_channels"], 1)
        old_record = asdict(match)
        old_record["utc"] = match.utc.isoformat()
        current, _ = self.current()
        later = NOW + timedelta(hours=6)
        self.assertEqual(broadcast_listings.retain_recent_evidence(build, [current], [old_record], later)["retained_channels"], 0)
