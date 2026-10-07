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
import re
import shutil
import sys
import tempfile
from threading import Thread
import unittest
from urllib.parse import quote, urlsplit

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
    def page(self, target, *, width=1280, theme="light", at="20261007-1300", html=None, story=None, touch=False):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900 if width > 600 else 844},
            locale="en-US", timezone_id="America/New_York", color_scheme=theme,
            device_scale_factor=1, has_touch=touch)
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

    def drag_filter(self, page, source, target, *, touch=False, cancel=False):
        """Exercise pointer capture and edge scrolling, including destinations off screen."""
        grip = page.locator(source).locator('.fpill__grip')
        grip.scroll_into_view_if_needed()
        start = grip.bounding_box()
        x, y = start['x'] + start['width'] / 2, start['y'] + start['height'] / 2
        session = page.context.new_cdp_session(page) if touch else None

        def move(x, y):
            if touch:
                session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x, 'y': y}]})
            else:
                page.mouse.move(x, y, steps=3)

        if touch:
            session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y}]})
        else:
            page.mouse.move(x, y)
            page.mouse.down()
        move(x + 12, y)
        expect(page.locator('.fpill--drag-ghost')).to_have_count(1)
        for _ in range(35):
            destination = page.locator(target).bounding_box()
            panel = page.locator('#drawer').bounding_box()
            tx, ty = destination['x'] + 3, destination['y'] + min(15, destination['height'] / 2)
            if panel['y'] + 45 <= ty <= panel['y'] + panel['height'] - 75:
                move(tx, ty)
                break
            edge_y = panel['y'] + 20 if ty < panel['y'] + 45 else panel['y'] + panel['height'] - 60
            move(panel['x'] + panel['width'] / 2, edge_y)
            page.clock.run_for(180)
        else:
            self.fail('Drag did not scroll the destination into view')
        if cancel:
            page.keyboard.press('Escape')
        if touch:
            session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
            session.detach()
        else:
            page.mouse.up()
        page.clock.run_for(1)
        expect(page.locator('.fpill--drag-ghost')).to_have_count(0)
        expect(page.locator('.is-drop-area, .is-drop-target')).to_have_count(0)

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

    def test_compact_team_format_wraps_long_names_without_overflow(self):
        fixtures = [("a", "2026-10-07T16:30:00+00:00", "in", "ESPN+"),
                    ("b", "2026-10-07T16:30:00+00:00", "in", "CBS Sports Network"),
                    ("c", "2026-10-07T18:00:00+00:00", "pre", "Apple TV"),
                    ("d", "2026-10-07T18:00:00+00:00", "pre", "beIN Sports")]
        names = {"a": ("FC", "Borussia Mönchengladbach"),
                 "b": ("Brighton & Hove Albion", "Paris Saint-Germain"),
                 "c": ("Wolverhampton Wanderers", "New York Red Bulls"),
                 "d": ("Club Atlético Independiente", "Deportivo Riestra")}
        html = render_page(build, fixtures=fixtures, team_names=names, league_logos=True)
        for width in (1280, 820, 600, 390, 320):
            with self.subTest(width=width), self.page("after", width=width, html=html) as (page, _):
                page.locator("#btn-all").click()
                expect(page.locator('.hdr #controls .seg')).to_have_count(1)
                expect(page.locator('.hdr #btn-menu')).to_have_count(1)
                title, toggle, menu, tally = (page.locator(selector).bounding_box() for selector in ('.hdr h1', '#controls .seg', '#btn-menu', '.hdr__tally'))
                if width > 900:
                    self.assertLessEqual(title['x'] + title['width'], toggle['x'])
                    self.assertLessEqual(toggle['x'] + toggle['width'], menu['x'])
                    self.assertLessEqual(menu['x'] + menu['width'], tally['x'])
                else:
                    self.assertGreaterEqual(toggle['y'], title['y'] + title['height'])
                    self.assertTrue(menu['x'] >= toggle['x'] + toggle['width'] or menu['y'] >= toggle['y'] + toggle['height'])
                page.locator('#btn-menu').click()
                panel = page.locator('#drawer').bounding_box()
                self.assertGreaterEqual(panel['y'], menu['y'] + menu['height'])
                self.assertLessEqual(panel['x'] + panel['width'], width)
                self.assertLessEqual(panel['y'] + panel['height'], page.viewport_size['height'])
                page.locator('#btn-filters-close').click()
                expect(page.locator('li.row:visible')).to_have_count(4)
                for row in page.locator('li.row:visible').all():
                    expect(row.locator('.row__teams')).to_have_css('display', 'flex')
                    expect(row.locator('.team').first).to_have_css('display', 'flex')
                    expect(row.locator('.vs')).to_be_visible()
                    clock = row.locator('.row__time').bounding_box()
                    kickoff = row.locator('.row__time .t').bounding_box()
                    emblem = row.locator('.row__league .lg').bounding_box()
                    teams = row.locator('.row__teams').bounding_box()
                    self.assertAlmostEqual(emblem['x'], clock['x'], delta=1)
                    self.assertLessEqual(emblem['y'] + emblem['height'], kickoff['y'])
                    self.assertGreaterEqual(teams['x'], clock['x'] + clock['width'])
                    expect(row.locator('.row__time .row__league')).to_have_count(1)
                    expect(row.locator('.row__meta .lg')).to_have_count(0)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.locator('#outlook').screenshot(path=str(self.artifacts / f"compact-rows-{width}.png"))

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

    def test_bulk_filters_are_independent_persist_and_reset_to_owner_defaults(self):
        enabled_services = {"hbo", "fox", "para", "espn", "apple", "usa", "prime", "netflix", "disney"}
        hidden_leagues = {"fifa.friendly.w", "usa.usl.1", "usa.usl.l1", "usa.nwsl",
                          "ned.1", "fra.1", "uefa.europa.conf", "uefa.europa"}
        leagues = ["eng.1", "esp.1", *sorted(hidden_leagues)]
        html = render_page(build, fixtures=[
            (f"match-{i}", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", league)
            for i, league in enumerate(leagues)])

        def keys(page, kind, pressed):
            return set(page.locator(f'[data-kind="{kind}"][aria-pressed="{pressed}"]').evaluate_all(
                "buttons => buttons.map(b => b.dataset.key)"))

        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=html) as (page, _):
                self.assertEqual(keys(page, "have", "true"), enabled_services)
                self.assertEqual(keys(page, "comp", "false"), hidden_leagues)
                expect(page.locator('li.row:visible')).to_have_count(2)
                page.locator("#btn-menu").click()
                expect(page.locator("#filter-sum")).to_have_text("9 services, 8 competitions hidden")
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                page.locator("#drawer").screenshot(path=str(self.artifacts / f"bulk-filters-{width}.png"))

                page.get_by_role("button", name="Select all broadcasters", exact=True).click()
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(len(build.SERVICES))
                self.assertEqual(keys(page, "comp", "false"), hidden_leagues)
                page.get_by_role("button", name="Clear all leagues", exact=True).click()
                expect(page.locator('li.row:visible')).to_have_count(0)
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(len(build.SERVICES))
                page.reload()
                self.assertEqual(keys(page, "comp", "false"), set(leagues))
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(len(build.SERVICES))

                page.locator("#btn-menu").click()
                page.get_by_role("button", name="Select all leagues", exact=True).click()
                expect(page.locator('li.row:visible')).to_have_count(len(leagues))
                page.get_by_role("button", name="Clear all broadcasters", exact=True).click()
                expect(page.locator('li.row:visible')).to_have_count(0)
                self.assertEqual(keys(page, "comp", "true"), set(leagues))
                page.reload()
                expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(0)
                self.assertEqual(keys(page, "comp", "true"), set(leagues))

                page.locator("#btn-all").click()
                expect(page.locator('li.row:visible')).to_have_count(len(leagues))
                page.locator("#btn-menu").click()
                page.get_by_role("button", name="Reset to defaults", exact=True).click()
                self.assertEqual(keys(page, "have", "true"), enabled_services)
                self.assertEqual(keys(page, "comp", "false"), hidden_leagues)
                expect(page.locator("#btn-mine")).to_have_attribute("aria-pressed", "true")
                expect(page.locator('li.row:visible')).to_have_count(2)
                page.reload()
                self.assertEqual(keys(page, "have", "true"), enabled_services)
                self.assertEqual(keys(page, "comp", "false"), hidden_leagues)
                expect(page.locator('li.row:visible')).to_have_count(2)

    def test_filter_groups_drag_between_areas_sort_persist_and_accept_empty_drops(self):
        leagues = ['eng.1', 'esp.1', 'fra.1', 'uefa.europa']
        html = render_page(build, fixtures=[(lg, '2026-10-07T18:00:00+00:00', 'pre', 'ESPN+', lg) for lg in leagues], league_logos=True)
        for width in (1280, 390):
            with self.subTest(width=width), self.page('after', width=width, html=html, touch=width <= 600) as (page, _):
                touch = width <= 600
                page.locator('#btn-menu').click()

                def alphabetical(kind):
                    names = build.SERVICES if kind == 'have' else {lg: info['name'] for lg, info in build.LEAGUES.items()}
                    ids = page.locator(f'#{kind}-disabled .fpill').evaluate_all('els => els.map(el => el.dataset.key)')
                    labels = [names[key] for key in ids]
                    self.assertEqual(labels, sorted(labels, key=str.casefold))

                expect(page.locator('#have-enabled .fpill')).to_have_count(len(build.OWNER))
                expect(page.locator('#comp-disabled .fpill')).to_have_count(2)
                alphabetical('have'); alphabetical('comp')
                league = '#comp-pills [data-key="eng.1"]'
                off = '.filter-area[data-filter-kind="comp"][data-enabled="false"]'
                on = '.filter-area[data-filter-kind="comp"][data-enabled="true"]'
                self.drag_filter(page, league, off, touch=touch)
                expect(page.locator('#comp-disabled [data-key="eng.1"]')).to_have_attribute('aria-pressed', 'false')
                expect(page.locator('li.row[data-id="eng.1"]')).to_be_hidden()
                alphabetical('comp')
                page.reload()
                expect(page.locator('#comp-disabled [data-key="eng.1"]')).to_have_count(1)
                page.locator('#btn-menu').click()
                self.drag_filter(page, league, '#comp-enabled [data-key="esp.1"]', touch=touch)
                expect(page.locator('#comp-enabled .fpill').first).to_have_attribute('data-key', 'eng.1')
                expect(page.locator('li.row[data-id="eng.1"]')).to_be_visible()
                page.locator('#btn-clear-leagues').click()
                expect(page.locator('#comp-enabled .fpill')).to_have_count(0)
                self.drag_filter(page, league, on, touch=touch)
                expect(page.locator('#comp-enabled .fpill')).to_have_attribute('data-key', 'eng.1')
                page.locator('#btn-select-leagues').click()
                expect(page.locator('#comp-disabled .fpill')).to_have_count(0)
                self.drag_filter(page, '#comp-pills [data-key="esp.1"]', off, touch=touch)
                alphabetical('comp')
                self.drag_filter(page, league, off, touch=touch, cancel=True)
                expect(page.locator('#drawer')).to_be_hidden()
                expect(page.locator('#comp-enabled [data-key="eng.1"]')).to_have_count(1)
                page.locator('#btn-menu').click()
                self.drag_filter(page, league, '.filter-area[data-filter-kind="have"][data-enabled="false"]', touch=touch)
                expect(page.locator('#comp-enabled [data-key="eng.1"]')).to_have_count(1)
                network = '#have-pills [data-key="espn"]'
                self.drag_filter(page, network, '.filter-area[data-filter-kind="have"][data-enabled="false"]', touch=touch)
                expect(page.locator('#have-disabled [data-key="espn"]')).to_have_attribute('aria-pressed', 'false')
                expect(page.locator('li.row[data-id="eng.1"]')).to_be_hidden()
                alphabetical('have')
                page.locator('#btn-clear').click()
                self.drag_filter(page, network, '.filter-area[data-filter-kind="have"][data-enabled="true"]', touch=touch)
                expect(page.locator('#have-enabled .fpill')).to_have_attribute('data-key', 'espn')
                expect(page.locator('li.row[data-id="eng.1"]')).to_be_visible()
                page.locator('#have-disabled [data-key="espnplus"]').click()
                preferred = page.locator('#have-enabled [data-key="espnplus"]')
                preferred.focus(); preferred.press('Alt+ArrowUp')
                expect(page.locator('#have-enabled .fpill').first).to_have_attribute('data-key', 'espnplus')
                expect(page.locator('li.row[data-id="eng.1"]')).to_have_attribute('data-svc', 'espnplus')
                page.reload()
                expect(page.locator('#have-enabled .fpill').first).to_have_attribute('data-key', 'espnplus')
                expect(page.locator('#comp-disabled .fpill')).to_have_attribute('data-key', 'esp.1')
                page.locator('#btn-menu').click()
                page.locator('#btn-reset').click()
                self.assertIsNone(page.evaluate("localStorage.getItem('ssg4-service-order')"))
                expect(page.locator('#have-enabled .fpill')).to_have_count(len(build.OWNER))
                expect(page.locator('#comp-disabled .fpill')).to_have_count(2)
                alphabetical('have'); alphabetical('comp')
                self.assertLessEqual(page.locator('#drawer').evaluate('el => el.scrollWidth - el.clientWidth'), 1)
                page.locator('#comp-enabled-h').scroll_into_view_if_needed()
                page.screenshot(path=str(self.artifacts / f'grouped-filters-{width}.png'))

    def test_icon_contrast_only_boosts_dark_artwork_and_ignores_transparent_padding(self):
        def svg(body):
            return 'data:image/svg+xml,' + quote('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48">' + body + '</svg>')
        dark = svg('<rect width="48" height="48" fill="#23102b"/>')
        # A tiny, bright red mark surrounded by transparency should not be considered dark.
        bright = svg('<rect x="20" y="20" width="8" height="8" fill="#ff534b"/>')
        mixed = svg('<rect width="48" height="48" fill="#111"/><rect width="16" height="48" fill="white"/>')
        fixtures = [('dark-league', '2026-10-07T18:00:00+00:00', 'pre', 'ESPN+', 'eng.1'),
                    ('bright-league', '2026-10-07T19:00:00+00:00', 'pre', 'ESPN+', 'esp.1')]
        html = render_page(build, fixtures=fixtures, league_logos=True)
        keys = iter(['l-dark-team', 'l-bright-team', 'l-mixed-team', 'l-dark-team'])
        html = re.sub(r'<i class="logo logo--txt"[^>]*>.*?</i>', lambda _: '<i class="logo ' + next(keys) + '"></i>', html)
        css = ''.join(f'.{key}{{background-image:url("{url}")}}' for key, url in [('l-L0', dark), ('l-L1', bright), ('l-dark-team', dark), ('l-bright-team', bright), ('l-mixed-team', mixed)])
        html = html.replace('<script type="application/json"', '<style>' + css + '</style><script type="application/json"')
        with self.page('after', theme='dark', html=html) as (page, _):
            for key in ('l-dark-team', 'l-L0'):
                expect(page.locator('#picks .' + key).first).to_have_css('filter', 'contrast(0.5) brightness(1.8) saturate(0.85)')
            for key in ('l-bright-team', 'l-mixed-team', 'l-L1'):
                expect(page.locator('#picks .' + key).first).to_have_css('filter', 'none')
            for icon in page.locator('#picks .lg, #picks .logo').all():
                expect(icon).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
                expect(icon).to_have_css('border-radius', '0px')
            page.emulate_media(color_scheme='light')
            expect(page.locator('#picks .l-dark-team').first).to_have_css('filter', 'brightness(1)')
            expect(page.locator('#picks .l-bright-team').first).to_have_css('filter', 'none')

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
            expect(page.locator("#forecast")).to_have_text("5 matches in the next 24 hours on your services.")
            expect(page.locator("#eyebrow")).to_have_text("Wednesday, October 7 · Next 24 hours")
            page.locator("#btn-menu").click()
            page.locator('[data-kind="comp"][data-key="eng.1"]').click()
            expect(summary).to_contain_text("Next 24 hours: 6 matches hidden by competition filters.")
            expect(summary).not_to_contain_text("Next kickoff")
            page.locator('[data-kind="comp"][data-key="eng.1"]').click()
            page.locator("#btn-clear").click()
            expect(summary).to_contain_text("No services selected")

    def test_unconfirmed_matches_only_appear_under_everything(self):
        fixtures = [("unlisted", "2026-10-07T18:00:00+00:00", "pre", None, "fifa.friendly.w"),
                    ("unknown", "2026-10-07T18:00:00+00:00", "pre", "Mystery Sports+", "esp.1"),
                    ("off-lineup", "2026-10-07T19:00:00+00:00", "pre", "Peacock", "eng.1"),
                    ("usual-off-lineup", "2026-10-07T19:00:00+00:00", "pre", None, "eng.1"),
                    ("later", "2026-10-10T18:00:00+00:00", "pre", "ESPN+", "esp.1")]
        html = render_page(build, fixtures=fixtures)
        story = self.tagged_story()
        part = {"segments": [{"text": "Relevant match with coverage pending.", "match_ids": ["unlisted"]}], "sources": []}
        story["lede_items"] = [part]
        story["forecast"]["items"] = [part]
        with self.page("after", html=html, story=story) as (page, _):
            # Exercise broadcast availability independently of the default league exclusions.
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="fifa.friendly.w"]').click()
            page.locator("#btn-filters-close").click()
            for mid in ("unlisted", "unknown", "off-lineup", "usual-off-lineup"):
                expect(page.locator(f'li.row[data-id="{mid}"]')).to_be_hidden()
            expect(page.locator("#schedule-summary")).to_contain_text("with unconfirmed coverage")
            expect(page.locator("#outlook-body > .empty")).to_contain_text("No matches on your selected services and competitions in the next 24 hours.")
            self.assertTrue(page.locator("#outlook-body").evaluate("el => el.firstElementChild.classList.contains('empty')"))
            expect(page.locator("#forecast .editorial-item")).to_have_count(0)
            expect(page.locator("#story")).to_be_hidden()
            expect(page.locator("#tally-n")).to_have_text("0")
            page.locator("#btn-all").click()
            for mid in ("unlisted", "unknown", "off-lineup", "usual-off-lineup"):
                expect(page.locator(f'li.row[data-id="{mid}"]')).to_be_visible()
            expect(page.locator("#forecast .editorial-item")).to_have_count(0)
            expect(page.locator("#story")).to_be_hidden()
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="fifa.friendly.w"]').click()
            expect(page.locator('li.row[data-id="unlisted"]')).to_be_hidden()
            expect(page.locator("#forecast .editorial-item")).to_have_count(0)
            page.locator("#btn-filters-close").click()
            page.locator("#btn-mine").click()
            expect(page.locator('li.row[data-id="unknown"]')).to_be_hidden()

    def test_counts_all_leagues_and_groups_simultaneous_kickoffs(self):
        for leagues in (["usa.1"] * 6 + ["usa.nwsl"] * 6, ["caf.nations"] * 4):
            fixtures = [(str(i), "2026-10-07T18:00:00+00:00", "pre", "ESPN+", league)
                        for i, league in enumerate(leagues)]
            with self.subTest(leagues=leagues), self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
                page.locator("#btn-menu").click()
                page.get_by_role("button", name="Select all leagues", exact=True).click()
                page.locator("#btn-filters-close").click()
                summary = page.locator("#schedule-summary")
                expect(summary).to_contain_text(f"{len(leagues)} upcoming in selected competitions")
                next_kickoff = summary.locator("p").filter(has_text="Next kickoff")
                expect(next_kickoff).to_have_count(1)
                expect(next_kickoff).to_contain_text("Next kickoff · 2 pm")
                expect(next_kickoff).to_contain_text(f"{len(leagues) - 3} more at this time")
                self.assertNotIn("then", next_kickoff.inner_text())
                expect(page.locator("#forecast .editorial-item")).to_have_count(0)
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

    def test_one_section_blurb_changes_with_competitions_and_services(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["lede_items"] = []
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", html=render_page(build, fixtures=fixtures), story=story, width=width) as (page, _):
                editorial = page.locator("#forecast")
                expect(editorial.locator('.editorial-item')).to_have_count(1)
                expect(editorial).to_contain_text("Context for Chicago and Vancouver.")
                expect(editorial.locator('.editorial-tags, .editorial-tag')).to_have_count(0)
                expect(editorial.locator('.editorial-part[data-matches="mls"]')).to_have_text("Context for Chicago and Vancouver.")
                page.locator("#btn-menu").click()
                page.locator('#comp-pills [data-key="usa.1"]').click()
                expect(editorial).to_contain_text("Context for the Spanish match.")
                expect(editorial.locator('.editorial-item')).to_have_count(1)
                page.locator("#btn-clear").click()
                expect(editorial).to_be_hidden()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(editorial).to_contain_text("Context for the Spanish match.")
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(editorial).to_contain_text("Context for the Spanish match.")
                expect(editorial).not_to_contain_text("Context for Chicago")

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
                for host in ("#story-lede",):
                    expect(page.locator(host + " .editorial-part--filtered")).to_have_count(0)
                page.locator('[data-kind="have"][data-key="apple"]').click()
                for host in ("#story-lede",):
                    expect(page.locator(host + " .editorial-part--filtered")).to_have_text("MLS")
                    expect(page.locator(host + ' .editorial-part[data-matches="pl"]')).to_have_class("editorial-part")
                    expect(page.locator(host + ' .editorial-part[data-matches="mls"]')).to_have_css("text-decoration-line", "none")
                    expect(page.locator(host)).to_contain_text("This evening MLS and the Premier League have matches.")
                page.locator("#btn-filters-close").click()
                page.screenshot(path=str(self.artifacts / f"phrases-{theme}.png"), full_page=True)
                page.locator("#btn-all").click()
                expect(page.locator("#story-lede .editorial-part--filtered")).to_have_text("MLS")
                page.locator("#btn-menu").click()
                page.locator('#comp-pills [data-key="usa.1"]').click()
                expect(page.locator("#story-lede .editorial-part--filtered")).to_have_text("MLS")

    def test_main_paragraph_leads_directly_to_cards(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["league_blurbs"] = [dict(story["lede_items"][0], league_id="usa.1", interest=100)]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                expect(page.locator("p#story-lede")).to_have_text("An MLS storyline. A Spanish storyline.")
                expect(page.locator("#story-lede p, #story-lede div, #story-lede .editorial-tags")).to_have_count(0)
                expect(page.locator('#story-tags, .editorial-tags, .editorial-tag')).to_have_count(0)
                expect(page.locator("#period-h")).to_have_count(0)
                self.assertEqual(page.locator("#story").evaluate("el => el.nextElementSibling.id"), "nextup")
                self.assertEqual(page.locator("#nextup").evaluate("el => el.nextElementSibling.id"), "picks-section")
                expect(page.locator("#picks-section")).to_be_visible()
                expect(page.locator("#nextup")).to_be_hidden()
                expect(page.locator("#schedule-summary")).to_be_hidden()
                expect(page.locator("#schedule-info")).not_to_have_attribute("open", "")
                # The second section does not repeat the opening's fixture coverage.
                expect(page.locator("#forecast .editorial-item")).to_have_count(0)
                self.assertEqual(page.locator("#forecast").evaluate("el => el.nextElementSibling.querySelector('h2').id"), "outlook-h")
                hero = page.locator("#picks .pick").first.bounding_box()
                self.assertLess(hero["y"] + hero["height"], page.viewport_size["height"])
                page.screenshot(path=str(self.artifacts / f"compact-opening-{width}.png"), full_page=True)

    def test_league_blurbs_choose_interest_after_time_and_service_filters(self):
        fixtures = [("upcoming", "2026-10-07T17:05:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("mls", "2026-10-07T19:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("off", "2026-10-07T20:00:00+00:00", "pre", "Peacock", "usa.nwsl"),
                    ("pl", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("france", "2026-10-09T20:00:00+00:00", "pre", "FS1", "fra.1"),
                    ("far", "2026-10-16T18:00:00+00:00", "pre", "ESPN+", "ita.1")]
        story = self.tagged_story()
        story["lede_items"], story["forecast"] = [], {"items": []}
        story["league_blurbs"] = [dict(league_id=league, interest=interest,
                                            segments=[{"text": text, "match_ids": [mid]}], sources=[])
                                   for mid, league, interest, text in (
                                       ("upcoming", "esp.1", 50, "Spanish match context."),
                                       ("mls", "usa.1", 80, "MLS match context."),
                                       ("off", "usa.nwsl", 99, "Unavailable match context."),
                                       ("pl", "eng.1", 95, "Friday Premier League context."),
                                       ("france", "fra.1", 90, "Friday French match context."),
                                       ("far", "ita.1", 100, "Much later Italian context."))]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, feed):
                # Exercise news ranking independently of the owner's league defaults.
                page.locator("#btn-menu").click()
                page.get_by_role("button", name="Select all leagues", exact=True).click()
                page.locator("#btn-filters-close").click()
                lede = page.locator("p#story-lede")
                expect(lede).to_have_text("MLS match context.")
                expect(page.locator("#story-lede .editorial-item")).to_have_count(1)
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="apple"]').click()
                expect(lede).to_have_text("Spanish match context.")
                feed["data"] = scoreboard("post")
                page.clock.run_for(60000)
                expect(lede).to_have_text("Friday Premier League context.")
                expect(page.locator("#story-h")).to_have_text("Overview · Further ahead")
                page.locator('#comp-pills [data-key="eng.1"]').click()
                expect(lede).to_have_text("Friday French match context.")
                page.locator("#btn-filters-close").click()
                page.screenshot(path=str(self.artifacts / f"league-fallback-{width}.png"), full_page=True)
                page.locator("#btn-menu").click()
                page.locator("#btn-clear").click()
                expect(page.locator("#story")).to_be_hidden()
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(page.locator("#story")).to_be_hidden()
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="fox"]').click()
                expect(lede).to_have_text("Friday French match context.")
                page.locator('[data-kind="have"][data-key="fox"]').click()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(lede).to_have_text("Much later Italian context.")
                expect(page.locator("#nextup")).to_be_hidden()
                expect(page.locator("#picks .pick")).to_have_attribute("data-match-id", "far")
                expect(page.locator('li.row[data-id="far"]')).to_have_count(1)

    def test_top_three_follow_ratings_filters_and_near_window(self):
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
                expect(hero).to_be_hidden()
                expect(page.locator("#picks-section")).to_be_visible()
                expect(page.locator('#misses [data-id="unknown"]')).to_have_count(0)
                self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["best", "near", "routine"])
                page.screenshot(path=str(self.artifacts / f"ranked-{width}.png"), full_page=True)
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="apple"]').click()
                self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["far", "near", "routine"])
                page.locator('#comp-pills [data-key="esp.1"]').click()
                expect(page.locator("#picks .pick")).to_have_attribute("data-match-id", "routine")
                expect(page.locator("#picks-section")).to_be_visible()
                page.locator("#btn-clear").click()
                expect(hero).to_be_hidden()
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(hero).to_be_hidden()
                expect(page.locator("#picks .pick")).to_have_count(0)
                expect(page.locator("#picks-section")).to_be_hidden()

    def test_later_picks_fill_from_nearest_windows_and_finished_match_is_removed(self):
        fixtures = [("early", "2026-10-09T18:00:00+00:00", "pre", "ESPN+"),
                    ("best", "2026-10-09T20:00:00+00:00", "pre", "ESPN+"),
                    ("too-far", "2026-10-11T18:00:00+00:00", "pre", "ESPN+")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(score=score) for mid, score in (("early", 50), ("best", 70), ("too-far", 90), ("upcoming", 85))}
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            expect(page.locator("#nextup")).to_be_hidden()
            self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["too-far", "best", "early"])
        with self.page("after", story=story) as (page, feed):
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "live")
            expect(page.locator('#picks [data-match-id="upcoming"]')).to_have_count(1)
            feed["data"] = scoreboard("post")
            page.clock.run_for(60000)
            expect(page.locator("#nextup")).not_to_have_attribute("data-match-id", "upcoming")
            expect(page.locator('#picks [data-match-id="upcoming"]')).to_have_count(0)
            expect(page.locator("#picks .pick")).to_have_count(3)

    def test_live_feature_uses_blended_score_is_unique_and_updates_on_final(self):
        fixtures = [("upcoming", "2026-10-07T16:30:00+00:00", "in", "ESPN+", "eng.1"),
                    ("live-second", "2026-10-07T16:35:00+00:00", "in", "ESPN+", "esp.1"),
                    ("mls", "2026-10-07T19:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("future", "2026-10-07T20:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("fourth", "2026-10-07T21:00:00+00:00", "pre", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story["league_order"] = ["eng.1", "esp.1", "usa.1"]
        story["rankings"] = {mid: dict(score=score, blurb=f"Specific context for {mid}.", sources=[{"url": "https://example.com/report"}])
                             for mid, score in (("upcoming", 80), ("live-second", 80.5), ("mls", 90), ("future", 70), ("fourth", 40))}
        story['rankings']['live-second']['blurb'] = 'A longer match preview with team news and recent form, demonstrating that every card keeps its color bar aligned even when the commentary takes several more lines than its neighboring cards.'
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures, league_logos=True), story=story) as (page, feed):
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'upcoming')
                expect(page.locator('#nextup-status')).to_contain_text('Live now')
                expect(page.locator('#nextup-rating')).to_have_text('Pick score · 84/100')
                self.assertEqual(page.locator('#picks .pick').evaluate_all('els => els.map(e => e.dataset.matchId)'), ['mls', 'live-second', 'future'])
                expect(page.locator('#picks .pick__story')).to_have_count(3)
                expect(page.locator('#picks .pick__sources a')).to_have_count(3)
                expect(page.locator('#picks .pick__league')).to_have_count(3)
                bars = []
                for card in page.locator('#picks .pick').all():
                    rect = card.bounding_box()
                    bar = card.locator('.pick__colors').bounding_box()
                    sources = card.locator('.pick__sources').bounding_box()
                    emblem = card.locator('.pick__league').bounding_box()
                    bars.append(bar['y'])
                    self.assertAlmostEqual(rect['y'] + rect['height'] - (bar['y'] + bar['height']), 13, delta=1)
                    self.assertGreater(bar['y'], sources['y'] + sources['height'])
                    self.assertGreater(emblem['x'], rect['x'] + rect['width'] / 2)
                    self.assertLess(emblem['y'] - rect['y'], 25)
                self.assertLess(max(bars) - min(bars), 1)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.screenshot(path=str(self.artifacts / f'live-top-three-{width}.png'), full_page=True)
                feed['data'] = scoreboard('post')
                page.clock.run_for(60000)
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'live-second')
                self.assertEqual(page.locator('#picks .pick').evaluate_all('els => els.map(e => e.dataset.matchId)'), ['mls', 'future', 'fourth'])
                page.locator('#btn-menu').click()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(page.locator('#nextup')).to_be_hidden()
                expect(page.locator('#picks .pick')).to_have_attribute('data-match-id', 'mls')
                page.locator('#btn-filters-close').click()
                page.locator('#btn-all').click()
                expect(page.locator('#nextup')).to_be_hidden()
                expect(page.locator('#picks .pick')).to_have_count(1)

    def test_league_priority_blends_with_interest_persists_and_resets(self):
        fixtures = [("eng", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("esp", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("unconfirmed-live", "2026-10-07T16:45:00+00:00", "pre", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story['league_order'] = ['eng.1', 'esp.1']
        story['rankings'] = {mid: dict(score=score) for mid, score in [('eng', 80), ('esp', 80.5), ('unconfirmed-live', 40)]}
        for width in (1280, 390):
            with self.subTest(width=width), self.page('after', width=width, html=render_page(build, fixtures=fixtures, league_logos=True), story=story, touch=width <= 600) as (page, _):
                expect(page.locator('#nextup')).to_be_hidden()  # kickoff time alone is not a confirmed live game
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'eng')
                page.locator('#btn-menu').click()
                expect(page.locator('#comp-pills .lg')).to_have_count(2)
                self.assertTrue(page.locator('#comp-pills .lg').evaluate_all(
                    "els => els.every(el => getComputedStyle(el).backgroundImage !== 'none')"))
                self.assertLessEqual(page.locator('#drawer').evaluate('el => el.scrollWidth - el.clientWidth'), 1)
                page.locator('.league-priority > summary').click()
                page.get_by_role('button', name='Move La Liga up', exact=True).click()
                expect(page.locator('#drawer')).to_be_visible()
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'esp')
                self.assertEqual(page.locator('#comp-pills .fpill').first.get_attribute('data-key'), 'esp.1')
                page.reload()
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'esp')
                page.locator('#btn-menu').click()
                page.locator('#comp-pills [data-key="esp.1"] .lg').click()
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'eng')
                page.locator('#btn-reset').click()
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'eng')
                self.assertIsNone(page.evaluate("localStorage.getItem('ssg4-league-order')"))
                source = page.locator('#comp-pills [data-key="esp.1"] .fpill__grip')
                source.scroll_into_view_if_needed()
                start = source.bounding_box()
                end = page.locator('#comp-pills [data-key="eng.1"]').bounding_box()
                sx, sy = start['x'] + start['width'] / 2, start['y'] + start['height'] / 2
                tx, ty = end['x'] + 2, end['y'] + end['height'] / 2
                if width <= 600:
                    session = page.context.new_cdp_session(page)
                    session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': sx, 'y': sy}]})
                    session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': tx, 'y': ty}]})
                    session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                    session.detach()
                else:
                    page.mouse.move(sx, sy)
                    page.mouse.down()
                    page.mouse.move(tx, ty, steps=8)
                    page.mouse.up()
                page.clock.run_for(1)
                self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('ssg4-league-order'))[0]"), 'esp.1')
                expect(page.locator('#comp-pills [data-key="esp.1"]')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#comp-pills [data-key="eng.1"]')).to_have_attribute('aria-pressed', 'true')
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'esp')
                expect(page.locator('.fpill--drag-ghost')).to_have_count(0)
                page.reload()
                expect(page.locator('#picks .pick').first).to_have_attribute('data-match-id', 'esp')
                page.locator('#btn-menu').click()
                page.locator('#comp-pills [data-key="esp.1"]').click()
                expect(page.locator('#comp-pills [data-key="esp.1"]')).to_have_attribute('aria-pressed', 'false')

    def test_rolling_window_and_unrated_recommendation_fallback(self):
        fixtures = [("inside", "2026-10-08T16:59:00+00:00", "pre", "ESPN+"),
                    ("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+"),
                    ("later", "2026-10-10T17:00:00+00:00", "pre", "ESPN+")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
            expect(page.locator("#tally-n")).to_have_text("1")
            expect(page.locator("#schedule-summary")).to_contain_text("1 upcoming")
            expect(page.locator("#picks .pick")).to_have_count(3)
            expect(page.locator("#picks-section")).to_be_visible()
            expect(page.locator("#nextup")).to_be_hidden()
            expect(page.locator('li.row[data-id="inside"]')).to_be_visible()
            expect(page.locator('li.row[data-id="edge"]')).to_be_visible()
            expect(page.locator('details[data-b="later"]')).to_have_attribute("open", "")
            page.locator('details[data-b="later"] > summary').click()
            expect(page.locator('li.row[data-id="edge"]')).to_be_hidden()
            page.evaluate("location.hash = '#at-20261007-1301'")
            expect(page.locator("#tally-n")).to_have_text("2")
            self.assertEqual(self.bucket(page, "edge"), "Tomorrow")
            expect(page.locator('li.row[data-id="edge"]')).to_be_visible()
            expect(page.locator('details[data-b="later"]')).not_to_have_attribute("open", "")

    def test_later_section_defaults_follow_visible_match_count(self):
        for count in (0, 4, 5):
            fixtures = [(str(i), "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "esp.1") for i in range(count)]
            fixtures.append(("later", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1"))
            with self.subTest(count=count), self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
                fold = page.locator('details[data-b="later"]')
                self.assertEqual(fold.evaluate('el => el.open'), count < 5)
                if count == 0:
                    expect(page.locator("#forecast")).to_be_hidden()
                expect(page.locator("#forecast-later")).to_be_visible()
                self.assertEqual(page.locator("#forecast-later").evaluate("el => el.nextElementSibling.dataset.b"), "later")
        fixtures = [("upcoming" if i == 0 else str(i), "2026-10-07T17:05:00+00:00", "pre", "ESPN+", "esp.1") for i in range(4)]
        fixtures += [("fifth", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                     ("later", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, feed):
            fold = page.locator('details[data-b="later"]')
            expect(fold).not_to_have_attribute("open", "")
            page.locator("#btn-menu").click()
            page.locator('[data-kind="have"][data-key="apple"]').click()
            expect(fold).to_have_attribute("open", "")
            page.locator("#btn-filters-close").click()
            fold.locator('summary').click()
            feed["data"] = scoreboard("in")
            page.clock.run_for(60000)
            expect(fold).not_to_have_attribute("open", "")

    def test_later_news_stays_beside_later_schedule_with_no_near_matches(self):
        fixtures = [("main", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("second", "2026-10-09T20:00:00+00:00", "pre", "Apple TV", "usa.1")]
        story = self.tagged_story()
        story["lede_items"] = [{"segments": [{"text": "Main Friday story.", "match_ids": ["main"]}], "sources": []}]
        story["forecast"]["items"] = [
            {"segments": [{"text": "Repeated Friday coverage.", "match_ids": ["main"]}], "sources": []},
            {"segments": [{"text": "Other Friday context.", "match_ids": ["second"]}], "sources": []}]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                expect(page.locator("#forecast")).to_be_hidden()
                expect(page.locator("#forecast-later")).to_contain_text("Other Friday context.")
                expect(page.locator("#forecast-later .editorial-item")).to_have_count(1)
                expect(page.locator("#forecast-later")).not_to_contain_text("Repeated")
                expect(page.locator("#period-h")).to_have_count(0)
                expect(page.locator("#picks .pick")).to_have_count(2)
                expect(page.locator('details[data-b="later"]')).to_have_attribute("open", "")
                page.screenshot(path=str(self.artifacts / f"empty-near-{width}.png"), full_page=True)

    def test_later_editorial_is_retained_and_rolls_into_window(self):
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
            expect(page.locator("#forecast-later")).to_contain_text("Beyond 24 hours")
            page.evaluate("location.hash = '#at-20261007-1401'")
            expect(page.locator("#forecast")).to_contain_text("Next 24 hours")
        story["later_reason"] = ""
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#forecast-later")).to_contain_text("Later context")

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
            expect(page.locator("#forecast-later")).to_contain_text("Later match context")
            expect(page.locator("#forecast-later")).to_contain_text("Beyond 24 hours")
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
                expect(page.locator("#forecast .editorial-item")).to_have_count(0)
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
