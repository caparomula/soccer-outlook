#!/usr/bin/env python3
"""Writes the day's storylines for Soccer Outlook with Claude and web search.

build.py --facts writes the matches worth talking about (today and tomorrow, on the owner's services
and elsewhere, plus the biggest of the week) with only what ESPN reports. This script hands those
facts to Claude Opus 5.5 with the web search and web fetch tools, asks for a headline, a short lede
and a note on each notable match, and receives them through one tool call, publish_story.

The page shows what it writes, so every claim has to be traceable. The model is told to state only
what it read in this session, and the script keeps only the source links that appeared in the search
and fetch results of this run; a note left with no verified source is dropped. The output is
story.json, published beside the page, which loads it if it is less than 30 hours old.

Modes:
  auto   reuse the previous story when it was written for today's date, else write a new one
  force  write a new one; on failure keep the previous story if it is still for today

Without ANTHROPIC_API_KEY the script reuses today's previous story if there is one and otherwise
writes nothing, so the page simply shows no storylines. It exits 0 unless its arguments are wrong:
a failed story must never block the schedule from being published.

Usage: python story.py --facts work/facts.json --out site/story.json [--previous old-story.json]
                       [--mode auto|force]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

MODEL = "claude-opus-5-5"
EFFORT = "high"                      # intelligence-sensitive writing; Opus 5.5 defaults to medium
MAX_TOKENS = 32000                    # streamed, so a large ceiling costs nothing unless used
MAX_REQUESTS = 6                      # pause_turn continuations plus one nudge to publish
SEARCH_LIMIT = 12                     # web searches per run, at about a cent each
FETCH_LIMIT = 6                       # full-page reads per run
# Opus 5.5 list prices in USD, for the cost log only: input, cache write (1.25x), cache read (0.1x),
# output, and one web search.
PRICE_IN, PRICE_CACHE_WRITE, PRICE_CACHE_READ, PRICE_OUT, PRICE_SEARCH = 4.00 / 1e6, 5.00 / 1e6, 0.40 / 1e6, 20.00 / 1e6, 0.01

SYSTEM = """You write the daily storylines for Soccer Outlook, a personal web page that shows one household which soccer matches they can watch on their streaming services. The page already lists kickoff times, channels, table positions, recent form and top scorers. Your part is what a knowledgeable friend would add: why a match matters, what is at stake, who is missing or returning, rivalries, records and milestones, a manager under pressure, a debut.

Research with web search before writing, and read a full article when a search snippet is not enough. Prefer recent reporting from established outlets: clubs and federations, major newspapers, broadcasters, wire services. State only what you read in this session. If you cannot confirm something, leave it out rather than guess, and never predict results or invent lineups, injuries or quotes. Do not restate the schedule data as news.

Write plainly, in present tense, for a reader in the United States. When you have what you need, call publish_story once."""

PUBLISH_TOOL = {
    "name": "publish_story",
    "description": "Publish the day's storylines to the page. Call exactly once, after your research.",
    "strict": True,
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["headline", "lede", "lede_sources", "notes"],
        "properties": {
            "headline": {"type": "string", "description": "Headline for the day's soccer, sentence case, at most about 80 characters."},
            "lede": {"type": "string", "description": "Two or three sentences on the day ahead, at most about 400 characters, leading with matches on the household's services."},
            "lede_sources": {"type": "array", "items": {"type": "string"}, "description": "URLs, from your research in this session, that support the lede."},
            "notes": {
                "type": "array",
                "description": "Four to eight notes, one per notable match.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["match_id", "note", "sources"],
                    "properties": {
                        "match_id": {"type": "string", "description": "The match's id from the facts."},
                        "note": {"type": "string", "description": "One or two sentences, at most about 260 characters."},
                        "sources": {"type": "array", "items": {"type": "string"}, "description": "One to three URLs, from your research in this session, that support the note."},
                    },
                },
            },
        },
    },
}

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": SEARCH_LIMIT},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": FETCH_LIMIT},
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
    ids = {m["id"] for key in ("today_and_tomorrow_on_owner_services", "today_and_tomorrow_elsewhere",
                               "later_this_week_biggest") for m in facts.get(key, [])}
    headline = clip(raw.get("headline", ""), 110)
    lede = clip(raw.get("lede", ""), 520)
    if not headline or not lede:
        return None
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
    return {"headline": headline, "lede": lede, "sources": verified(raw.get("lede_sources"), seen, 6), "notes": notes}


def user_prompt(facts):
    return (f"Today is {facts['weekday']}, {facts['date']}, in US Eastern time. The household's services are "
            f"{', '.join(facts['owner_services'])}.\n\n"
            "Here are the candidate matches, ranked by a rough measure of stature. 'watch_on' says where the "
            "household can watch; it is absent when the match is not on their services.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Write storylines for the four to eight most interesting matches of today and tomorrow, favoring "
            "ones the household can watch but including anything unmissable elsewhere. A big match later in "
            "the week can earn a note if it is the story of the week. Use the match ids exactly as given.")


def write_story(facts):
    """Runs the research and returns (story dict or None, served model)."""
    import anthropic   # imported here so reuse and no-key paths work without the package

    client = anthropic.Anthropic(max_retries=3)
    messages = [{"role": "user", "content": user_prompt(facts)}]
    seen, served = {}, MODEL
    totals = {"in": 0, "cache_write": 0, "cache_read": 0, "out": 0, "searches": 0, "fetches": 0}
    nudged = False
    for attempt in range(1, MAX_REQUESTS + 1):
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=messages,
            tools=TOOLS,
            output_config={"effort": EFFORT},
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
            report_cost(totals, served)
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
    report_cost(totals, served)
    return None, served


def report_cost(totals, served):
    cost = (totals["in"] * PRICE_IN + totals["cache_write"] * PRICE_CACHE_WRITE + totals["cache_read"] * PRICE_CACHE_READ
            + totals["out"] * PRICE_OUT + totals["searches"] * PRICE_SEARCH)
    line = (f"Storylines: {served}; input {totals['in']:,} tokens fresh, {totals['cache_write']:,} cache-written, "
            f"{totals['cache_read']:,} cache-read; output {totals['out']:,} tokens; {totals['searches']} searches, "
            f"{totals['fetches']} page reads; about ${cost:.2f} at Opus 5.5 list prices")
    log(line)
    summary(line)


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
    ap.add_argument("--mode", choices=("auto", "force"), default="auto")
    args = ap.parse_args()

    with open(args.facts, encoding="utf-8") as f:
        facts = json.load(f)
    previous = load_previous(args.previous) if args.previous else None
    todays = previous if previous and previous.get("date") == facts["date"] else None

    if args.mode == "auto" and todays:
        save(args.out, todays)
        log("reused today's storylines")
        summary("Storylines: reused this morning's.")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if todays:
            save(args.out, todays)
        log("no ANTHROPIC_API_KEY; " + ("reused today's storylines" if todays else "no storylines this run"))
        summary("Storylines: skipped, no ANTHROPIC_API_KEY secret.")
        return 0

    started = time.monotonic()
    try:
        story, served = write_story(facts)
    except Exception as e:     # any failure here must leave the page publishable
        log(f"storyline request failed: {type(e).__name__}: {e}")
        story, served = None, MODEL
    if story and story["notes"]:
        story.update(version=1, date=facts["date"], model=served,
                     generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        save(args.out, story)
        log(f"wrote {len(story['notes'])} notes in {time.monotonic() - started:.0f}s")
        return 0
    if todays:
        save(args.out, todays)
        log("kept today's earlier storylines")
    summary("Storylines: none written this run; see the log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
