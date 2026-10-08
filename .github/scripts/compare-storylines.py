#!/usr/bin/env python3
"""Lays storyline runs side by side as Markdown, for choosing a model and effort on evidence.

Reads the facts the runs were given and, for each configuration, the story it wrote (LABEL.json)
and its usage report (LABEL.usage.json, from story.py --usage-out). Prints a table of cost, time,
searches and notes, then each run's headline, lede and notes with the sites they cite, and its
forecast, so the writing and the sourcing can be judged next to what each run cost. Nothing here
grades the writing: that is the reader's call, with the sources open.

For runs in ratings mode (Claude's three scores and nothing else) there is no writing to read, so
it measures instead how far the configurations agree: with each other, with the ratings the page
publishes now (PUBLISHED, story.json) and with the page's own Outlook score (from PAGE, the built
page). No configuration is the truth, the published one included; the agreement between two runs of
the same model shows how much of a difference is noise. Each pair is compared by Spearman's rank
correlation over the fixtures both rated, with a 95% interval, and by the decision the ratings
drive: the three highest-rated matches each day on the default lineup.

Usage: compare-storylines.py FACTS DIR [PAGE [PUBLISHED]]
"""
import glob
import html
import json
import math
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def host(url):
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def site(source):
    """What the page calls a source: its site, or ESPN's facts when story.py marked it so."""
    facts = source.get("kind") == "facts" or source.get("title") == "ESPN match facts"
    return "ESPN table and form" if facts else host(source["url"])


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")



def ranks(xs):
    """1-based ranks, ties sharing the mean of their places."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a, b):
    """Spearman's rho over the keys two {fixture: score} maps share, with a 95% interval from Fisher's z
    (variance 1.06 / (n - 3), for ranks). Returns (rho, low, high, n), or None when fewer than four
    fixtures are shared or either side is constant, which has no rank order to compare."""
    keys = sorted(set(a) & set(b))
    n = len(keys)
    if n < 4:
        return None
    rx, ry = ranks([a[k] for k in keys]), ranks([b[k] for k in keys])
    mean = (n + 1) / 2
    sxy = sum((x - mean) * (y - mean) for x, y in zip(rx, ry))
    sxx, syy = sum((x - mean) ** 2 for x in rx), sum((y - mean) ** 2 for y in ry)
    if sxx == 0 or syy == 0:
        return None
    rho = sxy / math.sqrt(sxx * syy)
    z, se = math.atanh(max(-0.999999, min(0.999999, rho))), math.sqrt(1.06 / (n - 3))
    return rho, math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se), n


def page_rows(path):
    """Each schedule row of the built page: {fixture: (Eastern date, on the default lineup, Outlook score, name)}."""
    try:
        with open(path, encoding="utf-8") as f:
            page = f.read()
    except OSError:
        return {}
    out = {}
    for tag in re.findall(r'<li class="row [^>]*>', page):
        attr = lambda name: html.unescape((re.search(f' data-{name}="([^"]*)"', tag) or [None, ""])[1])
        try:
            day = datetime.fromisoformat(attr("utc").replace("Z", "+00:00")).astimezone(ET).date()
            outlook = float(attr("outlook"))
        except ValueError:
            continue
        out[attr("id")] = (day, attr("svc") not in ("", "none"), outlook, f"{attr('home')} v {attr('away')}")
    return out


def top_three_by_day(scores, rows):
    """{Eastern date: the three highest-scoring fixtures on the default lineup}, ties broken by fixture ID."""
    days = {}
    for mid, value in scores.items():
        if mid in rows and rows[mid][1]:
            days.setdefault(rows[mid][0], []).append((-value, mid))
    return {day: [mid for _, mid in sorted(group)[:3]] for day, group in days.items()}


def ratings_report(runs, rows, published, names):
    """The agreement section for ratings-mode runs, as Markdown lines."""
    sources = []
    for label, u, story in runs:
        if u.get("mode") == "ratings" and story and story.get("rankings"):
            sources.append((f"{u.get('model', label)} at {u.get('effort', '?')}", {k: r["score"] for k, r in story["rankings"].items()}))
    if not sources:
        return []
    window = set().union(*(set(scores) for _, scores in sources))
    reference = None
    if published and isinstance(published.get("rankings"), dict) and published["rankings"]:
        ref = {k: r["score"] for k, r in published["rankings"].items() if k in window and isinstance(r, dict)
               and isinstance(r.get("score"), (int, float))}
        reference = (f"published ({published.get('model', '?')} at {published.get('effort', '?')}, {published.get('kind', '?')} "
                     f"run of {published.get('generated_at', '?')})", ref)
    outlook = ("Outlook score", {k: v[2] for k, v in rows.items() if k in window})
    columns = sources + ([reference] if reference else []) + ([outlook] if outlook[1] else [])
    lines = ["## Ratings agreement", "",
             f"{len(window)} fixtures in the window. Spearman's rank correlation over the fixtures each pair shares "
             "(1 = the same order, 0 = unrelated), with the number shared; the configurations' own disagreement is the noise floor.", "",
             "| | " + " | ".join(name for name, _ in columns) + " |", "|---" * (len(columns) + 1) + "|"]
    for name, scores in columns:
        cells = []
        for other_name, other in columns:
            s = spearman(scores, other) if other_name != name else None
            cells.append("" if other_name == name else f"{s[0]:.2f} (n={s[3]})" if s else "n/a")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if reference:
        lines += ["", "95% intervals against the published ratings:"]
        for name, scores in sources + ([outlook] if outlook[1] else []):
            s = spearman(scores, reference[1])
            lines.append(f"- {name}: " + (f"{s[0]:.2f} ({s[1]:.2f} to {s[2]:.2f}, n={s[3]})" if s else "n/a"))
    base_name, base = reference if reference else sources[0]
    base_top = top_three_by_day(base, rows)
    if base_top:
        lines += ["", f"Top three each day on the default lineup, against {base_name.split(' (')[0]}: how many of its three "
                  "each one also picks, and whether it puts the same match first.", "",
                  "| Day | " + " | ".join(name for name, _ in columns if name != base_name) + " |",
                  "|---" * len(columns) + "|"]
        totals = {}
        for day in sorted(base_top):
            cells = []
            for name, scores in columns:
                if name == base_name:
                    continue
                top = top_three_by_day(scores, rows).get(day, [])
                shared, first = len(set(top) & set(base_top[day])), bool(top) and top[0] == base_top[day][0]
                totals.setdefault(name, []).append((shared, first))
                cells.append(f"{shared}/{len(base_top[day])}" + (" · same #1" if first else ""))
            lines.append(f"| {day:%a %b} {day.day} | " + " | ".join(cells) + " |")
        lines.append("| Overall | " + " | ".join(
            f"{sum(v[0] for v in totals[name]) / len(totals[name]):.1f} of 3 · same #1 on {sum(v[1] for v in totals[name])} of {len(totals[name])} days"
            for name, _ in columns if name != base_name) + " |")
    for name, scores in sources:
        best = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        lines += ["", f"**{name}, top ten:** " + "; ".join(f"{names.get(mid, mid)} {value:g}" for mid, value in best)]
    return lines + [""]


def main():
    facts_path, folder = sys.argv[1], sys.argv[2]
    rows = page_rows(sys.argv[3]) if len(sys.argv) > 3 else {}
    published = load(sys.argv[4]) if len(sys.argv) > 4 else None
    facts = load(facts_path) or {}
    names = {}
    for key in ("next_24_hours", "later_if_needed", "ranking_candidates"):
        for m in facts.get(key, []):
            names[m["id"]] = f"{m['home']['name']} v {m['away']['name']}"
    runs = []
    for usage_path in sorted(glob.glob(os.path.join(folder, "*.usage.json"))):
        label = os.path.basename(usage_path)[: -len(".usage.json")]
        runs.append((label, load(usage_path) or {}, load(os.path.join(folder, label + ".json"))))

    print(f"## Storyline comparison for {facts.get('weekday', '')} {facts.get('date', '')}")
    print()
    print("Each run researched the same facts from scratch, a few minutes apart, so the web they searched differs "
          "slightly. Costs are estimates at list prices. Judge accuracy with the sources open.")
    print()
    print("| Configuration | Cost | Time | Searches | Page reads | Notes kept | Dropped | Rated | Headline |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, u, story in runs:
        usage = u.get("usage") or {}
        c = u.get("cost_usd")
        cost = (f"${c:.2f}" if c >= 0.10 else f"${c:.4f}") if isinstance(c, (int, float)) else "n/a"   # a ratings run can cost a fraction of a cent
        served = f" (served by {u['served']})" if u.get("served") and u.get("served") != u.get("model") else ""
        headline = (story.get("headline") or (f"scores only, next {story.get('window_hours', '?')} hours" if story.get("kind") == "ratings"
                    else f"{len(story.get('league_blurbs') or [])} league blurbs")) if story else "*no story*"
        print(f"| {cell(u.get('model', label))} at {cell(u.get('effort', '?'))}{cell(served)} | {cost} | {u.get('seconds', '?')}s "
              f"| {usage.get('searches', '?')} | {usage.get('fetches', '?')} | {u.get('notes', 0)} | {u.get('dropped', '?')} "
              f"| {u.get('rated', '?')} | {cell(headline)} |")
    print()
    for line in ratings_report(runs, rows, published, names):
        print(line)
    for label, u, story in runs:
        if u.get("mode") == "ratings":
            continue        # nothing written to read: the agreement section above covers its scores
        print(f"<details><summary><b>{cell(u.get('model', label))} at {cell(u.get('effort', '?'))}</b></summary>")
        print()
        if not story:
            print("No story was written; see this configuration's log group.")
        else:
            for blurb in sorted(story.get("league_blurbs") or [], key=lambda b: -b["interest"]):
                sites = ", ".join(f"[{site(s)}]({s['url']})" for s in blurb.get("sources") or [])
                print(f"**{blurb['league_id']} · interest {blurb['interest']}/100**")
                print()
                print(f"{blurb['text']} ({sites})")
                print()
            if story.get("headline"):
                print(f"**{story['headline']}**")
                print()
            if story.get("lede"):
                print(story["lede"])
            lede_sites = ", ".join(site(s) for s in story.get("sources") or [])
            if lede_sites:
                print(f"*Sources: {lede_sites}*")
            print()
            for mid, n in (story.get("notes") or {}).items():
                sites = ", ".join(f"[{site(s)}]({s['url']})" for s in n.get("sources") or [])
                print(f"- **{names.get(mid, mid)}**: {n.get('note', '')} ({sites})")
            print()
            fc = story.get("forecast")
            if fc:
                for item in fc.get("items", []):
                    print(f"- *Forecast:* {item['text']} (matches: {', '.join(item['match_ids'])})")
            else:
                print("*No forecast; the page shows schedule facts.*")
            ratings = story.get("rankings") or {}
            print()
            print(f"**Ratings:** {len(ratings)} of {len(facts.get('ranking_candidates', []))} fixtures. "
                  "Fixed standout threshold: 80/100.")
            facts_only = sum(1 for r in ratings.values() if r.get("sources") and all(site(s) == "ESPN table and form" for s in r["sources"]))
            print(f"{sum(1 for r in ratings.values() if r.get('blurb'))} card blurbs, {facts_only} resting only on ESPN's table and form.")
            for mid, rating in sorted(ratings.items(), key=lambda pair: -pair[1]["score"])[:10]:
                print(f"- **{names.get(mid, mid)}: {rating['score']}** "
                      f"(popularity {rating['popularity']}, gameplay {rating['gameplay']}, impact {rating['impact']})"
                      + (f": {rating['blurb']}" if rating.get("blurb") else ""))
        print()
        print("</details>")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
