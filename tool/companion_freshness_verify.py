#!/usr/bin/env python3
"""Offline live-freshness protocol proof using independently authored answers.

The same literal cases run in the Dart VM, compiled publisher CLI, Chromium and
WebKit. Browser requests are restricted to the proof's exact local resources;
this tool does not collect provider data or exercise live destinations.
"""
import argparse
from copy import deepcopy
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outlook_runtime import source_manifest
from tool.companion_p2_verify import (assert_response, command_checks, require,
                                    run, serve, sha, verify_runtime)

EXAMPLES = ROOT / "docs/companion/rules/live-response-freshness-examples.json"


def load_checks():
    """Load fixed expectations; the implementation never supplies the oracle."""
    examples = json.loads(EXAMPLES.read_text())
    require(examples["version"] == 1 and examples["cases"], "Independent examples are missing")
    checks = []
    for example in examples["cases"]:
        require(example.get("because"), "Independent expectation needs a reason")
        checks.append(dict(id="independent:" + example["id"], projection="complete",
                           request=example["request"], expected=example["expected"]))
    require(len({check["id"] for check in checks}) == len(checks), "Duplicate example identities")
    return checks


def invalid_requests(checks):
    original = checks[0]["request"]["cases"][0]
    changes = [dict(now_utc="2026-10-23T17:00:00.0000001Z"),
               dict(now_utc="2026-10-23T17:00:00+00:00"),
               dict(now_utc="2026-02-30T00:00:00Z"), dict(now_utc=None),
               dict(received_at="not-a-date"), dict(received_at=True),
               dict(received_at="2026-10-23T17:00:00"),
               dict(received_at="2026-10-23T17:00:00.0000001Z"),
               dict(source_state="live"), dict(source_state=None),
               dict(timeout_seconds=181)]
    requests = [dict(version=1, operation="live_freshness", cases=[dict(original, **change)])
                for change in changes]
    for field in ("received_at", "now_utc", "source_state"):
        missing = deepcopy(original)
        missing.pop(field)
        requests.append(dict(version=1, operation="live_freshness", cases=[missing]))
    requests.append(dict(version=1, operation="live_freshness", cases=[original, original]))
    return requests


def browser_protocol(engine, base, checks):
    errors, blocked = [], []
    allowed = {base + "/index.html", base + "/outlook.js"}
    with engine.new_context() as context:
        def route(request):
            if request.request.url in allowed:
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()
        context.route("**/*", route)
        context.add_init_script("window.__violations=[];addEventListener('securitypolicyviolation',e=>__violations.push(e.violatedDirective));")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/index.html")
        for check in checks:
            assert_response(check, page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(check["request"])))
        malformed = invalid_requests(checks)
        for request in malformed:
            require(page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(request)) ==
                    dict(version=1, error="invalid_input"), "Malformed freshness input was accepted")
        assert_response(checks[0], page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(checks[0]["request"])))
        require(not errors and not page.evaluate("__violations"), "Browser errors or strict CSP violations")
    return dict(checks=len(checks), malformed_requests_rejected=len(malformed),
                strict_csp=True, page_errors=errors, blocked_requests=blocked)


def source_hashes():
    values = source_manifest()
    for path in (Path(__file__), ROOT / "outlook_runtime.py", EXAMPLES,
                 ROOT / "docs/companion/rules/live-response-freshness.md",
                 ROOT / "tests/test_companion_freshness.py",
                 ROOT / "packages/outlook_core/test/source_freshness_test.dart",
                 ROOT / "tool/companion_p2_verify.py", ROOT / "tool/companion_p0_limits.json"):
        values[str(path.relative_to(ROOT))] = sha(path)
    return dict(sorted(values.items()))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--runtime", type=Path, default=ROOT / "work/outlook-runtime")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-freshness")
    parser.add_argument("--browser", choices=("chromium", "webkit", "both"), default="both")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(not (out / "report.json").exists(), "Choose a new evidence directory; existing reports are preserved")
    before = source_hashes()
    verify_runtime(runtime)
    version = subprocess.run([args.dart, "--version"], capture_output=True, text=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    require(f"version: {pin} " in dart_version, "Dart VM differs from pinned compiler")
    checks = load_checks()
    shutil.copyfile(runtime / "outlook.js", out / "outlook.js")
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>Freshness proof</title>')
    malformed = invalid_requests(checks)
    outcomes = {}
    commands = dict(dart_vm=[args.dart, "--suppress-analytics", "run", "bin/outlook.dart"],
                    compiled_cli=[str(runtime / "outlook")])
    for name, command in commands.items():
        outcomes[name] = command_checks(command, checks)
        payloads = malformed + [checks[0]["request"]]
        output, _ = run([*command, "--serve"], "\n".join(json.dumps(value) for value in payloads) + "\n")
        answers = [json.loads(line) for line in output.splitlines()]
        require(len(answers) == len(payloads), "Malformed stream lost framing")
        require(all(answer == dict(version=1, error="invalid_input") for answer in answers[:-1]),
                "Malformed freshness payload accepted")
        assert_response(checks[0], answers[-1])
        outcomes[name]["freshness_malformed_requests_rejected"] = len(malformed)
    from playwright.sync_api import sync_playwright
    browsers = {}
    names = ("chromium", "webkit") if args.browser == "both" else (args.browser,)
    with serve(out) as base, sync_playwright() as playwright:
        for name in names:
            with getattr(playwright, name).launch() as engine:
                browsers[name] = dict(version=engine.version, **browser_protocol(engine, base, checks))
    require(before == source_hashes(), "Sources changed during freshness proof")
    verify_runtime(runtime)
    size = len(gzip.compress((runtime / "outlook.js").read_bytes(), mtime=0))
    limit = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
    require(size <= limit, "Core exceeds gzip budget")
    report = dict(version=1, status="passed", source_hashes=before, sources_unchanged=True,
                  dart_version=dart_version, checks=len(checks),
                  literal_decisions=sum(len(check["expected"]["results"]) for check in checks),
                  runtime_hashes={name: sha(runtime / name) for name in ("runtime.json", "outlook", "outlook.js")},
                  browser_gzip_bytes=size, browser_gzip_limit=limit, consumers=outcomes, browsers=browsers,
                  limitations=["Protocol parity proves the shared owner, not generated-page timer and link consumers; those require browser integration checks.",
                               "Response receipt is not a provider score-observation timestamp; source age remains unknown.",
                               "No source or AI calls, publication, distribution or physical-device acceptance is claimed."])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(status="passed", checks=len(checks), literal_decisions=report["literal_decisions"], browsers=list(browsers))))


if __name__ == "__main__":
    main()
