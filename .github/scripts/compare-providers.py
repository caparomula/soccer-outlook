#!/usr/bin/env python3
"""Compares AI providers on Soccer Outlook's two jobs, on the day's real fixtures, publishing nothing.

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

What it measures, all mechanically: cost from each API's own usage report at providers.py's list prices,
time, searches, and for ratings how far each model's order agrees with the others', with the ratings
the page publishes now and with the page's own Outlook score. For research, every URL a blurb cites
is checked twice: whether the provider's own search returned it in that response (one it did not
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
                            --research CONFIGS [--fixtures IDS] [--budget USD] --out RESULTS
CONFIGS are provider:model:effort, separated by spaces; provider is anthropic, openai or google.
The Markdown report goes to stdout, progress to stderr.
"""
import argparse
import http.client
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import providers  # noqa: E402
import story  # noqa: E402
from providers import Reply  # noqa: E402

_spec = importlib.util.spec_from_file_location("compare_storylines", Path(__file__).with_name("compare-storylines.py"))
storylines = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(storylines)

PROVIDERS = tuple(providers.PROVIDER_NAMES)
KEYS = providers.KEY_NAMES
EFFORTS = providers.EFFORTS
# Ratings only: Haiku 5.5 has no dynamic web tools (story.py), and Google doesn't list Grounding with
# Google Search for Gemini 3.1 Flash-Lite.
RATINGS_ONLY = {("anthropic", m) for m in story.RATINGS_ONLY_MODELS} | {("google", "gemini-3.1-flash-lite")}
GROUNDING_HOST = "vertexaisearch.cloud.google.com"
RESEARCH_FIXTURES = 6
RESEARCH_SEARCHES, RESEARCH_FETCHES = 8, 4      # Claude's web search and web fetch; OpenAI's max_tool_calls is their sum
RESEARCH_MAX_TOKENS = 32000
MAX_CONTINUATIONS = 4                           # Claude's paused turns, as story.py continues them
BLURB_LIMIT = story.LIMITS["blurb"]
USER_AGENT = "Mozilla/5.0 (compatible; SoccerOutlookLinkCheck/1.0; +https://github.com/caparomula/soccer-outlook)"

RESEARCH_SYSTEM = """You write match blurbs for Soccer Outlook, a soccer schedule for viewers in the United States. The page already lists kickoff times, channels, table positions, recent form and top scorers, so a blurb must add something specific about the upcoming match: its stakes, player availability, likely selection supported by reporting, a relevant matchup, or a scheduling change. General club news, ownership stories and unrelated controversy do not belong.

Research with web search before writing, and read a full article when a search snippet is not enough. Prefer recent reporting from established outlets: clubs and federations, major newspapers, broadcasters, wire services. State only what the pages you read in this session say or what the supplied facts establish. If you cannot confirm something, leave it out rather than guess, and never predict results or invent lineups, injuries or quotes. Write plainly, in present tense."""


def research_prompt(header, fixtures):
    return (f"{header}\n\nWrite one blurb for each of these {len(fixtures)} fixtures, at most {BLURB_LIMIT} characters each, "
            "and cite one to three URLs of pages your searches returned in this session that support it. Put URLs only in "
            "sources, never in the blurb text. If no reporting you find supports a blurb for a fixture, give it an empty "
            "blurb and no sources rather than guess.\n\n"
            f"Fixtures:\n{story.compact(fixtures)}\n\n"
            f"You have up to {RESEARCH_SEARCHES} web searches. When you are done, reply with only this JSON object and no "
            'other text: {"blurbs": [{"match_id": "<fixture id>", "blurb": "<the blurb>", "sources": ["<url>"]}]}')


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
        elif task == "research" and (cfg.provider, cfg.model) in RATINGS_ONLY:
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


USAGE_KEYS = ("in", "cached", "cache_write", "out", "reasoning", "searches", "opens", "prompt_max")


def add_usage(totals, usage):
    for key in USAGE_KEYS:
        value = usage.get(key, 0) or 0
        totals[key] = max(totals.get(key, 0), value) if key == "prompt_max" else totals.get(key, 0) + value
    return totals


def estimate(cfg, task, prompt_chars):
    """A generous guess at one task's cost, for the budget check before it starts: the prompt at three
    characters a token, twice over for ratings (a second request may follow), 100,000 tokens of pages
    for research, 10,000 output tokens, and twelve searches."""
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


def match_key(url):
    """story.url_key without tracking parameters (OpenAI adds utm_source=openai to the links it cites)."""
    try:
        p = urlsplit(url.strip())
        query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")])
    except (AttributeError, ValueError):
        return ""
    return story.url_key(urlunsplit((p.scheme, p.netloc, p.path, query, "")))


def story_usage(u):
    """story.py's usage keys in providers.cost's."""
    return {"in": u["in"], "cached": u["cache_read"], "cache_write": u["cache_write"], "out": u["out"],
            "reasoning": u.get("reasoning", 0), "searches": u["searches"], "opens": u["fetches"], "prompt_max": u["prompt_max"]}


def anthropic_text(content):
    """The text after the reply's last non-text block (its research and thinking), with each text
    block's citations as spans of that text."""
    last = max((i for i, b in enumerate(content) if getattr(b, "type", "") != "text"), default=-1)
    pieces, native = [], []
    for block in content[last + 1:]:
        text, base = getattr(block, "text", "") or "", sum(map(len, pieces))
        for c in getattr(block, "citations", None) or []:
            url = getattr(c, "url", None)
            if isinstance(url, str) and match_key(url):
                native.append((base, base + len(text), url))
        pieces.append(text)
    return "".join(pieces), native


def anthropic_research(client, cfg, system, prompt):
    """Claude's research request as story.py makes it (dynamic web search and fetch, effort, caching,
    the server-side fallback), continued while the server pauses the turn. The answer is the final
    text; if that holds no blurbs JSON, every text block of the turn is searched for it."""
    messages, reply, seen, usage, texts = [{"role": "user", "content": prompt}], Reply(served=cfg.model), {}, {}, []
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": RESEARCH_SEARCHES},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": RESEARCH_FETCHES}]
    for _ in range(MAX_CONTINUATIONS):
        with client.beta.messages.stream(model=cfg.model, max_tokens=RESEARCH_MAX_TOKENS, system=system, messages=messages,
                                         tools=tools, output_config={"effort": cfg.effort}, cache_control={"type": "ephemeral"},
                                         **story.request_options(cfg.model)) as stream:
            message = stream.get_final_message()
        reply.served = message.model
        add_usage(usage, story_usage(story.usage_of(message)))
        story.collect_sources(message.content, seen)
        for block in message.content:
            if getattr(block, "type", "") == "text":
                texts.append(getattr(block, "text", "") or "")
            query = (getattr(block, "input", None) or {}).get("query") if getattr(block, "type", "") == "server_tool_use" else None
            if isinstance(query, str) and query.strip() and query.strip() not in reply.queries:
                reply.queries.append(query.strip())
        if message.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": message.content})
    reply.text, reply.native = anthropic_text(message.content)
    if extract_json(reply.text)[0] is None:
        reply.text, reply.native = "".join(texts), []
    reply.stop = {"end_turn": "end", "max_tokens": "max_tokens", "refusal": "refusal"}.get(message.stop_reason, message.stop_reason or "unknown")
    reply.usage = usage
    reply.returned = {match_key(url): (url, title) for url, title in seen.values() if match_key(url)}
    return reply


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


def extract_json(text):
    """The first JSON object in `text` holding a "blurbs" list, and where it starts; (None, None) if none."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text or ""):
        try:
            value, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("blurbs"), list):
            return value, m.start()
    return None, None


def blurb_spans(text, start):
    """[(element, (start, end))] for each element of the "blurbs" array in the object at `start`, so a
    provider's citation spans can be matched to the fixture whose blurb they fall in; [] if the text
    can't be walked."""
    m = re.compile(r'"blurbs"\s*:\s*\[').search(text, start)
    if not m:
        return []
    decoder, pos, out = json.JSONDecoder(), m.end(), []
    try:
        while True:
            while pos < len(text) and text[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(text) or text[pos] == "]":
                return out
            value, end = decoder.raw_decode(text, pos)
            out.append((value, (pos, end)))
            pos = end
    except ValueError:
        return out


def research_blurbs(reply, ids):
    """{fixture ID: blurb} from a research reply, or None when it holds no blurbs JSON. Each blurb keeps
    its text as written, the URLs it lists and the provider's own citations that fall within it."""
    data, start = extract_json(reply.text)
    if data is None:
        return None
    elements = blurb_spans(reply.text, start) or [(value, None) for value in data["blurbs"]]
    out = {}
    for value, span in elements:
        mid = value.get("match_id") if isinstance(value, dict) else None
        if mid not in ids or mid in out:
            continue
        written = value.get("blurb") if isinstance(value.get("blurb"), str) else ""
        written = re.sub(r"\s+", " ", story.normalize_editorial({"blurb": written})["blurb"]).strip()
        text = without_links(written)
        listed = list(dict.fromkeys(u.strip() for u in value.get("sources") or [] if isinstance(u, str) and u.strip()))
        native = list(dict.fromkeys(url for s, e, url in reply.native if span and s < span[1] and e > span[0]))
        out[mid] = {"text": text, "length": len(text), "listed": listed, "native": native, "links_in_text": text != written}
    return out


def without_links(text):
    """The blurb as the page would show it: a link the model wrote into the text despite being asked
    not to (OpenAI's inline citations look like '([site](url))') is taken out, a Markdown link keeps
    its words, and bare URLs go. The report notes that it happened."""
    text = re.sub(r"\s*\(\s*\[[^\]]*\]\(\s*https?://[^)\s]*\s*\)\s*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(\s*https?://[^)\s]*\s*\)", r"\1", text)
    text = re.sub(r"\(?\s*https?://[^\s)]+\s*\)?", " ", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", text)).strip()


def run_research(cfg, header, fixtures, keys, clients):
    result = {"config": f"{cfg.provider}:{cfg.model}:{cfg.effort}", "provider": cfg.provider, "model": cfg.model,
              "effort": cfg.effort, "served": None, "usage": {}, "queries": [], "blurbs": {}}
    started, reply = time.monotonic(), None
    try:
        if cfg.provider == "anthropic":
            reply = anthropic_research(anthropic_client(clients), cfg, RESEARCH_SYSTEM, research_prompt(header, fixtures))
        else:
            reply = providers.request(cfg.model, cfg.effort, RESEARCH_SYSTEM, research_prompt(header, fixtures), keys[cfg.provider],
                                      max_tokens=RESEARCH_MAX_TOKENS, tool_calls=RESEARCH_SEARCHES + RESEARCH_FETCHES,
                                      url_key=match_key)
        result.update(served=reply.served, stop=reply.stop, usage=add_usage({}, reply.usage), queries=reply.queries,
                      text=reply.text[:20000])
        blurbs = research_blurbs(reply, {f["id"] for f in fixtures})
        result["status"] = "ok" if blurbs else "unparsed" if blurbs is None else "empty"
        result["blurbs"] = blurbs or {}
    except Exception as e:
        result.update(status="error", error=f"{type(e).__name__}: {e}"[:600])
    result["seconds"] = round(time.monotonic() - started, 1)
    result["cost_usd"] = cost_of(cfg, {k: result["usage"].get(k, 0) for k in USAGE_KEYS})
    return result, reply


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def location(url, timeout=20):
    """Where a redirect points, read without following it, or None."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout):
            return None
    except urllib.error.HTTPError as e:
        return e.headers.get("Location") if 300 <= e.code < 400 else None
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return None


def check_link(url, timeout=20):
    """Whether a page loads now: live, dead (404 or 410, a 'not found' title, or a redirect to the
    site's front page), blocked (the site refused a script: 401, 403, 429, 451) or unreachable."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            head = response.read(65536).decode("utf-8", "replace")
            final, status = response.geturl(), response.status
    except urllib.error.HTTPError as e:
        verdict = "dead" if e.code in (404, 410) else "blocked" if e.code in (401, 403, 429, 451, 999) else "unreachable"
        return {"state": verdict, "status": e.code}
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        return {"state": "unreachable", "status": None, "why": type(e).__name__}
    title = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
    if title and re.search(r"\b(404|not found|page not found)\b", title.group(1), re.I):
        return {"state": "dead", "status": status, "why": "a 'not found' page"}
    if urlsplit(url).path.strip("/") and not urlsplit(final).path.strip("/"):
        return {"state": "dead", "status": status, "why": "redirected to the front page"}
    return {"state": "live", "status": status}


def settle_sources(runs, replies, workers=8):
    """Resolves Google's grounding redirects into the pages they stand for, then marks every cited URL:
    `returned` when the provider's own search returned it in that response, and its state now."""
    redirects = set()
    for result, reply in zip(runs, replies):
        if reply is not None and result["provider"] == "google":
            redirects |= set(reply.redirects)
            redirects |= {u for b in result["blurbs"].values() for u in b["listed"] if urlsplit(u).netloc == GROUNDING_HOST}
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
        for blurb in result["blurbs"].values():
            blurb["native"] = list(dict.fromkeys(resolved.get(u) or u for u in blurb["native"]))
            blurb["sources"] = []
            for url in blurb["listed"]:
                shown = resolved.get(url) or url
                blurb["sources"].append({"url": shown, "listed": True, "returned": match_key(shown) in returned})
            for url in blurb["native"]:
                if match_key(url) not in {match_key(s["url"]) for s in blurb["sources"]}:
                    blurb["sources"].append({"url": url, "listed": False, "returned": True})
        result["pages_returned"] = len(returned)
    urls = sorted({s["url"] for result in runs for b in result["blurbs"].values() for s in b["sources"]})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        states = dict(zip(urls, pool.map(check_link, urls)))
    for result in runs:
        for blurb in result["blurbs"].values():
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
    ap.add_argument("--fixtures", default="", help="fixture IDs for the research task (default: chosen as featured() says)")
    ap.add_argument("--budget", type=float, default=5.0, help="USD; no task starts that could take spending past it")
    ap.add_argument("--out", required=True, help="where to write the results as JSON")
    args = ap.parse_args(argv)

    try:
        ratings, research = parse_configs(args.ratings, "ratings"), parse_configs(args.research, "research")
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
               "ratings": [], "research": {"fixtures": chosen, "runs": []}, "skipped": []}

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
    settle_sources(results["research"]["runs"], replies)
    results["spent_usd"] = budget.spent
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(report(results, facts, rows, published))
    return 0


if __name__ == "__main__":
    sys.exit(main())
