#!/usr/bin/env python3
"""Offline P2 league/filter proof with independent and frozen browser answers."""
import argparse
import base64
from datetime import timedelta
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tool.companion_p2_verify import (FixtureBindings, assert_response, command_checks, require,
                                     serve, sha, source_hashes as first_family_hashes, verify_runtime)

BASELINE = ROOT / "tests/contracts/p2/eligibility-baseline-2026-10-20.json"
EXAMPLES = ROOT / "docs/companion/rules/eligibility-examples.json"


def frozen_check(scenario):
    """Adapt recorded JS answers into the new wire shape, without recomputing rules."""
    old = scenario["projection"]
    catalog = {league["id"]: league for league in old["catalog"]}
    league_answers = {league["id"]: league for league in old["leagues"]}
    order = old["league_order"]
    case = dict(id=scenario["id"],
                leagues=[dict(id=key, default_followed=catalog[key]["default_enabled"]) for key in order],
                preferences=dict(choices=old["choices"], paused=old["paused"], solo_id=old["solo"]),
                has_selected_services=bool(old["selected_service_ids"]),
                fixtures=[dict(id=row["id"], league_id=row["league_id"], has_route=row["has_selected_route"],
                               in_window=row["in_window"]) for row in old["fixtures"]])
    # The old fixture answer records league visibility; use that answer for
    # visible leagues with fixtures and the recorded followed/hidden values for
    # fixture-free calendar leagues. Neither calls the implementation under test.
    visible = {row["league_id"]: row["expected"]["league_visible"] for row in old["fixtures"]}
    leagues = []
    for key in order:
        previous = league_answers[key]
        leagues.append(dict(id=key, followed=previous["followed"], hidden_by_strip=previous["hidden_by_strip"],
                            visible=visible.get(key, previous["followed"] and not previous["hidden_by_strip"]),
                            calendar_editorial_eligible=previous["calendar_editorial_eligible"]))
    answer = dict(id=case["id"], active_solo_id=old["solo"], solo_reset_reason=None,
                  strip_league_ids=old["strip_league_ids"], leagues=leagues,
                  fixtures=[dict(id=row["id"], eligible=row["expected"]["passes_filters"],
                                 schedule_eligible=row["expected"]["schedule_visible"]) for row in old["fixtures"]])
    return dict(id="legacy:" + scenario["id"], projection="complete",
                request=dict(version=1, operation="viewer_eligibility", cases=[case]),
                expected=dict(version=1, results=[answer]))


def load_checks():
    examples = json.loads(EXAMPLES.read_text())
    require(examples["version"] == 1 and examples["cases"], "Independent eligibility examples are missing")
    checks = []
    for case in examples["cases"]:
        require(case.get("because"), "Independent example must explain its expected answer")
        checks.append(dict(id="independent:" + case["id"], projection="complete",
                           request=case["request"], expected=case["expected"]))
    baseline = json.loads(BASELINE.read_text())
    require(baseline["fixture_count"] == 360 and len(baseline["scenarios"]) == 9, "Frozen baseline scope changed")
    for scenario in baseline["scenarios"]:
        require(len(scenario["projection"]["fixtures"]) == 360, "A baseline lost fixtures")
        checks.append(frozen_check(scenario))
    return checks


def invalid_requests(checks):
    case = next(check["request"]["cases"][0] for check in checks if check["id"] == "legacy:defaults")
    def payload(change):
        value = json.loads(json.dumps(case))
        change(value)
        return dict(version=1, operation="viewer_eligibility", cases=[value])
    return [payload(lambda value: value["leagues"].append(value["leagues"][0])),
            payload(lambda value: value["fixtures"].append(value["fixtures"][0])),
            payload(lambda value: value.update(has_selected_services="true")),
            payload(lambda value: value["preferences"]["choices"].update({"eng.1": 1})),
            payload(lambda value: value.update(unknown_field=True))]


def source_hashes():
    values = first_family_hashes()
    for path in (Path(__file__), BASELINE, EXAMPLES, ROOT / "tests/test_companion_eligibility.py",
                 ROOT / "tests/browser.py", ROOT / "tests/browser_schedule.py"):
        values[str(path.relative_to(ROOT))] = sha(path)
    return dict(sorted(values.items()))


def write_pages(out):
    import build
    from tests.page_fixture import render_page
    fixtures = [("english", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                ("spanish", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1"),
                ("disabled", "2026-10-07T20:00:00+00:00", "pre", "HBO Max", "fifa.friendly.w"),
                ("no-route", "2026-10-07T21:00:00+00:00", "pre", "Mystery Sports+", "ger.1")]
    html = render_page(build, fixtures=fixtures, ai=False, inline_runtime=False)
    parsed = FixtureBindings(html)
    require(len(parsed.rows) == 4, "Static page must retain every fixture, including hidden/unavailable ones")
    require(len(parsed.external_scripts) == 1 and parsed.external_scripts[0]["src"] == build.CORE_ASSET,
            "Consumer proof must use actual production script packaging")
    build.outlook_runtime.publish_browser(out)
    digest = sha(out / build.CORE_ASSET)
    require(digest == sha(build.outlook_runtime.artifacts() / "outlook.js"), "Published core bytes differ")
    require(build.CORE_ASSET == f"outlook-core-{digest}.js" and parsed.external_scripts[0]["integrity"] ==
            "sha256-" + base64.b64encode(bytes.fromhex(digest)).decode(), "Production script lacks the expected content address or SRI")
    (out / "rendered.html").write_text(html)
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>Eligibility proof</title>')
    return fixtures, build.CORE_ASSET, parsed.rows


def browser_protocol(engine, base, checks):
    errors = []
    with engine.new_context() as context:
        context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname == "127.0.0.1"
                      else route.fulfill(status=404, body=""))
        context.add_init_script("window.__violations=[];addEventListener('securitypolicyviolation',e=>__violations.push(e.violatedDirective));")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/index.html")
        for check in checks:
            assert_response(check, page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(check["request"])))
        for request in invalid_requests(checks):
            require(page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(request)) ==
                    dict(version=1, error="invalid_input"), "Malformed eligibility input was accepted")
        require(not errors and not page.evaluate("__violations"), "Protocol browser error or strict CSP violation")
    return dict(checks=len(checks), malformed_requests_rejected=5, strict_csp=True, page_errors=errors)


def browser_consumer(engine, base, fixtures, core_asset, bindings):
    from playwright.sync_api import expect
    from tests.browser import BrowserChecks
    from tests.browser_schedule import snapshot
    from tests.page_fixture import BUILT_AT
    errors, requests, transitions = [], [], []
    state = {"snapshot": None}
    with engine.new_context(timezone_id="America/New_York", viewport={"width": 390, "height": 844}) as context:
        def route(request):
            path = urlsplit(request.request.url).path
            if urlsplit(request.request.url).hostname == "127.0.0.1" and path in ("/rendered.html", "/" + core_asset):
                requests.append(path)
                request.continue_()
            elif path.endswith("/schedule.json") and state["snapshot"] is not None:
                request.fulfill(content_type="application/json", body=json.dumps(state["snapshot"]))
            else:
                request.fulfill(status=404, body="")
        context.route("**/*", route)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install(time=BUILT_AT - timedelta(seconds=1))
        page.clock.pause_at(BUILT_AT)
        page.goto(base + "/rendered.html")
        expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
        def shown(label, ids, strip):
            actual = page.locator("#outlook-body li.row").evaluate_all("rows=>rows.filter(r=>!r.hidden).map(r=>r.dataset.id).sort()")
            require(actual == sorted(ids), f"{label}: incorrect schedule fixture filter {actual}")
            controls = page.locator("#league-strip-list [data-league]").evaluate_all("buttons=>buttons.map(b=>b.dataset.league)")
            require(controls == strip, f"{label}: incorrect strip membership {controls}")
            for fixture_id in ids:
                row = page.locator(f'#outlook-body li.row[data-id="{fixture_id}"]')
                factual = row.evaluate("(row,names)=>Object.fromEntries(names.map(name=>[name,row.getAttribute(name)]))", list(FixtureBindings.names))
                require(factual == bindings[fixture_id], f"{label}: source fixture bindings changed")
            require(not errors, f"{label}: page errors {errors}")
            transitions.append(dict(name=label, visible_ids=actual, strip_ids=controls))
        shown("initial", ["english", "spanish"], ["eng.1", "esp.1"])
        page.locator('#league-strip-list [data-league="esp.1"]').click()
        shown("paused", ["english"], ["eng.1", "esp.1"])
        saved = page.evaluate("localStorage.getItem('ssg1-league-paused')")
        require(json.loads(saved) == ["esp.1"], "Pause was not persisted")
        page.locator('#league-strip-list [data-league="esp.1"]').focus()
        page.keyboard.press("Shift+Enter")
        shown("solo_over_pause", ["spanish"], ["eng.1", "esp.1"])
        require(page.evaluate("localStorage.getItem('ssg1-league-paused')") == saved, "Solo changed saved pause")
        page.keyboard.press("Shift+Enter")
        shown("restore_pause", ["english"], ["eng.1", "esp.1"])
        page.reload()
        expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
        shown("reload_preserves_pause", ["english"], ["eng.1", "esp.1"])
        BrowserChecks.open_settings(page, "leagues")
        page.locator('[data-kind="comp"][data-key="eng.1"]').click()
        page.locator("#btn-filters-close").click()
        shown("explicit_disabled", [], ["esp.1"])
        saved_choices = page.evaluate("localStorage.getItem('ssg5-leagues')")
        expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
        state["snapshot"] = snapshot([], generated="2026-10-07T17:01:00Z")
        page.clock.fast_forward(5 * 60000 + 100)
        expect(page.locator("#app")).to_have_attribute("data-schedule-built", "2026-10-07T17:01:00Z")
        shown("empty_fixture_week", [], [])
        require(page.evaluate("localStorage.getItem('ssg5-leagues')") == saved_choices, "Empty snapshot discarded explicit choices")
        BrowserChecks.open_settings(page, "ai")
        page.locator("#ai-generate").click()
        expect(page.locator("#ai-error")).to_be_empty()
        calendar = BrowserChecks.personal_ai_snapshot(page)
        require(calendar["matches"] == [], "Empty week prompt fabricated fixtures")
        calendar_ids = [league["id"] for league in calendar["league_order"]]
        require("uefa.champions" in calendar_ids and "eng.1" not in calendar_ids and "esp.1" not in calendar_ids,
                "Fixture-free calendar eligibility ignored saved choices or pause")
        page.locator("#btn-filters-close").click()
        state["snapshot"] = snapshot(fixtures, generated="2026-10-07T17:06:00Z")
        page.clock.fast_forward(5 * 60000 + 100)
        expect(page.locator("#app")).to_have_attribute("data-schedule-built", "2026-10-07T17:06:00Z")
        shown("fixtures_return_choices_retained", [], ["esp.1"])
        require(page.evaluate("localStorage.getItem('ssg5-leagues')") == saved_choices, "Returning fixtures reset explicit choices")
        BrowserChecks.open_settings(page, "leagues")
        page.locator('[data-kind="comp"][data-key="fifa.friendly.w"]').click()
        page.locator("#btn-filters-close").click()
        shown("default_off_explicitly_enabled", ["disabled"], ["esp.1", "fifa.friendly.w"])
        BrowserChecks.open_settings(page)
        page.locator("#btn-clear").click()
        page.locator("#btn-filters-close").click()
        shown("empty_services", [], [])
        BrowserChecks.open_settings(page, "ai")
        page.locator("#ai-generate").click()
        expect(page.locator("#ai-error")).not_to_be_empty()
        require(not errors, f"Consumer JavaScript errors: {errors}")
    return dict(transitions=transitions, calendar_only_league_ids=calendar_ids, page_errors=errors,
                field_bindings_preserved=True,
                production_core_requests=sum(path.endswith(core_asset) for path in requests))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--runtime", type=Path, default=ROOT / "work/outlook-runtime")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-eligibility")
    args = parser.parse_args()
    out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checks = load_checks()
    before = source_hashes()
    verify_runtime(runtime)
    os.environ["OUTLOOK_RUNTIME_DIR"] = str(runtime)
    version = subprocess.run([args.dart, "--version"], capture_output=True, text=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    require(f"version: {pin} " in dart_version, "Dart VM does not match the pinned compiler")
    shutil.copyfile(runtime / "outlook.js", out / "outlook.js")
    size = len(gzip.compress((runtime / "outlook.js").read_bytes(), mtime=0))
    limit = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
    require(size <= limit, "Full core exceeds gzip budget")
    fixtures, core_asset, bindings = write_pages(out)
    commands = dict(dart_vm=[args.dart, "--suppress-analytics", "run", "bin/outlook.dart"],
                    compiled_cli=[str(runtime / "outlook")])
    outcomes = {name: command_checks(command, checks) for name, command in commands.items()}
    from tool.companion_p2_verify import run
    for command in commands.values():
        for request in invalid_requests(checks):
            output, _ = run(command, json.dumps(request), expected_exit=65)
            require(json.loads(output) == dict(version=1, error="invalid_input"), "Malformed eligibility payload accepted")
    from playwright.sync_api import sync_playwright
    browsers = {}
    with serve(out) as base, sync_playwright() as playwright:
        for name in ("chromium", "webkit"):
            with getattr(playwright, name).launch() as engine:
                browsers[name] = dict(version=engine.version, protocol=browser_protocol(engine, base, checks),
                                      generated_page=browser_consumer(engine, base, fixtures, core_asset, bindings))
    require(before == source_hashes(), "Eligibility sources changed during proof")
    verify_runtime(runtime)
    report = dict(version=1, status="passed", source_hashes=before, sources_unchanged=True,
                  dart_version=dart_version,
                  independent_requests=sum(check["id"].startswith("independent:") for check in checks),
                  frozen_browser_scenarios=9, frozen_fixture_comparisons=3240,
                  runtime_hashes={name: sha(runtime / name) for name in ("runtime.json", "outlook", "outlook.js")},
                  browser_gzip_bytes=size, browser_gzip_limit=limit, consumers=outcomes, browsers=browsers,
                  limitations=["Source/status/time buckets are supplied inputs; this extraction does not prove their correctness.",
                               "Frozen previous-browser results establish migration parity; independent rule examples provide a separate oracle.",
                               "All source and optional AI requests are intercepted; creating a local prompt does not call an AI service.",
                               "No publication, distribution, physical-device or performance acceptance is claimed."])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "independent_requests", "frozen_fixture_comparisons", "browser_gzip_bytes")}))


if __name__ == "__main__":
    main()
