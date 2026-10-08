"""What story.py and the comparison scripts know about the AI providers: the models each offers here,
their list prices, what settings.toml's [ai] table may say, and HTTP clients for the two providers
called without an SDK (OpenAI's Responses API and Google's generateContent).

The page's daily AI work can run on any model in MODELS once its provider's key is set: Claude through
the anthropic SDK (story.py), the others through request() here. Each model belongs to one provider,
so settings.toml names a model alone. Only Claude runs the full design (research, overview, blurbs):
story.py's research rests on Claude's tool use and web tools, so RESEARCH_MODELS lists the two Claude
models that have them; every model here can do the ratings.

Prices are list prices in USD per token, for cost lines and budgets only; each table says where and
when it was checked. A request is tried again after a 429 or 5xx, twice, and never after a timeout or
a dropped connection, which the provider may have done and billed.
"""
import copy
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google"}
KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY"}
EFFORTS = {"anthropic": ("low", "medium", "high", "xhigh", "max"),                     # output_config.effort
           "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),     # reasoning.effort
           "google": ("minimal", "low", "medium", "high")}                            # thinkingConfig.thinkingLevel

# Claude, checked 2026-10-08 against https://platform.claude.com/docs/en/about-claude/pricing: input,
# 5-minute cache write, cache read, output. Haiku 5.5 is priced by prompt length: a request whose
# prompt (fresh, cache-written and cache-read input together) is over the limit pays the second card.
ANTHROPIC_PRICES = {
    "claude-opus-5-5": (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6),
    "claude-sonnet-5-5": (2.00e-6, 2.50e-6, 0.10e-6, 10.00e-6),
    "claude-haiku-5-5": (0.10e-6, 0.125e-6, 0.01e-6, 0.50e-6),
}
ANTHROPIC_LONG = {"claude-haiku-5-5": (100_000, (0.50e-6, 0.625e-6, 0.05e-6, 2.50e-6))}
ANTHROPIC_SEARCH = 0.01               # per web search on any model; web fetch costs only its tokens
# OpenAI and Google (input, cached input, output), checked 2026-10-08 at
# https://developers.openai.com/api/docs/pricing (standard tier, prompts up to 272K tokens) and
# https://ai.google.dev/gemini-api/docs/pricing (paid tier). Reasoning and thinking tokens are billed as
# output. OpenAI bills $10 per 1,000 web search calls, with the pages it reads billed as input tokens.
# Google bills Gemini 3 models per search query the model runs, 5,000 a month free across Gemini 3 and
# then $14 per 1,000; the cost here is the list price, as if the allowance were used up, and its
# tool-use prompt tokens are counted as input, which may overstate it.
OPENAI_PRICES = {
    "gpt-6-luna": (0.10e-6, 0.01e-6, 0.50e-6),
    "gpt-6.1-sol": (2.00e-6, 0.10e-6, 10.00e-6),
    "gpt-5.5": (5.00e-6, 0.50e-6, 30.00e-6),
}
OPENAI_SEARCH = 0.01
GEMINI_PRICES = {
    "gemini-3.1-flash-lite": (0.25e-6, 0.025e-6, 1.50e-6),
    "gemini-3.8-flash": (0.75e-6, 0.075e-6, 3.75e-6),          # through 2026; twice that from 1 January 2027
    "gemini-3.1-pro-preview": (2.00e-6, 0.20e-6, 12.00e-6),
}
GEMINI_LONG = {"gemini-3.1-pro-preview": (200_000, (4.00e-6, 0.40e-6, 18.00e-6))}
GEMINI_SEARCH = 0.014

MODELS = {**dict.fromkeys(ANTHROPIC_PRICES, "anthropic"), **dict.fromkeys(OPENAI_PRICES, "openai"),
          **dict.fromkeys(GEMINI_PRICES, "google")}
RESEARCH_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
DESIGNS = ("ratings", "full")

OPENAI_URL = "https://api.openai.com/v1/responses"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
REQUEST_TIMEOUT = 600
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_TOKENS = 32000


def check_ai(table):
    """settings.toml's [ai] table, checked: ({"enabled", "design", "model", "effort", "provider"}, []), or
    (None, problems) listing every problem found. build.py refuses to publish on a problem; story.py
    then spends nothing, so the two never act on different readings of the file."""
    if not isinstance(table, dict):
        return None, ["[ai]: must be a table"]
    expected, problems = ("enabled", "design", "model", "effort"), []
    extra = sorted(set(table) - set(expected))
    if extra:
        problems.append(f"[ai]: unknown key {', '.join(extra)} (expected {', '.join(expected)})")
    absent = [k for k in expected if k not in table]
    if absent:
        problems.append(f"[ai]: missing {', '.join(absent)}")
    enabled, design, model, effort = (table.get(k) for k in expected)
    if "enabled" in table and not isinstance(enabled, bool):
        problems.append("[ai] enabled: must be true or false")
    if "design" in table and design not in DESIGNS:
        problems.append(f"[ai] design: must be {' or '.join(DESIGNS)}")
    provider = MODELS.get(model) if isinstance(model, str) else None
    if "model" in table and provider is None:
        problems.append(f"[ai] model: must be one of {', '.join(MODELS)}")
    elif provider and "effort" in table and effort not in EFFORTS[provider]:
        problems.append(f"[ai] effort: {model} takes {', '.join(EFFORTS[provider])}")
    if design == "full" and provider and model not in RESEARCH_MODELS:
        problems.append(f"[ai] model: the full design is Claude's research, so it needs {' or '.join(RESEARCH_MODELS)}")
    if problems:
        return None, problems
    return {"enabled": enabled, "design": design, "model": model, "effort": effort, "provider": provider}, []


class ProviderError(Exception):
    """A request the provider refused or failed, with its status and message; never the request's headers."""


def error_detail(body):
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        err = data.get("error") if isinstance(data, dict) else None
        message = err.get("message") if isinstance(err, dict) else err
        return str(message or data)[:500]
    except ValueError:
        return body.decode("utf-8", "replace")[:500]


def post_json(url, body, headers, timeout=REQUEST_TIMEOUT, attempts=3):
    """POSTs JSON and returns the parsed reply. A 429 or 5xx is tried again after 2 and then 4 seconds;
    any other HTTP error raises ProviderError at once, and a timeout or network failure propagates
    without a retry, since the provider may have done (and billed) the work."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = error_detail(e.read())
            if e.code in RETRY_STATUSES and attempt < attempts:
                print(f"HTTP {e.code} ({detail[:120]}); trying again", file=sys.stderr, flush=True)
                time.sleep(2 ** attempt)
                continue
            raise ProviderError(f"HTTP {e.code}: {detail}") from None


@dataclass
class Reply:
    """One provider's answer, normalized. `usage` has the keys cost() reads; `returned` holds
    {key(url): (url, title)} for every page its search returned; `native` holds the provider's own
    citations as (start, end, url) spans of `text`; `redirects` holds Google's grounding links, which
    point through a redirect, with the title of each."""
    text: str = ""
    stop: str = ""          # end, max_tokens, refusal, or the provider's own word
    served: str = ""
    usage: dict = field(default_factory=dict)
    returned: dict = field(default_factory=dict)
    native: list = field(default_factory=list)
    queries: list = field(default_factory=list)
    redirects: dict = field(default_factory=dict)


def openai_reply(resp, key=None):
    """A Responses API reply: its message text with url_citation spans, the web_search_call items'
    queries, sources and page opens, and its usage (output_tokens includes reasoning_tokens). `key`
    normalizes the URLs `returned` is keyed by; a URL it maps to nothing is ignored."""
    key = key or (lambda url: url)
    reply, pieces, refused, searches, opens = Reply(served=resp.get("model") or ""), [], False, 0, 0
    for item in resp.get("output") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "search":
                searches += 1
            else:
                opens += 1
            for q in [action.get("query"), *(action.get("queries") or [])]:
                if isinstance(q, str) and q.strip() and q.strip() not in reply.queries:
                    reply.queries.append(q.strip())
            for source in action.get("sources") or []:
                if isinstance(source, dict) and isinstance(source.get("url"), str) and key(source["url"]):
                    reply.returned.setdefault(key(source["url"]), (source["url"], source.get("title") or ""))
            if isinstance(action.get("url"), str) and key(action["url"]):
                reply.returned.setdefault(key(action["url"]), (action["url"], ""))
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    refused = True
                if part.get("type") != "output_text":
                    continue
                if pieces:
                    pieces.append("\n")
                base, text = sum(map(len, pieces)), part.get("text") or ""
                pieces.append(text)
                for a in part.get("annotations") or []:
                    if a.get("type") == "url_citation" and isinstance(a.get("url"), str) and key(a["url"]):
                        reply.native.append((base + (a.get("start_index") or 0), base + (a.get("end_index") or 0), a["url"]))
                        reply.returned.setdefault(key(a["url"]), (a["url"], a.get("title") or ""))
    reply.text = "".join(pieces)
    reason = (resp.get("incomplete_details") or {}).get("reason")
    reply.stop = ("refusal" if refused or reason == "content_filter" else "max_tokens" if reason == "max_output_tokens"
                  else "end" if resp.get("status") == "completed" else reason or resp.get("status") or "unknown")
    u = resp.get("usage") or {}
    total, cached = u.get("input_tokens") or 0, (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
    reply.usage = {"in": total - cached, "cached": cached, "cache_write": 0, "out": u.get("output_tokens") or 0,
                   "reasoning": (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0,
                   "searches": searches, "opens": opens, "prompt_max": total}
    return reply


def char_span(text, segment):
    """A grounding segment's span in characters. Google gives byte offsets into the part; the segment's
    own text, when present, settles any doubt."""
    data = text.encode("utf-8")
    start = len(data[:segment.get("startIndex") or 0].decode("utf-8", "ignore"))
    end = len(data[:segment.get("endIndex") or 0].decode("utf-8", "ignore"))
    quoted = segment.get("text")
    if isinstance(quoted, str) and quoted and text[start:end] != quoted and quoted in text:
        start = text.find(quoted)
        end = start + len(quoted)
    return start, end


GEMINI_STOPS = {"STOP": "end", "MAX_TOKENS": "max_tokens", "SAFETY": "refusal", "PROHIBITED_CONTENT": "refusal",
                "BLOCKLIST": "refusal", "SPII": "refusal", "RECITATION": "recitation"}


def gemini_reply(resp):
    """A generateContent reply: the first candidate's text (thought summaries left out), its grounding
    chunks and supports as spans, its search queries and usage (thinking billed as output)."""
    reply = Reply(served=resp.get("modelVersion") or "")
    candidates = resp.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    offsets, pieces = {}, []
    for i, part in enumerate((candidate.get("content") or {}).get("parts") or []):
        if isinstance(part.get("text"), str) and not part.get("thought"):
            offsets[i] = (sum(map(len, pieces)), part["text"])
            pieces.append(part["text"])
    reply.text = "".join(pieces)
    finish = candidate.get("finishReason") or ""
    blocked = (resp.get("promptFeedback") or {}).get("blockReason")
    reply.stop = "refusal" if blocked else GEMINI_STOPS.get(finish, finish.lower() or "unknown")
    grounding = candidate.get("groundingMetadata") or {}
    reply.queries = list(dict.fromkeys(q.strip() for q in grounding.get("webSearchQueries") or [] if isinstance(q, str) and q.strip()))
    chunks = []
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = web.get("uri") if isinstance(web.get("uri"), str) else None
        chunks.append(uri)
        if uri:
            reply.redirects.setdefault(uri, web.get("title") or "")
    for support in grounding.get("groundingSupports") or []:
        segment = support.get("segment") or {}
        index = segment.get("partIndex") or 0        # proto3 JSON leaves out zeros
        if index not in offsets:
            continue
        base, text = offsets[index]
        start, end = char_span(text, segment)
        for k in support.get("groundingChunkIndices") or []:
            if isinstance(k, int) and 0 <= k < len(chunks) and chunks[k]:
                reply.native.append((base + start, base + end, chunks[k]))
    u = resp.get("usageMetadata") or {}
    prompt, cached, tool = u.get("promptTokenCount") or 0, u.get("cachedContentTokenCount") or 0, u.get("toolUsePromptTokenCount") or 0
    thoughts = u.get("thoughtsTokenCount") or 0
    reply.usage = {"in": prompt - cached + tool, "cached": cached, "cache_write": 0,
                   "out": (u.get("candidatesTokenCount") or 0) + thoughts, "reasoning": thoughts,
                   "searches": len(reply.queries), "opens": 0, "prompt_max": prompt + tool}
    return reply


def gemini_schema(schema):
    """The schema without additionalProperties, which Gemini's responseJsonSchema may not take; the
    answer is validated by the caller either way."""
    schema = copy.deepcopy(schema)

    def strip(node):
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            for value in node.values():
                strip(value)
        elif isinstance(node, list):
            for value in node:
                strip(value)
    strip(schema)
    return schema


def request(model, effort, system, prompt, key, schema=None, max_tokens=MAX_TOKENS, tool_calls=12, url_key=None):
    """One request to an OpenAI or Google model. With `schema`: structured output and no tools. Without:
    the provider's web search (OpenAI's web_search, at most `tool_calls` calls, with the sources each
    search returned; Google's Grounding with Google Search). Returns a Reply; raises ProviderError when
    the provider refuses the request. Claude goes through the anthropic SDK in story.py instead."""
    provider = MODELS.get(model)
    if provider == "openai":
        body = {"model": model, "instructions": system, "input": prompt, "reasoning": {"effort": effort},
                "max_output_tokens": max_tokens, "store": False}
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": True}}
        else:
            body.update(tools=[{"type": "web_search"}], include=["web_search_call.action.sources"], max_tool_calls=tool_calls)
        return openai_reply(post_json(OPENAI_URL, body, {"Authorization": f"Bearer {key}"}), url_key)
    if provider == "google":
        config = {"thinkingConfig": {"thinkingLevel": effort}, "maxOutputTokens": max_tokens}
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": config}
        if schema is not None:
            config.update(responseMimeType="application/json", responseJsonSchema=gemini_schema(schema))
        else:
            body["tools"] = [{"google_search": {}}]
        # The key goes in a header, never the URL, which error messages and logs can show.
        return gemini_reply(post_json(GEMINI_URL.format(model=model), body, {"x-goog-api-key": key}))
    raise ValueError(f"{model} isn't an OpenAI or Google model this module calls")


def cost(model, usage):
    """USD at list prices, or None for a model without a recorded price. `usage` is normalized: `in` is
    input billed at the full rate, `cached` input read from a cache, `cache_write` input written to
    Claude's cache, `out` output with any reasoning, `searches` and `opens` the search tool's calls,
    `prompt_max` the largest single prompt (which picks a long-prompt card)."""
    get = lambda k: usage.get(k) or 0
    provider = MODELS.get(model)
    if provider == "anthropic":
        p_in, p_write, p_read, p_out = ANTHROPIC_PRICES[model]
        limit, long_prices = ANTHROPIC_LONG.get(model, (None, None))
        if limit is not None and get("prompt_max") > limit:
            p_in, p_write, p_read, p_out = long_prices    # as if every request were long: exact for one request, an upper bound otherwise
        return (get("in") * p_in + get("cache_write") * p_write + get("cached") * p_read + get("out") * p_out
                + get("searches") * ANTHROPIC_SEARCH)
    if provider == "openai":
        p_in, p_cached, p_out = OPENAI_PRICES[model]
        # Every web_search_call item is a call, page opens included: an upper bound if only searches are billed.
        return get("in") * p_in + get("cached") * p_cached + get("out") * p_out + (get("searches") + get("opens")) * OPENAI_SEARCH
    if provider == "google":
        p_in, p_cached, p_out = GEMINI_PRICES[model]
        limit, long_prices = GEMINI_LONG.get(model, (None, None))
        if limit is not None and get("prompt_max") > limit:
            p_in, p_cached, p_out = long_prices
        return get("in") * p_in + get("cached") * p_cached + get("out") * p_out + get("searches") * GEMINI_SEARCH
    return None
