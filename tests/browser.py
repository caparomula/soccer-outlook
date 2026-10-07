"""Offline Chromium checks; run with python3 -m tests.browser --help."""
import argparse
from copy import deepcopy
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from threading import Thread
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from PIL import Image
from pixelmatch.contrib.PIL import pixelmatch

import build
from tests.page_fixture import BUILT_AT, TODAY, render_page, scoreboard


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class BrowserChecks(unittest.TestCase):
    @contextmanager
    def page(self, target, *, width=1280, theme="light", at="20261007-1300", html=None, story=None):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900 if width > 600 else 844},
            locale="en-US", timezone_id="America/New_York", color_scheme=theme,
            device_scale_factor=1)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        feed = {"data": {"events": []}, "fail": False, "requests": 0}

        def route_request(route):
            url = urlsplit(route.request.url)
            if html is not None and url.path.endswith("/index.html"):
                route.fulfill(content_type="text/html", body=html)
            elif url.path.startswith("/espn/"):
                feed["requests"] += 1
                if feed["fail"]:
                    route.fulfill(status=503, body="Fixture: ESPN unavailable")
                else:
                    route.fulfill(content_type="application/json", body=json.dumps(feed["data"]))
            elif url.path.endswith("/story.json"):
                if story is None:
                    route.fulfill(status=404, body="No fixture story")
                else:
                    route.fulfill(content_type="application/json", body=json.dumps(story))
            elif url.hostname != "127.0.0.1":
                # Stable offline screenshots: use the same fallback fonts on both pages.
                route.fulfill(content_type="text/css", body="")
            else:
                route.continue_()

        context.route("**/*", route_request)
        page.clock.install(time=BUILT_AT)
        page.clock.pause_at(BUILT_AT)
        try:
            page.goto(f"{self.base}/{target}/index.html?scoresbase={self.base}/espn/#at-{at}")
            expect(page.locator("#outlook-sub")).to_contain_text("The next 24 hours")
            page.clock.run_for(1)
            if feed["requests"]:
                expect(page.locator("#livenote")).to_contain_text("last checked")
            if story is not None:
                page.wait_for_load_state("networkidle")
            self.assertEqual(errors, [], f"JavaScript startup errors in {target}")
            yield page, feed
            self.assertEqual(errors, [], f"JavaScript errors in {target}")
        finally:
            context.close()

    @staticmethod
    def bucket(page, match_id):
        return page.locator(f'li.row[data-id="{match_id}"]').evaluate(
            "row => row.closest('.bucket').querySelector('.bucket__h > span').textContent")

    def test_display(self):
        for width in (1280, 390):
            for theme in ("light", "dark"):
                shots = {}
                for target in self.targets:
                    with self.subTest(width=width, theme=theme, target=target), self.page(target, width=width, theme=theme) as (page, _):
                        for state in ("schedule", "lineup", "details"):
                            if state == "lineup":
                                page.locator("#btn-menu").click()
                            elif state == "details":
                                page.keyboard.press("Escape")
                                page.locator('li[data-id="upcoming"] .more').click()
                            page.evaluate("window.scrollTo(0, 0)")
                            # Let scrolling and sticky layers finish painting with the paused clock.
                            page.clock.run_for(50)
                            path = self.artifacts / f"{target}-{width}-{theme}-{state}.png"
                            shots[target, state] = page.screenshot(path=str(path), full_page=True, animations="disabled")
                if "before" in self.targets:
                    for state in ("schedule", "lineup", "details"):
                        with self.subTest(width=width, theme=theme, state=state):
                            if shots["before", state] == shots["after", state]:
                                continue
                            before = Image.open(BytesIO(shots["before", state])).convert("RGBA")
                            after = Image.open(BytesIO(shots["after", state])).convert("RGBA")
                            self.assertEqual(before.size, after.size)
                            diff = Image.new("RGBA", before.size)
                            # Identical Chromium pages can differ at rounded/sticky edges.
                            # Use pixelmatch's standard antialiasing detection and tolerance.
                            changed = pixelmatch(before, after, diff, threshold=0.1)
                            if changed:
                                diff.save(self.artifacts / f"diff-{width}-{theme}-{state}.png")
                            self.assertEqual(changed, 0, f"Screenshot mismatch; inspect {self.artifacts}")

    def test_team_columns_align_with_long_names_and_different_broadcasters(self):
        fixtures = [("a", "2026-10-07T16:30:00+00:00", "in", "ESPN+"),
                    ("b", "2026-10-07T16:30:00+00:00", "in", "CBS Sports Network"),
                    ("c", "2026-10-07T18:00:00+00:00", "pre", "Apple TV"),
                    ("d", "2026-10-07T18:00:00+00:00", "pre", "beIN Sports")]
        names = {"a": ("FC", "Borussia Mönchengladbach"),
                 "b": ("Brighton & Hove Albion", "Paris Saint-Germain"),
                 "c": ("Wolverhampton Wanderers", "New York Red Bulls"),
                 "d": ("Club Atlético Independiente", "Deportivo Riestra")}
        html = render_page(build, fixtures=fixtures, team_names=names)
        for width in (1280, 820, 600, 390, 320):
            with self.subTest(width=width), self.page("after", width=width, html=html) as (page, _):
                page.locator("#btn-all").click()
                geometry = page.locator('li.row:visible').evaluate_all("""rows => rows.map(row => {
                    const teams = [...row.querySelectorAll('.row__teams > .team')];
                    const pos = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, right: r.right}; };
                    return {teams: teams.map(t => pos(t.querySelector('.team__name'))),
                            logos: teams.map(t => pos(t.querySelector('.logo'))),
                            scores: teams.map(t => pos(t.querySelector('.score'))),
                            watch: pos(row.querySelector('.row__watch')),
                            fits: teams.every(t => t.scrollWidth <= t.clientWidth + 1)};
                })""")
                self.assertEqual(len(geometry), 4)
                for side in (0, 1):
                    self.assertLess(max(r['teams'][side]['x'] for r in geometry) - min(r['teams'][side]['x'] for r in geometry), 1)
                    self.assertLess(max(r['logos'][side]['x'] for r in geometry) - min(r['logos'][side]['x'] for r in geometry), 1)
                    self.assertAlmostEqual(geometry[0]['scores'][side]['right'], geometry[1]['scores'][side]['right'], delta=1)
                for row in geometry:
                    self.assertTrue(row['fits'])
                    if width <= 600:
                        self.assertAlmostEqual(row['teams'][0]['x'], row['teams'][1]['x'], delta=1)
                        self.assertGreater(row['teams'][1]['y'], row['teams'][0]['y'])
                    else:
                        self.assertGreater(row['teams'][1]['x'], row['teams'][0]['x'] + 100)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.locator('#outlook').screenshot(path=str(self.artifacts / f"aligned-rows-{width}.png"))

    def test_lineup_persists_and_resets(self):
        for target in self.targets:
            with self.subTest(target=target), self.page(target) as (page, _):
                row = page.locator('li.row[data-id="upcoming"]')
                expect(row).to_have_attribute("data-svc", "espn")
                page.locator("#btn-menu").click()
                page.locator("#btn-clear").click()
                expect(row).to_be_hidden()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                page.locator("#btn-filters-close").click()
                expect(row).to_be_visible()
                page.reload()
                expect(row).to_have_attribute("data-svc", "espn")
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('ssg3-have'))"), ["espn"])
                page.locator("#btn-menu").click()
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(1)
                page.locator("#btn-reset").click()
                self.assertIsNone(page.evaluate("localStorage.getItem('ssg3-have')"))
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(len(build.OWNER))

    def test_midnight_and_sports_day_boundary(self):
        cases = [
            ("20261007-2359", "Tonight", "Tonight", "Tomorrow"),
            ("20261008-0001", "Tonight", "Tonight", "Tomorrow"),
            ("20261008-0359", "Earlier today", "Live now", "Tomorrow"),
            ("20261008-0400", "Yesterday", "Live now", "Live now"),
        ]
        for target in self.targets:
            for at, midnight, late, dawn in cases:
                with self.subTest(target=target, at=at), self.page(target, at=at) as (page, _):
                    self.assertEqual(self.bucket(page, "midnight"), midnight)
                    self.assertEqual(self.bucket(page, "late"), late)
                    self.assertEqual(self.bucket(page, "dawn"), dawn)

    def test_live_score_transitions(self):
        for target in self.targets:
            with self.subTest(target=target), self.page(target) as (page, feed):
                row = page.locator('li.row[data-id="upcoming"]')
                self.assertEqual(self.bucket(page, "upcoming"), "This afternoon")
                expect(page.locator("#schedule-summary")).to_contain_text("1 live · 5 upcoming")
                feed["data"] = scoreboard("in")
                page.clock.run_for(60000)
                expect(row).to_have_attribute("data-state", "in")
                expect(row.locator(".team .score")).to_have_text(["2", "1"])
                expect(row.locator(".row__goals")).to_contain_text("A. Player 63'")
                self.assertEqual(self.bucket(page, "upcoming"), "Live now")
                expect(page.locator("#schedule-summary")).to_contain_text("2 live · 4 upcoming")
                feed["data"] = scoreboard("post")
                page.clock.run_for(60000)
                expect(row).to_have_attribute("data-state", "post")
                expect(row.locator(".row__status")).to_have_text("FT")
                self.assertEqual(self.bucket(page, "upcoming"), "Earlier today")
                expect(page.locator("#schedule-summary")).to_contain_text("1 live · 4 upcoming")
                self.assertGreaterEqual(feed["requests"], 2)

    def test_failed_scoreboard_preserves_scores(self):
        for target in self.targets:
            with self.subTest(target=target), self.page(target) as (page, feed):
                row = page.locator('li.row[data-id="live"]')
                before = row.inner_html()
                feed["fail"] = True
                requests = feed["requests"]
                page.clock.run_for(60000)
                expect(page.locator("#livenote")).to_contain_text("latest check didn’t get through")
                self.assertGreater(feed["requests"], requests)
                self.assertEqual(row.inner_html(), before)
                expect(row).to_have_attribute("data-state", "in")

    def test_factual_summary_and_filters(self):
        with self.page("after") as (page, _):
            summary = page.locator("#schedule-summary")
            expect(summary).to_contain_text("1 live · 5 upcoming in selected competitions")
            expect(summary).to_contain_text("5 listed on your services; 1 with unconfirmed coverage")
            expect(summary).to_contain_text("Next kickoff · 1:05 pm")
            expect(page.locator("#forecast")).to_be_hidden()
            expect(page.locator("#eyebrow")).to_have_text("Wednesday, October 7 · Next 24 hours")
            page.locator("#btn-menu").click()
            page.locator('[data-kind="comp"][data-key="eng.1"]').click()
            expect(summary).to_contain_text("Next 24 hours: 6 matches hidden by competition filters.")
            expect(summary).not_to_contain_text("Next kickoff")
            page.locator('[data-kind="comp"][data-key="eng.1"]').click()
            page.locator("#btn-clear").click()
            expect(summary).to_contain_text("No services selected")

    def test_unlisted_matches_remain_visible_on_my_services(self):
        fixtures = [("unlisted", "2026-10-07T18:00:00+00:00", "pre", None, "fifa.friendly.w"),
                    ("off-lineup", "2026-10-07T19:00:00+00:00", "pre", "Peacock", "eng.1")]
        html = render_page(build, fixtures=fixtures)
        story = self.tagged_story()
        part = {"segments": [{"text": "Relevant match with coverage pending.", "match_ids": ["unlisted"]}], "sources": []}
        story["lede_items"] = [part]
        story["forecast"]["items"] = [part]
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator('li.row[data-id="unlisted"]')).to_be_visible()
            expect(page.locator('li.row[data-id="off-lineup"]')).to_be_hidden()
            expect(page.locator("#schedule-summary")).to_contain_text("1 with unconfirmed coverage")
            expect(page.locator("#forecast")).to_be_hidden()
            expect(page.locator("#story")).to_be_hidden()
            expect(page.locator("#tally-n")).to_have_text("0")
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="fifa.friendly.w"]').click()
            expect(page.locator('li.row[data-id="unlisted"]')).to_be_hidden()
            expect(page.locator("#forecast")).to_be_hidden()

    def test_counts_all_leagues_and_groups_simultaneous_kickoffs(self):
        for leagues in (["usa.1"] * 6 + ["usa.nwsl"] * 6, ["caf.nations"] * 4):
            fixtures = [(str(i), "2026-10-07T18:00:00+00:00", "pre", "ESPN+", league)
                        for i, league in enumerate(leagues)]
            with self.subTest(leagues=leagues), self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
                summary = page.locator("#schedule-summary")
                expect(summary).to_contain_text(f"{len(leagues)} upcoming in selected competitions")
                next_kickoff = summary.locator("p").filter(has_text="Next kickoff")
                expect(next_kickoff).to_have_count(1)
                expect(next_kickoff).to_contain_text("Next kickoff · 2 pm")
                expect(next_kickoff).to_contain_text(f"{len(leagues) - 3} more at this time")
                self.assertNotIn("then", next_kickoff.inner_text())
                expect(page.locator("#forecast")).to_be_hidden()
                self.assertNotRegex(summary.inner_text(), r"quiet|international break|best|pick of|weekend")

    def test_summary_distinguishes_coverage_and_missing_data(self):
        fixtures = [("listed", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("usual", "2026-10-07T18:00:00+00:00", "pre", None, "esp.1"),
                    ("unknown", "2026-10-07T18:00:00+00:00", "pre", "Mystery Sports+", "esp.1"),
                    ("other", "2026-10-07T18:00:00+00:00", "pre", "Peacock", "eng.1")]
        html = render_page(build, fixtures=fixtures, failed=[("eng.1", TODAY)])
        with self.page("after", html=html) as (page, _):
            summary = page.locator("#schedule-summary")
            expect(summary).to_contain_text("1 listed on your services; 1 with usual coverage on your services (not yet listed); 1 with unconfirmed coverage")
            expect(summary).to_contain_text("Some fixtures may be missing")
            page.locator("#btn-all").click()
            expect(summary).to_contain_text("1 more at this time")

    def test_summary_uses_actual_next_date_and_marks_pending_scores(self):
        fixtures = [("late-score", "2026-10-07T16:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("spain", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("germany", "2026-10-11T18:00:00+00:00", "pre", "ESPN+", "ger.1")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
            summary = page.locator("#schedule-summary")
            expect(summary).to_contain_text("1 awaiting score updates")
            expect(summary).to_contain_text("Beyond 24 hours · next kickoff · Friday, October 9, 2 pm")
            self.assertNotRegex(summary.inner_text(), r"returns|wait until|weekend")
        with self.page("after", at="20261008-0001") as (page, _):
            summary = page.locator("#schedule-summary")
            expect(summary).to_contain_text("Next 24 hours:")
            expect(summary).to_contain_text("Next kickoff · 12:30 am")

    @staticmethod
    def tagged_story():
        def item(text, mid):
            return {"text": text, "match_ids": [mid], "segments": [{"text": text, "match_ids": [mid]}], "sources": [{"url": "https://example.com/report", "title": "Report"}]}
        return {"version": 1, "date": "2026-10-07", "generated_at": "2026-10-07T17:00:00Z",
                "focus_until": "2026-10-08T17:00:00Z", "headline": "Fixture headline",
                "headline_segments": [{"text": "Fixture headline", "match_ids": ["mls"]}], "lede": "Fixture lede",
                "notes": {}, "sources": [], "later_reason": "",
                "lede_items": [item("An MLS storyline.", "mls"), item("A Spanish storyline.", "spain")],
                "forecast": {"items": [item("Context for Chicago and Vancouver.", "mls"),
                                       item("Context for the Spanish match.", "spain")]}}

    def test_irrelevant_blurbs_are_hidden_independently_for_leagues_and_services(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        html = render_page(build, fixtures=fixtures).replace('data-home="Arsenal"', 'data-home="Chicago"').replace('data-away="Chelsea"', 'data-away="Vancouver"')
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", html=html, story=self.tagged_story(), width=width) as (page, _):
                editorial = page.locator("#forecast")
                mls = editorial.locator('.editorial-item[data-matches="mls"]')
                spain = editorial.locator('.editorial-item[data-matches="spain"]')
                expect(editorial).to_contain_text("Forecast by Claude")
                expect(mls.locator('[data-kind="team"]')).to_have_text(["Chicago", "Vancouver"])
                expect(mls.locator('[data-kind="comp"]')).to_have_text("MLS")
                expect(mls.locator('[data-kind="broadcaster"]')).to_have_text("Apple TV")
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="netflix"]').click()
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")
                page.locator('#comp-pills [data-key="usa.1"]').click()
                expect(mls).to_have_count(0)
                expect(page.locator('#story-lede .editorial-part[data-matches="mls"]')).to_have_count(0)
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")
                page.locator('#comp-pills [data-key="usa.1"]').click()
                page.locator("#btn-clear").click()
                expect(editorial).to_be_hidden()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")
                expect(mls).to_have_count(0)
                page.locator("#btn-filters-close").click()
                page.screenshot(path=str(self.artifacts / f"tagged-{width}-filtered.png"), full_page=True)
                page.locator("#btn-all").click()
                expect(mls).to_have_count(0)
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")
                page.locator("#btn-menu").click()
                page.locator("#btn-reset").click()
                expect(spain.locator(".editorial-part")).to_have_class("editorial-part")

    def test_one_phrase_dims_without_changing_other_league_or_connecting_words(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("pl", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "eng.1")]
        story = self.tagged_story()
        parts = [{"text": "This evening ", "match_ids": []}, {"text": "MLS", "match_ids": ["mls"]},
                 {"text": " and ", "match_ids": []}, {"text": "the Premier League", "match_ids": ["pl"]},
                 {"text": " have matches.", "match_ids": []}]
        item = {"segments": parts, "sources": []}
        story["lede_items"] = [item]
        story["forecast"]["items"] = [item]
        story["headline_segments"] = parts
        for theme in ("light", "dark"):
            with self.subTest(theme=theme), self.page("after", html=render_page(build, fixtures=fixtures), story=story, theme=theme) as (page, _):
                page.locator("#btn-menu").click()
                # Explicitly select both relevant services, independent of the owner's defaults.
                page.locator("#btn-clear").click()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                page.locator('[data-kind="have"][data-key="apple"]').click()
                for host in ("#forecast", "#story-lede"):
                    expect(page.locator(host + " .editorial-part--filtered")).to_have_count(0)
                page.locator('[data-kind="have"][data-key="apple"]').click()
                for host in ("#forecast", "#story-lede"):
                    expect(page.locator(host + " .editorial-part--filtered")).to_have_text("MLS")
                    expect(page.locator(host + ' .editorial-part[data-matches="pl"]')).to_have_class("editorial-part")
                    expect(page.locator(host + ' .editorial-part[data-matches="mls"]')).to_have_css("text-decoration-line", "none")
                    expect(page.locator(host)).to_contain_text("This evening MLS and the Premier League have matches.")
                page.locator("#btn-filters-close").click()
                page.screenshot(path=str(self.artifacts / f"phrases-{theme}.png"), full_page=True)
                page.locator("#btn-all").click()
                expect(page.locator("#forecast .editorial-part--filtered")).to_have_text("MLS")
                page.locator("#btn-menu").click()
                page.locator('#comp-pills [data-key="usa.1"]').click()
                expect(page.locator("#forecast .editorial-part--filtered")).to_have_text("MLS")

    def test_single_paragraph_storyline_and_time_of_day_heading(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=self.tagged_story()) as (page, _):
                expect(page.locator("p#story-lede")).to_have_text("An MLS storyline. A Spanish storyline.")
                expect(page.locator("#story-lede p, #story-lede div, #story-lede .editorial-tags")).to_have_count(0)
                expect(page.locator('#story-tags [data-kind="comp"]')).to_have_text(["MLS", "La Liga"])
                expect(page.locator("#story-h")).to_have_text("Storyline")
                self.assertEqual(page.locator("#story").evaluate("el => el.nextElementSibling.querySelector('h2').id"), "period-h")
                for at, label in (("0900", "This morning"), ("1300", "This afternoon"), ("1800", "This evening"), ("2100", "Tonight"), ("0100", "Tonight")):
                    page.evaluate("at => location.hash = '#at-20261007-' + at", at)
                    expect(page.locator("#period-h")).to_have_text(label)

    def test_ranked_headline_and_absolute_standouts_follow_filters(self):
        fixtures = [("routine", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("best", "2026-10-07T19:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("near", "2026-10-08T16:59:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("far", "2026-10-10T17:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("unknown", "2026-10-07T20:00:00+00:00", "pre", None, "fifa.friendly.w"),
                    ("finished", "2026-10-07T16:00:00+00:00", "post", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(popularity=score, gameplay=score, impact=score, score=score)
                             for mid, score in (("routine", 60), ("best", 90), ("near", 80), ("far", 95), ("unknown", 100), ("finished", 100))}
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                hero = page.locator("#nextup")
                expect(hero).to_have_attribute("data-match-id", "best")
                expect(page.locator("#nextup-rating")).to_contain_text("90/100")
                expect(page.locator('#misses [data-id="unknown"]')).to_have_count(0)
                self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["far", "best", "near"])
                page.screenshot(path=str(self.artifacts / f"ranked-{width}.png"), full_page=True)
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="apple"]').click()
                expect(hero).to_have_attribute("data-match-id", "near")
                page.locator('#comp-pills [data-key="esp.1"]').click()
                expect(hero).to_have_attribute("data-match-id", "routine")
                expect(page.locator("#picks .pick")).to_have_count(0)
                page.locator("#btn-clear").click()
                expect(hero).to_be_hidden()
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(hero).to_be_hidden()
                expect(page.locator("#picks .pick")).to_have_count(0)

    def test_later_headline_uses_first_available_window_and_finished_pick_is_removed(self):
        fixtures = [("early", "2026-10-09T18:00:00+00:00", "pre", "ESPN+"),
                    ("best", "2026-10-09T20:00:00+00:00", "pre", "ESPN+"),
                    ("too-far", "2026-10-11T18:00:00+00:00", "pre", "ESPN+")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(score=score) for mid, score in (("early", 50), ("best", 70), ("too-far", 90), ("upcoming", 85))}
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "best")
        with self.page("after", story=story) as (page, feed):
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "upcoming")
            expect(page.locator("#picks .pick")).to_have_count(1)
            feed["data"] = scoreboard("post")
            page.clock.run_for(60000)
            expect(page.locator("#nextup")).not_to_have_attribute("data-match-id", "upcoming")
            expect(page.locator("#picks .pick")).to_have_count(0)

    def test_rolling_window_and_unrated_recommendation_fallback(self):
        fixtures = [("inside", "2026-10-08T16:59:00+00:00", "pre", "ESPN+"),
                    ("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+"),
                    ("later", "2026-10-10T17:00:00+00:00", "pre", "ESPN+")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
            expect(page.locator("#tally-n")).to_have_text("1")
            expect(page.locator("#schedule-summary")).to_contain_text("1 upcoming")
            expect(page.locator("#picks .pick")).to_have_count(0)
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "inside")
            expect(page.locator('li.row[data-id="inside"]')).to_be_visible()
            expect(page.locator('li.row[data-id="edge"]')).to_be_hidden()
            expect(page.locator('details[data-b="later"]')).not_to_have_attribute("open", "")
            page.locator('details[data-b="later"] > summary').click()
            expect(page.locator('li.row[data-id="edge"]')).to_be_visible()
            page.evaluate("location.hash = '#at-20261007-1301'")
            expect(page.locator("#tally-n")).to_have_text("2")
            self.assertEqual(self.bucket(page, "edge"), "Tomorrow")
            expect(page.locator('li.row[data-id="edge"]')).to_be_visible()
            expect(page.locator('details[data-b="later"]')).to_have_attribute("open", "")

    def test_later_editorial_only_as_researched_fallback_and_rolls_into_window(self):
        html = render_page(build, fixtures=[
            ("upcoming", "2026-10-07T17:05:00+00:00", "pre", "ESPN+"),
            ("usual", "2026-10-08T18:00:00+00:00", "pre", "ESPN+")])
        story = self.tagged_story()
        story["lede_items"] = []
        near = {"segments": [{"text": "Near context.", "match_ids": ["upcoming"]}], "sources": []}
        later = {"segments": [{"text": "Later context.", "match_ids": ["usual"]}], "sources": []}
        story["forecast"]["items"] = [near, later]
        story["later_reason"] = "No pertinent story found sooner."
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#forecast")).to_contain_text("Near context")
            expect(page.locator("#forecast")).not_to_contain_text("Later context")
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="eng.1"]').click()
            expect(page.locator("#forecast")).to_be_hidden()
        story["forecast"]["items"] = [later]
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#forecast")).to_contain_text("Further ahead")
            page.evaluate("location.hash = '#at-20261007-1401'")
            expect(page.locator("#forecast")).to_contain_text("Next 24 hours")
        story["later_reason"] = ""
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#forecast")).to_be_hidden()

    def test_match_notes_follow_services_even_in_everything_mode(self):
        fixtures = [("peacock", "2026-10-07T18:00:00+00:00", "pre", "Peacock", "eng.1")]
        story = self.tagged_story()
        story["notes"] = {"peacock": {"note": "Match-specific lineup news.", "sources": []}}
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            page.locator("#btn-all").click()
            expect(page.locator('li.row[data-id="peacock"]')).to_be_visible()
            expect(page.locator('.row__story')).to_be_hidden()
            expect(page.locator('.miss__story')).to_have_count(0)
            page.locator("#btn-menu").click()
            page.locator('[data-kind="have"][data-key="peacock"]').click()
            expect(page.locator('.row__story')).to_be_visible()

    def test_filtering_near_news_can_reveal_available_later_fallback(self):
        fixtures = [("near", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("later", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["lede_items"] = []
        story["later_reason"] = "Fallback for lineups without the earlier match."
        story["forecast"]["items"] = [
            {"segments": [{"text": "Near match context.", "match_ids": ["near"]}], "sources": []},
            {"segments": [{"text": "Later match context.", "match_ids": ["later"]}], "sources": []}]
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            expect(page.locator("#forecast")).to_contain_text("Near match context")
            expect(page.locator("#forecast")).not_to_contain_text("Later match context")
            page.locator("#btn-menu").click()
            page.locator('[data-kind="have"][data-key="apple"]').click()
            expect(page.locator("#forecast")).not_to_contain_text("Near match context")
            expect(page.locator("#forecast")).to_contain_text("Later match context")
            expect(page.locator("#forecast")).to_contain_text("Further ahead")
            page.locator('[data-kind="have"][data-key="apple"]').click()
            expect(page.locator("#forecast")).to_contain_text("Near match context")

    def test_legacy_expired_unknown_and_completed_editorial_do_not_leak(self):
        story = self.tagged_story()
        story["lede_items"] = []
        for kind in ("legacy", "expired", "unknown", "completed"):
            changed = deepcopy(story)
            if kind == "legacy":
                changed["forecast"] = {"label": "Old", "today": "Untagged", "ahead": "Untagged"}
            elif kind == "expired":
                changed["focus_until"] = "2026-10-07T16:59:00Z"
            else:
                changed["forecast"]["items"] = [{"segments": [{"text": "Stale", "match_ids": ["missing" if kind == "unknown" else "finished"]}]}]
            with self.subTest(kind=kind), self.page("after", story=changed) as (page, _):
                expect(page.locator("#forecast")).to_be_hidden()
                expect(page.locator("#schedule-summary")).to_contain_text("1 live · 5 upcoming")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-build", type=Path,
                        help="original build.py, beside its rights.toml (and web/ if applicable)")
    parser.add_argument("--artifacts", type=Path, default=Path("work/browser"))
    args = parser.parse_args()
    pages = {"after": render_page(build)}
    if args.baseline_build:
        spec = importlib.util.spec_from_file_location("baseline_build", args.baseline_build.resolve())
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        pages = {"before": render_page(baseline), **pages}
        if pages["before"] != pages["after"] or render_page(baseline, fragment=True) != render_page(build, fragment=True):
            raise AssertionError("Baseline and refactored HTML differ")
        print("Baseline and refactored document + fragment HTML are byte-for-byte identical.", flush=True)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        for name, html in pages.items():
            folder = Path(directory) / name
            folder.mkdir()
            (folder / "index.html").write_text(html, encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=directory))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        executable = os.environ.get("BROWSER_EXECUTABLE") or shutil.which("chromium")
        browser = playwright.chromium.launch(executable_path=executable)
        try:
            BrowserChecks.browser = browser
            BrowserChecks.base = f"http://127.0.0.1:{server.server_port}"
            BrowserChecks.targets = list(pages)
            BrowserChecks.artifacts = args.artifacts.resolve()
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BrowserChecks))
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
