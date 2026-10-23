#!/usr/bin/env python3
"""Offline source-status proof: independent literals, legacy parity and real pages."""
import argparse
import base64
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
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
from tool.companion_window_verify import page_context

EXAMPLES = ROOT / "docs/companion/rules/espn-status-examples.json"
BASELINE = ROOT / "tests/contracts/p2/legacy-espn-status-2026-10-23.json"
FUNCTIONS = ROOT / "tests/contracts/p2/legacy-espn-status-functions-2026-10-23.js"
BASELINE_SHA = "76fb3c86389d78cf28142b5e88bcf5ca2d2f9808111b1e24dac65ba108b8e5f3"
LIVE_LINK = "https://example.com/status-proof/live-only"


def declared_corrections():
    """Complete literal new answers; original defects remain in the baseline."""
    rows = [
        ("cancel-abbreviation", "cancelled", "post", "post", "Canceled", False, False, True, 0, 0, None, None),
        ("postponed-pre", "postponed", "post", "pre", "Postponed", False, False, True, 0, 0, None, None),
        ("abandoned", "abandoned", "post", "post", "Abandoned", False, False, True, 2, 1, None, None),
        ("unknown-terminal", "unknown", "post", "post", "Status unknown", False, False, False, 0, 0, None, None),
        ("empty-terminal", "unknown", "post", "post", "Status unknown", False, False, False, 0, 0, None, None),
        ("missing-status", "unknown", "unknown", "", "Status unknown", False, False, False, 0, 0, None, None),
        ("object-zero", "live", "in", "in", "12'", True, False, False, 0, None, 0, None),
        ("over-contract", "finished", "post", "post", "FT", True, True, False, None, 1, None, 1),
        ("conflict", "unknown", "post", "post", "Status unknown", False, False, False, 0, 0, None, None),
    ]
    return [dict(id="synthetic:" + ident, state=state, phase=phase, provider_state=provider,
                 label=label, played=played, final=final, void=void,
                 reported_scores=dict(home=rh, away=ra), scores=dict(home=h, away=a))
            for ident, state, phase, provider, label, played, final, void, rh, ra, h, a in rows]


def load_checks():
    examples = json.loads(EXAMPLES.read_text())
    require(examples["version"] == 1 and examples["cases"], "Independent examples are missing")
    checks = []
    for example in examples["cases"]:
        require(example.get("because"), "Independent expectation needs a reason")
        checks.append(dict(id="independent:" + example["id"], projection="complete",
                           request=example["request"], expected=example["expected"]))
    require(sha(BASELINE) == BASELINE_SHA, "Frozen status capture changed")
    baseline = json.loads(BASELINE.read_text())
    require(sha(FUNCTIONS) == baseline["origin"]["legacy_functions_sha256"], "Frozen browser functions changed")
    preserved = [case for case in baseline["cases"] if case["origin"] is not None]
    defects = [case for case in baseline["cases"] if case["origin"] is None]
    require(len(preserved) == 360 and len(defects) == 9, "Frozen parity/defect partition changed")
    expected = []
    for case in preserved:
        browser, publisher = case["browser"], case["python"]
        require(all(browser[key] == publisher[key] for key in ("state", "label", "played")), "Ordinary legacy consumers diverged")
        require(browser["state"] in ("pre", "in", "post") and not publisher["void"], "Exceptional result entered parity corpus")
        # This projection preserves recorded answers. It does not classify new
        # raw input or evaluate any exceptional case through the new owner.
        scores = [int(value) if value != "" else None for value in browser["scores"]]
        reported = [int(value) for value in publisher["scores"]]
        require(all(0 <= value <= 100 for value in reported), "Ordinary capture score is outside contract")
        phase = browser["state"]
        expected.append(dict(id=case["id"], state={"pre": "scheduled", "in": "live", "post": "finished"}[phase],
            phase=phase, provider_state=phase, label=browser["label"], played=browser["played"], final=phase == "post",
            void=False, reported_scores=dict(zip(("home", "away"), reported)), scores=dict(zip(("home", "away"), scores))))
    checks.append(dict(id="legacy:360-ordinary-captured-decisions", projection="complete",
        request=dict(version=1, operation="espn_status", cases=[case["input"] for case in preserved]),
        expected=dict(version=1, results=expected)))
    answers = declared_corrections()
    require([case["id"] for case in defects] == [answer["id"] for answer in answers], "Declared defect identities changed")
    checks.append(dict(id="independent:nine-declared-legacy-corrections", projection="complete",
        request=dict(version=1, operation="espn_status", cases=[case["input"] for case in defects]),
        expected=dict(version=1, results=answers)))
    checks.append(dict(id="independent:unknown-window-seam", projection="complete", request=dict(version=1,
        operation="fixture_window", cases=[dict(id="unknown-pending", now_utc="2026-10-23T17:00:00Z",
        time_zone="America/New_York", kickoff_utc="2026-10-23T16:55:00Z", time_confirmed=True,
        source_state="unknown", received_at="2026-10-23T16:59:00Z")]), expected=dict(version=1, results=[dict(
        id="unknown-pending", day_index=0, bucket="live", in_window=True, in_focus=True,
        source_in_progress=False, recent_live_receipt=False)])))
    return checks, baseline


def invalid_requests(checks):
    original = checks[0]["request"]["cases"][0]
    requests = []
    for field in ("competition_status", "home_score"):
        missing = deepcopy(original); missing.pop(field)
        requests.append(dict(version=1, operation="espn_status", cases=[missing]))
    requests.extend([
        dict(version=1, operation="espn_status", cases=[dict(original, extra=True)]),
        dict(version=1, operation="espn_status", cases=[original, original]),
        dict(version=1, operation="espn_status", cases=[dict(original, id=17)]),
        dict(version=1, operation="espn_status", cases=[None]),
        dict(version=1, operation="fixture_status", cases=[dict(id="missing", phase="in", home_score=0, away_score=0)]),
        dict(version=1, operation="fixture_status", cases=[dict(id="extra", phase="in", label="Live", home_score=0, away_score=0, extra=True)]),
    ])
    return requests


def browser_protocol(engine, base, checks):
    errors = []
    with engine.new_context() as context:
        context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname == "127.0.0.1"
                      else route.fulfill(status=404, body=""))
        context.add_init_script("window.__violations=[];addEventListener('securitypolicyviolation',e=>__violations.push(e.violatedDirective));")
        page = context.new_page(); page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/index.html")
        for check in checks:
            assert_response(check, page.evaluate("value=>JSON.parse(outlookDecisions(value))", json.dumps(check["request"])))
        malformed = invalid_requests(checks)
        for request in malformed:
            require(page.evaluate("value=>JSON.parse(outlookDecisions(value))", json.dumps(request)) ==
                    dict(version=1, error="invalid_input"), "Malformed status frame was accepted")
        assert_response(checks[0], page.evaluate("value=>JSON.parse(outlookDecisions(value))", json.dumps(checks[0]["request"])))
        require(not errors and not page.evaluate("__violations"), "Protocol browser error or strict CSP violation")
    return dict(checks=len(checks), malformed_requests_rejected=len(malformed), strict_csp=True, page_errors=errors)


STATIC = {
    "upcoming": ("pre", "", "0", "0"),
    "cancelled": ("post", "Canceled", "0", "0"),
    "postponed": ("pre", "Postponed", "0", "0"),
    "abandoned": ("post", "Abandoned", "2", "1"),
    "unknown": ("post", "Awaiting Official Ruling", "0", "0"),
    "missing": ("unknown", "Status unknown", "0", "0"),
    "tbd": ("pre", "", "0", "0"),
    "tbd-live": ("in", "12'", "0", ""),
    "final": ("post", "FT", "0", "1"),
    "raw-clock-ft": ("in", "FT", "1", "0"),
    "raw-clock-postponed": ("in", "Postponed", "1", "0"),
}


def write_pages(out, baseline):
    import build
    from tests.page_fixture import render_page
    actual = build.build_page
    def scorers(matches):
        for match in matches:
            match.goals = [("12'", match.home.id, "Literal Scorer", "")]
    def decorated(matches, *args, **kwargs):
        scorers(matches)
        for index, match in enumerate(matches):
            if match.id in STATIC:
                match.state, match.status, match.home.score, match.away.score = STATIC[match.id]
            if match.id in ("raw-clock-ft", "raw-clock-postponed"):
                source = dict(id=match.id, date=match.utc.isoformat(), competitions=[dict(
                    status=dict(type=dict(state="in", name="STATUS_IN_PROGRESS"), displayClock=match.status),
                    competitors=[dict(homeAway=side, score=score, team=dict(id=team.id,
                        displayName=team.name, abbreviation=team.abbr))
                        for side, team, score in (("home", match.home, "1"), ("away", match.away, "0"))],
                    geoBroadcasts=[dict(media=dict(shortName="ESPN+"), region="us", market=dict(type="National"))])])
                decoded = build.interpret("eng.1", source)
                # Exercise the actual persisted Match shape, not a synthetic
                # private decoder cache; one row is restored through JSON.
                if match.id == "raw-clock-postponed":
                    saved = json.loads(json.dumps(asdict(decoded), default=lambda value: value.isoformat()))
                    saved["utc"] = datetime.fromisoformat(saved["utc"])
                    saved["home"], saved["away"] = build.Team(**saved["home"]), build.Team(**saved["away"])
                    saved["outlets"] = [build.Outlet(**value) for value in saved["outlets"]]
                    if saved["rule"] is not None:
                        saved["rule"] = build.Outlet(**saved["rule"])
                    decoded = build.Match(**saved)
                scorers([decoded]); matches[index] = decoded
        return actual(matches, *args, **kwargs)
    def destinations(match):
        return {"ESPN+": {"espnplus": dict(url=LIVE_LINK, kind="match", title="Qualified live destination", live_only=True)}}
    with patch.object(build, "provider_links", side_effect=destinations):
        with patch.object(build, "build_page", side_effect=decorated):
            static = render_page(build, fixtures=[(identity,
                "2026-10-07T18:00:00+00:00" if identity == "tbd-live" else "2026-10-07T16:30:00+00:00", facts[0], "ESPN+")
                for identity, facts in STATIC.items()], tbd=("tbd", "tbd-live"), inline_runtime=False)
        defects = [case for case in baseline["cases"] if case["origin"] is None]
        with patch.object(build, "build_page", side_effect=decorated):
            dynamic = render_page(build, fixtures=[(case["id"].split(":")[1], "2026-10-07T16:30:00+00:00", "pre", "ESPN+")
                for case in defects], inline_runtime=False)
        def correct_exceptions(matches, *args, **kwargs):
            scorers(matches)
            for match in matches:
                if match.id == "over-contract":
                    match.state, match.status, match.home.score, match.away.score = "post", "Canceled", "0", "0"
            return actual(matches, *args, **kwargs)
        with patch.object(build, "build_page", side_effect=correct_exceptions):
            correction = render_page(build, fixtures=[(case["id"].split(":")[1], "2026-10-07T16:30:00+00:00",
                "post" if case["id"] in ("synthetic:cancel-abbreviation", "synthetic:over-contract") else "pre", "ESPN+")
                for case in defects], inline_runtime=False)
    build.outlook_runtime.publish_browser(out)
    asset = build.CORE_ASSET; digest = sha(out / asset)
    require(asset == f"outlook-core-{digest}.js", "Core asset is not content-addressed")
    require(FixtureBindings(static).external_scripts == [dict(src=asset,
        integrity="sha256-" + base64.b64encode(bytes.fromhex(digest)).decode())], "External core SRI differs")
    for name, html in (("static.html", static), ("dynamic.html", dynamic), ("correction.html", correction)):
        (out / name).write_text(html)
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>Status proof</title>')
    return static, dynamic, correction, asset


def row_state(page, identity):
    return page.locator(f'li.row[data-id="{identity}"]').evaluate("""row=>({
      phase:row.dataset.state,kind:row.dataset.statusKind,label:row.querySelector('.row__status').textContent,
      scores:Array.from(row.querySelectorAll('.team .score')).map(el=>el.hidden?null:el.textContent),
      links:Array.from(row.querySelectorAll('a.network__link')).map(el=>el.href),
      goals:Array.from(row.querySelectorAll('.row__goals')).map(el=>el.textContent),
      time_confirmed:row.dataset.tv,service:row.dataset.svc
    })""")


def assert_row(page, identity, *, phase, kind, label, scores, link=False):
    from playwright.sync_api import expect
    row = page.locator(f'li.row[data-id="{identity}"]')
    expect(row).to_have_attribute("data-state", phase)
    expect(row).to_have_attribute("data-status-kind", kind)
    expect(row.locator('.row__status')).to_have_text(label)
    value = row_state(page, identity)
    require(value["scores"] == scores, f"{identity}: scores differ: {value}")
    require(value["links"] == ([LIVE_LINK] if link else []), f"{identity}: live-only access differs: {value}")
    if kind not in ("live", "finished"):
        require(value["goals"] == [], f"{identity}: non-played result retained goal markup: {value}")
    return value


def event_from_case(case):
    value = case["input"]
    return dict(id=case["id"].split(":")[1], status=value["event_status"], competitions=[dict(
        status=value["competition_status"], competitors=[
            dict(homeAway="home", score=value["home_score"], team=dict(id="1", abbreviation="ARS")),
            dict(homeAway="away", score=value["away_score"], team=dict(id="2", abbreviation="CHE"))],
        details=[dict(scoringPlay=True, clock=dict(displayValue="12'"), team=dict(id="1"),
                      athletesInvolved=[dict(shortName="Literal Scorer")])])])


def browser_consumer(engine, base, pages, asset, baseline, out, name):
    from playwright.sync_api import expect
    from tests.browser import BrowserChecks
    from tests.browser_schedule import SnapshotHTML
    static, dynamic, correction = pages
    transitions = []
    with page_context(engine, base, static, asset, out, name + "-static") as (page, _state):
        answers = {}
        for identity, kind, label in (("cancelled", "cancelled", "Canceled"), ("postponed", "postponed", "Postponed"),
                                      ("abandoned", "abandoned", "Abandoned"), ("unknown", "unknown", "Status unknown")):
            answers[identity] = assert_row(page, identity, phase="post", kind=kind, label=label, scores=[None, None])
            card = page.locator(f'.nextup[data-match-id="{identity}"] .pick__when')
            expect(card).to_contain_text(label)
            require("Final" not in card.text_content() and "FT" not in card.text_content(), "Exceptional card invented a final result")
        answers["missing"] = assert_row(page, "missing", phase="unknown", kind="unknown", label="Status unknown", scores=[None, None])
        answers["final"] = assert_row(page, "final", phase="post", kind="finished", label="FT", scores=["0", "1"])
        require(answers["final"]["goals"] and "Literal Scorer" in answers["final"]["goals"][0], "Final scorer fixture was not rendered")
        answers["tbd"] = assert_row(page, "tbd", phase="pre", kind="scheduled", label="", scores=[None, None])
        require(answers["tbd"]["time_confirmed"] == "0", "Status projection invented kickoff confirmation")
        answers["tbd-live"] = assert_row(page, "tbd-live", phase="in", kind="live", label="12'", scores=["0", None], link=True)
        require(answers["tbd-live"]["time_confirmed"] == "0", "In-progress status confirmed a future TBD placeholder")
        for identity, label in (("raw-clock-ft", "FT"), ("raw-clock-postponed", "Postponed")):
            answers[identity] = assert_row(page, identity, phase="in", kind="live", label=label, scores=["1", "0"], link=True)
            require(answers[identity]['goals'], "Raw live display collision lost played scorer facts")
        BrowserChecks.select_match(page, "unknown")
        page.locator('#nextup button.more').click()
        expect(page.locator('#match-dialog')).to_be_visible()
        expect(page.locator('#match-dialog')).to_have_attribute('data-match-id', 'unknown')
        require("Final" not in page.locator('#nextup .pick__when').text_content(), "Opening details changed the unknown card into Final")
        page.locator('#match-dialog-close').click()
        transitions.append(dict(name="static_exception_unknown_final_and_tbd_surfaces", rows=answers))
    with page_context(engine, base, dynamic, asset, out, name + "-live") as (page, state):
        defects = [case for case in baseline["cases"] if case["origin"] is None]
        state["feed"] = dict(events=[event_from_case(case) for case in defects])
        page.clock.run_for(60000)
        expected = declared_corrections(); answers = {}
        for answer in expected:
            identity = answer['id'].split(':')[1]
            scores = [None if answer['scores'][side] is None else str(answer['scores'][side]) for side in ('home', 'away')]
            answers[identity] = assert_row(page, identity, phase=answer['phase'], kind=answer['state'],
                label=answer['label'], scores=scores, link=answer['state'] == 'live')
            if answer['played']:
                require(answers[identity]['goals'] and "Literal Scorer" in answers[identity]['goals'][0],
                        "Accepted played update did not render its scorer fixture")
        require(state["scores_requests"] >= 2, "Live decoder proof did not receive a new source response")
        state["feed"] = {"events": []}
        transitions.append(dict(name="accepted_raw_live_updates_apply_nine_declared_corrections", rows=answers))
        # A newer complete publication is authoritative over an earlier live
        # cancellation. This exercises status correction through the existing
        # schedule path, not a new live-overlay ordering policy.
        parsed = SnapshotHTML(correction)
        legacy_rows = [row.replace('<div class="row__context">',
            '<div class="row__context"><div class="row__goals">Literal old scorer</div>', 1)
            if 'data-id="over-contract"' in row else row for row in parsed.rows]
        require(legacy_rows != parsed.rows, "Legacy void scorer fixture did not apply")
        state["snapshot"] = dict(version=1, complete=True, generated_at="2026-10-07T17:02:00Z",
                                 runtime_revision=parsed.revision, rows=legacy_rows, logos={})
        page.clock.fast_forward(4 * 60000 + 100)
        expect(page.locator('#app')).to_have_attribute('data-schedule-built', '2026-10-07T17:02:00Z')
        corrected = assert_row(page, "cancel-abbreviation", phase="post", kind="finished", label="FT", scores=["2", "1"])
        same_phase = assert_row(page, "over-contract", phase="post", kind="cancelled", label="Canceled", scores=[None, None])
        expect(page.locator('.nextup[data-match-id="cancel-abbreviation"] .pick__when')).to_contain_text('FT')
        expect(page.locator('.nextup[data-match-id="over-contract"] .pick__when')).to_contain_text('Canceled')
        transitions.append(dict(name="newer_snapshot_corrects_prior_exception_and_same_phase_final", row=corrected, same_phase=same_phase,
            input_transformation="Literal old scorer markup inserted into the cancelled replacement row; actual publisher status and score facts preserved."))
        # A display-only type attribute cannot override the shared seam. This
        # source fixture replaces one saved row with unknown phase and otherwise
        # valid markup; unknown status must retain independently valid access.
        changed = deepcopy(state["snapshot"])
        changed["generated_at"] = "2026-10-07T17:06:00Z"
        changed["rows"] = [row.replace('data-state="pre"', 'data-state="unknown"', 1).replace(
            '<div class="row__context">', '<div class="row__context"><div class="row__goals">Literal old scorer</div>', 1)
            if 'data-id="missing-status"' in row else row
                           for row in changed["rows"]]
        require(changed["rows"] != state["snapshot"]["rows"], "Unknown snapshot fixture transformation did not apply")
        state["snapshot"] = changed
        page.clock.fast_forward(5 * 60000 + 100)
        expect(page.locator('#app')).to_have_attribute('data-schedule-built', '2026-10-07T17:06:00Z')
        unknown = assert_row(page, "missing-status", phase="unknown", kind="unknown", label="Status unknown", scores=[None, None])
        require(unknown['service'] == 'espnplus', "Unknown progress erased an independently valid viewing route")
        transitions.append(dict(name="unknown_snapshot_retains_valid_route", row=unknown,
                                input_transformation="One literal data-state changed to unknown and old scorer markup inserted; destination and route facts preserved."))
    # Accessible fallback uses the actual publisher-produced markup as well.
    with engine.new_context(java_script_enabled=False) as context:
        context.route('**/*', lambda request: request.fulfill(status=404, body=''))
        page = context.new_page(); page.set_content(static)
        for identity in ('cancelled', 'postponed', 'abandoned', 'unknown', 'missing'):
            require(page.locator(f'li.row[data-id="{identity}"] .row__goals').count() == 0,
                    "Static fallback exposed an exceptional scorer")
            require(page.locator(f'li.row[data-id="{identity}"] .team .score').evaluate_all('els=>els.every(el=>el.hidden)'),
                    "Static fallback exposed an exceptional placeholder score")
        require(page.locator('li.row[data-id="final"] .team .score').all_text_contents() == ['0', '1'], "Static fallback lost final zero")
        for identity in ('raw-clock-ft', 'raw-clock-postponed'):
            row = page.locator(f'li.row[data-id="{identity}"]')
            require(row.get_attribute('data-state') == 'in' and row.get_attribute('data-status-kind') == 'live',
                    "Static fallback reclassified a raw live display label")
            require(row.locator('.team .score').all_text_contents() == ['1', '0'], "Static fallback lost bound live scores")
        transitions.append(dict(name="no_javascript_static_result_fallback", final_scores=['0', '1']))
    return dict(transitions=transitions, external_sri_core=True, page_errors=[],
                fixture_inputs="Synthetic normalized Match values with literal scorer facts; raw live display collisions decoded through interpret (one JSON-restored Match); frozen raw defects with a supplied scoring-play detail; a qualified literal live-only destination; explicitly described old scorer and unknown snapshot transformations; no destination opened.",
                csp_scope="Protocol page enforces strict CSP; generated production page retains existing inline scripts/styles.")


def source_hashes():
    values = core_hashes()
    paths = [Path(__file__), ROOT / "tests/test_companion_status.py", EXAMPLES, BASELINE, FUNCTIONS,
             ROOT / "tool/companion_window_verify.py", ROOT / "metrics.py", ROOT / "discovery.py",
             ROOT / "companion_export.py", ROOT / "broadcast_listings.py", ROOT / "provider_utils.py"]
    paths.extend((ROOT / "packages/outlook_data/lib").rglob("*.dart"))
    for path in paths:
        values[str(path.relative_to(ROOT))] = sha(path)
    return dict(sorted(values.items()))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dart', default='dart')
    parser.add_argument('--runtime', type=Path, default=ROOT / 'work/outlook-runtime')
    parser.add_argument('--out', type=Path, default=ROOT / 'work/companion-status')
    parser.add_argument('--browser', choices=('chromium', 'webkit', 'both'), default='both')
    return parser.parse_args(argv)


def main():
    args = parse_args(); out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(not (out / 'report.json').exists(), 'Choose a new evidence directory; existing reports are preserved')
    before = source_hashes(); verify_runtime(runtime)
    os.environ['OUTLOOK_RUNTIME_DIR'] = str(runtime)
    version = subprocess.run([args.dart, '--version'], capture_output=True, text=True, check=True)
    dart_version = (version.stdout + version.stderr).strip()
    pin = json.loads((ROOT / 'companion-toolchain.json').read_text())['dart']
    require(f'version: {pin} ' in dart_version, 'Dart VM differs from pinned compiler')
    checks, baseline = load_checks()
    shutil.copyfile(runtime / 'outlook.js', out / 'outlook.js')
    static, dynamic, correction, asset = write_pages(out, baseline)
    outcomes = {}
    commands = dict(dart_vm=[args.dart, '--suppress-analytics', 'run', 'bin/outlook.dart'], compiled_cli=[str(runtime / 'outlook')])
    malformed = invalid_requests(checks)
    for name, command in commands.items():
        outcomes[name] = command_checks(command, checks)
        payloads = malformed + [checks[0]['request']]
        output, _ = run([*command, '--serve'], '\n'.join(json.dumps(value) for value in payloads) + '\n')
        answers = [json.loads(line) for line in output.splitlines()]
        require(len(answers) == len(payloads), 'Malformed stream lost framing')
        require(all(answer == dict(version=1, error='invalid_input') for answer in answers[:-1]), 'Malformed status frame accepted')
        assert_response(checks[0], answers[-1])
        outcomes[name]['status_malformed_requests_rejected'] = len(malformed)
    from playwright.sync_api import sync_playwright
    browsers = {}
    with serve(out) as base, sync_playwright() as playwright:
        for name in (('chromium', 'webkit') if args.browser == 'both' else (args.browser,)):
            with getattr(playwright, name).launch() as engine:
                browsers[name] = dict(version=engine.version, protocol=browser_protocol(engine, base, checks),
                    generated_page=browser_consumer(engine, base, (static, dynamic, correction), asset, baseline, out, name))
    require(before == source_hashes(), 'Sources changed during status proof'); verify_runtime(runtime)
    size = len(gzip.compress((runtime / 'outlook.js').read_bytes(), mtime=0))
    limit = json.loads((ROOT / 'tool/companion_p0_limits.json').read_text())['budgets']['browser_core_gzip_bytes']
    require(size <= limit, 'Core exceeds gzip budget')
    report = dict(version=1, status='passed', source_hashes=before, sources_unchanged=True, dart_version=dart_version,
        checks=len(checks), literal_status_decisions=sum(len(group['request']['cases']) for group in json.loads(EXAMPLES.read_text())['cases']),
        preserved_legacy_decisions=360, declared_corrections=9, unknown_window_cases=1,
        baseline=dict(sha256=sha(BASELINE), origin=baseline['origin']),
        runtime_hashes={name:sha(runtime/name) for name in ('runtime.json','outlook','outlook.js')},
        browser_gzip_bytes=size, browser_gzip_limit=limit, consumers=outcomes, browsers=browsers,
        limitations=['Provider observation freshness, overlay identity/order, goals, time confirmation and new shootout/aggregate mapping remain outside this decoder.',
                     'Ordinary360capture contains no exceptional terminal or TBD cases; independent literals and synthetic production pages provide those boundaries.',
                     'All source and AI requests are intercepted; no destination opened, network collection, distribution or device acceptance claimed.'])
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(status='passed',checks=len(checks),browsers=list(browsers))))


if __name__ == '__main__':
    main()
