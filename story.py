#!/usr/bin/env python3
"""Writes the day's storylines for Soccer Outlook with Claude and web search.

build.py --facts writes the matches worth talking about (today and tomorrow, on the owner's services
and elsewhere, plus the biggest of the week, and the day's notable results so far) with only what
ESPN reports. This script hands those facts to Claude with the web search and web fetch tools, asks
for a headline, a short lede and a note on each notable match, and receives them through one tool
call, publish_story. The same call carries the page's forecast: a label for the kind of day, a
sentence or two on its shape and one on tomorrow and the week ahead, written from the schedule in the
facts (build.py's schedule_by_day), not from the web. The page sets its own live line, with scores and
what is still to come, between the two, since that changes by the minute.

The page shows what it writes, so every claim has to be traceable. The model is told to state only
what it read in this session, and the script keeps only the source links that appeared in the search
and fetch results of this run; a note left with no verified source is dropped. The forecast rests on
the schedule the page itself lists, so it carries no links, and the model is told to leave news to the
notes. The output is story.json, published beside the page, which shows it only on the day it was
written for. It records the lineup it was written for, and a viewer with another lineup gets the
page's own forecast instead, since "on your services" would be someone else's.

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
        "required": ["headline", "lede", "lede_sources", "notes", "forecast"],
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
            "forecast": {
                "type": "object",
                "description": "The page's viewing forecast, written from the schedule in the facts.",
                "additionalProperties": False,
                "required": ["label", "today", "ahead"],
                "properties": {
                    "label": {"type": "string", "description": "Two to four words naming the kind of day, for the page's top line beside the weekday: 'International break', 'Champions League night', 'Full club weekend', 'Quiet midweek'."},
                    "today": {"type": "string", "description": "One or two sentences, at most about 300 characters, on the shape of today's soccer: what kind of day it is, which competitions carry it, which major leagues are off and why."},
                    "ahead": {"type": "string", "description": "One or two sentences, at most about 300 characters, on tomorrow and the rest of the week: when the major leagues return, and the pick of the week with its day, time and service."},
                },
            },
        },
    },
}

FORECAST_GUIDE = ("Also write the page's forecast, which sits below the storylines as a practical guide to the viewing "
                  "week. Write it from 'schedule_by_day' and the match lists, which give every competition's matches "
                  "by Eastern day, how many are on the household's services and when the first kicks off; your research "
                  "can say why a league is off (an international break, a cup round), but news belongs in the notes, "
                  "and the forecast should not repeat the lede. The page follows 'today' with a live line of its own "
                  "listing what is on now and still to come on the household's services, with times and channels, so "
                  "don't list today's slate: say what kind of day it is. Give times in Eastern time, as the facts do, "
                  "and name a service only as 'watch_on' gives it.")



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
    story = {"headline": headline, "lede": lede, "sources": verified(raw.get("lede_sources"), seen, 6), "notes": notes,
             "_dropped": dropped}
    forecast = clean_forecast(raw.get("forecast"))
    if forecast:
        story["forecast"] = forecast
    else:
        log("no usable forecast in the story; the page will compose its own")
    return story


def clean_forecast(raw):
    """The forecast with each part trimmed to what the page has room for, or None unless all three
    parts are there: a label past forty characters is a sentence, not a label, and half a forecast
    beside the page's own live line would read worse than the page's own forecast."""
    if not isinstance(raw, dict):
        return None
    part = lambda key: raw[key] if isinstance(raw.get(key), str) else ""
    label = re.sub(r"\s+", " ", part("label")).strip().rstrip(".")
    today, ahead = clip(part("today"), 420), clip(part("ahead"), 420)
    if not label or len(label) > 40 or not today or not ahead:
        return None
    return {"label": label, "today": today, "ahead": ahead}


def clock(iso):
    """'4:52 pm' in Eastern time, from an ISO timestamp in UTC."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    return t.strftime("%I:%M %p").lstrip("0").lower()


def user_prompt(facts, budget):
    return (f"Today is {facts['weekday']}, {facts['date']}, in US Eastern time. The household's services are "
            f"{', '.join(facts['owner_services'])}.\n\n"
            "Here are the candidate matches, ranked by a rough measure of stature. 'watch_on' says where the "
            "household can watch; it is absent when the match is not on their services. 'played_today', when "
            "present, gives the day's notable results so far, for context.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Write storylines for the four to eight most interesting matches of today and tomorrow, favoring "
            "ones the household can watch but including anything unmissable elsewhere. A big match later in "
            "the week can earn a note if it is the story of the week. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} "
            f"You have up to {budget[0]} web searches and {budget[1]} page reads.")


def refresh_prompt(facts, previous, budget):
    """The update run's request: the story as published earlier today, then the facts as they are now."""
    earlier = {"headline": previous.get("headline", ""), "lede": previous.get("lede", ""),
               "lede_sources": [s["url"] for s in previous.get("sources") or [] if isinstance(s, dict) and s.get("url")],
               "notes": [{"match_id": mid, "note": n.get("note", ""), "sources": [s["url"] for s in n.get("sources") or []
                                                                               if isinstance(s, dict) and s.get("url")]}
                         for mid, n in (previous.get("notes") or {}).items() if isinstance(n, dict)]}
    if isinstance(previous.get("forecast"), dict):
        earlier["forecast"] = previous["forecast"]
    return (f"It is {clock(facts['built_at'])} on {facts['weekday']}, {facts['date']}, US Eastern time. The household's "
            f"services are {', '.join(facts['owner_services'])}.\n\n"
            f"At {clock(previous['generated_at'])} you published these storylines:\n\n"
            f"{json.dumps(earlier, ensure_ascii=False, indent=1)}\n\n"
            "Here are the matches as they stand now, ranked by a rough measure of stature. Finished matches are no "
            "longer listed; 'played_today', when present, gives the day's notable results so far.\n\n"
            f"{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
            "Update the storylines for this moment rather than starting over. Search only for what may have changed "
            "since they were written: team news, confirmed lineups, injuries and suspensions, and results that change "
            "what is at stake. Keep any note that still holds, with its sources exactly as given; revise or replace "
            "the others, and add notes for matches that have become the day's stories. Rewrite the headline and lede "
            "so they read right for now; a notable result can lead the lede. Notes are only for matches in the lists "
            "above. Use the match ids exactly as given. "
            f"{FORECAST_GUIDE} Rewrite it for this moment: 'today' covers what is left of the day. "
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
    published = bool(story and story["notes"])
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
                     services=facts.get("owner_service_ids") or [],
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
