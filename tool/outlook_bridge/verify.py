#!/usr/bin/env python3
"""Historical P0 spike measurement; current consumers use companion_p2_verify.py.

Run with the repository's Python 3.11+ and tests/requirements-browser.txt.
Artifacts and measurements go under work/; no production files are rewritten.
"""
import argparse
from contextlib import contextmanager
from functools import partial
import gzip
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from threading import Thread
from time import perf_counter
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def browser_size_budget(path):
    def invalid_constant(value):
        raise ValueError(f"Non-JSON numeric constant: {value}")

    limits = json.loads(path.read_text(), parse_constant=invalid_constant)
    value = limits["budgets"]["browser_core_gzip_bytes"]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("Browser gzip budget must be a positive integer byte count")
    return value


def run(command, *, input_text=None, cwd=BRIDGE, expected_exit=0):
    started = perf_counter()
    result = subprocess.run(command, input=input_text, cwd=cwd, text=True,
                            capture_output=True, timeout=120)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{command[0]} exited {result.returncode}:\n"
                           f"{result.stdout}\n{result.stderr}")
    return result.stdout, (perf_counter() - started) * 1000


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if urlsplit(self.path).path == "/index.html":
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; "
                             "base-uri 'none'; object-src 'none'")
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


def request_for(cases):
    return {"version": 1, "operation": "sports_day",
            "cases": [{key: value for key, value in case.items() if key != "expected"}
                      for case in cases]}


def reject_external(route):
    if urlsplit(route.request.url).hostname == "127.0.0.1":
        route.continue_()
    else:
        route.fulfill(status=404, body="")


def browser_checks(engine, base, cases, request, expected, invalid):
    errors = []
    with engine.new_context() as context:
        context.route("**/*", reject_external)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.__cspViolations = [];
            addEventListener('securitypolicyviolation',
                e => window.__cspViolations.push(e.violatedDirective));
        """)
        page.goto(base + "/index.html")
        actual = page.evaluate("(input) => JSON.parse(outlookSportsDay(input))", json.dumps(request))
        assert actual == expected, (actual, expected)
        for payload in invalid:
            assert page.evaluate("(input) => JSON.parse(outlookSportsDay(input))", payload) == {
                "version": 1, "error": "invalid_input"}
        assert page.locator("#result").inner_text() != "Loading…"
        assert page.evaluate("window.__cspViolations") == []
        assert not errors, errors

    # Run the actual existing page's private functions through a test-only hook.
    # This is migration evidence, never the oracle for the Dart operation.
    legacy_results = []
    for zone in sorted({case["time_zone"] for case in cases}):
        with engine.new_context(timezone_id=zone) as context:
            context.route("**/*", reject_external)
            page = context.new_page()
            page.goto(base + "/migration.html")
            for case in (case for case in cases if case["time_zone"] == zone):
                actual = page.evaluate("""c => {
                    const day = __p0Time.sportsDayStart(new Date(c.instant));
                    const end = new Date(day); end.setDate(end.getDate() + 1);
                    return {id: c.id,
                        date: [day.getFullYear(), String(day.getMonth()+1).padStart(2,'0'),
                            String(day.getDate()).padStart(2,'0')].join('-'),
                        start_utc: day.toISOString(), end_utc: end.toISOString(),
                        day_index: __p0Time.dayIndex(Date.parse(c.instant), Date.parse(c.reference))};
                }""", case)
                assert actual == {"id": case["id"], **case["expected"]}, actual
                legacy_results.append(actual)

    timings = {"baseline": [], "with_bridge": []}
    first_call_ms = []
    for _ in range(7):
        for name in timings:
            with engine.new_context() as context:
                context.route("**/*", reject_external)
                page = context.new_page()
                page.goto(base + f"/{name}.html")
                timings[name].append(page.evaluate(
                    "performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd"))
                assert page.locator("li.row").count() == 9
                if name == "with_bridge":
                    assert page.evaluate("window.__p0Result") == expected
                    first_call_ms.append(page.evaluate("window.__p0FirstCallMs"))
    medians = {name: statistics.median(values) for name, values in timings.items()}
    return {"version": engine.version, "independent_cases": len(cases),
            "legacy_cases": len(legacy_results), "invalid_batches": len(invalid),
            "strict_csp": "passed", "dom_content_loaded_ms": timings,
            "first_batch_including_timezone_initialization_ms": first_call_ms,
            "median_ms": medians,
            "median_added_ms": medians["with_bridge"] - medians["baseline"],
            "legacy_results": legacy_results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-p0")
    parser.add_argument("--browsers", nargs="+", choices=("chromium", "webkit"),
                        default=["chromium", "webkit"])
    args = parser.parse_args()
    if "outlookDecisions" in (ROOT / "web/app.js").read_text():
        parser.error("The production page now embeds the shared core. The P0 baseline/added-bridge "
                     "timing comparison is historical; use tool/companion_p2_verify.py instead.")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    dart = [args.dart, "--suppress-analytics"]
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())
    version = subprocess.run([args.dart, "--version"], text=True, capture_output=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    if f"version: {pin['dart']} " not in dart_version:
        raise RuntimeError(f"Expected Dart {pin['dart']}; found {dart_version}")
    for package in (ROOT / "packages/outlook_core", BRIDGE):
        run(dart + ["pub", "get", "--offline", "--enforce-lockfile"], cwd=package)
        run(dart + ["analyze"], cwd=package)
    run(dart + ["test"], cwd=ROOT / "packages/outlook_core")
    run(dart + ["compile", "js", "-O2", "--no-source-maps", "--csp",
                "-o", str(out / "outlook.js"), "web/bridge.dart"])
    run(dart + ["compile", "exe", "bin/outlook.dart", "-o", str(out / "outlook")])
    for name in ("index.html", "demo.js"):
        shutil.copyfile(BRIDGE / "web" / name, out / name)

    # Synthetic, frozen source data exercises the real current Python renderer.
    import build
    from tests.page_fixture import render_page
    baseline = render_page(build)
    if "outlookDecisions" in baseline:
        raise RuntimeError("The rendered baseline already includes the shared core; "
                           "use tool/companion_p2_verify.py for current migration evidence.")
    (out / "baseline.html").write_text(baseline)
    marker = "function sportsDayStart(d)"
    assert marker in baseline
    (out / "migration.html").write_text(baseline.replace(marker,
        "window.__p0Time = {sportsDayStart, dayIndex};\n  " + marker, 1))

    cases = json.loads((BRIDGE / "cases/sports_day.json").read_text())["cases"]
    request = request_for(cases)
    expected = {"version": 1, "results": [{"id": c["id"], **c["expected"]} for c in cases]}
    (out / "benchmark.js").write_text(
        "const started = performance.now();\n"
        f"window.__p0Result = JSON.parse(outlookSportsDay({json.dumps(json.dumps(request))}));\n"
        "window.__p0FirstCallMs = performance.now() - started;\n")
    (out / "with_bridge.html").write_text(baseline.replace(
        "<head>", '<head><script src="outlook.js"></script>'
        '<script src="benchmark.js"></script>', 1))
    invalid = ["{", json.dumps({**request, "version": 2}),
               json.dumps(request_for([{**cases[0], "instant": "2026-02-30T00:00:00Z"}])),
               json.dumps(request_for([{**cases[0], "instant": "2026-10-07T12:00:00"}])),
               json.dumps(request_for([{**cases[0], "time_zone": "Unknown/Nowhere"}])),
               json.dumps(request_for([cases[0], cases[0]]))]
    timings = {}
    for label, command in (("dart_vm", dart + ["run", "bin/outlook.dart"]),
                           ("python_to_cli", [str(out / "outlook")])):
        output, elapsed = run(command, input_text=json.dumps(request))
        assert json.loads(output) == expected
        timings[label] = elapsed
        for payload in invalid:
            output, _ = run(command, input_text=payload, expected_exit=65)
            assert json.loads(output) == {"version": 1, "error": "invalid_input"}
        invalid_utf8 = subprocess.run(command, input=b"\xff", capture_output=True,
                                      cwd=BRIDGE, timeout=30)
        assert invalid_utf8.returncode == 65
        assert json.loads(invalid_utf8.stdout) == {"version": 1, "error": "invalid_input"}
        assert invalid_utf8.stderr == b""

    from playwright.sync_api import sync_playwright
    browsers = {}
    with serve(out) as base, sync_playwright() as playwright:
        for name in args.browsers:
            with getattr(playwright, name).launch() as engine:
                browsers[name] = browser_checks(engine, base, cases, request, expected, invalid)
    artifact = (out / "outlook.js").read_bytes()
    artifact_gzip_bytes = len(gzip.compress(artifact, mtime=0))
    limits_path = ROOT / "tool/companion_p0_limits.json"
    size_limit = browser_size_budget(limits_path)
    if artifact_gzip_bytes > size_limit:
        raise RuntimeError(f"Compiled core exceeds browser budget: "
                           f"{artifact_gzip_bytes} > {size_limit} gzip bytes")
    baseline_bytes = baseline.encode()
    source_paths = [ROOT / "companion-toolchain.json", limits_path, ROOT / "tests/page_fixture.py",
                    ROOT / "settings.toml", ROOT / "rights.toml", ROOT / "watch-links.toml"]
    source_paths.extend(ROOT.glob("*.py"))
    source_paths.extend(p for p in (ROOT / "web").iterdir() if p.is_file())
    for source_root in (ROOT / "packages/outlook_core", BRIDGE):
        source_paths.extend(p for p in source_root.rglob("*")
                            if p.is_file() and ".dart_tool" not in p.parts)
    report = {
        "status": "P0 shared-time spike; native and stage acceptance recorded separately in docs/companion/p0-evidence.md",
        "source_revision": run(["git", "rev-parse", "HEAD"], cwd=ROOT)[0].strip(),
        "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(source_paths) if "__pycache__" not in p.parts},
        "dart": dart_version, "python": sys.version.split()[0],
        "javascript_bytes": len(artifact), "javascript_gzip_bytes": artifact_gzip_bytes,
        "size_budget": {"limit_bytes": size_limit, "passed": True,
                        "remaining_bytes": size_limit - artifact_gzip_bytes},
        "performance_acceptance": "pending release-mode physical reference measurements",
        "artifact_hashes": {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                            for name in ("baseline.html", "with_bridge.html", "outlook.js", "outlook")},
        "baseline_html_bytes": len(baseline_bytes),
        "baseline_html_gzip_bytes": len(gzip.compress(baseline_bytes, mtime=0)),
        "cold_batch_ms": timings, "browsers": browsers, "expected_results": expected,
        "measurement_limits": "7 localhost samples per variant, fresh browser contexts, warm host; "
                              "desktop development measurements, no device/network budget certification",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("javascript_bytes", "javascript_gzip_bytes",
                                                  "baseline_html_bytes", "cold_batch_ms")}, indent=2))
    print(f"Parity and strict CSP passed. Full evidence: {out / 'report.json'}")


if __name__ == "__main__":
    main()
