"""Recovery and reuse through the real CLI path, with all paid requests replaced."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import providers
import story
from tests.test_story import RATINGS, RATINGS_BLURBS, RATINGS_OVERVIEW, RUN_FACTS, openai_ratings


class Recovery(unittest.TestCase):
    def run_main(self, *, previous=None, facts=None, settings=RATINGS, mode="daily", flags=(), post=None,
                 overview=None, blurbs=None, keys=None, now=None):
        facts = copy.deepcopy(facts or RUN_FACTS)
        now = now or datetime.fromisoformat(facts["built_at"].replace("Z", "+00:00"))
        clock = SimpleNamespace(now=lambda tz: now, fromisoformat=datetime.fromisoformat)
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            root = Path(folder)
            out, usage = root / "story.json", root / "usage.json"
            (root / "facts.json").write_text(json.dumps(facts))
            (root / "settings.toml").write_text(settings)
            argv = ["story.py", "--facts", str(root / "facts.json"), "--out", str(out), "--mode", mode,
                    "--settings", str(root / "settings.toml"), "--usage-out", str(usage), *flags]
            if previous is not None:
                (root / "previous.json").write_text(json.dumps(previous))
                argv += ["--previous", str(root / "previous.json")]
            stack.enter_context(patch.object(sys, "argv", argv))
            stack.enter_context(patch.dict(os.environ, {"OPENAI_API_KEY": "fake", **(keys or {})}, clear=True))
            stack.enter_context(patch.object(story, "datetime", clock))
            stack.enter_context(patch.object(story, "log"))
            stack.enter_context(patch.object(story, "summary"))
            request = stack.enter_context(patch.object(providers, "post_json", side_effect=post or AssertionError("unexpected paid request")))
            if overview:
                stack.enter_context(patch.object(story, "add_overview", side_effect=lambda *args: overview(out, *args)))
            if blurbs:
                stack.enter_context(patch.object(story, "add_blurbs", side_effect=lambda *args: blurbs(out, *args)))
            stack.enter_context(patch.object(story, "check_link", return_value={"state": "live"}))
            stdout = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(story.main(), 0)
            return (json.loads(out.read_text()) if out.exists() else None,
                    json.loads(usage.read_text()) if usage.exists() else None, stdout.getvalue(), request.call_count)

    def previous(self, facts=None):
        output, _, _, _ = self.run_main(facts=facts, post=openai_ratings([]))
        return output

    def test_daily_keeps_accepted_ratings_and_requests_only_new_or_missing_ids(self):
        previous = self.previous()
        del previous["rankings"]["2"]
        calls = []
        output, usage, _, count = self.run_main(previous=previous, post=openai_ratings(calls))
        asked = json.loads(calls[0][1]["input"].split("exactly once:\n")[1])
        self.assertEqual([m["id"] for m in asked], ["2"])
        self.assertEqual(count, 1)
        self.assertEqual(output["rankings"]["1"], previous["rankings"]["1"])
        self.assertEqual(usage["carried"], 3)
        kept, _, _, count = self.run_main(previous=output)
        self.assertEqual((kept, count), (output, 0))

    def test_four_am_window_expansion_adds_the_new_last_day(self):
        facts = copy.deepcopy(RUN_FACTS)
        facts.update(date="2026-11-01", weekday="Sunday", built_at="2026-11-01T08:50:00Z")  # 3:50 EST
        facts["ranking_candidates"] = [dict(RUN_FACTS["ranking_candidates"][0], id="early", kickoff_utc="2026-11-01T18:00:00Z"),
                                       dict(RUN_FACTS["ranking_candidates"][0], id="new-day", kickoff_utc="2026-11-04T18:00:00Z")]
        previous = self.previous(facts)
        self.assertEqual(set(previous["rankings"]), {"early"})
        facts["built_at"] = "2026-11-01T09:50:00Z"  # 4:50 EST: same calendar date, larger sports-day window
        calls = []
        output, _, _, count = self.run_main(previous=previous, facts=facts, post=openai_ratings(calls))
        asked = json.loads(calls[0][1]["input"].split("exactly once:\n")[1])
        self.assertEqual([m["id"] for m in asked], ["new-day"])
        self.assertEqual(set(output["rankings"]), {"early", "new-day"})
        self.assertEqual(output["rating_window_until"], "2026-11-05T04:00:00-05:00")
        self.assertEqual(count, 1)

    def test_optional_research_can_publish_an_unrated_fixture_without_losing_ratings(self):
        facts = copy.deepcopy(RUN_FACTS)
        facts["overview_fixtures"] = facts["ranking_candidates"][:2]
        facts["pick_inputs"] = {"outlook": {"1": 40, "2": 90}}
        calls = []
        rate = openai_ratings(calls, drop=("2",))
        page = "https://news.example/preview"

        def post(url, body, headers, **kwargs):
            if "tools" not in body:
                return rate(url, body, headers, **kwargs)
            answer = {"blurbs": [{"match_id": "2", "blurb": "The visitors face a decisive fixture.", "sources": [page]}]}
            return {"model": "gpt-6.1-sol", "status": "completed", "output": [
                {"type": "web_search_call", "action": {"type": "search", "query": "match", "sources": [{"url": page}]}},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {}}
        output, usage, _, _ = self.run_main(facts=facts, settings=RATINGS_BLURBS, post=post)
        self.assertEqual(set(output["rankings"]), {"1", "3", "4"})
        self.assertIn("decisive", output["notes"]["2"]["note"])
        self.assertTrue(usage["published"])

    def test_ratings_are_saved_before_research_and_survive_enrichment_failure(self):
        def broken(path, candidate, *args):
            saved = json.loads(path.read_text())
            self.assertEqual(len(saved["rankings"]), 4)
            self.assertEqual(saved["overview_model"], "gemini-3.1-pro-preview")
            candidate["rankings"].clear()
            raise RuntimeError("malformed enrichment")
        output, usage, _, _ = self.run_main(settings=RATINGS_OVERVIEW, post=openai_ratings([]), overview=broken)
        self.assertEqual(len(output["rankings"]), 4)
        self.assertTrue(usage["published"])
        self.assertIn("malformed", usage["overview"]["why"])

    def test_failed_overview_retries_without_rerating_even_without_the_ratings_key(self):
        previous = self.previous()
        previous.update(overview_model="gemini-3.1-pro-preview", overview_effort="medium")

        def success(path, candidate, *args):
            candidate["overview"] = {"text": "An overview.", "sources": [{"url": "https://news.example/a"}]}
            return {"published": True, "cost_complete": True}
        output, _, _, count = self.run_main(previous=previous, settings=RATINGS_OVERVIEW, overview=success,
                                            keys={"OPENAI_API_KEY": "", "GEMINI_API_KEY": "fake"})
        self.assertEqual(count, 0)
        self.assertEqual(output["rankings"], previous["rankings"])
        self.assertEqual(output["generated_at"], previous["generated_at"])
        self.assertEqual(output["focus_until"], previous["focus_until"])
        kept, _, _, count = self.run_main(previous=output, settings=RATINGS_OVERVIEW)
        self.assertEqual((kept, count), (output, 0))

    def test_blurb_retry_requests_only_missing_text_and_keeps_ratings(self):
        previous = self.previous()
        previous.update(blurbs_model="gpt-6.1-sol", blurbs_effort="medium")
        previous["rankings"]["1"].update(blurb="An accepted preview.", sources=[{"url": "https://news.example/a"}])
        facts = copy.deepcopy(RUN_FACTS)
        facts["overview_fixtures"] = facts["ranking_candidates"][:2]
        seen = []

        def post(url, body, headers, **kwargs):
            self.assertIn("tools", body)  # No rating request should be made.
            seen.extend(json.loads(body["input"].split("Fixtures:\n")[1].split("\n\nYou have up to")[0]))
            page = "https://news.example/b"
            return {"model": "gpt-6.1-sol", "status": "completed", "output": [
                {"type": "web_search_call", "action": {"type": "search", "sources": [{"url": page}]}},
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"blurbs": [
                    {"match_id": "2", "blurb": "New researched context.", "sources": [page]}]})}]}], "usage": {}}
        output, _, _, count = self.run_main(previous=previous, facts=facts, settings=RATINGS_BLURBS, post=post)
        self.assertEqual([m["id"] for m in seen], ["2"])
        self.assertEqual(count, 1)
        self.assertEqual(output["rankings"]["1"], previous["rankings"]["1"])
        self.assertEqual(output["rankings"]["2"]["blurb"], "New researched context.")
        kept, _, _, count = self.run_main(previous=output, facts=facts, settings=RATINGS_BLURBS)
        self.assertEqual((kept, count), (output, 0))

    def test_unknown_optional_cost_does_not_remove_saved_ratings(self):
        rate = openai_ratings([])

        def post(url, body, headers, **kwargs):
            if "tools" in body:
                raise providers.AmbiguousRequestError("timed out")
            return rate(url, body, headers, **kwargs)
        output, usage, _, count = self.run_main(settings=RATINGS_BLURBS, post=post)
        self.assertEqual(len(output["rankings"]), 4)
        self.assertEqual(count, 2)
        self.assertFalse(usage["cost_complete"])
        self.assertFalse(usage["blurbs"]["cost_complete"])

    def test_ambiguous_transport_failure_is_not_retried_and_marks_cost_incomplete(self):
        output, usage, _, count = self.run_main(post=providers.AmbiguousRequestError("connection dropped"))
        self.assertIsNone(output)
        self.assertEqual(count, 1)
        self.assertEqual(usage["usage"]["unknown_requests"], 1)
        self.assertFalse(usage["cost_complete"])

    def test_claude_sdk_does_not_hide_transport_retries(self):
        constructor = SimpleNamespace(Anthropic=lambda **kwargs: kwargs)
        with patch.dict(sys.modules, {"anthropic": constructor}):
            self.assertEqual(story.claude_client(), {"max_retries": 0})

    def test_fallback_only_requires_matching_configuration_date_and_freshness(self):
        previous = self.previous()
        output, usage, stdout, count = self.run_main(previous=previous, flags=("--fallback-only",))
        self.assertEqual((output, usage, stdout, count), (previous, None, "", 0))
        for changed in (dict(previous, effort="medium"), dict(previous, date="2026-10-06"),
                        dict(previous, focus_until=RUN_FACTS["built_at"]), dict(previous, generated_at="2026-10-05T00:00:00Z")):
            output, _, _, count = self.run_main(previous=changed, flags=("--fallback-only",))
            self.assertEqual((output, count), (None, 0))

    def test_sdk_plan_handles_reuse_and_optional_jobs_without_calls_or_writes(self):
        previous = self.previous()
        flag = ("--needs-sdk",)
        cases = [
            (dict(previous=previous), ""),
            (dict(settings=RATINGS.replace("gpt-6.1-sol", "claude-sonnet-5-5"), keys={"ANTHROPIC_API_KEY": "fake"}), "anthropic\n"),
            (dict(settings=RATINGS_BLURBS.replace('blurbs_model = "gpt-6.1-sol"', 'blurbs_model = "claude-sonnet-5-5"'), keys={"ANTHROPIC_API_KEY": "fake"}), "anthropic\n"),
            (dict(settings=RATINGS.replace("gpt-6.1-sol", "claude-sonnet-5-5")), ""),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                output, usage, stdout, count = self.run_main(flags=flag, **kwargs)
                self.assertEqual((output, usage, stdout, count), (None, None, expected, 0))

    def test_atomic_save_preserves_previous_file_on_serialization_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "story.json"
            story.save(path, {"old": True})
            with self.assertRaises(TypeError):
                story.save(path, {"bad": object()})
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["story.json"])
