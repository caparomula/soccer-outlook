#!/usr/bin/env python3
"""Offline TIME-WINDOW-1 proof with independent answers and real page transitions."""
import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tool.companion_p2_verify import (FixtureBindings, assert_response, command_checks,
                                    require, run, serve, sha, source_hashes as core_hashes,
                                    verify_runtime)

EXAMPLES = ROOT / "docs/companion/rules/fixture-window-examples.json"
BASELINE = ROOT / "tests/contracts/p2/legacy-fixture-windows-2026-10-23.json"
FUNCTIONS = ROOT / "tests/contracts/p2/legacy-fixture-windows-functions-2026-10-23.js"
BASELINE_SHA = "238b2cb41153202968fa57c7fb57fec8bfad52e060504dfcbf7dfe2e90ded757"
AT = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
LIVE_LINK = "https://example.com/window-proof/live-only"
FIXTURES = [("upcoming", "2026-10-07T13:30:00+00:00", "pre", "ESPN+"),
            ("pending", "2026-10-07T14:55:00+00:00", "pre", "ESPN+"),
            ("tbd", "2026-10-07T13:00:00+00:00", "pre", "ESPN+"),
            ("dawn", "2026-10-08T07:00:00+00:00", "pre", "ESPN+"),
            ("tbd-dawn", "2026-10-08T07:00:00+00:00", "in", "ESPN+"),
            ("grace-dawn", "2026-10-08T05:30:00+00:00", "in", "ESPN+")]


def load_checks():
    """Load frozen/literal expectations without asking either decision owner."""
    examples = json.loads(EXAMPLES.read_text())
    require(examples["version"] == 1 and examples["cases"], "Independent examples are missing")
    checks = []
    for example in examples["cases"]:
        require(example.get("because"), "Independent expectation needs a reason")
        checks.append(dict(id="independent:" + example["id"], projection="complete",
                           request=example["request"], expected=example["expected"]))
    require(sha(BASELINE) == BASELINE_SHA, "Frozen original browser answers changed")
    baseline = json.loads(BASELINE.read_text())
    require(sha(FUNCTIONS) == baseline["origin"]["legacy_functions_sha256"], "Frozen original functions changed")
    unchanged = [case for case in baseline["cases"] if not case["intentional_delta"]]
    changed = [case for case in baseline["cases"] if case["intentional_delta"]]
    require(len(unchanged) == 453 and len(changed) == 3, "Frozen parity/delta partition changed")
    checks.append(dict(id="legacy:453-preserved-decisions", projection="complete",
                       request=dict(version=1, operation="fixture_window", cases=[case["input"] for case in unchanged]),
                       expected=dict(version=1, results=[case["legacy"] for case in unchanged])))
    # These three literal corrections share a same-day five-hour-old in-progress
    # kickoff. Receipt is future or hours stale at the supplied evaluation time.
    # Preserve original outputs in the baseline; never rewrite them as new truth.
    require([case["id"] for case in changed] == ["receipt:-1", "pinned:past", "pinned:future"], "Clock delta identities changed")
    answers = [dict(id=identity, day_index=0, bucket="earlier", in_window=True,
                    in_focus=False, source_in_progress=True, recent_live_receipt=False)
               for identity in ("receipt:-1", "pinned:past", "pinned:future")]
    require(all(case["legacy"]["bucket"] == "live" and case["legacy"]["recent_live_receipt"] for case in changed),
            "Frozen clock defects no longer establish the stated legacy behavior")
    checks.append(dict(id="independent:three-declared-clock-corrections", projection="complete",
                       request=dict(version=1, operation="fixture_window", cases=[case["input"] for case in changed]),
                       expected=dict(version=1, results=answers)))
    return checks, baseline


def invalid_requests(checks):
    original = checks[0]["request"]["cases"][0]
    changes = [dict(now_utc="2026-10-23T17:00:00.0000001Z"),
               dict(now_utc="2026-10-23T17:00:00+00:00"),
               dict(kickoff_utc="2026-02-30T00:00:00Z"), dict(kickoff_utc=None),
               dict(received_at="not-a-date"), dict(time_confirmed="true"),
               dict(time_confirmed=1), dict(source_state="unrecognized"),
               dict(time_zone="Mars/Olympus"), dict(unknown_field=True)]
    requests = [dict(version=1, operation="fixture_window", cases=[dict(original, **change)]) for change in changes]
    for field in ("received_at", "now_utc", "time_confirmed"):
        missing = deepcopy(original)
        missing.pop(field)
        requests.append(dict(version=1, operation="fixture_window", cases=[missing]))
    return requests


def write_pages(out):
    import build
    from tests.page_fixture import render_page
    def destinations(match):
        return {"ESPN+": {"espnplus": dict(url=LIVE_LINK, kind="match", title="Qualified live destination", live_only=True)}}
    # The rollover scenario tests window arithmetic with explicitly current
    # synthetic live observations; receipt freshness has its own proof below.
    rollover_receipts = {identity: "2026-10-08T07:59:00Z" for identity in ("tbd-dawn", "grace-dawn")}
    with patch.object(build, "provider_links", side_effect=destinations):
        html = render_page(build, fixtures=FIXTURES, tbd=("tbd", "tbd-dawn"), source_receipts=rollover_receipts, inline_runtime=False)
        replacement = render_page(build, fixtures=[
            (fixture[0], fixture[1], fixture[2], "Paramount+" if fixture[0] == "upcoming" else fixture[3])
            for fixture in FIXTURES], tbd=("tbd", "tbd-dawn"), source_receipts=rollover_receipts, inline_runtime=False)
    build.outlook_runtime.publish_browser(out)
    asset = build.CORE_ASSET
    parsed = FixtureBindings(html)
    digest = sha(out / asset)
    require(asset == f"outlook-core-{digest}.js", "Core asset is not content-addressed")
    require(parsed.external_scripts == [dict(src=asset,
                integrity="sha256-" + base64.b64encode(bytes.fromhex(digest)).decode())], "External core SRI differs")
    (out / "rendered.html").write_text(html)
    (out / "replacement.html").write_text(replacement)
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>Window proof</title>')
    return html, replacement, asset


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
        malformed = invalid_requests(checks)
        for request in malformed:
            require(page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(request)) ==
                    dict(version=1, error="invalid_input"), "Malformed window input was accepted")
        # Reuse the same compiled owner after invalid frames to prove recovery.
        assert_response(checks[0], page.evaluate("input=>JSON.parse(outlookDecisions(input))", json.dumps(checks[0]["request"])))
        require(not errors and not page.evaluate("__violations"), "Protocol browser error or strict CSP violation")
    return dict(checks=len(checks), malformed_requests_rejected=len(malformed), strict_csp=True, page_errors=errors)


@contextmanager
def page_context(engine, base, html, asset, out, label, *, at=AT):
    from playwright.sync_api import expect
    errors, assets = [], []
    state = dict(feed={"events": []}, snapshot=None, scores_requests=0, schedule_requests=0)
    with engine.new_context(timezone_id="America/New_York", viewport={"width": 1280, "height": 900}) as context:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        def route(request):
            parsed = urlsplit(request.request.url)
            if parsed.hostname == "127.0.0.1" and parsed.path == "/rendered.html":
                request.fulfill(content_type="text/html", body=html)
            elif parsed.hostname == "127.0.0.1" and parsed.path == "/" + asset:
                assets.append(parsed.path)
                request.continue_()
            elif parsed.path.endswith("/schedule.json"):
                state["schedule_requests"] += 1
                if state["snapshot"] is None:
                    request.fulfill(status=404, body="Offline fixture")
                else:
                    request.fulfill(content_type="application/json", body=json.dumps(state["snapshot"]))
            elif parsed.path.startswith("/espn/"):
                state["scores_requests"] += 1
                request.fulfill(content_type="application/json", body=json.dumps(state["feed"]))
            else:
                request.fulfill(status=404, body="Offline fixture")
        context.route("**/*", route)
        context.add_init_script("localStorage.setItem('ssg3-have',JSON.stringify(['espnplus','para']));localStorage.setItem('ssg4-service-order',JSON.stringify(['espnplus','para']));")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install(time=at - timedelta(seconds=1))
        page.clock.pause_at(at)
        try:
            page.goto(base + "/rendered.html?scoresbase=" + base + "/espn/")
            expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
            page.clock.run_for(1)
            expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
            expect(page.locator("#livenote")).to_contain_text("last checked")
            require(not errors, f"{label}: startup errors {errors}")
            yield page, state
            require(not errors, f"{label}: JavaScript errors {errors}")
            require(assets, f"{label}: external SRI core was not loaded")
        finally:
            context.tracing.stop(path=str(out / (label + ".zip")))


def row_state(page, identity):
    return page.locator(f'li.row[data-id="{identity}"]').evaluate("""row => ({
      bucket:row.closest('.bucket').querySelector('.bucket__h > span').textContent,
      window_bucket:row._timeWindow.bucket,source_label:row._sourceStatus.label,
      source_received_at:row.dataset.sourceReceivedAt,
      source_state:row.dataset.state, service:row.dataset.svc,
      scores:Array.from(row.querySelectorAll('.team .score')).filter(el=>!el.hidden).map(el=>el.textContent),
      status:row.querySelector('.row__status').textContent,
      links:Array.from(row.querySelectorAll('a.network__link')).map(el=>el.href)
    })""")


def recompute(page, at):
    """Evaluate through an existing lifecycle trigger; no claim of a new timer."""
    page.clock.set_system_time(at)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def expect_bucket(page, identity, label, *, window_bucket=None):
    from playwright.sync_api import expect
    expect(page.locator(f'li.row[data-id="{identity}"]').locator("xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' bucket ')][1]").locator('.bucket__h > span').first).to_have_text(label)
    value = row_state(page, identity)
    expected = window_bucket if window_bucket is not None else {
        "Live now": "live", "Today": "today", "Earlier today": "earlier", "Yesterday": "yesterday"}[label]
    require(value["window_bucket"] == expected, f"{identity}: shared window bucket changed: {value}")
    return value


def browser_consumer(engine, base, html, replacement, asset, out, name):
    from playwright.sync_api import expect
    from tests.page_fixture import scoreboard
    from tests.browser_schedule import SnapshotHTML
    transitions = []
    with page_context(engine, base, html, asset, out, name + "-boundaries") as (page, _state):
        # Startup consumes one millisecond for its normal scheduled poll; move
        # back to the exact boundary and request the existing lifecycle render.
        recompute(page, AT)
        pending = expect_bucket(page, "pending", "Live now")
        require(pending["source_state"] == "pre" and pending["scores"] == [] and pending["links"] == [],
                "Estimated live placement invented source progress, score or a live-only destination")
        recompute(page, AT + timedelta(milliseconds=1))
        after = expect_bucket(page, "pending", "Earlier today")
        tbd = expect_bucket(page, "tbd", "Today")
        require(tbd["source_state"] == "pre" and tbd["scores"] == [], "TBD placeholder invented source progress")
        transitions.append(dict(name="inclusive_125_and_pending_status", exact=pending, after=after, tbd=tbd))
    with page_context(engine, base, html, asset, out, name + "-receipt") as (page, state):
        expect_bucket(page, "upcoming", "Earlier today")
        state["feed"] = scoreboard("in")
        page.clock.run_for(59999)
        expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-state", "in")
        require(page.evaluate("new Date().toISOString()") == "2026-10-07T17:01:00.000Z", "Accepted update clock drifted")
        accepted = expect_bucket(page, "upcoming", "Live now")
        require(accepted["scores"] == ["2", "1"] and accepted["links"] == [LIVE_LINK], "Accepted progress did not update independent score/link presentation")
        state["feed"] = {"events": []}
        # Older schedule facts replace the row and outlet while the newer live
        # observation and its original receipt remain eligible for carry-over.
        parsed = SnapshotHTML(replacement)
        state["snapshot"] = dict(version=1, complete=True, generated_at="2026-10-07T17:00:30Z",
                                 runtime_revision=parsed.revision, rows=parsed.rows, logos={})
        page.clock.run_for(3 * 60000)
        stale = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        require(stale["source_state"] == "in" and stale["scores"] == ["2", "1"] and stale["source_label"] == "63'",
                "Freshness expiry rewrote the accepted source status or score")
        require(stale["status"] == "Last known · 63'" and stale["links"] == [] and
                stale["source_received_at"] == "2026-10-07T17:01:00.000Z",
                "Stale presentation renewed its receipt or retained a live-only destination")
        page.clock.fast_forward(60000 + 1)
        expect(page.locator("#app")).to_have_attribute("data-schedule-built", "2026-10-07T17:00:30Z")
        expect(page.locator('li.row[data-id="upcoming"]')).to_have_attribute("data-svc", "para")
        carried = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        require(carried["source_state"] == "in" and carried["scores"] == ["2", "1"], "Snapshot replacement lost the newer live observation")
        recompute(page, AT + timedelta(minutes=10, seconds=59, milliseconds=999))
        before = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        recompute(page, AT + timedelta(minutes=11))
        exact = expect_bucket(page, "upcoming", "Earlier today")
        require(exact["source_state"] == "in" and exact["scores"] == ["2", "1"], "Placement expiry rewrote source status/score outside this family")
        transitions.append(dict(name="accepted_receipt_exact_ten_minutes_and_snapshot_carry", received_at="2026-10-07T17:01:00.000Z",
                                accepted=accepted, stale_presentation=stale, replacement=carried, before=before, exact=exact))
        page.evaluate("location.hash='at-20261007-1305'")
        pinned = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        recompute(page, AT + timedelta(hours=2))
        wall_advanced = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        page.evaluate("location.hash='at-20261007-1300'")
        future = expect_bucket(page, "upcoming", "Earlier today")
        page.evaluate("location.hash='at-20261007-1311'")
        expired = expect_bucket(page, "upcoming", "Earlier today")
        transitions.append(dict(name="pinned_clock_and_future_receipt", pinned=pinned, wall_advanced=wall_advanced,
                                future_receipt=future, exact_ten_minutes=expired))
        page.evaluate("history.replaceState(null,'',location.pathname+location.search);Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
        page.clock.set_system_time(AT + timedelta(minutes=5))
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
        resumed = expect_bucket(page, "upcoming", "Today", window_bucket="live")
        transitions.append(dict(name="visibility_resume_recomputes_supplied_clock", state=resumed))
    rollover = datetime(2026, 10, 8, 7, 59, 59, 998000, tzinfo=timezone.utc)
    with page_context(engine, base, html, asset, out, name + "-rollover", at=rollover) as (page, _state):
        before = {identity: expect_bucket(page, identity, "Live now") for identity in ("dawn", "tbd-dawn", "grace-dawn")}
        recompute(page, datetime(2026, 10, 8, 8, tzinfo=timezone.utc))
        after = dict(dawn=expect_bucket(page, "dawn", "Live now"),
                     tbd=expect_bucket(page, "tbd-dawn", "Yesterday"),
                     grace=expect_bucket(page, "grace-dawn", "Yesterday"))
        transitions.append(dict(name="four_am_rollover_confirmed_tbd_and_same_day_grace", before=before, after=after))
    return dict(transitions=transitions, external_sri_core=True, page_errors=[],
                fixture_inputs="Synthetic qualified live-only destination and actual production-rendered row replacement; rollover live rows have an explicit 2026-10-08T07:59:00Z receipt. Shared window buckets are checked separately from the three-minute stale presentation, retained score and revoked live-only link; no destination opened.",
                recomputation="Existing minute/hash/visibility/snapshot events, with exact boundary projection triggered explicitly; no dedicated boundary-timer claim.",
                csp_scope="Protocol page enforces strict CSP; generated production page retains its existing inline scripts and styles.")


def source_hashes():
    values = core_hashes()
    for path in (Path(__file__), ROOT / "tests/test_companion_windows.py", EXAMPLES, BASELINE, FUNCTIONS,
                 ROOT / "broadcast_listings.py", ROOT / "companion_export.py", ROOT / "provider_utils.py"):
        values[str(path.relative_to(ROOT))] = sha(path)
    return dict(sorted(values.items()))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--runtime", type=Path, default=ROOT / "work/outlook-runtime")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-window")
    parser.add_argument("--browser", choices=("chromium", "webkit", "both"), default="both")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(not (out / "report.json").exists(), "Choose a new evidence directory; existing reports are preserved")
    before = source_hashes()
    verify_runtime(runtime)
    os.environ["OUTLOOK_RUNTIME_DIR"] = str(runtime)
    version = subprocess.run([args.dart, "--version"], capture_output=True, text=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    require(f"version: {pin} " in dart_version, "Dart VM differs from pinned compiler")
    checks, baseline = load_checks()
    shutil.copyfile(runtime / "outlook.js", out / "outlook.js")
    html, replacement, asset = write_pages(out)
    outcomes = {}
    commands = dict(dart_vm=[args.dart, "--suppress-analytics", "run", "bin/outlook.dart"], compiled_cli=[str(runtime / "outlook")])
    malformed = invalid_requests(checks)
    for name, command in commands.items():
        outcomes[name] = command_checks(command, checks)
        # Invalid frames in a persistent process must not poison the next valid
        # answer; one-shot invalid-exit behavior is covered by command_checks.
        payloads = malformed + [checks[0]["request"]]
        output, _ = run([*command, "--serve"], "\n".join(json.dumps(value) for value in payloads) + "\n")
        answers = [json.loads(line) for line in output.splitlines()]
        require(len(answers) == len(payloads), "Malformed stream lost framing")
        require(all(answer == dict(version=1, error="invalid_input") for answer in answers[:-1]), "Malformed window payload accepted")
        assert_response(checks[0], answers[-1])
        outcomes[name]["window_malformed_requests_rejected"] = len(malformed)
    from playwright.sync_api import sync_playwright
    browsers = {}
    names = ("chromium", "webkit") if args.browser == "both" else (args.browser,)
    with serve(out) as base, sync_playwright() as playwright:
        for name in names:
            with getattr(playwright, name).launch() as engine:
                browsers[name] = dict(version=engine.version, protocol=browser_protocol(engine, base, checks),
                    generated_page=browser_consumer(engine, base, html, replacement, asset, out, name))
    require(before == source_hashes(), "Sources changed during window proof")
    verify_runtime(runtime)
    size = len(gzip.compress((runtime / "outlook.js").read_bytes(), mtime=0))
    limit = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
    require(size <= limit, "Core exceeds gzip budget")
    report = dict(version=1, status="passed", source_hashes=before, sources_unchanged=True,
                  dart_version=dart_version, checks=len(checks), literal_decisions=38,
                  preserved_legacy_decisions=453, declared_clock_corrections=3,
                  baseline=dict(sha256=sha(BASELINE), origin=baseline["origin"]),
                  runtime_hashes={name: sha(runtime / name) for name in ("runtime.json", "outlook", "outlook.js")},
                  browser_gzip_bytes=size, browser_gzip_limit=limit, consumers=outcomes, browsers=browsers,
                  limitations=["Source status decoding, stale score/link presentation, transport due policy and final candidate/ranking policies remain outside this family.",
                               "Browser lifecycle uses millisecond clocks; exact microsecond decisions are separately proved through all four protocol runtimes.",
                               "All source/AI requests are intercepted; no network, publication, distribution or physical-device acceptance is claimed."])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(status="passed", checks=len(checks), legacy_preserved=453, browsers=list(browsers))))


if __name__ == "__main__":
    main()
