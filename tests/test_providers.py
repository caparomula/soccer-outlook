"""The provider comparison: that every provider is asked the same thing, that each API's reply is read
as its documentation describes it, and that what the report says (costs, citations, agreement, what
was skipped) is right, worked out by hand. Run with: python3 -m unittest -v
"""
import contextlib
import copy
import http.server
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import providers  # noqa: E402
import story  # noqa: E402

_spec = importlib.util.spec_from_file_location("compare_providers", ROOT / ".github" / "scripts" / "compare-providers.py")
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)

HEADER = "It is 2:12 am on Thursday, 2026-10-08, US Eastern time."
FIXTURES = [{"id": "1", "kickoff": "Sat Oct 10, 7:30 AM ET", "competition": "Premier League",
             "home": {"name": "Arsenal", "table": "2nd of 20, 12 pts"}, "away": {"name": "Leeds United"}},
            {"id": "2", "kickoff": "Sat Oct 10, 3:00 PM ET", "competition": "La Liga",
             "home": {"name": "Real Madrid"}, "away": {"name": "Villarreal"}}]
LUNA, FLASH = compare.Config("openai", "gpt-6-luna", "low"), compare.Config("google", "gemini-3.8-flash", "low")
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def zero_usage(**over):
    return dict(dict.fromkeys(compare.USAGE_KEYS, 0), **over)


def claude_usage(**over):
    values = dict(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                  server_tool_use=SimpleNamespace(web_search_requests=0, web_fetch_requests=0))
    values.update(over)
    return SimpleNamespace(**values)


class FakeClaude:
    """Stands in for anthropic.Anthropic(): each stream() call gets the next message and is recorded
    with a copy of its messages, which the caller goes on appending to."""

    def __init__(self, *messages):
        self.messages, self.calls = list(messages), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kwargs):
        self.calls.append(dict(kwargs, messages=list(kwargs["messages"])))
        message = self.messages.pop(0)
        return contextlib.nullcontext(SimpleNamespace(get_final_message=lambda: message))


class Recorder:
    """Stands in for post_json: records (url, body, headers) and answers with the next reply."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, body, headers, **_):
        self.calls.append((url, copy.deepcopy(body), headers))
        return self.replies.pop(0)


class Configs(unittest.TestCase):
    def test_every_entry_that_cannot_run_is_named_at_once(self):
        with self.assertRaises(ValueError) as caught:
            compare.parse_configs("openai:gpt-6-luna x:gpt:low openai:gpt-9:low google:gemini-3.8-flash:max "
                                  "anthropic:claude-haiku-5-5:low google:gemini-3.1-flash-lite:low "
                                  "openai:gpt-5.5:medium openai:gpt-5.5:medium", "research")
        message = str(caught.exception)
        for part in ("openai:gpt-6-luna: expected provider:model:effort", "x:gpt:low: provider must be one of",
                     "openai:gpt-9:low: no list price", "google:gemini-3.8-flash:max: google effort must be one of",
                     "claude-haiku-5-5 can't search the web", "gemini-3.1-flash-lite can't search the web",
                     "openai:gpt-5.5:medium: listed twice"):
            self.assertIn(part, message)

    def test_ratings_take_the_models_that_cannot_search(self):
        configs = compare.parse_configs("anthropic:claude-haiku-5-5:low  google:gemini-3.1-flash-lite:minimal", "ratings")
        self.assertEqual(configs, [compare.Config("anthropic", "claude-haiku-5-5", "low"),
                                   compare.Config("google", "gemini-3.1-flash-lite", "minimal")])
        self.assertEqual(compare.parse_configs("", "ratings"), [])


class SameQuestion(unittest.TestCase):
    """Every provider is asked what story.py asks Claude, word for word."""

    def test_ratings_prompt_is_the_one_story_py_sends_claude(self):
        reply = SimpleNamespace(model="claude-haiku-5-5", usage=claude_usage(), stop_reason="end_turn",
                                content=[SimpleNamespace(type="text", text='{"ratings": []}')])
        client = FakeClaude(reply)
        story.rate_chunk(client, "claude-haiku-5-5", "low", FIXTURES, [], HEADER, scores_only=True)
        sent = client.calls[0]
        self.assertEqual(sent["messages"][0]["content"], story.rating_prompt(HEADER, FIXTURES, scores_only=True))
        self.assertEqual(sent["system"], story.SCORES_SYSTEM)

    def test_openai_and_google_ratings_carry_the_same_text_and_schema(self):
        post = Recorder({"status": "completed", "output": [], "usage": {}},
                        {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]})
        schema = copy.deepcopy(story.SCORES_SCHEMA)
        keys = {"openai": "sk-test", "google": "g-test"}
        with patch.object(providers, "post_json", post):
            compare.rate_once(LUNA, HEADER, FIXTURES, keys, {})
            compare.rate_once(FLASH, HEADER, FIXTURES, keys, {})
        (o_url, o_body, o_headers), (g_url, g_body, g_headers) = post.calls
        prompt = story.rating_prompt(HEADER, FIXTURES, scores_only=True)
        self.assertEqual(o_url, "https://api.openai.com/v1/responses")
        self.assertEqual(o_headers, {"Authorization": "Bearer sk-test"})
        self.assertEqual(o_body, {"model": "gpt-6-luna", "instructions": story.SCORES_SYSTEM, "input": prompt,
                                  "reasoning": {"effort": "low"}, "max_output_tokens": story.RATING_MAX_TOKENS, "store": False,
                                  "text": {"format": {"type": "json_schema", "name": "answer", "schema": story.SCORES_SCHEMA,
                                                      "strict": True}}})
        # The key travels in a header, never in the URL.
        self.assertEqual(g_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent")
        self.assertEqual(g_headers, {"x-goog-api-key": "g-test"})
        self.assertEqual(g_body["systemInstruction"], {"parts": [{"text": story.SCORES_SYSTEM}]})
        self.assertEqual(g_body["contents"], [{"role": "user", "parts": [{"text": prompt}]}])
        config = g_body["generationConfig"]
        self.assertEqual((config["responseMimeType"], config["thinkingConfig"], config["maxOutputTokens"]),
                         ("application/json", {"thinkingLevel": "low"}, story.RATING_MAX_TOKENS))
        self.assertNotIn("tools", g_body)
        self.assertNotIn("additionalProperties", json.dumps(config["responseJsonSchema"]))
        item = config["responseJsonSchema"]["properties"]["ratings"]["items"]
        self.assertEqual(item["required"], ["match_id", "popularity", "gameplay", "impact"])
        self.assertEqual(story.SCORES_SCHEMA, schema)          # stripped from a copy, not from story.py's own

    def test_research_carries_the_same_text_and_each_providers_search(self):
        post = Recorder({"status": "completed", "output": [], "usage": {}},
                        {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}]})
        claude = FakeClaude(SimpleNamespace(model="claude-opus-5-5", usage=claude_usage(), stop_reason="end_turn",
                                            content=[SimpleNamespace(type="text", text='{"blurbs": []}')]))
        keys = {"openai": "sk", "google": "g"}
        with patch.object(providers, "post_json", post):
            for cfg in (compare.Config("openai", "gpt-5.5", "medium"), compare.Config("google", "gemini-3.1-pro-preview", "medium"),
                        compare.Config("anthropic", "claude-opus-5-5", "medium")):
                compare.run_research(cfg, HEADER, FIXTURES, keys, {"anthropic": claude})
        (_, o_body, _), (_, g_body, _) = post.calls
        c_body = claude.calls[0]
        prompt = compare.research_prompt(HEADER, FIXTURES)
        self.assertEqual((o_body["instructions"], o_body["input"]), (compare.RESEARCH_SYSTEM, prompt))
        self.assertEqual((g_body["systemInstruction"]["parts"][0]["text"], g_body["contents"][0]["parts"][0]["text"]),
                         (compare.RESEARCH_SYSTEM, prompt))
        self.assertEqual((c_body["system"], c_body["messages"][0]["content"]), (compare.RESEARCH_SYSTEM, prompt))
        self.assertEqual((o_body["tools"], o_body["include"], o_body["max_tool_calls"], o_body["reasoning"]),
                         ([{"type": "web_search"}], ["web_search_call.action.sources"], 12, {"effort": "medium"}))
        self.assertNotIn("text", o_body)
        self.assertEqual(g_body["tools"], [{"google_search": {}}])
        self.assertEqual(g_body["generationConfig"], {"thinkingConfig": {"thinkingLevel": "medium"}, "maxOutputTokens": 32000})
        self.assertEqual([(t["type"], t["max_uses"]) for t in c_body["tools"]], [("web_search_20260209", 8), ("web_fetch_20260209", 4)])
        self.assertEqual(c_body["output_config"], {"effort": "medium"})
        self.assertIn("at most 260 characters", prompt)
        self.assertIn('[{"id":"1","kickoff":"Sat Oct 10, 7:30 AM ET"', prompt)          # the facts the ratings get, too


class OpenAIReplies(unittest.TestCase):
    TEXT = '{"blurbs": [{"match_id": "1", "blurb": "Saka is fit.", "sources": ["https://news.example/d"]}]}'

    def reply(self, **over):
        resp = {"model": "gpt-6-luna-2026-09-01", "status": "completed", "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "web_search_call", "id": "ws_1", "status": "completed", "action": {
                "type": "search", "query": "arsenal leeds team news", "queries": ["arsenal leeds team news", "saka fitness"],
                "sources": [{"type": "url", "url": "https://www.bbc.com/sport/a?utm_source=openai"}, {"type": "url", "url": "https://news.example/b"}]}},
            {"type": "web_search_call", "id": "ws_2", "status": "completed", "action": {"type": "open_page", "url": "https://news.example/c"}},
            {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": self.TEXT, "annotations": [
                    {"type": "url_citation", "start_index": 48, "end_index": 60, "url": "https://news.example/d", "title": "D"}], "logprobs": []}]}],
            "usage": {"input_tokens": 12000, "input_tokens_details": {"cached_tokens": 2000}, "output_tokens": 900,
                      "output_tokens_details": {"reasoning_tokens": 600}, "total_tokens": 12900}}
        resp.update(over)
        return providers.openai_reply(resp, compare.match_key)

    def test_text_citations_searches_and_usage(self):
        reply = self.reply()
        self.assertEqual((reply.text, reply.stop, reply.served), (self.TEXT, "end", "gpt-6-luna-2026-09-01"))
        self.assertEqual(reply.queries, ["arsenal leeds team news", "saka fitness"])
        self.assertEqual(reply.usage, zero_usage(**{"in": 10000, "cached": 2000, "out": 900, "reasoning": 600,
                                                     "searches": 1, "opens": 1, "prompt_max": 12000}))
        self.assertEqual(set(reply.returned), {compare.match_key(u) for u in (
            "https://bbc.com/sport/a", "https://news.example/b", "https://news.example/c", "https://news.example/d")})
        self.assertEqual(reply.native, [(48, 60, "https://news.example/d")])
        # 10,000 fresh and 2,000 cached input, 900 output with reasoning, two calls: $0.001 + $0.00002 + $0.00045 + $0.02.
        self.assertAlmostEqual(compare.cost_of(LUNA, reply.usage), 0.02147)
        blurbs = compare.research_blurbs(reply, {"1"})
        self.assertEqual(blurbs["1"]["native"], ["https://news.example/d"])     # the span 48-60 is inside fixture 1's entry

    def test_cut_short_or_refused_replies_are_not_read_as_ratings(self):
        cut = self.reply(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        self.assertEqual(cut.stop, "max_tokens")
        self.assertEqual(story.parse_ratings('{"ratings": [{"match_id": "1"}]}', cut.stop), [])
        refused = self.reply(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "No."}]}])
        self.assertEqual((refused.stop, refused.text), ("refusal", ""))
        self.assertEqual(self.reply(status="incomplete", incomplete_details={"reason": "content_filter"}).stop, "refusal")
        self.assertEqual(story.parse_ratings("not json", "end"), [])
        self.assertEqual(story.parse_ratings('{"ratings": [{"match_id": "1"}]}', "end"), [{"match_id": "1"}])


class GeminiReplies(unittest.TestCase):
    # Google's segment offsets count bytes: the curly apostrophe and the accent take more than one each.
    PART0 = "Atlético’s run "
    PART1 = '{"blurbs": [{"match_id": "2", "blurb": "Alavés host Atlético’s unbeaten side.", "sources": []}]}'
    SEGMENT = "Alavés host Atlético’s unbeaten side."

    def reply(self, **over):
        start = len(self.PART1[:self.PART1.index(self.SEGMENT)].encode("utf-8"))
        candidate = {"content": {"role": "model", "parts": [{"text": self.PART0}, {"text": self.PART1, "thoughtSignature": "c2ln"}]},
                     "finishReason": "STOP", "groundingMetadata": {
                         "webSearchQueries": ["alaves atletico preview", "", "alaves atletico preview"],
                         "groundingChunks": [{"web": {"uri": REDIRECT + "AAA", "title": "marca.com"}}, {"web": {"uri": REDIRECT + "BBB", "title": "as.com"}}],
                         "groundingSupports": [
                             {"segment": {"partIndex": 1, "startIndex": start, "endIndex": start + len(self.SEGMENT.encode("utf-8")),
                                          "text": self.SEGMENT}, "groundingChunkIndices": [0, 1], "confidenceScores": [0.9, 0.8]},
                             {"segment": {"endIndex": len("Atlético".encode("utf-8")), "text": "Atlético"}, "groundingChunkIndices": [1, 7]}]}}
        candidate.update(over)
        return providers.gemini_reply({"modelVersion": "gemini-3.8-flash", "candidates": [candidate], "usageMetadata": {
            "promptTokenCount": 5000, "cachedContentTokenCount": 1000, "candidatesTokenCount": 700, "thoughtsTokenCount": 300,
            "toolUsePromptTokenCount": 2000, "totalTokenCount": 8000}})

    def test_grounding_spans_are_characters_of_the_joined_text(self):
        reply = self.reply()
        self.assertEqual(reply.text, self.PART0 + self.PART1)
        at = len(self.PART0) + self.PART1.index(self.SEGMENT)
        self.assertEqual(reply.native, [(at, at + len(self.SEGMENT), REDIRECT + "AAA"), (at, at + len(self.SEGMENT), REDIRECT + "BBB"),
                                        (0, len("Atlético"), REDIRECT + "BBB")])        # index 7 names no chunk
        self.assertEqual(reply.text[at:at + len(self.SEGMENT)], self.SEGMENT)
        self.assertEqual(reply.redirects, {REDIRECT + "AAA": "marca.com", REDIRECT + "BBB": "as.com"})
        self.assertEqual(reply.queries, ["alaves atletico preview"])                     # blank and repeated queries aren't billed
        self.assertEqual(compare.research_blurbs(reply, {"2"})["2"]["native"], [REDIRECT + "AAA", REDIRECT + "BBB"])

    def test_usage_bills_thinking_as_output_and_search_queries(self):
        usage = self.reply().usage
        self.assertEqual(usage, zero_usage(**{"in": 6000, "cached": 1000, "out": 1000, "reasoning": 300, "searches": 1, "prompt_max": 7000}))
        # 6,000 input at $0.75/M, 1,000 cached at $0.075/M, 1,000 output at $3.75/M, one query at $14 per 1,000.
        self.assertAlmostEqual(compare.cost_of(FLASH, usage), 0.0045 + 0.000075 + 0.00375 + 0.014)
        pro = compare.Config("google", "gemini-3.1-pro-preview", "low")
        self.assertAlmostEqual(compare.cost_of(pro, usage), 6000 * 2e-6 + 1000 * 0.2e-6 + 1000 * 12e-6 + 0.014)
        long = dict(usage, prompt_max=250_000)          # past 200,000 tokens the whole request pays the second card
        self.assertAlmostEqual(compare.cost_of(pro, long), 6000 * 4e-6 + 1000 * 0.4e-6 + 1000 * 18e-6 + 0.014)

    def test_stops_and_thought_summaries(self):
        self.assertEqual(self.reply(finishReason="MAX_TOKENS").stop, "max_tokens")
        self.assertEqual(self.reply(finishReason="SAFETY").stop, "refusal")
        blocked = providers.gemini_reply({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})
        self.assertEqual((blocked.stop, blocked.text), ("refusal", ""))
        thought = self.reply(content={"parts": [{"text": "Thinking about it.", "thought": True}, {"text": "{}"}]})
        self.assertEqual(thought.text, "{}")

    def test_schema_for_google_keeps_everything_but_additional_properties(self):
        stripped = providers.gemini_schema({"type": "object", "additionalProperties": False, "required": ["a"],
                                          "properties": {"a": {"type": "array", "items": [{"additionalProperties": False, "type": "object"}]}}})
        self.assertEqual(stripped, {"type": "object", "required": ["a"], "properties": {"a": {"type": "array", "items": [{"type": "object"}]}}})


class ClaudeResearch(unittest.TestCase):
    def test_paused_turns_continue_and_citations_are_kept(self):
        first = SimpleNamespace(model="claude-opus-5-5", stop_reason="pause_turn", usage=claude_usage(
            input_tokens=1000, server_tool_use=SimpleNamespace(web_search_requests=1, web_fetch_requests=0)), content=[
            SimpleNamespace(type="text", text="Searching."),
            SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "arsenal leeds team news"}),
            SimpleNamespace(type="web_search_tool_result", content=[SimpleNamespace(type="web_search_result", url="https://news.example/a", title="A")])])
        second = SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn", usage=claude_usage(
            input_tokens=3000, output_tokens=400, server_tool_use=SimpleNamespace(web_search_requests=0, web_fetch_requests=1)), content=[
            SimpleNamespace(type="web_fetch_tool_result", content=SimpleNamespace(type="web_fetch_result", url="https://news.example/b",
                                                                                   content=SimpleNamespace(title="B"))),
            SimpleNamespace(type="text", text='{"blurbs": [{"match_id": "1", "blurb": "', citations=None),
            SimpleNamespace(type="text", text="Saka is fit.", citations=[SimpleNamespace(type="web_search_result_location", url="https://news.example/a")]),
            SimpleNamespace(type="text", text='", "sources": ["https://news.example/a", "https://made.up/x"]}]}', citations=None)])
        client = FakeClaude(first, second)
        cfg = compare.Config("anthropic", "claude-opus-5-5", "medium")
        reply = compare.anthropic_research(client, cfg, "system", "prompt")
        self.assertEqual(client.calls[1]["messages"][1], {"role": "assistant", "content": first.content})
        self.assertEqual((reply.stop, reply.served, reply.queries), ("end", "claude-opus-5-5", ["arsenal leeds team news"]))
        self.assertEqual(set(reply.returned), {compare.match_key("https://news.example/a"), compare.match_key("https://news.example/b")})
        self.assertEqual(reply.usage, zero_usage(**{"in": 4000, "out": 450, "searches": 1, "opens": 1, "prompt_max": 3000}))
        # Opus 5.5: 4,000 input at $4/M, 450 output at $20/M, one search at a cent; the fetch costs only its tokens.
        self.assertAlmostEqual(compare.cost_of(cfg, reply.usage), 0.016 + 0.009 + 0.01)
        blurb = compare.research_blurbs(reply, {"1"})["1"]
        self.assertEqual((blurb["text"], blurb["listed"], blurb["native"]),
                         ("Saka is fit.", ["https://news.example/a", "https://made.up/x"], ["https://news.example/a"]))

    def test_blurbs_written_before_the_last_search_are_still_found(self):
        message = SimpleNamespace(model="claude-sonnet-5-5", stop_reason="end_turn", usage=claude_usage(), content=[
            SimpleNamespace(type="text", text='{"blurbs": [{"match_id": "1", "blurb": "Early.", "sources": []}]}'),
            SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "q"}),
            SimpleNamespace(type="text", text="That is all.")])
        reply = compare.anthropic_research(FakeClaude(message), compare.Config("anthropic", "claude-sonnet-5-5", "low"), "s", "p")
        self.assertEqual(compare.research_blurbs(reply, {"1"})["1"]["text"], "Early.")


class Blurbs(unittest.TestCase):
    def test_the_json_is_found_after_prose_and_inside_fences(self):
        text = 'Here they are {not json}:\n```json\n{"blurbs": [{"match_id": "1", "blurb": "A.", "sources": []}]}\n```'
        data, start = compare.extract_json(text)
        self.assertEqual((data, text[start]), ({"blurbs": [{"match_id": "1", "blurb": "A.", "sources": []}]}, "{"))
        self.assertEqual(compare.extract_json('{"ratings": []} and no blurbs'), (None, None))
        self.assertIsNone(compare.research_blurbs(compare.Reply(text='{"blurbs": [{"match_id": "1"'), {"1"}))

    def test_unknown_repeated_and_linked_blurbs(self):
        text = json.dumps({"blurbs": [
            {"match_id": "9", "blurb": "Not a fixture we asked about.", "sources": []},
            {"blurb": "Saka returns ([bbc.com](https://www.bbc.com/x?utm_source=openai)) after   injury, \\u2019per [the club](https://arsenal.com/n).",
             "sources": ["https://www.bbc.com/x", "https://www.bbc.com/x", " "], "match_id": "1"},
            {"match_id": "1", "blurb": "A second try.", "sources": []}, "junk"]})
        blurbs = compare.research_blurbs(compare.Reply(text=text), {"1", "2"})
        self.assertEqual(list(blurbs), ["1"])
        self.assertEqual(blurbs["1"]["text"], "Saka returns after injury, ’per the club.")
        self.assertEqual(blurbs["1"]["length"], len("Saka returns after injury, ’per the club."))
        self.assertEqual(blurbs["1"]["listed"], ["https://www.bbc.com/x"])
        self.assertTrue(blurbs["1"]["links_in_text"])
        self.assertEqual(compare.without_links("Plain text, as asked."), "Plain text, as asked.")
        self.assertEqual(compare.without_links("See https://x.example/a for more."), "See for more.")

    def test_citations_go_to_the_entry_they_fall_in_whatever_the_key_order(self):
        text = '{"blurbs": [{"blurb": "First.", "match_id": "1", "sources": []}, {"match_id": "2", "sources": [], "blurb": "Second."}]}'
        first, second = text.index("First."), text.index("Second.")
        reply = compare.Reply(text=text, native=[(first, first + 6, "https://a.example/1"), (second, second + 7, "https://b.example/2"),
                                                 (0, 1, "https://c.example/outside")])
        blurbs = compare.research_blurbs(reply, {"1", "2"})
        self.assertEqual((blurbs["1"]["native"], blurbs["2"]["native"]), (["https://a.example/1"], ["https://b.example/2"]))


class Server(http.server.BaseHTTPRequestHandler):
    """A local site with one page per way a link can fare, and an API that fails on cue."""
    hits, script, lock = [], [], threading.Lock()

    def log_message(self, *_):
        pass

    def send(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        page = {"/ok": (200, b"<html><title>Saka fit for Leeds</title></html>", ()), "/": (200, b"<title>Home</title>", ()),
                "/gone": (404, b"", ()), "/forbidden": (403, b"", ()), "/broken": (500, b"", ()),
                "/soft": (200, b"<html><head><title>Page Not Found | News</title></head></html>", ()),
                "/moved": (302, b"", (("Location", "/"),)), "/hop": (302, b"", (("Location", "/ok"),))}.get(self.path, (404, b"", ()))
        self.send(*page)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with Server.lock:
            Server.hits.append(self.path)
            status = Server.script.pop(0) if Server.script else 200
        if status == "slow":
            time.sleep(1.5)
            status = 200
        body = json.dumps({"ok": True} if status == 200 else {"error": {"message": f"status {status}", "code": status}}).encode()
        self.send(status, body)


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass          # the timeout test hangs up on a handler that is still asleep


class Network(unittest.TestCase):
    """Against a local server, not mocks: what urllib really does with each answer."""

    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        cls.env.start()
        cls.server = QuietServer(("127.0.0.1", 0), Server)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()

    def setUp(self):
        Server.hits.clear()
        Server.script.clear()

    def test_links_are_classified_as_a_reader_would_find_them(self):
        state = lambda path: compare.check_link(self.base + path)["state"]
        self.assertEqual([state(p) for p in ("/ok", "/hop", "/gone", "/soft", "/moved", "/forbidden", "/broken")],
                         ["live", "live", "dead", "dead", "dead", "blocked", "unreachable"])
        self.assertEqual(compare.check_link(self.base + "/soft")["why"], "a 'not found' page")
        self.assertEqual(compare.check_link(self.base + "/moved")["why"], "redirected to the front page")
        self.assertEqual(compare.check_link("http://127.0.0.1:9/nothing-listens", timeout=2)["state"], "unreachable")

    def test_a_redirect_is_read_without_being_followed(self):
        self.assertEqual(compare.location(self.base + "/hop"), "/ok")
        self.assertIsNone(compare.location(self.base + "/ok"))
        self.assertIsNone(compare.location(self.base + "/gone"))

    def test_rate_limits_and_server_errors_are_retried_but_not_bad_requests(self):
        Server.script[:] = [429, 503]
        with patch.object(providers.time, "sleep") as slept:
            self.assertEqual(providers.post_json(self.base + "/v1", {"a": 1}, {}), {"ok": True})
        self.assertEqual((len(Server.hits), [c.args[0] for c in slept.call_args_list]), (3, [2, 4]))
        Server.hits.clear()
        Server.script[:] = [400]
        with self.assertRaises(providers.ProviderError) as caught:
            providers.post_json(self.base + "/v1", {}, {})
        self.assertEqual((str(caught.exception), len(Server.hits)), ("HTTP 400: status 400", 1))
        Server.hits.clear()
        Server.script[:] = [429, 429, 429]
        with patch.object(providers.time, "sleep"), self.assertRaises(providers.ProviderError):
            providers.post_json(self.base + "/v1", {}, {})
        self.assertEqual(len(Server.hits), 3)

    def test_a_timeout_is_not_retried(self):
        Server.script[:] = ["slow"]
        with self.assertRaises(OSError):            # socket.timeout and URLError are both OSErrors
            providers.post_json(self.base + "/v1", {}, {}, timeout=0.3)
        self.assertEqual(len(Server.hits), 1)


class Sources(unittest.TestCase):
    def test_match_keys_ignore_tracking_but_not_the_page(self):
        key = compare.match_key
        self.assertEqual(key("https://www.bbc.com/sport/a?utm_source=openai"), key("http://bbc.com/sport/a/"))
        self.assertEqual(key("https://x.example/a?id=1&utm_medium=x"), key("https://x.example/a?id=1"))
        self.assertNotEqual(key("https://x.example/a?id=1"), key("https://x.example/a?id=2"))
        self.assertEqual(key("not a url"), "")

    def test_redirects_resolve_and_every_cited_page_is_checked(self):
        aaa, bbb, ccc = REDIRECT + "AAA", REDIRECT + "BBB", REDIRECT + "CCC"
        targets = {aaa: "https://www.marca.com/futbol/x.html", bbb: "https://as.com/y", ccc: "https://elsewhere.example/z"}
        states = {"https://marca.com/futbol/x.html/": {"state": "live", "status": 200}, "https://as.com/y": {"state": "dead", "status": 404},
                  "https://made.up/z": {"state": "unreachable", "status": None}, "https://elsewhere.example/z": {"state": "live", "status": 200},
                  "https://news.example/a?utm_source=openai": {"state": "blocked", "status": 403}}
        google = {"provider": "google", "blurbs": {"2": {"listed": ["https://marca.com/futbol/x.html/", "https://made.up/z", ccc],
                                                         "native": [aaa, bbb, aaa]}}}
        openai = {"provider": "openai", "blurbs": {"1": {"listed": ["https://news.example/a?utm_source=openai"], "native": []}}}
        replies = [compare.Reply(redirects={aaa: "marca.com", bbb: "as.com"}),
                   compare.Reply(returned={compare.match_key("https://news.example/a"): ("https://news.example/a", "A")})]
        checked = []
        with patch.object(compare, "location", targets.get), \
                patch.object(compare, "check_link", lambda url: checked.append(url) or states[url]):
            compare.settle_sources([google, openai], replies, workers=2)
        self.assertEqual(google["blurbs"]["2"]["sources"], [
            # The page a grounding chunk led to, cited by the model as it appears on the site: from its search.
            {"url": "https://marca.com/futbol/x.html/", "listed": True, "returned": True, "state": "live", "status": 200},
            {"url": "https://made.up/z", "listed": True, "returned": False, "state": "unreachable", "status": None},
            # A redirect the model wrote itself is followed, but it is not one of the chunks its search returned.
            {"url": "https://elsewhere.example/z", "listed": True, "returned": False, "state": "live", "status": 200},
            # Google's own citation of the blurb, which the model didn't list.
            {"url": "https://as.com/y", "listed": False, "returned": True, "state": "dead", "status": 404}])
        self.assertEqual(google["blurbs"]["2"]["native"], ["https://www.marca.com/futbol/x.html", "https://as.com/y"])
        self.assertEqual(openai["blurbs"]["1"]["sources"], [{"url": "https://news.example/a?utm_source=openai", "listed": True,
                                                              "returned": True, "state": "blocked", "status": 403}])
        self.assertEqual(len(checked), len(set(checked)))              # each page is fetched once
        self.assertEqual((google["pages_returned"], openai["pages_returned"]), (2, 1))


def fixture(mid, hours, league, home="Home", away="Away"):
    built = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)
    return {"id": mid, "kickoff_utc": (built + timedelta(hours=hours)).isoformat(), "league_id": league, "competition": league.upper(),
            "home": {"name": f"{home} {mid}"}, "away": {"name": f"{away} {mid}"}, "source_url": f"https://www.espn.com/soccer/match/_/gameId/{mid}"}


FACTS = {"date": "2026-10-08", "weekday": "Thursday", "built_at": "2026-10-08T06:00:00Z",
         "ranking_candidates": [fixture("a", 10, "eng.1"), fixture("b", 20, "eng.1"), fixture("c", 30, "esp.1"),
                                fixture("d", 40, "ita.1"), fixture("e", 50, "usa.1")]}
ROW = ('<li class="row avail" data-id="{id}" data-utc="{utc}" data-svc="{svc}" data-outlook="{outlook}" '
       'data-home="Home {id}" data-away="Away {id}">')


def page(outlook, off_lineup=()):
    return "".join(ROW.format(id=m["id"], utc=m["kickoff_utc"].replace("+00:00", "Z"), svc="none" if m["id"] in off_lineup else "espn",
                              outlook=outlook[m["id"]]) for m in FACTS["ranking_candidates"])


class Featured(unittest.TestCase):
    def rows(self, outlook, off=()):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            path.write_text(page(outlook, off))
            return compare.storylines.page_rows(path)

    def test_one_per_competition_on_the_default_lineup_by_published_rating(self):
        rows = self.rows(dict(a=10, b=20, c=30, d=40, e=50), off=("d",))
        published = {"rankings": {"a": {"score": 70}, "b": {"score": 80}, "c": {"score": 60}, "d": {"score": 99}, "e": {"score": 60}}}
        ids = lambda **kw: [m["id"] for m in compare.featured(FACTS, rows, published, **kw)]
        # b beats a in its league; d is off the lineup; c and e tie at 60 and go by ID.
        self.assertEqual(ids(), ["b", "c", "e"])
        self.assertEqual(ids(count=2), ["b", "c"])
        self.assertEqual(ids(ids=["e", "a"]), ["e", "a"])
        with self.assertRaises(ValueError):
            compare.featured(FACTS, rows, published, ids=["a", "zz"])

    def test_the_outlook_score_decides_when_nothing_is_published(self):
        rows = self.rows(dict(a=10, b=20, c=30, d=40, e=50))
        self.assertEqual([m["id"] for m in compare.featured(FACTS, rows, None)], ["e", "d", "c", "b"])


SERVED = {"gpt-6-luna": "gpt-6-luna-2026-09-01", "gpt-6.1-sol": "gpt-6-sol"}     # a dated snapshot, and another model


def fake_ratings(order):
    """A rate_once that rates every fixture it is sent, in the order `order` gives each configuration,
    for 1,000 input and 100 output tokens, and records the order of the calls."""
    calls = []

    def rate(cfg, header, fixtures, keys, clients):
        calls.append(cfg.model)
        ranking = order[cfg.model]
        raw = [{"match_id": f["id"], "popularity": ranking[f["id"]], "gameplay": ranking[f["id"]], "impact": ranking[f["id"]]} for f in fixtures]
        return raw, zero_usage(**{"in": 1000, "out": 100}), "end", SERVED.get(cfg.model, cfg.model)
    return rate, calls


class Run(unittest.TestCase):
    """main(), end to end, with the providers replaced: the order it spends in, what it skips and why,
    and the agreement it reports, worked out by hand."""
    ORDER = {"claude-haiku-5-5": dict(a=50, b=40, c=30, d=20, e=10),
             "gpt-6-luna": dict(a=10, b=20, c=30, d=40, e=50),          # the reverse
             "gpt-6.1-sol": dict(a=10, b=20, c=30, d=40, e=50),
             "gpt-5.5": dict(a=10, b=20, c=30, d=40, e=50)}

    def run_main(self, ratings, budget, keys=("ANTHROPIC_API_KEY", "OPENAI_API_KEY")):
        rate, calls = fake_ratings(self.ORDER)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "facts.json").write_text(json.dumps(FACTS))
            later = ROW.format(id="f", utc="2026-10-20T19:00:00Z", svc="espn", outlook=99)
            (tmp / "page.html").write_text(page(dict(a=50, b=40, c=30, d=20, e=10)) + later)
            # The published ratings also reach a day past the window, as the real ones do (f, on 20 October).
            published = {k: {"score": v} for k, v in self.ORDER["claude-haiku-5-5"].items()}
            published["f"] = {"score": 99}
            (tmp / "published.json").write_text(json.dumps({"rankings": published}))
            out = io.StringIO()
            env = {k: "test" for k in keys}
            with patch.dict(os.environ, env, clear=False), patch.object(compare, "rate_once", rate), \
                    patch.object(compare, "log", lambda *_: None), patch.object(compare, "settle_sources", lambda *_: None), redirect_stdout(out):
                for k in set(compare.KEYS.values()) - set(keys):
                    os.environ.pop(k, None)
                code = compare.main(["--facts", str(tmp / "facts.json"), "--page", str(tmp / "page.html"),
                                     "--published", str(tmp / "published.json"), "--ratings", ratings, "--budget", str(budget),
                                     "--out", str(tmp / "out" / "results.json")])
                results = json.loads((tmp / "out" / "results.json").read_text())
        return code, results, out.getvalue(), calls

    def test_cheapest_first_and_nothing_starts_that_could_pass_the_budget(self):
        configs = "openai:gpt-5.5:low openai:gpt-6-luna:low anthropic:claude-haiku-5-5:low openai:gpt-6.1-sol:low google:gemini-3.8-flash:low"
        chars = len(story.SCORES_SYSTEM) + len(story.rating_prompt(
            "It is 2:00 am on Thursday, 2026-10-08, US Eastern time.", [story.rating_fixture(m) for m in FACTS["ranking_candidates"]],
            scores_only=True))
        cfg = lambda text: compare.parse_configs(text, "ratings")[0]
        estimates = {m: compare.estimate(cfg(f"{p}:{m}:low"), "ratings", chars)
                     for p, m in (("anthropic", "claude-haiku-5-5"), ("openai", "gpt-6-luna"), ("openai", "gpt-6.1-sol"), ("openai", "gpt-5.5"))}
        # Each run here costs 1,000 input and 100 output tokens: haiku $0.00015, luna $0.00015, sol $0.003.
        spent = 0.00015 + 0.00015 + 0.003
        budget = spent + estimates["gpt-5.5"] - 0.0001          # just short of what gpt-5.5 might cost
        code, results, text, calls = self.run_main(configs, budget)
        self.assertEqual(code, 0)
        # Haiku and luna cost the same per token, so they keep the order they were listed in.
        self.assertEqual(calls, ["gpt-6-luna", "claude-haiku-5-5", "gpt-6.1-sol"])
        self.assertEqual([(s["config"], s["why"].split(" ")[0]) for s in results["skipped"]],
                         [("google:gemini-3.8-flash:low", "no"), ("openai:gpt-5.5:low", "its")])
        self.assertIn("no GEMINI_API_KEY", text)
        self.assertAlmostEqual(results["spent_usd"], spent)
        self.assertEqual([r["cost_usd"] for r in results["ratings"]], [0.00015, 0.00015, 0.003])

    def test_agreement_with_the_published_ratings_and_the_other_providers(self):
        code, results, text, _ = self.run_main("anthropic:claude-haiku-5-5:low openai:gpt-6-luna:low openai:gpt-6.1-sol:low", 5)
        row = lambda name: next(line for line in text.splitlines() if line.startswith(f"| {name} at low | $"))
        # Haiku is the published order (rho 1); the OpenAI models are its reverse (rho -1) and agree with each other.
        # At |rho| = 1 the interval is clamped at atanh(0.999999), with variance 1.06 / (5 - 3).
        z, se = math.atanh(0.999999), math.sqrt(1.06 / 2)
        interval = f"({math.tanh(z - 1.96 * se):.2f} to {math.tanh(z + 1.96 * se):.2f})"
        self.assertIn(f"| 5/5 | 1.00 {interval} | -1.00 | 1.00 |", row("claude-haiku-5-5"))
        # Against the other providers: luna's consensus is Haiku alone (-1); Haiku's is the mean of the two OpenAI models (-1).
        self.assertIn("| -1.00 | -1.00 |", row("gpt-6-luna"))
        # Eastern days: a and b on the 8th, c and d on the 9th, e on the 10th; f, on the 20th, is outside every
        # model's window and doesn't count. The reverse order still shares every pick (each day has at most two
        # fixtures) but puts the published #1 first only on the 10th.
        self.assertIn("| 5 of 5 picks · same #1 on 3 of 3 days |", row("claude-haiku-5-5"))
        self.assertIn("| 5 of 5 picks · same #1 on 1 of 3 days |", row("gpt-6-luna"))
        self.assertIn("served by gpt-6-sol", row("gpt-6.1-sol"))
        self.assertNotIn("served by", row("gpt-6-luna"))                # a dated snapshot of the model asked for
        self.assertIn("## Ratings agreement", text)


class RatingsRequests(unittest.TestCase):
    """story.write_ratings' rule for one chunk: whatever comes back without a valid rating is asked
    for once more, and a failure keeps what was rated and spent before it."""

    def run_with(self, *answers):
        calls, answers = [], list(answers)

        def rate(cfg, header, fixtures, keys, clients):
            calls.append([f["id"] for f in fixtures])
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer, zero_usage(**{"in": 1000, "out": 100}), "end", cfg.model
        with patch.object(compare, "rate_once", rate), patch.object(compare, "log", lambda *_: None):
            return compare.run_ratings(LUNA, HEADER, FIXTURES, {}, {}), calls

    def test_fixtures_left_unrated_are_asked_for_once_more(self):
        out_of_range = {"match_id": "2", "popularity": 101, "gameplay": 60, "impact": 70}
        result, calls = self.run_with([{"match_id": "1", "popularity": 50, "gameplay": 60, "impact": 70}, out_of_range],
                                      [{"match_id": "2", "popularity": 40, "gameplay": 40, "impact": 40}])
        self.assertEqual(calls, [["1", "2"], ["2"]])
        self.assertEqual((result["status"], result["rated"], result["requests"]), ("ok", 2, 2))
        # 25% popularity, 35% gameplay, 40% impact: 12.5 + 21 + 28 for the first.
        self.assertEqual(result["scores"], {"1": 61.5, "2": 40.0})
        self.assertAlmostEqual(result["cost_usd"], 2 * (1000 * 0.10e-6 + 100 * 0.50e-6))

    def test_a_failed_second_request_keeps_the_first_requests_ratings(self):
        result, calls = self.run_with([{"match_id": "1", "popularity": 50, "gameplay": 60, "impact": 70}],
                                      providers.ProviderError("HTTP 500: overloaded"))
        self.assertEqual((result["status"], result["rated"], result["requests"], result["error"]),
                         ("ok", 1, 1, "ProviderError: HTTP 500: overloaded"))
        self.assertAlmostEqual(result["cost_usd"], 1000 * 0.10e-6 + 100 * 0.50e-6)

    def test_nothing_rated_twice_is_given_up(self):
        result, calls = self.run_with([], [])
        self.assertEqual((result["status"], result["rated"], len(calls)), ("empty", 0, 2))
        failed, _ = self.run_with(providers.ProviderError("HTTP 400: bad schema"))
        self.assertEqual((failed["status"], failed["requests"], failed["cost_usd"]), ("error", 0, 0))


def asked_fixtures(prompt):
    """The fixture IDs a ratings or research prompt lists."""
    if "exactly once:\n" in prompt:
        return [f["id"] for f in json.loads(prompt.split("exactly once:\n", 1)[1])]
    return [f["id"] for f in json.loads(prompt.split("Fixtures:\n", 1)[1].split("\n\nYou have up to", 1)[0])]


def ratings_json(ids, base):
    return json.dumps({"ratings": [{"match_id": i, "popularity": base - n, "gameplay": base - n, "impact": base - n} for n, i in enumerate(ids)]})


def blurbs_json(ids, source):
    return json.dumps({"blurbs": [{"match_id": i, "blurb": f"News for {i}.", "sources": [source(i)]} for i in ids]})


def fake_api(url, body, headers, **_):
    """OpenAI's and Google's APIs, answering ratings and research requests in their documented shapes."""
    if "openai" in url:
        ids = asked_fixtures(body["input"])
        usage = {"input_tokens": 2000, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 300,
                 "output_tokens_details": {"reasoning_tokens": 100}}
        if "text" in body:
            return {"model": body["model"], "status": "completed", "usage": usage, "output": [
                {"type": "message", "content": [{"type": "output_text", "text": ratings_json(ids, 90), "annotations": []}]}]}
        return {"model": body["model"], "status": "completed", "usage": usage, "output": [
            {"type": "web_search_call", "action": {"type": "search", "query": "q", "sources": [{"type": "url", "url": f"https://o.example/{i}"} for i in ids]}},
            {"type": "message", "content": [{"type": "output_text", "text": blurbs_json(ids, lambda i: f"https://o.example/{i}?utm_source=openai"), "annotations": []}]}]}
    ids = asked_fixtures(body["contents"][0]["parts"][0]["text"])
    usage = {"promptTokenCount": 2000, "candidatesTokenCount": 300, "thoughtsTokenCount": 100}
    if "tools" not in body:
        return {"modelVersion": "gemini", "usageMetadata": usage, "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": ratings_json(ids, 10)}]}}]}
    text = blurbs_json(ids, lambda i: "https://made.up/" + i)
    return {"modelVersion": "gemini", "usageMetadata": usage, "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]},
            "groundingMetadata": {"webSearchQueries": ["q"], "groundingChunks": [{"web": {"uri": REDIRECT + i, "title": "g.example"}} for i in ids],
                                  "groundingSupports": [{"segment": {"startIndex": text.index(f"News for {i}."), "endIndex": text.index(f"News for {i}.") + 12,
                                                                     "text": f"News for {i}."}, "groundingChunkIndices": [n]} for n, i in enumerate(ids)]}}]}


class FakeAnthropic:
    """The anthropic package: ratings requests (no tools) and research requests (with tools)."""

    def __init__(self):
        self.Anthropic = lambda **_: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=self.stream)))

    def stream(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        ids = asked_fixtures(prompt)
        if "tools" in kwargs:
            content = [SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "q"}),
                       SimpleNamespace(type="web_search_tool_result", content=[
                           SimpleNamespace(type="web_search_result", url=f"https://c.example/{i}", title=i) for i in ids]),
                       SimpleNamespace(type="text", text=blurbs_json(ids, lambda i: f"https://c.example/{i}"), citations=None)]
        else:
            content = [SimpleNamespace(type="text", text=ratings_json(ids, 50))]
        message = SimpleNamespace(model=kwargs["model"], stop_reason="end_turn", content=content, usage=claude_usage(input_tokens=2000, output_tokens=300))
        return contextlib.nullcontext(SimpleNamespace(get_final_message=lambda: message))


class EveryProvider(unittest.TestCase):
    def test_a_run_with_every_provider_reports_both_tasks(self):
        facts = dict(FACTS, ranking_candidates=FACTS["ranking_candidates"])
        live = {f"https://o.example/{i}?utm_source=openai" for i in "abcde"} | {f"https://c.example/{i}" for i in "abcde"}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "facts.json").write_text(json.dumps(facts))
            (tmp / "page.html").write_text(page(dict(a=50, b=40, c=30, d=20, e=10)))
            out = io.StringIO()
            env = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"}
            with patch.dict(os.environ, env), patch.dict(sys.modules, {"anthropic": FakeAnthropic()}), \
                    patch.object(providers, "post_json", fake_api), patch.object(compare, "log", lambda *_: None), \
                    patch.object(compare, "location", lambda uri: "https://g.example/" + uri.rsplit("/", 1)[1]), \
                    patch.object(compare, "check_link", lambda url: {"state": "live" if url in live or "g.example" in url else "dead", "status": 200}), \
                    redirect_stdout(out):
                code = compare.main(["--facts", str(tmp / "facts.json"), "--page", str(tmp / "page.html"), "--out", str(tmp / "r.json"),
                                     "--ratings", "anthropic:claude-haiku-5-5:low openai:gpt-6-luna:low google:gemini-3.8-flash:low",
                                     "--research", "anthropic:claude-sonnet-5-5:medium openai:gpt-6.1-sol:medium google:gemini-3.1-pro-preview:medium"])
                results = json.loads((tmp / "r.json").read_text())
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual([r["status"] for r in results["ratings"]], ["ok", "ok", "ok"])
        self.assertEqual([r["rated"] for r in results["ratings"]], [5, 5, 5])
        self.assertEqual([r["status"] for r in results["research"]["runs"]], ["ok", "ok", "ok"])
        # Featured by the Outlook score (nothing published), one per competition: a (eng.1, 50) beats b; then c, d, e.
        self.assertEqual([f["id"] for f in results["research"]["fixtures"]], ["a", "c", "d", "e"])
        row = lambda name: next(line for line in text.splitlines() if line.startswith(f"| {name} at medium | $"))
        # Claude and OpenAI cite the pages their searches returned, all live; Gemini lists made-up pages (not from
        # its search, dead) while its own grounding cites a page per blurb, which the notes count.
        self.assertIn("| 4/4 | 0 | 4 | 4 | 4 | 0 | 0 |", row("claude-sonnet-5-5"))
        self.assertIn("| 4/4 | 0 | 4 | 4 | 4 | 0 | 0 |", row("gpt-6.1-sol"))
        self.assertIn("| 4/4 | 0 | 4 | 0 | 0 | 4 | 0 | 4 more cited by the provider's own annotations |", row("gemini-3.1-pro-preview"))
        self.assertIn("[g.example](https://g.example/a) (from its search, live, provider annotation)", text)
        self.assertIn("### Ratings: three scores for each of the 5 fixtures", text)
        # Gemini 3.1 Pro: 2,000 input at $2/M, 400 output (with thinking) at $12/M, one query at $0.014.
        pro = next(r for r in results["research"]["runs"] if r["model"] == "gemini-3.1-pro-preview")
        self.assertAlmostEqual(pro["cost_usd"], 2000 * 2e-6 + 400 * 12e-6 + 0.014)
        self.assertAlmostEqual(results["spent_usd"], sum(r["cost_usd"] for r in results["ratings"] + results["research"]["runs"]))


class Estimates(unittest.TestCase):
    def test_estimates_by_hand(self):
        # Ratings: twice the prompt at a third of a token a character, plus 10,000 output tokens.
        self.assertAlmostEqual(compare.estimate(LUNA, "ratings", 3000), 2 * 1000 * 0.10e-6 + 10_000 * 0.50e-6)
        # Research: the prompt plus 100,000 tokens of pages, 10,000 output tokens and twelve searches.
        self.assertAlmostEqual(compare.estimate(LUNA, "research", 3000), 101_000 * 0.10e-6 + 10_000 * 0.50e-6 + 12 * 0.01)
        opus = compare.Config("anthropic", "claude-opus-5-5", "low")
        self.assertAlmostEqual(compare.estimate(opus, "research", 3000), 101_000 * 4e-6 + 10_000 * 20e-6 + 12 * 0.01)
        budget = compare.Budget(1.0)
        budget.charge(0.75)
        self.assertTrue(budget.allows(0.25))
        self.assertFalse(budget.allows(0.2501))
        budget.charge(None)                      # a model without a price never gets this far, but None must not break it
        self.assertEqual(budget.spent, 0.75)


if __name__ == "__main__":
    unittest.main()
