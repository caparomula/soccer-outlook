#!/usr/bin/env python3
"""Writes the day's storylines for Soccer Outlook with Claude and web search.

build.py --facts supplies matches with known service coverage in the next 24 hours and later fallback candidates.
Claude writes one interest-rated paragraph per league, plus forecast items and match notes, all with separately
referenced phrases. Every paragraph cites researched reporting or supplied ESPN match facts, with
sources and exact fixture IDs. The browser derives team, league and broadcaster tags from those
fixtures and dims phrases excluded by the visitor's filters.

Later stories are retained as alternatives; the browser prefers relevant near-term news after filtering.
The browser supplies factual schedule counts and coverage independently. Old untagged stories keep
their match notes but cannot supply opening commentary; the next research run supplies tagged text.

Modes, one per kind of build:
  full     research the day from scratch (the early-morning build)
  refresh  update today's story for the moment: the model gets the earlier story and its sources,
           searches only for what may have changed (team news, lineups, results), and keeps what
           still holds. A smaller search budget makes it cheaper than a full run. Without a story
           for today it runs as full. (The midday and evening builds.)
  keep     republish the current story unchanged, whatever its date, and never call the API (builds
           after a code change: the news hasn't changed, and a push should never cost anything; the
           page shows a story only on the day it was written for)
On failure, full and refresh keep the previous story if it is for today.

Model and effort come from --model and --effort, else STORY_MODEL and STORY_EFFORT (STORY_REFRESH_EFFORT
for refresh runs), else the defaults below. --usage-out writes the run's tokens, searches, cost and
time as JSON, for comparing configurations (.github/workflows/compare-storylines.yml).

Without ANTHROPIC_API_KEY the script reuses today's previous story if there is one and otherwise
writes nothing, so the page simply shows no storylines. It exits 0 unless its arguments are wrong:
a failed story must never block the schedule from being published.

Usage: python story.py --facts work/facts.json --out site/story.json [--previous old-story.json]
                       [--mode full|refresh|keep] [--model ID] [--effort LEVEL] [--usage-out FILE]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DEFAULT_MODEL = "claude-opus-5-5"
# Effort per mode: both at Opus 5.5's own default. On research work Anthropic's published curves are
# nearly flat, medium matching high's accuracy at 70-87% of the cost (Optimizing for cost and
# intelligence, checked 2026-10-07). .github/workflows/compare-storylines.yml checks that on this
# workload; the repository variables STORY_EFFORT and STORY_REFRESH_EFFORT override these.
DEFAULT_EFFORT = {"full": "medium", "refresh": "medium"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
BUDGETS = {"full": (12, 6), "refresh": (5, 2)}   # (web searches, full-page reads) per run
MAX_TOKENS = 64000                    # a backstop only: streamed, and billed only when used
MAX_REQUESTS = 6                      # pause_turn continuations plus one nudge to publish
# List prices in USD per token, for the cost line only (checked 2026-10-07 against
# https://platform.claude.com/docs/en/about-claude/pricing): input, 5-minute cache write, cache read,
# output. Both models take the dynamic-filtering web tools, effort and server-side fallbacks, which
# this script relies on; Haiku 4.5 takes none of them, so it isn't offered.
PRICES = {
    "claude-opus-5-5": (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6),
    "claude-sonnet-5-5": (2.00e-6, 2.50e-6, 0.20e-6, 10.00e-6),
}
PRICE_SEARCH = 0.01                   # per web search on any model; web fetch costs only its tokens

SYSTEM = """You write the daily storylines for Soccer Outlook, a soccer schedule with visitor-controlled competition and service filters. News serves upcoming matches available through the visitor's selected services. The news candidate lists contain only fixtures with listed or usual service coverage; ranking_candidates separately contains the full upcoming slate. Prioritize the default lineup for the opening; visitors can select other services and the browser filters the text accordingly. The page already lists kickoff times, channels, table positions, recent form and top scorers. Cover only facts that directly affect a specific upcoming fixture: the stakes, player availability, likely selection supported by reporting, a relevant matchup, or a scheduling change. An upcoming international break belongs only when explaining its effect on a listed fixture. Exclude general club news, financial investigations, ownership stories, or unrelated managerial controversy. Mentioning a team that has a fixture is not enough: explain the concrete match connection.

Research with web search before writing, and read a full article when a search snippet is not enough. Prefer recent reporting from established outlets: clubs and federations, major newspapers, broadcasters, wire services. For news, state only what you read in this session or what the supplied ESPN facts establish. If you cannot confirm something, leave it out rather than guess, and never predict results or invent lineups, injuries or quotes. Do not merely repeat kickoff times and channels as news. Supplied ESPN match facts may support useful fixture-specific context when fresh reporting is unavailable.

Also rate every fixture in ranking_candidates using the fixed rubric supplied by the user. Ratings are editorial judgments from the fixture facts and established appeal, separate from sourced news.

Write plainly, in present tense, for a reader in the United States. When you have what you need, call publish_story with the complete news and ratings."""

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
                    "description": "One to three URLs read in this session supporting this sentence."},
    },
}
PUBLISH_TOOL = {
    "name": "publish_story",
    "description": "Publish the storylines. Call exactly once, after your research.",
    "strict": True,
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["lede_items", "league_order", "league_blurbs", "notes", "forecast", "ranked_matches"],
        "properties": {
            "league_order": {"type": "array", "items": {"type": "string"},
                             "description": "Every supplied leagues league_id exactly once, ordered by general viewing interest for a US soccer audience. Consider overall quality, appeal and stakes, independently of today's filters. Visitors can reorder this default. The browser blends 80% match interest with 20% league priority, so do not bake this preference into the match ratings."},
            "lede_items": {
                "type": "array", "items": EDITORIAL_SCHEMA,
                "description": "A general overview in one short paragraph, at most 450 characters total. One to three tagged sentences connecting the day's available fixtures and pertinent match news. Prioritize current-day fixtures on the default services and enabled competitions; look further ahead when none qualify. Every claim must be tied to supplied fixtures and sources. Do not repeat the individual match blurbs.",
            },
            "league_blurbs": {
                "type": "array", "description": "One sourced, independently usable paragraph for EVERY league_candidates entry. Rate its news interest independently of service filters. Do not omit later leagues.",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["league_id", "interest", "segments", "sources"],
                          "properties": {
                              "league_id": {"type": "string", "description": "Exact league_candidates league_id."},
                              "interest": {"type": "integer", "description": "0–100 editorial interest of this fixture-specific story: routine useful context to unusually compelling stakes/news. Not match quality or an outcome probability."},
                              "segments": {"type": "array", "items": SEGMENT_SCHEMA,
                                           "description": "One short paragraph, one to three sentences, at most 450 characters total. Tag each match-specific phrase. Reference only supplied upcoming fixtures from this league."},
                              "sources": {"type": "array", "items": {"type": "string"},
                                          "description": "URLs read in this research or supplied ESPN source_url links supporting only facts supplied in the input."},
                          }},
            },
            "ranked_matches": {
                "type": "array", "description": "Rate EVERY ranking_candidates fixture exactly once on the fixed rubric, not just the highlights. Ratings are independent of services and filters.",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["match_id", "popularity", "gameplay", "impact", "blurb", "sources"],
                          "properties": {
                              "match_id": {"type": "string"},
                              "popularity": {"type": "integer", "description": "0–100: audience appeal on the fixed global scale."},
                              "gameplay": {"type": "integer", "description": "0–100: expected football quality and competitiveness, without predicting a result."},
                              "impact": {"type": "integer", "description": "0–100: competitive stakes of this particular fixture, supported by stage/table context."},
                              "blurb": {"type": "string", "description": "One concise explanation of this fixture's appeal, matchup or stakes, at most 260 characters. Use researched news or supplied team/form/table/stage facts; no invented news or generic hype. Every fixture needs its own useful blurb so changing filters can reveal any match."},
                              "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs read during research, or this fixture's supplied ESPN source_url for supplied facts only, supporting the blurb."},
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
                        "sources": {"type": "array", "items": {"type": "string"}, "description": "One to three URLs read in this session supporting the note."},
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
               "scheduling facts and cite its supplied ESPN source_url. Those URLs support supplied facts only; "
               "external claims still require a source read in this session. Do not simply repeat kickoff times "
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

RANKING_GUIDE = ("Also rate EVERY fixture in ranking_candidates, exactly once. This is an editorial assessment, "
                 "separate from sourced news: use the supplied teams, form, tables and stage, established audience "
                 "appeal and any verified research. Do not invent injuries, lineups, stakes or predicted scores. "
                 "Give separate integer scores from 0 to 100 for popularity, gameplay and impact. Use the same "
                 "absolute scale across all competitions and days: 20=limited appeal/quality/stakes, 40=routine, "
                 "60=notably appealing/competitive/meaningful, 80=exceptional, 95=rare global event or decisive final. "
                 "Judge each dimension independently; a famous club does not automatically mean compelling play "
                 "or high stakes. Missing evidence must not inflate scores. The combined score is 25% popularity, "
                 "35% gameplay and 40% impact. The browser selects the top three passing filters; there is no minimum score. Do NOT normalize "
                 "scores to this slate, force any fixture over 80, or change scores for service availability. "
                 "Return the full set even when no match is exceptional. Research is concentrated on news and "
                 "the strongest candidates; it is not necessary to search separately for every routine match. "
                 "Also give EVERY rated match its own blurb and sources. In at most 260 characters, explain the "
                 "matchup, stakes or relevant news using facts actually supplied or researched. Cite that fixture's "
                 "ESPN source_url when relying on supplied form, table or stage facts. These are the card blurbs; "
                 "they must be specific and stand on their own. Do not merely restate teams, time, channel or score.")



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
                    if getattr(r, "type", "") == "web_search_result" and getattr(r, "url", ""):
                        seen.setdefault(url_key(r.url), (r.url, getattr(r, "title", "") or ""))
        elif btype == "web_fetch_tool_result":
            result = getattr(block, "content", None)
            if getattr(result, "type", "") == "web_fetch_result" and getattr(result, "url", ""):
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


def verified(urls, seen, limit):
    out = []
    for u in urls or []:
        hit = seen.get(url_key(u)) if isinstance(u, str) else None
        if hit and hit[0] not in [s["url"] for s in out]:
            out.append({"url": hit[0], "title": hit[1][:200]})
        if len(out) >= limit:
            break
    return out


def news_matches(facts):
    matches = {m["id"]: m for key in ("next_24_hours", "later_if_needed") for m in facts.get(key, [])}
    matches.update({m["id"]: m for league in facts.get("league_candidates", []) for m in league["matches"]})
    return matches


def clean_blurbs(raw, facts, seen):
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
        items = clean_items([blurb], leagues[league], near_ids, seen)
        if items and len(items[0]["text"]) <= 450:
            blurbs[league] = dict(items[0], league_id=league, interest=interest)
    return list(blurbs.values())


def clean_story(raw, facts, seen):
    """Validates the tool input against the facts and the pages actually read. Returns the story
    dict for story.json, or None when what is left is not worth showing."""
    if not isinstance(raw, dict):
        return None
    matches = news_matches(facts)
    seen = dict(seen)
    for m in list(matches.values()) + facts.get("ranking_candidates", []):
        if m.get("source_url"):
            seen.setdefault(url_key(m["source_url"]), (m["source_url"], "ESPN match facts"))
    ids = set(matches)
    # Keep validation of the older opening format for existing snapshots and comparison fixtures.
    headline_segments = clean_segments(raw.get("headline"), ids)
    headline = "".join(part["text"] for part in headline_segments)
    if len(headline) > 160:
        headline, headline_segments = "", []
    near_ids = {m["id"] for m in facts.get("next_24_hours", [])}
    later_reason = clip(raw.get("later_reason", ""), 300)
    lede_items = clean_items(raw.get("lede_items"), ids, near_ids, seen)
    forecast_raw = raw.get("forecast")
    forecast_items = clean_items(forecast_raw.get("items") if isinstance(forecast_raw, dict) else None,
                                 ids, near_ids, seen)
    notes, dropped = {}, 0
    for n in raw.get("notes") or []:
        if not isinstance(n, dict) or n.get("match_id") not in ids or n.get("match_id") in notes:
            dropped += 1
            continue
        text = clip(n.get("note", ""), 320)
        sources = verified(n.get("sources"), seen, 3)
        if not text or not sources:
            dropped += 1
            continue
        notes[n["match_id"]] = {"note": text, "sources": sources}
    if dropped:
        log(f"dropped {dropped} note(s) with an unknown match id or no verified source")
    # Keep complete tagged sentences; never truncate through a fixture reference.
    compact_lede = []
    for item in lede_items:
        if len(" ".join(i["text"] for i in compact_lede + [item])) <= 450:
            compact_lede.append(item)
    lede_items = compact_lede
    lead_ids = {mid for item in lede_items for mid in item["match_ids"]}
    if any(mid not in lead_ids for part in headline_segments for mid in part["match_ids"]):
        headline, headline_segments = "", []
    sources = {s["url"]: s for item in lede_items for s in item["sources"]}
    story = {"headline": headline, "headline_segments": headline_segments, "lede": " ".join(item["text"] for item in lede_items),
             "lede_items": lede_items, "sources": list(sources.values()), "notes": notes,
             "later_reason": later_reason, "_dropped": dropped}
    if forecast_items:
        story["forecast"] = {"items": forecast_items}
    story["league_blurbs"] = clean_blurbs(raw.get("league_blurbs"), facts, seen)
    known_leagues = {league["league_id"] for league in facts.get("leagues", [])}
    league_order = raw.get("league_order")
    story["league_order"] = list(dict.fromkeys(league for league in league_order
                                               if isinstance(league, str) and league in known_leagues)) if isinstance(league_order, list) else []
    story["blurb_coverage"] = {"written": len(story["league_blurbs"]), "total": len(facts.get("league_candidates", []))}
    story["rankings"] = clean_rankings(raw.get("ranked_matches"), facts, seen)
    story["ranking_coverage"] = {"rated": len(story["rankings"]), "total": len(facts.get("ranking_candidates", []))}
    story["match_blurb_coverage"] = {"written": sum(bool(r.get("blurb")) for r in story["rankings"].values()),
                                     "total": len(facts.get("ranking_candidates", []))}
    return story


def clean_rankings(raw, facts, seen=None):
    """Validate ratings and calculate the fixed score without renormalizing against this slate."""
    ids = {m["id"] for m in facts.get("ranking_candidates", [])}
    ratings = {}
    for rating in raw if isinstance(raw, list) else []:
        if not isinstance(rating, dict):
            continue
        mid = rating.get("match_id")
        if not isinstance(mid, str) or mid not in ids or mid in ratings:
            continue
        parts = [rating.get(key) for key in ("popularity", "gameplay", "impact")]
        if any(type(value) is not int or not 0 <= value <= 100 for value in parts):
            continue
        ratings[mid] = {key: value for key, value in zip(("popularity", "gameplay", "impact"), parts)}
        ratings[mid]["score"] = round(parts[0] * .25 + parts[1] * .35 + parts[2] * .4, 1)
        blurb = rating.get("blurb")
        sources = verified(rating.get("sources"), seen or {}, 3)
        if isinstance(blurb, str) and blurb.strip() and len(blurb) <= 260 and sources:
            ratings[mid].update(blurb=blurb.strip(), sources=sources)
    return ratings


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


def clean_items(raw, ids, near_ids, seen, limit=3):
    """Keep sourced text with complete phrase references; never clip through a tagged phrase."""
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parts = clean_segments(item.get("segments"), ids)
        refs = list(dict.fromkeys(mid for part in parts for mid in part["match_ids"]))
        text = "".join(part["text"] for part in parts)
        if not refs or not text.strip() or len(text) > 520:
            continue
        near = set(refs) & near_ids
        if near and not set(refs) <= near_ids:
            continue
        sources = verified(item.get("sources"), seen, 3)
        if sources:
            items.append({"text": text, "match_ids": refs, "segments": parts, "sources": sources})
        if len(items) == limit:
            break
    return items


def clock(iso):
    """'4:52 pm' in Eastern time, from an ISO timestamp in UTC."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    return t.strftime("%I:%M %p").lstrip("0").lower()


def user_prompt(facts, budget):
    return (f"Today is {facts['weekday']}, {facts['date']}, in US Eastern time. The household's services are "
            f"{', '.join(facts['owner_services'])}.\n\n"
            "Here are the candidate matches. 'watch_on' records where the default household could watch; "
            "prioritize it for the opening. 'played_today', when "
            "present, gives the day's notable results so far, for context.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Write a paragraph for every supplied league, plus up to eight researched match notes. Rank all supplied leagues "
            "in league_order by general viewing interest; the browser separately blends 80% match interest with 20% league priority. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} {RANKING_GUIDE} "
            f"You have up to {budget[0]} web searches and {budget[1]} page reads.")


def refresh_prompt(facts, previous, budget):
    """The update run's request: the story as published earlier today, then the facts as they are now."""
    earlier = {"headline": previous.get("headline_segments", previous.get("headline", "")), "lede": previous.get("lede", ""),
               "lede_items": previous.get("lede_items", []), "later_reason": previous.get("later_reason", ""),
               "notes": [{"match_id": mid, "note": n.get("note", ""), "sources": [s["url"] for s in n.get("sources") or []
                                                                               if isinstance(s, dict) and s.get("url")]}
                         for mid, n in (previous.get("notes") or {}).items() if isinstance(n, dict)]}
    if isinstance(previous.get("forecast"), dict):
        earlier["forecast"] = previous["forecast"]
    earlier["league_blurbs"] = previous.get("league_blurbs") or []
    earlier["league_order"] = previous.get("league_order") or []
    if isinstance(previous.get("rankings"), dict):
        earlier["rankings"] = previous["rankings"]
    return (f"It is {clock(facts['built_at'])} on {facts['weekday']}, {facts['date']}, US Eastern time. The household's "
            f"services are {', '.join(facts['owner_services'])}.\n\n"
            f"At {clock(previous['generated_at'])} you published these storylines:\n\n"
            f"{json.dumps(earlier, ensure_ascii=False, indent=1)}\n\n"
            "Here are the matches as they stand now, ordered by kickoff. Finished matches are no "
            "longer listed; 'played_today', when present, gives the day's notable results so far.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Update the storylines for this moment rather than starting over. Search only for what may have changed "
            "since they were written: team news, confirmed lineups, injuries and suspensions, and results that change "
            "what is at stake. Drop general club news from the earlier story even if still true; every retained note "
            "must affect a specific upcoming fixture. Keep qualifying notes with their sources exactly as given; revise or replace "
            "the others, and add notes for matches that have become the day's stories. Supply a paragraph for every league, "
            "including later options for filter changes; past results are context, not the lead. Notes are only for matches in the lists "
            "above. Rank every supplied league in league_order by general viewing interest; this supplies the separate 20% league-priority component. "
            "Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} {RANKING_GUIDE} Rewrite it for this moment. "
            f"You have up to {budget[0]} web searches and {budget[1]} page reads. Call publish_story once with the "
            "complete set of notes, kept ones included, and the forecast.")


def earlier_sources(previous):
    """The pages a story published earlier today cited, all checked against that run's own results, so a
    refresh can keep a note and its sources without reading them again."""
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
        if isinstance(s, dict) and isinstance(s.get("url"), str) and url_key(s["url"]):
            seen.setdefault(url_key(s["url"]), (s["url"], s.get("title", "") or ""))
    return seen


def write_story(facts, model, effort, mode, previous, totals):
    """Runs the research and returns (story dict or None, served model), adding the usage of every
    request to `totals` as it goes, so a run that fails partway still reports what it spent."""
    import anthropic   # imported here so reuse and no-key paths work without the package

    client = anthropic.Anthropic(max_retries=3)
    budget = BUDGETS[mode]
    prompt = refresh_prompt(facts, previous, budget) if mode == "refresh" else user_prompt(facts, budget)
    messages = [{"role": "user", "content": prompt}]
    seen = earlier_sources(previous) if mode == "refresh" else {}
    served = model
    nudged = False
    publication_retried = False
    for attempt in range(1, MAX_REQUESTS + 1):
        with client.beta.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=messages,
            tools=tools(budget),
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},   # continuations resend the research so far; cached at a tenth of the price
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
        served = message.model
        usage = message.usage
        totals["in"] += usage.input_tokens or 0
        totals["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        totals["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        totals["out"] += usage.output_tokens or 0
        stu = getattr(usage, "server_tool_use", None)
        if stu:
            totals["searches"] += getattr(stu, "web_search_requests", 0) or 0
            totals["fetches"] += getattr(stu, "web_fetch_requests", 0) or 0
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
            story = clean_story(raw, facts, seen)
            missing = sorted({m["id"] for m in facts.get("ranking_candidates", [])}
                             - set(story.get("rankings", {}) if story else {}))
            missing_leagues = sorted({group["league_id"] for group in facts.get("league_candidates", [])}
                                     - {item["league_id"] for item in (story or {}).get("league_blurbs", [])})
            missing_blurbs = sorted({m["id"] for m in facts.get("ranking_candidates", [])}
                                    - {mid for mid, rating in (story or {}).get("rankings", {}).items() if rating.get("blurb")})
            missing_overview = bool(news_matches(facts)) and not (story or {}).get("lede_items")
            missing_order = sorted({league["league_id"] for league in facts.get("leagues", [])}
                                   - set((story or {}).get("league_order", [])))
            if story and (missing or missing_leagues or missing_blurbs or missing_overview or missing_order) and not publication_retried and attempt < MAX_REQUESTS:
                publication_retried = True
                log(f"requesting {len(missing)} ratings, {len(missing_blurbs)} match blurbs, {len(missing_leagues)} league blurbs; missing overview={missing_overview}")
                messages.append({"role": "assistant", "content": message.content})
                messages.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": call.id, "is_error": True,
                    "content": "The publication is incomplete. Call publish_story again with the complete overview, ratings, match blurbs and league_blurbs. "
                               "Missing/invalid rating IDs: " + ", ".join(missing) + ". Missing/invalid league blurbs: "
                               + ", ".join(missing_leagues) + ". Write a sourced paragraph for each, even beyond 24 hours. "
                               "If no fresh reporting is available, use supplied match facts and cite its ESPN source_url. "
                               "Keep each paragraph within 450 characters and its fixture references within that league. "
                               "Missing/invalid match blurbs: " + ", ".join(missing_blurbs) + ". Each ranked_match needs a specific blurb "
                               "of at most 260 characters and verified sources (or its supplied ESPN source_url). "
                               "Missing league_order entries: " + ", ".join(missing_order) + ". "
                               + ("Supply lede_items as a sourced, fixture-tagged overview paragraph. " if missing_overview else ""),
                }]})
                continue
            if missing:
                log(f"ranking coverage incomplete: {len(missing)} fixtures remain unrated")
            if missing_leagues:
                log(f"blurb coverage incomplete: {len(missing_leagues)} leagues remain without a paragraph")
            if missing_blurbs or missing_overview:
                log(f"editorial coverage incomplete: {len(missing_blurbs)} match blurbs missing; missing overview={missing_overview}")
            return story, served
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
    return None, served


def cost_of(totals, model):
    """Estimated cost in USD at list prices, or None for a model this script has no prices for."""
    prices = PRICES.get(model)
    if not prices:
        return None
    p_in, p_write, p_read, p_out = prices
    return (totals["in"] * p_in + totals["cache_write"] * p_write + totals["cache_read"] * p_read + totals["out"] * p_out
            + totals["searches"] * PRICE_SEARCH)


def report_cost(totals, served, effort, mode, seconds):
    cost = cost_of(totals, served)
    priced = f"about ${cost:.2f} at {served} list prices" if cost is not None else "no price table for this model"
    line = (f"Storylines ({mode}): {served} at {effort} effort; input {totals['in']:,} tokens fresh, "
            f"{totals['cache_write']:,} cache-written, {totals['cache_read']:,} cache-read; output {totals['out']:,} "
            f"tokens; {totals['searches']} searches, {totals['fetches']} page reads; {seconds:.0f}s; {priced}")
    log(line)
    summary(line)
    return cost


def load_previous(path):
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
        return prev if isinstance(prev, dict) and prev.get("version") == 1 else None
    except (OSError, ValueError, TypeError):
        return None


def save(path, story):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--facts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--previous", help="the story.json currently published, to reuse or fall back on")
    ap.add_argument("--mode", choices=("full", "refresh", "keep"), default="keep")
    ap.add_argument("--model", help=f"default: STORY_MODEL, else {DEFAULT_MODEL}")
    ap.add_argument("--effort", choices=EFFORTS, help="default: STORY_EFFORT or STORY_REFRESH_EFFORT, else by mode")
    ap.add_argument("--usage-out", help="write the run's tokens, searches, cost and time here as JSON")
    args = ap.parse_args()

    with open(args.facts, encoding="utf-8") as f:
        facts = json.load(f)
    previous = load_previous(args.previous) if args.previous else None
    todays = previous if previous and previous.get("date") == facts["date"] and previous.get("generated_at") else None

    if args.mode == "keep":
        if previous:
            save(args.out, previous)
        log("kept the current storylines" if previous else "no storylines to keep")
        summary("Storylines: kept the current ones." if previous else "Storylines: none to keep.")
        return 0
    mode = "refresh" if args.mode == "refresh" and todays else "full"
    if args.mode != mode:
        log("no storylines for today yet, so this refresh writes them from scratch")
    model = args.model or os.environ.get("STORY_MODEL") or DEFAULT_MODEL
    if model not in PRICES:
        log(f"{model} isn't one this script supports ({', '.join(PRICES)}); using {DEFAULT_MODEL}")
        model = DEFAULT_MODEL
    effort_var = "STORY_REFRESH_EFFORT" if mode == "refresh" else "STORY_EFFORT"
    effort = args.effort or os.environ.get(effort_var) or DEFAULT_EFFORT[mode]
    if effort not in EFFORTS:
        log(f"effort {effort!r} isn't one of {', '.join(EFFORTS)}; using {DEFAULT_EFFORT[mode]}")
        effort = DEFAULT_EFFORT[mode]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if todays:
            save(args.out, todays)
        log("no ANTHROPIC_API_KEY; " + ("reused today's storylines" if todays else "no storylines this run"))
        summary("Storylines: skipped, no ANTHROPIC_API_KEY secret.")
        return 0

    started = time.monotonic()
    totals = {"in": 0, "cache_write": 0, "cache_read": 0, "out": 0, "searches": 0, "fetches": 0}
    try:
        story, served = write_story(facts, model, effort, mode, todays, totals)
    except Exception as e:     # any failure here must leave the page publishable
        log(f"storyline request failed: {type(e).__name__}: {e}")
        story, served = None, model
    seconds = time.monotonic() - started
    cost = report_cost(totals, served, effort, mode, seconds) if any(totals.values()) else None
    published = bool(story and (story["notes"] or story["lede_items"] or story.get("forecast") or story.get("league_blurbs") or story.get("rankings")))
    if args.usage_out:
        save(args.usage_out, {"mode": mode, "model": model, "served": served, "effort": effort,
                              "budget": {"searches": BUDGETS[mode][0], "page_reads": BUDGETS[mode][1]}, "usage": totals,
                              "cost_usd": round(cost, 4) if cost is not None else None, "seconds": round(seconds, 1),
                              "published": published, "notes": len(story["notes"]) if story else 0,
                              "forecast": bool(story and story.get("forecast")),
                              "dropped": story["_dropped"] if story else None})
    if published:
        story.pop("_dropped", None)
        story.update(version=1, date=facts["date"], model=served, effort=effort, kind=mode,
                     services=facts.get("owner_service_ids") or [], focus_until=facts.get("focus_until"),
                     generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        save(args.out, story)
        log(f"wrote {len(story['league_blurbs'])} league blurbs, {len(story['notes'])} notes and {len(story['rankings'])} ratings in {seconds:.0f}s")
        return 0
    if todays:
        save(args.out, todays)
        log("kept today's earlier storylines")
    summary("Storylines: none written this run; see the log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
