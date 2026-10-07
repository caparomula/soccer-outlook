#!/usr/bin/env python3
"""Lays storyline runs side by side as Markdown, for choosing a model and effort on evidence.

Reads the facts the runs were given and, for each configuration, the story it wrote (LABEL.json)
and its usage report (LABEL.usage.json, from story.py --usage-out). Prints a table of cost, time,
searches and notes, then each run's headline, lede and notes with the sites they cite, and its
forecast, so the writing and the sourcing can be judged next to what each run cost. Nothing here
grades the writing: that is the reader's call, with the sources open.

Usage: compare-storylines.py FACTS DIR
"""
import glob
import json
import os
import sys
from urllib.parse import urlsplit


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def host(url):
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def main():
    facts_path, folder = sys.argv[1], sys.argv[2]
    facts = load(facts_path) or {}
    names = {}
    for key in ("next_24_hours", "later_if_needed"):
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
    print("| Configuration | Cost | Time | Searches | Page reads | Notes kept | Dropped | Headline |")
    print("|---|---|---|---|---|---|---|---|")
    for label, u, story in runs:
        usage = u.get("usage") or {}
        cost = f"${u['cost_usd']:.2f}" if isinstance(u.get("cost_usd"), (int, float)) else "n/a"
        served = f" (served by {u['served']})" if u.get("served") and u.get("served") != u.get("model") else ""
        headline = story.get("headline", "") if story else "*no story*"
        print(f"| {cell(u.get('model', label))} at {cell(u.get('effort', '?'))}{cell(served)} | {cost} | {u.get('seconds', '?')}s "
              f"| {usage.get('searches', '?')} | {usage.get('fetches', '?')} | {u.get('notes', 0)} | {u.get('dropped', '?')} "
              f"| {cell(headline)} |")
    print()
    for label, u, story in runs:
        print(f"<details><summary><b>{cell(u.get('model', label))} at {cell(u.get('effort', '?'))}</b></summary>")
        print()
        if not story:
            print("No story was written; see this configuration's log group.")
        else:
            print(f"**{story.get('headline', '')}**")
            print()
            print(story.get("lede", ""))
            lede_sites = ", ".join(host(s["url"]) for s in story.get("sources") or [])
            if lede_sites:
                print(f"*Sources: {lede_sites}*")
            print()
            for mid, n in (story.get("notes") or {}).items():
                sites = ", ".join(f"[{host(s['url'])}]({s['url']})" for s in n.get("sources") or [])
                print(f"- **{names.get(mid, mid)}**: {n.get('note', '')} ({sites})")
            print()
            fc = story.get("forecast")
            if fc:
                for item in fc.get("items", []):
                    print(f"- *Forecast:* {item['text']} (matches: {', '.join(item['match_ids'])})")
            else:
                print("*No forecast; the page shows schedule facts.*")
        print()
        print("</details>")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
