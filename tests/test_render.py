"""The standalone page must include its assets regardless of the caller's cwd."""
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import build
from tests.page_fixture import render_page


class Tags(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class Rendering(unittest.TestCase):
    def test_document_and_fragment_include_assets_and_schedule(self):
        root = Path(build.__file__).resolve().parent
        for fragment in (False, True):
            with self.subTest(fragment=fragment):
                page = render_page(build, fragment=fragment)
                self.assertIn((root / "web/styles.css").read_text(encoding="utf-8"), page)
                self.assertIn((root / "web/app.js").read_text(encoding="utf-8"), page)
                self.assertNotRegex(page, r"@@[A-Z_]+@@")
                tags = Tags(page).tags
                rows = [attrs for tag, attrs in tags if tag == "li" and "row" in attrs.get("class", "").split()]
                self.assertEqual(len(rows), 9)
                self.assertFalse(any(tag == "script" and "src" in attrs for tag, attrs in tags))
                self.assertEqual(page.startswith("<!doctype html>"), not fragment)

    def test_loading_assets_from_another_working_directory(self):
        script = str(Path(build.__file__).resolve())
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, script, "--help"], cwd=cwd,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--fragment", result.stdout)


class MalformedFeed(unittest.TestCase):
    """ESPN's lists can hold nulls: on 7 October 2026 the Saudi Pro League table gave a team
    `logos: [null]` and every build crashed. A bad entry must cost at most that entry."""

    @staticmethod
    def entry(team_id, name, rank, logos):
        return {"team": {"id": team_id, "displayName": name, "logos": logos},
                "stats": [{"name": "rank", "value": rank}, None, {"name": "points", "displayValue": "9"}]}

    def test_a_team_without_a_usable_logo_keeps_its_row(self):
        table = build.standings_of({"children": [None, {"name": "", "standings": {"entries": [
            self.entry("1", "Al Faisaly", 2, [None]),
            self.entry("2", "Al Hilal", 1, [None, {"href": "https://a.espncdn.com/hilal.png"}]),
            None]}}]})
        rows = table["tables"][0][1]
        self.assertEqual([(r["name"], r["rank"], r["logo"], r["pts"]) for r in rows],
                         [("Al Hilal", 1, "https://a.espncdn.com/hilal.png", "9"), ("Al Faisaly", 2, "", "9")])
        self.assertEqual(sorted(table["by_team"]), ["1", "2"])

    def test_a_table_in_another_shape_costs_only_that_table(self):
        good = {"children": [{"name": "", "standings": {"entries": [self.entry("2", "Arsenal", 1, [])]}}]}
        answers = {"eng.1": good, "esp.1": {"children": [{"name": "", "standings": {"entries": [{"team": "x"}]}}]},
                   "ita.1": {"children": "junk"}, "fra.1": {"standings": [1, 2]},
                   "ger.1": {"children": [{"name": "", "standings": "unavailable"}]}}     # reaches the guard
        def fake_curl(url, attempts=3):
            league = url.split("/soccer/")[1].split("/")[0]
            return json.dumps(answers[league]).encode() if league in answers else None
        with patch.object(build, "curl_bytes", fake_curl), patch("sys.stderr", io.StringIO()):
            out = build.fetch_standings(list(answers), 2)
        self.assertEqual(list(out), ["eng.1"])

    def test_nulls_inside_an_event_cost_only_what_they_held(self):
        event = Markup.event()
        comp = event["competitions"][0]
        comp["competitors"][0].update(records=[None], leaders=[None, {"name": "goals", "leaders": [None]}])
        comp.update(notes=[None], headlines=[None], geoBroadcasts=[],
                    details=[None, {"scoringPlay": True, "athletesInvolved": [None], "team": {"id": "660"}}])
        match = build.interpret("fifa.friendly.w", event)
        self.assertEqual((match.home.record, match.note), ("", ""))

    def test_an_event_that_cannot_be_read_is_skipped_not_fatal(self):
        broken = Markup.event()
        broken["id"] = "8"
        broken["competitions"][0]["status"] = "postponed"      # a string where an object belongs
        with patch("sys.stderr", io.StringIO()) as err:
            matches = build.interpret_all({"fifa.friendly.w": {"8": broken, "9": Markup.event()}})
        self.assertEqual([m.id for m in matches], ["9"])
        self.assertIn("skip fifa.friendly.w 8", err.getvalue())


class Markup(unittest.TestCase):
    """What build.py puts on the page from ESPN's feed: links that can't run script, labels a screen
    reader can use, and the US national teams shown by default."""

    @staticmethod
    def event(links=(), home="United States"):
        return {"id": "9", "date": "2026-10-10T18:30:00Z", "links": list(links),
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"displayName": home, "id": "660", "abbreviation": "USA"}},
                    {"homeAway": "away", "team": {"displayName": "Spain", "id": "164", "abbreviation": "ESP"}}],
                    "status": {"type": {"state": "pre", "shortDetail": "Sat"}}}]}

    def test_match_link_must_be_http(self):
        espn = "https://www.espn.com/soccer/match/_/gameId/9"
        self.assertEqual(build.interpret("fifa.friendly.w", self.event([{"href": "javascript:alert(1)"}, {"href": espn}])).link, espn)
        for links in ([{"href": " JaVaScRiPt:alert(1)"}], [{"href": "data:text/html,x"}], [{"href": None}], []):
            self.assertEqual(build.interpret("fifa.friendly.w", self.event(links)).link, "")

    def test_us_national_teams_are_shown_by_default_in_an_off_competition(self):
        self.assertTrue(build.LEAGUES["fifa.friendly.w"].get("default_off"))
        usa = build.interpret("fifa.friendly.w", self.event())
        other = build.interpret("fifa.friendly.w", self.event(home="India"))
        self.assertEqual((build.featured(usa), build.shown_by_default(usa)), (True, True))
        self.assertEqual((build.featured(other), build.shown_by_default(other)), (False, False))
        fixtures = [("usa", "2026-10-07T23:00:00+00:00", "pre", "HBO Max", "fifa.friendly.w"),
                    ("india", "2026-10-07T23:30:00+00:00", "pre", "HBO Max", "fifa.friendly.w")]
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = Path(tmp) / "facts.json"
            page = render_page(build, fixtures=fixtures, facts_path=facts_path,
                               team_names={"usa": ("United States", "Spain"), "india": ("India", "Russia")})
            facts = json.loads(facts_path.read_text())
        rows = {attrs["data-id"]: attrs for tag, attrs in Tags(page).tags if tag == "li" and "data-id" in attrs}
        self.assertEqual(rows["usa"].get("data-featured"), "1")
        self.assertNotIn("data-featured", rows["india"])
        shown = {m["id"]: m.get("default_competition") for m in facts["ranking_candidates"] + facts["next_24_hours"] if "default_competition" in m}
        self.assertEqual(shown, {"usa": True, "india": False})

    def test_details_buttons_name_their_match_and_logos_stay_out_of_the_reading_order(self):
        page = render_page(build)
        buttons = [attrs for tag, attrs in Tags(page).tags if tag == "button" and "more" in attrs.get("class", "").split()]
        self.assertEqual(len(buttons), 9)
        self.assertTrue(all(b["aria-label"] == "Details: Arsenal v Chelsea" for b in buttons))
        team = build.Team("Arsenal", "ARS", "k1", "")
        logo = build.logo_html(team, {"k1": "data:image/png;base64,"})
        self.assertIn('aria-hidden="true"', logo)
        self.assertNotIn("role=", logo)
