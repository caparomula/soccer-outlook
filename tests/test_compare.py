"""The storyline comparison's report: its statistics, worked out by hand, and its ratings section."""
from contextlib import redirect_stdout
from datetime import date
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "compare-storylines.py"
_spec = importlib.util.spec_from_file_location("compare_storylines", SCRIPT)
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


class Statistics(unittest.TestCase):
    def test_spearman_on_orders_ties_and_shared_fixtures(self):
        four = {"a": 1, "b": 2, "c": 3, "d": 4}
        self.assertAlmostEqual(compare.spearman(four, {"a": 10, "b": 20, "c": 30, "d": 40})[0], 1.0)
        self.assertAlmostEqual(compare.spearman(four, {"a": 4, "b": 3, "c": 2, "d": 1})[0], -1.0)
        # Ties share their places: ranks 1, 2.5, 2.5, 4 against 1, 2, 3, 4 give 4.5 / sqrt(4.5 x 5).
        rho, low, high, n = compare.spearman({"a": 1, "b": 2, "c": 2, "d": 3}, four)
        self.assertAlmostEqual(rho, 4.5 / math.sqrt(4.5 * 5))
        # The interval: tanh(atanh(rho) -/+ 1.96 x sqrt(1.06 / (n - 3))).
        z, se = math.atanh(rho), math.sqrt(1.06 / 1)
        self.assertAlmostEqual(low, math.tanh(z - 1.96 * se))
        self.assertAlmostEqual(high, math.tanh(z + 1.96 * se))
        self.assertEqual(n, 4)
        # Only fixtures both rated count; fewer than four, or a side with no order, can't be compared.
        self.assertEqual(compare.spearman(dict(four, x=9), dict(four, y=0))[3], 4)
        self.assertIsNone(compare.spearman({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 2, "c": 3}))
        self.assertIsNone(compare.spearman(dict.fromkeys(four, 5), four))

    def test_top_three_each_day_on_the_default_lineup(self):
        sat, sun = date(2026, 10, 10), date(2026, 10, 11)
        rows = {mid: (day, lineup, 50.0, mid.upper()) for mid, day, lineup in
                (("a", sat, True), ("b", sat, True), ("c", sat, False), ("d", sat, True), ("e", sat, True), ("f", sun, True))}
        scores = {"a": 90, "b": 80, "c": 99, "d": 70, "e": 60, "f": 10, "z": 100}     # c is off the lineup, z isn't on the page
        self.assertEqual(compare.top_three_by_day(scores, rows), {sat: ["a", "b", "d"], sun: ["f"]})
        self.assertEqual(compare.top_three_by_day({"b": 5, "a": 5}, rows), {sat: ["a", "b"]})   # a tie goes by fixture ID


class Report(unittest.TestCase):
    ROW = ('<li class="row avail" data-id="{id}" data-utc="2026-10-10T{hour}:00:00Z" data-svc="{svc}" '
           'data-outlook="{outlook}" data-home="Home {id}" data-away="Away {id}">')

    def test_ratings_runs_get_an_agreement_section_instead_of_writing(self):
        published = {"a": 80, "b": 70, "c": 60, "d": 50, "e": 40}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "facts.json").write_text(json.dumps({"date": "2026-10-08", "weekday": "Thursday", "ranking_candidates": []}))
            runs = tmp / "compare"
            runs.mkdir()
            for label, model, scores in (("h", "claude-haiku-5-5", published),                    # the same order
                                         ("s", "claude-sonnet-5-5", {k: 100 - v for k, v in published.items()})):  # reversed
                (runs / f"{label}.usage.json").write_text(json.dumps(
                    {"mode": "ratings", "model": model, "effort": "low", "cost_usd": 0.0042, "seconds": 9.5, "usage": {}}))
                (runs / f"{label}.json").write_text(json.dumps(
                    {"kind": "ratings", "window_hours": 72, "rankings": {k: {"score": v} for k, v in scores.items()}}))
            # Outlook scores in the published order too, except that d and e swap places.
            outlook = {"a": 70, "b": 60, "c": 50, "d": 30, "e": 40}
            (tmp / "page.html").write_text("".join(self.ROW.format(id=k, hour=10 + i, svc="none" if k == "c" else "espn", outlook=v)
                                                   for i, (k, v) in enumerate(outlook.items())))
            (tmp / "published.json").write_text(json.dumps({"model": "claude-opus-5-5", "effort": "medium", "kind": "full",
                                                            "generated_at": "2026-10-07T20:55:20Z",
                                                            "rankings": {k: {"score": v} for k, v in published.items()}}))
            out = io.StringIO()
            with patch.object(sys, "argv", ["compare", str(tmp / "facts.json"), str(runs), str(tmp / "page.html"), str(tmp / "published.json")]), \
                    redirect_stdout(out):
                self.assertEqual(compare.main(), 0)
        text = out.getvalue()
        self.assertIn("scores only, next 72 hours", text)
        self.assertIn("## Ratings agreement", text)
        self.assertNotIn("<details>", text)                       # no writing to show for ratings runs
        self.assertIn("| claude-haiku-5-5 at low | $0.0042 | 9.5s |", text)          # a fraction of a cent, not "$0.00"
        lines = text.splitlines()
        lines = lines[lines.index("## Ratings agreement"):]
        row = lambda name: next(line for line in lines if line.startswith(f"| {name} |"))
        # Columns: haiku, sonnet, published, Outlook. One swap among five: 1 - 6 x (1 + 1) / (5 x 24) = 0.9.
        self.assertEqual(row("claude-haiku-5-5 at low"), "| claude-haiku-5-5 at low |  | -1.00 (n=5) | 1.00 (n=5) | 0.90 (n=5) |")
        self.assertIn("- claude-sonnet-5-5 at low: -1.00", text)
        # The lineup's top three on the day (c is off it): a, b, d published; reversed, e, d, b; the Outlook score, a, b, e.
        self.assertEqual(row("Sat Oct 10"), "| Sat Oct 10 | 3/3 · same #1 | 2/3 | 2/3 · same #1 |")
        self.assertIn("**claude-haiku-5-5 at low, top ten:** a 80; b 70; c 60; d 50; e 40", text)


if __name__ == "__main__":
    unittest.main()
