"""Offline Chromium checks; run with python3 -m tests.browser --help."""
import argparse
from copy import deepcopy
from datetime import timedelta
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import re
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
    def page(self, target, *, width=1280, theme="light", at="20261007-1300", html=None, story=None, touch=False, locale="en-US"):
        context = self.browser.new_context(
            viewport={"width": width, "height": 900 if width > 600 else 844},
            locale=locale, timezone_id="America/New_York", color_scheme=theme,
            device_scale_factor=1, has_touch=touch)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # "hold" names a part of a scoreboard path whose answers wait in "held" for the test to send.
        feed = {"data": {"events": []}, "fail": False, "requests": 0, "hold": None, "held": [], "stories": 0}

        def route_request(route):
            url = urlsplit(route.request.url)
            if html is not None and url.path.endswith("/index.html"):
                route.fulfill(content_type="text/html", body=html)
            elif url.path.startswith("/espn/"):
                feed["requests"] += 1
                if feed["hold"] and feed["hold"] in url.path:
                    feed["held"].append(route)
                elif feed["fail"]:
                    route.fulfill(status=503, body="Fixture: ESPN unavailable")
                else:
                    route.fulfill(content_type="application/json", body=json.dumps(feed["data"]))
            elif url.path.endswith("/story.json"):
                feed["stories"] += 1
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
        # Leave room for real time between the two protocol calls before freezing the clock.
        page.clock.install(time=BUILT_AT - timedelta(seconds=1))
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

    def full_page_shot(self, page, name):
        """Save a full-page screenshot from the top of the page, where the controls bar is in its place
        rather than stuck wherever the check had scrolled to."""
        page.evaluate("window.scrollTo(0, 0)")
        page.clock.run_for(50)
        page.screenshot(path=str(self.artifacts / name), full_page=True)

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
                                expect(page.locator("#drawer")).to_be_visible()
                            elif state == "details":
                                page.keyboard.press("Escape")
                                page.locator('li[data-id="upcoming"] .more').click()
                                expect(page.locator("#match-dialog")).to_be_visible()
                            # Without a baseline there is nothing to compare pixels with; the layout must still fit.
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width, state)
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

    def test_details_hover_preview_is_stable_hoverable_and_dismissible(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme), self.page('after', theme=theme) as (page, _):
                row = page.locator('li.row[data-id="upcoming"]')
                button = row.locator('button.more')
                button.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().bottom + scrollY - innerHeight + 35)")
                before = row.bounding_box()
                button.hover()
                page.clock.run_for(200)
                preview = page.locator('#match-preview')
                expect(preview).to_be_visible()
                expect(page.locator('#match-dialog')).to_be_hidden()
                expect(row.locator('.row__detail')).to_be_hidden()
                self.assertEqual(preview.locator('.row__detail').text_content(), row.locator('.row__detail').text_content())
                box = preview.bounding_box()
                anchor = button.bounding_box()
                self.assertLessEqual(box['y'] + box['height'], anchor['y'])
                self.assertGreaterEqual(box['x'], 0)
                self.assertGreaterEqual(box['y'], 0)
                self.assertLessEqual(box['x'] + box['width'], page.viewport_size['width'])
                self.assertEqual(before, row.bounding_box())
                page.mouse.move(box['x'] + 25, box['y'] + 25)
                page.clock.run_for(400)
                expect(preview).to_be_visible()
                page.screenshot(path=str(self.artifacts / f'details-hover-{theme}.png'))
                page.keyboard.press('Escape')
                expect(preview).to_be_hidden()
                button.hover()
                page.clock.run_for(200)
                expect(preview).to_be_visible()
                page.mouse.move(1, 1)
                page.clock.run_for(250)
                expect(preview).to_be_hidden()
                button.hover()
                page.clock.run_for(200)
                preview.get_by_role('link', name='League table').click()
                expect(preview).to_be_hidden()
                expect(page.locator('.tables__item[data-lg="eng.1"]')).to_have_attribute('open', '')

    def test_details_dialog_mouse_touch_keyboard_and_focus_without_reflow(self):
        for width in (1280, 390, 320):
            with self.subTest(width=width), self.page('after', width=width, touch=width <= 600) as (page, feed):
                row = page.locator('li.row[data-id="upcoming"]')
                button = row.locator('button.more')
                button.scroll_into_view_if_needed()
                before = row.bounding_box()
                if width <= 600:
                    button.tap()
                else:
                    button.focus()
                    page.keyboard.press('Enter')
                page.clock.run_for(250)
                dialog = page.locator('#match-dialog')
                expect(dialog).to_be_visible()
                expect(page.locator('#match-preview')).to_be_hidden()
                expect(dialog).to_have_attribute('aria-labelledby', 'match-dialog-title')
                expect(page.locator('#match-dialog-title')).to_have_text('Arsenal v Chelsea')
                expect(page.locator('#match-dialog-close')).to_be_focused()
                expect(dialog.locator('.detail__facts').first).to_contain_text('Top scorer A. Player, 6 goals')
                calendar = dialog.get_by_role('link', name='Add to calendar')
                expect(calendar).to_have_attribute('href', re.compile('^https://calendar.google.com/'))
                expect(calendar).to_have_attribute('title', 'Add to Google Calendar')
                expect(row.locator('.row__detail')).to_be_hidden()
                self.assertEqual(before, row.bounding_box())
                box = dialog.bounding_box()
                self.assertGreaterEqual(box['x'], 0)
                self.assertGreaterEqual(box['y'], 0)
                self.assertLessEqual(box['x'] + box['width'], width)
                self.assertLessEqual(box['y'] + box['height'], page.viewport_size['height'])
                for _ in range(6):
                    page.keyboard.press('Tab')
                    self.assertTrue(dialog.evaluate('el => el.contains(document.activeElement)'))
                page.screenshot(path=str(self.artifacts / f'details-dialog-{width}.png'))
                page.keyboard.press('Escape')
                page.clock.run_for(50)
                expect(dialog).to_be_hidden()
                expect(button).to_be_focused()
                self.assertEqual(before, row.bounding_box())
                button.click()
                page.locator('#match-dialog-close').click()
                page.clock.run_for(50)
                expect(button).to_be_focused()
                button.click()
                page.mouse.click(2, 2)
                page.clock.run_for(50)
                expect(dialog).to_be_hidden()
                expect(button).to_be_focused()
                button.click()
                dialog.get_by_role('link', name='League table').click()
                page.clock.run_for(50)
                expect(dialog).to_be_hidden()
                expect(page.locator('.tables__item[data-lg="eng.1"] > summary')).to_be_focused()
                expect(page.locator('.tables__item[data-lg="eng.1"]')).to_have_attribute('open', '')
                self.assertFalse(page.locator('html').evaluate("el => el.classList.contains('has-match-dialog')"))

    def test_elsewhere_details_use_the_hidden_schedule_match(self):
        # Three better-rated matches fill the picks, so the Peacock match stays for Elsewhere alone.
        fixtures = [('off', '2026-10-07T18:00:00+00:00', 'pre', 'Peacock', 'eng.1')] + [
            (mid, f'2026-10-07T{hour}:00:00+00:00', 'pre', 'ESPN+', 'eng.1') for mid, hour in (('p1', 19), ('p2', 20), ('p3', 21), ('p4', 22))]
        html = render_page(build, fixtures=fixtures)
        html = re.sub(r'data-score="[0-9]+"', 'data-score="95"', html)
        story = self.tagged_story()
        story['rankings'] = {mid: dict(score=score) for mid, score in (('off', 10), ('p1', 90), ('p2', 90), ('p3', 90), ('p4', 90))}
        with self.page('after', html=html, story=story) as (page, _):
            expect(page.locator('#picks [data-match-id="off"]')).to_have_count(0)
            row = page.locator('li.row[data-id="off"]')
            button = page.locator('#misses [data-id="off"] button.more')
            expect(row).to_be_hidden()
            button.click()
            expect(page.locator('#match-dialog')).to_have_attribute('data-match-id', 'off')
            expect(page.locator('#match-dialog .detail__venue')).to_have_text('Fixture Stadium')
            expect(page.locator('#match-dialog .detail__facts').first).to_contain_text('Top scorer A. Player, 6 goals')
            page.locator('#match-dialog-close').click()
            page.clock.run_for(50)
            expect(button).to_be_focused()
        # Rated highest, it becomes a pick and leaves Elsewhere, so no match is shown twice.
        story['rankings']['off'] = dict(score=99)
        with self.page('after', html=html, story=story) as (page, _):
            expect(page.locator('#picks .pick[data-match-id="off"]')).to_have_class(re.compile(r'\bmatch--off\b'))
            expect(page.locator('#misses [data-id="off"]')).to_have_count(0)

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
                # The masthead (title, tally) above the bar with the view toggle and Lineup, on one line at every width.
                expect(page.locator('#bar #controls .seg')).to_have_count(1)
                expect(page.locator('#bar #btn-menu')).to_have_count(1)
                title, toggle, menu, tally, bar = (page.locator(selector).bounding_box() for selector in ('.hdr h1', '#controls .seg', '#btn-menu', '.hdr__tally', '#bar'))
                self.assertLessEqual(title['x'] + title['width'], tally['x'])
                self.assertGreaterEqual(bar['y'], max(title['y'] + title['height'], tally['y'] + tally['height']) - 1)
                self.assertLessEqual(toggle['x'] + toggle['width'], menu['x'])
                self.assertAlmostEqual(toggle['y'] + toggle['height'] / 2, menu['y'] + menu['height'] / 2, delta=2)
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
                    rail = row.locator('.row__kickoff').bounding_box()
                    self.assertAlmostEqual(emblem['y'], clock['y'] + 2, delta=1)
                    self.assertAlmostEqual(rail['y'] + rail['height'], clock['y'] + clock['height'], delta=1)
                    content_box = row.bounding_box()
                    self.assertAlmostEqual(clock['y'] + clock['height'], content_box['y'] + content_box['height'] - 13, delta=1)
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
            # The first match leads the top card and the second is a pick: both kinds of featured card.
            def featured(key):
                return page.locator(f'#nextup .{key}, #picks .{key}').first
            expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'dark-league')
            for key in ('l-dark-team', 'l-L0'):
                expect(featured(key)).to_have_css('filter', 'contrast(0.5) brightness(1.8) saturate(0.85)')
            for key in ('l-bright-team', 'l-mixed-team', 'l-L1'):
                expect(featured(key)).to_have_css('filter', 'none')
            for icon in page.locator('#nextup .lg, #nextup .logo, #picks .lg, #picks .logo').all():
                expect(icon).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
                expect(icon).to_have_css('border-radius', '0px')
            page.emulate_media(color_scheme='light')
            expect(featured('l-dark-team')).to_have_css('filter', 'brightness(1)')
            expect(featured('l-bright-team')).to_have_css('filter', 'none')

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
            expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
            expect(page.locator("#eyebrow")).to_have_text("Wednesday, October 7")   # the schedule runs past 24 hours
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
            expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
            expect(page.locator("#story")).to_be_hidden()
            expect(page.locator("#tally-n")).to_have_text("0")
            page.locator("#btn-all").click()
            for mid in ("unlisted", "unknown", "off-lineup", "usual-off-lineup"):
                expect(page.locator(f'li.row[data-id="{mid}"]')).to_be_visible()
            expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
            expect(page.locator("#story")).to_be_hidden()
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="fifa.friendly.w"]').click()
            expect(page.locator('li.row[data-id="unlisted"]')).to_be_hidden()
            expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
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
                expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
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

    def test_one_overview_changes_with_competitions_and_services(self):
        fixtures = [("mls", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["lede_items"] = []
        story["league_blurbs"] = [dict(item, interest=80 - i, league_id=league)
                                   for i, (item, league) in enumerate(zip(story["forecast"]["items"], ("usa.1", "esp.1")))]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", html=render_page(build, fixtures=fixtures), story=story, width=width) as (page, _):
                editorial = page.locator("#story-lede")
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
                self.full_page_shot(page, f"phrases-{theme}.png")
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
                # The top card is a section of its own, headed, after the overview and before the picks.
                self.assertEqual(page.locator("#story").evaluate("el => el.nextElementSibling.id"), "nextup-section")
                self.assertEqual(page.locator("#nextup-section").evaluate("el => el.nextElementSibling.id"), "picks-section")
                expect(page.locator("#nextup-h")).to_have_text("Next up")
                expect(page.locator("#nextup-sub")).to_have_text("The soonest kickoff in your lineup")
                heading, card = page.locator("#nextup-h").bounding_box(), page.locator("#nextup").bounding_box()
                self.assertLessEqual(heading["y"] + heading["height"], card["y"])
                expect(page.locator("#nextup")).to_have_attribute("data-match-id", "mls")
                expect(page.locator("#picks-section")).to_be_visible()
                expect(page.locator("#picks .pick")).to_have_attribute("data-match-id", "spain")
                expect(page.locator("#schedule-summary")).to_be_hidden()
                expect(page.locator("#schedule-info")).not_to_have_attribute("open", "")
                # Schedule sections have no independent prose or factual intro.
                expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
                self.assertEqual(page.locator("#outlook").evaluate("el => el.firstElementChild.querySelector('h2').id"), "outlook-h")
                hero = page.locator("#nextup").bounding_box()
                self.assertLess(hero["y"] + hero["height"], page.viewport_size["height"])
                self.full_page_shot(page, f"compact-opening-{width}.png")

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
                self.full_page_shot(page, f"league-fallback-{width}.png")
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
                expect(page.locator("#nextup")).to_have_attribute("data-match-id", "far")
                # The top card follows the lineup; the picks come from every service.
                expect(page.locator("#picks-section")).to_be_visible()
                expect(page.locator('#picks [data-match-id="far"]')).to_have_count(0)
                expect(page.locator('li.row[data-id="far"]')).to_have_count(1)

    def test_top_three_select_by_rating_but_display_chronologically(self):
        fixtures = [("low", "2026-10-07T17:30:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("routine", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("best", "2026-10-07T19:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("near", "2026-10-08T16:59:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("far", "2026-10-10T17:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("unknown", "2026-10-07T20:00:00+00:00", "pre", None, "fifa.friendly.w"),
                    ("finished", "2026-10-07T16:00:00+00:00", "post", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(popularity=score, gameplay=score, impact=score, score=score)
                             for mid, score in (("low", 10), ("routine", 60), ("best", 90), ("near", 80), ("far", 95), ("unknown", 100), ("finished", 100))}
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                hero = page.locator("#nextup")
                # Nothing is live: the top card is the soonest match to watch, whatever its rating.
                expect(hero).to_have_attribute("data-match-id", "low")
                expect(page.locator("#picks-section")).to_be_visible()
                expect(page.locator('#misses [data-id="unknown"]')).to_have_count(0)
                picks = lambda: page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)")
                off = lambda: page.locator("#picks .pick.match--off").evaluate_all("els => els.map(e => e.dataset.matchId)")
                self.assertEqual(picks(), ["routine", "best", "near"])
                self.assertEqual(off(), [])
                self.full_page_shot(page, f"ranked-{width}.png")
                # Without Apple TV the top card moves on, but the picks stay the best matches anywhere: best
                # keeps its place, dimmed as its schedule row is and saying it's outside the lineup; low,
                # also on Apple TV, now fills the place the top card's routine left.
                page.locator("#btn-menu").click()
                page.locator('[data-kind="have"][data-key="apple"]').click()
                expect(hero).to_have_attribute("data-match-id", "routine")
                self.assertEqual(picks(), ["low", "best", "near"])
                self.assertEqual(off(), ["low", "best"])
                best = page.locator('#picks .pick[data-match-id="best"]')
                expect(best).to_have_class(re.compile(r"\bsvc-off\b"))
                expect(best.locator(".match__watch")).to_have_text("Not in your lineup")
                # Muted as .row--off mutes its row (that row is out of the schedule now), where near's names are not.
                muted = page.evaluate("getComputedStyle(document.getElementById('picks-sub')).color")
                expect(best.locator(".team__name").first).to_have_css("color", muted)
                expect(page.locator('#picks .pick[data-match-id="near"] .team__name').first).not_to_have_css("color", muted)
                expect(page.locator('#picks .pick[data-match-id="near"] .match__watch')).to_contain_text("ESPN+")
                # A hidden competition hides its matches from the schedule, not from the picks.
                page.locator('#comp-pills [data-key="esp.1"]').click()
                expect(hero).to_have_attribute("data-match-id", "routine")
                expect(page.locator('li.row[data-id="near"]')).to_be_hidden()
                self.assertEqual(picks(), ["low", "best", "near"])
                # With no services at all there is no top card, and the picks are the three best, all outside the lineup.
                page.locator("#btn-clear").click()
                expect(hero).to_be_hidden()
                self.assertEqual(picks(), ["routine", "best", "near"])
                self.assertEqual(off(), ["routine", "best", "near"])
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(hero).to_be_hidden()
                self.assertEqual(picks(), ["routine", "best", "near"])
                # A match nobody is known to carry is never a pick, however it is rated.
                expect(page.locator('#picks [data-match-id="unknown"]')).to_have_count(0)

    def test_later_picks_fill_from_nearest_windows_and_finished_match_is_removed(self):
        fixtures = [("early", "2026-10-09T18:00:00+00:00", "pre", "ESPN+"),
                    ("best", "2026-10-09T20:00:00+00:00", "pre", "ESPN+"),
                    ("low", "2026-10-09T21:00:00+00:00", "pre", "ESPN+"),
                    ("too-far", "2026-10-11T18:00:00+00:00", "pre", "ESPN+"),
                    ("farther", "2026-10-12T18:00:00+00:00", "pre", "ESPN+")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(score=score) for mid, score in (("early", 50), ("best", 70), ("low", 30), ("too-far", 90),
                                                                       ("farther", 95), ("upcoming", 85))}
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            # Nothing in the next 24 hours: the soonest match is next up, two days and an hour away ...
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "early")
            expect(page.locator("#nextup-h")).to_have_text("Next up")
            expect(page.locator("#nextup-status")).to_have_text("Kickoff 2:00 pm Fri")
            expect(page.locator("#nextup-count")).to_have_text("2d 01h")
            # ... and the picks fill from the nearest later window, then the next, never jumping ahead to
            # the best-rated match further out.
            self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["best", "low", "too-far"])
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
        for width in (1280, 820, 390, 320):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures, league_logos=True), story=story) as (page, feed):
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'upcoming')
                expect(page.locator('#nextup-h')).to_have_text('Live now')
                expect(page.locator('#nextup-sub')).to_have_text('Best pick score of the 2 in progress in your lineup')
                expect(page.locator('#nextup-status')).to_have_text("30'")
                # Interest (80 + Outlook 57.5) / 2 = 68.75; 80% of that and 20% of league priority 100.
                expect(page.locator('#nextup-rating')).to_have_text('Pick score · 75/100')
                self.assertEqual(page.locator('#picks .pick').evaluate_all('els => els.map(e => e.dataset.matchId)'), ['live-second', 'mls', 'future'])
                expect(page.locator('#picks .row__story')).to_have_count(3)
                expect(page.locator('#picks .story-src a')).to_have_count(3)
                expect(page.locator('#picks .pick__league')).to_have_count(3)
                # The same match body must retain every field in both featured shapes.
                for card in page.locator('#nextup, #picks .pick').all():
                    mid = card.get_attribute('data-match-id')
                    row = page.locator(f'li.row[data-id="{mid}"]')
                    for selector in ('.row__teams', '.row__meta', '.row__story', '.pills'):
                        self.assertEqual(card.locator(selector).text_content(), row.locator(selector).text_content())
                    self.assertEqual(card.locator('.form').evaluate_all('els => els.map(e => [e.title, e.innerHTML])'),
                                     row.locator('.form').evaluate_all('els => els.map(e => [e.title, e.innerHTML])'))
                    expect(card.locator('.form')).to_have_count(2)
                    expect(card.locator('.form .f')).to_have_count(10)
                    expect(card.locator('.venue')).to_have_text('Fixture Stadium')
                    if row.get_attribute('data-state') == 'pre':
                        expect(card.locator('.row__until')).to_be_visible()
                        expect(card.locator('.row__until')).to_have_text(row.locator('.row__until').text_content())
                    self.assertEqual(card.locator('.match__watch').text_content(), row.locator('.row__watch').text_content())
                    card.locator('button.more').click()
                    expect(page.locator('#match-dialog')).to_be_visible()
                    self.assertEqual(page.locator('#match-dialog .row__detail').text_content(), row.locator('.row__detail').text_content())
                    expect(row.locator('.row__detail')).to_be_hidden()
                    page.locator('#match-dialog-close').click()
                    page.clock.run_for(50)
                expect(page.locator('#nextup .row__story')).to_contain_text('Specific context for upcoming.')
                bars = []
                for card in page.locator('#picks .pick').all():
                    rect = card.bounding_box()
                    bar = card.locator('.pick__colors').bounding_box()
                    sources = card.locator('.row__story').bounding_box()
                    emblem = card.locator('.pick__league').bounding_box()
                    bars.append(bar['y'])
                    self.assertAlmostEqual(rect['y'] + rect['height'] - (bar['y'] + bar['height']), 13, delta=1)
                    self.assertGreater(bar['y'], sources['y'] + sources['height'])
                    self.assertGreater(emblem['x'], rect['x'] + rect['width'] / 2)
                    self.assertLess(emblem['y'] - rect['y'], 25)
                self.assertLess(max(bars) - min(bars), 1)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                self.full_page_shot(page, f'live-top-three-{width}.png')
                page.locator('#nextup button.more').click()
                feed['data'] = scoreboard('in')
                page.clock.run_for(60000)
                expect(page.locator('#nextup .team .score')).to_have_text(['2', '1'])
                expect(page.locator('#nextup .row__goals')).to_contain_text('A. Player')
                expect(page.locator('#match-dialog')).to_be_visible()
                # A clock/scorer-only update also refreshes featured content and preserves details.
                feed['data']['events'][0]['competitions'][0]['status']['displayClock'] = "64'"
                feed['data']['events'][0]['competitions'][0]['details'][0]['athletesInvolved'][0]['shortName'] = 'Corrected Scorer'
                page.clock.run_for(60000)
                expect(page.locator('#nextup-status')).to_contain_text("64'")
                expect(page.locator('#nextup .row__goals')).to_contain_text('Corrected Scorer')
                expect(page.locator('#match-dialog')).to_be_visible()
                page.locator('#match-dialog-close').click()
                page.clock.run_for(50)
                expect(page.locator('#nextup button.more')).to_be_focused()
                page.keyboard.press('Enter')
                feed['data'] = scoreboard('post')
                page.clock.run_for(60000)
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'live-second')
                self.assertEqual(page.locator('#picks .pick').evaluate_all('els => els.map(e => e.dataset.matchId)'), ['mls', 'future', 'fourth'])
                expect(page.locator('#match-dialog-title')).to_have_text('Arsenal v Chelsea')
                expect(page.locator('#match-dialog')).to_have_attribute('data-match-id', 'upcoming')
                page.locator('#match-dialog-close').click()
                page.clock.run_for(50)
                page.locator('#btn-menu').click()
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'mls')
                expect(page.locator('#nextup')).not_to_have_class(re.compile(r'\bnextup--live\b'))
                expect(page.locator('#nextup-h')).to_have_text('Next up')
                expect(page.locator('#nextup-status')).to_have_text('Kickoff 3:00 pm')
                expect(page.locator('#nextup-count')).to_have_text('2h 00m')
                # The picks still come from every service: the ESPN+ matches, now outside the lineup.
                self.assertEqual(page.locator('#picks .pick').evaluate_all('els => els.map(e => e.dataset.matchId)'), ['live-second', 'future', 'fourth'])
                expect(page.locator('#picks .pick.match--off')).to_have_count(3)
                # A blurb its row hides outside the lineup still shows on its pick.
                expect(page.locator('li.row[data-id="future"] .row__story')).to_be_hidden()
                expect(page.locator('#picks .pick[data-match-id="future"] .row__story')).to_be_visible()
                expect(page.locator('#picks .pick[data-match-id="future"] .row__story')).to_contain_text('Specific context for future.')
                page.locator('#btn-filters-close').click()
                page.locator('#btn-all').click()
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'mls')
                expect(page.locator('#picks .pick')).to_have_count(3)

    def test_top_card_counts_down_to_the_next_match_to_watch_until_one_is_live(self):
        fixtures = [("tbd", "2026-10-07T17:05:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("elsewhere", "2026-10-07T17:06:00+00:00", "pre", "Peacock", "eng.1"),
                    ("nwsl", "2026-10-07T17:08:00+00:00", "pre", "ESPN+", "usa.nwsl"),
                    ("apple", "2026-10-07T17:10:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("upcoming", "2026-10-07T17:10:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("evening", "2026-10-07T23:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(score=score) for mid, score in
                             (("tbd", 99), ("elsewhere", 99), ("nwsl", 99), ("upcoming", 90), ("apple", 10), ("evening", 95))}
        html = render_page(build, fixtures=fixtures, tbd={"tbd"})
        for width in (1280, 320):
            with self.subTest(width=width), self.page("after", width=width, html=html, story=story) as (page, feed):
                card, status, count = page.locator("#nextup"), page.locator("#nextup-status"), page.locator("#nextup-count")
                # The soonest confirmed kickoff on the lineup, with nothing live: not the earlier match whose
                # time is still to be set, nor the ones outside the lineup; of two at once, the better rated.
                expect(card).to_have_attribute("data-match-id", "upcoming")
                expect(card).not_to_have_class(re.compile(r"\bnextup--live\b"))
                expect(page.locator("#nextup-h")).to_have_text("Next up")
                expect(status).to_have_text("Kickoff 1:10 pm")
                expect(count).to_have_text("10:00")
                expect(page.locator('#picks [data-match-id="upcoming"]')).to_have_count(0)
                # The lower-rated of the two at 1:10 comes first on the page, so only the rating can choose.
                self.assertEqual(page.locator('li.row[data-id="apple"]').evaluate(
                    "el => el.compareDocumentPosition(document.querySelector('li.row[data-id=upcoming]')) & Node.DOCUMENT_POSITION_FOLLOWING"), 4)
                page.screenshot(path=str(self.artifacts / f"next-up-{width}.png"))
                # Without a time fixed in the address, the countdown runs with the clock, second by second.
                # (The page loaded on the second; its ticks fall on the next two, at 599 and 598 seconds to go.)
                page.evaluate("location.hash = ''")
                expect(count).to_have_text("9:59")
                page.clock.run_for(2000)
                expect(count).to_have_text("9:58")
                page.evaluate("location.hash = '#at-20261007-1300'")
                expect(count).to_have_text("10:00")
                # It follows the lineup both ways; Everything widens the schedule, not the card.
                page.locator("#btn-menu").click()
                page.locator('#comp-pills [data-key="usa.nwsl"]').click()
                expect(card).to_have_attribute("data-match-id", "nwsl")
                expect(count).to_have_text("8:00")
                page.locator('#comp-pills [data-key="usa.nwsl"]').click()
                expect(card).to_have_attribute("data-match-id", "upcoming")
                page.locator('[data-kind="have"][data-key="espn"]').click()
                expect(card).to_have_attribute("data-match-id", "apple")
                page.locator('[data-kind="have"][data-key="espn"]').click()
                page.locator("#btn-filters-close").click()
                page.locator("#btn-all").click()
                expect(page.locator('li.row[data-id="elsewhere"]')).to_be_visible()
                expect(card).to_have_attribute("data-match-id", "upcoming")
                page.locator("#btn-mine").click()
                # The countdown runs to kickoff ...
                page.evaluate("location.hash = '#at-20261007-1309'")
                expect(count).to_have_text("1:00")
                # ... and a kickoff without word from ESPN keeps the card, in the words its row uses.
                page.evaluate("location.hash = '#at-20261007-1311'")
                expect(card).to_have_attribute("data-match-id", "upcoming")
                expect(page.locator("#nextup-h")).to_have_text("Next up")      # only ESPN's word makes it live
                expect(status).to_have_text("Kickoff 1:10 pm · status pending")
                expect(count).to_have_text("Awaiting score")
                expect(page.locator('li.row[data-id="upcoming"] .row__live')).to_have_text("Awaiting score")
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                # Once ESPN reports it under way, the same card turns live and keeps keyboard focus (on its
                # League table link: with no blurb the card shows its details, and has no Details button).
                page.locator("#nextup a.detail__table").focus()
                feed["data"] = scoreboard("in")
                page.clock.run_for(60000)
                expect(card).to_have_class(re.compile(r"\bnextup--live\b"))
                expect(page.locator("#nextup-h")).to_have_text("Live now")
                expect(page.locator("#nextup-sub")).to_have_text("In progress in your lineup")
                expect(status).to_have_text("63'")
                expect(count).to_have_text("2\u20131")
                expect(page.locator("#nextup a.detail__table")).to_be_focused()
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                # At full time it moves on to the next match, here one whose kickoff is also awaiting word.
                feed["data"] = scoreboard("post")
                page.clock.run_for(60000)
                expect(card).to_have_attribute("data-match-id", "apple")
                expect(card).not_to_have_class(re.compile(r"\bnextup--live\b"))
                expect(count).to_have_text("Awaiting score")
                # Nothing on the lineup, no card.
                page.locator("#btn-menu").click()
                page.locator("#btn-clear").click()
                expect(card).to_be_hidden()
                self.assertIsNone(card.get_attribute("data-match-id"))

    def test_top_card_reads_full_time_until_every_competition_has_answered(self):
        # The live poll asks each competition for its day and redraws once all have answered; the
        # card's one-second tick can come in between and must word the moment as the row does.
        fixtures = [("upcoming", "2026-10-07T16:30:00+00:00", "in", "ESPN+", "eng.1"),
                    ("spain", "2026-10-07T17:05:00+00:00", "pre", "ESPN+", "esp.1")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, feed):
            card = page.locator("#nextup")
            expect(card).to_have_attribute("data-match-id", "upcoming")
            expect(card).to_have_class(re.compile(r"\bnextup--live\b"))
            feed["hold"], feed["data"] = "/esp.1/", scoreboard("post")
            page.clock.run_for(60000)
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-state", "post")
            self.assertEqual(len(feed["held"]), 1)
            page.clock.run_for(2000)    # two ticks, with Spain's answer outstanding (fetches give up after 8 s)
            expect(page.locator("#nextup-status")).to_have_text("FT")
            expect(page.locator("#nextup-count")).to_have_text("2\u20131")
            expect(card).not_to_have_class(re.compile(r"\bnextup--live\b"))
            expect(card).to_have_attribute("data-match-id", "upcoming")
            feed["held"].pop().fulfill(content_type="application/json", body=json.dumps(feed["data"]))
            expect(card).to_have_attribute("data-match-id", "spain")
            expect(page.locator("#nextup-h")).to_have_text("Next up")
            expect(page.locator("#nextup-status")).to_have_text("Kickoff 1:05 pm")

    def test_coffee_link_ends_the_bar_at_every_width(self):
        for width in (1280, 390, 320):
            with self.subTest(width=width), self.page("after", width=width, touch=width <= 600) as (page, _):
                link = page.get_by_role("link", name="Buy me a coffee", exact=True)
                expect(link).to_be_visible()
                expect(link).to_have_attribute("href", "https://buymeacoffee.com/caparomula")
                expect(link).to_have_attribute("target", "_blank")
                self.assertIn("noopener", link.get_attribute("rel").split())
                # At the right end of the bar, on the toggle's line (the bar stays in view; see the bar's check).
                box, bar, toggle = link.bounding_box(), page.locator("#bar").bounding_box(), page.locator("#controls .seg").bounding_box()
                self.assertAlmostEqual(box["x"] + box["width"], bar["x"] + bar["width"], delta=1)
                self.assertAlmostEqual(box["y"] + box["height"] / 2, toggle["y"] + toggle["height"] / 2, delta=2)
                # The words where there's room; a phone shows the cup and keeps the name for screen readers.
                words = page.locator(".coffee__txt").bounding_box()
                if width > 600:
                    self.assertGreater(words["width"], 60)
                else:
                    self.assertLessEqual(words["width"], 1)
                self.assertGreaterEqual(min(box["width"], box["height"]), 24)     # WCAG 2.5.8's minimum target
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)

    @staticmethod
    def long_day():
        """36 matches 25 minutes apart; every third is on a service outside the default lineup."""
        fixtures = []
        for i in range(36):
            hour, minute = divmod(17 * 60 + 10 + i * 25, 60)
            fixtures.append((f"m{i:02d}", f"2026-10-{7 + hour // 24:02d}T{hour % 24:02d}:{minute:02d}:00+00:00", "pre",
                             "Peacock" if i % 3 == 2 else "ESPN+", "eng.1"))
        return render_page(build, fixtures=fixtures)

    def test_bar_stays_at_the_top_keeps_the_readers_place_and_focus_in_view(self):
        top_row = """() => { const below = document.getElementById('bar').getBoundingClientRect().bottom;
            const r = [...document.querySelectorAll('#outlook li.row')].find(el => el.getClientRects().length && el.getBoundingClientRect().bottom > below + 1);
            return { id: r.dataset.id, top: r.getBoundingClientRect().top }; }"""
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=self.long_day(), touch=width <= 600) as (page, _):
                bar = page.locator("#bar")
                expect(bar).not_to_have_css("background-color", "rgba(0, 0, 0, 0)")    # what scrolls beneath is hidden
                page.evaluate("window.scrollTo(0, 2200)")
                # Deep in the page the view toggle, Lineup and the coffee link are still at the top of the window.
                box = bar.bounding_box()
                self.assertAlmostEqual(box["y"], 0, delta=1)
                for control in (page.locator("#btn-mine"), page.locator("#btn-all"), page.locator("#btn-menu"),
                                page.get_by_role("link", name="Buy me a coffee", exact=True)):
                    expect(control).to_be_in_viewport()
                    self.assertLessEqual(control.bounding_box()["y"] + control.bounding_box()["height"], box["y"] + box["height"])
                # Switching the view from there keeps the reader's place: the row at the top of the view stays
                # put, and the rows Everything reveals appear around it.
                before = page.evaluate(top_row)
                page.locator("#btn-all").click()
                expect(page.locator('li.row[data-id="m02"]')).to_be_visible()
                # (Scroll offsets are whole pixels, so each correction can round by up to a pixel.)
                row = page.locator(f'li.row[data-id="{before["id"]}"]')
                self.assertAlmostEqual(row.evaluate("el => el.getBoundingClientRect().top"), before["top"], delta=2)
                page.locator("#btn-mine").click()
                expect(page.locator('li.row[data-id="m02"]')).to_be_hidden()
                self.assertAlmostEqual(row.evaluate("el => el.getBoundingClientRect().top"), before["top"], delta=2)
                # A row the change hides gives way to the next one shown, in its place: in Everything, put a
                # Peacock match first in the window, under the bar (where the browser's own scroll anchoring
                # would lose it), then go back to the lineup, which hides it.
                page.locator("#btn-all").click()
                page.evaluate("window.scrollBy(0, document.querySelector('li.row[data-id=m05]').getBoundingClientRect().top + 10)")
                page.locator("#btn-mine").click()
                expect(page.locator('li.row[data-id="m05"]')).to_be_hidden()
                self.assertAlmostEqual(page.locator('li.row[data-id="m06"]').evaluate("el => el.getBoundingClientRect().top"), -10, delta=2)
                # The Lineup panel opens right beneath its button, scrolled or not.
                page.locator("#btn-menu").click()
                panel, menu = page.locator("#drawer").bounding_box(), page.locator("#btn-menu").bounding_box()
                self.assertGreaterEqual(panel["y"], menu["y"] + menu["height"])
                self.assertLess(panel["y"], menu["y"] + menu["height"] + 16)
                page.keyboard.press("Escape")
                expect(page.locator("#drawer")).to_be_hidden()
                # Keyboard focus never hides under the bar: Tab onto a Details button that sits beneath it ...
                details = page.locator("#outlook li.row:visible button.more")
                page.evaluate("""() => { const all = [...document.querySelectorAll('#outlook li.row button.more')].filter(el => el.getClientRects().length);
                    window.scrollBy(0, all[12].getBoundingClientRect().top - 20); all[11].focus({ preventScroll: true }); }""")
                page.keyboard.press("Tab")
                page.clock.run_for(50)
                expect(details.nth(12)).to_be_focused()
                self.assertGreaterEqual(details.nth(12).bounding_box()["y"], bar.bounding_box()["y"] + bar.bounding_box()["height"])
                # ... and keyboard focus on the bar's own controls doesn't move the page.
                page.locator("#nextup .team__name a").first.focus()    # the first thing after the bar, far above
                page.evaluate("window.scrollTo(0, 2200)")
                page.keyboard.press("Shift+Tab")
                page.clock.run_for(50)
                expect(page.get_by_role("link", name="Buy me a coffee", exact=True)).to_be_focused()
                self.assertEqual(page.evaluate("scrollY"), 2200)
                page.screenshot(path=str(self.artifacts / f"bar-stuck-{width}.png"))

    def test_top_card_takes_the_rows_layout_with_the_emblem_under_its_countdown(self):
        fixtures = [("upcoming", "2026-10-07T17:10:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("spain", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["rankings"] = {mid: dict(score=score, blurb=f"Context for {mid}: the stakes, team news and recent form.",
                                       sources=[{"url": "https://example.com/report"}]) for mid, score in (("upcoming", 90), ("spain", 80))}
        html = render_page(build, fixtures=fixtures, league_logos=True)
        for width in (1280, 820, 390, 320):
            with self.subTest(width=width), self.page("after", width=width, html=html, story=story, touch=width <= 600) as (page, _):
                card = page.locator("#nextup")
                expect(card).to_have_attribute("data-match-id", "upcoming")
                box, column, count, emblem, body = (loc.bounding_box() for loc in (
                    card, card.locator(".nextup__left"), page.locator("#nextup-count"), card.locator(".nextup__league"), card.locator(".match__body")))
                # The emblem is in the countdown's column, under the countdown, at the foot of the card.
                self.assertAlmostEqual(emblem["x"], column["x"], delta=1)
                self.assertGreaterEqual(emblem["y"], count["y"] + count["height"])
                self.assertAlmostEqual(emblem["y"] + emblem["height"], column["y"] + column["height"], delta=1)
                self.assertAlmostEqual(column["y"] + column["height"], box["y"] + box["height"] - 13, delta=1)
                # The match beside that column, as in a schedule row, with home, v and away on one line where there's room.
                self.assertGreaterEqual(body["x"], column["x"] + column["width"])
                if width >= 820:
                    lines = card.locator(".row__teams > .team, .row__teams > .vs").evaluate_all(
                        "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom]; })")
                    self.assertLess(max(top for top, _ in lines), min(bottom for _, bottom in lines))
                # The broadcaster: its own 212-pixel column on the right, as in the row; under the match where the
                # row puts it there; and not at all on a phone, where the colored pill says it.
                watch = card.locator(".nextup__watch")
                if width > 820:
                    chip = watch.bounding_box()
                    self.assertGreaterEqual(chip["x"], body["x"] + body["width"])
                    self.assertAlmostEqual(chip["x"], box["x"] + box["width"] - 1 - 16 - 212, delta=1)
                    self.assertLess(chip["y"], body["y"] + 20)
                elif width > 600:
                    chip = watch.bounding_box()
                    self.assertGreaterEqual(chip["y"], body["y"] + body["height"] - 1)
                    self.assertAlmostEqual(chip["x"], body["x"], delta=1)
                else:
                    expect(watch).to_be_hidden()
                    # "Next up" over "1:10 pm" and the score's label over its value, in the narrow column.
                    # "Pick score" over "92": each part one box from the column's edge (an inline part that
                    # wraps has a box per line), the separator gone.
                    parts = page.locator("#nextup-rating > span:visible").evaluate_all(
                        "els => els.map(e => { const r = e.getClientRects(); return [r.length, r[0].left, r[0].top]; })")
                    self.assertEqual(len(parts), 2)
                    for boxes, left, _ in parts:
                        self.assertEqual(boxes, 1)
                        self.assertAlmostEqual(left, column["x"], delta=1)
                    self.assertGreater(parts[1][2], parts[0][2])
                    # Interest (90 + Outlook 57.5) / 2 = 73.75; 80% of that, 20% of league priority 100.
                    expect(page.locator("#nextup-rating")).to_have_text("Pick score · 79/100")
                    expect(page.locator("#nextup-status")).to_have_text("Kickoff 1:10 pm")
                    status = page.locator("#nextup-status").bounding_box()
                    self.assertLessEqual(status["x"] + status["width"], column["x"] + column["width"] + 1)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                card.screenshot(path=str(self.artifacts / f"top-card-{width}.png"))

    def test_league_priority_breaks_kickoff_ties_persists_and_resets(self):
        fixtures = [("eng", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("esp", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("unconfirmed-live", "2026-10-07T16:45:00+00:00", "pre", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story['league_order'] = ['eng.1', 'esp.1']
        story['rankings'] = {mid: dict(score=score) for mid, score in [('eng', 80), ('esp', 80.5), ('unconfirmed-live', 40)]}
        for width in (1280, 390):
            with self.subTest(width=width), self.page('after', width=width, html=render_page(build, fixtures=fixtures, league_logos=True), story=story, touch=width <= 600) as (page, _):
                # A kickoff time that has passed is not a confirmed live game; the top card says so.
                expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'unconfirmed-live')
                expect(page.locator('#nextup')).not_to_have_class(re.compile(r'\bnextup--live\b'))
                expect(page.locator('#nextup-status')).to_have_text('Kickoff 12:45 pm · status pending')
                expect(page.locator('#nextup-count')).to_have_text('Awaiting score')
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'eng')
                page.locator('#btn-menu').click()
                expect(page.locator('#comp-pills .lg')).to_have_count(2)
                self.assertTrue(page.locator('#comp-pills .lg').evaluate_all(
                    "els => els.every(el => getComputedStyle(el).backgroundImage !== 'none')"))
                self.assertLessEqual(page.locator('#drawer').evaluate('el => el.scrollWidth - el.clientWidth'), 1)
                page.locator('.league-priority > summary').click()
                page.get_by_role('button', name='Move La Liga up', exact=True).click()
                expect(page.locator('#drawer')).to_be_visible()
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'esp')
                self.assertEqual(page.locator('#comp-pills .fpill').first.get_attribute('data-key'), 'esp.1')
                page.reload()
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'esp')
                page.locator('#btn-menu').click()
                # Hiding La Liga hides its match from the schedule, not from the picks, where its priority still leads.
                page.locator('#comp-pills [data-key="esp.1"] .lg').click()
                expect(page.locator('li.row[data-id="esp"]')).to_be_hidden()
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'esp')
                page.locator('#btn-reset').click()
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'eng')
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
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'esp')
                expect(page.locator('.fpill--drag-ghost')).to_have_count(0)
                page.reload()
                expect(page.locator('#picks .pick:not([data-match-id="unconfirmed-live"])').first).to_have_attribute('data-match-id', 'esp')
                page.locator('#btn-menu').click()
                page.locator('#comp-pills [data-key="esp.1"]').click()
                expect(page.locator('#comp-pills [data-key="esp.1"]')).to_have_attribute('aria-pressed', 'false')

    def test_every_row_shows_the_pick_score_its_card_would(self):
        fixtures = [("eng", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("esp", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("off", "2026-10-07T19:00:00+00:00", "pre", "Peacock", "usa.1"),
                    ("done", "2026-10-07T15:00:00+00:00", "post", "ESPN+", "eng.1")]
        story = self.tagged_story()
        story['league_order'] = ['eng.1', 'esp.1']
        story['rankings'] = {mid: dict(score=score, popularity=score, gameplay=score, impact=score)
                             for mid, score in (('eng', 80), ('esp', 80.5), ('off', 30), ('done', 60))}

        def row_score(mid):
            return page.locator(f'li.row[data-id="{mid}"] .row__pick')

        def card_label(mid):        # the pick card's, or the top card's when the match is there
            return page.locator(f'#picks .pick[data-match-id="{mid}"] .pick__rating, #nextup[data-match-id="{mid}"] #nextup-rating')

        def card_number(mid):
            return card_label(mid).text_content().split('· ')[1].split('/')[0]
        for width in (1280, 320):
            with self.subTest(width=width), self.page('after', width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                page.locator('#btn-all').click()        # every row, the one off the lineup and the finished one too
                page.locator('li.row[data-id="done"]').evaluate("el => { el.closest('details').open = true; }")   # Earlier today is folded
                for mid in ('eng', 'esp', 'off', 'done'):
                    expect(row_score(mid)).to_be_visible()
                    expect(row_score(mid)).to_have_text(re.compile(r'^Pick \d+(\.\d)?$'))
                # The same number and breakdown as the match's pick card.
                for mid in ('eng', 'esp'):
                    expect(row_score(mid)).to_have_text('Pick ' + card_number(mid))
                    self.assertEqual(row_score(mid).get_attribute('title'), card_label(mid).get_attribute('title'))
                self.assertEqual(row_score('eng').get_attribute('aria-label'), 'Pick score ' + card_number('eng') + ' out of 100')
                # It follows the visitor's league priority.
                before = float(row_score('esp').text_content().split(' ')[1])
                page.locator('#btn-menu').click()
                page.locator('.league-priority > summary').click()
                page.get_by_role('button', name='Move La Liga up', exact=True).click()
                page.locator('#btn-filters-close').click()
                self.assertGreater(float(row_score('esp').text_content().split(' ')[1]), before)
                expect(row_score('esp')).to_have_text('Pick ' + card_number('esp'))
                # Inside its column at a phone's width, and the page doesn't scroll sideways.
                cell, label = page.locator('li.row[data-id="esp"] .row__time').bounding_box(), row_score('esp').bounding_box()
                self.assertLessEqual(label['x'] + label['width'], cell['x'] + cell['width'] + 14)       # into the column gap at most
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                page.locator('li.row[data-id="esp"]').screenshot(path=str(self.artifacts / f'row-pick-{width}.png'))
        # A page built without scores shows none.
        with self.page('after', html=re.sub(r' data-outlook="[^"]*"', '', render_page(build))) as (page, _):
            expect(page.locator('li.row .row__pick:visible')).to_have_count(0)

    def test_rolling_window_and_unrated_recommendation_fallback(self):
        fixtures = [("inside", "2026-10-08T16:59:00+00:00", "pre", "ESPN+"),
                    ("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+"),
                    ("later", "2026-10-10T17:00:00+00:00", "pre", "ESPN+")]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
            expect(page.locator("#tally-n")).to_have_text("1")
            expect(page.locator("#schedule-summary")).to_contain_text("1 upcoming")
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "inside")
            self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["edge", "later"])
            expect(page.locator("#picks-section")).to_be_visible()
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
                expect(page.locator("#forecast, #forecast-later")).to_have_count(0)
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

    def test_later_news_stays_in_match_cards_with_one_overview(self):
        fixtures = [("main", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1"),
                    ("second", "2026-10-09T20:00:00+00:00", "pre", "Apple TV", "usa.1")]
        story = self.tagged_story()
        story["lede_items"] = [{"segments": [{"text": "Main Friday story.", "match_ids": ["main"]}], "sources": []}]
        story["forecast"]["items"] = [
            {"segments": [{"text": "Unwanted section blurb.", "match_ids": ["second"]}], "sources": []}]
        story["rankings"] = {mid: dict(score=80, blurb=f"Match news for {mid}.", sources=[{"url": "https://example.com/report"}])
                             for mid in ("main", "second")}
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=fixtures), story=story) as (page, _):
                expect(page.locator("#forecast, #forecast-later, #period-h")).to_have_count(0)
                expect(page.locator("#story-lede")).to_have_text("Main Friday story.")
                expect(page.locator("#story-h")).to_have_text("Overview · Further ahead")
                expect(page.locator("#app")).not_to_contain_text("Unwanted section blurb")
                expect(page.locator("#nextup")).to_have_attribute("data-match-id", "main")
                expect(page.locator("#picks .pick")).to_have_count(1)
                for card, mid in (("#nextup", "main"), ("#picks .pick", "second")):
                    expect(page.locator(f'{card}[data-match-id="{mid}"] .row__story')).to_contain_text(f"Match news for {mid}.")
                    expect(page.locator(f'li.row[data-id="{mid}"] .row__story')).to_contain_text(f"Match news for {mid}.")
                expect(page.locator('details[data-b="later"]')).to_have_attribute("open", "")
                self.full_page_shot(page, f"empty-near-{width}.png")

    def test_later_overview_is_retained_and_rolls_into_window(self):
        html = render_page(build, fixtures=[
            ("upcoming", "2026-10-07T17:05:00+00:00", "pre", "ESPN+"),
            ("usual", "2026-10-08T18:00:00+00:00", "pre", "ESPN+")])
        story = self.tagged_story()
        near = {"segments": [{"text": "Near context.", "match_ids": ["upcoming"]}], "sources": []}
        later = {"segments": [{"text": "Later context.", "match_ids": ["usual"]}], "sources": []}
        story["lede_items"] = [near, later]
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#story-lede")).to_have_text("Near context.")
            page.locator("#btn-menu").click()
            page.locator('#comp-pills [data-key="eng.1"]').click()
            expect(page.locator("#story")).to_be_hidden()
        story["lede_items"] = [later]
        with self.page("after", html=html, story=story) as (page, _):
            expect(page.locator("#story-h")).to_have_text("Overview · Further ahead")
            expect(page.locator("#story-lede")).to_have_text("Later context.")
            page.evaluate("location.hash = '#at-20261007-1401'")
            expect(page.locator("#story-h")).to_have_text("Overview")
            expect(page.locator("#story-lede")).to_have_text("Later context.")
            expect(page.locator("#forecast, #forecast-later")).to_have_count(0)

    def test_match_notes_follow_services_even_in_everything_mode(self):
        fixtures = [("peacock", "2026-10-07T18:00:00+00:00", "pre", "Peacock", "eng.1")]
        story = self.tagged_story()
        story["notes"] = {"peacock": {"note": "Match-specific lineup news.", "sources": []}}
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            page.locator("#btn-all").click()
            expect(page.locator('li.row[data-id="peacock"]')).to_be_visible()
            expect(page.locator('li.row .row__story')).to_be_hidden()
            expect(page.locator('.miss__story')).to_have_count(0)
            page.locator("#btn-menu").click()
            page.locator('[data-kind="have"][data-key="peacock"]').click()
            expect(page.locator('li.row .row__story')).to_be_visible()

    def test_filtering_near_news_can_reveal_available_later_fallback(self):
        fixtures = [("near", "2026-10-07T18:00:00+00:00", "pre", "Apple TV", "usa.1"),
                    ("later", "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1")]
        story = self.tagged_story()
        story["later_reason"] = "Fallback for lineups without the earlier match."
        story["lede_items"] = [
            {"segments": [{"text": "Near match context.", "match_ids": ["near"]}], "sources": []},
            {"segments": [{"text": "Later match context.", "match_ids": ["later"]}], "sources": []}]
        with self.page("after", html=render_page(build, fixtures=fixtures), story=story) as (page, _):
            expect(page.locator("#story-lede")).to_contain_text("Near match context")
            expect(page.locator("#story-lede")).not_to_contain_text("Later match context")
            page.locator("#btn-menu").click()
            page.locator('[data-kind="have"][data-key="apple"]').click()
            expect(page.locator("#story-lede")).not_to_contain_text("Near match context")
            expect(page.locator("#story-lede")).to_contain_text("Later match context")
            expect(page.locator("#story-h")).to_have_text("Overview · Further ahead")
            page.locator('[data-kind="have"][data-key="apple"]').click()
            expect(page.locator("#story-lede")).to_contain_text("Near match context")

    def test_legacy_expired_unknown_and_completed_editorial_do_not_leak(self):
        story = self.tagged_story()
        story["lede_items"] = []
        for kind in ("legacy", "expired", "unknown", "completed"):
            changed = deepcopy(story)
            if kind == "legacy":
                changed["forecast"] = {"label": "Old", "today": "Untagged", "ahead": "Untagged"}
            elif kind == "expired":
                changed["focus_until"] = "2026-10-07T16:59:00Z"
                changed["lede_items"] = [{"segments": [{"text": "Expired", "match_ids": ["upcoming"]}]}]
            else:
                changed["lede_items"] = [{"segments": [{"text": "Stale", "match_ids": ["missing" if kind == "unknown" else "finished"]}]}]
            with self.subTest(kind=kind), self.page("after", story=changed) as (page, _):
                expect(page.locator("#story")).to_be_hidden()
                expect(page.locator("#schedule-summary")).to_contain_text("1 live · 5 upcoming")


    # ---- fixes from the October review: focus, preview, priority keys, saved settings, labels ----------
    @staticmethod
    def overview_story(sources=None):
        sources = [{"url": "https://news.example/a", "title": "A"}] if sources is None else sources
        text = "Arsenal meet Chelsea with the league lead at stake."
        return {"version": 1, "date": "2026-10-07", "generated_at": "2026-10-07T17:00:00Z", "focus_until": "2026-10-08T17:00:00Z",
                "notes": {}, "league_order": ["eng.1"], "league_blurbs": [],
                "lede_items": [{"text": text, "match_ids": ["upcoming"], "segments": [{"text": text, "match_ids": ["upcoming"]}],
                                "sources": sources}]}

    def test_keyboard_focus_survives_minute_ticks_goals_and_card_rebuilds(self):
        with self.page("after", story=self.overview_story()) as (page, feed):
            page.locator("#story-by a").first.focus()
            page.evaluate("window.__link = document.activeElement")
            page.clock.run_for(61000)       # the minute tick redraws the overview's facts, not its text
            # The same element, not a rebuilt copy: rebuilding unchanged text also loses a selection.
            self.assertTrue(page.evaluate("window.__link.isConnected && document.activeElement === window.__link"))
            details = page.locator('li.row[data-id="upcoming"] button.more')
            details.focus()
            feed["data"] = scoreboard("in")
            page.clock.run_for(60000)       # a goal re-renders the schedule and moves the row
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-state", "in")
            self.assertTrue(details.evaluate("el => el === document.activeElement"))
            pick = page.locator('#picks .pick[data-match-id="upcoming"] a.detail__table')
            pick.focus()
            page.evaluate("window.__oldButton = document.activeElement")
            clocked = deepcopy(scoreboard("in"))
            clocked["events"][0]["competitions"][0]["status"]["displayClock"] = "64'"
            feed["data"] = clocked
            page.clock.run_for(60000)       # a clock change rebuilds the pick cards
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text("64'")
            self.assertTrue(page.evaluate("!window.__oldButton.isConnected && document.activeElement.matches("
                                          "'#picks .pick[data-match-id=\"upcoming\"] a.detail__table')"))

    def test_hover_preview_stays_open_through_a_live_update(self):
        with self.page("after") as (page, feed):
            page.locator('li.row[data-id="upcoming"] button.more').hover()
            page.clock.run_for(250)
            preview = page.locator("#match-preview")
            expect(preview).to_be_visible()
            box = preview.bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(20, box["height"] / 2))
            feed["data"] = scoreboard("in")
            page.clock.run_for(60000)
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-state", "in")
            expect(preview).to_be_visible()
            page.mouse.move(5, 5)
            page.clock.run_for(400)
            expect(preview).to_be_hidden()

    def test_league_priority_buttons_move_a_league_repeatedly_from_the_keyboard(self):
        with self.page("after") as (page, _):
            page.locator("#btn-menu").click()
            page.locator(".league-priority > summary").click()
            first = page.locator("#league-order li").first
            league = first.get_attribute("data-league")
            name = page.evaluate("id => JSON.parse(document.getElementById('service-meta').textContent).leagues[id]", league)
            page.get_by_role("button", name=f"Move {name} down", exact=True).focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            expect(page.locator("#league-order li").nth(2)).to_have_attribute("data-league", league)
            self.assertEqual(page.evaluate("document.activeElement.getAttribute('aria-label')"), f"Move {name} down")

    def test_malformed_saved_settings_fall_back_to_defaults(self):
        with self.page("after") as (page, _):
            page.evaluate("""() => {
                localStorage.setItem('ssg2-comp-off', '{}'); localStorage.setItem('ssg5-leagues', '[1]');
                localStorage.setItem('ssg3-have', '"espn"'); localStorage.setItem('ssg4-league-order', '"x"');
                localStorage.setItem('ssg4-service-order', '5'); }""")
            page.reload()
            page.clock.run_for(1)
            expect(page.locator('li.row[data-id="upcoming"]')).to_be_visible()
            page.locator("#btn-menu").click()
            expect(page.locator('[data-kind="have"][aria-pressed="true"]')).to_have_count(len(build.OWNER))

    def test_saved_leagues_leave_unseen_competitions_at_their_default(self):
        hidden = next(lg for lg, info in build.LEAGUES.items() if info.get("default_off"))
        fixtures = [("pl", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                    ("quiet", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", hidden)]
        with self.page("after", html=render_page(build, fixtures=fixtures)) as (page, _):
            # Saved in the earlier format on a day without that competition's fixtures.
            page.evaluate("localStorage.setItem('ssg2-comp-off', JSON.stringify(['esp.1']))")
            page.reload()
            page.clock.run_for(1)
            expect(page.locator('li.row[data-id="pl"]')).to_be_visible()
            expect(page.locator('li.row[data-id="quiet"]')).to_be_hidden()
            page.locator("#btn-menu").click()
            page.locator(f'#drawer [data-kind="comp"][data-key="{hidden}"]').click()
            expect(page.locator('li.row[data-id="quiet"]')).to_be_visible()
            self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('ssg5-leagues'))"), {"on": [hidden], "off": ["esp.1"]})
            self.assertIsNone(page.evaluate("localStorage.getItem('ssg2-comp-off')"))

    def test_us_national_team_shows_by_default_until_its_competition_is_switched_off(self):
        fixtures = [("usa", "2026-10-07T23:00:00+00:00", "pre", "HBO Max", "fifa.friendly.w"),
                    ("india", "2026-10-07T23:30:00+00:00", "pre", "HBO Max", "fifa.friendly.w")]
        names = {"usa": ("United States", "Spain"), "india": ("India", "Russia")}
        with self.page("after", html=render_page(build, fixtures=fixtures, team_names=names)) as (page, _):
            usa, india = page.locator('li.row[data-id="usa"]'), page.locator('li.row[data-id="india"]')
            expect(usa).to_be_visible()
            expect(india).to_be_hidden()
            page.locator("#btn-menu").click()
            pill = page.locator('#drawer [data-kind="comp"][data-key="fifa.friendly.w"]')
            pill.click()
            expect(india).to_be_visible()
            pill.click()                     # switched off by the viewer, not by default: both go
            expect(usa).to_be_hidden()
            expect(india).to_be_hidden()
            page.locator("#btn-reset").click()
            expect(usa).to_be_visible()

    def test_24_hour_clocks_keep_their_minutes(self):
        fixtures = [("next", "2026-10-07T18:00:00+00:00", "pre", "ESPN+")]
        with self.page("after", html=render_page(build, fixtures=fixtures), locale="en-GB") as (page, _):
            expect(page.locator("#schedule-summary")).to_contain_text("Next kickoff · 14:00")

    def test_kickoff_without_word_from_espn_awaits_its_score(self):
        with self.page("after", at="20261007-1310") as (page, _):
            expect(page.locator('li.row[data-id="upcoming"] .row__live')).to_have_text("Awaiting score")
            expect(page.locator('li.row[data-id="live"] .row__live')).to_have_text("Live")

    def test_a_card_without_a_blurb_shows_what_its_details_panel_holds(self):
        # The fixture's home team is first of two with 18 points, a 6-0-2 record and a top scorer, A. Player, on 6.
        for width in (1280, 390, 320):
            with self.subTest(width=width), self.page("after", width=width) as (page, _):
                card = page.locator("#picks .pick").first
                expect(card).to_have_class(re.compile(r"\bmatch--facts\b"))
                home = card.locator(".team").first
                expect(home.locator(".team__table")).to_have_text("1st of 2 · 18 pts · 6-0-2")
                expect(home.locator(".team__rec")).to_have_attribute("title", "Won 6, drawn 0, lost 2 this season")
                expect(home.locator(".team__scorer")).to_have_text("Top scorer A. Player, 6 goals")
                expect(home.locator(".team__scorer")).to_be_visible()
                expect(card.locator(".more")).to_have_count(0)                 # nothing left hidden behind it
                expect(card.locator(".match__links a")).to_have_text(["Add to calendar", "League table"])
                expect(page.locator("#nextup")).to_have_class(re.compile(r"\bmatch--facts\b"))
                expect(page.locator("#nextup .match__links")).to_be_visible()
                # The schedule's rows stay compact: the same facts wait in their Details panel.
                row = page.locator(f'li.row[data-id="{card.get_attribute("data-match-id")}"]')
                expect(row.locator(".team__rec").first).to_be_hidden()
                expect(row.locator(".team__scorer").first).to_be_hidden()
                expect(row.locator("button.more")).to_be_visible()
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                card.screenshot(path=str(self.artifacts / f"pick-facts-{width}.png"))
                card.get_by_role("link", name="League table").click()
                expect(page.locator('.tables__item[data-lg="eng.1"]')).to_have_attribute("open", "")
        # With a blurb the card keeps it and its Details button, and the facts stay in the panel.
        story = self.overview_story()
        story["rankings"] = {mid: {"score": 60, "popularity": 60, "gameplay": 60, "impact": 60, "blurb": f"Context for {mid}.",
                                   "sources": [{"url": "https://news.example/a", "title": "A"}]} for mid in ("upcoming", "midnight", "late", "dawn")}
        with self.page("after", story=story) as (page, _):
            card = page.locator("#picks .pick").first
            expect(card.locator(".row__story")).to_have_count(1)
            expect(card).not_to_have_class(re.compile(r"\bmatch--facts\b"))
            expect(card.locator(".team__scorer").first).to_be_hidden()
            expect(card.locator("button.more")).to_have_count(1)
            expect(card.locator(".match__links")).to_have_count(0)

    def test_picks_are_scored_before_the_ai_rates_them(self):
        with self.page("after") as (page, _):
            expect(page.locator("#picks-h")).to_have_text("Top three")
            expect(page.locator("#picks-sub")).to_have_text("Selected by Outlook score + league priority from every service and competition · shown in kickoff order")
        story = self.overview_story()
        story["rankings"] = {mid: {"score": 50, "popularity": 50, "gameplay": 50, "impact": 50} for mid in ("upcoming", "midnight", "late")}
        with self.page("after", story=story) as (page, _):
            expect(page.locator("#picks-h")).to_have_text("Top three")
            expect(page.locator("#picks-sub")).to_have_text("Selected by AI rating + Outlook score + league priority from every service and competition · shown in kickoff order")
            # A story that records no model leaves the tooltip's rating unattributed rather than guessed.
            title = page.locator('#picks .pick[data-match-id="upcoming"] .pick__rating').get_attribute("title")
            self.assertIn("AI rating: Popularity 50", title)
        # A page built without scores (an older build) still lists the next matches, without calling them picks.
        with self.page("after", html=re.sub(r' data-outlook="[^"]*"', "", render_page(build))) as (page, _):
            expect(page.locator("#picks-h")).to_have_text("Upcoming")
            expect(page.locator("#picks-sub")).to_have_text("In kickoff order · every service and competition · no ratings yet")

    # Five matches in the next 24 hours, one league, statures chosen so the Outlook scores differ:
    # 100 x (0.40 x stature / 150 + 0.25 x 0.5 + 0 + 0 + 0.10 x 0.5) gives a 25.5, b 57.5, c 33.5,
    # d 49.5 and e 41.5. The soonest, a, takes the top card; the picks come from the other four.
    SCORED = [(mid, f"2026-10-07T{hour}:00:00+00:00", "pre", "ESPN+") for mid, hour in zip("abcde", (18, 19, 20, 21, 22))]
    STATURE = {"a": 30, "b": 150, "c": 60, "d": 120, "e": 90}

    def test_with_ai_off_picks_rank_by_the_outlook_score_and_no_story_is_asked_for(self):
        html = render_page(build, fixtures=self.SCORED, stature=self.STATURE, ai=False)
        with self.page("after", html=html, story=self.overview_story()) as (page, feed):
            page.clock.run_for(11 * 60000)      # past the ten minutes after which an open tab asks again
            self.assertEqual(feed["stories"], 0)
            expect(page.locator("#story")).to_be_hidden()
            expect(page.locator("#story-by")).to_have_text("")      # the byline is never filled in
            self.assertNotIn("AI Summary", page.locator("body").inner_text())
            expect(page.locator("#nextup")).to_have_attribute("data-match-id", "a")
            self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["b", "d", "e"])
            expect(page.locator("#picks-sub")).to_have_text("Selected by Outlook score + league priority from every service and competition · shown in kickoff order")
            label = page.locator('#picks .pick[data-match-id="b"] .pick__rating')
            expect(label).to_have_text("Pick score · 66/100")      # 80% of 57.5 and 20% of league priority 100
            self.assertEqual(label.get_attribute("title"),
                             "80% Outlook score (57.5) + 20% league priority (100). Outlook score 57.5: occasion 100 · "
                             "evenly matched no data · stakes 0 · TV 0 · goals expected no data.")
            expect(page.locator("footer")).to_contain_text("No AI is used on this page")
            expect(page.locator("#priority-hint")).to_contain_text("80% the Outlook score and 20% this order")

    def test_the_ai_rating_and_the_outlook_score_share_the_interest(self):
        # The AI rating alone would drop d (its lowest), the Outlook score alone c (its lowest). Their
        # mean, b 58.75, c 51.75, d 49.75, e 46.75, drops e.
        story = dict(self.overview_story(), model="gpt-6.1-sol-2026-09-01", requested_model="gpt-6.1-sol")
        story["rankings"] = {mid: {"score": score, "popularity": score, "gameplay": score, "impact": score}
                             for mid, score in (("a", 50), ("b", 60), ("c", 70), ("d", 50), ("e", 52))}
        html = render_page(build, fixtures=self.SCORED, stature=self.STATURE)
        with self.page("after", html=html, story=story) as (page, feed):
            self.assertGreater(feed["stories"], 0)
            self.assertEqual(page.locator("#picks .pick").evaluate_all("els => els.map(e => e.dataset.matchId)"), ["b", "c", "d"])
            expect(page.locator("#picks-sub")).to_have_text("Selected by AI rating + Outlook score + league priority from every service and competition · shown in kickoff order")
            label = page.locator('#picks .pick[data-match-id="b"] .pick__rating')
            expect(label).to_have_text("Pick score · 67/100")      # 80% of 58.75 and 20% of 100
            # The model settings.toml asked for, not the dated snapshot that answered.
            self.assertEqual(label.get_attribute("title"),
                             "80% interest (58.8) + 20% league priority (100). Interest: 50% AI rating (60) + 50% Outlook score "
                             "(57.5). AI rating by gpt-6.1-sol: Popularity 60 · Expected gameplay 60 · Competitive impact 60. "
                             "Outlook score 57.5: occasion 100 · evenly matched no data · stakes 0 · TV 0 · goals expected no data.")
            expect(page.locator("footer")).to_contain_text("50% the AI rating and 50% the Outlook score")
            expect(page.locator("footer")).to_contain_text("gpt-6.1-sol, an OpenAI AI model, rates every match")

    def test_malformed_story_does_not_stop_filters_or_scores(self):
        story = self.overview_story(sources="not a list")
        story.update(league_order="eng.1", notes={"upcoming": {"note": "A note.", "sources": "nope"}},
                     rankings={"upcoming": {"score": 60, "popularity": 60, "gameplay": 60, "impact": 60, "blurb": 7,
                                            "sources": [{"url": "https://news.example/x", "title": 99}]}},
                     league_blurbs=["junk", None, {"segments": "junk"}])
        with self.page("after", story=story) as (page, feed):
            expect(page.locator("#story-lede")).to_contain_text("league lead")
            page.locator("#btn-menu").click()
            page.locator("#btn-clear").click()
            expect(page.locator('li.row[data-id="upcoming"]')).to_be_hidden()
            page.locator("#btn-reset").click()
            page.locator("#btn-filters-close").click()
            feed["data"] = scoreboard("in")
            page.clock.run_for(60000)
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-state", "in")

    def test_sources_name_espn_facts_and_number_a_repeated_site(self):
        sources = [{"url": "https://www.espn.com/soccer/match/_/gameId/upcoming", "title": "ESPN table and form", "kind": "facts"},
                   {"url": "https://news.example/a", "title": "A"}, {"url": "https://news.example/b", "title": "B"}]
        with self.page("after", story=self.overview_story(sources)) as (page, _):
            expect(page.locator("#story-by a")).to_have_text(["ESPN table and form", "news.example", "news.example (2)"])
            self.assertIn("not from reporting", page.locator("#story-by a").first.get_attribute("title"))
            # The page's disclosure that the overview is written by AI, ahead of its sources.
            expect(page.locator("#story-by")).to_have_text("AI Summary · ESPN table and form, news.example, news.example (2)")
        with self.page("after", story=self.overview_story([])) as (page, _):
            expect(page.locator("#story-by")).to_have_text("AI Summary")

    def test_the_ratings_designs_overview_reads_the_same_whatever_the_filters(self):
        text = "Arsenal meet Chelsea with the league lead at stake, and Spain's women visit the United States."
        story = dict(self.overview_story(), lede_items=[], overview={
            "text": text, "model": "gemini-3.1-pro-preview", "requested_model": "gemini-3.1-pro-preview", "effort": "medium",
            "sources": [{"url": "https://news.example/a", "title": "A"}, {"url": "https://news.example/b", "title": "B"},
                        {"url": "javascript:alert(1)", "title": "not a page"}]})
        with self.page("after", story=story) as (page, _):
            lede = page.locator("#story-lede")
            expect(page.locator("#story")).to_be_visible()
            expect(page.locator("#story-h")).to_have_text("Overview")
            expect(lede).to_have_text(text)
            # One plain paragraph: no phrases to dim or hide, and only real links among its sources.
            expect(lede.locator(".editorial-part")).to_have_count(0)
            expect(page.locator("#story-by")).to_have_text("AI Summary · news.example, news.example (2)")
            # With no services and no competitions, every visitor still reads the same overview.
            page.locator("#btn-menu").click()
            page.locator("#btn-clear").click()
            page.locator("#btn-filters-close").click()
            expect(page.locator('li.row[data-id="upcoming"]')).to_be_hidden()
            expect(page.locator("#story")).to_be_visible()
            expect(lede).to_have_text(text)
        # A malformed overview is ignored, and the page falls back to a full-design story's overview if it has one.
        broken = dict(self.overview_story(), overview={"text": ["not", "text"]})
        with self.page("after", story=broken) as (page, _):
            expect(page.locator("#story-lede")).to_contain_text("league lead at stake")
        empty = dict(self.overview_story(), lede_items=[], overview={"text": "  "})
        with self.page("after", story=empty) as (page, _):
            expect(page.locator("#story")).to_be_hidden()


# Exit status when Chromium can't start: the checks didn't run, which says nothing about the page.
# 77 is automake's "skipped"; Python and argparse already use 1 and 2 for their own errors.
NOT_RUN = 77


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
        try:
            # Playwright's own Chromium, the version it was built against, unless BROWSER_EXECUTABLE names
            # another. A runner's system Chromium (GitHub's images put one on PATH) changes from week to week.
            try:
                browser = playwright.chromium.launch(executable_path=os.environ.get("BROWSER_EXECUTABLE") or None)
            except Exception as e:
                print(f"Chromium could not start: {e}", file=sys.stderr)
                return NOT_RUN
            try:
                BrowserChecks.browser = browser
                BrowserChecks.base = f"http://127.0.0.1:{server.server_port}"
                BrowserChecks.targets = list(pages)
                BrowserChecks.artifacts = args.artifacts.resolve()
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BrowserChecks))
            finally:
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
