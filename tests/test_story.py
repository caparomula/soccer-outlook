"""Checks on what story.py keeps from the model's publish_story call, and on the week's schedule
build.py gives it for the forecast. The page shows whatever survives these, so each case is a way a
plausible answer could reach the page wrong. Run with: python3 -m unittest -v
"""
import os
import json
import tempfile
from pathlib import Path
import sys
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build  # noqa: E402
import story  # noqa: E402   (imports without the anthropic package, which only write_story needs)

SOURCE = "https://example.com/a"
SEEN = {story.url_key(SOURCE): (SOURCE, "A")}
FACTS = {"next_24_hours": [{"id": "1"}, {"id": "2"}], "later_if_needed": [{"id": "later"}]}


def item(text="Researched context.", ids=None, sources=None):
    return {"segments": [{"text": text, "match_ids": ["1"] if ids is None else ids}],
            "sources": [SOURCE] if sources is None else sources}


def raw_story(**over):
    raw = {"headline": [{"text": "A headline", "match_ids": ["1"]}], "lede_items": [item("A sourced lede.")], "later_reason": "",
           "notes": [{"match_id": "1", "note": "A note.", "sources": [SOURCE]}],
           "forecast": {"items": [item()]}}
    raw.update(over)
    return raw


class Forecast(unittest.TestCase):
    def test_keeps_sentences_with_exact_fixture_references_and_sources(self):
        result = story.clean_story(raw_story(), FACTS, SEEN)
        self.assertEqual(result["forecast"]["items"][0]["match_ids"], ["1"])
        self.assertEqual(result["forecast"]["items"][0]["sources"], [{"url": SOURCE, "title": "A"}])
        self.assertEqual(result["lede"], "A sourced lede.")

    def test_bad_sentence_does_not_remove_other_sentences(self):
        for bad in (item(ids=[]), item(ids=["1", "unknown"]), item(ids=[1]),
                    item(sources=[]), item(sources=["https://unverified.example/a"]),
                    item(text="  "), {"text": "Missing references"}, None):
            with self.subTest(bad=bad):
                result = story.clean_story(raw_story(forecast={"items": [bad, item(ids=["2"])]}), FACTS, SEEN)
                self.assertEqual(len(result["forecast"]["items"]), 1)
                self.assertEqual(result["forecast"]["items"][0]["match_ids"], ["2"])

    def test_legacy_forecast_is_not_guessed_into_tags(self):
        for old in ({"label": "Some day", "today": "Text", "ahead": "More"}, None, "text"):
            result = story.clean_story(raw_story(forecast=old), FACTS, SEEN)
            self.assertNotIn("forecast", result)
            self.assertEqual(list(result["notes"]), ["1"])

    def test_later_requires_explanation_and_no_near_story(self):
        later = item(ids=["later"])
        raw = raw_story(headline=[{"text": "Later headline", "match_ids": ["later"]}], lede_items=[later], notes=[], forecast={"items": [later]})
        result = story.clean_story(raw, FACTS, SEEN)
        self.assertEqual(result["lede_items"], [])
        self.assertNotIn("forecast", result)
        raw["later_reason"] = "No supported near-term angle after research."
        result = story.clean_story(raw, FACTS, SEEN)
        self.assertEqual(result["forecast"]["items"][0]["match_ids"], ["later"])
        raw["forecast"]["items"].insert(0, item())
        result = story.clean_story(raw, FACTS, SEEN)
        self.assertEqual(len(result["forecast"]["items"]), 1)
        self.assertEqual(result["lede_items"], [])
        self.assertEqual(result["headline"], "")
        self.assertEqual(result["later_reason"], "")

    def test_near_match_note_also_prevents_later_lead(self):
        result = story.clean_story(raw_story(lede_items=[item(ids=["later"])],
                                           forecast={"items": []}, later_reason="No news soon."), FACTS, SEEN)
        self.assertEqual(result["lede_items"], [])
        self.assertEqual(result["later_reason"], "")

    def test_mixed_time_windows_rejected_without_losing_other_items(self):
        result = story.clean_story(raw_story(forecast={"items": [item(ids=["1", "later"]), item()]}), FACTS, SEEN)
        self.assertEqual(len(result["forecast"]["items"]), 1)
        self.assertEqual(result["forecast"]["items"][0]["match_ids"], ["1"])

    def test_deduplicates_ids_and_limits_length(self):
        result = story.clean_story(raw_story(forecast={"items": [item("First sentence. ", ["1", "1"])]}), FACTS, SEEN)
        saved = result["forecast"]["items"][0]
        self.assertEqual(saved["match_ids"], ["1"])
        self.assertEqual(saved["text"], "First sentence. ")

    def test_phrase_tags_preserve_spacing_and_keep_independent_references(self):
        segments = [{"text": "MLS", "match_ids": ["1"]}, {"text": " and ", "match_ids": []},
                    {"text": "the Premier League", "match_ids": ["2"]}, {"text": " have matches.", "match_ids": []}]
        result = story.clean_story(raw_story(forecast={"items": [{"segments": segments, "sources": [SOURCE]}]}), FACTS, SEEN)
        saved = result["forecast"]["items"][0]
        self.assertEqual(saved["segments"], segments)
        self.assertEqual(saved["text"], "MLS and the Premier League have matches.")
        self.assertEqual(saved["match_ids"], ["1", "2"])

    def test_oversized_text_is_not_clipped_through_phrase_tags(self):
        result = story.clean_story(raw_story(forecast={"items": [item("Context. " * 100)]}), FACTS, SEEN)
        self.assertNotIn("forecast", result)

    def test_refresh_retains_verified_sentence_sources(self):
        previous = {"lede_items": [{"sources": [{"url": SOURCE, "title": "A"}]}],
                    "forecast": {"items": [{"sources": [{"url": "https://example.com/b", "title": "B"}]}]}}
        seen = story.earlier_sources(previous)
        self.assertIn(story.url_key(SOURCE), seen)
        self.assertIn(story.url_key("https://example.com/b"), seen)

    def test_prompt_focus_and_access_independence_in_both_modes(self):
        facts = {"built_at": "2026-10-07T16:55:00Z", "weekday": "Wednesday", "date": "2026-10-07", "owner_services": ["HBO Max"]}
        previous = {"generated_at": "2026-10-07T08:55:00Z", "headline": "h", "lede": "l", "notes": {}}
        for prompt in (story.user_prompt(facts, (5, 2)), story.refresh_prompt(facts, previous, (5, 2))):
            self.assertIn("rolling next 24 hours", prompt)
            self.assertIn("Assume every match is viewable", prompt)
            self.assertIn("nothing of interest", prompt)
            self.assertIn("team, league and broadcaster tags", prompt)


class RollingFacts(unittest.TestCase):
    def test_boundary_and_all_competitions_are_available_to_claude(self):
        from tests.page_fixture import render_page
        fixtures = [
            ("live", "2026-10-07T16:30:00+00:00", "in", "ESPN+", "eng.1"),
            ("inside", "2026-10-08T16:59:00+00:00", "pre", "Peacock", "usa.1"),
            ("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+", "esp.1"),
            ("finished", "2026-10-07T16:00:00+00:00", "post", "ESPN+", "eng.1"),
            ("hidden", "2026-10-07T19:00:00+00:00", "pre", "ESPN+",
             next(lg for lg, info in build.LEAGUES.items() if info.get("default_off"))),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            render_page(build, fixtures=fixtures, facts_path=path)
            facts = json.loads(path.read_text())
        self.assertEqual([m["id"] for m in facts["next_24_hours"]], ["live", "hidden", "inside"])
        self.assertEqual([m["id"] for m in facts["later_if_needed"]], ["edge"])
        self.assertEqual(facts["focus_until"], "2026-10-08T17:00:00+00:00")
        self.assertEqual(facts["next_24_hours"][0]["league_id"], "eng.1")
        self.assertEqual(facts["next_24_hours"][0]["home"]["id"], "1")
        self.assertEqual(facts["next_24_hours"][-1]["broadcasters"], ["Peacock"])


def match(mid, league, et_hour, day, state="pre", status="", service="", comp=None, time_valid=True):
    utc = datetime(day.year, day.month, day.day, et_hour, 0, tzinfo=build.ET).astimezone(timezone.utc)
    return SimpleNamespace(id=mid, league=league, comp=comp or build.LEAGUES[league]["name"], utc=utc,
                           state=state, status=status, service=service, time_valid=time_valid)


class ScheduleByDay(unittest.TestCase):
    """The week's digest the forecast is written from: counts the model can't get wrong by sampling."""

    DAY = date(2026, 10, 7)

    def test_counts_and_first_kickoff(self):
        ms = [match("a", "eng.1", 15, self.DAY, service="peacock"), match("b", "eng.1", 10, self.DAY),
              match("c", "eng.1", 12, self.DAY, state="post", status="FT", service="peacock"),
              match("d", "esp.1", 14, date(2026, 10, 8))]
        week = build.schedule_by_day(ms, self.DAY, days=3)
        self.assertEqual([d["date"] for d in week], ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(week[0]["weekday"], "Wednesday")
        pl = week[0]["competitions"][build.LEAGUES["eng.1"]["name"]]
        self.assertEqual(pl, {"matches": 3, "on_owner_services": 2, "finished": 1, "first_kickoff": "10:00 AM ET"})
        self.assertEqual(list(week[1]["competitions"]), [build.LEAGUES["esp.1"]["name"]])
        self.assertEqual(week[2]["competitions"], {})

    def test_called_off_and_hidden_competitions_left_out(self):
        hidden = next(lg for lg, info in build.LEAGUES.items() if info.get("default_off"))
        ms = [match("a", "eng.1", 15, self.DAY, state="post", status="Postponed"), match("b", hidden, 19, self.DAY)]
        self.assertEqual(build.schedule_by_day(ms, self.DAY, days=1)[0]["competitions"], {})

    def test_time_to_be_set(self):
        ms = [match("a", "eng.1", 0, self.DAY, time_valid=False), match("b", "eng.1", 20, self.DAY)]
        pl = build.schedule_by_day(ms, self.DAY, days=1)[0]["competitions"][build.LEAGUES["eng.1"]["name"]]
        self.assertEqual(pl["first_kickoff"], "8:00 PM ET")
        self.assertEqual(pl["matches"], 2)

    def test_eastern_dates(self):
        # 9 pm Eastern on the 7th is 01:00 UTC on the 8th, and belongs to the 7th, as ESPN files it.
        ms = [match("a", "eng.1", 21, self.DAY)]
        self.assertEqual(ms[0].utc.date(), date(2026, 10, 8))
        week = build.schedule_by_day(ms, self.DAY, days=2)
        self.assertEqual(len(week[0]["competitions"]), 1)
        self.assertEqual(week[1]["competitions"], {})


if __name__ == "__main__":
    unittest.main()
