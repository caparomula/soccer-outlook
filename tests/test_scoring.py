"""The AI switch, the Outlook score and settings.toml, which tunes both.

Expected values are worked out by hand from settings.toml's documented arithmetic, not taken from the
code under test: each test says how its number arises.
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import build
from tests.page_fixture import FIXTURE_SETTINGS, fixture_settings, render_page

SETTINGS = fixture_settings(build)
CHANNELS = {o["label"] for o in build.OUTLETS.values()}


def match(score=100, draw=None, goal_line=None, stage="", ranks=None, channels=(), league="eng.1"):
    home, away = build.Team("Home", "HOM", "", "", id="1"), build.Team("Away", "AWY", "", "", id="2")
    if ranks:
        (home.rank, home.size), (away.rank, away.size) = ranks
    return build.Match(id="m", utc=datetime(2026, 10, 10, 14, tzinfo=timezone.utc), time_valid=True, league=league,
                       comp="Premier League", stage=stage, note="", home=home, away=away, venue="", state="pre",
                       status="", outlets=[build.map_outlet(c, league) for c in channels], rule=None, hint="",
                       service="", basis="none", outlet="", score=score, draw=draw, goal_line=goal_line)


def parts(m):
    return build.outlook_parts(m, SETTINGS)


def load(text):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.toml"
        path.write_text(text, encoding="utf-8")
        return build.load_settings(str(path), CHANNELS)


class SettingsFile(unittest.TestCase):
    def test_the_repository_file_loads(self):
        s = build.load_settings(build.SETTINGS_PATH, CHANNELS)
        self.assertIsInstance(s.ai, bool)
        self.assertEqual(set(s.outlook["weights"]), set(build.OUTLOOK_PARTS))
        self.assertGreater(s.blend["ai"] + s.blend["outlook"], 0)
        self.assertIn(s.model, build.providers.MODELS)        # the choice story.py will act on
        self.assertGreater(s.blend["interest"] + s.blend["league_priority"], 0)

    def test_every_problem_is_listed_at_once(self):
        bad = """
[ai]
enabled = "yes"
design = "everything"
model = "gpt-6.1-sol"
effort = "extreme"
[blend]
ai = 0
outlook = 0
interest = -5
leage_priority = 20
[outlook]
weights = { stature = 40, close = 25, stakes = 15, tv = 10 }
missing = 2
stature_full = 150
draw_from = 0.3
draw_to = 0.1
bottom = 0.6
goals_from = 2.0
goals_to = 4.0
knockout = { "final" = 1.5 }
network = ["NBC", "Channel Nine"]
cable = ["NBC"]
"""
        with self.assertRaises(build.SettingsError) as raised:
            load(bad)
        message = str(raised.exception)
        for expected in ("[ai] enabled: must be true or false",
                         "[ai] design: must be ratings or full",
                         "[ai] effort: gpt-6.1-sol takes none, minimal, low, medium, high, xhigh, max",
                         "[blend]: unknown key leage_priority",
                         "[blend]: missing league_priority",
                         "[blend] ai and outlook: at least one must be above 0",
                         "[blend] interest: must be at least 0",
                         "[outlook] weights: missing goals",
                         "[outlook] missing: must be from 0 to 1",
                         "[outlook] draw_from, draw_to: the first must be below the second",
                         "[outlook] knockout.final: must be from 0 to 1",
                         "[outlook] network: Channel Nine not a channel in rights.toml",
                         "[outlook] network and cable both list NBC"):
            self.assertIn(expected, message)

    def test_not_toml(self):
        with self.assertRaisesRegex(build.SettingsError, r"settings\.toml"):
            load("[ai]\nenabled = \n")

    def test_a_commit_to_the_settings_rebuilds_the_page(self):
        # settings.toml promises that a commit changing it is live within minutes: the push that runs
        # the build must include it, and every module the build and story.py import.
        workflow = (Path(build.__file__).parent / ".github" / "workflows" / "refresh.yml").read_text()
        paths = re.search(r"push:\s*\n\s*branches: \[main\]\s*\n\s*paths: \[([^\]]*)\]", workflow).group(1)
        for name in ("settings.toml", "build.py", "story.py", "providers.py", "rights.toml"):
            self.assertIn(f'"{name}"', paths)

    def test_fixture_settings_match_the_documented_first_values(self):
        self.assertEqual(SETTINGS.blend, {"ai": 50, "outlook": 50, "interest": 80, "league_priority": 20})
        self.assertEqual((SETTINGS.design, SETTINGS.model, SETTINGS.effort, SETTINGS.provider), ("ratings", "gpt-6.1-sol", "low", "openai"))
        self.assertEqual(SETTINGS.outlook["weights"], {"stature": 40, "close": 25, "stakes": 15, "tv": 10, "goals": 10})


class Odds(unittest.TestCase):
    @staticmethod
    def event(odds):
        comp = {"competitors": [
            {"homeAway": "home", "team": {"displayName": "Arsenal", "id": "359", "abbreviation": "ARS"}},
            {"homeAway": "away", "team": {"displayName": "Leeds United", "id": "357", "abbreviation": "LEE"}}],
            "status": {"type": {"state": "pre", "shortDetail": "Sat"}}}
        if odds is not None:
            comp["odds"] = odds
        return {"id": "7", "date": "2026-10-10T11:30:00Z", "competitions": [comp]}

    def test_american_prices(self):
        self.assertAlmostEqual(build.implied_chance(240), 100 / 340)
        self.assertAlmostEqual(build.implied_chance("+240"), 100 / 340)
        self.assertAlmostEqual(build.implied_chance(-120), 120 / 220)
        for even in ("EVEN", " even ", 100, -100):
            self.assertAlmostEqual(build.implied_chance(even), 0.5)
        for bad in (50, -99, 0, None, "", "abc", True, float("inf"), float("nan"), [240]):
            self.assertIsNone(build.implied_chance(bad), bad)

    def test_the_draw_price_and_goal_line_are_read_from_the_event(self):
        m = build.interpret("eng.1", self.event([None, {"drawOdds": {"moneyLine": 240}, "overUnder": 2.5, "details": "ARS -275"}]))
        self.assertAlmostEqual(m.draw, 100 / 340)
        self.assertEqual(m.goal_line, 2.5)
        m = build.interpret("eng.1", self.event([{"drawOdds": None, "overUnder": "3.5"}]))
        self.assertEqual((m.draw, m.goal_line), (None, 3.5))
        for odds in (None, [], "junk", [None], [{"overUnder": True}]):
            m = build.interpret("eng.1", self.event(odds))
            self.assertEqual((m.draw, m.goal_line), (None, None), odds)


class OutlookParts(unittest.TestCase):
    def test_stature_is_the_build_score_over_the_full_mark_capped_at_1(self):
        self.assertAlmostEqual(parts(match(score=100))["stature"], 100 / 150)
        self.assertEqual(parts(match(score=200))["stature"], 1.0)
        self.assertEqual(parts(match(score=0))["stature"], 0.0)

    def test_close_and_goals_follow_the_market(self):
        self.assertAlmostEqual(parts(match(draw=100 / 340))["close"], (100 / 340 - 0.10) / 0.20)
        self.assertEqual(parts(match(draw=0.05))["close"], 0.0)
        self.assertEqual(parts(match(draw=0.35))["close"], 1.0)
        for line, expected in ((2.5, 0.25), (3.5, 0.75), (4.5, 1.0), (1.5, 0.0)):
            self.assertAlmostEqual(parts(match(goal_line=line))["goals"], expected)
        p = parts(match())
        self.assertEqual((p["close"], p["goals"]), (None, None))

    def test_stakes_from_the_table(self):
        # Places run from 1 (first) to 0 (last) over 20 teams, steps of 1/19.
        cases = {((1, 20), (2, 20)): 18 / 19,                  # the top two: the lower one's place
                 ((19, 20), (20, 20)): 0.6 * 18 / 19,          # the bottom two: 0.6 x the upper one's distance from the top
                 ((10, 20), (11, 20)): 9 / 19,                 # mid-table: 9/19 beats 0.6 x 9/19
                 ((1, 20), (20, 20)): 0.0}                     # top against bottom
        for ranks, expected in cases.items():
            self.assertAlmostEqual(parts(match(ranks=ranks))["stakes"], expected, msg=ranks)
        self.assertIsNone(parts(match())["stakes"])
        self.assertIsNone(parts(match(ranks=((1, 1), (1, 1))))["stakes"])

    def test_a_knockout_round_counts_by_its_longest_word(self):
        for stage, expected in (("Semifinals", 0.9), ("Quarterfinals · 2nd Leg", 0.8), ("Final", 1.0),
                                ("Knockout round playoffs", 0.7), ("Round of 16", 0.7)):
            self.assertEqual(parts(match(stage=stage, ranks=((1, 20), (20, 20))))["stakes"], expected, stage)
        self.assertAlmostEqual(parts(match(stage="Group A", ranks=((1, 4), (2, 4))))["stakes"], 2 / 3)

    def test_tv_from_the_channel_lists(self):
        self.assertEqual(parts(match(channels=["NBC"]))["tv"], 1.0)
        self.assertEqual(parts(match(channels=["USA Net"]))["tv"], 0.5)        # ESPN's name for USA Network
        self.assertEqual(parts(match(channels=["Peacock"]))["tv"], 0.0)
        self.assertEqual(parts(match(channels=["Peacock", "NBC"]))["tv"], 1.0)
        self.assertIsNone(parts(match())["tv"])                                # nothing listed yet


class OutlookScore(unittest.TestCase):
    def test_the_weighted_mean_of_the_parts(self):
        p = dict(stature=2 / 3, close=0.9706, stakes=18 / 19, tv=1.0, goals=0.25)
        # 0.40 x 0.66667 + 0.25 x 0.9706 + 0.15 x 0.94737 + 0.10 x 1 + 0.10 x 0.25 = 0.77642
        self.assertEqual(build.outlook_score(p, SETTINGS), 77.6)

    def test_a_missing_part_neither_helps_nor_hurts(self):
        p = dict(stature=1.0, close=None, stakes=None, tv=None, goals=None)
        self.assertEqual(build.outlook_score(p, SETTINGS), 70.0)               # 0.40 + 0.60 x 0.5
        self.assertEqual(build.outlook_score(dict.fromkeys(build.OUTLOOK_PARTS, 0.5), SETTINGS), 50.0)

    def test_weights_count_in_proportion(self):
        doubled = replace(SETTINGS, outlook=dict(SETTINGS.outlook, weights={k: 2 * v for k, v in SETTINGS.outlook["weights"].items()}))
        p = dict(stature=0.3, close=0.8, stakes=None, tv=0.5, goals=0.1)
        self.assertEqual(build.outlook_score(p, doubled), build.outlook_score(p, SETTINGS))

    def test_shares_are_whole_percentages_summing_to_100(self):
        self.assertEqual(build.shares({"a": 40, "b": 25, "c": 15, "d": 10, "e": 10}), {"a": 40, "b": 25, "c": 15, "d": 10, "e": 10})
        thirds = build.shares({"a": 1, "b": 1, "c": 1})
        self.assertEqual((sum(thirds.values()), sorted(thirds.values())), (100, [33, 33, 34]))


class PageScoring(unittest.TestCase):
    @staticmethod
    def footer(page):
        return re.search(r"<footer[^>]*>(.*?)</footer>", page, re.S).group(1)

    def test_rows_carry_the_outlook_score_and_its_parts(self):
        page = render_page(build)
        row = re.search(r'<li class="row [^>]*data-id="upcoming"[^>]*>', page).group(0)
        # The fixture: stature 150 (1.0), no odds (0.5 each), first against last of a two-team table
        # (stakes 0), ESPN+ (streaming: tv 0). 0.40 + 0.25 x 0.5 + 0 + 0 + 0.10 x 0.5 = 0.575.
        self.assertIn('data-outlook="57.5"', row)
        listed = json.loads(re.search(r'data-outlook-parts="([^"]*)"', row).group(1).replace("&quot;", '"'))
        self.assertEqual(listed, {"stature": 100, "close": None, "stakes": 0, "tv": 0, "goals": None})
        odds = render_page(build, odds={"upcoming": (0.26, 3.0)})
        # close (0.26 - 0.10) / 0.20 = 0.8, goals (3.0 - 2.0) / 2.0 = 0.5: 0.40 + 0.25 x 0.8 + 0.10 x 0.5 = 0.65.
        self.assertIn('data-outlook="65"', re.search(r'<li class="row [^>]*data-id="upcoming"[^>]*>', odds).group(0))

    def test_the_switch_reaches_the_page_and_its_explanations(self):
        on, off = render_page(build), render_page(build, ai=False)
        self.assertIn('data-ai="on"', on)
        self.assertIn('data-ai="off"', off)
        blend = json.loads(re.search(r'<script type="application/json" id="scoring">([^<]*)</script>', on).group(1))
        self.assertEqual(blend, {"blend": {"ai": 50, "outlook": 50, "interest": 80, "league_priority": 20}, "dots": [35, 45, 55, 68]})
        self.assertIn("one to five golden dots: two from 35, three from 45, four from 55 and five from 68 out of 100", self.footer(on))
        self.assertIn("gpt-6.1-sol, an OpenAI AI model, rates every match the page shows, from today through the third day after it", self.footer(on))
        self.assertIn("the page shows no AI-written text", self.footer(on))
        self.assertNotIn("Claude", self.footer(on))
        self.assertIn("50% the AI rating and 50% the Outlook score", self.footer(on))
        self.assertIn("80% interest and 20% league priority", self.footer(on))
        self.assertIn("No AI is used on this page", self.footer(off))
        self.assertNotIn("Claude", self.footer(off))
        self.assertIn("80% the Outlook score and 20% league priority", self.footer(off))
        hint = lambda page: re.search(r'id="priority-hint">([^<]*)<', page).group(1)
        self.assertIn("80% match interest (the AI rating and the Outlook score) and 20% this order", hint(on))
        self.assertIn("80% the Outlook score and 20% this order", hint(off))


    def test_the_footer_names_the_model_and_what_it_does(self):
        full = self.footer(render_page(build, design="full", model="claude-opus-5-5"))
        self.assertIn("written by Claude, Anthropic&#x27;s AI model (claude-opus-5-5)", full)
        self.assertIn("a sourced blurb for every rated match", full)
        haiku = self.footer(render_page(build, model="claude-haiku-5-5"))
        self.assertIn("claude-haiku-5-5, an Anthropic AI model, rates every match", haiku)
        gemini = self.footer(render_page(build, model="gemini-3.1-flash-lite"))
        self.assertIn("gemini-3.1-flash-lite, a Google AI model, rates every match", gemini)

    def test_the_ai_table_names_a_model_that_can_do_its_design(self):
        base = FIXTURE_SETTINGS
        for change, expected in (
                (('model = "gpt-6.1-sol"', 'model = "gpt-9"'), "[ai] model: must be one of claude-opus-5-5"),
                (('design = "ratings"', 'design = "full"'), "[ai] model: the full design is Claude's research"),
                (('model = "gpt-6.1-sol"\neffort = "low"', 'model = "claude-haiku-5-5"\neffort = "none"'), "[ai] effort: claude-haiku-5-5 takes low"),
                (('effort = "low"\n', ''), "[ai]: missing effort"),
                (('effort = "low"\n', 'effort = "low"\nbudget = 3\n'), "[ai]: unknown key budget")):
            with self.subTest(expected=expected), self.assertRaises(build.SettingsError) as raised:
                load(base.replace(*change))
            self.assertIn(expected, str(raised.exception))
        ok = load(base.replace('design = "ratings"\nmodel = "gpt-6.1-sol"\neffort = "low"',
                               'design = "full"\nmodel = "claude-sonnet-5-5"\neffort = "medium"'))
        self.assertEqual((ok.design, ok.model, ok.provider), ("full", "claude-sonnet-5-5", "anthropic"))

    def test_the_overview_needs_a_model_that_can_search_and_the_ratings_design(self):
        base = FIXTURE_SETTINGS.replace('effort = "low"\n', 'effort = "low"\noverview_model = "gemini-3.1-pro-preview"\noverview_effort = "medium"\n')
        ok = load(base)
        self.assertEqual((ok.overview_model, ok.overview_effort), ("gemini-3.1-pro-preview", "medium"))
        self.assertEqual((load(FIXTURE_SETTINGS).overview_model, load(FIXTURE_SETTINGS).overview_effort), (None, None))
        for change, expected in (
                (('overview_model = "gemini-3.1-pro-preview"', 'overview_model = "gemini-3.1-flash-lite"'),
                 "[ai] overview_model: the overview is written after a web search, so it must be one of claude-opus-5-5"),
                (('overview_model = "gemini-3.1-pro-preview"', 'overview_model = "claude-haiku-5-5"'), "[ai] overview_model: the overview is written after a web search"),
                (('overview_effort = "medium"', 'overview_effort = "xhigh"'), "[ai] overview_effort: gemini-3.1-pro-preview takes minimal, low, medium, high"),
                (('overview_effort = "medium"\n', ''), "[ai]: overview_model and overview_effort go together"),
                (('design = "ratings"\nmodel = "gpt-6.1-sol"\neffort = "low"', 'design = "full"\nmodel = "claude-opus-5-5"\neffort = "medium"'),
                 "[ai] overview_model: the full design writes its own overview")):
            with self.subTest(expected=expected), self.assertRaises(build.SettingsError) as raised:
                load(base.replace(*change))
            self.assertIn(expected, str(raised.exception))
        # Every OpenAI model searches; so do Claude Sonnet and Opus.
        for model, effort in (("gpt-6-luna", "low"), ("claude-sonnet-5-5", "medium")):
            self.assertEqual(load(base.replace('"gemini-3.1-pro-preview"', f'"{model}"').replace('overview_effort = "medium"', f'overview_effort = "{effort}"')).overview_model, model)

    def test_the_dots_need_four_rising_scores(self):
        for value in ("[35, 45, 55]", "[35, 45, 45, 68]", "[35, 45, 55, 101]", "[0, 45, 55, 68]", '["a", 45, 55, 68]', "[true, 45, 55, 68]", "50"):
            with self.subTest(value=value), self.assertRaises(build.SettingsError) as raised:
                load(FIXTURE_SETTINGS.replace("thresholds = [35, 45, 55, 68]", f"thresholds = {value}"))
            self.assertIn("[dots] thresholds: must be four rising scores from above 0 to 100", str(raised.exception))
        with self.assertRaises(build.SettingsError) as raised:
            load(FIXTURE_SETTINGS.replace("\n[dots]\nthresholds = [35, 45, 55, 68]\n", ""))
        self.assertIn("missing dots", str(raised.exception))
        self.assertEqual(load(FIXTURE_SETTINGS.replace("[35, 45, 55, 68]", "[30, 40.5, 60, 100]")).dots, (30, 40.5, 60, 100))

    def test_the_blurbs_take_the_overviews_rules(self):
        base = FIXTURE_SETTINGS.replace('effort = "low"\n', 'effort = "low"\nblurbs_model = "gpt-6.1-sol"\nblurbs_effort = "medium"\n')
        ok = load(base)
        self.assertEqual((ok.blurbs_model, ok.blurbs_effort, ok.overview_model), ("gpt-6.1-sol", "medium", None))
        for change, expected in (
                (('blurbs_model = "gpt-6.1-sol"', 'blurbs_model = "gemini-3.1-flash-lite"'),
                 "[ai] blurbs_model: the top picks' blurbs are written after a web search, so it must be one of"),
                (('blurbs_effort = "medium"', 'blurbs_effort = "maximal"'), "[ai] blurbs_effort: gpt-6.1-sol takes none, minimal"),
                (('blurbs_effort = "medium"\n', ''), "[ai]: blurbs_model and blurbs_effort go together"),
                (('design = "ratings"\nmodel = "gpt-6.1-sol"\neffort = "low"', 'design = "full"\nmodel = "claude-opus-5-5"\neffort = "medium"'),
                 "[ai] blurbs_model: the full design writes its own blurbs")):
            with self.subTest(expected=expected), self.assertRaises(build.SettingsError) as raised:
                load(base.replace(*change))
            self.assertIn(expected, str(raised.exception))
        with patch.object(build, "SETTINGS", replace(fixture_settings(build), blurbs_model="gpt-6.1-sol", blurbs_effort="medium")):
            footer = build.about_ai()
        self.assertIn("For the cards, gpt-6.1-sol, an OpenAI AI model, searches the web for a blurb on each of the 6 best-scored matches", footer)
        self.assertIn("It is published only with a page its own search returned", footer)
        with patch.object(build, "SETTINGS", replace(fixture_settings(build), blurbs_model="gpt-6.1-sol", blurbs_effort="medium",
                                                     overview_model="gemini-3.1-pro-preview", overview_effort="medium")):
            self.assertIn("Each is published only with a page its own search returned", build.about_ai())

    def test_the_footer_names_the_overview_model_and_its_rule(self):
        no_overview = self.footer(render_page(build))
        self.assertIn("not predicted results, and the page shows no AI-written text.", no_overview)
        with patch.object(build, "SETTINGS", replace(fixture_settings(build), overview_model="gemini-3.1-pro-preview", overview_effort="medium")):
            footer = build.about_ai()
        self.assertIn("Then gemini-3.1-pro-preview, a Google AI model, searches the web and writes the overview at the top (marked AI Summary)", footer)
        self.assertIn("published only with a page its own search returned", footer)
        self.assertNotIn("the page shows no AI-written text", footer)


if __name__ == "__main__":
    unittest.main()
