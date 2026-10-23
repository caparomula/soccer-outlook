"""Offline browser regressions for retained-live presentation and page lifecycle."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

import build
import watch_links
from tests.browser_live_freshness import FIXTURES, PLAYER, URL
from tests.page_fixture import BUILT_AT, render_page
from tool.companion_publication_capture.capture import load_browser_resources


ENGINE = "chromium"


class FreshnessPresentationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = getattr(cls.playwright, ENGINE).launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    @contextmanager
    def page(self, *, fixtures=FIXTURES, receipts=None, story=None):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            with patch.dict(watch_links.YOUTUBE_TV_LINKS,
                            {"upcoming": {"url": PLAYER, "channel": "USA Network"}}, clear=True):
                html = render_page(build, fixtures=fixtures, ai=story is not None,
                                   source_receipts=receipts, inline_runtime=False)
            (site / "index.html").write_text(html)
            runtime = build.outlook_runtime.publish_browser(site)
            _, resources = load_browser_resources(site)
            context = self.browser.new_context(timezone_id="America/New_York")
            if story is not None:
                context.add_init_script("localStorage.setItem('ssg3-ai-blurbs', 'true')")
            page = context.new_page()
            errors, fulfilled = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                url = urlsplit(route.request.url)
                name = url.path.rsplit("/", 1)[-1]
                if url.netloc != "127.0.0.1:8765":
                    route.abort()
                elif url.path.startswith("/espn/"):
                    route.fulfill(content_type="application/json", body='{"events": []}')
                elif name == "story.json" and story is not None:
                    route.fulfill(content_type="application/json", body=json.dumps(story))
                elif name in resources:
                    fulfilled.append(name)
                    route.fulfill(content_type="application/javascript" if name.endswith(".js") else "text/html",
                                  body=resources[name])
                elif name in ("story.json", "shared-ai.json", "schedule.json"):
                    route.fulfill(status=404, content_type="application/json", body="{}")
                else:
                    route.abort()

            context.route("**/*", route_request)
            page.clock.install(time=BUILT_AT - timedelta(seconds=1))
            page.clock.pause_at(BUILT_AT)
            try:
                page.goto(URL)
                page.wait_for_selector("#park", state="attached")
                page.clock.run_for(0)
                expect(page.locator("#livenote")).to_contain_text("last checked")
                self.assertIn(runtime, fulfilled)
                yield page
                self.assertEqual(errors, [])
            finally:
                context.close()

    @staticmethod
    def story(*, legacy=False):
        headline = "Premier League rivals contest midfield control"
        text = "Both coaches adjust their midfield plans."
        item = {"text": text, "match_ids": ["upcoming"],
                "segments": [{"text": text, "match_ids": ["upcoming"]}], "sources": []}
        story = {"version": 1, "date": "2026-10-07", "generated_at": "2026-10-07T17:00:00Z",
                 "focus_until": "2026-10-08T17:00:00Z", "notes": {}, "sources": [],
                 "later_reason": "", "lede_items": [item], "forecast": {"items": []}}
        if legacy:
            story.update(headline=headline,
                         headline_segments=[{"text": headline, "match_ids": ["upcoming"]}])
        else:
            story["league_blurbs"] = [dict(item, league_id="eng.1", interest=95, headline=headline)]
        return headline, story

    def check_last_known_headline(self, *, legacy):
        headline, story = self.story(legacy=legacy)
        with self.page(receipts={"upcoming": "2026-10-07T16:56:59Z"}, story=story) as page:
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text("Last known · 30'")
            expect(page.locator("#story-headlines .story-headline__title")).to_have_text(headline)
            expect(page.locator("#story")).to_be_visible()

    def test_automatic_headline_retains_last_known_in_progress_fixture(self):
        self.check_last_known_headline(legacy=False)

    def test_legacy_headline_retains_last_known_in_progress_fixture(self):
        self.check_last_known_headline(legacy=True)

    def test_last_known_fixtures_form_one_contiguous_carousel_group(self):
        fixtures = [("a", "2026-10-07T16:30:00Z", "in", "USA Network", "eng.1"),
                    ("b", "2026-10-07T16:45:00Z", "pre", "USA Network", "eng.1"),
                    ("c", "2026-10-07T17:00:00Z", "in", "USA Network", "eng.1")]
        with self.page(fixtures=fixtures, receipts={"a": None, "c": None}) as page:
            groups = page.locator('#nextup-track .nextup-group[data-time-group^="last-known-"]')
            expect(groups).to_have_count(1)
            expect(groups.locator("h2")).to_have_text("Last reported in progress")
            expect(groups.locator(".nextup-group__heading p")).to_have_text("2 matches")
            self.assertEqual(groups.locator("[data-match-id]").evaluate_all(
                "cards => cards.map(card => card.dataset.matchId)"), ["a", "c"])
            expect(page.locator('#nextup-track [data-match-id="b"]')).to_have_count(1)
            # Cohort layout must not expand the existing opening-match pick exclusion.
            expect(page.locator('#picks [data-match-id="a"]')).to_have_count(0)
            expect(page.locator('#picks [data-match-id="c"]')).to_have_count(1)

    def test_cancelled_beforeunload_keeps_exact_freshness_deadline(self):
        # Offset the cutoff from both the 60-second render and poll intervals.
        # beforeunload alone models Stop/Escape, a download, or a 204 response.
        with self.page(receipts={"upcoming": "2026-10-07T16:59:50.123Z"}) as page:
            row = page.locator('li.row[data-id="upcoming"]')
            card = page.locator('#nextup-track [data-match-id="upcoming"]')
            page.clock.run_for(130000)
            page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
            page.clock.run_for(40122)
            expect(row.locator(".row__live")).to_have_text("Live")
            expect(row.locator("a.network__link")).to_have_count(1)
            expect(card).to_have_class(re.compile(r"\bnextup--live\b"))
            page.clock.run_for(1)
            expect(row.locator(".row__status")).to_have_text("Last known · 30'")
            expect(row.locator(".row__live")).to_be_hidden()
            expect(row.locator("a.network__link")).to_have_count(0)
            expect(page.locator("#nextup a.network__link")).to_have_count(0)
            expect(card).not_to_have_class(re.compile(r"\bnextup--live\b"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    args, rest = parser.parse_known_args()
    ENGINE = args.browser
    unittest.main(argv=[__file__, *rest])
