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
    return dict.fromkeys(("in", "out", "cache_write", "cache_read", "searches", "fetches"), 0)


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
        self.assertEqual(spent, {"in": 40, "out": 20, "cache_write": 8, "cache_read": 12, "searches": 0, "fetches": 0})

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

    def main(self, mode, previous, key="test-key", writer=None):
        with tempfile.TemporaryDirectory() as tmp:
            facts_path, prev_path, out = Path(tmp) / "facts.json", Path(tmp) / "prev.json", Path(tmp) / "story.json"
            facts_path.write_text(json.dumps(dict(RUN_FACTS, date="2026-10-07")))
            args = ["story.py", "--facts", str(facts_path), "--out", str(out), "--mode", mode]
            if previous is not None:
                prev_path.write_text(json.dumps(previous))
                args += ["--previous", str(prev_path)]
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            if key:
                env["ANTHROPIC_API_KEY"] = key
            writer = writer or (lambda *a: self.fail("no API call expected"))
            with patch.object(sys, "argv", args), patch.dict(os.environ, env, clear=True), \
                    patch.object(story, "write_story", writer), patch.object(story, "log", lambda *_: None):
                self.assertEqual(story.main(), 0)
            return json.loads(out.read_text()) if out.exists() else None

    def test_keep_and_fallbacks_never_lose_the_published_story(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = {"version": 1, "date": "2026-10-07", "generated_at": fresh, "lede": "Today's."}
        yesterday = {"version": 1, "date": "2026-10-06", "generated_at": "2026-10-06T22:00:00Z", "lede": "Yesterday's."}
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
        self.assertEqual({k: result[k] for k in ("version", "date", "kind", "model", "effort", "services", "focus_until")},
                         {"version": 1, "date": "2026-10-07", "kind": "full", "model": "test-model", "effort": "medium",
                          "services": ["espn"], "focus_until": "2026-10-08T17:00:00+00:00"})
        self.assertNotIn("_dropped", result)


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


if __name__ == "__main__":
    unittest.main()
