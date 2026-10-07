#!/usr/bin/env python3
"""Writes the day's storylines for Soccer Outlook with Claude and web search.

build.py --facts supplies every match in the next 24 hours and later candidates as a fallback.
Claude researches all competitions without prioritizing broadcast access. It publishes separately
referenced phrases for the headline, lede and forecast, plus match notes. Every sentence cites verified
sources and exact fixture IDs. The browser derives team, league and broadcaster tags from those
fixtures and dims phrases excluded by the visitor's filters.

Later stories are permitted only when research finds nothing pertinent in the next 24 hours.
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

SYSTEM = """You write the daily storylines for Soccer Outlook, a soccer schedule with visitor-controlled competition and service filters. Assume every match is viewable while selecting news; broadcast access must never decide editorial importance. The page already lists kickoff times, channels, table positions, recent form and top scorers. Your part is what a knowledgeable friend would add: why a match matters, what is at stake, who is missing or returning, rivalries, records and milestones, a manager under pressure, a debut.

Research with web search before writing, and read a full article when a search snippet is not enough. Prefer recent reporting from established outlets: clubs and federations, major newspapers, broadcasters, wire services. State only what you read in this session. If you cannot confirm something, leave it out rather than guess, and never predict results or invent lineups, injuries or quotes. Do not restate the schedule data as news.

Write plainly, in present tense, for a reader in the United States. When you have what you need, call publish_story once."""

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
                     "description": "One short sentence, about 180-300 characters, split at independently filterable phrases. Concatenating all text fragments must reproduce the sentence exactly. Every mentioned team or league and its related claims must reference its fixtures."},
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
        "required": ["headline", "lede_items", "later_reason", "notes", "forecast"],
        "properties": {
            "headline": {"type": "array", "items": SEGMENT_SCHEMA,
                         "description": "A headline, sentence case, about 80 characters, with separately tagged phrases. It must concern only fixtures in the lede."},
            "lede_items": {"type": "array", "maxItems": 3, "items": EDITORIAL_SCHEMA,
                           "description": "Up to three sentences on the most pertinent story in the next 24 hours."},
            "later_reason": {"type": "string", "description": "Empty when covering the next 24 hours. Otherwise briefly explain why research found no pertinent story sooner; low stature or absent major leagues do not establish that."},
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
                "properties": {"items": {"type": "array", "maxItems": 3, "items": EDITORIAL_SCHEMA}},
            },
        },
    },
}

FOCUS_GUIDE = ("The focus is the rolling next 24 hours, from built_at to focus_until, including matches live now. "
               "Research next_24_hours first, across all its leagues regardless of stature. Find what is pertinent "
               "there. Only if that research finds nothing of interest should the headline, lede or forecast use "
               "later_if_needed, starting with the soonest pertinent fixture; explain the decision in later_reason. "
               "Assume every match is viewable: do not favor the owner's services or omit matches elsewhere. "
               "The visitor's filters decide what fits their lineup. Do not pad a short story with distant fixtures. "
               "Use segments in the headline, lede_items and forecast items to tag exact phrases with fixture IDs. "
               "The page derives team, league and broadcaster tags from those IDs. Tag a team/league name and "
               "its related claim separately from unrelated fixtures; neutral joining words get []. For example, "
               "a sentence naming MLS and the Premier League must have separate tagged segments for each league, "
               "so excluding MLS dims only its words, without altering the rest of the sentence. Preserve spaces "
               "and punctuation across segments. Never mix near and later fixtures "
               "in one item. Avoid 'your services', 'today' and 'tomorrow' in editorial text: selections and the "
               "clock change after publication. Give actual dates and Eastern times when needed. ")

FORECAST_GUIDE = (FOCUS_GUIDE + "Write up to three short forecast items with researched context and source URLs. "
                  "An empty list is better than canned commentary. The browser separately shows factual counts, "
                  "coverage and the next kickoff, so do not repeat those or the lede. An absent fixture is not "
                  "evidence of a break, a quiet day or a weekend return. Never invent an explanation.")



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


def clean_story(raw, facts, seen):
    """Validates the tool input against the facts and the pages actually read. Returns the story
    dict for story.json, or None when what is left is not worth showing."""
    if not isinstance(raw, dict):
        return None
    matches = {m["id"]: m for key in ("next_24_hours", "later_if_needed") for m in facts.get(key, [])}
    ids = set(matches)
    headline_segments = clean_segments(raw.get("headline"), ids)
    headline = "".join(part["text"] for part in headline_segments)
    if len(headline) > 160:
        headline, headline_segments = "", []
    near_ids = {m["id"] for m in facts.get("next_24_hours", [])}
    later_reason = clip(raw.get("later_reason", ""), 300)
    lede_items = clean_items(raw.get("lede_items"), ids, near_ids, seen, bool(later_reason))
    forecast_raw = raw.get("forecast")
    forecast_items = clean_items(forecast_raw.get("items") if isinstance(forecast_raw, dict) else None,
                                 ids, near_ids, seen, bool(later_reason))
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
    # One researched near-term item is enough to keep the whole opening focused on this window.
    if (any(set(item["match_ids"]) <= near_ids for item in lede_items + forecast_items)
            or set(notes) & near_ids):
        lede_items = [item for item in lede_items if set(item["match_ids"]) <= near_ids]
        forecast_items = [item for item in forecast_items if set(item["match_ids"]) <= near_ids]
        notes = {mid: note for mid, note in notes.items() if mid in near_ids}
        later_reason = ""
    if not later_reason:
        notes = {mid: note for mid, note in notes.items() if mid in near_ids}
    lead_ids = {mid for item in lede_items for mid in item["match_ids"]}
    if any(mid not in lead_ids for part in headline_segments for mid in part["match_ids"]):
        headline, headline_segments = "", []
    sources = {s["url"]: s for item in lede_items for s in item["sources"]}
    story = {"headline": headline, "headline_segments": headline_segments, "lede": " ".join(item["text"] for item in lede_items),
             "lede_items": lede_items, "sources": list(sources.values()), "notes": notes,
             "later_reason": later_reason, "_dropped": dropped}
    if forecast_items:
        story["forecast"] = {"items": forecast_items}
    return story


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


def clean_items(raw, ids, near_ids, seen, allow_later):
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
        if (near and not set(refs) <= near_ids) or (not near and not allow_later):
            continue
        sources = verified(item.get("sources"), seen, 3)
        if sources:
            items.append({"text": text, "match_ids": refs, "segments": parts, "sources": sources})
        if len(items) == 3:
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
            "it must not affect editorial selection. 'played_today', when "
            "present, gives the day's notable results so far, for context.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Write storylines for up to eight pertinent matches, with no minimum count. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} "
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
    return (f"It is {clock(facts['built_at'])} on {facts['weekday']}, {facts['date']}, US Eastern time. The household's "
            f"services are {', '.join(facts['owner_services'])}.\n\n"
            f"At {clock(previous['generated_at'])} you published these storylines:\n\n"
            f"{json.dumps(earlier, ensure_ascii=False, indent=1)}\n\n"
            "Here are the matches as they stand now, ordered by kickoff. Finished matches are no "
            "longer listed; 'played_today', when present, gives the day's notable results so far.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Update the storylines for this moment rather than starting over. Search only for what may have changed "
            "since they were written: team news, confirmed lineups, injuries and suspensions, and results that change "
            "what is at stake. Keep any note that still holds, with its sources exactly as given; revise or replace "
            "the others, and add notes for matches that have become the day's stories. Rewrite the headline and lede "
            "so they focus on the next 24 hours from this build; past results are context, not the lead. Notes are only for matches in the lists "
            "above. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} Rewrite it for this moment. "
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
    for item in previous.get("lede_items") or []:
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
            return clean_story(raw, facts, seen), served
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
    published = bool(story and (story["notes"] or story["lede_items"] or story.get("forecast")))
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
        log(f"wrote {len(story['notes'])} notes in {seconds:.0f}s")
        return 0
    if todays:
        save(args.out, todays)
        log("kept today's earlier storylines")
    summary("Storylines: none written this run; see the log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
