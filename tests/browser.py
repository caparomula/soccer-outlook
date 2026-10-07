"""Offline Chromium checks; run with python3 -m tests.browser --help."""
import argparse
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
from tests.page_fixture import BUILT_AT, render_page, scoreboard


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class BrowserChecks(unittest.TestCase):
    @contextmanager
    def page(self, target, *, width=1280, theme="light", at="20261007-1300"):
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
            if url.path.startswith("/espn/"):
                feed["requests"] += 1
                if feed["fail"]:
                    route.fulfill(status=503, body="Fixture: ESPN unavailable")
                else:
                    route.fulfill(content_type="application/json", body=json.dumps(feed["data"]))
            elif url.path.endswith("/story.json"):
                route.fulfill(status=404, body="No fixture story")
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
            expect(page.locator("#outlook-sub")).to_contain_text("From where you are")
            page.clock.run_for(1)
            expect(page.locator("#livenote")).to_contain_text("last checked")
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
            ("20261008-0400", "Yesterday", "Yesterday", "Live now"),
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
                feed["data"] = scoreboard("in")
                page.clock.run_for(60000)
                expect(row).to_have_attribute("data-state", "in")
                expect(row.locator(".team .score")).to_have_text(["2", "1"])
                expect(row.locator(".row__goals")).to_contain_text("A. Player 63'")
                self.assertEqual(self.bucket(page, "upcoming"), "Live now")
                feed["data"] = scoreboard("post")
                page.clock.run_for(60000)
                expect(row).to_have_attribute("data-state", "post")
                expect(row.locator(".row__status")).to_have_text("FT")
                self.assertEqual(self.bucket(page, "upcoming"), "Earlier today")
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
