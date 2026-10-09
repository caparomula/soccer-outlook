"""Public schedules use real viewing routes and honest, bounded time windows."""
from dataclasses import replace
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import unittest

import build
import discovery


BUILT_AT = datetime(2026, 10, 9, 13, tzinfo=timezone.utc)
SITE = "https://example.com/soccer/"


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.matches = []
        self.canonical = []
        self.hrefs = []
        self.times = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-match-id" in attrs:
            self.matches.append(attrs["data-match-id"])
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical.append(attrs.get("href"))
        if tag == "a":
            self.hrefs.append(attrs.get("href"))
        if tag == "time":
            self.times.append(attrs.get("datetime"))


def fixture(match_id="match", league="eng.1", channel="USA Network", kickoff="2026-10-09T18:00:00+00:00", **changes):
    outlets = [build.map_outlet(channel, league)] if channel else []
    home = build.Team("Home United", "HOM", "", "", score="2", id="1")
    away = build.Team("Away City", "AWY", "", "", score="1", id="2")
    match = build.Match(id=match_id, utc=datetime.fromisoformat(kickoff), time_valid=True,
                        league=league, comp=build.LEAGUES[league]["name"], stage="", note="",
                        home=home, away=away, venue="Fixture Stadium", state="pre", status="",
                        outlets=outlets, rule=None, hint="", service="", basis="none", outlet="",
                        link="https://www.espn.com/soccer/match/_/gameId/1")
    return replace(match, **changes)


class PublicSchedules(unittest.TestCase):
    def pages(self, matches=(), **kwargs):
        return discovery.pages(build, list(matches), {}, kwargs.pop("built_at", BUILT_AT),
                               kwargs.pop("site_url", SITE), **kwargs)

    def test_explicit_build_date_selects_historical_schedule_without_changing_update_time(self):
        match = fixture(kickoff="2026-08-15T18:00:00+00:00")
        self.assertEqual(Markup(self.pages([match])["premier-league/"]).matches, [])
        page = self.pages([match], schedule_day=date(2026, 8, 15))["premier-league/"]
        self.assertEqual(Markup(page).matches, ["match"])
        self.assertIn("Saturday, August 15", page)
        self.assertIn(BUILT_AT.isoformat(), page)

    def test_pages_have_distinct_metadata_and_crawlable_navigation(self):
        pages = self.pages()
        expected = {"premier-league/", "mls/", "paramount-plus/"}
        self.assertEqual(set(pages), expected)
        for slug, page in pages.items():
            parsed = Markup(page)
            self.assertEqual(parsed.canonical, [SITE + slug])
            self.assertTrue({SITE, *(SITE + other for other in expected)}.issubset(set(parsed.hrefs)))
            self.assertIn(f'href="{SITE}favicon.png"', page)
            self.assertIn(f'href="{SITE}sitemap.xml"', page)
            self.assertEqual(page.count("<title>"), 1)
            self.assertLess(page.index("<title>"), page.index("</head>"))
            self.assertIn('aria-current="page"', page)
            self.assertIn('aria-label="US soccer schedules"', page)
            self.assertNotIn("noindex", page)
            self.assertNotIn("<script", page)

    def test_each_league_page_includes_only_its_fixtures(self):
        pages = self.pages([fixture("prem"), fixture("mls", league="usa.1", channel="Apple TV"),
                            fixture("serie", league="ita.1", channel="Paramount+")])
        self.assertEqual(Markup(pages["premier-league/"]).matches, ["prem"])
        self.assertEqual(Markup(pages["mls/"]).matches, ["mls"])
        self.assertEqual(Markup(pages["paramount-plus/"]).matches, ["serie"])

    def test_paramount_uses_available_alternative_not_owner_primary_service(self):
        match = fixture(league="ita.1", channel="Paramount+", service="cable", outlet="CBS Sports Network")
        match.outlets.insert(0, build.map_outlet("CBS Sports Network", "ita.1"))
        page = self.pages([match])["paramount-plus/"]
        self.assertEqual(Markup(page).matches, ["match"])
        self.assertIn('class="match-coverage">Paramount+</p>', page)
        self.assertNotIn("via CBS Sports Network", page)
        self.assertIn("Premium plan", page)

    def test_paramount_cbs_sports_network_simulcast_depends_on_competition(self):
        matches = [fixture("serie", league="ita.1", channel="CBS Sports Network"),
                   fixture("nwsl", league="usa.nwsl", channel="CBS Sports Network"),
                   fixture("golazo", league="eng.2", channel="CBS Sports Golazo")]
        page = self.pages(matches)["paramount-plus/"]
        self.assertEqual(Markup(page).matches, ["serie"])
        self.assertIn("Paramount+ via CBS Sports Network", page)

    def test_unconfirmed_rights_are_labelled_and_unknown_channels_not_assumed_viewable(self):
        usual = build.Outlet("Apple TV", ["apple"])
        mls = fixture("usual", league="usa.1", channel=None, rule=usual)
        unlisted = fixture("unknown", league="usa.1", channel=None,
                           outlets=[build.Outlet("Unknown network", ["apple"], known=False)])
        pages = self.pages([mls, unlisted, fixture("none", channel=None),
                            fixture("para-usual", league="ita.1", channel=None,
                                    rule=build.Outlet("Paramount+", ["para"]))])
        self.assertEqual(Markup(pages["mls/"]).matches, ["usual"])
        self.assertIn("Usually Apple TV; match listing not yet confirmed", pages["mls/"])
        self.assertIn("1 other fixture has no known US viewing option", pages["mls/"])
        self.assertEqual(Markup(pages["premier-league/"]).matches, [])
        self.assertIn("Usually Paramount+; match listing not yet confirmed", pages["paramount-plus/"])

    def test_four_sports_days_include_late_night_and_exclude_exact_end_boundary(self):
        matches = [fixture("before", kickoff="2026-10-09T07:59:00+00:00"),
                   fixture("start", kickoff="2026-10-09T08:00:00+00:00"),
                   fixture("late", kickoff="2026-10-13T07:59:00+00:00"),
                   fixture("end", kickoff="2026-10-13T08:00:00+00:00")]
        page = self.pages(matches)["premier-league/"]
        self.assertEqual(Markup(page).matches, ["start", "late"])
        self.assertIn("Tue, Oct 13", page)  # actual date, even though Monday's sports day
        self.assertIn("3:59 am", page)
        self.assertIn("All kickoff times are Eastern", page)

    def test_pre_dawn_build_uses_previous_sports_day(self):
        page = self.pages([fixture("prior", kickoff="2026-10-08T18:00:00+00:00"),
                           fixture("late-end", kickoff="2026-10-12T07:59:00+00:00"),
                           fixture("outside", kickoff="2026-10-12T08:00:00+00:00")],
                          built_at=datetime(2026, 10, 9, 7, 59, tzinfo=timezone.utc))["premier-league/"]
        self.assertEqual(Markup(page).matches, ["prior", "late-end"])
        self.assertIn("Thursday, October 8", page)

    def test_day_windows_survive_daylight_saving_change(self):
        page = self.pages([fixture("start", kickoff="2026-10-31T08:00:00+00:00"),
                           fixture("last", kickoff="2026-11-04T08:59:00+00:00"),
                           fixture("outside", kickoff="2026-11-04T09:00:00+00:00")],
                          built_at=datetime(2026, 10, 31, 13, tzinfo=timezone.utc))["premier-league/"]
        self.assertEqual(Markup(page).matches, ["start", "last"])

    def test_tbd_never_turns_placeholder_into_a_kickoff(self):
        match = fixture(time_valid=False)
        page = self.pages([match])["premier-league/"]
        self.assertIn("Time TBD", page)
        self.assertNotIn(match.utc.isoformat(), Markup(page).times)
        self.assertNotIn("2:00 pm", page)

    def test_snapshot_is_honest_about_status_and_omits_cancelled_games(self):
        matches = [fixture("live", state="in", status="31′"), fixture("final", state="post", status="FT"),
                   fixture("cancelled", state="post", status="Canceled"),
                   fixture("postponed", state="post", status="Postponed")]
        page = self.pages(matches)["premier-league/"]
        self.assertEqual(set(Markup(page).matches), {"live", "final"})
        self.assertIn("In progress at last update · 31′ · 2–1", page)
        self.assertIn("Final · 2–1", page)
        self.assertNotIn("Live now", page)

    def test_quiet_pages_keep_useful_guidance_and_truthful_empty_states(self):
        pages = self.pages()
        self.assertIn("No matches with listed US coverage or an established usual home in this snapshot", pages["mls/"])
        self.assertIn("Apple TV for the 2026 season", pages["mls/"])
        self.assertIn("NBC, USA Network or Peacock", pages["premier-league/"])
        self.assertIn("How to watch", pages["paramount-plus/"])
        self.assertNotIn("may be incomplete", pages["mls/"])
        later = self.pages(built_at=datetime(2027, 1, 1, 13, tzinfo=timezone.utc))
        self.assertNotIn("Apple TV for the 2026 season", later["mls/"])

    def test_incomplete_sources_are_disclosed_in_relevant_schedules(self):
        pages = self.pages(failed=[("eng.1", BUILT_AT.date())], skipped=[{"league": "usa.1", "id": "bad"}])
        for page in pages.values():
            self.assertIn("may be incomplete", page)
        pages = self.pages(failed=[("eng.1", BUILT_AT.date())])
        self.assertIn("may be incomplete", pages["premier-league/"])
        self.assertNotIn("may be incomplete", pages["mls/"])

    def test_fixture_strings_and_urls_are_escaped_and_unsafe_links_dropped(self):
        match = fixture(venue='<script>alert("venue")</script>', link="javascript:alert(1)",
                        outlets=[build.Outlet("Channel <img>", ["usa"])])
        match.home.name = '<img src=x onerror="bad()">'
        page = self.pages([match], site_url="https://example.com/a&b/")["premier-league/"]
        parsed = Markup(page)
        self.assertNotIn("<img", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Channel &lt;img&gt;", page)
        self.assertEqual(parsed.canonical, ["https://example.com/a&b/premier-league/"])

    def test_verification_metadata_is_in_each_document_head(self):
        token = '<meta name="google-site-verification" content="validated-token">'
        for page in self.pages(head_extra=token).values():
            self.assertIn(token, page[:page.index("</head>")])


if __name__ == "__main__":
    unittest.main()
