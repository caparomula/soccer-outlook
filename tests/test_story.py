"""Checks on what story.py keeps from Claude's research and ratings, and on how it spends requests.
The page shows whatever survives these, so each case is a way a plausible answer could reach the page
wrong, or a run could cost far more than it needs to. Run with: python3 -m unittest -v
"""
import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build  # noqa: E402
import providers  # noqa: E402
import story  # noqa: E402   (imports without the anthropic package, which only write_story needs)

SOURCE = "https://example.com/a"
SEEN = {story.url_key(SOURCE): (SOURCE, "A")}
FACTS = {"next_24_hours": [{"id": "1", "watch_on": "ESPN"}, {"id": "2", "watch_on": "Apple TV"}], "later_if_needed": [{"id": "later"}]}
NO_LINKS = {}


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
    def test_literal_unicode_escapes_are_normalized_in_new_and_kept_editorial(self):
        text = r"Hincapié and Atl\\u00e9tico; Alavés."
        raw = raw_story(lede_items=[item(text)])
        result = story.clean_story(raw, FACTS, SEEN)
        self.assertEqual(result['lede'], 'Hincapié and Atlético; Alavés.')
        previous = {'version': 1, 'rankings': {'1': {'blurb': text, 'sources': [{'url': r'https://example.com/é'}]}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'story.json'
            path.write_text(json.dumps(previous))
            kept = story.load_previous(path)
        self.assertEqual(kept['rankings']['1']['blurb'], 'Hincapié and Atlético; Alavés.')
        self.assertEqual(kept['rankings']['1']['sources'], previous['rankings']['1']['sources'])

    def test_storyline_is_one_short_paragraph_of_complete_tagged_sentences(self):
        sentences = [item("A" * 210 + "."), item("B" * 210 + "."), item("C" * 210 + ".")]
        result = story.clean_story(raw_story(lede_items=sentences), FACTS, SEEN)
        self.assertEqual(len(result["lede_items"]), 2)
        self.assertEqual(result["lede"], "A" * 210 + ". " + "B" * 210 + ".")
        self.assertLessEqual(len(result["lede"]), 450)

    def test_tool_schema_uses_supported_array_constraints(self):
        # Anthropic's strict tool schema rejects maxItems; enforce the editorial cap locally.
        self.assertNotIn('"maxItems"', json.dumps(story.PUBLISH_TOOL))
        result = story.clean_story(raw_story(forecast={"items": [item()] * 5}), FACTS, SEEN)
        self.assertEqual(len(result["forecast"]["items"]), 3)

    def test_schemas_avoid_numeric_bounds_the_api_rejects(self):
        for schema in (story.PUBLISH_TOOL, story.RATING_SCHEMA):
            text = json.dumps(schema)
            for keyword in ('"minimum"', '"maximum"', '"maxLength"', '"minItems"', '"maxItems"'):
                self.assertNotIn(keyword, text)

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

    def test_later_stories_are_preserved_alongside_nearer_news(self):
        later = item(ids=["later"])
        result = story.clean_story(raw_story(lede_items=[later], forecast={"items": [item(), later]}), FACTS, SEEN)
        self.assertEqual(len(result["forecast"]["items"]), 2)
        self.assertEqual(result["lede_items"][0]["match_ids"], ["later"])
        self.assertEqual(list(result["notes"]), ["1"])

    def test_later_fallback_survives_when_near_news_is_not_on_default_services(self):
        facts = {"next_24_hours": [{"id": "1"}], "later_if_needed": [{"id": "later"}]}
        result = story.clean_story(raw_story(later_reason="Nothing available on the default lineup sooner.",
                                           forecast={"items": [item(), item(ids=["later"])]}), facts, SEEN)
        self.assertEqual(len(result["forecast"]["items"]), 2)
        self.assertTrue(result["later_reason"])

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


LINK = "https://www.espn.com/soccer/match/_/gameId/{}".format
READ = "https://news.example/preview"


class Sources(unittest.TestCase):
    """A source counts only if this run read it, or it is ESPN's page for a fixture the text names,
    shown as ESPN's table and form. These are the checks a plausible citation has to pass."""

    LINKS = {"1": LINK("1"), "2": LINK("2")}

    def test_url_key_ignores_presentation_but_not_the_page(self):
        key = story.url_key
        self.assertEqual(key("https://www.example.com/a/b/"), key("http://example.com/a/b"))
        self.assertEqual(key("https://example.com/a#comments"), key("https://example.com/a"))
        self.assertNotEqual(key("https://example.com/a"), key("https://example.com/b"))
        self.assertNotEqual(key("https://example.com/a?id=1"), key("https://example.com/a?id=2"))
        for bad in ("javascript:alert(1)", "ftp://example.com/a", "example.com/a", "", "http://"):
            self.assertEqual(key(bad), "", bad)

    def test_collect_sources_records_search_and_fetch_results_only(self):
        content = [
            SimpleNamespace(type="web_search_tool_result", content=[
                SimpleNamespace(type="web_search_result", url="https://news.example/a", title="A"),
                SimpleNamespace(type="web_search_result", url="not a url", title="Junk")]),
            SimpleNamespace(type="web_search_tool_result", content=SimpleNamespace(type="web_search_tool_result_error", error_code="max_uses_exceeded")),
            SimpleNamespace(type="web_fetch_tool_result", content=SimpleNamespace(
                type="web_fetch_result", url="https://news.example/b", content=SimpleNamespace(title="B"))),
            SimpleNamespace(type="text", text="https://news.example/c is mentioned, not read"),
        ]
        seen = {}
        story.collect_sources(content, seen)
        self.assertEqual(seen, {story.url_key("https://news.example/a"): ("https://news.example/a", "A"),
                                story.url_key("https://news.example/b"): ("https://news.example/b", "B")})

    def test_espn_page_counts_only_for_the_fixture_the_text_names(self):
        sources, unverified = story.cite([LINK("1"), LINK("2"), READ], ["1"], {}, self.LINKS)
        self.assertEqual(sources, [story.facts_source(LINK("1"))])
        self.assertEqual(unverified, 2)
        sources, unverified = story.cite([READ, READ, LINK("1")], ["1"], {story.url_key(READ): (READ, "R")}, self.LINKS)
        self.assertEqual(sources, [{"url": READ, "title": "R"}, story.facts_source(LINK("1"))])
        self.assertEqual(unverified, 0)

    def test_news_needs_a_page_read_in_this_run(self):
        facts = {"next_24_hours": [{"id": "1", "source_url": LINK("1")}]}
        seen = {story.url_key(READ): (READ, "R")}
        espn_only = item("Facts only.", sources=[LINK("1")])
        both = item("Reported.", sources=[READ, LINK("1")])
        result = story.clean_story(raw_story(lede_items=[espn_only, both], forecast={"items": [espn_only]},
                                             notes=[{"match_id": "1", "note": "ESPN says so.", "sources": [LINK("1")]}]), facts, seen)
        self.assertEqual(result["lede"], "Reported.")
        self.assertEqual(result["lede_items"][0]["sources"], [{"url": READ, "title": "R"}, story.facts_source(LINK("1"))])
        self.assertNotIn("forecast", result)
        self.assertEqual(result["notes"], {})

    def test_notes_need_a_known_fixture_and_a_page_read(self):
        facts = {"next_24_hours": [{"id": "1", "source_url": LINK("1")}, {"id": "2", "source_url": LINK("2")}]}
        seen = {story.url_key(READ): (READ, "R")}
        notes = [{"match_id": "unknown", "note": "Not on the page.", "sources": [READ]},
                 {"match_id": "1", "note": "First.", "sources": [READ]},
                 {"match_id": "1", "note": "Second note for the same match.", "sources": [READ]},
                 {"match_id": "2", "note": "Unread.", "sources": ["https://elsewhere.example/x"]},
                 {"match_id": "2", "note": "Nothing cited.", "sources": []}, "not a note", {"match_id": "2"}]
        result = story.clean_story(raw_story(notes=notes), facts, seen)
        self.assertEqual(result["notes"], {"1": {"note": "First.", "sources": [{"url": READ, "title": "R"}]}})
        self.assertEqual(result["_dropped"], 6)

    def test_league_paragraph_rests_on_facts_only_when_it_cites_nothing(self):
        facts = dict(FACTS, league_candidates=[{"league_id": "eng.1", "matches": [{"id": "1", "source_url": LINK("1")}]}])
        cases = {(): [story.facts_source(LINK("1"))], (LINK("1"),): [story.facts_source(LINK("1"))],
                 (READ,): [{"url": READ, "title": "R"}], ("https://unread.example/a",): None, (LINK("2"),): None}
        for cited, expected in cases.items():
            with self.subTest(cited=cited):
                blurb = dict(item("Context.", sources=list(cited)), league_id="eng.1", interest=40)
                result = story.clean_story(raw_story(league_blurbs=[blurb]), facts, {story.url_key(READ): (READ, "R")})
                saved = result["league_blurbs"][0]["sources"] if result["league_blurbs"] else None
                self.assertEqual(saved, expected)

    def test_long_league_paragraph_is_trimmed_at_a_sentence(self):
        facts = dict(FACTS, league_candidates=[{"league_id": "eng.1", "matches": [{"id": "1"}]}])
        first, second = "A" * 300 + ". ", "B" * 200 + "."
        segments = [{"text": first[:100], "match_ids": ["1"]}, {"text": first[100:] + second, "match_ids": []}]
        blurb = {"league_id": "eng.1", "interest": 40, "segments": segments, "sources": [SOURCE]}
        saved = story.clean_story(raw_story(league_blurbs=[blurb]), facts, SEEN)["league_blurbs"][0]
        self.assertEqual(saved["text"], first.rstrip())
        self.assertEqual("".join(s["text"] for s in saved["segments"]), saved["text"])
        self.assertEqual(saved["segments"][0], segments[0])

    def test_earlier_espn_facts_are_not_treated_as_read(self):
        previous = {"rankings": {"1": {"sources": [{"url": LINK("1"), "title": "ESPN match facts"}]},
                                 "2": {"sources": [story.facts_source(LINK("2"))]},
                                 "3": {"sources": [{"url": READ, "title": "R"}]}}}
        self.assertEqual(set(story.earlier_sources(previous)), {story.url_key(READ)})


def fixture(mid, hours, league="eng.1"):
    built = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
    return {"id": mid, "kickoff_utc": (built + timedelta(hours=hours)).isoformat(), "league_id": league,
            "competition": "Premier League" if league == "eng.1" else "La Liga", "source_url": LINK(mid),
            "home": {"name": "Home " + mid}, "away": {"name": "Away " + mid}, "venue": "Ground " + mid}


RUN_FACTS = {
    "date": "2026-10-07", "weekday": "Wednesday", "built_at": "2026-10-07T17:00:00Z",
    "focus_until": "2026-10-08T17:00:00+00:00", "owner_services": ["ESPN"], "owner_service_ids": ["espn"],
    "leagues": [{"league_id": "eng.1", "competition": "Premier League"}, {"league_id": "esp.1", "competition": "La Liga"},
                {"league_id": "ita.1", "competition": "Serie A"}],
    "next_24_hours": [fixture("1", 3)], "later_if_needed": [fixture("2", 30, "esp.1")],
    "league_candidates": [{"league_id": "eng.1", "competition": "Premier League", "matches": [fixture("1", 3)]},
                          {"league_id": "esp.1", "competition": "La Liga", "matches": [fixture("2", 30, "esp.1")]}],
    "ranking_candidates": [fixture("1", 3), fixture("2", 30, "esp.1"), fixture("3", 60), fixture("4", 80), fixture("5", 100)],
    "overview_fixtures": [fixture("1", 3)],
}


def blurb(league, mid, sources=()):
    return {"league_id": league, "interest": 50, "segments": [{"text": f"Context for {mid}.", "match_ids": [mid]}], "sources": list(sources)}


def publication(**over):
    raw = {"lede_items": [{"segments": [{"text": "Home 1 face Away 1 after a week of news.", "match_ids": ["1"]}], "sources": [READ]}],
           "league_order": ["esp.1", "eng.1"],
           "league_blurbs": [blurb("eng.1", "1"), blurb("esp.1", "2")],
           "notes": [{"match_id": "1", "note": "A researched note.", "sources": [READ]}],
           "forecast": {"items": []}}
    raw.update(over)
    return raw


def usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=2, cache_read_input_tokens=3, server_tool_use=None)


def news_reply(raw=None, read=(), stop="tool_use", as_string=False):
    content = []
    if read:
        content.append(SimpleNamespace(type="web_search_tool_result", content=[
            SimpleNamespace(type="web_search_result", url=u, title="T") for u in read]))
    if raw is not None:
        content.append(SimpleNamespace(type="tool_use", name="publish_story", id="publish",
                                       input=raw if not as_string else (raw if isinstance(raw, str) else json.dumps(raw))))
    return SimpleNamespace(model="test-model", usage=usage(), content=content, stop_reason=stop)


def every_rating(fixtures, sources=()):
    return [{"match_id": f["id"], "popularity": 50, "gameplay": 60, "impact": 70, "blurb": f"Blurb {f['id']}.",
             "sources": list(sources)} for f in fixtures]


class FakeSDK:
    """Stands in for the anthropic package. A request with tools is the research and gets the next
    news reply; one without is a rating request, answered by `rate` from the fixtures it lists.
    Rating requests arrive on worker threads, so the record is kept under a lock."""

    def __init__(self, news, rate=every_rating):
        self.news, self.rate, self.calls, self.lock = list(news), rate, [], threading.Lock()
        messages = SimpleNamespace(stream=self.stream)
        self.module = SimpleNamespace(Anthropic=lambda **_: SimpleNamespace(beta=SimpleNamespace(messages=messages)))

    @staticmethod
    def fixtures(call):
        return json.loads(call["messages"][0]["content"].split("exactly once:\n", 1)[1])

    def stream(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            message = self.news.pop(0) if "tools" in kwargs else None
        if message is None:
            ratings = self.rate(self.fixtures(kwargs))
            message = SimpleNamespace(model="test-model", usage=usage(), stop_reason="end_turn",
                                      content=[SimpleNamespace(type="text", text=json.dumps({"ratings": ratings}))])
        return contextlib.nullcontext(SimpleNamespace(get_final_message=lambda: message))

    def research_calls(self):
        return [c for c in self.calls if "tools" in c]

    def rating_calls(self):
        return [c for c in self.calls if "tools" not in c]


def totals():
    return dict.fromkeys(("in", "out", "cache_write", "cache_read", "searches", "fetches", "prompt_max"), 0)


def run(sdk, mode="full", previous=None, facts=RUN_FACTS, spent=None):
    spent = totals() if spent is None else spent
    with patch.dict(sys.modules, {"anthropic": sdk.module}), patch.object(story, "log", lambda *_: None):
        return story.write_story(facts, "test-model", "medium", mode, previous, spent)


class Requests(unittest.TestCase):
    """How a run spends requests: research once, ratings in small parallel chunks, repairs only for
    what is missing."""

    def test_full_run_researches_once_and_rates_every_fixture_in_chunks(self):
        sdk = FakeSDK([news_reply(publication(), read=[READ])])
        spent = totals()
        with patch.object(story, "RATING_CHUNK", 2), patch.object(story, "RATING_WORKERS", 2):
            result, served = run(sdk, spent=spent)
        self.assertEqual(served, "test-model")
        research, ratings = sdk.research_calls(), sdk.rating_calls()
        self.assertEqual(len(research), 1)
        self.assertEqual(research[0]["system"], story.SYSTEM)
        self.assertEqual(sorted(len(FakeSDK.fixtures(c)) for c in ratings), [1, 2, 2])
        for call in ratings:
            self.assertEqual(call["system"], story.RATING_SYSTEM)
            self.assertEqual(call["output_config"], {"effort": "medium", "format": {"type": "json_schema", "schema": story.RATING_SCHEMA}})
            self.assertNotIn("cache_control", call)
            self.assertNotIn("ESPN", json.dumps(FakeSDK.fixtures(call)))   # no URLs to copy
        self.assertEqual(sorted(result["rankings"]), ["1", "2", "3", "4", "5"])
        self.assertEqual(result["rankings"]["3"], {"popularity": 50, "gameplay": 60, "impact": 70, "score": 61.5,
                                                   "blurb": "Blurb 3.", "sources": [story.facts_source(LINK("3"))]})
        self.assertEqual(result["ranking_coverage"], {"rated": 5, "total": 5, "carried": 0})
        self.assertEqual(result["notes"]["1"]["sources"], [{"url": READ, "title": "T"}])
        self.assertEqual(result["league_order"], ["esp.1", "eng.1", "ita.1"])   # completed in build.py's order
        # Four requests of 10 fresh, 2 cache-written and 3 cache-read tokens: sums, but the largest single prompt.
        self.assertEqual(spent, {"in": 40, "out": 20, "cache_write": 8, "cache_read": 12, "searches": 0, "fetches": 0,
                                 "prompt_max": 15})

    def test_research_prompt_sends_each_fixture_once_and_no_rating_candidates(self):
        sdk = FakeSDK([news_reply(publication(), read=[READ])])
        run(sdk)
        prompt = sdk.research_calls()[0]["messages"][0]["content"]
        self.assertEqual(prompt.count('"venue":"Ground 1"'), 1)    # in next_24_hours and league_candidates
        self.assertIn('"next_24_hours":["1"]', prompt)
        for absent in ("Ground 3", "ranking_candidates", "schedule_by_day", "source_url", '\n "'):
            self.assertNotIn(absent, prompt)
        self.assertIn(story.FACTS_GUIDE, prompt)
        self.assertIn(story.FORECAST_GUIDE, prompt)

    def test_rating_blurbs_may_cite_the_research_but_nothing_else(self):
        def rate(fixtures):
            cited = {"1": [READ], "2": [LINK("3")], "3": ["https://unread.example/x"], "4": [READ, "https://unread.example/y"]}
            return [dict(r, sources=cited.get(r["match_id"], [])) for r in every_rating(fixtures)]
        sdk = FakeSDK([news_reply(publication(), read=[READ])], rate)
        result, _ = run(sdk)
        ranks = result["rankings"]
        self.assertEqual(ranks["1"]["sources"], [{"url": READ, "title": "T"}])
        self.assertNotIn("blurb", ranks["2"])           # another fixture's ESPN page
        self.assertNotIn("blurb", ranks["3"])           # a page nobody read
        self.assertEqual(ranks["4"]["sources"], [{"url": READ, "title": "T"}])
        self.assertEqual(ranks["5"]["sources"], [story.facts_source(LINK("5"))])
        self.assertEqual(ranks["2"]["score"], 61.5)     # the rating itself stands
        # The two blurbs were asked for again, once, with nothing else.
        self.assertEqual([sorted(f["id"] for f in FakeSDK.fixtures(c)) for c in sdk.rating_calls()][-1], ["2", "3"])
        self.assertEqual(result["match_blurb_coverage"], {"written": 3, "total": 5})

    def test_missing_and_failed_ratings_are_asked_for_once(self):
        state = {"rounds": 0}

        def rate(fixtures):
            ids = [f["id"] for f in fixtures]
            if "3" in ids and state["rounds"] == 0:
                state["rounds"] += 1
                raise RuntimeError("overloaded")
            return every_rating([f for f in fixtures if f["id"] != "5"])
        sdk = FakeSDK([news_reply(publication(), read=[READ])], rate)
        with patch.object(story, "RATING_CHUNK", 2):
            result, _ = run(sdk)
        self.assertEqual(sorted(result["rankings"]), ["1", "2", "3", "4"])
        retry = [sorted(f["id"] for f in FakeSDK.fixtures(c)) for c in sdk.rating_calls()][3:]
        self.assertEqual(sorted(sum(retry, [])), ["3", "4", "5"])   # the failed chunk and the missing fixture
        self.assertEqual(len(sdk.rating_calls()), 3 + 2)             # no third round

    def test_repair_asks_only_for_what_is_missing_and_keeps_the_rest(self):
        first = publication(league_blurbs=[blurb("eng.1", "1")], league_order=["eng.1"])
        second = publication(lede_items=[], notes=[], league_blurbs=[blurb("esp.1", "2"), blurb("eng.1", "1", [READ])],
                             league_order=["ita.1", "esp.1", "eng.1"])
        sdk = FakeSDK([news_reply(first, read=[READ]), news_reply(second)])
        result, _ = run(sdk)
        research = sdk.research_calls()
        self.assertEqual(len(research), 2)
        reply = research[1]["messages"][-1]["content"][0]
        self.assertEqual((reply["type"], reply["tool_use_id"], reply["is_error"]), ("tool_result", "publish", True))
        self.assertIn("esp.1", reply["content"])
        self.assertIn("ita.1", reply["content"])
        self.assertNotIn("lede_items", reply["content"])
        self.assertEqual(result["lede"], "Home 1 face Away 1 after a week of news.")
        self.assertEqual(list(result["notes"]), ["1"])
        self.assertEqual([b["league_id"] for b in result["league_blurbs"]], ["eng.1", "esp.1"])
        self.assertEqual(result["league_blurbs"][0]["sources"], [story.facts_source(LINK("1"))])   # the first one kept
        self.assertEqual(result["league_order"], ["eng.1", "ita.1", "esp.1"])
        self.assertEqual(result["blurb_coverage"], {"written": 2, "total": 2})

    def test_repair_is_asked_for_once(self):
        first = publication(league_blurbs=[blurb("eng.1", "1")])
        sdk = FakeSDK([news_reply(first, read=[READ]), news_reply(first)])
        result, _ = run(sdk)
        self.assertEqual(len(sdk.research_calls()), 2)
        self.assertEqual(result["blurb_coverage"], {"written": 1, "total": 2})

    def test_truncated_refused_or_unparseable_publications_are_not_used(self):
        for reply in (news_reply(publication(), read=[READ], stop="max_tokens"),
                      news_reply(publication(), read=[READ], stop="refusal"),
                      news_reply('{"lede_items": [', read=[READ], as_string=True)):
            with self.subTest(stop=reply.stop_reason):
                sdk = FakeSDK([reply])
                result, _ = run(sdk)
                self.assertEqual(len(sdk.research_calls()), 1)
                self.assertEqual((result["lede"], result["notes"], result["league_blurbs"]), ("", {}, []))
                self.assertEqual(len(result["rankings"]), 5)   # the ratings are still written

    def test_publication_sent_as_a_string_is_parsed(self):
        sdk = FakeSDK([news_reply(publication(), read=[READ], as_string=True)])
        result, _ = run(sdk)
        self.assertEqual(result["lede"], "Home 1 face Away 1 after a week of news.")

    def test_paused_turns_continue_and_an_unfinished_turn_is_nudged_once(self):
        sdk = FakeSDK([news_reply(read=[READ], stop="pause_turn"), news_reply(stop="end_turn"),
                       news_reply(publication())])
        result, _ = run(sdk)
        research = sdk.research_calls()
        self.assertEqual(len(research), 3)
        self.assertEqual(research[2]["messages"][-1], {"role": "user", "content": "Please call publish_story now with what you found."})
        self.assertEqual(list(result["notes"]), ["1"])   # the page read before the pause still counts

    def test_refresh_rerates_the_next_day_and_keeps_earlier_ratings(self):
        def rated(blurb_text, sources):
            return {"popularity": 40, "gameplay": 40, "impact": 40, "score": 99, "blurb": blurb_text, "sources": sources}
        previous = {"generated_at": "2026-10-07T09:00:00Z", "date": "2026-10-07", "lede_items": [], "notes": {},
                    "rankings": {"1": rated("Earlier blurb 1.", [story.facts_source(LINK("1"))]),
                                 "2": {"popularity": 40, "gameplay": 40, "impact": 40, "score": 40},
                                 "3": rated("Earlier blurb 3.", [{"url": LINK("3"), "title": "ESPN match facts"}]),
                                 "4": rated("Earlier blurb 4.", [{"url": READ, "title": "R"}]),
                                 "gone": rated("A finished match.", [])}}
        sdk = FakeSDK([news_reply(publication(), read=[READ])])
        result, _ = run(sdk, mode="refresh", previous=previous)
        rerated = sorted(f["id"] for c in sdk.rating_calls() for f in FakeSDK.fixtures(c))
        self.assertEqual(rerated, ["1", "2", "5"])       # due within 24 hours, without a blurb, unrated
        self.assertEqual(result["rankings"]["3"], {"popularity": 40, "gameplay": 40, "impact": 40, "score": 40.0,
                                                   "blurb": "Earlier blurb 3.", "sources": [story.facts_source(LINK("3"))]})
        self.assertEqual(result["rankings"]["4"]["sources"], [{"url": READ, "title": "R"}])
        self.assertEqual(result["rankings"]["1"]["blurb"], "Blurb 1.")
        self.assertNotIn("gone", result["rankings"])
        self.assertEqual(result["ranking_coverage"], {"rated": 5, "total": 5, "carried": 2})
        prompt = sdk.research_calls()[0]["messages"][0]["content"]
        self.assertNotIn("Earlier blurb", prompt)       # earlier ratings are not resent to the research
        self.assertEqual(sdk.research_calls()[0]["tools"][0]["max_uses"], story.BUDGETS["refresh"][0])

    def test_refresh_keeps_earlier_news_when_the_research_fails(self):
        previous = {"generated_at": "2026-10-07T09:00:00Z", "date": "2026-10-07", "lede": "Earlier overview.",
                    "lede_items": [{"text": "Earlier overview.", "match_ids": ["1"], "segments": [], "sources": []}],
                    "notes": {"1": {"note": "Earlier note.", "sources": [{"url": READ, "title": "R"}]}},
                    "league_blurbs": [], "league_order": ["eng.1"], "rankings": {}}
        sdk = FakeSDK([news_reply(publication(), read=[READ], stop="refusal")])
        result, _ = run(sdk, mode="refresh", previous=previous)
        self.assertEqual(result["lede"], "Earlier overview.")
        self.assertEqual(result["notes"], previous["notes"])
        self.assertEqual(len(result["rankings"]), 5)


# The settings the tests run under unless they say otherwise: the full design, as before the switch,
# and what a story written under them records.
FULL = '[ai]\nenabled = true\ndesign = "full"\nmodel = "claude-opus-5-5"\neffort = "medium"\n'
RATINGS = '[ai]\nenabled = true\ndesign = "ratings"\nmodel = "gpt-6.1-sol"\neffort = "low"\n'
BY_FULL = {"kind": "full", "model": "claude-opus-5-5", "effort": "medium"}
BY_RATINGS = {"kind": "ratings", "model": "gpt-6.1-sol", "requested_model": "gpt-6.1-sol", "effort": "low"}


class Modes(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)

    def test_auto_mode_follows_the_age_of_todays_story(self):
        choose = story.choose_mode
        self.assertEqual(choose("auto", None, self.NOW)[0], "full")
        self.assertEqual(choose("auto", {"generated_at": "2026-10-07T15:30:00Z"}, self.NOW)[0], "keep")
        self.assertEqual(choose("auto", {"generated_at": "2026-10-07T13:30:00Z"}, self.NOW)[0], "refresh")
        self.assertEqual(choose("auto", {"generated_at": "garbled"}, self.NOW)[0], "full")
        self.assertEqual(choose("refresh", None, self.NOW)[0], "full")
        for mode in ("full", "keep"):
            self.assertEqual(choose(mode, None, self.NOW)[0], mode)

    def test_daily_mode_writes_the_days_story_once_and_never_refreshes_it(self):
        choose = story.choose_mode
        self.assertEqual(choose("daily", None, self.NOW)[0], "full")
        for written in ("2026-10-07T16:30:00Z", "2026-10-07T08:55:00Z", "garbled"):    # fresh, hours old, unreadable
            self.assertEqual(choose("daily", {"generated_at": written}, self.NOW)[0], "keep")

    def test_daily_runs_write_only_when_today_has_no_story(self):
        today = dict(BY_FULL, version=1, date="2026-10-07", generated_at="2026-10-07T08:55:00Z", lede="This morning's.")
        yesterday = dict(BY_FULL, version=1, date="2026-10-06", generated_at="2026-10-06T08:55:00Z", lede="Yesterday's.")
        self.assertEqual(self.main("daily", today), today)          # the midday and evening builds: no API call
        modes = []
        written = {"headline": "", "headline_segments": [], "lede": "Today's.", "lede_items": [], "sources": [],
                   "notes": {}, "later_reason": "", "league_blurbs": [], "league_order": [], "_dropped": 0,
                   "rankings": {"1": {"score": 50}}, "ranking_coverage": {"rated": 1, "total": 1, "carried": 0}}

        def writer(*args, **kwargs):
            modes.append(next(a for a in args if a in ("full", "refresh")))
            return dict(written), "test-model"
        result = self.main("daily", yesterday, writer=writer)      # the morning build, or the next if it failed
        self.assertEqual(modes, ["full"])
        self.assertEqual((result["date"], result["kind"], result["lede"]), ("2026-10-07", "full", "Today's."))

    def main(self, mode, previous, key="test-key", writer=None, settings=FULL, extra=(), key_name="ANTHROPIC_API_KEY", keys=None):
        """Runs story.main() on RUN_FACTS with its own settings file (`settings` is its text, or None
        for no file at all), so the repository's settings never decide a test. `key` goes in `key_name`,
        `keys` adds others ({name: key}), and no other provider key is set."""
        with tempfile.TemporaryDirectory() as tmp:
            facts_path, prev_path, out = Path(tmp) / "facts.json", Path(tmp) / "prev.json", Path(tmp) / "story.json"
            facts_path.write_text(json.dumps(dict(RUN_FACTS, date="2026-10-07")))
            settings_path = Path(tmp) / "settings.toml"
            if settings is not None:
                settings_path.write_text(settings)
            args = ["story.py", "--facts", str(facts_path), "--out", str(out), "--mode", mode,
                    "--settings", str(settings_path), *extra]
            if previous is not None:
                prev_path.write_text(json.dumps(previous))
                args += ["--previous", str(prev_path)]
            env = {k: v for k, v in os.environ.items() if k not in providers.KEY_NAMES.values()}
            if key:
                env[key_name] = key
            env.update(keys or {})
            writer = writer or (lambda *a: self.fail("no API call expected"))
            with patch.object(sys, "argv", args), patch.dict(os.environ, env, clear=True), \
                    patch.object(story, "write_story", writer), patch.object(story, "log", lambda *_: None):
                self.assertEqual(story.main(), 0)
            return json.loads(out.read_text()) if out.exists() else None

    def test_ai_switched_off_calls_nothing_and_drops_the_published_story(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = dict(BY_FULL, version=1, date="2026-10-07", generated_at=fresh, lede="Today's.")
        off = FULL.replace("enabled = true", "enabled = false")
        # Not even kept: the publish that follows must take Claude's text off the page.
        for mode in ("keep", "daily", "full", "refresh", "auto"):
            self.assertIsNone(self.main(mode, today, settings=off), mode)
        self.assertIsNone(self.main("daily", None, settings=off))
        # A switch that can't be read spends nothing either.
        for unreadable in (None, FULL.replace("true", '"yes"'), "[ai]\nenabled = true\n", "[ai\n", "ai = 3\n",
                           FULL.replace("claude-opus-5-5", "gpt-6.1-sol"), FULL.replace('effort = "medium"', 'effort = "none"')):
            self.assertIsNone(self.main("full", today, settings=unreadable), unreadable)

    def test_measuring_ignores_the_switch(self):
        calls = []

        def writer(*args, **kwargs):
            calls.append(next(a for a in args if a in ("full", "refresh")))
            return None, "test-model"
        self.main("full", None, writer=writer, settings=FULL.replace("enabled = true", "enabled = false"), extra=["--ignore-switch"])
        self.assertEqual(calls, ["full"])

    def test_keep_and_fallbacks_never_lose_the_published_story(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = dict(BY_FULL, version=1, date="2026-10-07", generated_at=fresh, lede="Today's.")
        yesterday = dict(BY_FULL, version=1, date="2026-10-06", generated_at="2026-10-06T22:00:00Z", lede="Yesterday's.")
        self.assertEqual(self.main("keep", yesterday), yesterday)          # a code push republishes as is
        self.assertEqual(self.main("auto", today), today)                  # too fresh to pay for again
        self.assertEqual(self.main("refresh", today, key=None), today)     # no key: today's is reused
        self.assertIsNone(self.main("refresh", yesterday, key=None))       # but never yesterday's

        def failing(*_):
            raise RuntimeError("API down")
        old = dict(today, generated_at="2026-10-07T09:00:00Z")
        self.assertEqual(self.main("refresh", old, writer=failing), old)  # a failure keeps today's

    def test_written_story_records_what_the_page_needs(self):
        written = {"headline": "", "headline_segments": [], "lede": "", "lede_items": [], "sources": [], "notes": {},
                   "later_reason": "", "league_blurbs": [], "league_order": [], "_dropped": 0,
                   "rankings": {"1": {"score": 50}}, "ranking_coverage": {"rated": 1, "total": 5, "carried": 0}}
        result = self.main("full", None, writer=lambda *a: (dict(written), "test-model"))
        self.assertEqual({k: result[k] for k in ("version", "date", "kind", "model", "requested_model", "effort", "services", "focus_until")},
                         {"version": 1, "date": "2026-10-07", "kind": "full", "model": "test-model", "requested_model": "claude-opus-5-5",
                          "effort": "medium",
                          "services": ["espn"], "focus_until": "2026-10-08T17:00:00+00:00"})
        self.assertNotIn("_dropped", result)


def openai_ratings(calls, served="gpt-6.1-sol", drop=()):
    """Stands in for providers.post_json as OpenAI's Responses API: rates every fixture a request lists
    (except those in `drop`) with popularity 50, gameplay 60 and impact 70, and records each request."""
    def post(url, body, headers, **_):
        calls.append((url, body, headers))
        fixtures = json.loads(body["input"].split("exactly once:\n", 1)[1])
        ratings = [{"match_id": f["id"], "popularity": 50, "gameplay": 60, "impact": 70} for f in fixtures if f["id"] not in drop]
        return {"model": served, "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps({"ratings": ratings}), "annotations": []}]}],
                "usage": {"input_tokens": 17000, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 2800,
                          "output_tokens_details": {"reasoning_tokens": 20}}}
    return post


class Switch(unittest.TestCase):
    """settings.toml's [ai] table chooses the design, the model and its effort; a story another choice
    wrote is not today's, and the push that changes the choice writes at once."""
    NOW = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
    main = Modes.main

    def test_gpt_rates_through_openai_with_claudes_prompt_and_schema(self):
        calls, real = [], story.write_story
        broken = SimpleNamespace(Anthropic=lambda **_: self.fail("the ratings design on gpt-6.1-sol never calls Claude"))
        with patch.object(providers, "post_json", openai_ratings(calls)), patch.dict(sys.modules, {"anthropic": broken}), \
                patch.object(story, "summary", lambda *_: None):
            result = self.main("daily", None, writer=real, settings=RATINGS, key="sk-test", key_name="OPENAI_API_KEY")
        (url, body, headers), = calls
        self.assertEqual((url, headers), ("https://api.openai.com/v1/responses", {"Authorization": "Bearer sk-test"}))
        self.assertEqual((body["model"], body["instructions"], body["reasoning"]), ("gpt-6.1-sol", story.SCORES_SYSTEM, {"effort": "low"}))
        self.assertEqual(body["text"]["format"]["schema"], story.SCORES_SCHEMA)
        self.assertEqual([f["id"] for f in json.loads(body["input"].split("exactly once:\n", 1)[1])], ["1", "2", "3", "4", "5"])
        self.assertNotIn("tools", body)
        self.assertEqual({k: result[k] for k in ("kind", "model", "requested_model", "effort")},
                         {"kind": "ratings", "model": "gpt-6.1-sol", "requested_model": "gpt-6.1-sol", "effort": "low"})
        # 25% of 50, 35% of 60 and 40% of 70: 12.5 + 21 + 28.
        self.assertEqual(result["rankings"]["1"], {"popularity": 50, "gameplay": 60, "impact": 70, "score": 61.5})
        self.assertEqual((result["lede_items"], result["notes"], result["league_blurbs"]), ([], {}, []))

    def test_a_fixture_left_out_is_asked_for_once_more_and_the_cost_is_openais(self):
        calls, real, lines = [], story.write_story, []
        first = openai_ratings(calls, drop=("3",))
        answers = [first, openai_ratings(calls)]
        with patch.object(providers, "post_json", lambda *a, **k: answers.pop(0)(*a, **k)), \
                patch.object(story, "summary", lines.append):
            result = self.main("ratings", None, writer=real, settings=RATINGS, key="sk", key_name="OPENAI_API_KEY")
        self.assertEqual([json.loads(b["input"].split("exactly once:\n", 1)[1])[0]["id"] for _, b, _ in calls], ["1", "3"])
        self.assertEqual(sorted(result["rankings"]), ["1", "2", "3", "4", "5"])
        # Two requests of 16,000 fresh and 1,000 cached input and 2,800 output tokens at gpt-6.1-sol's
        # $2, $0.10 and $10 per million: 2 x (0.032 + 0.0001 + 0.028) = $0.1202.
        self.assertIn("about $0.1202 at gpt-6.1-sol list prices", lines[0])
        self.assertIn("output 5,600 tokens, 40 of them reasoning", lines[0])

    def test_the_ratings_design_rates_once_a_day_and_whenever_asked(self):
        choose = lambda mode, todays=None, switched=False: story.choose_mode(mode, todays, self.NOW, "ratings", switched)[0]
        today = dict(BY_RATINGS, generated_at="2026-10-07T08:55:00Z")
        self.assertEqual([choose(m) for m in ("daily", "auto", "full", "refresh", "ratings")], ["ratings"] * 5)
        self.assertEqual([choose(m, today) for m in ("daily", "auto")], ["keep", "keep"])
        self.assertEqual([choose(m, today) for m in ("full", "refresh", "ratings")], ["ratings"] * 3)
        self.assertEqual((choose("keep"), choose("keep", switched=True)), ("keep", "ratings"))
        self.assertEqual(story.choose_mode("keep", None, self.NOW, "full", True)[0], "full")

    def test_a_story_another_choice_wrote_is_rewritten_even_by_a_push(self):
        models = []

        def writer(facts, model, effort, mode, *rest):
            models.append((model, effort, mode))
            return {"lede_items": [], "notes": {}, "league_blurbs": [], "league_order": [], "_dropped": 0, "rankings": {"1": {"score": 50}},
                    "ranking_coverage": {"rated": 1, "total": 1, "carried": 0}}, model
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        opus = dict(BY_FULL, version=1, date="2026-10-07", generated_at=fresh, lede="Opus wrote this.")
        mine = dict(BY_RATINGS, version=1, date="2026-10-07", generated_at=fresh)
        sol = dict(key="sk", key_name="OPENAI_API_KEY", settings=RATINGS)
        self.assertEqual(self.main("keep", mine, **sol), mine)                    # a push with nothing changed: no call
        self.assertEqual(self.main("daily", mine, **sol), mine)                   # the midday build
        result = self.main("keep", opus, writer=writer, **sol)                    # the push that switches to gpt-6.1-sol
        self.assertEqual((models, result["kind"], result["requested_model"]), ([("gpt-6.1-sol", "low", "ratings")], "ratings", "gpt-6.1-sol"))
        self.main("daily", opus, writer=writer, **sol)                            # or the morning build, if no push came first
        self.main("keep", dict(mine, effort="medium"), writer=writer, **sol)      # an effort changed is a choice changed
        haiku = dict(mine, model="claude-haiku-5-5", requested_model="claude-haiku-5-5")
        self.main("daily", haiku, writer=writer, **sol)                           # so is a model, design and effort alike
        self.assertEqual([m[2] for m in models], ["ratings"] * 4)
        # Back to the full design: the push writes Claude's storylines (the full run), and a story it wrote counts.
        back = []
        self.main("keep", mine, writer=lambda f, m, e, mode, *r: back.append(mode) or (None, m))
        self.assertEqual(back, ["full"])
        self.assertEqual(self.main("daily", opus), opus)

    def test_without_the_providers_key_nothing_is_called(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mine = dict(BY_RATINGS, version=1, date="2026-10-07", generated_at=fresh)
        # An Anthropic key is no use to gpt-6.1-sol: today's story stays up, and with none there is nothing to publish.
        self.assertEqual(self.main("refresh", mine, settings=RATINGS, key="sk-ant", key_name="ANTHROPIC_API_KEY"), mine)
        self.assertIsNone(self.main("refresh", None, settings=RATINGS, key="sk-ant", key_name="ANTHROPIC_API_KEY"))

    def test_other_providers_rate_scores_only_and_are_priced_as_requested(self):
        with self.assertRaises(ValueError):
            story.api_rate_chunk("gpt-6.1-sol", "low", "sk", [], [], "", scores_only=False)
        million = {"in": 1_000_000, "cache_write": 0, "cache_read": 1_000_000, "out": 1_000_000, "searches": 0, "fetches": 0, "prompt_max": 0}
        self.assertAlmostEqual(story.cost_of(million, "gpt-6.1-sol"), 2 + 0.10 + 10)
        self.assertAlmostEqual(story.cost_of(million, "gemini-3.1-flash-lite"), 0.25 + 0.025 + 1.50)
        self.assertIsNone(story.cost_of(million, "gpt-6.1-sol-2026-09-01"))
        with patch.object(story, "summary", lambda *_: None), patch.object(story, "log", lambda *_: None):
            # A dated snapshot of the requested model has no price of its own: it is priced as requested.
            self.assertAlmostEqual(story.report_cost(dict(million), "gpt-6.1-sol-2026-09-01", "gpt-6.1-sol", "low", "ratings", 1), 12.10)
            # A server-side fallback to another model with prices is priced as served.
            self.assertAlmostEqual(story.report_cost(dict(million), "claude-sonnet-5-5", "claude-opus-5-5", "low", "ratings", 1), 12.10)

    def test_google_rates_through_its_own_structured_output(self):
        calls, real = [], story.write_story

        def post(url, body, headers, **_):
            calls.append((url, body, headers))
            fixtures = json.loads(body["contents"][0]["parts"][0]["text"].split("exactly once:\n", 1)[1])
            text = json.dumps({"ratings": [{"match_id": f["id"], "popularity": 40, "gameplay": 40, "impact": 40} for f in fixtures]})
            return {"modelVersion": "gemini-3.1-flash-lite", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
                    "usageMetadata": {"promptTokenCount": 19000, "candidatesTokenCount": 4000, "thoughtsTokenCount": 100}}
        settings = RATINGS.replace("gpt-6.1-sol", "gemini-3.1-flash-lite")
        with patch.object(providers, "post_json", post), patch.object(story, "summary", lambda *_: None):
            result = self.main("daily", None, writer=real, settings=settings, key="g-key", key_name="GEMINI_API_KEY")
        (url, body, headers), = calls
        self.assertTrue(url.endswith("/models/gemini-3.1-flash-lite:generateContent"))
        self.assertEqual(headers, {"x-goog-api-key": "g-key"})
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], story.SCORES_SYSTEM)
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual((result["requested_model"], result["rankings"]["2"]["score"]), ("gemini-3.1-flash-lite", 40.0))


RATINGS_OVERVIEW = RATINGS + 'overview_model = "gemini-3.1-pro-preview"\noverview_effort = "medium"\n'
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def overview_reply(text, **over):
    return providers.Reply(**dict({"text": text, "stop": "end", "served": "test"}, **over))


def live(url):
    return {"state": "live", "status": 200}


class Overview(unittest.TestCase):
    """The ratings design's overview: what of a model's answer may reach the page, and how the daily run
    asks for it. The rule is the full design's: a cited page counts only if the model's own search
    returned it, and an overview without one such page is not shown."""

    def check(self, reply, resolve=None, check=live):
        return story.checked_overview(reply, resolve=resolve or (lambda url: None), check=check, workers=2)

    def test_only_pages_its_search_returned_are_kept_and_the_text_is_plain(self):
        page = "https://news.example/a"
        text = json.dumps({"overview": "Arsenal host Leeds [1]  on Saturday ([bbc](https://bbc.com/x)), and AZ visit Feyenoord [2, 3].",
                           "sources": [page + "?utm_source=openai", "https://made.up/b", page, 7]})
        inside = text.index("Arsenal")
        before = "https://news.example/prose"      # its search returned it, but it is cited in prose outside the overview
        reply = overview_reply("Here it is: " + text, returned={story.link_key(page): (page, "A preview"), story.link_key(before): (before, "")},
                               native=[(len("Here it is: ") + inside, len("Here it is: ") + inside + 7, page), (0, 4, before)])
        overview, why = self.check(reply)
        self.assertEqual(why, "")
        # Footnote markers, inline links and doubled spaces go; the page lists the sources itself.
        self.assertEqual(overview["text"], "Arsenal host Leeds on Saturday, and AZ visit Feyenoord.")
        # The tracked link is the page its search returned; the made-up one, and a citation outside the JSON, are not.
        self.assertEqual(overview["sources"], [{"url": page + "?utm_source=openai", "title": "A preview"}])

    def test_a_long_overview_is_cut_at_a_sentence(self):
        page = "https://news.example/a"
        first, second = "A" * 300 + ".", " " + "B" * 200 + "."
        reply = overview_reply(json.dumps({"overview": first + second, "sources": [page]}), returned={story.link_key(page): (page, "")})
        self.assertEqual(self.check(reply)[0]["text"], first)

    def test_googles_grounding_links_are_its_search_whether_or_not_the_reply_records_it(self):
        chunk, cited, expired = REDIRECT + "CHUNK", REDIRECT + "CITED", REDIRECT + "EXPIRED"
        targets = {chunk: "https://www.marca.com/futbol/x.html", cited: "https://as.com/y"}
        text = json.dumps({"overview": "Barcelona host Getafe.", "sources": [cited, "https://made.up/z", expired]})
        start = text.index("Barcelona")
        # The grounding record names one chunk, cited inside the paragraph; the model also lists a grounding
        # link of its own (as Gemini 3.x does with no record at all), a page of its own and a dead redirect.
        reply = overview_reply(text, redirects={chunk: "marca.com"}, native=[(start, start + 9, chunk)], queries=["barcelona getafe"])
        overview, why = self.check(reply, resolve=targets.get)
        self.assertEqual(overview["sources"], [{"url": "https://as.com/y", "title": ""},
                                               {"url": "https://www.marca.com/futbol/x.html", "title": "marca.com"}])
        # With no grounding record at all, the cited links still lead to the pages its search found.
        bare = overview_reply(json.dumps({"overview": "Barcelona host Getafe.", "sources": [cited]}))
        self.assertEqual(self.check(bare, resolve=targets.get)[0]["sources"], [{"url": "https://as.com/y", "title": ""}])

    def test_every_reason_an_overview_is_not_shown(self):
        page = "https://news.example/a"
        returned = {story.link_key(page): (page, "A")}
        cases = [
            (overview_reply("No JSON here.", stop="max_tokens"), "its reply held no overview (it stopped: max_tokens)"),
            (overview_reply('{"overview": ["a list"]}'), "its reply held no overview"),
            (overview_reply('{"overview": "  ", "sources": []}'), "it wrote an empty overview, having found nothing it could support"),
            (overview_reply('{"overview": "Text.", "sources": []}', returned=returned), "it cited no pages"),
            (overview_reply('{"overview": "Text.", "sources": ["https://made.up/a", "https://made.up/b"]}'),
             "none of the 2 pages it cited came from its own search; its reply recorded no search"),
            (overview_reply('{"overview": "Text.", "sources": ["https://made.up/a"]}', returned=returned, queries=["q"]),
             "none of the 1 pages it cited came from its own search"),
        ]
        for reply, why in cases:
            with self.subTest(why=why):
                self.assertEqual(self.check(reply), (None, why))
        gone = overview_reply(json.dumps({"overview": "Text.", "sources": [page]}), returned=returned)
        self.assertEqual(self.check(gone, check=lambda url: {"state": "dead", "status": 404}),
                         (None, "every page from its own search that it cited is gone"))
        # A site that refuses a script says nothing about the page, so the page stays.
        self.assertEqual(len(self.check(gone, check=lambda url: {"state": "blocked", "status": 403})[0]["sources"]), 1)

    def test_the_daily_run_writes_it_after_the_ratings_with_its_own_providers_key(self):
        calls, lines, real = [], [], story.write_story
        rate = openai_ratings(calls)
        page = "https://www.bbc.com/sport/football/arsenal-leeds"
        answer = json.dumps({"overview": "Arsenal host Leeds on Saturday with Saka fit.", "sources": [REDIRECT + "AAA"]})

        def post(url, body, headers, **kw):
            if "openai" in url:
                return rate(url, body, headers, **kw)
            calls.append((url, body, headers))
            return {"modelVersion": "gemini-3.1-pro-preview", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": answer}]}}],
                    "usageMetadata": {"promptTokenCount": 6000, "candidatesTokenCount": 500, "thoughtsTokenCount": 2500}}
        with patch.object(providers, "post_json", post), patch.object(story, "summary", lines.append), \
                patch.object(story, "location", {REDIRECT + "AAA": page}.get), patch.object(story, "check_link", live):
            result = self.main("daily", None, writer=real, settings=RATINGS_OVERVIEW, key="sk", key_name="OPENAI_API_KEY",
                               keys={"GEMINI_API_KEY": "g-key"})
        (o_url, _, _), (g_url, g_body, g_headers) = calls
        self.assertIn("openai", o_url)                                  # the ratings first
        self.assertEqual((g_url.rsplit("/", 1)[1], g_headers), ("gemini-3.1-pro-preview:generateContent", {"x-goog-api-key": "g-key"}))
        facts = dict(RUN_FACTS, date="2026-10-07")
        self.assertEqual(g_body["systemInstruction"]["parts"][0]["text"], story.OVERVIEW_SYSTEM)
        self.assertEqual(g_body["contents"][0]["parts"][0]["text"], story.overview_prompt(story.overview_header(facts), story.overview_view(facts)))
        self.assertEqual((g_body["tools"], g_body["generationConfig"]["thinkingConfig"]), ([{"google_search": {}}], {"thinkingLevel": "medium"}))
        self.assertEqual(result["overview"], {"text": "Arsenal host Leeds on Saturday with Saka fit.", "sources": [{"url": page, "title": ""}],
                                              "model": "gemini-3.1-pro-preview", "requested_model": "gemini-3.1-pro-preview", "effort": "medium"})
        self.assertEqual((result["overview_model"], result["overview_effort"], sorted(result["rankings"])),
                         ("gemini-3.1-pro-preview", "medium", ["1", "2", "3", "4", "5"]))
        # 6,000 input at $2/M and 3,000 output (with thinking) at $12/M: $0.012 + $0.036.
        overview_line = next(line for line in lines if line.startswith("Overview:"))
        self.assertIn("about $0.0480; published with 1 source(s)", overview_line)

    def test_a_failed_or_impossible_overview_never_costs_the_ratings(self):
        calls, lines, real = [], [], story.write_story
        rate = openai_ratings(calls)

        def post(url, body, headers, **kw):
            if "openai" in url:
                return rate(url, body, headers, **kw)
            raise providers.ProviderError("HTTP 503: overloaded")
        with patch.object(providers, "post_json", post), patch.object(story, "summary", lines.append):
            failed = self.main("daily", None, writer=real, settings=RATINGS_OVERVIEW, key="sk", key_name="OPENAI_API_KEY",
                               keys={"GEMINI_API_KEY": "g-key"})
            without_key = self.main("daily", None, writer=real, settings=RATINGS_OVERVIEW, key="sk", key_name="OPENAI_API_KEY")
        for result in (failed, without_key):
            self.assertEqual(sorted(result["rankings"]), ["1", "2", "3", "4", "5"])
            self.assertNotIn("overview", result)
            self.assertEqual(result["overview_model"], "gemini-3.1-pro-preview")
        self.assertIn("not published: the request failed: ProviderError: HTTP 503: overloaded", " ".join(lines))
        self.assertIn("Overview: gemini-3.1-pro-preview at medium; not published: no GEMINI_API_KEY", lines)

    def test_changing_the_overview_model_writes_at_once_and_an_unchanged_one_is_kept(self):
        modes = []

        def writer(facts, model, effort, mode, *rest):
            modes.append(mode)
            return {"lede_items": [], "notes": {}, "league_blurbs": [], "league_order": [], "_dropped": 0, "rankings": {"1": {"score": 50}},
                    "ranking_coverage": {"rated": 1, "total": 1, "carried": 0}}, model
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        without = dict(BY_RATINGS, version=1, date="2026-10-07", generated_at=fresh)
        with_it = dict(without, overview_model="gemini-3.1-pro-preview", overview_effort="medium")
        run = dict(writer=writer, key="sk", key_name="OPENAI_API_KEY", settings=RATINGS_OVERVIEW)
        with patch.object(story, "summary", lambda *_: None):
            self.assertEqual(self.main("keep", with_it, **run), with_it)                # nothing changed: no call
            self.main("keep", without, **run)                                           # the push that adds the overview
            self.main("keep", dict(with_it, overview_effort="high"), **run)             # or changes its effort
            # Taking the overview away is a change too: the next story has none.
            result = self.main("keep", with_it, writer=writer, key="sk", key_name="OPENAI_API_KEY", settings=RATINGS)
        self.assertEqual(modes, ["ratings"] * 3)
        self.assertNotIn("overview", result)
        self.assertNotIn("overview_model", result)

    def test_claude_writes_it_with_web_search_and_fetch(self):
        page = "https://news.example/a"
        message = SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn", usage=SimpleNamespace(
            input_tokens=5000, output_tokens=400, cache_creation_input_tokens=0, cache_read_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=2, web_fetch_requests=0)), content=[
            SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "arsenal leeds"}),
            SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(type="web_search_result", url=page, title="A")]),
            SimpleNamespace(type="text", text=json.dumps({"overview": "Arsenal host Leeds.", "sources": [page]}), citations=None)])
        sent = []

        def stream(**kwargs):
            sent.append(kwargs)
            return contextlib.nullcontext(SimpleNamespace(get_final_message=lambda: message))
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
        facts = dict(RUN_FACTS)
        reply = story.overview_request("claude-opus-5-5", "medium", None, facts, client=client)
        self.assertEqual(sent[0]["system"], story.OVERVIEW_SYSTEM)
        self.assertEqual(sent[0]["messages"][0]["content"], story.overview_prompt(story.overview_header(facts), story.overview_view(facts)))
        self.assertEqual([(t["type"], t["max_uses"]) for t in sent[0]["tools"]],
                         [("web_search_20260209", story.OVERVIEW_SEARCHES), ("web_fetch_20260209", story.OVERVIEW_READS)])
        self.assertEqual(self.check(reply), ({"text": "Arsenal host Leeds.", "sources": [{"url": page, "title": "A"}]}, ""))
        # Opus 5.5: 5,000 input at $4/M, 400 output at $20/M and two searches at a cent.
        self.assertAlmostEqual(providers.cost("claude-opus-5-5", reply.usage), 0.02 + 0.008 + 0.02)

    main = Modes.main



RATINGS_BLURBS = RATINGS + 'blurbs_model = "gpt-6.1-sol"\nblurbs_effort = "medium"\n'


def frame_fixture(mid, league, hours=3):
    return dict(fixture(mid, hours, league), league_id=league)


class TopPickBlurbs(unittest.TestCase):
    """Blurbs for the top picks: which matches get one, what of an answer may reach the cards, and how
    the daily run asks for them."""
    main = Modes.main

    def facts(self, outlook, blend=None, leagues=("eng.1", "esp.1", "ita.1")):
        frame = [frame_fixture(mid, league, hours) for mid, league, hours in
                 (("a", "eng.1", 3), ("b", "esp.1", 4), ("c", "ita.1", 5), ("d", "eng.1", 6), ("e", "esp.1", 7))]
        return {"leagues": [{"league_id": lg} for lg in leagues], "overview_fixtures": frame,
                "pick_inputs": {"blend": blend or {"ai": 50, "outlook": 50, "interest": 80, "league_priority": 20}, "outlook": outlook}}

    def test_candidates_by_the_pages_own_pick_score(self):
        # Priorities by the page's league order: eng.1 100, esp.1 50, ita.1 0.
        rankings = {"a": {"score": 40}, "b": {"score": 80}, "c": {"score": 90}, "d": {"score": 60}}
        facts = self.facts({"a": 60, "b": 60, "c": 80, "e": 70})
        # Interest a 50, b 70, c 85, d 60 (no Outlook score), e 70 (no rating).
        # Pick: a 0.8*50+0.2*100 = 60; b 56+10 = 66; c 68+0 = 68; d 48+20 = 68; e 56+10 = 66.
        ids = lambda **kw: [m["id"] for m in story.pick_candidates(facts, rankings, **kw)]
        self.assertEqual(ids(), ["c", "d", "b", "e", "a"])                  # ties go by kickoff
        self.assertEqual(ids(count=2), ["c", "d"])
        # The blend decides: with interest alone, league priority doesn't count.
        self.assertEqual([m["id"] for m in story.pick_candidates(
            self.facts({"a": 60, "b": 60, "c": 80, "e": 70}, blend={"ai": 50, "outlook": 50, "interest": 1, "league_priority": 0}), rankings)],
            ["c", "b", "e", "d", "a"])
        # A match with neither score can't be ranked.
        self.assertEqual([m["id"] for m in story.pick_candidates(self.facts({}), {"a": {"score": 50}})], ["a"])
        self.assertEqual(story.pick_candidates({}, {}), [])

    def test_only_blurbs_its_search_supports_reach_the_cards(self):
        page, other = "https://news.example/a", "https://news.example/b"
        text = json.dumps({"blurbs": [
            {"match_id": "a", "blurb": "Saka [1] returns ([bbc](https://bbc.com/x)) for Arsenal.", "sources": [page + "?utm_source=openai"]},
            {"blurb": "B" * 250 + ". " + "C" * 50 + ".", "match_id": "b", "sources": ["https://made.up/x"]},
            {"match_id": "c", "blurb": "Unsupported.", "sources": ["https://made.up/y"]},
            {"match_id": "a", "blurb": "A second try.", "sources": [page]},
            {"match_id": "zz", "blurb": "Not asked about.", "sources": [page]}]})
        b_at = text.index("BBB")
        reply = providers.Reply(text=text, stop="end", returned={story.link_key(page): (page, "A"), story.link_key(other): (other, "B")},
                                native=[(b_at, b_at + 10, other)])
        blurbs, why = story.checked_blurbs(reply, {"a", "b", "c", "d"}, resolve=lambda u: None, check=live, workers=2)
        self.assertEqual(blurbs, {
            "a": {"blurb": "Saka returns for Arsenal.", "sources": [{"url": page + "?utm_source=openai", "title": "A"}]},
            # The made-up page goes, but the provider's own citation inside the blurb stands; cut at a sentence.
            "b": {"blurb": "B" * 250 + ".", "sources": [{"url": other, "title": "B"}]}})
        self.assertEqual(why, "1 not written, 1 without a page its search returned that still loads")
        gone, why = story.checked_blurbs(reply, {"a"}, resolve=lambda u: None, check=lambda u: {"state": "dead", "status": 404})
        self.assertEqual((gone, why), ({}, "1 without a page its search returned that still loads"))
        self.assertEqual(story.checked_blurbs(providers.Reply(text="Sorry.", stop="refusal"), {"a"}),
                         ({}, "its reply held no blurbs (it stopped: refusal)"))

    def test_the_daily_run_researches_the_top_picks_after_the_ratings(self):
        calls, lines, real = [], [], story.write_story
        rate = openai_ratings(calls)
        page = "https://news.example/one"

        def post(url, body, headers, **kw):
            if "tools" not in body:
                return rate(url, body, headers, **kw)
            calls.append((url, body, headers))
            asked = json.loads(body["input"].split("Fixtures:\n", 1)[1].split("\n\nYou have up to", 1)[0])
            answer = json.dumps({"blurbs": [{"match_id": f["id"], "blurb": f"News for {f['id']}.", "sources": [page + "?utm_source=openai"]}
                                            for f in asked]})
            return {"model": "gpt-6.1-sol", "status": "completed", "output": [
                {"type": "web_search_call", "action": {"type": "search", "query": "q", "sources": [{"type": "url", "url": page}]}},
                {"type": "message", "content": [{"type": "output_text", "text": answer, "annotations": []}]}],
                "usage": {"input_tokens": 30000, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1000,
                          "output_tokens_details": {"reasoning_tokens": 500}}}
        facts = dict(RUN_FACTS, overview_fixtures=[fixture("1", 3), fixture("2", 30, "esp.1")],
                     pick_inputs={"blend": {"ai": 50, "outlook": 50, "interest": 80, "league_priority": 20}, "outlook": {"1": 40, "2": 90}})
        with patch.object(providers, "post_json", post), patch.object(story, "summary", lines.append), \
                patch.object(story, "check_link", live), patch.dict(RUN_FACTS, facts):
            result = self.main("daily", None, writer=real, settings=RATINGS_BLURBS, key="sk", key_name="OPENAI_API_KEY")
        ratings_call, (b_url, b_body, _) = calls
        self.assertNotIn("tools", ratings_call[1])                      # the ratings first, then the research
        self.assertEqual((b_body["instructions"], b_body["reasoning"], b_body["tools"]), (story.RESEARCH_SYSTEM, {"effort": "medium"}, [{"type": "web_search"}]))
        # Both rated 61.5; 2 has the better Outlook score (90 v 40) and 1 the better league (eng.1 100 v esp.1 50):
        # 1 is 0.8 x 50.75 + 20 = 60.6, 2 is 0.8 x 75.75 + 10 = 70.6.
        self.assertEqual(b_body["input"], story.research_prompt(story.overview_header(dict(facts, date="2026-10-07")),
                                                                [story.rating_fixture(fixture("2", 30, "esp.1")), story.rating_fixture(fixture("1", 3))]))
        for mid in ("1", "2"):
            self.assertEqual((result["rankings"][mid]["blurb"], result["rankings"][mid]["sources"]),
                             (f"News for {mid}.", [{"url": page + "?utm_source=openai", "title": ""}]))
        self.assertNotIn("blurb", result["rankings"]["3"])
        self.assertEqual((result["blurbs_model"], result["blurbs_effort"], result["match_blurb_coverage"]),
                         ("gpt-6.1-sol", "medium", {"written": 2, "total": 5}))
        # 30,000 input at $2/M, 1,000 output at $10/M and one search call at a cent.
        self.assertIn("about $0.0800; 2 of 2 top picks", next(line for line in lines if line.startswith("Blurbs:")))

    def test_a_failed_request_keeps_the_ratings_and_a_changed_choice_rewrites(self):
        calls, lines, real = [], [], story.write_story
        rate = openai_ratings(calls)

        def post(url, body, headers, **kw):
            if "tools" in body:
                raise providers.ProviderError("HTTP 500: overloaded")
            return rate(url, body, headers, **kw)
        with patch.object(providers, "post_json", post), patch.object(story, "summary", lines.append):
            result = self.main("daily", None, writer=real, settings=RATINGS_BLURBS, key="sk", key_name="OPENAI_API_KEY")
        self.assertEqual(sorted(result["rankings"]), ["1", "2", "3", "4", "5"])
        self.assertFalse(any("blurb" in r for r in result["rankings"].values()))
        self.assertIn("(the request failed: ProviderError: HTTP 500: overloaded)", next(line for line in lines if line.startswith("Blurbs:")))
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        mine = dict(BY_RATINGS, version=1, date="2026-10-07", generated_at=fresh)
        modes = []

        def writer(facts, model, effort, mode, *rest):
            modes.append(mode)
            return None, model
        with patch.object(story, "summary", lambda *_: None):
            self.main("keep", mine, writer=writer, key="sk", key_name="OPENAI_API_KEY", settings=RATINGS_BLURBS)
            kept = dict(mine, blurbs_model="gpt-6.1-sol", blurbs_effort="medium")
            self.assertEqual(self.main("keep", kept, writer=writer, key="sk", key_name="OPENAI_API_KEY", settings=RATINGS_BLURBS), kept)
        self.assertEqual(modes, ["ratings"])


class RollingFacts(unittest.TestCase):
    def test_boundary_and_all_competitions_are_available_to_claude(self):
        from tests.page_fixture import render_page
        fixtures = [
            ("live", "2026-10-07T16:30:00+00:00", "in", "ESPN+", "eng.1"),
            ("inside", "2026-10-08T16:59:00+00:00", "pre", "Peacock", "usa.1"),
            ("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+", "esp.1"),
            ("finished", "2026-10-07T16:00:00+00:00", "post", "ESPN+", "eng.1"),
            ("unlisted", "2026-10-07T18:00:00+00:00", "pre", None, "fifa.friendly.w"),
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
        self.assertTrue(all(m["available_service_ids"] for m in facts["next_24_hours"] + facts["later_if_needed"]))
        # Ratings cover the full slate, including unknown broadcasts, but no finished matches.
        self.assertEqual({m["id"] for m in facts["ranking_candidates"]}, {"live", "inside", "edge", "unlisted", "hidden"})
        candidates = {group["league_id"]: group["matches"] for group in facts["league_candidates"]}
        self.assertEqual(candidates["eng.1"][0]["id"], "live")
        self.assertNotIn("fifa.friendly.w", candidates)
        self.assertNotIn("schedule_by_day", facts)

    def test_the_overview_gets_the_top_threes_time_frame_on_every_service(self):
        from tests.page_fixture import render_page

        def overview(fixtures):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "facts.json"
                render_page(build, fixtures=fixtures, facts_path=path)
                facts = json.loads(path.read_text())
            ids = [m["id"] for m in facts["overview_fixtures"]]
            # What story.py needs to rank them as the page does: the blend, and each one's Outlook score.
            self.assertEqual(sorted(facts["pick_inputs"]["outlook"]), sorted(ids))
            self.assertEqual(facts["pick_inputs"]["blend"], {"ai": 50, "outlook": 50, "interest": 80, "league_priority": 20})
            self.assertTrue(all(isinstance(v, float) for v in facts["pick_inputs"]["outlook"].values()))
            return ids
        hidden = next(lg for lg, info in build.LEAGUES.items() if info.get("default_off"))
        # Built at 17:00 UTC on 7 October: the next 24 hours run to 17:00 on the 8th. Coverage known on any
        # service counts, in any competition; a match with none listed, or finished, doesn't.
        near = [("live", "2026-10-07T16:30:00+00:00", "in", "ESPN+", "eng.1"),
                ("hidden", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", hidden),
                ("unlisted", "2026-10-07T18:00:00+00:00", "pre", None, "fifa.friendly.w"),
                ("finished", "2026-10-07T16:00:00+00:00", "post", "ESPN+", "eng.1"),
                ("inside", "2026-10-08T16:59:00+00:00", "pre", "Peacock", "usa.1")]
        later = [("edge", "2026-10-08T17:00:00+00:00", "pre", "ESPN+", "esp.1")]
        self.assertEqual(overview(near + later), ["live", "hidden", "inside"])
        # Fewer than three: the 24 hours from the next kickoff join, and no more once there are three.
        sparse = [("one", "2026-10-07T20:00:00+00:00", "pre", "ESPN+", "eng.1"),
                  ("a", "2026-10-09T12:00:00+00:00", "pre", "ESPN+", "esp.1"),
                  ("b", "2026-10-10T11:59:00+00:00", "pre", "Peacock", "eng.1"),
                  ("c", "2026-10-10T12:00:00+00:00", "pre", "ESPN+", "ita.1")]
        self.assertEqual(overview(sparse), ["one", "a", "b"])
        # An international break: nothing in the next 24 hours, two in the next span, so the one after joins too.
        empty = [("a", "2026-10-09T12:00:00+00:00", "pre", "ESPN+", "esp.1"), ("b", "2026-10-09T13:00:00+00:00", "pre", "ESPN+", "esp.1"),
                 ("c", "2026-10-11T12:00:00+00:00", "pre", "ESPN+", "ita.1"), ("d", "2026-10-11T13:00:00+00:00", "pre", "ESPN+", "ita.1")]
        self.assertEqual(overview(empty), ["a", "b", "c", "d"])
        # Thirty in the first later span: every one, where later_if_needed stops at twenty.
        busy = [(str(i), f"2026-10-09T{12 + i // 6:02d}:{i % 6 * 10:02d}:00+00:00", "pre", "ESPN+", "esp.1") for i in range(30)]
        self.assertEqual(overview(busy), [str(i) for i in range(30)])

    def test_later_leagues_are_not_lost_when_other_leagues_fill_the_digest(self):
        from tests.page_fixture import render_page
        fixtures = [(str(i), "2026-10-09T18:00:00+00:00", "pre", "ESPN+", "esp.1") for i in range(21)]
        fixtures.append(("later-league", "2026-10-11T18:00:00+00:00", "pre", "ESPN+", "eng.1"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            render_page(build, fixtures=fixtures, facts_path=path)
            facts = json.loads(path.read_text())
        self.assertNotIn("later-league", {m["id"] for m in facts["later_if_needed"]})
        candidates = {group["league_id"]: group["matches"] for group in facts["league_candidates"]}
        self.assertEqual(candidates["eng.1"][0]["id"], "later-league")
        self.assertEqual(len(candidates["esp.1"]), 21)


class Rankings(unittest.TestCase):
    @staticmethod
    def rating(mid="1", **over):
        return dict(match_id=mid, popularity=80, gameplay=70, impact=90, **over)

    def test_fixed_weighted_score_is_independent_of_the_other_candidates(self):
        raw = [self.rating(), self.rating("2")]
        ratings = story.clean_rankings(raw, {"1", "2"}, {}, NO_LINKS)
        self.assertEqual(ratings["1"]["score"], 80.5)
        self.assertEqual(story.clean_rankings(raw[:1], {"1", "2"}, {}, NO_LINKS)["1"], ratings["1"])

    def test_unknown_duplicate_and_invalid_ratings_cannot_change_valid_scores(self):
        good = self.rating()
        for bad in (None, {}, self.rating("missing"), dict(good, match_id=1),
                    dict(good, popularity=True), dict(good, gameplay=101),
                    dict(good, impact=-1), dict(good, impact="90"), dict(good, popularity=80.0)):
            with self.subTest(bad=bad):
                result = story.clean_rankings([bad, good, dict(good, popularity=1)], {"1"}, {}, NO_LINKS)
                self.assertEqual(result, {"1": {"popularity": 80, "gameplay": 70, "impact": 90, "score": 80.5}})

    def test_match_blurbs_fit_the_card_and_rest_on_their_own_fixture(self):
        links = {"1": LINK("1"), "2": LINK("2")}
        result = story.clean_rankings([self.rating(blurb="The leaders face a side unbeaten in five.", sources=[])], {"1"}, {}, links)
        self.assertEqual(result["1"]["sources"], [story.facts_source(LINK("1"))])
        long_blurb = "First sentence about the matchup. " * 4 + "A second sentence that runs on and on " * 6
        saved = story.clean_rankings([self.rating(blurb=long_blurb, sources=[])], {"1"}, {}, links)["1"]["blurb"]
        self.assertLessEqual(len(saved), 260)
        self.assertTrue(saved.endswith("matchup."))
        for bad in (dict(self.rating(), blurb="x", sources=[LINK("2")]), dict(self.rating(), blurb=" ", sources=[]),
                    dict(self.rating(), blurb="Unread.", sources=["https://unread.example/a"])):
            with self.subTest(bad=bad):
                saved = story.clean_rankings([bad], {"1"}, {}, links)["1"]
                self.assertNotIn("blurb", saved)
                self.assertEqual(saved["score"], 80.5)

    def test_league_order_preserves_only_known_unique_ids(self):
        result = story.clean_story(raw_story(league_order=['esp.1', 'fake', None, 'eng.1', 'esp.1']),
                                   dict(FACTS, leagues=[{'league_id': 'eng.1'}, {'league_id': 'esp.1'}]), SEEN)
        self.assertEqual(result['league_order'], ['esp.1', 'eng.1'])


class LeagueBlurbs(unittest.TestCase):
    FACTS = dict(FACTS, league_candidates=[
        {"league_id": "eng.1", "matches": [{"id": "1", "source_url": SOURCE}]},
        {"league_id": "esp.1", "matches": [{"id": "later", "source_url": SOURCE}]},
    ])

    def test_keeps_one_scored_paragraph_per_league_including_later(self):
        raw = raw_story(league_blurbs=[dict(item(), league_id="eng.1", interest=50),
                                      dict(item(ids=["later"]), league_id="esp.1", interest=90)])
        # Supplied ESPN facts can support useful context without an unrelated web article.
        result = story.clean_story(raw, self.FACTS, {})
        self.assertEqual(result["blurb_coverage"], {"written": 2, "total": 2})
        self.assertEqual([b["interest"] for b in result["league_blurbs"]], [50, 90])
        self.assertEqual(result["league_blurbs"][1]["match_ids"], ["later"])
        self.assertEqual(result["league_blurbs"][1]["sources"], [story.facts_source(SOURCE)])

    def test_rejects_mislabeled_unsourced_oversized_and_invalid_interest(self):
        good = dict(item(), league_id="eng.1", interest=50)
        for bad in (dict(good, league_id="esp.1"), dict(good, league_id="missing"),
                    dict(good, interest=True), dict(good, interest=101), dict(good, interest=-1),
                    dict(good, interest="90"), dict(good, sources=["https://unseen.example/a"]),
                    dict(item("A" * 451), league_id="eng.1", interest=50)):
            with self.subTest(bad=bad):
                result = story.clean_story(raw_story(league_blurbs=[bad, good, dict(good, interest=99)]), self.FACTS, SEEN)
                self.assertEqual(len(result["league_blurbs"]), 1)
                self.assertEqual(result["league_blurbs"][0]["interest"], 50)

    def test_refresh_keeps_blurb_sources(self):
        self.assertIn(story.url_key(SOURCE), story.earlier_sources({"league_blurbs": [{"sources": [{"url": SOURCE}]}]}))



def scores(fixtures):
    return [{"match_id": f["id"], "popularity": 50, "gameplay": 60, "impact": 70} for f in fixtures]


class RatingsMode(unittest.TestCase):
    """Ratings mode: Claude's three scores for the next three days, and nothing else."""
    BUILT = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)

    def facts(self, hours):
        fixture = lambda i, h: {"id": f"m{i}", "kickoff_utc": (self.BUILT + timedelta(hours=h)).isoformat(),
                                "competition": "Premier League", "home": {"name": f"Home {i}"}, "away": {"name": f"Away {i}"}}
        return dict(RUN_FACTS, built_at="2026-10-08T12:00:00Z", ranking_candidates=[fixture(i, h) for i, h in enumerate(hours)])

    def test_the_window_is_three_days_widened_a_day_at_a_time(self):
        facts = self.facts([10, 30, 80, 100, 150])
        for least, ids, hours in ((2, ["m0", "m1"], 72),                      # enough within three days
                                  (3, ["m0", "m1", "m2"], 96),                # one more day reaches 80 hours out
                                  (4, ["m0", "m1", "m2", "m3"], 120),
                                  (10, ["m0", "m1", "m2", "m3", "m4"], 168)):  # widened until nothing is left out
            with patch.object(story, "MIN_RATED", least):
                window, got = story.rating_window(facts)
            self.assertEqual(([m["id"] for m in window], got), (ids, hours), least)
        facts["ranking_candidates"].append({"id": "odd", "kickoff_utc": "soon"})
        with patch.object(story, "MIN_RATED", 2):
            self.assertIn("odd", [m["id"] for m in story.rating_window(facts)[0]])   # kept, not lost

    def test_scores_only_no_research_and_no_text(self):
        sdk = FakeSDK([], rate=scores)
        with patch.object(story, "MIN_RATED", 1):
            result, served = run(sdk, mode="ratings", facts=self.facts([10, 30, 80]))
        self.assertEqual((sdk.research_calls(), len(sdk.rating_calls()), served), ([], 1, "test-model"))
        call = sdk.rating_calls()[0]
        self.assertEqual(call["system"], story.SCORES_SYSTEM)
        self.assertEqual(call["output_config"]["format"]["schema"], story.SCORES_SCHEMA)
        self.assertNotIn("tools", call)
        self.assertNotIn("Reporting from this run's research", call["messages"][0]["content"])
        self.assertEqual([f["id"] for f in FakeSDK.fixtures(call)], ["m0", "m1"])     # 80 hours out is beyond the window
        # 25% of 50, 35% of 60 and 40% of 70: 12.5 + 21 + 28.
        self.assertEqual(result["rankings"]["m0"], {"popularity": 50, "gameplay": 60, "impact": 70, "score": 61.5})
        self.assertEqual((result["lede_items"], result["league_blurbs"], result["notes"]), ([], [], {}))
        self.assertEqual((result["ranking_coverage"], result["window_hours"]), ({"rated": 2, "total": 2, "carried": 0}, 72))

    def test_a_missing_score_is_asked_for_once_more(self):
        asked = []

        def rate(fixtures):
            asked.append([f["id"] for f in fixtures])
            return scores(fixtures[1:] if len(asked) == 1 else fixtures)       # the first answer leaves one out
        with patch.object(story, "MIN_RATED", 1):
            result, _ = run(FakeSDK([], rate=rate), mode="ratings", facts=self.facts([10, 20, 30]))
        self.assertEqual(asked, [["m0", "m1", "m2"], ["m0"]])
        self.assertEqual(sorted(result["rankings"]), ["m0", "m1", "m2"])

    def test_haiku_requests_carry_no_fallback(self):
        for model, fallback in (("claude-haiku-5-5", False), ("claude-opus-5-5", True)):
            sdk = FakeSDK([], rate=scores)
            with patch.dict(sys.modules, {"anthropic": sdk.module}), patch.object(story, "log", lambda *_: None), \
                    patch.object(story, "MIN_RATED", 1):
                story.write_story(self.facts([10]), model, "low", "ratings", None, totals())
            call = sdk.rating_calls()[0]
            self.assertEqual((call["model"], "fallbacks" in call, "betas" in call), (model, fallback, fallback))

    def test_haiku_takes_part_in_ratings_mode_only(self):
        models = []

        def writer(facts, model, *rest):
            models.append(model)
            return None, model
        main = Modes.main      # the helper that runs story.main() with its own settings file
        for mode in ("full", "ratings"):
            main(self, mode, None, writer=writer, extra=["--model", "claude-haiku-5-5"])
        self.assertEqual(models, [story.DEFAULT_MODEL, "claude-haiku-5-5"])

    def test_prices_per_million_tokens(self):
        million = {"in": 1_000_000, "cache_write": 0, "cache_read": 1_000_000, "out": 1_000_000, "searches": 0, "fetches": 0,
                   "prompt_max": 50_000}
        self.assertAlmostEqual(story.cost_of(million, "claude-sonnet-5-5"), 2 + 0.10 + 10)        # cache reads 0.05x input
        self.assertAlmostEqual(story.cost_of(dict(million, searches=1), "claude-opus-5-5"), 4 + 0.20 + 20 + 0.01)
        self.assertAlmostEqual(story.cost_of(million, "claude-haiku-5-5"), 0.10 + 0.01 + 0.50)
        self.assertAlmostEqual(story.cost_of(dict(million, prompt_max=100_000), "claude-haiku-5-5"), 0.10 + 0.01 + 0.50)
        self.assertAlmostEqual(story.cost_of(dict(million, prompt_max=100_001), "claude-haiku-5-5"), 0.50 + 0.05 + 2.50)


if __name__ == "__main__":
    unittest.main()
