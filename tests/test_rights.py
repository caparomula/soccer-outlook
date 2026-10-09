"""Checks on rights.toml and the mapping build.py derives from it. The workflow runs these before
every build, and a failure keeps the last published page up.

The regression cases are mistakes the page has actually made: each one passed every other check
and showed up only as a wrong answer on the page. Run with: python3 -m unittest -v
"""
import os
import json
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build  # noqa: E402

SEASON_DAY = date(2026, 10, 6)   # a day inside the 2026-27 season, for the facts as checked


def via(name, league="eng.1"):
    return set(build.map_outlet(name, league).via)


class Repository(unittest.TestCase):
    """The file in the repository loads, and agrees with the page it feeds."""

    def test_loads(self):
        build.load_rights(build.RIGHTS_PATH, build.LEAGUES)

    def test_owner_lineup_is_defined(self):
        self.assertTrue(set(build.OWNER) <= set(build.SERVICES))

    def test_every_service_has_its_colors(self):
        # A service without its colors renders as an uncolored pill: easy to miss, so check it.
        styles = (Path(build.__file__).resolve().parent / "web" / "styles.css").read_text(encoding="utf-8")
        for sid in build.SERVICES:
            with self.subTest(service=sid):
                self.assertEqual(styles.count(f"--svc-{sid}:"), 3, "light, dark and forced-dark colors")
                self.assertEqual(styles.count(f".svc-{sid} {{"), 1)

    def test_every_channel_maps_to_defined_services(self):
        for name, o in build.OUTLETS.items():
            with self.subTest(name=name):
                self.assertTrue(set(o["via"]) <= set(build.SERVICES))

    def test_names_match_in_any_case_and_spacing(self):
        self.assertEqual(via(" usa NET "), via("USA Network"))

    def test_report_is_clean_for_the_season_as_checked(self):
        build.UNKNOWN_OUTLETS.clear()
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            self.assertEqual(build.audit([]), [])


class Regressions(unittest.TestCase):
    """One case per mistake the page has shipped."""

    def test_usa_network_under_espns_abbreviation(self):
        # ESPN lists USA Network as "USA Net"; unmapped, its matches showed as on no service.
        self.assertIn("usa", via("USA Net"))

    def test_fubo_means_its_pro_plan(self):
        # The Fubo pill used to match only listings named "Fubo", so a Fubo-only lineup was empty.
        for name in ("FS1", "FS2", "FOX", "ESPN", "ESPN2", "ABC", "CBS", "CBSSN", "NBC", "USA Net", "NBCSN", "Tele", "beIN Sports"):
            with self.subTest(carried=name):
                self.assertIn("fubo", via(name))
        # Not in Pro, or not on Fubo at all.
        for name in ("TNT", "TBS", "truTV", "Univision", "TUDN", "UniMas", "Universo", "ESPNU", "ESPN Deportes", "Fox Deportes",
                     "Fox Soccer Plus", "ESPN+", "Peacock"):
            with self.subTest(not_carried=name):
                self.assertNotIn("fubo", via(name))

    def test_fandango_and_nwsl_plus_are_free(self):
        for name in ("Fandango", "NWSL+"):
            with self.subTest(name=name):
                o = build.map_outlet(name, "ger.1")
                self.assertTrue(o.known and o.free)
                self.assertIn("free", o.via)

    def test_bundesliga_is_usually_on_fandango_not_espn(self):
        # The Bundesliga moved from ESPN to Versant in 2026-27; "usually ESPN+" outlived the deal.
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            home = build.usual_home("ger.1")
        self.assertEqual(home.label, "Fandango")
        self.assertIn("free", home.via)
        self.assertNotIn("espn", home.via)

    def test_dfb_pokal_claims_no_usual_home(self):
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            self.assertIsNone(build.usual_home("ger.dfb_pokal"))
            self.assertIn("not confirmed", build.league_hint("ger.dfb_pokal"))

    def test_peacock_carries_nbc_but_not_usa_network_or_nbcsn(self):
        self.assertIn("peacock", via("NBC"))
        self.assertNotIn("peacock", via("USA Net"))
        self.assertNotIn("peacock", via("NBCSN"))

    def test_cbs_sports_network_streams_on_paramount_only_for_its_competitions(self):
        self.assertIn("para", via("CBSSN", "ita.1"))
        self.assertIn("para", via("CBS Sports Network", "uefa.champions"))
        self.assertNotIn("para", via("CBSSN", "usa.nwsl"))


class UnlistedCompetitions(unittest.TestCase):
    """Competitions ESPN lists no broadcasters for, so the page has only rights.toml to go on."""

    def home(self, league, club=""):
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            return build.usual_home(league, club)

    def test_liga_mx_goes_by_the_home_club(self):
        self.assertEqual(self.home("mex.1", "227").via, ["vix"])       # América
        self.assertEqual(self.home("mex.1", "219").label, "Peacock") # Guadalajara
        self.assertEqual(self.home("mex.1", "10125").via, ["fox"])   # Tijuana
        self.assertEqual(self.home("mex.1", "220").via, ["vix"])     # Monterrey
        self.assertIsNone(self.home("mex.1", "América"))             # a name is not an identity

    def test_liga_mx_club_the_table_doesnt_name_claims_nothing(self):
        self.assertIsNone(self.home("mex.1", "20702"))
        self.assertIsNone(self.home("mex.1"))
        match = SimpleNamespace(league="mex.1", home=SimpleNamespace(name="Mazatlán", id="20702"), outlets=[])
        build.UNKNOWN_OUTLETS.clear()
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            report = build.audit([match])
        self.assertTrue(any("'Mazatlán'" in line and "by_home_team" in line for line in report), report)

    def test_liga_mx_table_covers_every_club_espn_lists(self):
        teams = json.loads((Path(__file__).parent / "fixtures" / "espn-mex-teams.json").read_text())["teams"]
        espn = {team["id"] for team in teams}
        self.assertEqual(set(build.RIGHTS.leagues["mex.1"].usual.by_home), espn)

    def test_primeira_liga_is_on_beins_own_service_not_fubo(self):
        home = self.home("por.1")
        self.assertEqual(home.label, "beIN Sports Connect")
        self.assertEqual(home.via, ["bein"])

    def test_concacaf_nations_league_moved_to_fox(self):
        hint = build.league_hint("concacaf.nations.league")
        self.assertIn("FOX", hint)
        self.assertNotIn("Paramount", hint)

    def test_ligue_1_claims_no_usual_home(self):
        # beIN guarantees only four live matches a round; claiming every match would be a guess.
        self.assertIsNone(self.home("fra.1"))


class FailingSafe(unittest.TestCase):
    """What the page does with a fact it can no longer vouch for."""

    def setUp(self):
        build.UNKNOWN_OUTLETS.clear()

    tearDown = setUp

    def test_unknown_name_is_unrecognized_and_reported(self):
        o = build.map_outlet("Mystery Sports+", "eng.1")
        self.assertFalse(o.known)
        self.assertEqual(o.via, [])
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            report = build.audit([])
        self.assertTrue(any("'Mystery Sports+'" in line for line in report), report)

    def test_unrecognized_channel_is_not_called_unavailable(self):
        m = SimpleNamespace(service="", basis="none", outlet="", outlets=[build.map_outlet("Mystery Sports+", "eng.1")])
        self.assertIn("Channel not recognized", build.chip_html(m))

    def test_usual_home_lapses_after_its_season(self):
        until = build.RIGHTS.leagues["esp.1"].usual.until
        with mock.patch.object(build, "TODAY", until):
            self.assertIsNotNone(build.usual_home("esp.1"))
        with mock.patch.object(build, "TODAY", date.fromordinal(until.toordinal() + 1)):
            self.assertIsNone(build.usual_home("esp.1"))
            self.assertIn("not confirmed for this season", build.league_hint("esp.1"))
            self.assertTrue(any("La Liga" in line and "lapsed" in line for line in build.audit([])))

    def test_lapse_is_announced_a_month_ahead(self):
        until = build.RIGHTS.leagues["esp.1"].usual.until
        with mock.patch.object(build, "TODAY", date.fromordinal(until.toordinal() - 20)):
            self.assertTrue(any("La Liga" in line and "lapses in 20 days" in line for line in build.audit([])))

    def test_unchecked_facts_are_reported(self):
        oldest = min(when for _, when in build.RIGHTS.checked)
        with mock.patch.object(build, "TODAY", date.fromordinal(oldest.toordinal() + build.STALE_AFTER_DAYS + 1)):
            self.assertTrue(any("last checked" in line for line in build.audit([])))

    def test_usual_home_that_disagrees_with_the_listings_is_reported(self):
        listed = [SimpleNamespace(league="ger.1", outlets=[build.map_outlet("ESPN+", "ger.1")]) for _ in range(6)]
        with mock.patch.object(build, "TODAY", SEASON_DAY):
            report = build.audit(listed)
        self.assertTrue(any("Bundesliga" in line and "only 0 of its 6" in line for line in report), report)


GOOD = """
[channels]
"Alpha" = {}
"Beta" = { espn = ["Bee"], free = true }

[services.one]
name = "One"
channels = ["Alpha"]
source = "test"
checked = 2026-10-01

[leagues."esp.1"]
usual = "Alpha"
season = "2026-27"
until = 2027-06-30
source = "test"
checked = 2026-10-01
"""


class Validation(unittest.TestCase):
    """load_rights() refuses each kind of edit that would quietly break the page."""

    def load(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
            f.write(textwrap.dedent(text))
        try:
            return build.load_rights(f.name, build.LEAGUES)
        finally:
            os.unlink(f.name)

    def refuses(self, text, phrase):
        with self.assertRaises(build.RightsError) as caught:
            self.load(text)
        self.assertIn(phrase, str(caught.exception))
        return str(caught.exception)

    def test_good_file_loads(self):
        r = self.load(GOOD)
        self.assertEqual(r.outlets["bee"]["label"], "Beta")
        self.assertEqual(r.outlets["alpha"]["via"], ["one"])

    def test_service_naming_an_undefined_channel(self):
        self.refuses(GOOD.replace('channels = ["Alpha"]', 'channels = ["Alpha", "Gamma"]'), "no channel 'Gamma'")

    def test_two_channels_claiming_one_name(self):
        self.refuses(GOOD.replace('"Alpha" = {}', '"Alpha" = { espn = ["Bee"] }'), "also belongs to")

    def test_usual_home_without_a_season_end(self):
        self.refuses(GOOD.replace("until = 2027-06-30\n", ""), "until must be a date")

    def test_usual_home_no_service_carries(self):
        self.refuses(GOOD.replace('usual = "Alpha"', 'usual = "Beta"'), "no service carries 'Beta'")

    def test_misspelt_key(self):
        self.refuses(GOOD.replace('channels = ["Alpha"]', 'chanels = ["Alpha"]'), "unknown key chanels")

    def test_untracked_competition(self):
        self.refuses(GOOD.replace('[leagues."esp.1"]', '[leagues."esp.9"]'), "not a competition build.py tracks")

    def test_fact_without_a_source(self):
        self.refuses(GOOD.replace('name = "One"\nchannels = ["Alpha"]\nsource = "test"', 'name = "One"\nchannels = ["Alpha"]'),
                     "services.one: missing source")

    def test_check_date_in_the_future(self):
        self.refuses(GOOD.replace("checked = 2026-10-01", "checked = 2099-01-01", 1), "in the future")

    def test_every_problem_is_listed_at_once(self):
        message = self.refuses(GOOD.replace('channels = ["Alpha"]', 'channels = ["Gamma"]').replace("until = 2027-06-30\n", ""),
                               "problem(s)")
        self.assertIn("no channel 'Gamma'", message)
        self.assertIn("until must be a date", message)

    def test_club_table_naming_an_undefined_channel(self):
        self.refuses(GOOD.replace('usual = "Alpha"', 'by_home_team = { "123" = "Gamma" }'), "names no channel 'Gamma'")

    def test_club_table_without_a_season_end(self):
        self.refuses(GOOD.replace('usual = "Alpha"', 'by_home_team = { "123" = "Alpha" }').replace("until = 2027-06-30\n", ""),
                     "until must be a date")

    def test_club_table_alone_is_enough(self):
        r = self.load(GOOD.replace('usual = "Alpha"', 'by_home_team = { "123" = "Alpha" }'))
        self.assertEqual(r.leagues["esp.1"].usual.by_home, {"123": "Alpha"})
        self.assertEqual(r.leagues["esp.1"].usual.channel, "")

    def test_club_table_rejects_display_names(self):
        self.refuses(GOOD.replace('usual = "Alpha"', 'by_home_team = { "Club" = "Alpha" }'), "numeric ESPN team ID")

    def test_not_toml(self):
        self.refuses("[channels\n", ".toml: ")


if __name__ == "__main__":
    unittest.main()
