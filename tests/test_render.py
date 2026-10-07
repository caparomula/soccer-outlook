"""The standalone page must include its assets regardless of the caller's cwd."""
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
