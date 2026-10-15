"""Offline schedule refresh checks: python -m tests.browser_schedule [--browser webkit]."""
import argparse
from copy import deepcopy
from functools import partial
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
import unittest

from playwright.sync_api import expect, sync_playwright

import build
from tests.browser import BrowserChecks, QuietHandler
from tests.page_fixture import render_page, scoreboard


FIXTURES = [
    ("upcoming", "2026-10-07T17:05:00+00:00", "pre", "USA Network", "eng.1"),
    ("spanish", "2026-10-08T20:00:00+00:00", "pre", "Universo", "mex.1"),
    ("removed", "2026-10-09T18:00:00+00:00", "pre", "USA Network", "eng.1"),
]


class SnapshotHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html, self.rows, self.depth, self.start, self.revision = html, [], 0, 0, ""
        self.lines = [0]
        for line in html.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.feed(html)

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "app":
            self.revision = attrs.get("data-runtime-revision", "")
        if tag == "li":
            if self.depth:
                self.depth += 1
            elif "row" in attrs.get("class", "").split():
                self.depth, self.start = 1, self.source_offset()

    def handle_endtag(self, tag):
        if tag == "li" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.rows.append(self.html[self.start:self.source_offset() + len("</li>")])


def snapshot(fixtures=FIXTURES, generated="2026-10-07T17:01:00Z"):
    html = SnapshotHTML(render_page(build, fixtures=fixtures))
    return {"version": 1, "complete": True, "generated_at": generated,
            "runtime_revision": html.revision, "rows": html.rows, "logos": {}}


class ScheduleChecks(unittest.TestCase):
    page = BrowserChecks.page

    def serve(self, page, payload):
        state = {"payload": payload, "requests": 0, "fail": False}

        def request(route):
            state["requests"] += 1
            route.fulfill(status=503 if state["fail"] else 200, content_type="application/json",
                          body=json.dumps(state["payload"]))
        page.route("**/schedule.json", request)
        return state

    def tick(self, page, state, minutes=5):
        before = state["requests"]
        page.clock.fast_forward(minutes * 60000 + 100)
        page.wait_for_function("document.querySelector('#schedule-note') !== null")
        # A DOM assertion below waits for the fetch to finish; networkidle also covers rejected data.
        page.wait_for_load_state("networkidle")
        self.assertGreater(state["requests"], before)

    def test_channels_kickoff_added_removed_and_package_filters_update(self):
        fixtures = [
            ("upcoming", "2026-10-07T18:05:00+00:00", "pre", "TUDN", "eng.1"),
            ("spanish", "2026-10-08T20:00:00+00:00", "pre", "Universo", "mex.1"),
            ("new", "2026-10-09T20:00:00+00:00", "pre", "Paramount+", "ita.1"),
            ("replay", "2026-10-07T14:00:00+00:00", "post", "TUDN", "mex.1"),
        ]
        for width in (1280, 390):
            with self.subTest(width=width), self.page("after", width=width, html=render_page(build, fixtures=FIXTURES)) as (page, _):
                self.assertTrue(page.locator('#app').get_attribute('data-runtime-revision'))
                expect(page.locator('li.row[data-id="spanish"]')).not_to_be_visible()
                state = self.serve(page, snapshot(fixtures))
                self.tick(page, state)
                row = page.locator('li.row[data-id="upcoming"]')
                expect(row).to_have_attribute('data-utc', '2026-10-07T18:05:00Z')
                expect(row.locator('.network summary')).to_contain_text('TUDN')
                expect(page.locator('li.row[data-id="new"]')).to_be_visible()
                expect(page.locator('li.row[data-id="removed"]')).to_have_count(0)
                expect(page.locator('li.row[data-id="spanish"]')).not_to_be_visible()
                expect(page.locator('#outlook-body [data-b="live"] li[data-id="replay"]')).to_have_count(0)
                expect(page.locator('#picks [data-match-id="replay"]')).to_have_count(0)
                expect(page.locator('#league-strip [data-league="ita.1"]')).to_be_visible()
                page.locator('#btn-menu').click()
                page.locator('[data-settings-tab="leagues"]').click()
                expect(page.locator('[data-kind="comp"][data-key="ita.1"] .fpill__n')).to_have_text('1')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)

    def test_preferences_ai_draft_and_open_dialog_survive(self):
        with self.page("after", html=render_page(build, fixtures=FIXTURES)) as (page, _):
            page.locator('#btn-menu').click()
            page.locator('[data-settings-tab="leagues"]').click()
            page.locator('[data-kind="comp"][data-key="mex.1"]').click()
            page.locator('[data-settings-tab="ai"]').click()
            page.locator('#ai-response').fill('My unfinished response')
            page.locator('#btn-menu').click()
            before = page.evaluate('JSON.stringify(localStorage)')
            page.locator('li.row[data-id="upcoming"] .more').click()
            payload = snapshot([*FIXTURES[:1], ("mex", "2026-10-08T20:00:00Z", "pre", "TUDN", "mex.1")])
            payload['rows'] = [row.replace('Fixture Stadium', 'Updated Stadium') for row in payload['rows']]
            state = self.serve(page, payload)
            self.tick(page, state)
            expect(page.locator('#match-dialog')).to_be_visible()
            expect(page.locator('#match-dialog-content')).to_contain_text('Updated Stadium')
            expect(page.locator('li.row[data-id="mex"]')).not_to_be_visible()
            self.assertEqual(page.evaluate('JSON.stringify(localStorage)'), before)
            expect(page.locator('#ai-response')).to_have_value('My unfinished response')
            page.locator('#match-dialog-close').click()
            expect(page.locator('li.row[data-id="upcoming"] .more')).to_be_focused()

    def test_a_newer_live_score_survives_an_older_schedule_snapshot(self):
        with self.page("after", html=render_page(build, fixtures=FIXTURES)) as (page, feed):
            feed['data'] = scoreboard('in')
            page.clock.fast_forward(120100)
            expect(page.locator('li.row[data-id="upcoming"] .score').first).to_have_text('2')
            state = self.serve(page, snapshot())
            self.tick(page, state)
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row).to_have_attribute('data-state', 'in')
            expect(row.locator('.score').first).to_have_text('2')
            expect(row.locator('.row__status')).to_contain_text("63'")

    def test_bad_snapshots_and_outages_preserve_the_current_schedule(self):
        base = snapshot()
        invalid = []
        for field, value in [('complete', False), ('version', 99), ('runtime_revision', 'different'),
                             ('generated_at', '2026-10-07T16:59:00Z'), ('generated_at', '2026-10-08T17:00:00Z')]:
            item = deepcopy(base); item[field] = value; invalid.append(item)
        for markup in ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>']:
            item = deepcopy(base); item['rows'][0] = item['rows'][0].replace('</li>', markup + '</li>'); invalid.append(item)
        item = deepcopy(base); item['rows'][0] = item['rows'][0].replace('https://www.espn.com/', 'javascript:alert(1)//'); invalid.append(item)
        item = deepcopy(base); item['rows'].append(item['rows'][0]); invalid.append(item)
        for i, payload in enumerate(invalid):
            with self.subTest(case=i), self.page("after", html=render_page(build, fixtures=FIXTURES)) as (page, _):
                state = self.serve(page, payload)
                self.tick(page, state)
                expect(page.locator('#app')).not_to_have_attribute('data-schedule-built', payload['generated_at'])
                expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute('data-utc', '2026-10-07T17:05:00Z')
                expect(page.locator('li.row[data-id="removed"]')).to_be_visible()
        with self.page("after", html=render_page(build, fixtures=FIXTURES)) as (page, _):
            state = self.serve(page, base); state['fail'] = True
            self.tick(page, state)
            expect(page.locator('li.row[data-id="upcoming"]')).to_be_visible()
            expect(page.locator('#schedule-note')).to_contain_text('saved schedule remains')
            state['fail'] = False
            self.tick(page, state, minutes=10)
            expect(page.locator('#app')).to_have_attribute('data-schedule-built', base['generated_at'])

    def test_healthy_empty_snapshot_and_returning_competition_keep_preferences(self):
        with self.page("after", html=render_page(build, fixtures=FIXTURES)) as (page, _):
            page.locator('#btn-menu').click()
            page.locator('[data-settings-tab="leagues"]').click()
            page.locator('[data-kind="comp"][data-key="mex.1"]').click()
            before = page.evaluate('localStorage.getItem("ssg5-leagues")')
            state = self.serve(page, snapshot([]))
            self.tick(page, state)
            expect(page.locator('li.row')).to_have_count(0)
            expect(page.locator('#outlook-body .empty')).to_be_visible()
            expect(page.locator('[data-kind="comp"]')).to_have_count(0)
            state['payload'] = snapshot([('returning', '2026-10-08T20:00:00Z', 'pre', 'TUDN', 'mex.1')], generated='2026-10-07T17:06:00Z')
            self.tick(page, state)
            expect(page.locator('#comp-disabled [data-key="mex.1"]')).to_be_visible()
            expect(page.locator('li.row[data-id="returning"]')).not_to_be_visible()
            self.assertEqual(page.evaluate('localStorage.getItem("ssg5-leagues")'), before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=('chromium', 'webkit'), default='chromium')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        folder = Path(directory) / 'after'; folder.mkdir()
        (folder / 'index.html').write_text(render_page(build, fixtures=FIXTURES), encoding='utf-8')
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=directory))
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            launch = {'executable_path': os.environ.get('BROWSER_EXECUTABLE') or None} if args.browser == 'chromium' else {}
            browser = getattr(playwright, args.browser).launch(**launch)
            try:
                ScheduleChecks.browser = browser
                ScheduleChecks.base = f'http://127.0.0.1:{server.server_port}'
                result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScheduleChecks))
            finally:
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
