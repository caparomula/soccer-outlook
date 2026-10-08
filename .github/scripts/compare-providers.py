#!/usr/bin/env python3
"""Compares AI providers on Soccer Outlook's AI jobs, on the day's real fixtures, publishing nothing.

  ratings   the three scores story.py's ratings mode asks Claude for (popularity, gameplay and impact,
            0 to 100) for every fixture in its window, asked of each model with the same system
            prompt, fixtures and JSON schema, through story.py's own requests (rate_chunk for Claude,
            api_rate_chunk for OpenAI and Google), so it measures exactly what the daily run sends.
            As in story.py, fixtures left without a valid rating are asked for once more.
  research  one blurb of at most 260 characters for each of a few featured fixtures, researched with
            the provider's own web search (Claude: web search and web fetch; OpenAI: web_search;
            Google: Grounding with Google Search), from the same instructions and facts. The blurbs
            come back as JSON in the reply's text, asked of every model the same way, because not
            every model accepts a response schema together with its search tool.
  overview  one plain paragraph of at most 450 characters for the top of the page, about the whole slate
            in the time frame the top three come from (the next 24 hours, longer when that holds fewer
            than three matches) across every competition and service, since every visitor reads it
            whatever they follow, researched and returned the same way as the blurbs, with its sources.

What it measures, all mechanically: cost from each API's own usage report at providers.py's list prices,
time, searches, and for ratings how far each model's order agrees with the others', with the ratings
the page publishes now and with the page's own Outlook score. For research and the overview, every
URL cited is checked twice: whether the provider's own search returned it in that response (one it did not
return was recalled or made up, and story.py would never show it) and whether it loads now.

A conflict of interest: Claude wrote this comparison of Claude with its competitors. So nothing here
scores the writing: the report lists every blurb, and the results file feeds a review page that
hides which model wrote what. Agreement is measured against the other providers' consensus as well
as against the Claude ratings the page publishes now, which favour a model that thinks like Claude.

Spending is capped. Calls run one at a time, cheapest first, and a task starts only while the amount
spent plus a generous estimate of that task stays within --budget. A provider whose key is missing
is skipped. OpenAI and Google requests are tried again after a 429 or 5xx, twice, and never after a
timeout, which may have been billed; Claude's follow the Anthropic SDK's retries, as story.py's do.

Usage: compare-providers.py --facts FACTS --page PAGE [--published STORY] --ratings CONFIGS
                            --research CONFIGS [--overview CONFIGS] [--fixtures IDS] [--budget USD]
                            --out RESULTS
CONFIGS are provider:model:effort, separated by spaces; provider is anthropic, openai or google.
The Markdown report goes to stdout, progress to stderr.
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import providers  # noqa: E402
import story  # noqa: E402
Reply = providers.Reply      # the tests build replies through it

_spec = importlib.util.spec_from_file_location("compare_storylines", Path(__file__).with_name("compare-storylines.py"))
storylines = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(storylines)

PROVIDERS = tuple(providers.PROVIDER_NAMES)
KEYS = providers.KEY_NAMES
EFFORTS = providers.EFFORTS
# Ratings only: the models that can't search the web here (providers.SEARCH_MODELS says why).
RATINGS_ONLY = {(p, m) for m, p in providers.MODELS.items() if m not in providers.SEARCH_MODELS}
GROUNDING_HOST = story.GROUNDING_HOST
RESEARCH_FIXTURES = 6
RESEARCH_SEARCHES, RESEARCH_FETCHES = 8, 4      # Claude's web search and web fetch; OpenAI's max_tool_calls is their sum
RESEARCH_MAX_TOKENS = 32000
BLURB_LIMIT = story.LIMITS["blurb"]
OVERVIEW_LIMIT = story.LIMITS["lede"]           # the page's overview paragraph
OVERVIEW_SYSTEM, overview_view, overview_prompt = story.OVERVIEW_SYSTEM, story.overview_view, story.overview_prompt
RESEARCH_SYSTEM, research_prompt = story.RESEARCH_SYSTEM, story.research_prompt

def log(msg):
    print(msg, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    effort: str

    @property
    def name(self):
        return f"{self.model} at {self.effort}"


def parse_configs(text, task):
    """CONFIGS for `task` as [Config]; raises ValueError listing every entry that can't run."""
    configs, problems = [], []
    if (text or "").strip().lower() == "none":       # the workflow's way to skip a task: GitHub fills an empty input with its default
        return []
    for entry in (text or "").split():
        parts = entry.split(":")
        if len(parts) != 3:
            problems.append(f"{entry}: expected provider:model:effort")
            continue
        cfg = Config(*parts)
        if cfg.provider not in PROVIDERS:
            problems.append(f"{entry}: provider must be one of {', '.join(PROVIDERS)}")
        elif cfg.effort not in EFFORTS[cfg.provider]:
            problems.append(f"{entry}: {cfg.provider} effort must be one of {', '.join(EFFORTS[cfg.provider])}")
        elif providers.MODELS.get(cfg.model) != cfg.provider:
            problems.append(f"{entry}: no list price recorded for {cfg.model} at {cfg.provider}, so its spending can't be capped")
        elif task != "ratings" and (cfg.provider, cfg.model) in RATINGS_ONLY:
            problems.append(f"{entry}: {cfg.model} can't search the web here, so it takes part in ratings only")
        elif cfg in configs:
            problems.append(f"{entry}: listed twice")
        else:
            configs.append(cfg)
    if problems:
        raise ValueError(f"{task}: " + "; ".join(problems))
    return configs


def price_card(cfg):
    """(input, output, per search) in USD, from providers.py's tables."""
    if cfg.provider == "anthropic":
        p = providers.ANTHROPIC_PRICES[cfg.model]
        return p[0], p[3], providers.ANTHROPIC_SEARCH
    table, search = ((providers.OPENAI_PRICES, providers.OPENAI_SEARCH) if cfg.provider == "openai"
                     else (providers.GEMINI_PRICES, providers.GEMINI_SEARCH))
    p = table[cfg.model]
    return p[0], p[2], search


def cost_of(cfg, usage):
    """USD at list prices for usage in providers.cost's keys."""
    return providers.cost(cfg.model, usage)


# Reading replies, checking links and asking Claude with web search are story.py's, so the comparison
# measures what the daily run does.
USAGE_KEYS, add_usage, story_usage = story.REPLY_USAGE_KEYS, story.sum_usage, story.reply_usage
match_key, location, check_link, without_links = story.link_key, story.location, story.check_link, story.plain_text


def estimate(cfg, task, prompt_chars):
    """A generous guess at one task's cost, for the budget check before it starts: the prompt at three
    characters a token, twice over for ratings (a second request may follow), 100,000 tokens of pages
    for research and the overview, 10,000 output tokens, and twelve searches."""
    p_in, p_out, p_search = price_card(cfg)
    if task == "ratings":
        return 2 * (prompt_chars / 3 * p_in) + 10_000 * p_out
    return (prompt_chars / 3 + 100_000) * p_in + 10_000 * p_out + 12 * p_search


class Budget:
    """What has been spent against the cap. Tasks run one at a time, so checks and charges happen in
    the order of the calls and need no lock."""

    def __init__(self, cap):
        self.cap, self.spent = cap, 0.0

    def allows(self, amount):
        return self.spent + amount <= self.cap + 1e-12

    def charge(self, amount):
        self.spent += amount or 0.0


def anthropic_research(client, cfg, system, prompt, key="blurbs"):
    """Claude's research request, as story.claude_search makes it for the overview."""
    return story.claude_search(client, cfg.model, cfg.effort, system, prompt, key, ANSWERS[key], RESEARCH_SEARCHES,
                               RESEARCH_FETCHES, RESEARCH_MAX_TOKENS)


def rate_once(cfg, header, fixtures, keys, clients):
    """One ratings request for `fixtures` through story.py's own path for the model's provider, so the
    comparison measures exactly what the daily run sends: (raw ratings, usage, stop, served)."""
    if cfg.provider == "anthropic":
        raw, usage, stop, served = story.rate_chunk(anthropic_client(clients), cfg.model, cfg.effort, fixtures, [], header, scores_only=True)
    else:
        raw, usage, stop, served = story.api_rate_chunk(cfg.model, cfg.effort, keys[cfg.provider], fixtures, [], header)
    return raw, story_usage(usage), {"end_turn": "end"}.get(stop, stop), served


def anthropic_client(clients):
    if "anthropic" not in clients:
        import anthropic   # here, so the other providers and the tests run without the package
        clients["anthropic"] = anthropic.Anthropic(max_retries=2)
    return clients["anthropic"]


def run_ratings(cfg, header, fixtures, keys, clients):
    """story.write_ratings' two attempts for one chunk: every fixture, then once more for any left
    without a valid rating. A failed request ends the task, keeping what was rated and spent before it."""
    by_id = {f["id"]: f for f in fixtures}
    result = {"config": f"{cfg.provider}:{cfg.model}:{cfg.effort}", "provider": cfg.provider, "model": cfg.model,
              "effort": cfg.effort, "served": None, "total": len(by_id), "requests": 0, "stops": [], "usage": {}}
    ratings, pending, started = {}, list(by_id), time.monotonic()
    try:
        for _ in (1, 2):
            raw, usage, stop, served = rate_once(cfg, header, [by_id[i] for i in pending], keys, clients)
            result["requests"] += 1
            result["stops"].append(stop)
            result["served"] = served or result["served"]
            add_usage(result["usage"], usage)
            for mid, rating in story.clean_rankings(raw, set(pending), {}, {}).items():
                ratings.setdefault(mid, rating)
            pending = [i for i in by_id if i not in ratings]
            if not pending:
                break
            log(f"{cfg.name}: asking again for {len(pending)} fixture(s) without a valid rating")
    except Exception as e:     # a failure costs this configuration the rest of its task, not the comparison
        result["error"] = f"{type(e).__name__}: {e}"[:600]
    result["status"] = "ok" if ratings else "error" if "error" in result else "empty"
    result["seconds"] = round(time.monotonic() - started, 1)
    result["rated"] = len(ratings)
    result["scores"] = {mid: r["score"] for mid, r in ratings.items()}
    result["parts"] = {mid: [r["popularity"], r["gameplay"], r["impact"]] for mid, r in ratings.items()}
    result["cost_usd"] = cost_of(cfg, {k: result["usage"].get(k, 0) for k in USAGE_KEYS})
    return result


ANSWERS = {"blurbs": list, "overview": str}     # each research task's JSON answer: its key, and that value's type


def extract_json(text, key="blurbs"):
    """The first JSON object in `text` holding the answer `key` with a value of its type, and where it
    starts and ends; (None, None, None) if none."""
    return story.find_json(text, key, ANSWERS[key])


blurb_spans = story.blurb_spans


def research_blurbs(reply, ids):
    """{fixture ID: blurb} from a research reply, or None when it holds no blurbs JSON. Each blurb keeps
    its text as written, the URLs it lists and the provider's own citations that fall within it."""
    data, start, _ = extract_json(reply.text)
    if data is None:
        return None
    elements = blurb_spans(reply.text, start) or [(value, None) for value in data["blurbs"]]
    out = {}
    for value, span in elements:
        mid = value.get("match_id") if isinstance(value, dict) else None
        if mid not in ids or mid in out:
            continue
        out[mid] = written_entry(value.get("blurb"), value.get("sources"), reply.native, span)
    return out


def written_entry(written, sources, native, span):
    """One blurb or overview as the report and the review page take it: its text as the page would
    show it, the URLs it lists, and the provider's own citations that fall within `span` of the reply."""
    written = written if isinstance(written, str) else ""
    written = re.sub(r"\s+", " ", story.normalize_editorial({"blurb": written})["blurb"]).strip()
    text = without_links(written)
    listed = list(dict.fromkeys(u.strip() for u in sources or [] if isinstance(u, str) and u.strip())) \
        if isinstance(sources, list) else []
    cited = list(dict.fromkeys(url for s, e, url in native if span and s < span[1] and e > span[0]))
    return {"text": text, "length": len(text), "listed": listed, "native": cited, "links_in_text": text != written}


def overview_entry(reply):
    """The overview from a reply, or None when it holds no overview JSON. The provider's citations
    count when they fall within that JSON object."""
    data, start, end = extract_json(reply.text, "overview")
    if data is None:
        return None
    return written_entry(data["overview"], data.get("sources"), reply.native, (start, end))


def cited(result):
    """Every blurb or overview a research or overview run wrote, for checking their sources."""
    return [*result.get("blurbs", {}).values(), *([result["overview"]] if result.get("overview") else [])]


def searched(cfg, system, prompt, key, keys, clients, read):
    """One request with the provider's own web search, answered in JSON under `key`, as a result:
    `read` takes the reply and gives (status, fields for the result). A failure is the result's error,
    with what was spent before it. Returns (result, reply), the reply None if no answer came."""
    result = {"config": f"{cfg.provider}:{cfg.model}:{cfg.effort}", "provider": cfg.provider, "model": cfg.model,
              "effort": cfg.effort, "served": None, "usage": {}, "queries": []}
    started, reply = time.monotonic(), None
    try:
        if cfg.provider == "anthropic":
            reply = anthropic_research(anthropic_client(clients), cfg, system, prompt, key)
        else:
            reply = providers.request(cfg.model, cfg.effort, system, prompt, keys[cfg.provider],
                                      max_tokens=RESEARCH_MAX_TOKENS, tool_calls=RESEARCH_SEARCHES + RESEARCH_FETCHES,
                                      url_key=match_key)
        result.update(served=reply.served, stop=reply.stop, usage=add_usage({}, reply.usage), queries=reply.queries,
                      text=reply.text[:20000])
        if cfg.provider != "anthropic" and not reply.queries:
            # A search tool's reply that records no search: keep it as received, to see whether the
            # provider didn't search or put its record where the parser doesn't look.
            result["raw"] = json.dumps(reply.raw, ensure_ascii=False)[:60000]
        status, fields = read(reply)
        result.update(fields, status=status)
    except Exception as e:
        result.update(status="error", error=f"{type(e).__name__}: {e}"[:600])
    result["seconds"] = round(time.monotonic() - started, 1)
    result["cost_usd"] = cost_of(cfg, {k: result["usage"].get(k, 0) for k in USAGE_KEYS})
    return result, reply


def run_research(cfg, header, fixtures, keys, clients):
    def read(reply):
        blurbs = research_blurbs(reply, {f["id"] for f in fixtures})
        return "ok" if blurbs else "unparsed" if blurbs is None else "empty", {"blurbs": blurbs or {}}
    result, reply = searched(cfg, RESEARCH_SYSTEM, research_prompt(header, fixtures), "blurbs", keys, clients, read)
    result.setdefault("blurbs", {})
    return result, reply


def run_overview(cfg, header, view, keys, clients):
    def read(reply):
        entry = overview_entry(reply)
        return ("unparsed" if entry is None else "ok" if entry["text"] else "empty"), {"overview": entry if entry and entry["text"] else None}
    result, reply = searched(cfg, OVERVIEW_SYSTEM, overview_prompt(header, view), "overview", keys, clients, read)
    result.setdefault("overview", None)
    return result, reply


def settle_sources(runs, replies, workers=8):
    """Resolves Google's grounding redirects into the pages they stand for, then marks every URL a
    blurb or overview cites:
    `returned` when the provider's own search returned it in that response, and its state now."""
    redirects = set()
    for result, reply in zip(runs, replies):
        if reply is not None and result["provider"] == "google":
            redirects |= set(reply.redirects)
            redirects |= {u for b in cited(result) for u in b["listed"] if urlsplit(u).netloc == GROUNDING_HOST}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        resolved = dict(zip(sorted(redirects), pool.map(location, sorted(redirects))))
    for result, reply in zip(runs, replies):
        if reply is None:
            continue
        returned = dict(reply.returned)
        for uri, title in reply.redirects.items():
            target = resolved.get(uri)
            if target and match_key(target):
                returned.setdefault(match_key(target), (target, title))
        # A grounding redirect the model cites itself came from its search (story.checked_overview says why).
        for uri in {u for b in cited(result) for u in b["listed"] + b["native"] if urlsplit(u).netloc == GROUNDING_HOST}:
            if resolved.get(uri) and match_key(resolved[uri]):
                returned.setdefault(match_key(resolved[uri]), (resolved[uri], ""))
        for blurb in cited(result):
            blurb["native"] = list(dict.fromkeys(resolved.get(u) or u for u in blurb["native"]))
            blurb["sources"] = []
            for url in blurb["listed"]:
                shown = resolved.get(url) or url
                blurb["sources"].append({"url": shown, "listed": True, "returned": match_key(shown) in returned})
            for url in blurb["native"]:
                if match_key(url) not in {match_key(s["url"]) for s in blurb["sources"]}:
                    blurb["sources"].append({"url": url, "listed": False, "returned": True})
        result["pages_returned"] = len(returned)
    urls = sorted({s["url"] for result in runs for b in cited(result) for s in b["sources"]})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        states = dict(zip(urls, pool.map(check_link, urls)))
    for result in runs:
        for blurb in cited(result):
            for source in blurb["sources"]:
                source.update(states.get(source["url"], {"state": "unreachable", "status": None}))
    return resolved


def featured(facts, rows, published, ids=None, count=RESEARCH_FIXTURES):
    """The research task's fixtures: those named, else the highest-rated in the ratings window that are
    on the default lineup, one per competition, by the published ratings where there are any, else
    by the Outlook score; ties go by fixture ID."""
    if ids:
        known = {m["id"]: m for m in facts.get("ranking_candidates", [])}
        missing = [i for i in ids if i not in known]
        if missing:
            raise ValueError(f"fixtures not among today's: {', '.join(missing)}")
        return [known[i] for i in ids]
    window, _ = story.rating_window(facts)
    rated = ((published or {}).get("rankings") or {}) if isinstance(published, dict) else {}

    def score(m):
        r = rated.get(m["id"])
        if isinstance(r, dict) and isinstance(r.get("score"), (int, float)):
            return float(r["score"])
        return rows[m["id"]][2] if not rated else -1.0
    out, leagues = [], set()
    for m in sorted((m for m in window if m["id"] in rows and rows[m["id"]][1]), key=lambda m: (-score(m), m["id"])):
        if m.get("league_id") not in leagues:
            leagues.add(m.get("league_id"))
            out.append(m)
        if len(out) == count:
            break
    return out


def money(c):
    """Dollars, to the hundredth of a cent below ten cents: a ratings run can cost a fraction of a cent."""
    return "n/a" if c is None else f"${c:.2f}" if c >= 0.10 or c == 0 else f"${c:.4f}"


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def consensus(results, provider):
    """{fixture: mean score} over the configurations of the other providers that rated it."""
    sums = {}
    for r in results:
        if r["provider"] != provider and r.get("status") == "ok":
            for mid, score in r["scores"].items():
                sums.setdefault(mid, []).append(score)
    return {mid: sum(v) / len(v) for mid, v in sums.items()}


def top_three_agreement(scores, reference, rows):
    """'9 of 12 picks · same #1 on 2 of 4 days': how many of the reference's top three on the default
    lineup each Eastern day this order also picks, and on how many days it puts the same match first.
    The reference counts only the fixtures this order rated: the published ratings reach days past the
    ratings window, where no model in the comparison could pick anything."""
    reference = {k: v for k, v in reference.items() if k in scores}
    base, mine = storylines.top_three_by_day(reference, rows), storylines.top_three_by_day(scores, rows)
    if not base:
        return ""
    shared = sum(len(set(mine.get(day, [])) & set(top)) for day, top in base.items())
    first = sum(bool(mine.get(day)) and mine[day][0] == top[0] for day, top in base.items())
    return f"{shared} of {sum(map(len, base.values()))} picks · same #1 on {first} of {len(base)} days"


def rho_text(s, interval=False):
    if not s:
        return "n/a"
    return f"{s[0]:.2f} ({s[1]:.2f} to {s[2]:.2f})" if interval else f"{s[0]:.2f}"


def ratings_section(results, rows, published, names, hours):
    if not results:
        return []
    reference = {}
    if isinstance(published, dict) and isinstance(published.get("rankings"), dict):
        reference = {k: r["score"] for k, r in published["rankings"].items()
                     if isinstance(r, dict) and isinstance(r.get("score"), (int, float))}
    outlook = {k: v[2] for k, v in rows.items()}
    total = max((r["total"] for r in results), default=0)
    lines = [f"### Ratings: three scores for each of the {total} fixtures kicking off within {hours} hours", "",
             "Spearman's rank correlation (1 = the same order, 0 = unrelated) against the ratings the page publishes now "
             "(Claude Opus 5.5, with its research), against the mean of the other providers' models, and against the "
             "page's own Outlook score. Agreement is not accuracy: there is no right answer to compare with, and the "
             "published ratings favour a model that thinks like Claude.", "",
             "| Model | Cost | Time | Rated | vs published (95% interval) | vs other providers | vs Outlook | Top three each day vs published | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        ok = r.get("status") == "ok"
        scores = r["scores"] if ok else {}
        others = consensus(results, r["provider"])
        notes = r.get("error") or ("" if ok else r.get("status", ""))
        if r.get("served") and r["served"] != r["model"] and not r["served"].startswith(r["model"]):
            notes = f"served by {r['served']}. {notes}".strip()
        if r.get("requests", 0) > 1:
            notes = f"{r['requests']} requests. {notes}".strip()
        lines.append(f"| {cell(r['model'])} at {r['effort']} | {money(r.get('cost_usd'))} | {r.get('seconds', '?')}s "
                     f"| {r['rated']}/{r['total']} | {rho_text(storylines.spearman(scores, reference), True) if ok and reference else 'n/a'} "
                     f"| {rho_text(storylines.spearman(scores, others)) if ok and others else 'n/a'} "
                     f"| {rho_text(storylines.spearman(scores, outlook)) if ok and outlook else 'n/a'} "
                     f"| {top_three_agreement(scores, reference, rows) if ok and reference else ''} | {cell(notes)} |")
    runs = [(r["config"], {"mode": "ratings", "model": r["model"], "effort": r["effort"]},
             {"rankings": {k: {"score": v} for k, v in r["scores"].items()}}) for r in results if r.get("status") == "ok"]
    return lines + [""] + storylines.ratings_report(runs, rows, published, names)


def research_section(research, fixtures, names):
    runs = research["runs"]
    if not runs:
        return []
    count = len(fixtures)
    lines = [f"### Research: a blurb for each of {count} featured fixtures", "",
             "Fixtures: " + "; ".join(f"{names.get(f['id'], f['id'])} ({f.get('competition', '')})" for f in fixtures) + ".", "",
             "A cited page counts as **from its search** only if the provider's own search returned it in that response. "
             "**Loads** is checked afterwards, from GitHub's runner; a site that refuses scripts is **blocked**, which says "
             "nothing about the page. Searches are the provider's count (OpenAI's include page opens).", "",
             "| Model | Cost | Time | Searches | Blurbs | Over 260 | Cited | From its search | Loads | Dead | Blocked or unreachable | Notes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        blurbs = r["blurbs"].values()
        written = [b for b in blurbs if b["text"]]
        listed = [s for b in blurbs for s in b.get("sources", []) if s["listed"]]
        state = lambda *names_: sum(s.get("state") in names_ for s in listed)
        u = r["usage"]
        searches = u.get("searches", 0) + u.get("opens", 0)
        notes = [r.get("error") or ""] if r.get("status") == "error" else []
        if r.get("status") in ("unparsed", "empty"):
            notes.append("no blurbs JSON in the reply" if r["status"] == "unparsed" else "no blurb for a known fixture")
        if r.get("stop") not in (None, "end"):
            notes.append(f"stopped: {r['stop']}")
        native = sum(1 for b in blurbs for s in b.get("sources", []) if not s["listed"])
        if native:
            notes.append(f"{native} more cited by the provider's own annotations")
        if any(b["links_in_text"] for b in blurbs):
            notes.append("links in the blurb text")
        lines.append(f"| {cell(r['model'])} at {r['effort']} | {money(r.get('cost_usd'))} | {r.get('seconds', '?')}s | {searches} "
                     f"| {len(written)}/{count} | {sum(b['length'] > BLURB_LIMIT for b in written)} | {len(listed)} "
                     f"| {sum(s['returned'] for s in listed)} | {state('live')} | {state('dead')} | {state('blocked', 'unreachable')} "
                     f"| {cell('; '.join(n for n in notes if n))} |")
    lines += [""]
    for f in fixtures:
        lines += [f"<details><summary><b>{cell(names.get(f['id'], f['id']))}</b> · {cell(f.get('competition', ''))} · {cell(f.get('kickoff', ''))}</summary>", ""]
        for r in runs:
            b = r["blurbs"].get(f["id"])
            if not b or not b["text"]:
                lines += [f"- *{cell(r['model'])} at {r['effort']}:* no blurb", ""]
                continue
            marks = []
            for s in b["sources"]:
                origin = "from its search" if s["returned"] else "**not from its search**"
                kind = "" if s["listed"] else ", provider annotation"
                marks.append(f"[{storylines.host(s['url'])}]({s['url']}) ({origin}, {s.get('state', '?')}{kind})")
            lines += [f"- *{cell(r['model'])} at {r['effort']}* ({b['length']} characters): {b['text']}",
                      f"  Sources: {'; '.join(marks) if marks else 'none'}", ""]
        lines += ["</details>", ""]
    return lines


def overview_section(overview):
    runs = overview["runs"]
    if not runs:
        return []
    limit = overview.get("limit", OVERVIEW_LIMIT)
    lines = [f"### Overview: one paragraph about the whole slate, at most {limit} characters", "",
             "Each model is given every match with known coverage, on any service and in any competition, in the time "
             "frame the top three come from: the next 24 hours, longer when that holds fewer than three. Sources are "
             "checked as for the blurbs.", "",
             f"| Model | Cost | Time | Searches | Characters | Over {limit} | Cited | From its search | Loads | Dead | Blocked or unreachable | Notes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        o = r.get("overview")
        listed = [s for s in (o or {}).get("sources", []) if s["listed"]]
        state = lambda *names_: sum(s.get("state") in names_ for s in listed)
        u = r["usage"]
        notes = [r.get("error") or ""] if r.get("status") == "error" else []
        if r.get("status") in ("unparsed", "empty"):
            notes.append("no overview JSON in the reply" if r["status"] == "unparsed" else "an empty overview")
        if r.get("stop") not in (None, "end"):
            notes.append(f"stopped: {r['stop']}")
        native = sum(1 for s in (o or {}).get("sources", []) if not s["listed"])
        if native:
            notes.append(f"{native} more cited by the provider's own annotations")
        if o and o["links_in_text"]:
            notes.append("links in the text")
        lines.append(f"| {cell(r['model'])} at {r['effort']} | {money(r.get('cost_usd'))} | {r.get('seconds', '?')}s "
                     f"| {u.get('searches', 0) + u.get('opens', 0)} | {o['length'] if o else 0} | {'yes' if o and o['length'] > limit else ''} "
                     f"| {len(listed)} | {sum(s['returned'] for s in listed)} | {state('live')} | {state('dead')} "
                     f"| {state('blocked', 'unreachable')} | {cell('; '.join(n for n in notes if n))} |")
    lines += ["", "<details><summary><b>The overviews</b></summary>", ""]
    for r in runs:
        o = r.get("overview")
        if not o:
            lines += [f"- *{cell(r['model'])} at {r['effort']}:* no overview", ""]
            continue
        marks = [f"[{storylines.host(s['url'])}]({s['url']}) ({'from its search' if s['returned'] else '**not from its search**'}, "
                 f"{s.get('state', '?')}{'' if s['listed'] else ', provider annotation'})" for s in o.get("sources", [])]
        lines += [f"- *{cell(r['model'])} at {r['effort']}* ({o['length']} characters): {o['text']}",
                  f"  Sources: {'; '.join(marks) if marks else 'none'}", ""]
    return lines + ["</details>", ""]


def report(results, facts, rows, published):
    names = {m["id"]: f"{m['home']['name'].strip()} v {m['away']['name'].strip()}" for m in facts.get("ranking_candidates", [])}
    lines = [f"## Provider comparison for {facts.get('weekday', '')} {facts.get('date', '')}", "",
             "Written by Claude, comparing Claude with other providers, so it measures and does not judge: cost at list "
             "prices from each API's own usage report, time, agreement, and whether cited pages came from the provider's "
             "own search and load now. The writing is for a person to judge, blind.", "",
             f"Spent {money(results['spent_usd'])} of the {money(results['budget_usd'])} budget."]
    skipped = results.get("skipped") or []
    if skipped:
        lines.append("Skipped: " + "; ".join(f"{s['config']} ({s['why']})" for s in skipped) + ".")
    lines.append("")
    lines += ratings_section(results["ratings"], rows, published, names, results.get("window_hours"))
    lines += research_section(results["research"], results["research"]["fixtures"], names)
    lines += overview_section(results.get("overview") or {"runs": []})
    return "\n".join(lines)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--facts", required=True)
    ap.add_argument("--page", required=True, help="the built page, for the default lineup and the Outlook score")
    ap.add_argument("--published", help="the story.json the page publishes now")
    ap.add_argument("--ratings", default="", help="configurations for the ratings task")
    ap.add_argument("--research", default="", help="configurations for the research task")
    ap.add_argument("--overview", default="", help="configurations for the overview task")
    ap.add_argument("--fixtures", default="", help="fixture IDs for the research task (default: chosen as featured() says)")
    ap.add_argument("--budget", type=float, default=5.0, help="USD; no task starts that could take spending past it")
    ap.add_argument("--out", required=True, help="where to write the results as JSON")
    args = ap.parse_args(argv)

    try:
        ratings, research = parse_configs(args.ratings, "ratings"), parse_configs(args.research, "research")
        overviews = parse_configs(args.overview, "overview")
    except ValueError as e:
        log(str(e))
        return 2
    facts, published = load(args.facts), load(args.published) if args.published else None
    if not facts or not facts.get("built_at"):
        log(f"no facts to compare on in {args.facts}")
        return 2
    rows = storylines.page_rows(args.page)
    window, hours = story.rating_window(facts)
    header = f"It is {story.clock(facts['built_at'])} on {facts.get('weekday', '')}, {facts.get('date', '')}, US Eastern time."
    rating_fixtures = [story.rating_fixture(m) for m in window]
    try:
        chosen = [story.rating_fixture(m) for m in featured(facts, rows, published, args.fixtures.split())]
    except ValueError as e:
        log(str(e))
        return 2
    keys = {p: os.environ.get(KEYS[p], "") for p in PROVIDERS}
    budget, clients = Budget(args.budget), {}
    results = {"date": facts.get("date"), "built_at": facts.get("built_at"), "budget_usd": args.budget, "window_hours": hours,
               "ratings": [], "research": {"fixtures": chosen, "runs": []}, "overview": {"limit": OVERVIEW_LIMIT, "runs": []},
               "skipped": []}

    def admit(cfg, task, prompt_chars):
        why = (f"no {KEYS[cfg.provider]}" if not keys[cfg.provider] else
               None if budget.allows(estimate(cfg, task, prompt_chars)) else
               f"its estimate of {money(estimate(cfg, task, prompt_chars))} would pass the budget")
        if why:
            results["skipped"].append({"config": f"{cfg.provider}:{cfg.model}:{cfg.effort}", "task": task, "why": why})
            log(f"skipping {task} on {cfg.name}: {why}")
        return why is None

    ratings_chars = len(story.SCORES_SYSTEM) + len(story.rating_prompt(header, rating_fixtures, scores_only=True))
    for cfg in sorted(ratings, key=lambda c: estimate(c, "ratings", ratings_chars)):
        if admit(cfg, "ratings", ratings_chars):
            log(f"ratings: {cfg.name} on {len(rating_fixtures)} fixtures")
            result = run_ratings(cfg, header, rating_fixtures, keys, clients)
            budget.charge(result["cost_usd"])
            log(f"  {result['status']}: {result['rated']} rated, {money(result['cost_usd'])}, {result['seconds']}s {result.get('error', '')}")
            results["ratings"].append(result)
    research_chars = len(RESEARCH_SYSTEM) + len(research_prompt(header, chosen))
    replies = []
    for cfg in sorted(research, key=lambda c: estimate(c, "research", research_chars)):
        if chosen and admit(cfg, "research", research_chars):
            log(f"research: {cfg.name} on {len(chosen)} fixtures")
            result, reply = run_research(cfg, header, chosen, keys, clients)
            budget.charge(result["cost_usd"])
            log(f"  {result['status']}: {len(result['blurbs'])} blurbs, {money(result['cost_usd'])}, {result['seconds']}s {result.get('error', '')}")
            results["research"]["runs"].append(result)
            replies.append(reply)
    view = overview_view(facts)
    overview_chars = len(OVERVIEW_SYSTEM) + len(overview_prompt(header, view))
    for cfg in sorted(overviews, key=lambda c: estimate(c, "overview", overview_chars)):
        if view["fixtures"] and admit(cfg, "overview", overview_chars):
            log(f"overview: {cfg.name} on {len(view['fixtures'])} fixtures")
            result, reply = run_overview(cfg, header, view, keys, clients)
            budget.charge(result["cost_usd"])
            log(f"  {result['status']}: {(result['overview'] or {}).get('length', 0)} characters, {money(result['cost_usd'])}, "
                f"{result['seconds']}s {result.get('error', '')}")
            results["overview"]["runs"].append(result)
            replies.append(reply)
    settle_sources(results["research"]["runs"] + results["overview"]["runs"], replies)
    results["spent_usd"] = budget.spent
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(report(results, facts, rows, published))
    return 0


if __name__ == "__main__":
    sys.exit(main())
