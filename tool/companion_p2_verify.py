#!/usr/bin/env python3
"""Offline first-family migration proof, using independent and frozen examples.

Prepare the compiled runtime with tool/build_outlook_runtime.py first. This tool
does not fetch source data or regenerate historical expectations. Frozen legacy
answers establish extraction parity; hand-authored rule examples establish the
intended behavior, including explicitly documented defensive changes.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
import gzip
import hashlib
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from threading import Thread
from time import perf_counter
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "tool/outlook_bridge"
BASELINE = ROOT / "tests/contracts/p2/legacy-route-selection-2026-10-20.json"
RULES = ROOT / "docs/companion/rules"
sys.path.insert(0, str(ROOT))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_cases(baseline_path=BASELINE, rules=RULES):
    """Load expectations; never obtain them by asking the implementation."""
    baseline = json.loads(Path(baseline_path).read_text())
    require(baseline["version"] == 1, "Unsupported frozen corpus")
    require(len(baseline["cases"]) == 360, "Frozen source corpus lost fixtures")
    require(len({case["id"] for case in baseline["cases"]}) == 360,
            "Frozen source corpus has duplicate identities")
    checks = []
    for filename in ("route-examples.json", "sports-day-examples.json"):
        examples = json.loads((Path(rules) / filename).read_text())
        require(examples["version"] == 1 and examples["cases"], "Missing independent examples")
        for case in examples["cases"]:
            require(bool(case.get("because")), "Independent example needs its reasoning")
            checks.append(dict(id="independent:" + case["id"], request=case["request"],
                               expected=case["expected"], projection="complete"))
    for lineup in baseline["lineups"]:
        request = dict(version=1, operation="route_selection",
                       lineup={key: lineup[key] for key in ("selected_service_ids", "service_order")},
                       cases=[{key: case[key] for key in ("id", "listed", "usual")}
                              for case in baseline["cases"]])
        request["lineup"]["service_names"] = baseline["service_names"]
        expected = dict(version=1, results=[dict(id=case["id"], **case["expected"][lineup["id"]])
                                           for case in baseline["cases"]])
        checks.append(dict(id="legacy:" + lineup["id"], request=request,
                           expected=expected, projection="selection"))
    time_cases = json.loads((BRIDGE / "cases/sports_day.json").read_text())["cases"]
    checks.append(dict(id="independent:containing-sports-day", projection="complete",
                       request=dict(version=1, operation="sports_day",
                                    cases=[{key: value for key, value in case.items() if key != "expected"}
                                           for case in time_cases]),
                       expected=dict(version=1, results=[dict(id=case["id"], **case["expected"])
                                                         for case in time_cases])))
    return baseline, time_cases, checks


def assert_response(check, actual):
    require(isinstance(actual, dict) and actual.get("version") == 1,
            f"{check['id']}: invalid response envelope")
    if check["projection"] == "selection":
        require(isinstance(actual.get("results"), list), f"{check['id']}: missing results")
        actual = dict(version=actual["version"], results=[
            {key: result.get(key) for key in ("id", "service_id", "basis", "outlet")}
            for result in actual["results"]])
    require(actual == check["expected"], f"{check['id']}: answer differs from recorded expectation")


def run(command, input_text, *, expected_exit=0):
    started = perf_counter()
    result = subprocess.run(command, cwd=BRIDGE, input=input_text, text=True,
                            capture_output=True, timeout=120)
    require(result.returncode == expected_exit,
            f"{command[0]} exited {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout, (perf_counter() - started) * 1000


def command_checks(command, checks):
    # One long-lived process is the actual publisher framing contract. Distinct
    # lineups in one stream also catch accidentally cached preference decisions.
    payload = "\n".join(json.dumps(check["request"]) for check in checks) + "\n"
    output, elapsed = run([*command, "--serve"], payload)
    answers = [json.loads(line) for line in output.splitlines()]
    require(len(answers) == len(checks), "Stream lost or added a response")
    for check, answer in zip(checks, answers):
        assert_response(check, answer)
    invalid = ["{", json.dumps(dict(version=2, operation="sports_day", cases=[])),
               json.dumps(dict(version=1, operation="sports_day", cases=[dict(
                   id="bad", instant="2026-02-30T00:00:00Z", reference="2026-01-01T00:00:00Z",
                   time_zone="America/New_York")]))]
    for payload in invalid:
        answer, _ = run(command, payload, expected_exit=65)
        require(json.loads(answer) == dict(version=1, error="invalid_input"), "Malformed input accepted")
    return dict(checks=len(checks), stream_elapsed_ms=elapsed, rejected_inputs=len(invalid))


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "public, max-age=3600")
        if urlsplit(self.path).path != "/rendered.html":
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; base-uri 'none'; object-src 'none'")
        super().end_headers()


@contextmanager
def serve(directory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def browser_checks(engine, base, checks, time_cases, samples):
    errors, violations = [], []
    with engine.new_context() as context:
        def route(request):
            if urlsplit(request.request.url).hostname == "127.0.0.1":
                request.continue_()
            else:
                request.fulfill(status=404, body="")
        context.route("**/*", route)
        # Playwright routing disables the HTTP cache. Measurements below are
        # explicitly warm-host page starts, not an HTTP-cache or device claim.
        context.add_init_script("window.__cspViolations=[];addEventListener('securitypolicyviolation',e=>__cspViolations.push(e.violatedDirective));")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/index.html")
        for check in checks:
            actual = page.evaluate("input => JSON.parse(outlookDecisions(input))", json.dumps(check["request"]))
            assert_response(check, actual)
        require(page.evaluate("JSON.parse(outlookDecisions('{'))") == dict(version=1, error="invalid_input"),
                "Browser accepted malformed input")
        starts, snapshots = [], []
        real = next(check["request"] for check in checks if check["id"] == "legacy:owner")
        for _ in range(samples):
            page.goto(base + "/index.html")
            sample = page.evaluate("""input => {
                const start=performance.now(); const output=JSON.parse(outlookDecisions(input));
                return {elapsed:performance.now()-start,count:output.results.length,
                    navigation:performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd};
            }""", json.dumps(real))
            require(sample["count"] == 360, "Timing batch did not evaluate full captured route set")
            starts.append(sample["navigation"])
            snapshots.append(sample["elapsed"])
            violations.extend(page.evaluate("__cspViolations"))
        require(not errors and not violations, f"Browser errors/CSP violations: {errors}, {violations}")
    legacy_count = 0
    for zone in sorted({case["time_zone"] for case in time_cases}):
        with engine.new_context(timezone_id=zone) as context:
            page = context.new_page()
            page.goto(base + "/legacy.html")
            for case in (case for case in time_cases if case["time_zone"] == zone):
                answer = page.evaluate("""c=>{
                    const day=sportsDayStart(new Date(c.instant));const end=new Date(day);end.setDate(end.getDate()+1);
                    return {date:[day.getFullYear(),String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0')].join('-'),
                        start_utc:day.toISOString(),end_utc:end.toISOString(),day_index:dayIndex(Date.parse(c.instant),Date.parse(c.reference))};
                }""", case)
                require(answer == case["expected"], "Frozen legacy calendar differs on unchanged-parity case")
                legacy_count += 1
    def summary(values):
        return dict(samples=values, median_ms=statistics.median(values), p95_ms=sorted(values)[max(0, int(len(values) * .95) - 1)])
    return dict(version=engine.version, checks=len(checks), frozen_calendar_cases=legacy_count,
                strict_csp=True, page_errors=errors, warm_host_page_start=summary(starts),
                first_360_route_evaluation=summary(snapshots))


class FixtureBindings(HTMLParser):
    """Record source facts before the actual browser adapter touches the page."""
    names = ("data-id", "data-utc", "data-lg", "data-home", "data-away", "data-tv",
             "data-state", "data-watch", "data-o", "data-r")

    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.external_scripts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.external_scripts.append(attrs)
        if tag == "li" and "row" in attrs.get("class", "").split():
            self.rows[attrs["data-id"]] = {key: attrs.get(key) for key in self.names}


def render_consumer_page(out):
    import build
    from tests.page_fixture import render_page
    fixtures = [("p2-listed", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
                ("p2-direct", "2026-10-07T18:05:00+00:00", "pre", "HBO Max", "fifa.friendly.w"),
                ("p2-usual", "2026-10-07T18:10:00+00:00", "pre", None, "esp.1"),
                ("p2-unknown", "2026-10-07T18:15:00+00:00", "pre", "Mystery Sports+", "eng.1")]
    html = render_page(build, fixtures=fixtures, ai=False, inline_runtime=False)
    parsed = FixtureBindings(html)
    require(len(parsed.external_scripts) == 1, "Generated page must reference exactly one core script")
    script = parsed.external_scripts[0]
    require(script["src"] == build.CORE_ASSET and script["integrity"] == build.CORE_INTEGRITY,
            "Generated page core reference differs from verified runtime")
    require(build.outlook_runtime.browser_script() not in html and "@@" not in html,
            "Generated page must retain the adjacent core asset packaging")
    name = build.outlook_runtime.publish_browser(out)
    raw = (out / name).read_bytes()
    digest = hashlib.sha256(raw).digest()
    require(name == f"outlook-core-{digest.hex()}.js", "Core filename is not content addressed")
    require(script["integrity"] == "sha256-" + base64.b64encode(digest).decode(), "Core SRI digest differs")
    (out / "rendered.html").write_text(html)
    return dict(bindings=parsed.rows, core_asset=name, integrity=script["integrity"],
                sha256=digest.hex(), page_sha256=sha(out / "rendered.html"))


def rendered_browser_checks(engine, base, rendered):
    """Exercise the actual production page and its adjacent verified script."""
    from playwright.sync_api import expect
    from tests.page_fixture import BUILT_AT
    scenarios = [
        ("defaults", None, None, {"p2-listed": ("yttv", "listed", "ESPN+"),
                                  "p2-direct": ("hbo", "listed", "HBO Max"),
                                  "p2-usual": ("yttv", "rule", "ESPN+")}),
        ("selected_subset", ["espnplus"], None, {"p2-listed": ("espnplus", "listed", "ESPN+"),
                                                "p2-usual": ("espnplus", "rule", "ESPN+")}),
        ("reordered", ["yttv", "espnplus"], ["espnplus", "yttv"],
         {"p2-listed": ("espnplus", "listed", "ESPN+"), "p2-usual": ("espnplus", "rule", "ESPN+")}),
        ("empty", [], None, {}),
    ]
    bindings = rendered["bindings"]
    results = []
    for zone in ("America/New_York", "America/Los_Angeles", "UTC", "Asia/Calcutta", "Asia/Katmandu",
                 "Europe/Kiev", "Etc/GMT+5", "Etc/GMT-9", "GMT"):
        for name, selected, order, expected in scenarios:
            errors, external, core_requests = [], [], []
            with engine.new_context(timezone_id=zone, viewport={"width": 390, "height": 844}) as context:
                def route(request):
                    url = urlsplit(request.request.url)
                    if url.hostname == "127.0.0.1" and url.path in ("/rendered.html", "/" + rendered["core_asset"]):
                        if url.path.endswith(rendered["core_asset"]):
                            core_requests.append(request.request.url)
                        request.continue_()
                    else:
                        external.append(request.request.url)
                        request.fulfill(status=404, body="")
                context.route("**/*", route)
                seed = {}
                if selected is not None:
                    seed["ssg3-have"] = selected
                if order is not None:
                    seed["ssg4-service-order"] = order
                # The national-team league defaults off; make the example's
                # independent route assertion explicit rather than relying on it.
                seed["ssg5-leagues"] = {"on": ["eng.1", "esp.1", "fifa.friendly.w"], "off": []}
                context.add_init_script("for(const [key,value] of Object.entries(" + json.dumps(seed) + "))localStorage.setItem(key,JSON.stringify(value));")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.clock.install(time=BUILT_AT - timedelta(seconds=1))
                page.clock.pause_at(BUILT_AT)
                page.goto(base + "/rendered.html")
                require(not errors, f"Actual generated page startup failed in {zone}/{name}: {errors}")
                expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
                require(page.evaluate("typeof window.outlookDecisions") == "function", "Production bridge export is missing")
                actual = page.locator("#outlook-body li.row").evaluate_all("""rows=>Object.fromEntries(rows.map(row=>[row.dataset.id,
                    [row.dataset.svc,row.dataset.basis,row.dataset.outlet]]))""")
                require(actual == {key: list(value) for key, value in expected.items()},
                        f"Rendered routes differ in {zone}/{name}: {actual}")
                for fixture_id in expected:
                    row = page.locator(f'#outlook-body li.row[data-id="{fixture_id}"]')
                    factual = row.evaluate("(row,names)=>Object.fromEntries(names.map(name=>[name,row.getAttribute(name)]))", list(FixtureBindings.names))
                    require(factual == bindings[fixture_id], "Browser route selection changed source fixture bindings")
                    expect(row.locator(".row__teams")).to_contain_text("Arsenal")
                    expect(row.locator(".row__teams")).to_contain_text("Chelsea")
                if name == "reordered":
                    providers = page.locator('#outlook-body li.row[data-id="p2-listed"] .network__providers li').all_text_contents()
                    require(providers == ["ESPN Select (ESPN+)", "YouTube TV (main plan)"], "Rendered provider preference order differs")
                require(not errors, f"Actual generated page raised JavaScript errors in {zone}/{name}: {errors}")
                require(len(core_requests) == 1, "Generated page did not make exactly one core request")
                results.append(dict(time_zone=zone, resolved_time_zone=page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"),
                                    lineup=name, visible_fixture_ids=sorted(actual), field_bindings_preserved=True,
                                    page_errors=errors, intercepted_requests=len(external), core_requests=len(core_requests)))
    failures = []
    for failure in ("request_failed", "corrupt_asset"):
        errors, core_requests = [], []
        with engine.new_context(timezone_id="America/New_York") as context:
            def route(request):
                url = urlsplit(request.request.url)
                if url.hostname == "127.0.0.1" and url.path == "/rendered.html":
                    request.continue_()
                elif url.hostname == "127.0.0.1" and url.path == "/" + rendered["core_asset"]:
                    core_requests.append(request.request.url)
                    if failure == "request_failed":
                        request.abort("failed")
                    else:
                        request.fulfill(content_type="application/javascript", body="window.__p2CorruptExecuted = true;")
                else:
                    request.fulfill(status=404, body="")
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.clock.install(time=BUILT_AT - timedelta(seconds=1))
            page.clock.pause_at(BUILT_AT)
            page.goto(base + "/rendered.html")
            require(len(core_requests) == 1, "Failed-core case did not attempt the production asset")
            require(page.evaluate("typeof window.outlookDecisions") == "undefined", "Failed core unexpectedly provided decisions")
            require(page.evaluate("window.__p2CorruptExecuted === undefined"), "Corrupt core bytes executed despite SRI")
            expect(page.locator("#runtime-note")).to_have_text(
                "Interactive features could not load. The saved schedule is still available. Reload to try again.")
            actual = page.locator("#outlook-body li.row").evaluate_all("""(rows,names)=>Object.fromEntries(rows.map(row=>[
                row.dataset.id,Object.fromEntries(names.map(name=>[name,row.getAttribute(name)]))]))""", list(FixtureBindings.names))
            require(actual == bindings, "Core failure discarded or changed the static fixture fallback")
            for fixture_id in ("p2-listed", "p2-direct", "p2-usual"):
                expect(page.locator(f'#outlook-body li.row[data-id="{fixture_id}"]')).to_be_visible()
            require(not errors, f"Missing core raised a JavaScript exception instead of retaining fallback: {errors}")
            failures.append(dict(failure=failure, static_fixture_ids=sorted(actual),
                                 field_bindings_preserved=True, corrupt_code_executed=False,
                                 fallback_notice_visible=True, page_errors=errors))
    return dict(cases=len(results), results=results, core_asset=rendered["core_asset"],
                integrity=rendered["integrity"], asset_sha256=rendered["sha256"], core_failure_cases=failures,
                csp_scope="Core artifact is checked under strict CSP separately; this actual generated page retains its existing inline scripts/styles.")


def source_hashes():
    sources = [Path(__file__), BASELINE, ROOT / "companion-toolchain.json", ROOT / "tool/companion_p0_limits.json"]
    sources.extend(ROOT / name for name in ("build.py", "outlook_runtime.py", "rights.toml", "settings.toml",
                   "watch_links.py", "watch-links.toml", "story_state.py", "schedule_data.py", "tests/page_fixture.py"))
    sources.extend(path for path in (ROOT / "web").iterdir() if path.is_file())
    for folder in (ROOT / "packages/outlook_core/lib", ROOT / "packages/outlook_data/lib", BRIDGE / "lib", BRIDGE / "bin", BRIDGE / "web", RULES):
        sources.extend(path for path in folder.rglob("*") if path.is_file())
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(set(sources))}


def verify_runtime(directory):
    """Bind both executed artifacts to current pinned sources before the proof."""
    from outlook_runtime import source_manifest
    manifest = json.loads((directory / "runtime.json").read_text())
    require(manifest.get("version") == 1 and manifest.get("sources") == source_manifest(),
            "Compiled runtime does not match current sources")
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    require(manifest.get("dart") == pin, "Compiled runtime has the wrong Dart pin")
    for name in ("outlook", "outlook.js"):
        require(manifest["artifacts"][name] == sha(directory / name), "Compiled artifact digest differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--runtime", type=Path, default=ROOT / "work/outlook-runtime")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-p2")
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "webkit"), default=["chromium", "webkit"])
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    require(1 <= args.samples <= 100, "Use 1-100 timing samples")
    out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    baseline, time_cases, checks = load_cases()
    before = source_hashes()
    verify_runtime(runtime)
    version = subprocess.run([args.dart, "--version"], capture_output=True, text=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    require(f"version: {pin} " in dart_version, "Dart VM does not match the pinned compiler")
    artifact = (runtime / "outlook.js").read_bytes()
    size = len(gzip.compress(artifact, mtime=0))
    limit = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
    require(size <= limit, f"Full compiled core exceeds gzip budget: {size} > {limit}")
    shutil.copyfile(runtime / "outlook.js", out / "outlook.js")
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>P2 shared decisions</title>')
    (out / "legacy.html").write_text('<!doctype html><meta charset="utf-8"><script src="legacy.js"></script><title>Frozen migration reference</title>')
    (out / "legacy.js").write_text("var DAY_START=4;\n" + baseline["legacy_sports_day_source"])
    bindings = render_consumer_page(out)
    vm = command_checks([args.dart, "--suppress-analytics", "run", "bin/outlook.dart"], checks)
    cli = command_checks([str(runtime / "outlook")], checks)
    from playwright.sync_api import sync_playwright
    browsers = {}
    with serve(out) as base, sync_playwright() as playwright:
        for name in args.browsers:
            with getattr(playwright, name).launch() as engine:
                browsers[name] = browser_checks(engine, base, checks, time_cases, args.samples)
                browsers[name]["actual_generated_page"] = rendered_browser_checks(engine, base, bindings)
    after = source_hashes()
    require(after == before, "Decision sources changed during proof")
    verify_runtime(runtime)
    report = dict(version=1, status="passed", source_hashes=before, sources_unchanged=True,
                  dart_version=dart_version,
                  runtime_artifact_hashes={name: sha(runtime / name) for name in ("outlook", "outlook.js", "runtime.json")},
                  independent_requests=sum(check["id"].startswith("independent:") for check in checks),
                  legacy_route_fixtures=360, legacy_lineups=len(baseline["lineups"]), legacy_route_comparisons=1440,
                  dart_vm=vm, compiled_cli=cli, browsers=browsers,
                  browser_size=dict(javascript_bytes=len(artifact), gzip_bytes=size, limit_bytes=limit, passed=True),
                  limitations=["No source network requests, source freshness renewal, AI calls, publication or device validation.",
                               "The captured route corpus is migration evidence; independent worked examples are a separate oracle.",
                               "Playwright interception disables HTTP caching; page starts are warm-host observations, not cached-device budgets.",
                               "Timing covers 360 route selections; it is not a complete future buildOutlook snapshot or physical performance acceptance."])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "independent_requests", "legacy_route_comparisons", "browser_size")}))


if __name__ == "__main__":
    main()
