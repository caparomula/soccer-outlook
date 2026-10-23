"""Companion bindings belong to accepted responses, never a later bundle refresh."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import story
from tests.test_story import SEEN, raw_story, totals


NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def facts(enabled=True):
    match = dict(id="1", league_id="eng.1", kickoff_utc="2026-10-21T19:00:00Z",
                 competition="Premier League", time_confirmed=True,
                 home={"name": "Home"}, away={"name": "Away"})
    return dict(_companion_bindings=enabled, built_at="2026-10-20T17:00:00Z", date="2026-10-20",
                focus_until="2026-10-21T17:00:00Z", ranking_candidates=[match], next_24_hours=[match],
                later_if_needed=[], league_candidates=[], leagues=[{"league_id": "eng.1"}])


class StoryBindings(unittest.TestCase):
    def fresh_rating(self, enabled=True):
        def rate(*args):
            return [{"match_id": "1", "popularity": 80, "gameplay": 70, "impact": 60}], {}, "end_turn", "test-model"
        with patch.object(story, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            return story.write_ratings(rate, facts(enabled), ["1"], [], {}, {}, totals(), scores_only=True)[0]["1"]

    def test_new_response_is_bound_but_legacy_default_output_is_unchanged(self):
        bound = self.fresh_rating()
        self.assertEqual(bound["companion_origin"], dict(
            fixture_bindings=[dict(id="1", league_id="eng.1", kickoff_utc="2026-10-21T19:00:00Z")],
            prompted_at="2026-10-20T18:00:00Z", generated_at="2026-10-20T18:00:00Z",
            expires_at="2026-10-21T17:00:00Z"))
        self.assertNotIn("companion_origin", self.fresh_rating(False))

    def test_carried_rating_preserves_original_identity_and_expiry_after_kickoff_change(self):
        old = self.fresh_rating()
        carried = story.earlier_ratings({"rankings": {"1": old}}, {"1"})
        changed = facts()["ranking_candidates"]
        changed[0]["kickoff_utc"] = "2026-10-22T19:00:00Z"
        updated = story.full_snapshot({}, {}, carried, changed)
        self.assertEqual(updated["rankings"]["1"]["companion_origin"], old["companion_origin"])
        updated["rankings"]["1"]["companion_origin"]["expires_at"] = "changed"
        self.assertEqual(old["companion_origin"]["expires_at"], "2026-10-21T17:00:00Z")
        legacy = dict(old)
        legacy.pop("companion_origin")
        self.assertNotIn("companion_origin", story.earlier_ratings({"rankings": {"1": legacy}}, {"1"})["1"])

    def test_resumed_news_preserves_bound_origin_and_never_binds_legacy(self):
        original = story.clean_story(raw_story(), facts(), SEEN)
        story.bind_companion_news(original, facts(), NOW, generated_at=NOW)
        changed_facts = facts()
        changed_facts["ranking_candidates"][0]["kickoff_utc"] = "2026-10-22T19:00:00Z"
        resumed = story.clean_story(story.earlier_news(original), changed_facts, SEEN)
        story.restore_companion_news(resumed, original)
        self.assertEqual(resumed["lede_items"][0]["companion_origin"], original["lede_items"][0]["companion_origin"])
        self.assertEqual(resumed["companion_headline_origin"], original["companion_headline_origin"])
        legacy = story.clean_story(raw_story(), facts(), SEEN)
        unbound = story.clean_story(story.earlier_news(legacy), changed_facts, SEEN)
        story.restore_companion_news(unbound, legacy)
        self.assertNotIn("companion_origin", unbound["lede_items"][0])

    def test_unknown_kickoff_is_explicit_and_missing_identity_stays_unbound(self):
        source = facts()
        source["ranking_candidates"][0]["time_confirmed"] = False
        origin = story.companion_origin(source, ["1"], NOW, generated_at=NOW)
        self.assertIsNone(origin["fixture_bindings"][0]["kickoff_utc"])
        source["ranking_candidates"][0].pop("league_id")
        self.assertIsNone(story.companion_origin(source, ["1"], NOW, generated_at=NOW))

    def test_sourced_calendar_period_can_bind_without_inventing_fixture_references(self):
        item = dict(league_id="eng.1", match_ids=[], headline="Draw next week", text="The draw is scheduled.",
                    event_start="2026-10-21T15:00:00Z", event_end="2026-10-21T16:00:00Z",
                    sources=[{"title": "Organizer", "url": "https://example.com/draw"}])
        value = {"league_blurbs": [item]}
        story.bind_companion_news(value, facts(), NOW, generated_at=NOW)
        self.assertEqual(item["companion_origin"]["fixture_bindings"], [])
        self.assertEqual(item["companion_origin"]["expires_at"], "2026-10-21T17:00:00Z")
        for changed in ({"sources": []}, {"event_end": None}, {"match_ids": ["missing"]}):
            invalid = {key: val for key, val in dict(item, **changed).items() if key != "companion_origin"}
            story.bind_companion_news({"league_blurbs": [invalid]}, facts(), NOW, generated_at=NOW)
            self.assertNotIn("companion_origin", invalid)

    def test_ratings_top_up_binds_only_new_responses(self):
        old = self.fresh_rating()
        current = facts()
        current["ranking_candidates"][0]["kickoff_utc"] = "2026-10-22T19:00:00Z"
        current["ranking_candidates"].append(dict(current["ranking_candidates"][0], id="2"))
        previous = {"rankings": {"1": old}}
        response = ([{"match_id": "2", "popularity": 80, "gameplay": 70, "impact": 60}], {}, "end_turn", "test-model")
        with patch.object(story, "api_rate_chunk", return_value=response) as provider, \
             patch.object(story, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result, _ = story.write_story(current, "gpt-6.1-sol", "low", "ratings", previous, totals())
        self.assertEqual(result["rankings"]["1"]["companion_origin"], old["companion_origin"])
        self.assertEqual(result["rankings"]["2"]["companion_origin"]["fixture_bindings"][0]["id"], "2")
        self.assertEqual([item["id"] for item in provider.call_args.args[3]], ["2"])

    def test_new_blurb_does_not_rebind_its_carried_rating(self):
        old = self.fresh_rating()
        current = facts()
        current["ranking_candidates"][0]["kickoff_utc"] = "2026-10-22T19:00:00Z"
        value = {"rankings": {"1": deepcopy(old)}}
        request = lambda *args: SimpleNamespace(served="gpt-6.1-sol", usage={})
        with patch.object(story, "pick_candidates", return_value=current["ranking_candidates"]), \
             patch.object(story, "checked_blurbs", return_value=({"1": {"blurb": "Fresh text", "sources": []}}, "")), \
             patch.dict(story.os.environ, {"OPENAI_API_KEY": "offline-test-key"}), \
             patch.object(story, "datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            story.add_blurbs(value, current, "gpt-6.1-sol", "low", request=request)
        self.assertEqual(value["rankings"]["1"]["companion_origin"], old["companion_origin"])
        self.assertEqual(value["rankings"]["1"]["companion_blurb_origin"]["fixture_bindings"][0]["kickoff_utc"],
                         "2026-10-22T19:00:00Z")

    def test_rewritten_unbound_blurb_cannot_reuse_old_score_or_text_origin(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                old = self.fresh_rating()
                old["companion_blurb_origin"] = deepcopy(old["companion_origin"])
                current = facts(enabled)
                if enabled:
                    current["ranking_candidates"][0].pop("league_id")  # incomplete facts fail binding
                value = {"rankings": {"1": deepcopy(old)}}
                request = lambda *args: SimpleNamespace(served="gpt-6.1-sol", usage={})
                with patch.object(story, "pick_candidates", return_value=current["ranking_candidates"]), \
                     patch.object(story, "checked_blurbs", return_value=({"1": {"blurb": "New unbound text", "sources": []}}, "")), \
                     patch.dict(story.os.environ, {"OPENAI_API_KEY": "offline-test-key"}):
                    story.add_blurbs(value, current, "gpt-6.1-sol", "low", request=request)
                rewritten = value["rankings"]["1"]
                self.assertEqual(rewritten["companion_origin"], old["companion_origin"])
                self.assertIn("companion_blurb_origin", rewritten)
                self.assertIsNone(rewritten["companion_blurb_origin"])
                carried = story.earlier_ratings(value, {"1"})["1"]
                self.assertIn("companion_blurb_origin", carried)
                self.assertIsNone(carried["companion_blurb_origin"])


if __name__ == "__main__":
    unittest.main()
