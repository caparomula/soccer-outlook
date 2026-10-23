#!/usr/bin/env python3
"""P0 evidence only: capture the deployed legacy website without live providers.

This is not a companion feed parser or a production domain implementation.
Raw public responses stay under work/; the compact report contains observations.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

BASE_URL = "https://caparomula.github.io/soccer-outlook/"
FILES = ("index.html", "schedule.json", "coverage.json", "schedule-context.json",
         "story.json", "shared-ai.json")
MAX_BYTES = 8 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class PublicationHTML(HTMLParser):
    """Read emitted attributes only; do not recompute entitlement or scores."""

    def __init__(self, text):
        super().__init__()
        self.rows = []
        self.app = {}
        self.json_blocks = {}
        self.external_scripts = []
        self._script = None
        self.feed(text)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if attrs.get("id") == "app":
            self.app = attrs
        if tag == "li" and "row" in attrs.get("class", "").split():
            self.rows.append(attrs)
        if tag == "script" and attrs.get("type") == "application/json":
            self._script = attrs.get("id")
            self.json_blocks[self._script] = ""
        if tag == "script" and "src" in attrs:
            self.external_scripts.append(attrs)

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None

    def handle_data(self, value):
        if self._script:
            self.json_blocks[self._script] += value


def referenced_runtime(raw_html):
    """Accept only the exact adjacent content-addressed script emitted by the builder."""
    scripts = PublicationHTML(raw_html.decode("utf-8")).external_scripts
    if not scripts:  # Historical captures contain an inline runtime.
        return None
    if len(scripts) != 1:
        raise ValueError("Captured page must reference one adjacent Outlook runtime")
    script = scripts[0]
    match = re.fullmatch(r"outlook-core-([0-9a-f]{64})\.js", script["src"])
    if not match:
        raise ValueError("Captured page runtime must be an adjacent content-addressed asset")
    integrity = "sha256-" + base64.b64encode(bytes.fromhex(match[1])).decode()
    if script.get("integrity") != integrity:
        raise ValueError("Captured page runtime integrity differs from its asset name")
    return script["src"]


def validate_runtime_resource(records, responses):
    name = referenced_runtime(responses["index.html"])
    if name is None:
        return
    if name not in responses or records.get(name, {}).get("status") != 200:
        raise ValueError(f"Captured page runtime is missing: {name}")
    raw = responses[name]
    if len(raw) > MAX_BYTES or name != f"outlook-core-{digest(raw)}.js":
        raise ValueError(f"Captured page runtime changed or exceeds bound: {name}")


def load_browser_resources(site):
    """Load a local publication, including its own runtime; never use the current build."""
    site = Path(site)
    records, responses = {}, {}
    for name in FILES:
        path = site / name
        responses[name] = path.read_bytes() if path.exists() else b"{}"
        records[name] = {"status": 200 if path.exists() else 404}
    name = referenced_runtime(responses["index.html"])
    if name is not None:
        path = site / name
        if path.resolve().parent != site.resolve():
            raise ValueError("Captured page runtime escapes the publication directory")
        if not path.is_file():
            raise ValueError(f"Captured page runtime is missing: {name}")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f"Captured page runtime exceeds bound: {name}")
        responses[name] = path.read_bytes()
        records[name] = {"status": 200}
    validate_runtime_resource(records, responses)
    return records, responses


def row_fact(row):
    """Explicit legacy-field allowlist; zero and null remain distinct."""
    return {
        "source_id": row["data-id"],
        "league": row.get("data-lg"),
        "kickoff": row.get("data-utc") if row.get("data-tv") == "1" else None,
        "time_confirmed": row.get("data-tv") == "1",
        "state": row.get("data-state"),
        "home": row.get("data-home"),
        "away": row.get("data-away"),
        "outlook_total": float(row["data-outlook"]) if row.get("data-outlook") else None,
        "outlook_parts_percent": json.loads(row.get("data-outlook-parts", "{}")),
        "outlets": json.loads(row.get("data-o", "[]")),
        "usual_rule": json.loads(row["data-r"]) if row.get("data-r") else None,
        "published_default": {
            "service": row.get("data-svc"),
            "basis": row.get("data-basis"),
            "outlet": row.get("data-outlet"),
        },
        "vitals": json.loads(row.get("data-ai-facts", "{}")),
        "watch_links": json.loads(row.get("data-watch", "{}")),
    }


def fetch(raw_dir):
    """Six fixed public GETs; no cookies, credentials, retries or redirects."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = {}
    for name in FILES:
        url = BASE_URL + ("" if name == "index.html" else name)
        headers_path = raw_dir / f"{name}.headers"
        path = raw_dir / name
        requested = timestamp()
        result = subprocess.run(
            ["curl", "--disable", "--silent", "--show-error", "--max-time", "30",
             "--max-filesize", str(MAX_BYTES), "--proto", "=https",
             "--dump-header", str(headers_path), "--output", str(path),
             "--write-out", "%{http_code}", url],
            text=True, capture_output=True, check=True,
        )
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError(f"{name} exceeds capture bound")
        # Record only cache/provenance headers, never arbitrary headers.
        headers = {}
        for line in headers_path.read_text().splitlines():
            key, sep, value = line.partition(":")
            if sep and key.lower() in ("content-type", "etag", "last-modified", "date"):
                headers[key.lower()] = value.strip()
        status = int(result.stdout)
        if status not in (200, 404) or (status != 200 and name in FILES[:4]):
            raise ValueError(f"{name}: unexpected HTTP {status}")
        records[name] = {
            "url": url, "requested_at": requested, "received_at": timestamp(),
            "status": status, "bytes": len(raw), "sha256": digest(raw), "headers": headers,
        }
    write_json(raw_dir / "capture-metadata.json", records)
    return records


def load_raw(raw_dir):
    records = json.loads((raw_dir / "capture-metadata.json").read_text())
    responses = {}
    for name in FILES:
        raw = (raw_dir / name).read_bytes()
        if len(raw) > MAX_BYTES or digest(raw) != records[name]["sha256"]:
            raise ValueError(f"Capture changed or exceeds bound: {name}")
        responses[name] = raw
    return records, responses


def publication_report(responses):
    html = PublicationHTML(responses["index.html"].decode())
    schedule = json.loads(responses["schedule.json"])
    context = json.loads(responses["schedule-context.json"])
    coverage = json.loads(responses["coverage.json"])
    schedule_html = PublicationHTML("".join(schedule["rows"]))
    facts = {row["data-id"]: row_fact(row) for row in html.rows}
    updated = {row["data-id"]: row_fact(row) for row in schedule_html.rows}
    context_ids = {str(match["id"]) for match in context["matches"]}
    shared_ids = set(facts) & set(updated)
    return {
        "format": "legacy-html-and-build-cache; not companion-v1",
        "html_built_at": html.app["data-built"],
        "schedule_generated_at": schedule["generated_at"],
        "context_generated_at": context["generated_at"],
        "standings_at": context["standings_at"],
        "coverage_generated_at": coverage["generated_at"],
        "runtime_revisions": {
            "html": html.app["data-runtime-revision"],
            "schedule": schedule["runtime_revision"],
            "context": context["runtime_revision"],
        },
        "source_completeness": {
            "schedule": schedule.get("complete"),
            "coverage": coverage.get("fixture_sources_complete"),
        },
        "fixture_counts": {"html": len(html.rows), "schedule": len(schedule_html.rows),
                           "context": len(context["matches"])},
        "fixture_ids": sorted(facts),
        "differences": {
            "schedule_only_ids": sorted(set(updated) - set(facts)),
            "html_only_ids": sorted(set(facts) - set(updated)),
            "context_only_ids": sorted(context_ids - set(facts)),
            "html_without_context_ids": sorted(set(facts) - context_ids),
            "changed_legacy_fact_ids": sorted(key for key in shared_ids if facts[key] != updated[key]),
        },
        "defaults": {key: json.loads(value) for key, value in html.json_blocks.items()
                     if key in ("service-meta", "scoring")},
        "counts_by_state": dict(Counter(fact["state"] for fact in facts.values())),
        "counts_by_default_basis": dict(Counter(fact["published_default"]["basis"] for fact in facts.values())),
        "counts_by_default_service": dict(Counter(fact["published_default"]["service"] for fact in facts.values())),
        "source_health": coverage,
        "standings_leagues": sorted(context["standings"]),
        "populated_standings_leagues": sorted(key for key, value in context["standings"].items()
                                             if value.get("tables") and value.get("by_team")),
        "all_facts_sha256": digest(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()),
    }, facts, context


BROWSER_OUTPUT = """() => {
  const text = selector => document.querySelector(selector)?.textContent.trim() ?? null;
  const ids = selector => Array.from(document.querySelectorAll(selector),
    el => el.dataset.matchId || el.dataset.id);
  const pressed = kind => Array.from(document.querySelectorAll('[data-kind="' + kind + '"]'),
    el => ({id: el.dataset.key, enabled: el.getAttribute('aria-pressed') === 'true'}));
  return {
    tally: text('#tally-n'), tally_label: text('#tally-txt'),
    defaults: {services: pressed('have'), leagues: pressed('comp'),
      ai_text: document.querySelector('#ai-blurbs').checked},
    schedule_groups: Array.from(document.querySelectorAll('#outlook-body .bucket'), el => ({
      bucket: el.dataset.b ?? 'static', heading: el.querySelector('h3')?.textContent.trim(),
      folded: el.tagName === 'DETAILS' && !el.open,
      fixture_ids: Array.from(el.querySelectorAll('li.row'), row => row.dataset.id)
    })),
    nonhidden_fixture_ids: ids('li.row:not([hidden])'),
    visible_fixture_ids: Array.from(document.querySelectorAll('li.row')).filter(el =>
      !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'
      && !el.closest('[hidden], details:not([open])')).map(el => el.dataset.id),
    parked_fixture_ids: ids('#park li.row'),
    carousel: {selected_ids: ids('#nextup-track [data-match-id][aria-current="true"]'),
      fixture_ids: ids('#nextup-track [data-match-id]')},
    picks: {title: text('#picks-h'), reason: text('#picks-sub'),
      fixture_ids: ids('#picks [data-match-id]')},
    headlines_hidden: document.querySelector('#story').hidden,
    headline_count: document.querySelectorAll('#story-headlines .story-headline').length,
    row_outputs: Array.from(document.querySelectorAll('li.row'), el => ({
      id: el.dataset.id, hidden: el.hidden,
      bucket: el.closest('.bucket')?.dataset.b ?? null,
      scores: Array.from(el.querySelectorAll('b.score'), score => ({
        value: score.textContent, hidden: score.hidden
      })),
      status: el.querySelector('.row__status')?.textContent ?? null,
      rating: el.querySelector('.rating-trigger')?.getAttribute('aria-label') ?? null,
      networks: Array.from(el.querySelectorAll('.network summary'), p => p.textContent.trim())
    }))
  };
}"""


def browser_capture(raw_dir, records, responses, instant, browser_name, zone, width, script=True):
    if script:
        validate_runtime_resource(records, responses)
    from playwright.sync_api import sync_playwright

    # Route fulfillment avoids a local server and prevents any actual browser network.
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch()
        context = browser.new_context(
            viewport={"width": width, "height": 900}, timezone_id=zone, locale="en-US",
            java_script_enabled=script, service_workers="block",
        )
        fulfilled, blocked, errors = [], [], []

        def route_request(route):
            url = route.request.url
            parts = urlsplit(url)
            suffix = parts.path.removeprefix("/soccer-outlook/") or "index.html"
            if (parts.scheme == "https" and parts.netloc == "caparomula.github.io"
                    and parts.path.startswith("/soccer-outlook/") and suffix in responses
                    and route.request.method == "GET" and not parts.query):
                fulfilled.append(suffix)
                content_type = ("text/html" if suffix == "index.html" else
                                "application/javascript" if suffix.endswith(".js") else
                                "application/json")
                route.fulfill(status=records[suffix]["status"], body=responses[suffix],
                              content_type=content_type)
            else:
                # Store origins only, omitting arbitrary query/fragment material.
                blocked.append(parts.scheme + "://" + parts.netloc)
                route.abort()

        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install(time=datetime.fromisoformat(instant.replace("Z", "+00:00")))
        page.clock.pause_at(datetime.fromisoformat(instant.replace("Z", "+00:00")))
        page.goto(BASE_URL, wait_until="networkidle")
        if script:
            page.wait_for_selector("#park", state="attached")
        output = page.evaluate(BROWSER_OUTPUT)
        output.update({
            "browser": browser_name, "browser_version": browser.version,
            "timezone": zone, "width": width, "script": script, "instant": instant,
            "fulfilled_resources": sorted(set(fulfilled)),
            "blocked_origins": dict(Counter(blocked)), "page_errors": errors,
        })
        label = f"{browser_name}-{zone.replace('/', '_')}-{width}-{'script' if script else 'static'}"
        write_json(raw_dir / f"browser-{label}.json", output)
        context.close()
        browser.close()
        return output


def compact_browser(output):
    # Keep complete decision IDs for future migration comparison; omit repeated text.
    return {key: value for key, value in output.items() if key != "row_outputs"} | {
        "row_outputs_sha256": digest(json.dumps(output["row_outputs"], sort_keys=True).encode()),
    }


def editorial_metadata(responses, records):
    """Retain original ages, counts and binding IDs without republishing prose."""
    result = {}
    for name in ("story.json", "shared-ai.json"):
        if records[name]["status"] != 200:
            result[name] = {"http_status": records[name]["status"], "present": False}
            continue
        data = json.loads(responses[name])
        result[name] = {
            "http_status": 200, "present": True,
            "original_metadata": {key: data[key] for key in
                                  ("version", "generated_at", "updated_at", "focus_until",
                                   "prompt_at", "expires_at", "score_calibration", "rating_weights")
                                  if key in data},
            "counts": {key: len(data.get(key, [])) for key in ("notes", "rankings", "league_blurbs")},
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("work/companion-publication"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Fetch exactly the six public resources")
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "webkit"),
                        default=["chromium", "webkit"])
    args = parser.parse_args()
    if args.fetch:
        fetch(args.raw_dir)
    records, responses = load_raw(args.raw_dir)
    publication, facts, context = publication_report(responses)
    instant = publication["html_built_at"]
    outputs = []
    for browser in args.browsers:
        for zone, width in (("America/New_York", 1280), ("America/New_York", 390),
                            ("America/Los_Angeles", 390)):
            outputs.append(browser_capture(args.raw_dir, records, responses, instant, browser, zone, width))
    static = browser_capture(args.raw_dir, records, responses, instant, "chromium", "America/New_York", 1280, False)
    # Include real selected picks and a representative of each emitted state/basis.
    sample_ids = set()
    seen = set()
    for key, fact in facts.items():
        group = (fact["state"], fact["published_default"]["basis"])
        if group not in seen:
            seen.add(group)
            sample_ids.add(key)
    for output in outputs:
        sample_ids.update(output["picks"]["fixture_ids"])
        sample_ids.update(output["carousel"]["selected_ids"])
    context_matches = {str(match["id"]): match for match in context["matches"]}
    report = {
        "evidence_version": 1, "captured_resources": records,
        "conditions": {"fresh_browser_context": True, "external_network_blocked": True,
                       "clock": "HTML publication instant", "locale": "en-US",
                       "credentials": "none; fixed public GETs only"},
        "publication": publication,
        "editorial": editorial_metadata(responses, records),
        "representative_legacy_facts": [facts[key] for key in sorted(sample_ids)],
        "representative_context_scores": {
            key: {side: context_matches[key][side].get("score") for side in ("home", "away")}
            for key in sorted(sample_ids) if key in context_matches
        },
        "representative_context_evidence": {
            key: {field: context_matches[key].get(field) for field in
                  ("broadcast_checked_at", "broadcast_check", "broadcast_evidence")}
            for key in sorted(sample_ids) if key in context_matches
        },
        "representative_browser_rows": {
            label: [row for row in output["row_outputs"] if row["id"] in sample_ids]
            for label, output in (("static", static), ("chromium_eastern", outputs[0]))
        },
        "static_browser": compact_browser(static),
        "browsers": [compact_browser(output) for output in outputs],
    }
    write_json(args.output, report)
    print(json.dumps({"report": str(args.output), "fixtures": publication["fixture_counts"],
                      "samples": len(sample_ids), "browser_runs": len(outputs),
                      "tallies": [static["tally"]] + [output["tally"] for output in outputs],
                      "page_errors": sum(len(output["page_errors"]) for output in outputs)}))


if __name__ == "__main__":
    main()
