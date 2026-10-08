#!/usr/bin/env python3
"""Writes the day's match ratings for Soccer Outlook, and in the full design Claude's storylines too.

build.py --facts supplies the fixtures with known service coverage in the next 24 hours, later
fallback candidates, each league's nearest window, and every upcoming fixture for the ratings. This
script makes two kinds of request:

  research  one request with web search and web fetch. Claude writes a short overview, up to eight
            match notes, up to three forecast items, one paragraph per league and a league order, all
            with separately referenced phrases, and returns them through one strict tool call,
            publish_story. The browser derives team, league and broadcaster tags from the fixture IDs
            and dims phrases excluded by the visitor's filters.
  ratings   requests without tools, RATING_CHUNK fixtures each and RATING_WORKERS at a time, that
            return structured output: popularity, gameplay and impact from 0 to 100 and a card blurb
            for every fixture.

Why two kinds. Rating every fixture inside the research request made one tool call of about 40,000
output tokens: the whole call had to fit under the output cap, which a busier week would pass; any
gap triggered a repair that regenerated all of it; and a refresh resent and re-rated fixtures days
away. Now the morning run rates everything once, a refresh re-rates only what kicks off within
RERATE_HOURS (or has no rating or blurb) and keeps the rest as rated that morning, a repair asks only
for what is missing, and no single response grows with the slate.

Sources. The page shows what Claude writes, so every claim has to be traceable. A source counts only
if this run's searches or page reads returned it (or a run earlier today did and the story cited it).
The overview, forecast items and notes need at least one such page. League paragraphs and card blurbs
may instead rest on the facts ESPN supplied (table, form, stage); the model then cites nothing and the
script attaches ESPN's page for the fixtures the text names, marked "facts", which the page labels as
ESPN's table and form rather than as reporting. An ESPN page counts only for the fixtures an item
names. A URL the run did not read is never shown, and text that cites only such URLs is dropped
rather than relabelled as resting on facts.

Modes, one per kind of build:
  full     research the day from scratch and rate every fixture (the first run of the day)
  refresh  update today's story for the moment: the model gets the earlier story and its sources,
           searches only for what may have changed (team news, lineups, results), and keeps what still
           holds, with a smaller search budget. Ratings are redone only for the next RERATE_HOURS.
           Without a story for today it runs as full.
  keep     republish the current story unchanged, whatever its date, and never call the API (builds
           after a code change: the news hasn't changed, and a push should never cost anything)
  daily    the scheduled builds: full when there is no story for today, otherwise keep, so the day's
           storylines are written once, by its first scheduled build that succeeds (normally the
           early-morning one; if that fails or never starts, the next one)
  auto     full when there is no story for today, keep when today's is less than MIN_GAP_HOURS old
           (GitHub can start a schedule hours late, right before the next one), otherwise refresh:
           storylines updated through the day, at the cost of those updates as well
  ratings  three scores per fixture and nothing else: no research, overview or blurbs, and no web
           search; one structured request for the fixtures kicking off within RATING_WINDOW_HOURS,
           widened a day at a time when that holds fewer than MIN_RATED. Any model in providers.MODELS
           can run it: Claude through the anthropic SDK, OpenAI's and Google's models through their
           own structured output, with the same system prompt, fixtures and schema
On failure, a refresh keeps today's earlier news, and ratings are still attempted.

settings.toml's [ai] table decides: `enabled` is the owner's switch, `design` is "ratings" (the
ratings mode above, once a day) or "full" (Claude's research, overview and blurbs as well, once a
day), and `model` and `effort` say who does it. In the ratings design every mode that writes rates,
and daily and auto rate once a day. A story written by another design, model or effort than the
settings name now doesn't count as today's, and even keep writes when the published one is such a
story: the push that changes the settings puts the change on the page at once. Off (or unreadable,
to be safe), the script makes no API call and writes nothing, not even the story already published,
so the next publish takes the AI's text and ratings off the page. --ignore-switch ignores the table,
for measuring configurations (compare-storylines.yml): --mode, --model and --effort then decide.
--usage-out writes the run's tokens, searches, cost and time as JSON. Without the model's provider's
key (ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY) the script reuses today's previous story if
there is one and otherwise writes nothing, so the page simply shows no AI ratings. It exits 0 unless
its arguments are wrong: a failed story must never block the schedule from being published.

Usage: python story.py --facts work/facts.json --out site/story.json [--previous old-story.json]
                       [--mode daily|auto|full|refresh|keep|ratings] [--model ID] [--effort LEVEL] [--usage-out FILE]
                       [--settings settings.toml] [--ignore-switch]
"""
import argparse
import json
import os
import re
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import providers

ET = ZoneInfo("America/New_York")
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.toml")
DEFAULT_MODEL = "claude-opus-5-5"
# Effort per mode when nothing says otherwise (settings.toml's [ai] effort does, and --effort): Opus
# 5.5's own default for the research. On research work Anthropic's published curves are nearly flat,
# medium matching high's accuracy at 70-87% of the cost (Optimizing for cost and intelligence, checked
# 2026-10-07); .github/workflows/compare-storylines.yml checks that on this workload. Every provider
# takes low and medium.
DEFAULT_EFFORT = {"full": "medium", "refresh": "medium", "ratings": "low"}
EFFORTS = providers.EFFORTS["anthropic"]
ALL_EFFORTS = tuple(dict.fromkeys(e for efforts in providers.EFFORTS.values() for e in efforts))
BUDGETS = {"full": (12, 6), "refresh": (5, 2), "ratings": (0, 0)}   # (web searches, full-page reads) per run
MAX_TOKENS = 64000                    # research request: a backstop only, streamed and billed only when used
MAX_REQUESTS = 6                      # pause_turn continuations, one nudge to publish and one repair
# Ratings: about 85 output tokens a fixture, so a chunk stays near 5,000 tokens however busy the week.
RATING_CHUNK = 60
RATING_WORKERS = 3
RATING_MAX_TOKENS = 32000
RERATE_HOURS = 24                     # a refresh re-rates fixtures kicking off this soon
# Ratings mode rates what kicks off within RATING_WINDOW_HOURS, widened a day at a time until the window
# holds MIN_RATED fixtures (an international break leaves the next three days nearly empty). The script
# decides this, not the model: it knows the fixtures, and asking would cost tokens and add a judgment.
RATING_WINDOW_HOURS = 72
MIN_RATED = 20
SCORES_CHUNK = 200                    # ratings mode: about 25 output tokens a fixture, so one request covers a window
MIN_GAP_HOURS = 3                     # auto mode keeps a story this fresh rather than paying again
LIMITS = {"blurb": 260, "league": 450, "item": 520, "lede": 450, "note": 320}
FACTS_TITLE = "ESPN table and form"
WEIGHTS = (("popularity", .25), ("gameplay", .35), ("impact", .40))
# List prices, the models each provider offers here and what each can run are in providers.py. Opus and
# Sonnet 5.5 take the dynamic-filtering web tools, effort, structured outputs and server-side fallbacks,
# which the research relies on. Haiku 5.5 takes effort and structured outputs but not those web tools,
# and has no server-side fallback (sending one is an error), so like every OpenAI and Google model it
# runs the ratings design only.
PRICES = providers.ANTHROPIC_PRICES
RATINGS_ONLY_MODELS = {m for m, p in providers.MODELS.items() if p == "anthropic"} - set(providers.RESEARCH_MODELS)
REQUEST_OPTIONS = dict(betas=["server-side-fallback-2026-07-01"], fallbacks="default")


def request_options(model):
    """The server-side fallback for a declined request, on the models that have one."""
    return {} if model in RATINGS_ONLY_MODELS else REQUEST_OPTIONS

SYSTEM = """You write the daily storylines for Soccer Outlook, a soccer schedule with visitor-controlled competition and service filters. News serves upcoming matches available through the visitor's selected services. The news candidate lists contain only fixtures with listed or usual service coverage. Prioritize the default lineup for the opening; visitors can select other services and the browser filters the text accordingly. The page already lists kickoff times, channels, table positions, recent form and top scorers. Cover only facts that directly affect a specific upcoming fixture: the stakes, player availability, likely selection supported by reporting, a relevant matchup, or a scheduling change. An upcoming international break belongs only when explaining its effect on a listed fixture. Exclude general club news, financial investigations, ownership stories, or unrelated managerial controversy. Mentioning a team that has a fixture is not enough: explain the concrete match connection.

Research with web search before writing, and read a full article when a search snippet is not enough. Prefer recent reporting from established outlets: clubs and federations, major newspapers, broadcasters, wire services. For news, state only what you read in this session or what the supplied ESPN facts establish. If you cannot confirm something, leave it out rather than guess, and never predict results or invent lineups, injuries or quotes. Do not merely repeat kickoff times and channels as news.

Cite only pages that your searches or page reads returned in this session; any other URL removes the item it supports. The overview, forecast items and match notes need such a page. A league paragraph may rest on the supplied ESPN facts alone (table, form, stage): then leave its sources empty, and the page labels it as based on ESPN's table and form. Ratings are handled separately.

Write plainly, in present tense, for a reader in the United States. When you have what you need, call publish_story with the complete news."""

SEGMENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["text", "match_ids"],
    "properties": {
        "text": {"type": "string", "description": "An exact text fragment, including its spaces and punctuation."},
        "match_ids": {"type": "array", "items": {"type": "string"},
                      "description": "Fixture IDs this phrase refers to. Use [] for connective words. Independently tag team/league names and each match-specific claim, so one league can dim without dimming the others."},
    },
}
EDITORIAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["segments", "sources"],
    "properties": {
        "segments": {"type": "array", "items": SEGMENT_SCHEMA,
                     "description": "One short sentence, about 180-300 characters, split at independently filterable phrases. Concatenating all text fragments must reproduce the sentence exactly. Every mentioned team or league and its related claims must reference its fixtures. Each sentence must explain something specific about that upcoming match; no general club news."},
        "sources": {"type": "array", "items": {"type": "string"},
                    "description": "One to three URLs of pages read in this session supporting this sentence."},
    },
}
PUBLISH_TOOL = {
    "name": "publish_story",
    "description": "Publish the storylines. Call exactly once, after your research.",
    "strict": True,
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["lede_items", "league_order", "league_blurbs", "notes", "forecast"],
        "properties": {
            "league_order": {"type": "array", "items": {"type": "string"},
                             "description": "Every supplied leagues league_id exactly once, ordered by general viewing interest for a US soccer audience. Consider overall quality, appeal and stakes, independently of today's filters. Visitors can reorder this default. The browser blends 80% match interest with 20% league priority."},
            "lede_items": {
                "type": "array", "items": EDITORIAL_SCHEMA,
                "description": "A general overview in one short paragraph, at most 450 characters total. One to three tagged sentences connecting the day's available fixtures and pertinent match news. Prioritize current-day fixtures on the default services and enabled competitions; look further ahead when none qualify. Every claim must be tied to supplied fixtures and to pages read in this session. Do not repeat the individual match blurbs.",
            },
            "league_blurbs": {
                "type": "array", "description": "One independently usable paragraph for EVERY league_candidates entry. Rate its news interest independently of service filters. Do not omit later leagues.",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["league_id", "interest", "segments", "sources"],
                          "properties": {
                              "league_id": {"type": "string", "description": "Exact league_candidates league_id."},
                              "interest": {"type": "integer", "description": "0–100 editorial interest of this fixture-specific story: routine useful context to unusually compelling stakes/news. Not match quality or an outcome probability."},
                              "segments": {"type": "array", "items": SEGMENT_SCHEMA,
                                           "description": "One short paragraph, one to three sentences, at most 450 characters total. Tag each match-specific phrase. Reference only supplied upcoming fixtures from this league."},
                              "sources": {"type": "array", "items": {"type": "string"},
                                          "description": "URLs of pages read in this session that support the paragraph. Leave empty when it rests only on the supplied ESPN facts."},
                          }},
            },
            "notes": {
                "type": "array", "description": "Up to eight researched match notes. Fewer is fine; never pad the count with later matches.",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["match_id", "note", "sources"],
                    "properties": {
                        "match_id": {"type": "string", "description": "The exact fixture ID from the facts."},
                        "note": {"type": "string", "description": "One or two sentences, at most about 260 characters."},
                        "sources": {"type": "array", "items": {"type": "string"}, "description": "One to three URLs of pages read in this session supporting the note."},
                    },
                },
            },
            "forecast": {
                "type": "object", "additionalProperties": False, "required": ["items"],
                "description": "Short-range researched context, with independently filterable phrases; do not repeat the lede or the browser's counts and coverage summary.",
                "properties": {"items": {"type": "array", "items": EDITORIAL_SCHEMA}},
            },
        },
    },
}

FOCUS_GUIDE = ("Supply exactly one league_blurbs paragraph for EVERY league_candidates entry, even leagues whose "
               "next fixtures are beyond 24 hours. Each paragraph is independently usable: one to three sentences, "
               "at most 450 characters, about a supplied upcoming fixture or fixtures in that league. Prefer "
               "fixtures with watch_on in that league's nearest supplied window. Assign an interest score from "
               "0 to 100 for the story itself. These paragraphs supply section context and a fallback when the "
               "general overview has no eligible fixtures after filtering. The browser prioritizes the rolling next 24 hours after service and "
               "competition filters, then the nearest later 24-hour window. There is no minimum score "
               "for a blurb: always offer useful fixture-specific context, including later leagues. "
               "Research fresh match previews, competitive stakes, player availability and scheduling context. "
               "If fresh reporting is unavailable, explain a matchup using the supplied table, form, stage or "
               "scheduling facts and leave the paragraph's sources empty; the page labels it as based on ESPN's "
               "table and form. Any claim beyond the supplied facts needs a page read in this session. Do not simply repeat kickoff times "
               "and channels, add faux announcing or invent news. Nothing of interest in breaking news does not "
               "mean an empty paragraph. A match with unconfirmed coverage is not evidence of availability. "
               "Keep each paragraph within one league and one time window. Use segments to tag exact phrases "
               "with fixture IDs. The page derives team, league and broadcaster tags from those IDs. Tag a "
               "team/league name and its related claim separately from unrelated fixtures; neutral joining words "
               "get []. Preserve spaces and punctuation. Avoid 'your services', 'today' and 'tomorrow': selections "
               "and the clock change. Give actual dates and Eastern times when needed. ")

FORECAST_GUIDE = ("Write lede_items as one general overview paragraph of at most 450 characters. Focus on the "
                  "current day and the default services and enabled competitions, explaining the slate as a whole "
                  "rather than previewing just one league. When no current-day fixture qualifies, look ahead to "
                  "the nearest available fixtures. Keep fixture references and sources so filters can choose relevant "
                  "sentences. Do not invent reasons for a sparse schedule. " + FOCUS_GUIDE +
                  "Write up to three short forecast items with researched context and source URLs. "
                  "An empty list is better than canned commentary. The browser separately shows factual counts, "
                  "coverage and the next kickoff, so do not repeat those or the lede. An absent fixture is not "
                  "evidence of a break, a quiet day or a weekend return. Never invent an explanation.")

FACTS_GUIDE = ("'fixtures' holds each fixture's facts once, keyed by its ID; next_24_hours, later_if_needed and "
               "each league_candidates entry list fixture IDs from it. 'watch_on' records where the default "
               "household could watch; prioritize it for the opening. 'hidden_by_default' marks a competition the "
               "page switches off unless the visitor turns it on. 'played_today', when present, gives the day's "
               "notable results so far, for context.")

RATING_SCALE = """Give separate integer scores from 0 to 100 for popularity (audience appeal), gameplay (expected football quality and competitiveness, without predicting a result) and impact (the competitive stakes of this particular fixture, supported by stage or table context). Use one absolute scale across all competitions and days: 20 = limited appeal, quality or stakes; 40 = routine; 60 = notably appealing, competitive or meaningful; 80 = exceptional; 95 = a rare global event or decisive final. Judge each dimension independently: a famous club does not automatically mean compelling play or high stakes, and missing evidence must not inflate a score. Do not normalize scores to this group, force any fixture above 80, or change scores for where the match can be watched. The combined score is 25% popularity, 35% gameplay and 40% impact."""
RATING_SYSTEM = """You rate upcoming soccer fixtures for Soccer Outlook, a schedule for viewers in the United States. Rate every fixture you are given, exactly once, as an editorial assessment from the supplied facts (teams, table, form, stage), established audience appeal and the reporting supplied with the request. Do not invent injuries, lineups, stakes or predicted scores.

""" + RATING_SCALE + """

Give every fixture its own blurb of at most 260 characters explaining its matchup, stakes or relevant news: specific, standing on its own, not a restatement of the teams, time, channel or score. When a blurb uses the supplied reporting, cite that reporting's URL exactly as listed. When it rests only on the supplied fixture facts, leave its sources empty: the page labels such blurbs as based on ESPN's table and form. Never cite any other URL."""
# Ratings mode: the same scale on the facts alone, with no blurb to write and no reporting to use.
SCORES_SYSTEM = """You rate upcoming soccer fixtures for Soccer Outlook, a schedule for viewers in the United States. Rate every fixture you are given, exactly once, as an editorial assessment from the supplied facts (teams, table, form, stage) and established audience appeal. Do not invent injuries, lineups, stakes or predicted scores.

""" + RATING_SCALE

RATING_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ratings"],
    "properties": {"ratings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["match_id", "popularity", "gameplay", "impact", "blurb", "sources"],
        "properties": {
            "match_id": {"type": "string"},
            "popularity": {"type": "integer", "description": "0-100"},
            "gameplay": {"type": "integer", "description": "0-100"},
            "impact": {"type": "integer", "description": "0-100"},
            "blurb": {"type": "string", "description": "At most 260 characters."},
            "sources": {"type": "array", "items": {"type": "string"},
                        "description": "URLs from the supplied reporting that the blurb uses; empty when it rests on the fixture facts."},
        }}}},
}


SCORES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ratings"],
    "properties": {"ratings": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["match_id", "popularity", "gameplay", "impact"],
        "properties": {
            "match_id": {"type": "string"},
            "popularity": {"type": "integer", "description": "0-100"},
            "gameplay": {"type": "integer", "description": "0-100"},
            "impact": {"type": "integer", "description": "0-100"},
        }}}},
}


def tools(budget):
    searches, fetches = budget
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches},
        PUBLISH_TOOL,
    ]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def summary(msg):
    """Adds a line to the GitHub Actions run summary when there is one."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def url_key(url):
    """Normalizes a URL for matching cited links against the links the tools returned."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (p.path.rstrip("/") or "/") + (("?" + p.query) if p.query else "")


def collect_sources(content, seen):
    """Records {url key: (url, title)} for every page a web search or web fetch returned."""
    for block in content:
        btype = getattr(block, "type", "")
        if btype == "web_search_tool_result":
            results = getattr(block, "content", None)
            if isinstance(results, list):      # a list on success; a single error object otherwise
                for r in results:
                    if getattr(r, "type", "") == "web_search_result" and url_key(getattr(r, "url", "") or ""):
                        seen.setdefault(url_key(r.url), (r.url, getattr(r, "title", "") or ""))
        elif btype == "web_fetch_tool_result":
            result = getattr(block, "content", None)
            if getattr(result, "type", "") == "web_fetch_result" and url_key(getattr(result, "url", "") or ""):
                doc = getattr(result, "content", None)
                title = getattr(doc, "title", "") or ""
                seen.setdefault(url_key(result.url), (result.url, title))


def clip(text, limit):
    """Trims to the last sentence end within limit characters, else to a word boundary."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit * 0.5:
        return cut[:end + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:") + "…"


def normalize_editorial(value):
    """Decode Unicode escapes a model has written literally inside already-decoded prose.

    Touch only editorial fields: preserve citation URLs and fixture IDs exactly as supplied.
    Existing UTF-8 text is unchanged, and lone surrogate escapes remain printable text.
    """
    if isinstance(value, list):
        return [normalize_editorial(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"blurb", "note", "text", "lede", "headline", "later_reason"} and isinstance(item, str):
            def decode(match):
                code = int(match.group(1), 16)
                return match.group(0) if 0xD800 <= code <= 0xDFFF else chr(code)
            result[key] = re.sub(r"\\+u([0-9a-fA-F]{4})", decode, item)
        else:
            result[key] = normalize_editorial(item)
    return result


def fixture_links(facts):
    """{fixture ID: its ESPN match page}, for every fixture the facts name."""
    lists = [facts.get(key, []) for key in ("next_24_hours", "later_if_needed", "ranking_candidates")]
    lists += [group.get("matches", []) for group in facts.get("league_candidates", [])]
    return {m["id"]: m["source_url"] for ms in lists for m in ms if isinstance(m, dict) and m.get("source_url")}


def facts_source(url):
    return {"url": url, "title": FACTS_TITLE, "kind": "facts"}


def is_facts(source):
    # "ESPN match facts" is how stories before the "kind" field marked an ESPN page nobody read.
    return source.get("kind") == "facts" or source.get("title") == "ESPN match facts"


def cite(urls, refs, seen, links, limit=3):
    """Sorts the URLs an item cites (an item naming the fixtures `refs`) into what may stand as its
    sources: pages this run read (or a run earlier today read and cited), and ESPN's page for a
    fixture the item itself names, marked as facts. Returns (sources, unverified), where unverified
    counts the URLs that are neither: a page nobody read, or another fixture's ESPN page. Those are
    never shown; the caller decides whether what is left still supports the item."""
    own = {url_key(links[r]): links[r] for r in refs if r in links}
    out, keys, unverified = [], set(), 0
    for u in urls if isinstance(urls, list) else []:
        key = url_key(u) if isinstance(u, str) else ""
        if key and key in keys:
            continue
        if key and key in seen:
            url, title = seen[key]
            out.append({"url": url, "title": (title or "")[:200]})
        elif key and key in own:
            out.append(facts_source(own[key]))
        else:
            unverified += 1
            continue
        keys.add(key)
    return out[:limit], unverified


def read_sources(sources):
    return [s for s in sources if not is_facts(s)]


def trim_segments(parts, limit):
    """Shortens tagged text to its last complete sentence within `limit` characters, so no phrase is
    cut mid-sentence; returns [] when no sentence ends in the first half or no fixture reference is left."""
    text = "".join(p["text"] for p in parts)
    if len(text) <= limit:
        return parts
    window = text[:limit + 1]
    cut = max(window.rfind(mark) for mark in (". ", "! ", "? ")) + 1
    if cut < limit * 0.5:
        return []
    out, pos = [], 0
    for p in parts:
        if pos >= cut:
            break
        piece = p["text"][:cut - pos]
        if piece:
            out.append({"text": piece, "match_ids": p["match_ids"]})
        pos += len(p["text"])
    return out if any(p["match_ids"] for p in out) else []


def clean_segments(raw, ids):
    """Preserve exact text boundaries; reject any unknown reference instead of guessing its tags."""
    if not isinstance(raw, list) or not raw:
        return []
    parts = []
    for part in raw:
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            return []
        refs = part.get("match_ids")
        if not isinstance(refs, list) or any(not isinstance(mid, str) or mid not in ids for mid in refs):
            return []
        parts.append({"text": part["text"], "match_ids": list(dict.fromkeys(refs))})
    return parts


def clean_item(item, ids, near_ids, seen, links, need_read, trim_to=None, max_len=LIMITS["item"]):
    """One tagged paragraph with its sources, or None. News (need_read) must cite a page read in this
    run; context may instead rest on the supplied facts, which then become its ESPN sources."""
    if not isinstance(item, dict):
        return None
    parts = clean_segments(item.get("segments"), ids)
    if trim_to:
        parts = trim_segments(parts, trim_to)
    refs = list(dict.fromkeys(mid for part in parts for mid in part["match_ids"]))
    text = "".join(part["text"] for part in parts)
    if not refs or not text.strip() or len(text) > max_len:
        return None
    near = set(refs) & near_ids
    if near and not set(refs) <= near_ids:
        return None
    sources, unverified = cite(item.get("sources"), refs, seen, links)
    if need_read and not read_sources(sources):
        return None
    if not sources:
        if unverified:      # it leaned on pages nobody read; that is not the supplied facts
            return None
        sources = [facts_source(links[r]) for r in refs if r in links][:3]
        if not sources:
            return None
    return {"text": text, "match_ids": refs, "segments": parts, "sources": sources}


def clean_items(raw, ids, near_ids, seen, links, limit=3):
    """Keep sourced news with complete phrase references; never clip through a tagged phrase."""
    items = []
    for item in raw if isinstance(raw, list) else []:
        kept = clean_item(item, ids, near_ids, seen, links, need_read=True)
        if kept:
            items.append(kept)
        if len(items) == limit:
            break
    return items


def news_matches(facts):
    matches = {m["id"]: m for key in ("next_24_hours", "later_if_needed") for m in facts.get(key, [])}
    matches.update({m["id"]: m for league in facts.get("league_candidates", []) for m in league["matches"]})
    return matches


def clean_blurbs(raw, facts, seen, links):
    """One paragraph per league, trimmed to whole sentences rather than rejected when long."""
    leagues = {group["league_id"]: {m["id"] for m in group["matches"]} for group in facts.get("league_candidates", [])}
    near_ids = {m["id"] for m in facts.get("next_24_hours", [])}
    blurbs = {}
    for blurb in raw if isinstance(raw, list) else []:
        if not isinstance(blurb, dict):
            continue
        league, interest = blurb.get("league_id"), blurb.get("interest")
        if not isinstance(league, str) or league not in leagues or league in blurbs:
            continue
        if type(interest) is not int or not 0 <= interest <= 100:
            continue
        kept = clean_item(blurb, leagues[league], near_ids, seen, links, need_read=False,
                          trim_to=LIMITS["league"], max_len=LIMITS["league"])
        if kept:
            blurbs[league] = dict(kept, league_id=league, interest=interest)
    return list(blurbs.values())


def clean_notes(raw, ids, seen, links):
    """Researched notes: a known fixture, at most one note each, and a page read in this run."""
    notes, dropped = {}, 0
    for n in raw if isinstance(raw, list) else []:
        mid = n.get("match_id") if isinstance(n, dict) else None
        if mid not in ids or mid in notes:
            dropped += 1
            continue
        text = clip(n.get("note") if isinstance(n.get("note"), str) else "", LIMITS["note"])
        sources, _ = cite(n.get("sources"), [mid], seen, links)
        if not text or not read_sources(sources):
            dropped += 1
            continue
        notes[mid] = {"note": text, "sources": sources}
    if dropped:
        log(f"dropped {dropped} note(s) with an unknown or repeated match id or without a page read in this run")
    return notes, dropped


def clean_story(raw, facts, seen, links=None):
    """Validates the research call's tool input against the facts and the pages actually read.
    Returns the news part of story.json, or None when the input is not a publication."""
    if not isinstance(raw, dict):
        return None
    raw = normalize_editorial(raw)
    links = fixture_links(facts) if links is None else links
    ids = set(news_matches(facts))
    near_ids = {m["id"] for m in facts.get("next_24_hours", [])}
    # Keep validation of the older opening format for existing snapshots and comparison fixtures.
    headline_segments = clean_segments(raw.get("headline"), ids)
    headline = "".join(part["text"] for part in headline_segments)
    if len(headline) > 160:
        headline, headline_segments = "", []
    later_reason = clip(raw.get("later_reason", ""), 300)
    lede_items = clean_items(raw.get("lede_items"), ids, near_ids, seen, links)
    forecast_raw = raw.get("forecast")
    forecast_items = clean_items(forecast_raw.get("items") if isinstance(forecast_raw, dict) else None,
                                 ids, near_ids, seen, links)
    notes, dropped = clean_notes(raw.get("notes"), ids, seen, links)
    # Keep complete tagged sentences; never truncate through a fixture reference.
    compact_lede = []
    for item in lede_items:
        if len(" ".join(i["text"] for i in compact_lede + [item])) <= LIMITS["lede"]:
            compact_lede.append(item)
    lede_items = compact_lede
    lead_ids = {mid for item in lede_items for mid in item["match_ids"]}
    if any(mid not in lead_ids for part in headline_segments for mid in part["match_ids"]):
        headline, headline_segments = "", []
    story = {"headline": headline, "headline_segments": headline_segments, "later_reason": later_reason,
             "notes": notes, "_dropped": dropped}
    set_lede(story, lede_items)
    if forecast_items:
        story["forecast"] = {"items": forecast_items}
    story["league_blurbs"] = clean_blurbs(raw.get("league_blurbs"), facts, seen, links)
    known_leagues = {league["league_id"] for league in facts.get("leagues", [])}
    league_order = raw.get("league_order")
    story["league_order"] = list(dict.fromkeys(league for league in league_order
                                               if isinstance(league, str) and league in known_leagues)) if isinstance(league_order, list) else []
    story["blurb_coverage"] = {"written": len(story["league_blurbs"]), "total": len(facts.get("league_candidates", []))}
    return story


def set_lede(story, lede_items):
    sources = {s["url"]: s for item in lede_items for s in item["sources"]}
    story.update(lede_items=lede_items, lede=" ".join(item["text"] for item in lede_items), sources=list(sources.values()))


def missing_news(story, facts):
    """What a publication still lacks: the overview (when there is news to write about), league
    paragraphs and league_order entries."""
    story = story or {}
    return {
        "overview": bool(news_matches(facts)) and not story.get("lede_items"),
        "leagues": sorted({g["league_id"] for g in facts.get("league_candidates", [])}
                          - {b["league_id"] for b in story.get("league_blurbs", [])}),
        "order": sorted({league["league_id"] for league in facts.get("leagues", [])} - set(story.get("league_order", []))),
    }


def repair_request(missing):
    """The tool result that asks for only what is missing, so the repair costs a few paragraphs."""
    asks = []
    if missing["overview"]:
        asks.append("lede_items: the overview, one to three fixture-tagged sentences, each citing a page read in this session")
    if missing["leagues"]:
        asks.append("league_blurbs for " + ", ".join(missing["leagues"]) + ": one paragraph each within 450 characters, "
                    "fixture references within that league; when no reporting applies, write from the supplied facts and "
                    "leave its sources empty")
    if missing["order"]:
        asks.append("league_order: the complete order, including " + ", ".join(missing["order"]))
    return ("The rest of the publication is accepted and will be kept. Call publish_story again with only these parts "
            "filled in, leaving every other field empty ([]): " + "; ".join(asks) + ".")


def merge_news(first, second):
    """Adds a repair's parts to the first publication without replacing anything it accepted."""
    if not second:
        return first
    out = dict(first)
    if not first.get("lede_items") and second.get("lede_items"):
        set_lede(out, second["lede_items"])
        out.update(headline=second.get("headline", ""), headline_segments=second.get("headline_segments", []))
    blurbs = {b["league_id"]: b for b in first.get("league_blurbs", [])}
    for b in second.get("league_blurbs", []):
        blurbs.setdefault(b["league_id"], b)
    out["league_blurbs"] = list(blurbs.values())
    out["league_order"] = list(dict.fromkeys(first.get("league_order", []) + second.get("league_order", [])))
    if not first.get("notes") and second.get("notes"):
        out["notes"] = second["notes"]
    if "forecast" not in first and "forecast" in second:
        out["forecast"] = second["forecast"]
    out["_dropped"] = first.get("_dropped", 0) + second.get("_dropped", 0)
    out["blurb_coverage"] = dict(first["blurb_coverage"], written=len(out["league_blurbs"]))
    return out


def complete_order(story, facts):
    """Appends any league Claude left out, in build.py's order, so the page's priority list is whole."""
    known = [league["league_id"] for league in facts.get("leagues", [])]
    story["league_order"] = list(dict.fromkeys(story.get("league_order", []) + known))


def weighted(parts):
    return round(sum(value * weight for value, (_, weight) in zip(parts, WEIGHTS)), 1)


def clean_rankings(raw, ids, seen, links):
    """Validate ratings and calculate the fixed score without renormalizing against this slate. A
    blurb is trimmed to whole sentences; it keeps the reporting it cites, rests on the fixture's
    ESPN facts when it cites nothing, and is dropped (the rating kept) when it cites a page nobody
    read."""
    ratings = {}
    for rating in raw if isinstance(raw, list) else []:
        if not isinstance(rating, dict):
            continue
        mid = rating.get("match_id")
        if not isinstance(mid, str) or mid not in ids or mid in ratings:
            continue
        parts = [rating.get(key) for key, _ in WEIGHTS]
        if any(type(value) is not int or not 0 <= value <= 100 for value in parts):
            continue
        ratings[mid] = dict(zip((key for key, _ in WEIGHTS), parts), score=weighted(parts))
        blurb = clip(rating.get("blurb") if isinstance(rating.get("blurb"), str) else "", LIMITS["blurb"])
        sources, unverified = cite(rating.get("sources"), [mid], seen, links)
        if not sources and not unverified and mid in links:
            sources = [facts_source(links[mid])]
        if blurb and sources:
            ratings[mid].update(blurb=blurb, sources=sources)
    return ratings


def earlier_ratings(previous, ids):
    """Today's earlier ratings for fixtures still to come, rescored with the current weights and with
    sources as that run verified them (older stories' unread ESPN pages relabelled as facts)."""
    out = {}
    for mid, r in ((previous or {}).get("rankings") or {}).items():
        if mid not in ids or not isinstance(r, dict):
            continue
        parts = [r.get(key) for key, _ in WEIGHTS]
        if any(type(value) is not int or not 0 <= value <= 100 for value in parts):
            continue
        out[mid] = dict(zip((key for key, _ in WEIGHTS), parts), score=weighted(parts))
        sources = [facts_source(s["url"]) if is_facts(s) else {"url": s["url"], "title": s.get("title", "")}
                   for s in r.get("sources") or [] if isinstance(s, dict) and isinstance(s.get("url"), str) and url_key(s["url"])]
        if isinstance(r.get("blurb"), str) and r["blurb"].strip() and sources:
            out[mid].update(blurb=clip(r["blurb"], LIMITS["blurb"]), sources=sources)
    return out


def et_kickoff(iso):
    """'Sat Oct 10, 7:30 AM ET' from an ISO timestamp, or '' when it can't be read."""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    except (AttributeError, TypeError, ValueError):
        return ""
    return t.strftime("%a %b ") + str(t.day) + t.strftime(", %I:%M %p ET").replace(" 0", " ")


def rating_fixture(m):
    """The facts a rating needs, compactly; no URL, since the script attaches each fixture's ESPN page."""
    return {k: v for k, v in dict(id=m["id"], kickoff=et_kickoff(m.get("kickoff_utc")), competition=m.get("competition"),
                                  stage=m.get("stage"), home=m.get("home"), away=m.get("away")).items()
            if v not in ("", None, [], {})}


def rating_context(story):
    """The research a card blurb may use: every note, overview, forecast and league paragraph with the
    pages it read. ESPN facts are not listed; a blurb that rests on them cites nothing."""
    out = []
    for mid, n in (story.get("notes") or {}).items():
        out.append({"match_ids": [mid], "text": n["note"], "sources": [s["url"] for s in read_sources(n["sources"])]})
    for item in story.get("lede_items", []) + (story.get("forecast") or {}).get("items", []) + story.get("league_blurbs", []):
        urls = [s["url"] for s in read_sources(item["sources"])]
        if urls:
            out.append({"match_ids": item["match_ids"], "text": item["text"], "sources": urls})
    return out


def usage_of(message):
    usage = message.usage
    stu = getattr(usage, "server_tool_use", None)
    return {"in": usage.input_tokens or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "out": usage.output_tokens or 0,
            "searches": (getattr(stu, "web_search_requests", 0) or 0) if stu else 0,
            "fetches": (getattr(stu, "web_fetch_requests", 0) or 0) if stu else 0,
            "prompt_max": (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
                          + (getattr(usage, "cache_read_input_tokens", 0) or 0)}


def add_usage(totals, usage):
    """Adds one request's usage to the run's; prompt_max keeps the largest single prompt."""
    for key, value in usage.items():
        totals[key] = max(totals.get(key, 0), value) if key == "prompt_max" else totals.get(key, 0) + value


def rating_prompt(header, fixtures, context=(), scores_only=False):
    """A rating request's text: the clock, this run's research when a blurb may use it, and the fixtures."""
    reporting = "" if scores_only else f"Reporting from this run's research, which a blurb may use and cite:\n{compact(context)}\n\n"
    return f"{header}\n\n{reporting}Rate each of these {len(fixtures)} fixtures exactly once:\n{compact(fixtures)}"


def parse_ratings(text, stop):
    """The ratings list in a structured reply, or [] when it was cut short or refused (output that may
    not match the schema) or isn't JSON."""
    if stop in ("refusal", "max_tokens"):
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    ratings = data.get("ratings") if isinstance(data, dict) else None
    return ratings if isinstance(ratings, list) else []


def rate_chunk(client, model, effort, fixtures, context, header, scores_only=False):
    """One rating request to Claude: structured output, no tools. Returns (ratings as sent, usage, stop
    reason, served model). With scores_only, the three scores alone, from the fixtures' facts (ratings
    mode); otherwise a blurb too, which may use this run's research. Runs on a worker thread, so it
    touches nothing shared; the caller merges the results."""
    with client.beta.messages.stream(
        model=model,
        max_tokens=RATING_MAX_TOKENS,
        system=SCORES_SYSTEM if scores_only else RATING_SYSTEM,
        messages=[{"role": "user", "content": rating_prompt(header, fixtures, context, scores_only)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCORES_SCHEMA if scores_only else RATING_SCHEMA}},
        **request_options(model),
    ) as stream:
        message = stream.get_final_message()
    text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
    return parse_ratings(text, message.stop_reason), usage_of(message), message.stop_reason, message.model


def api_rate_chunk(model, effort, key, fixtures, context, header, scores_only=True):
    """One rating request to an OpenAI or Google model, with the same system prompt, text and schema as
    Claude's ratings mode, through the provider's own structured output. Scores only: the blurbs rest
    on Claude's research, which only the full design runs. Returns what rate_chunk returns, with usage
    in this script's keys (plus the reasoning tokens, which are billed as output)."""
    if not scores_only:
        raise ValueError(f"{model} rates in the ratings design only")
    reply = providers.request(model, effort, SCORES_SYSTEM, rating_prompt(header, fixtures, scores_only=True), key,
                              schema=SCORES_SCHEMA, max_tokens=RATING_MAX_TOKENS)
    u = reply.usage
    usage = {"in": u["in"], "cache_write": u["cache_write"], "cache_read": u["cached"], "out": u["out"],
             "searches": u["searches"], "fetches": u["opens"], "prompt_max": u["prompt_max"], "reasoning": u["reasoning"]}
    return parse_ratings(reply.text, reply.stop), usage, reply.stop, reply.served


def write_ratings(rate, facts, wanted, context, seen, links, totals, scores_only=False):
    """Rates the fixtures in `wanted`, RATING_CHUNK at a time (SCORES_CHUNK with scores_only) on
    RATING_WORKERS threads, then asks once more for any that came back without a rating or, unless
    scores_only, without a blurb. `rate(fixtures, context, header, scores_only)` makes one request
    (rate_chunk or api_rate_chunk with the model filled in). Returns ({fixture ID: rating}, the model
    that served the last request answered, or None)."""
    candidates, wanted = {m["id"]: m for m in facts.get("ranking_candidates", [])}, set(wanted)
    order = [mid for mid in candidates if mid in wanted]
    header = (f"It is {clock(facts['built_at'])} on {facts.get('weekday', '')}, {facts.get('date', '')}, US Eastern time."
              if facts.get("built_at") else "")
    ratings, pending, served = {}, order, None
    for attempt in (1, 2):
        size = SCORES_CHUNK if scores_only else RATING_CHUNK
        chunks = [pending[i:i + size] for i in range(0, len(pending), size)]
        with ThreadPoolExecutor(max_workers=RATING_WORKERS) as pool:
            futures = [pool.submit(rate, [rating_fixture(candidates[mid]) for mid in chunk], context, header, scores_only)
                       for chunk in chunks]
            for chunk, future in zip(chunks, futures):
                try:
                    raw, usage, stop, by = future.result()
                except Exception as e:     # a failed chunk is asked again; its fixtures are not lost
                    log(f"rating request failed: {type(e).__name__}: {e}")
                    continue
                add_usage(totals, usage)
                served = by or served
                if stop in ("refusal", "max_tokens"):
                    log(f"rating request stopped: {stop}")
                for mid, rating in clean_rankings(raw, set(chunk), seen, links).items():
                    if mid not in ratings or ("blurb" in rating and "blurb" not in ratings[mid]):
                        ratings[mid] = rating
        pending = [mid for mid in order if mid not in ratings or not (scores_only or "blurb" in ratings[mid])]
        missing = "a rating" if scores_only else "a rating or a blurb"
        if not pending or attempt == 2:
            break
        log(f"asking again for {len(pending)} fixture(s) without {missing}")
    if pending:
        log(f"ratings incomplete: {len(pending)} fixture(s) without {missing}")
    return ratings, served


def clock(iso):
    """'4:52 pm' in Eastern time, from an ISO timestamp in UTC."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    return t.strftime("%I:%M %p").lstrip("0").lower()


def compact_fixture(m):
    out = {k: v for k, v in m.items()
           if k not in ("id", "source_url", "kickoff_utc", "time_confirmed", "league_id", "default_competition", "state")}
    if m.get("state") == "in":
        out["in_progress"] = True
    if m.get("default_competition") is False:
        out["hidden_by_default"] = True
    return out


def news_view(facts):
    """The research request's view of the facts: each fixture once, compactly, and fixture IDs
    everywhere else. The ratings' candidates are left out; the rating requests get them."""
    fixtures = {}

    def ref(m):
        fixtures.setdefault(m["id"], compact_fixture(m))
        return m["id"]
    view = {k: facts[k] for k in ("date", "weekday", "built_at", "focus_until", "owner_services") if k in facts}
    view["leagues"] = facts.get("leagues", [])
    view["next_24_hours"] = [ref(m) for m in facts.get("next_24_hours", [])]
    view["later_if_needed"] = [ref(m) for m in facts.get("later_if_needed", [])]
    view["league_candidates"] = [{"league_id": g["league_id"], "competition": g.get("competition", ""),
                                  "match_ids": [ref(m) for m in g.get("matches", [])]} for g in facts.get("league_candidates", [])]
    if facts.get("played_today"):
        view["played_today"] = facts["played_today"]
    view["fixtures"] = fixtures
    return view


def user_prompt(facts, budget):
    return (f"Today is {facts['weekday']}, {facts['date']}, in US Eastern time. The household's services are "
            f"{', '.join(facts['owner_services'])}.\n\n"
            f"Here are the candidate matches. {FACTS_GUIDE}\n\n"
            f"{compact(news_view(facts))}\n\n"
            "Write a paragraph for every supplied league, plus up to eight researched match notes. Rank all supplied leagues "
            "in league_order by general viewing interest; the browser separately blends 80% match interest with 20% league priority. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} "
            f"You have up to {budget[0]} web searches and {budget[1]} page reads.")


def earlier_news(previous):
    """The news part of the story published earlier today, compactly, for a refresh to update. Only
    the pages read are listed as sources: text that rested on ESPN's facts shows none, as asked."""
    def urls(sources):
        return [s["url"] for s in sources or [] if isinstance(s, dict) and isinstance(s.get("url"), str) and not is_facts(s)]

    def item(i, *keys):
        return dict({k: i[k] for k in ("segments",) + keys if k in i}, sources=urls(i.get("sources")))
    earlier = {"headline": previous.get("headline_segments", previous.get("headline", "")),
               "lede_items": [item(i) for i in previous.get("lede_items") or [] if isinstance(i, dict)],
               "later_reason": previous.get("later_reason", ""),
               "notes": [{"match_id": mid, "note": n.get("note", ""), "sources": urls(n.get("sources"))}
                         for mid, n in (previous.get("notes") or {}).items() if isinstance(n, dict)]}
    if isinstance(previous.get("forecast"), dict):
        earlier["forecast"] = {"items": [item(i) for i in previous["forecast"].get("items") or [] if isinstance(i, dict)]}
    earlier["league_blurbs"] = [item(b, "league_id", "interest") for b in previous.get("league_blurbs") or [] if isinstance(b, dict)]
    earlier["league_order"] = previous.get("league_order") or []
    return earlier


def refresh_prompt(facts, previous, budget):
    """The update run's request: the story as published earlier today, then the facts as they are now."""
    return (f"It is {clock(facts['built_at'])} on {facts['weekday']}, {facts['date']}, US Eastern time. The household's "
            f"services are {', '.join(facts['owner_services'])}.\n\n"
            f"At {clock(previous['generated_at'])} you published these storylines:\n\n"
            f"{compact(earlier_news(previous))}\n\n"
            "Here are the matches as they stand now, ordered by kickoff. Finished matches are no "
            f"longer listed. {FACTS_GUIDE}\n\n"
            f"{compact(news_view(facts))}\n\n"
            "Update the storylines for this moment rather than starting over. Search only for what may have changed "
            "since they were written: team news, confirmed lineups, injuries and suspensions, and results that change "
            "what is at stake. Drop general club news from the earlier story even if still true; every retained note "
            "must affect a specific upcoming fixture. Keep qualifying notes with their sources exactly as given; revise or replace "
            "the others, and add notes for matches that have become the day's stories. Supply a paragraph for every league, "
            "including later options for filter changes; past results are context, not the lead. Notes are only for matches in the lists "
            "above. Rank every supplied league in league_order by general viewing interest; this supplies the separate 20% league-priority component. "
            "Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} Rewrite it for this moment. "
            f"You have up to {budget[0]} web searches and {budget[1]} page reads. Call publish_story once with the "
            "complete set of notes, kept ones included, and the forecast.")


def earlier_sources(previous):
    """The pages a story published earlier today cited, all checked against that run's own results, so a
    refresh can keep a note and its sources without reading them again. ESPN pages kept as facts were
    never read, so they are not among them."""
    seen = {}
    cited = list(previous.get("sources") or [])
    for n in (previous.get("notes") or {}).values():
        if isinstance(n, dict):
            cited += n.get("sources") or []
    for rating in (previous.get("rankings") or {}).values():
        if isinstance(rating, dict):
            cited += rating.get("sources") or []
    for item in (previous.get("lede_items") or []) + (previous.get("league_blurbs") or []):
        if isinstance(item, dict):
            cited += item.get("sources") or []
    forecast = previous.get("forecast")
    if isinstance(forecast, dict):
        for item in forecast.get("items") or []:
            if isinstance(item, dict):
                cited += item.get("sources") or []
    for s in cited:
        if isinstance(s, dict) and isinstance(s.get("url"), str) and url_key(s["url"]) and not is_facts(s):
            seen.setdefault(url_key(s["url"]), (s["url"], s.get("title", "") or ""))
    return seen


def write_news(client, facts, model, effort, mode, previous, totals, links, seen):
    """Runs the research request. Returns (news or None, served model). Every page the tools return
    is added to `seen` as it arrives, so the ratings can cite it even if the research fails later."""
    budget = BUDGETS[mode]
    prompt = refresh_prompt(facts, previous, budget) if mode == "refresh" else user_prompt(facts, budget)
    messages = [{"role": "user", "content": prompt}]
    served, story, nudged, repaired = model, None, False, False
    for attempt in range(1, MAX_REQUESTS + 1):
        with client.beta.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=messages,
            tools=tools(budget),
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},   # continuations resend the research so far; cached at a tenth of the price
            **REQUEST_OPTIONS,
        ) as stream:
            message = stream.get_final_message()
        served = message.model
        add_usage(totals, usage_of(message))
        collect_sources(message.content, seen)
        log(f"request {attempt}: stop={message.stop_reason} model={served} pages_seen={len(seen)}")

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            log(f"refused: {getattr(details, 'category', None)}")
            break
        if message.stop_reason == "max_tokens":
            log("hit max_tokens; a publish_story call in this response may be truncated, so it is not used")
            break
        call = next((b for b in message.content if getattr(b, "type", "") == "tool_use" and b.name == "publish_story"), None)
        if call is not None:
            raw = call.input
            if isinstance(raw, str):        # eager input streaming hands back unparsed text when cut short
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raw = None
            found = clean_story(raw, facts, seen, links)
            story = merge_news(story, found) if story else found
            missing = missing_news(story, facts)
            # A league left out of league_order alone is appended in build.py's order (complete_order)
            # rather than paid for with another request.
            if story and (missing["overview"] or missing["leagues"]) and not repaired and attempt < MAX_REQUESTS:
                repaired = True
                log(f"asking for the missing parts: overview={missing['overview']}, "
                    f"{len(missing['leagues'])} league blurb(s), {len(missing['order'])} league_order entries")
                messages.append({"role": "assistant", "content": message.content})
                messages.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": call.id, "is_error": True, "content": repair_request(missing)}]})
                continue
            if story and (missing["overview"] or missing["leagues"]):
                log(f"news incomplete: overview={not missing['overview']}, {len(missing['leagues'])} league(s) without a paragraph")
            break
        if message.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": message.content})
            continue
        if message.stop_reason == "end_turn" and not nudged:
            messages.append({"role": "assistant", "content": message.content})
            messages.append({"role": "user", "content": "Please call publish_story now with what you found."})
            nudged = True
            continue
        log(f"stopped without publishing: {message.stop_reason}")
        break
    return story, served


def rating_window(facts):
    """Ratings mode's fixtures: those kicking off within RATING_WINDOW_HOURS of the build, widened a day
    at a time until there are MIN_RATED or no more. Returns (fixtures, hours). A kickoff that can't be
    read is kept, as a refresh keeps it due: better rated than lost."""
    candidates = facts.get("ranking_candidates", [])
    built = datetime.fromisoformat(facts["built_at"].replace("Z", "+00:00"))

    def before(m, limit):
        try:
            return datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00")) < limit
        except (KeyError, AttributeError, ValueError):
            return True
    hours = RATING_WINDOW_HOURS
    while True:
        window = [m for m in candidates if before(m, built + timedelta(hours=hours))]
        if len(window) >= MIN_RATED or len(window) == len(candidates):
            return window, hours
        hours += 24


def claude_client():
    import anthropic   # imported here so reuse, no-key and other-provider paths work without the package
    return anthropic.Anthropic(max_retries=3)


def write_story(facts, model, effort, mode, previous, totals):
    """Runs the research, then the ratings, and returns (story dict or None, served model), adding the
    usage of every request to `totals` as it goes, so a run that fails partway still reports what it
    spent. In a refresh the ratings of fixtures more than RERATE_HOURS away are kept from earlier today.
    Ratings mode runs on any model in providers.MODELS; the research is Claude's."""
    links = fixture_links(facts)
    if mode == "ratings":
        # Three scores per fixture and nothing else: no research, no overview or blurbs, no web search.
        provider = providers.MODELS.get(model, "anthropic")
        rate = (partial(rate_chunk, claude_client(), model, effort) if provider == "anthropic"
                else partial(api_rate_chunk, model, effort, os.environ.get(providers.KEY_NAMES[provider], "")))
        window, hours = rating_window(facts)
        log(f"rating {len(window)} fixtures kicking off within {hours} hours with {model}")
        rankings, served = write_ratings(rate, facts, [m["id"] for m in window], [], {}, links, totals, scores_only=True)
        story = {"lede_items": [], "notes": {}, "league_blurbs": [], "league_order": [], "_dropped": 0, "rankings": rankings,
                 "ranking_coverage": {"rated": len(rankings), "total": len(window), "carried": 0}, "window_hours": hours}
        return (story if rankings else None), served or model
    client = claude_client()
    seen = earlier_sources(previous) if mode == "refresh" and previous else {}
    try:
        news, served = write_news(client, facts, model, effort, mode, previous, totals, links, seen)
    except Exception as e:     # the ratings can still be written
        log(f"research request failed: {type(e).__name__}: {e}")
        news, served = None, model
    story = {"headline": "", "headline_segments": [], "lede": "", "lede_items": [], "sources": [], "notes": {},
             "later_reason": "", "league_blurbs": [], "league_order": [], "_dropped": 0,
             "blurb_coverage": {"written": 0, "total": len(facts.get("league_candidates", []))}}
    if news is not None:
        story = news
    elif mode == "refresh" and previous:
        log("keeping the news written earlier today")
        story.update({k: previous[k] for k in ("headline", "headline_segments", "lede", "lede_items", "sources", "notes",
                                               "later_reason", "forecast", "league_blurbs", "league_order", "blurb_coverage")
                      if k in previous})
    complete_order(story, facts)

    candidates = facts.get("ranking_candidates", [])
    ids = {m["id"] for m in candidates}
    earlier = earlier_ratings(previous, ids) if mode == "refresh" else {}
    built = datetime.fromisoformat(facts["built_at"].replace("Z", "+00:00")) if facts.get("built_at") else None
    soon = built + timedelta(hours=RERATE_HOURS) if built else None

    def due(m):
        if m["id"] not in earlier or "blurb" not in earlier[m["id"]] or soon is None:
            return True
        try:
            return datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00")) < soon
        except (KeyError, AttributeError, ValueError):
            return True
    wanted = [m["id"] for m in candidates if due(m)]
    log(f"rating {len(wanted)} of {len(candidates)} fixtures" + (f"; keeping {len(candidates) - len(wanted)} rated earlier today" if earlier else ""))
    rate = partial(rate_chunk, client, model, effort)
    fresh = write_ratings(rate, facts, wanted, rating_context(story), seen, links, totals)[0] if wanted else {}
    rankings, carried = {}, 0
    for m in candidates:
        new, old = fresh.get(m["id"]), earlier.get(m["id"])
        chosen = new if new and ("blurb" in new or not old or "blurb" not in old) else (old or new)
        if chosen:
            rankings[m["id"]] = chosen
            carried += chosen is old
    story["rankings"] = rankings
    story["ranking_coverage"] = {"rated": len(rankings), "total": len(candidates), "carried": carried}
    story["match_blurb_coverage"] = {"written": sum("blurb" in r for r in rankings.values()), "total": len(candidates)}
    has_news = bool(story["notes"] or story["lede_items"] or story.get("forecast") or story["league_blurbs"])
    return (story if has_news or rankings else None), served


def cost_of(totals, model):
    """Estimated cost in USD at list prices (providers.py), or None for a model without a recorded price."""
    return providers.cost(model, {"in": totals.get("in", 0), "cached": totals.get("cache_read", 0),
                                  "cache_write": totals.get("cache_write", 0), "out": totals.get("out", 0),
                                  "searches": totals.get("searches", 0), "opens": totals.get("fetches", 0),
                                  "prompt_max": totals.get("prompt_max", 0)})


def report_cost(totals, served, model, effort, mode, seconds):
    """Logs the run's usage and cost and adds them to the run summary. A served model with prices of its
    own (a server-side fallback) is priced as served; a dated snapshot of the requested model, which has
    none, at the requested model's prices."""
    priced_as = served if served in providers.MODELS else model
    cost = cost_of(totals, priced_as)
    priced = f"about ${cost:.4f} at {priced_as} list prices" if cost is not None else "no price table for this model"
    reasoning = f", {totals['reasoning']:,} of them reasoning" if totals.get("reasoning") else ""
    line = (f"Storylines ({mode}): {served} at {effort} effort; input {totals['in']:,} tokens fresh, "
            f"{totals['cache_write']:,} cache-written, {totals['cache_read']:,} cache-read; output {totals['out']:,} "
            f"tokens{reasoning}; {totals['searches']} searches, {totals['fetches']} page reads; {seconds:.0f}s; {priced}")
    log(line)
    summary(line)
    return cost


def ai_settings(path):
    """settings.toml's [ai] table as providers.check_ai reads it ({"enabled", "design", "model", "effort",
    "provider"}), or None when the file can't be read or the table is malformed. build.py refuses to
    publish on a malformed file, so None means something changed between the two; the caller then
    spends nothing."""
    try:
        with open(path, "rb") as f:
            table = tomllib.load(f).get("ai")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        return None
    return providers.check_ai(table)[0]


def written_by(story, config):
    """Whether `story` came from the design, model and effort settings.toml names now. One from another
    (the published story, on the day the model is switched) is not today's, so the next run writes."""
    kinds = ("ratings",) if config["design"] == "ratings" else ("full", "refresh")
    return (story.get("kind") in kinds and (story.get("requested_model") or story.get("model")) == config["model"]
            and story.get("effort") == config["effort"])


def load_previous(path):
    try:
        with open(path, encoding="utf-8") as f:
            prev = normalize_editorial(json.load(f))
        return prev if isinstance(prev, dict) and prev.get("version") == 1 else None
    except (OSError, ValueError, TypeError):
        return None


def save(path, story):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=1)


def story_age_hours(story, now):
    try:
        written = datetime.fromisoformat(story["generated_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return None
    return (now - written).total_seconds() / 3600


def choose_mode(requested, todays, now, design="full", switched=False):
    """The mode to run. keep republishes the published story, unless `switched`: settings.toml names a
    different design, model or effort than wrote it, so this run (the push that switched) writes.
    The full design: daily writes the day's story once (full without a story for today, else keep);
    auto also refreshes it (keep while under MIN_GAP_HOURS old, else refresh); a refresh without a
    story for today runs as full. The ratings design: every mode that writes rates, and daily and auto
    rate once a day."""
    if requested == "keep":
        if switched:
            return ("ratings" if design == "ratings" else "full"), "settings.toml names another design or model than the published story's"
        return "keep", ""
    if design == "ratings":
        if requested in ("daily", "auto"):
            return ("keep", "today's ratings are written; they are written once a day") if todays \
                else ("ratings", "no ratings for today yet")
        return "ratings", ("" if requested == "ratings" else f"the ratings design rates when asked for {requested}")
    if requested == "daily":
        return ("keep", "today's storylines are written; they are written once a day") if todays \
            else ("full", "no storylines for today yet")
    if requested == "auto":
        age = story_age_hours(todays, now) if todays else None
        if age is None:
            return "full", "no storylines for today yet"
        if age < MIN_GAP_HOURS:
            return "keep", f"today's storylines are {age * 60:.0f} minutes old"
        return "refresh", f"today's storylines are {age:.1f} hours old"
    if requested == "refresh" and not todays:
        return "full", "no storylines for today yet, so this refresh writes them from scratch"
    return requested, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--facts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--previous", help="the story.json currently published, to reuse or fall back on")
    ap.add_argument("--mode", choices=("daily", "auto", "full", "refresh", "keep", "ratings"), default="keep")
    ap.add_argument("--model", help=f"default: settings.toml's [ai] model, else {DEFAULT_MODEL}")
    ap.add_argument("--effort", choices=ALL_EFFORTS, help="default: settings.toml's [ai] effort, else by mode")
    ap.add_argument("--usage-out", help="write the run's tokens, searches, cost and time here as JSON")
    ap.add_argument("--settings", default=SETTINGS_PATH, help="the settings file with the [ai] table (default: settings.toml)")
    ap.add_argument("--ignore-switch", action="store_true",
                    help="ignore settings.toml's [ai] table, for measuring configurations: --mode, --model and --effort decide")
    args = ap.parse_args()

    # Before anything that writes: with AI off even the published story must not be carried forward.
    config = None if args.ignore_switch else ai_settings(args.settings)
    if not args.ignore_switch and not (config and config["enabled"]):
        why = "AI is switched off in settings.toml" if config else "settings.toml's [ai] table can't be read"
        log(f"{why}: no storylines and no API calls")
        summary(f"Storylines: none; {why}.")
        return 0

    with open(args.facts, encoding="utf-8") as f:
        facts = json.load(f)
    previous = load_previous(args.previous) if args.previous else None
    current = (lambda st: written_by(st, config)) if config else (lambda st: True)
    todays = (previous if previous and previous.get("date") == facts["date"] and previous.get("generated_at") and current(previous)
              else None)
    switched = bool(config and previous and not current(previous))

    mode, why = choose_mode(args.mode, todays, datetime.now(timezone.utc), config["design"] if config else "full", switched)
    if why:
        log(f"mode {mode}: {why}")
    if mode == "keep":
        kept = previous if args.mode == "keep" else todays
        if kept:
            save(args.out, kept)
        log("kept the current storylines" if kept else "no storylines to keep")
        summary("Storylines: kept the current ones." if kept else "Storylines: none to keep.")
        return 0
    model = args.model or (config["model"] if config else DEFAULT_MODEL)
    if model not in providers.MODELS:
        log(f"{model} isn't one this script supports ({', '.join(providers.MODELS)}); using {DEFAULT_MODEL}")
        model = DEFAULT_MODEL
    elif mode != "ratings" and model not in providers.RESEARCH_MODELS:
        log(f"{model} takes part in ratings mode only (it can't run the research); using {DEFAULT_MODEL}")
        model = DEFAULT_MODEL
    provider = providers.MODELS[model]
    effort = args.effort or (config["effort"] if config and model == config["model"] else DEFAULT_EFFORT[mode])
    if effort not in providers.EFFORTS[provider]:
        log(f"effort {effort!r} isn't one {model} takes ({', '.join(providers.EFFORTS[provider])}); using {DEFAULT_EFFORT[mode]}")
        effort = DEFAULT_EFFORT[mode]
    key_name = providers.KEY_NAMES[provider]
    if not os.environ.get(key_name):
        if todays:
            save(args.out, todays)
        log(f"no {key_name}; " + ("reused today's storylines" if todays else "no storylines this run"))
        summary(f"Storylines: skipped, no {key_name} secret.")
        return 0

    started = time.monotonic()
    totals = {"in": 0, "cache_write": 0, "cache_read": 0, "out": 0, "searches": 0, "fetches": 0, "prompt_max": 0}
    try:
        story, served = write_story(facts, model, effort, mode, todays, totals)
    except Exception as e:     # any failure here must leave the page publishable
        log(f"storyline request failed: {type(e).__name__}: {e}")
        story, served = None, model
    seconds = time.monotonic() - started
    cost = report_cost(totals, served, model, effort, mode, seconds) if any(totals.values()) else None
    published = bool(story)
    if args.usage_out:
        coverage = (story or {}).get("ranking_coverage", {})
        save(args.usage_out, {"mode": mode, "model": model, "served": served, "provider": provider, "effort": effort,
                              "budget": {"searches": BUDGETS[mode][0], "page_reads": BUDGETS[mode][1]}, "usage": totals,
                              "cost_usd": round(cost, 4) if cost is not None else None, "seconds": round(seconds, 1),
                              "published": published, "notes": len(story["notes"]) if story else 0,
                              "forecast": bool(story and story.get("forecast")),
                              "rated": coverage.get("rated", 0), "carried": coverage.get("carried", 0),
                              "dropped": story["_dropped"] if story else None})
    if published:
        story.pop("_dropped", None)
        # model is who answered (a fallback or a dated snapshot shows here); requested_model is what
        # settings.toml asked for, which the page names and the next run compares with its settings.
        story.update(version=1, date=facts["date"], model=served, requested_model=model, effort=effort, kind=mode,
                     services=facts.get("owner_service_ids") or [], focus_until=facts.get("focus_until"),
                     generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        save(args.out, story)
        log(f"wrote {len(story['league_blurbs'])} league blurbs, {len(story['notes'])} notes and "
            f"{len(story['rankings'])} ratings ({story['ranking_coverage']['carried']} kept from earlier today) in {seconds:.0f}s")
        return 0
    if todays:
        save(args.out, todays)
        log("kept today's earlier storylines")
    summary("Storylines: none written this run; see the log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
