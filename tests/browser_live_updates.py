"""Instrument the initialized, generated page to verify live-update work and facts.

Only observation hooks are inserted into the page closure. The real external Dart
runtime, page initialization, transport, rendering and network blocking all run.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

import build
from tests import browser_live_freshness as harness
from tests.page_fixture import scoreboard


PROBE = r"""
  window.liveProbe = {renders: 0, wipes: 0, queries: [], passes: 0};
  var originalRender = render, originalCore = coreDecision, originalFreshness = refreshLiveFreshness;
  render = function () { liveProbe.renders++; return originalRender.apply(this, arguments); };
  coreDecision = function (request) {
    if (request.operation === 'live_freshness') liveProbe.queries.push(request.cases.map(function (c) { return c.id; }));
    return originalCore.apply(this, arguments);
  };
  refreshLiveFreshness = function () { liveProbe.passes++; return originalFreshness.apply(this, arguments); };
  var htmlProperty = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  Object.defineProperty(body, 'innerHTML', {
    get: function () { return htmlProperty.get.call(this); },
    set: function (value) { liveProbe.wipes++; htmlProperty.set.call(this, value); }
  });
  liveProbe.refresh = function () { return refreshLiveFreshness(); };
  liveProbe.poll = function () { return liveScores.poll(); };
  liveProbe.apply = function (id, event, receipt) {
    var changed = {}; applyEvent(ROW_BY_ID[id], event, true, changed, receipt); return changed;
  };
  liveProbe.applyReplacement = function (id, event, receipt) {
    // Schedule overlays initialize a new row before installing it into rows.
    var replacement = ROW_BY_ID[id].cloneNode(true), changed = {};
    projectRowStatuses([replacement]); initializeRow(replacement);
    applyEvent(replacement, event, true, changed, receipt);
    return {state:replacement._state, receipt:replacement._sourceReceivedAt,
      decision:replacement._liveFreshness || null, connected:replacement.isConnected,
      installed:rows.map(function (row) { return {id:row.dataset.id, decision:row._liveFreshness}; }),
      queries:liveProbe.queries, changed:changed};
  };
  liveProbe.reset = function () { this.renders = this.wipes = this.passes = 0; this.queries = []; };
  liveProbe.renderWithRoutesDue = function () { routeEvaluatedAt = null; render(false); };
"""


def instrument(html):
    app = Path("web/app.js").read_text()
    assert app in html, "Generated page must contain the current adapter"
    return html.replace(app, app.rsplit("})();", 1)[0] + PROBE + "})();", 1)


def unchanged_event():
    event = scoreboard("in")["events"][0]
    comp = event["competitions"][0]
    comp["status"]["displayClock"] = "30'"
    for side in comp["competitors"]:
        side["score"] = "0"
    comp["details"] = []
    return event


class LiveUpdateChecks(unittest.TestCase):
    setUpClass = classmethod(harness.LiveFreshnessChecks.setUpClass.__func__)
    tearDownClass = classmethod(harness.LiveFreshnessChecks.tearDownClass.__func__)

    @contextmanager
    def page(self, **kwargs):
        with patch.object(build, "TEMPLATE", instrument(build.TEMPLATE)):
            with harness.LiveFreshnessChecks.page(self, **kwargs) as result:
                yield result

    def test_a1_one_rebuild_for_twenty_five_events_in_one_response(self):
        fixtures = [("upcoming" if i == 0 else f"match-{i}",
                     "2026-10-07T16:30:00Z" if i < 25 else "2026-10-08T18:00:00Z",
                     "in" if i < 25 else "pre", "USA Network", "eng.1") for i in range(300)]
        with self.page(fixtures=fixtures, receipts={f[0]: None for f in fixtures}) as (page, state):
            state["feed"] = {"events": [dict(deepcopy(unchanged_event()), id=f[0]) for f in fixtures[:25]]}
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=30))
            page.evaluate("liveProbe.reset()")
            # Invoke the real transport without a minute render.
            page.evaluate("liveProbe.poll()")
            expect(page.locator('li.row[data-id="match-24"]')).to_have_attribute(
                "data-source-received-at", "2026-10-07T17:00:30.000Z")
            self.assertEqual(page.evaluate("liveProbe.wipes"), 1)

    def test_a2_malformed_goals_rejects_all_facts_before_batch_completion(self):
        with self.page(receipts={"upcoming": None}) as (page, _):
            event = scoreboard("post")["events"][0]
            event["competitions"][0]["details"] = {}
            result = page.evaluate("""event => {
                var r = document.querySelector('li.row[data-id="upcoming"]'), error = null;
                try { liveProbe.apply('upcoming', event, '2026-10-07T17:00:00Z'); }
                catch (e) { error = String(e); }
                return {error, state:r._state, receipt:r._sourceReceivedAt,
                  live:r._liveFreshness.live_now, label:r._sourceStatus.label};
            }""", event)
            self.assertEqual(result, {"error": None, "state": "in", "receipt": None, "live": False, "label": "30'"})
            expect(page.locator('li.row[data-id="upcoming"] a.network__link')).to_have_count(0)
            expect(page.locator('#nextup.nextup--live')).to_have_count(0)

    def test_a3_last_known_unchanged_source_label_sets_no_flags(self):
        with self.page(receipts={"upcoming": None}) as (page, _):
            flags = page.evaluate("event => liveProbe.apply('upcoming', event, null)", unchanged_event())
            self.assertFalse(any(flags.values()), flags)
            expect(page.locator('li.row .row__status')).to_have_text("Last known · 30'")

    def test_a4_receipt_only_poll_does_not_render(self):
        with self.page(feed={"events": [unchanged_event()]}) as (page, _):
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=30))
            page.evaluate("liveProbe.reset(); liveProbe.poll()")
            expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute(
                "data-source-received-at", "2026-10-07T17:00:30.000Z")
            expect(page.locator('#livenote')).to_contain_text('last checked')
            self.assertEqual(page.evaluate("liveProbe.renders"), 0)

    def test_a5_unchanged_facts_memo_hit_and_no_label_mutation(self):
        with self.page() as (page, _):
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(milliseconds=17))
            result = page.evaluate("""() => {
                var status = document.querySelector('li.row .row__status');
                var observer = new MutationObserver(function () {}); observer.observe(status, {childList:true,characterData:true,subtree:true});
                liveProbe.reset(); liveProbe.refresh();
                return {queries:liveProbe.queries, mutations:observer.takeRecords().length};
            }""")
            self.assertEqual(result, {"queries": [], "mutations": 0})

    def test_a5_only_due_row_is_requeried_and_backwards_clock_requeries_all(self):
        fixtures = [harness.FIXTURES[0], ("second", *harness.FIXTURES[0][1:])]
        with self.page(fixtures=fixtures, receipts={"second": None}) as (page, _):
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=180))
            queried = page.evaluate("liveProbe.reset(); liveProbe.refresh(); liveProbe.queries")
            self.assertEqual(queried, [["upcoming"]])
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=179))
            queried = page.evaluate("liveProbe.reset(); liveProbe.refresh(); liveProbe.queries")
            self.assertEqual(queried, [["upcoming", "second"]])
            expect(page.locator('li.row[data-id="upcoming"] .row__status')).to_have_text("30'")

    def test_a5_draw_with_route_evaluation_runs_one_freshness_pass(self):
        with self.page() as (page, _):
            passes = page.evaluate("liveProbe.reset(); liveProbe.renderWithRoutesDue(); liveProbe.passes")
            self.assertEqual(passes, 1)

    def test_a5_backwards_clock_evaluates_detached_schedule_overlay_row(self):
        fixtures = [harness.FIXTURES[0], ("second", *harness.FIXTURES[0][1:])]
        with self.page(fixtures=fixtures) as (page, _):
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=181))
            page.evaluate("liveProbe.refresh()")
            page.clock.set_system_time(harness.BUILT_AT + harness.timedelta(seconds=30))
            result = page.evaluate("""event => {
                liveProbe.reset();
                return liveProbe.applyReplacement('upcoming', event, '2026-10-07T17:00:00Z');
            }""", unchanged_event())
            self.assertEqual(result["state"], "in")
            self.assertEqual(result["receipt"], "2026-10-07T17:00:00Z")
            self.assertFalse(result["connected"])
            self.assertIsNotNone(result["decision"], result)
            self.assertTrue(result["decision"]["live_now"], result)
            self.assertEqual({row["id"]: row["decision"]["live_now"] for row in result["installed"]},
                             {"upcoming": True, "second": True}, result)
            queried = [item for batch in result["queries"] for item in batch]
            self.assertIn("second", queried, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=["chromium", "webkit"], default="chromium")
    args, remaining = parser.parse_known_args()
    harness.ENGINE = args.browser
    unittest.main(argv=[__file__, *remaining], verbosity=2)
