"""Synthetic, network-blocked response freshness checks in Chromium or WebKit."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

import build
import watch_links
from tests.page_fixture import BUILT_AT, SOURCE_RECEIPT, render_page, scoreboard
from tests.browser_schedule import SnapshotHTML
from tool.companion_publication_capture.capture import load_browser_resources


ENGINE = "chromium"
URL = "http://127.0.0.1:8765/index.html?scoresbase=http%3A%2F%2F127.0.0.1%3A8765%2Fespn%2F"
PLAYER = "https://tv.youtube.com/watch/Qi_C4zyXBIg?vp=0gEEEgIwAQ%3D%3D"
FIXTURES = [("upcoming", "2026-10-07T16:30:00Z", "in", "USA Network", "eng.1")]


class LiveFreshnessChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = getattr(cls.playwright, ENGINE).launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    @contextmanager
    def page(self, *, receipts=None, fixtures=FIXTURES, feed=None):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            with patch.dict(watch_links.YOUTUBE_TV_LINKS,
                            {"upcoming": {"url": PLAYER, "channel": "USA Network"}}, clear=True):
                html = render_page(build, fixtures=fixtures, ai=False,
                                   source_receipts=receipts, inline_runtime=False)
            (site / "index.html").write_text(html)
            runtime = build.outlook_runtime.publish_browser(site)
            _, resources = load_browser_resources(site)
            state = {"feed": feed or {"events": []}, "fail": False, "schedule": None,
                     "score_requests": 0, "fulfilled": [], "blocked": []}
            context = self.browser.new_context(timezone_id="America/New_York")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                url = urlsplit(route.request.url)
                name = url.path.rsplit("/", 1)[-1]
                if url.netloc == "127.0.0.1:8765" and url.path.startswith("/espn/"):
                    state["score_requests"] += 1
                    route.fulfill(status=503 if state["fail"] else 200,
                                  content_type="application/json", body=json.dumps(state["feed"]))
                elif url.netloc == "127.0.0.1:8765" and name == "schedule.json":
                    route.fulfill(status=200 if state["schedule"] else 404,
                                  content_type="application/json", body=json.dumps(state["schedule"]))
                elif url.netloc == "127.0.0.1:8765" and name in resources:
                    state["fulfilled"].append(name)
                    route.fulfill(content_type="application/javascript" if name.endswith(".js") else "text/html",
                                  body=resources[name])
                else:
                    state["blocked"].append(route.request.url)
                    route.abort()

            context.route("**/*", route_request)
            page.clock.install(time=BUILT_AT - timedelta(seconds=1))
            page.clock.pause_at(BUILT_AT)
            try:
                page.goto(URL)
                page.wait_for_selector("#park", state="attached")
                # Drain startup timers without moving the receipt clock while
                # response bodies are still arriving in different engines.
                page.clock.run_for(0)
                expect(page.locator("#livenote")).to_contain_text("last checked")
                expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
                self.assertIn(runtime, state["fulfilled"])
                yield page, state
                self.assertEqual(errors, [])
            finally:
                context.close()

    def test_saved_receipt_expires_at_exact_boundary_despite_successful_missing_events(self):
        with self.page() as (page, state):
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row).to_have_attribute("data-source-received-at", SOURCE_RECEIPT)
            expect(row.locator("a.network__link")).to_have_attribute("href", PLAYER)
            page.clock.run_for(179999)
            expect(row.locator(".row__live")).to_have_text("Live")
            expect(row.locator("a.network__link")).to_have_count(1)
            page.clock.run_for(1)
            expect(row.locator(".row__status")).to_have_text("Last known · 30'")
            expect(page.locator('[data-b="live"] li[data-id="upcoming"]')).to_have_count(0)
            expect(page.locator('[data-b="today"] li[data-id="upcoming"]')).to_have_count(1)
            expect(row.locator("a.network__link")).to_have_count(0)
            expect(page.locator("#nextup a.network__link")).to_have_count(0)
            expect(page.locator("#nextup-status")).to_contain_text("Last known")
            expect(row.locator(".team .score")).to_have_text(["0", "0"])
            expect(row).to_have_attribute("data-state", "in")
            expect(row).to_have_attribute("data-source-received-at", SOURCE_RECEIPT)
            self.assertGreaterEqual(state["score_requests"], 3)

    def test_unknown_future_and_old_receipts_are_retained_without_a_live_claim(self):
        fixtures = [FIXTURES[0],
                    ("unknown", *FIXTURES[0][1:]), ("future", *FIXTURES[0][1:])]
        receipts = {"upcoming": "2026-10-07T16:56:00.123456Z", "unknown": None,
                    "future": "2026-10-07T17:01:00Z"}
        with self.page(fixtures=fixtures, receipts=receipts) as (page, _):
            for match_id in receipts:
                row = page.locator(f'li.row[data-id="{match_id}"]')
                expect(row.locator(".row__status")).to_have_text("Last known · 30'")
                expect(row.locator(".row__live")).to_be_hidden()
                expect(row.locator(".team .score")).to_have_text(["0", "0"])
                self.assertEqual(row.get_attribute("data-source-received-at"), receipts[match_id])
            expect(page.locator('[data-b="live"]')).to_have_count(0)

    def test_accepted_response_renews_only_supplied_valid_events_and_can_recover(self):
        second = ("missing", *FIXTURES[0][1:])
        with self.page(fixtures=[FIXTURES[0], second],
                       receipts={"upcoming": None, "missing": None}, feed=scoreboard("in")) as (page, state):
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row).to_have_attribute("data-source-received-at", "2026-10-07T17:00:00.000Z")
            expect(row.locator(".team .score")).to_have_text(["2", "1"])
            self.assertIsNone(page.locator('li.row[data-id="missing"]').get_attribute("data-source-received-at"))
            state["feed"] = {"events": [{"id": "upcoming", "competitions": []}]}
            page.clock.run_for(180000)
            expect(row.locator(".row__status")).to_have_text("Last known · 63'")
            expect(row).to_have_attribute("data-source-received-at", "2026-10-07T17:00:00.000Z")
            state["feed"] = scoreboard("in")
            page.clock.run_for(60000)
            expect(row.locator(".row__status")).to_have_text("63'")
            expect(row.locator("a.network__link")).to_have_count(1)
            expect(row).to_have_attribute("data-source-received-at", "2026-10-07T17:04:00.000Z")

    def test_failed_request_does_not_extend_receipt_or_live_links(self):
        with self.page() as (page, state):
            state["fail"] = True
            page.clock.run_for(180000)
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row.locator(".row__status")).to_have_text("Last known · 30'")
            expect(row.locator("a.network__link")).to_have_count(0)
            expect(row).to_have_attribute("data-source-received-at", SOURCE_RECEIPT)
            expect(page.locator("#livenote")).to_contain_text("latest check didn’t get through")

    def test_future_receipt_activates_and_expires_without_being_rewritten(self):
        receipt = "2026-10-07T17:00:02.500Z"
        with self.page(receipts={"upcoming": receipt}) as (page, _):
            row = page.locator('li.row[data-id="upcoming"]')
            page.clock.run_for(2499)
            expect(row.locator("a.network__link")).to_have_count(0)
            expect(row.locator(".row__status")).to_have_text("Last known · 30'")
            page.clock.run_for(1)
            expect(row.locator("a.network__link")).to_have_count(1)
            expect(row.locator(".row__status")).to_have_text("30'")
            page.clock.run_for(180000)
            expect(row.locator("a.network__link")).to_have_count(0)
            expect(row.locator(".row__status")).to_have_text("Last known · 30'")
            expect(row).to_have_attribute("data-source-received-at", receipt)

    def test_one_response_supplies_the_same_receipt_to_both_accepted_events(self):
        second = ("second", *FIXTURES[0][1:])
        feed = scoreboard("in")
        event = deepcopy(feed["events"][0])
        event["id"] = "second"
        feed["events"].append(event)
        with self.page(fixtures=[FIXTURES[0], second], feed=feed,
                       receipts={"upcoming": None, "second": None}) as (page, _):
            for match_id in ("upcoming", "second"):
                row = page.locator(f'li.row[data-id="{match_id}"]')
                expect(row).to_have_attribute("data-source-received-at", "2026-10-07T17:00:00.000Z")
                expect(row.locator(".team .score")).to_have_text(["2", "1"])

    def test_completed_response_removes_cloned_live_links_before_another_league_answers(self):
        other = ("spain", "2026-10-07T17:05:00Z", "pre", "USA Network", "esp.1")
        with self.page(fixtures=[FIXTURES[0], other]) as (page, state):
            held = []
            page.route("**/espn/esp.1/**", lambda route: held.append(route))
            state["feed"] = scoreboard("post")
            page.clock.run_for(60000)
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute('data-state', 'post')
            self.assertEqual(len(held), 1)
            page.clock.run_for(2000)
            expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'spain')
            finished = page.locator('#nextup-track .nextup[data-match-id="upcoming"]')
            expect(finished.locator('.nextup__status')).to_have_text('FT')
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text('FT')
            expect(finished.locator('.team .score')).to_have_text(['2', '1'])
            expect(finished.locator('a.network__link')).to_have_count(0)
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row.locator('a.network__link')).to_have_count(0)
            expect(row.locator('.row__live')).to_be_hidden()
            expect(page.locator('[data-b="earlier"] li[data-id="upcoming"]')).to_have_count(1)
            expect(page.locator('[data-b="live"] li[data-id="upcoming"]')).to_have_count(0)
            held.pop().fulfill(content_type='application/json', body='{"events":[]}')
            expect(page.locator('#nextup')).to_have_attribute('data-match-id', 'spain')

    def test_schedule_reapplication_preserves_original_overlay_receipt(self):
        with self.page() as (page, state):
            state["feed"] = scoreboard("in")
            page.clock.run_for(60000)
            expect(page.locator('li.row[data-id="upcoming"] .team .score')).to_have_text(["2", "1"])
            initial = page.locator('li.row[data-id="upcoming"]').get_attribute("data-source-received-at")
            replacement = SnapshotHTML(render_page(build, fixtures=FIXTURES, ai=False,
                                                    source_receipts={"upcoming": "2026-10-07T16:59:00Z"}))
            state["schedule"] = {"version": 1, "complete": True, "runtime_revision": replacement.revision,
                                 "generated_at": "2026-10-07T17:00:30Z", "rows": replacement.rows, "logos": {}}
            state["feed"] = {"events": []}
            page.clock.run_for(240000)
            expect(page.locator("#app")).to_have_attribute("data-schedule-built", "2026-10-07T17:00:30Z")
            row = page.locator('li.row[data-id="upcoming"]')
            expect(row).to_have_attribute("data-source-received-at", initial)
            expect(row.locator(".row__status")).to_have_text("Last known · 63'")
            expect(row.locator(".team .score")).to_have_text(["2", "1"])
            expect(row.locator("a.network__link")).to_have_count(0)

    def test_past_last_known_fixture_appears_once_in_recent_carousel(self):
        fixtures = [("upcoming", "2026-10-06T16:30:00Z", "in", "USA Network", "eng.1")]
        with self.page(fixtures=fixtures, receipts={"upcoming": None}) as (page, _):
            expect(page.locator('#nextup-track [data-match-id="upcoming"]')).to_have_count(1)
            expect(page.locator('[data-b="yesterday"] li[data-id="upcoming"]')).to_have_count(1)
            expect(page.locator('#picks [data-match-id="upcoming"]')).to_have_count(0)
            expect(page.locator('#nextup-status')).to_have_text("Last known · 30'")

    def test_microsecond_expiry_removes_an_open_link_at_first_representable_instant(self):
        with self.page(receipts={"upcoming": "2026-10-07T16:57:03.123456Z"}) as (page, _):
            page.locator('#nextup .network summary').click()
            page.locator('#nextup a.network__link').focus()
            page.clock.run_for(3123)
            expect(page.locator('#nextup a.network__link')).to_be_visible()
            page.clock.run_for(1)
            expect(page.locator('#nextup a.network__link')).to_have_count(0)
            expect(page.locator('#nextup-status')).to_have_text("Last known · 30'")
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute(
                "data-source-received-at", "2026-10-07T16:57:03.123456Z")

    def test_resume_reload_and_calendar_preview_do_not_renew_receipt(self):
        with self.page() as (page, _):
            page.evaluate("location.hash = 'at-20261007-1300'")
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            page.clock.fast_forward(240000)
            page.evaluate("window.dispatchEvent(new Event('pageshow'))")
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text("Last known · 30'")
            expect(page.locator('li.row[data-id="upcoming"] a.network__link')).to_have_count(0)
            page.reload()
            page.wait_for_selector("#park", state="attached")
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-source-received-at", SOURCE_RECEIPT)
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text("Last known · 30'")
            expect(page.locator('li.row[data-id="upcoming"] a.network__link')).to_have_count(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    args, rest = parser.parse_known_args()
    ENGINE = args.browser
    unittest.main(argv=[__file__, *rest])
