#!/usr/bin/env python3
"""Offline route-validity proof: literal protocol answers and saved-page expiry."""
import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
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

EXAMPLES = ROOT / "docs/companion/rules/route-validity-examples.json"
BUILT_AT = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
EXPIRY = "2026-10-07T17:00:02Z"
RETAINED_LINK = "https://example.com/watch/retained/espn"
ALTERNATE_LINK = "https://example.com/watch/alternative/paramount"
FIXTURES = [("retained", "2026-10-07T18:00:00+00:00", "pre", "ESPN+", "eng.1"),
            ("alternative", "2026-10-07T19:00:00+00:00", "pre", "ESPN+", "esp.1"),
            ("usual", "2026-10-08T18:00:00+00:00", "pre", None, "usa.1")]


def load_checks():
    examples = json.loads(EXAMPLES.read_text())
    require(examples["version"] == 1 and examples["cases"], "Independent examples are missing")
    checks = []
    for example in examples["cases"]:
        require(example.get("because"), "Independent expectation needs a reason")
        checks.append(dict(id="independent:" + example["id"], projection="complete",
                           request=example["request"], expected=example["expected"]))
    return checks + composition_checks()


def validity(**changes):
    """Fixture inputs only; no expected decision is computed here."""
    return dict(dict(basis="listed", retained=False, verified_at=None, expires_at=None,
                     kickoff_utc=None, rights_until=None, retention_seconds=21600), **changes)


def fixture_destinations(fixture_id):
    if fixture_id not in ("retained", "alternative"):
        return {}
    result = {"ESPN+": {"espnplus": dict(url=f"https://example.com/watch/{fixture_id}/espn",
                                         kind="match", title="Open qualified fixture", live_only=False)}}
    if fixture_id == "alternative":
        result["Paramount+"] = {"para": dict(url=ALTERNATE_LINK, kind="match", title="Open qualified alternate fixture", live_only=False)}
    return result


def composition_checks():
    expired = validity(retained=True, verified_at="2026-10-20T12:00:00Z",
                       kickoff_utc="2026-10-20T20:00:00Z")
    fresh = validity()
    usual = validity(basis="rule", rights_until="2026-10-20")
    def candidate(label, services, metadata):
        return dict(label=label, service_ids=services, spanish=False, validity=metadata)
    dead = candidate("ESPN+", ["espnplus"], expired)
    live = candidate("Paramount+", ["paramount"], fresh)
    fallback = candidate("Apple TV", ["apple"], usual)
    denied = dict(usable=False, reason="expired", expires_at="2026-10-20T18:00:00.000Z")
    unlimited = dict(usable=True, reason=None, expires_at=None)
    rule_answer = dict(usable=True, reason=None, expires_at="2026-10-21T04:00:00.000Z")
    cases = [dict(id="alternate-survives", listed=[dead, live], usual=None),
             dict(id="explicit-usual-survives", listed=[dead], usual=fallback),
             dict(id="no-invented-usual", listed=[dead], usual=None)]
    results = [dict(id="alternate-survives", service_id="paramount", basis="listed", outlet="Paramount+",
                    listed_providers=[[], ["paramount"]], usual_providers=[],
                    listed_validity=[denied, unlimited], usual_validity=None),
               dict(id="explicit-usual-survives", service_id="apple", basis="rule", outlet="Apple TV",
                    listed_providers=[[]], usual_providers=["apple"],
                    listed_validity=[denied], usual_validity=rule_answer),
               dict(id="no-invented-usual", service_id=None, basis="none", outlet="",
                    listed_providers=[[]], usual_providers=[],
                    listed_validity=[denied], usual_validity=None)]
    return [dict(id="independent:route-composition", projection="complete",
                 request=dict(version=1, operation="route_selection", now_utc="2026-10-20T18:00:00Z",
                              lineup=dict(selected_service_ids=["espnplus", "paramount", "apple"],
                                          service_order=["espnplus", "paramount", "apple"],
                                          service_names={"espnplus": "ESPN+", "paramount": "Paramount+", "apple": "Apple TV"}),
                              cases=cases), expected=dict(version=1, results=results))]


def invalid_requests(checks):
    original = checks[0]["request"]["cases"][0]
    changes = [dict(now_utc="2026-10-20T17:00:00.0000001Z"),
               dict(now_utc="2026-10-20T17:00:00+00:00"),
               dict(verified_at="2026-02-30T00:00:00Z"),
               dict(rights_until="2026-02-30"), dict(retained="true"),
               dict(retention_seconds=-1), dict(retention_seconds=21601),
               dict(retention_seconds=1.5), dict(retention_seconds=True),
               dict(basis="replay"), dict(unknown_field=True)]
    requests = [dict(version=1, operation="route_validity", cases=[dict(original, **change)]) for change in changes]
    missing = dict(version=1, operation="route_validity", cases=[deepcopy(original)])
    missing["cases"][0].pop("retention_seconds")
    requests.append(missing)
    mismatch = deepcopy(composition_checks()[0]["request"])
    mismatch["cases"][0]["listed"][0]["validity"]["basis"] = "rule"
    requests.append(mismatch)
    return requests


class RowTags(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "row" in attrs.get("class", "").split():
            self.rows.append((self.get_starttag_text(), attrs))


def stamp_routes(html):
    """Supply literal qualified facts while retaining the production page/app/core."""
    changed = []
    for tag, attrs in RowTags(html).rows:
        fixture_id = attrs["data-id"]
        if fixture_id not in {"retained", "alternative", "usual"}:
            continue
        replacement = tag
        def set_attr(name, value):
            nonlocal replacement
            literal = name + '="' + escape(json.dumps(value, separators=(",", ":")), quote=True) + '"'
            if name in attrs:
                replacement, count = re.subn(r"\b" + name + r'="[^"]*"', lambda _match: literal, replacement, count=1)
                require(count == 1, "Fixture attribute replacement failed")
            else:
                replacement = replacement[:-1] + " " + literal + ">"
        if fixture_id == "usual":
            route = json.loads(attrs["data-r"])
            route["t"] = validity(basis="rule", rights_until="2026-10-07")
            set_attr("data-r", route)
        else:
            routes = json.loads(attrs["data-o"])
            require(len(routes) in ((1, 2) if fixture_id == "alternative" else (1,)), "Unexpected synthetic listing count")
            routes[0]["t"] = validity(retained=True, verified_at="2026-10-07T11:00:02Z",
                                      expires_at=EXPIRY, kickoff_utc=attrs["data-utc"])
            if fixture_id == "alternative":
                if len(routes) == 1:
                    routes.append(dict(l="Paramount+", v=["para"], f=0, e=0, t=validity()))
                else:
                    require(routes[1]["l"] == "Paramount+" and "para" in routes[1]["v"], "Unexpected alternate fixture outlet")
                    routes[1]["t"] = validity()
            set_attr("data-o", routes)
            set_attr("data-watch", fixture_destinations(fixture_id))
        html = html.replace(tag, replacement, 1)
        changed.append(fixture_id)
    require(sorted(changed) == ["alternative", "retained", "usual"], "Temporal fixtures were not all stamped")
    return html


def legacy_page(out, directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        require(sha(directory / name) == digest, "Frozen legacy capture changed")
    html = stamp_routes((directory / "index.html").read_text())
    path = out / "legacy-transformed.html"
    path.write_text(html)
    parsed = FixtureBindings(html)
    require(len(parsed.external_scripts) == 1, "Legacy proof lost external core packaging")
    asset = parsed.external_scripts[0]["src"]
    shutil.copyfile(directory / asset, out / asset)
    return html, asset, dict(original_files=manifest["files"], transformed_sha256=sha(path),
                             transformation="Only literal route metadata, qualified synthetic HTTPS destinations in data-watch and an independent alternate candidate were added; original app/core bytes retained. Legacy alternate disclosure markup is unchanged and is not used to prove its link behavior.")


def write_pages(out):
    import build
    from tests.page_fixture import render_page
    actual_build_page = build.build_page
    def render_qualified_fixtures(matches, *args, **kwargs):
        for match in matches:
            if match.id in ("retained", "alternative"):
                match.broadcast_evidence = [dict(channel="ESPN+", source="Fixture listings",
                    url="https://example.com/listings/" + match.id, country="US", kind="match_listing",
                    checked_at="2026-10-07T11:00:02Z", retained=True, expires_at=EXPIRY)]
            if match.id == "alternative":
                match.outlets.append(build.map_outlet("Paramount+", match.league))
        return actual_build_page(matches, *args, **kwargs)
    with patch.object(build, "build_page", side_effect=render_qualified_fixtures), \
         patch.object(build, "provider_links", side_effect=lambda match: fixture_destinations(match.id)):
        html = stamp_routes(render_page(build, fixtures=FIXTURES, ai=True, inline_runtime=False))
    parsed = FixtureBindings(html)
    require(len(parsed.external_scripts) == 1, "Production proof requires one external core")
    build.outlook_runtime.publish_browser(out)
    asset = build.CORE_ASSET
    digest = sha(out / asset)
    require(asset == f"outlook-core-{digest}.js", "Core filename is not content-addressed")
    require(parsed.external_scripts[0] == dict(src=asset,
            integrity="sha256-" + base64.b64encode(bytes.fromhex(digest)).decode(), ),
            "Production external script attributes differ from the SRI contract")
    (out / "rendered.html").write_text(html)
    (out / "index.html").write_text('<!doctype html><meta charset="utf-8"><script src="outlook.js"></script><title>Validity proof</title>')
    return html, asset


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
                    dict(version=1, error="invalid_input"), "Malformed validity input was accepted")
        require(not errors and not page.evaluate("__violations"), "Protocol browser error or strict CSP violation")
    return dict(checks=len(checks), malformed_requests_rejected=len(malformed), strict_csp=True, page_errors=errors)


def fixture_story():
    text = "Retained fixture storyline"
    item = dict(text=text, match_ids=["retained"], segments=[dict(text=text, match_ids=["retained"])],
                sources=[dict(url="https://example.com/qualified-report", title="Fixture report")])
    return dict(version=1, date="2026-10-07", generated_at="2026-10-07T17:00:00Z",
                focus_until="2026-10-08T17:00:00Z", headline=text,
                headline_segments=[dict(text=text, match_ids=["retained"])], lede=text,
                lede_items=[item], forecast=dict(items=[item]), notes={}, sources=[], later_reason="")


@contextmanager
def page_context(engine, base, html, asset, out, label, *, at=BUILT_AT, pinned=False, story=False):
    from playwright.sync_api import expect
    errors, requests = [], []
    state = {"snapshot": None, "schedule_requests": 0}
    with engine.new_context(timezone_id="America/New_York", viewport={"width": 1280, "height": 900}) as context:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        def route(request):
            path = urlsplit(request.request.url).path
            if urlsplit(request.request.url).hostname == "127.0.0.1" and path == "/rendered.html":
                request.fulfill(content_type="text/html", body=html)
            elif urlsplit(request.request.url).hostname == "127.0.0.1" and path == "/" + asset:
                requests.append(path)
                request.continue_()
            elif path.endswith("/schedule.json"):
                state["schedule_requests"] += 1
                if state["snapshot"] is None:
                    request.fulfill(status=404, body="Offline fixture")
                else:
                    request.fulfill(content_type="application/json", body=json.dumps(state["snapshot"]))
            elif path.endswith("/story.json") and story:
                request.fulfill(content_type="application/json", body=json.dumps(fixture_story()))
            elif path.startswith("/espn/"):
                request.fulfill(content_type="application/json", body='{"events":[]}')
            else:
                request.fulfill(status=404, body="Offline fixture")
        context.route("**/*", route)
        context.add_init_script("localStorage.setItem('ssg3-have',JSON.stringify(['espnplus','para','apple']));localStorage.setItem('ssg4-service-order',JSON.stringify(['espnplus','para','apple']));localStorage.setItem('ssg3-ai-blurbs','true');")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install(time=at - timedelta(seconds=1))
        page.clock.pause_at(at)
        suffix = "#at-20261007-1300" if pinned else ""
        try:
            page.goto(base + "/rendered.html?scoresbase=" + base + "/espn/" + suffix)
            expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
            require(not errors, f"{label}: startup errors {errors}")
            route_state(page)
            yield page, state
            require(not errors, f"{label}: JavaScript errors {errors}")
            require(requests, f"{label}: external core was not loaded")
        finally:
            context.tracing.stop(path=str(out / (label + ".zip")))


def route_state(page):
    # Filtering detaches existing rows. Hold the original DOM elements so exact
    # expiry assertions still inspect their real updated public attributes.
    # Snapshot assertions separately inspect the connected DOM and local prompt;
    # an old retained reference is not evidence about a replacement row.
    return page.evaluate("""() => {
      window.__validityProofRows = window.__validityProofRows || {};
      document.querySelectorAll('li.row').forEach(row => { window.__validityProofRows[row.dataset.id] = row; });
      return Object.fromEntries(Object.entries(window.__validityProofRows).map(([id,row]) => [id, {
        service:row.dataset.svc, basis:row.dataset.basis, hidden:row.hidden,
        connected:row.isConnected, outlet:row.dataset.outlet, metadata:row.getAttribute('data-o'),
        provider_links:Array.from(row.querySelectorAll('a.network__link')).map(link=>link.href),
        provider_text:Array.from(row.querySelectorAll('.network__providers')).map(panel=>panel.textContent)
      }]));
    }""")


def visible_route_ids(page):
    return page.locator("#outlook-body li.row").evaluate_all("rows=>rows.filter(row=>!row.hidden).map(row=>row.dataset.id).sort()")


def legacy_consumer(engine, base, html, asset, out, name):
    with page_context(engine, base, html, asset, out, name + "-legacy") as (page, _state):
        before = route_state(page)
        require(before["retained"]["service"] == "espnplus", "Legacy starting route missing")
        page.clock.run_for(2000)
        exact = route_state(page)
        require(exact["retained"]["service"] == "espnplus" and not exact["retained"]["hidden"],
                "Legacy captured page no longer reproduces saved-route expiry defect")
        page.reload()
        after_reload = route_state(page)
        require(after_reload["retained"]["service"] == "espnplus", "Legacy reload unexpectedly honors validity metadata")
    return dict(reproduced=True, before=before, at_expiry=exact, reloaded=after_reload,
                expected_fix="At 17:00:02Z retained has no selected route; independently qualified alternate remains.")


def browser_consumer(engine, base, html, asset, out, name):
    from playwright.sync_api import expect
    from tests.browser import BrowserChecks
    from tests.browser_schedule import SnapshotHTML
    transitions = []
    with page_context(engine, base, html, asset, out, name + "-lifecycle", story=True) as (page, state):
        page.clock.run_for(1)
        expect(page.locator("#story-headlines .story-headline__title")).to_have_text("Retained fixture storyline")
        expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
        require(route_state(page)["alternative"]["service"] == "espnplus", "Initial preferred route did not precede its alternate")
        original_metadata = page.locator('li.row[data-id="retained"]').get_attribute("data-o")
        solo = page.locator('#league-strip-list [data-league="eng.1"]')
        solo.focus(); page.keyboard.press("Shift+Enter")
        require(route_state(page)["alternative"]["hidden"], "Solo setup did not hide alternate league")
        retained_row = page.locator('li.row[data-id="retained"]')
        retained_row.locator(".network summary").click()
        expect(retained_row.locator(f'a.network__link[href="{RETAINED_LINK}"]')).to_be_visible()
        expect(page.locator(f'#nextup a.network__link[href="{RETAINED_LINK}"]')).to_have_count(1)
        page.keyboard.press("Escape")
        retained_row.locator("button.more").click()
        expect(page.locator("#match-dialog")).to_be_visible()
        expect(page.locator("#match-dialog")).to_have_attribute("data-match-id", "retained")
        expect(page.locator("#match-dialog .detail__broadcast").first).to_contain_text("saved listing")
        page.clock.run_for(1998)
        require(page.evaluate("new Date().toISOString()") == "2026-10-07T17:00:01.999Z", "Boundary proof clock drifted before expiry")
        before = route_state(page)
        require(before["retained"]["service"] == "espnplus" and not before["retained"]["hidden"], "Route expired before its exact millisecond")
        page.clock.run_for(1)
        require(page.evaluate("new Date().toISOString()") == "2026-10-07T17:00:02.000Z", "Boundary proof did not evaluate the exact expiry")
        exact = route_state(page)
        require(exact["retained"]["service"] == "none" and exact["retained"]["hidden"], "Retained route survives exact expiry")
        require(exact["alternative"]["service"] == "para" and not exact["alternative"]["hidden"], "Alternate route or solo reset failed")
        require(visible_route_ids(page) == ["alternative", "usual"], "Expiry did not restore the surviving rows to the visible schedule")
        require(exact["retained"]["provider_links"] == [] and
                "This listing has expired." in exact["retained"]["provider_text"], "Expired retained disclosure kept access or lost its expiry explanation")
        require(exact["alternative"]["provider_links"] == [ALTERNATE_LINK], "Alternate disclosure did not retain exactly its valid link")
        expect(page.locator(f'a.network__link[href="{RETAINED_LINK}"]')).to_have_count(0)
        expect(page.locator(f'#nextup a.network__link[href="{ALTERNATE_LINK}"]')).to_have_count(1)
        expect(page.locator("#match-dialog")).to_be_visible()
        expect(page.locator("#match-dialog .detail__broadcast").first).to_contain_text("saved listing")
        page.locator("#match-dialog-close").click()
        alternate_row = page.locator('li.row[data-id="alternative"]')
        alternate_row.locator('.network[data-i="0"] summary').click()
        expect(alternate_row.locator('.network[data-i="0"] .network__providers')).to_have_text("This listing has expired.")
        page.keyboard.press("Escape")
        alternate_row.locator('.network[data-i="1"] summary').click()
        expect(alternate_row.locator(f'a.network__link[href="{ALTERNATE_LINK}"]')).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator('#league-strip-list [data-league="eng.1"]')).to_have_count(0)
        expect(page.locator("#story-headlines .story-headline__title")).to_have_count(0)
        require(exact["retained"]["metadata"] == original_metadata, "Clock expiry rewrote original route evidence")
        transitions.append(dict(name="exact_expiry_resets_solo_and_editorial", before_utc="2026-10-07T17:00:01.999Z",
                                after_utc="2026-10-07T17:00:02.000Z", before=before, after=exact))
        BrowserChecks.open_settings(page, "ai")
        page.locator("#ai-generate").click()
        expect(page.locator("#ai-error")).to_be_empty()
        prompt = BrowserChecks.personal_ai_snapshot(page)
        prompt_matches = {match["id"]: match for match in prompt["matches"]}
        require("retained" not in prompt_matches, "Expired route entered a local recommendation prompt")
        require(prompt_matches["alternative"]["channels"] == ["Paramount+"], "Expired channel leaked alongside the valid alternate into a local prompt")
        page.locator("#btn-filters-close").click()
        transitions.append(dict(name="local_prompt_uses_only_valid_channels", alternative_channels=prompt_matches["alternative"]["channels"]))
        page.reload()
        expect(page.locator("#eyebrow")).not_to_have_text("Your lineup")
        require(visible_route_ids(page) == ["alternative", "usual"], "Reload renewed expired access or lost a surviving route")
        require(route_state(page)["alternative"]["service"] == "para", "Reload lost the unexpired alternate")
        expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
        parsed = SnapshotHTML(html)
        state["snapshot"] = dict(version=1, complete=True, generated_at="2026-10-07T17:01:00Z",
                                 runtime_revision=parsed.revision, rows=parsed.rows, logos={})
        page.clock.fast_forward(5 * 60000 + 100)
        expect(page.locator("#app")).to_have_attribute("data-schedule-built", "2026-10-07T17:01:00Z")
        require(visible_route_ids(page) == ["alternative", "usual"], "Snapshot receipt renewed expired access or lost a surviving route")
        require(route_state(page)["alternative"]["service"] == "para", "Snapshot receipt lost the unexpired alternate")
        BrowserChecks.open_settings(page, "ai")
        page.locator("#ai-generate").click()
        expect(page.locator("#ai-error")).to_be_empty()
        refreshed_prompt = BrowserChecks.personal_ai_snapshot(page)
        refreshed_matches = {match["id"]: match for match in refreshed_prompt["matches"]}
        require("retained" not in refreshed_matches and refreshed_matches["alternative"]["channels"] == ["Paramount+"],
                "Snapshot replacement renewed expired access in the local prompt")
        page.locator("#btn-filters-close").click()
        transitions.append(dict(name="reload_and_snapshot_preserve_expiry", visible_ids=visible_route_ids(page),
                                prompt_channels=refreshed_matches["alternative"]["channels"]))
        malformed = deepcopy(state["snapshot"])
        malformed["generated_at"] = "2026-10-07T17:06:00Z"
        malformed["rows"][0] = malformed["rows"][0].replace('&quot;retention_seconds&quot;:21600', '&quot;retention_seconds&quot;:21601')
        require(malformed["rows"][0] != state["snapshot"]["rows"][0], "Malformed snapshot fixture was not changed")
        state["snapshot"] = malformed
        previous_requests = state["schedule_requests"]
        page.clock.fast_forward(5 * 60000 + 100)
        expect(page.locator("#schedule-note")).to_contain_text("saved schedule remains available")
        require(state["schedule_requests"] > previous_requests, "Malformed snapshot was not requested")
        require(page.locator("#app").get_attribute("data-schedule-built") == "2026-10-07T17:01:00Z", "Malformed metadata replaced the validated snapshot")
        transitions.append(dict(name="malformed_snapshot_rejected", state=route_state(page)))
    midnight = datetime(2026, 10, 8, 3, 59, 59, 999000, tzinfo=timezone.utc)
    with page_context(engine, base, html, asset, out, name + "-usual-midnight", at=midnight) as (page, _state):
        require(route_state(page)["usual"]["service"] == "apple", "Usual route expired before Eastern midnight")
        page.clock.run_for(1)
        require(route_state(page)["usual"]["service"] == "none", "Usual route survives Eastern midnight")
        require("usual" not in visible_route_ids(page), "Expired usual coverage remains in the visible schedule")
        transitions.append(dict(name="usual_eastern_midnight", state=route_state(page)))
    with page_context(engine, base, html, asset, out, name + "-pinned", at=BUILT_AT + timedelta(minutes=1), pinned=True) as (page, _state):
        require(route_state(page)["retained"]["service"] == "espnplus", "Pinned preview ignored its explicit clock")
        page.clock.run_for(3001)
        require(route_state(page)["retained"]["service"] == "espnplus", "Wall timer expired a pinned valid route")
        page.evaluate("location.hash='at-20261007-1301'")
        expect(page.locator('li.row[data-id="retained"]')).to_have_count(0)
        require(route_state(page)["retained"]["service"] == "none", "Pinned clock change did not expire the retained row")
        transitions.append(dict(name="pinned_preview_clock", state=route_state(page)))
    with page_context(engine, base, html, asset, out, name + "-resume") as (page, _state):
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))")
        page.clock.set_system_time(BUILT_AT + timedelta(seconds=5))
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('li.row[data-id="retained"]')).to_have_count(0)
        require(route_state(page)["retained"]["service"] == "none", "Visibility resume did not expire the retained row")
        transitions.append(dict(name="visibility_resume", state=route_state(page)))
    return dict(transitions=transitions, external_sri_core=True, page_errors=[],
                fixture_inputs="Qualified retained ledger facts, an actual second production-rendered outlet and literal synthetic HTTPS destinations; no destination is opened.",
                csp_scope="The protocol page enforces strict CSP; the actual production page retains existing inline scripts and styles.")


def source_hashes():
    values = core_hashes()
    for path in (Path(__file__), ROOT / "tests/test_companion_validity.py", EXAMPLES,
                 ROOT / "broadcast_listings.py", ROOT / "companion_export.py", ROOT / "provider_utils.py"):
        values[str(path.relative_to(ROOT))] = sha(path)
    return dict(sorted(values.items()))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--runtime", type=Path, default=ROOT / "work/outlook-runtime")
    parser.add_argument("--out", type=Path, default=ROOT / "work/companion-validity")
    parser.add_argument("--legacy", type=Path, help="Optional frozen historical page capture for local defect reproduction")
    parser.add_argument("--legacy-only", action="store_true")
    parser.add_argument("--browser", choices=("chromium", "webkit", "both"), default="both")
    args = parser.parse_args(argv)
    if args.legacy_only and args.legacy is None:
        parser.error("--legacy-only requires an explicit --legacy capture directory")
    return args


def main():
    args = parse_args()
    out, runtime = args.out.resolve(), args.runtime.resolve()
    out.mkdir(parents=True, exist_ok=True)
    baseline = None
    if args.legacy is not None:
        legacy_html, legacy_asset, baseline = legacy_page(out, args.legacy.resolve())
    checks, outcomes = [], {}
    before = None
    if not args.legacy_only:
        before = source_hashes()
        verify_runtime(runtime)
        os.environ["OUTLOOK_RUNTIME_DIR"] = str(runtime)
        version = subprocess.run([args.dart, "--version"], capture_output=True, text=True, check=True)
        dart_version = (version.stdout + version.stderr).strip()
        pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
        require(f"version: {pin} " in dart_version, "Dart VM differs from the pinned compiler")
        checks = load_checks()
        shutil.copyfile(runtime / "outlook.js", out / "outlook.js")
        html, asset = write_pages(out)
        commands = dict(dart_vm=[args.dart, "--suppress-analytics", "run", "bin/outlook.dart"], compiled_cli=[str(runtime / "outlook")])
        for name, command in commands.items():
            outcomes[name] = command_checks(command, checks)
            for request in invalid_requests(checks):
                output, _ = run(command, json.dumps(request), expected_exit=65)
                require(json.loads(output) == dict(version=1, error="invalid_input"), "Malformed validity payload accepted")
            outcomes[name]["validity_malformed_requests_rejected"] = len(invalid_requests(checks))
    from playwright.sync_api import sync_playwright
    browsers = {}
    names = ("chromium", "webkit") if args.browser == "both" else (args.browser,)
    with serve(out) as base, sync_playwright() as playwright:
        for name in names:
            with getattr(playwright, name).launch() as engine:
                result = dict(version=engine.version)
                if args.legacy is not None:
                    result["legacy"] = legacy_consumer(engine, base, legacy_html, legacy_asset, out, name)
                if not args.legacy_only:
                    result["protocol"] = browser_protocol(engine, base, checks)
                    result["generated_page"] = browser_consumer(engine, base, html, asset, out, name)
                browsers[name] = result
    report = dict(version=1, status="passed", legacy_requested=args.legacy is not None,
                  legacy_capture=baseline, browsers=browsers, consumers=outcomes)
    if not args.legacy_only:
        require(before == source_hashes(), "Sources changed during validity proof")
        verify_runtime(runtime)
        size = len(gzip.compress((runtime / "outlook.js").read_bytes(), mtime=0))
        limit = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
        require(size <= limit, "Core exceeds the gzip budget")
        report.update(source_hashes=before, sources_unchanged=True, dart_version=dart_version,
                      independent_requests=len(checks), browser_gzip_bytes=size, browser_gzip_limit=limit,
                      runtime_hashes={name: sha(runtime / name) for name in ("runtime.json", "outlook", "outlook.js")},
                      limitations=["Temporal facts are deliberately supplied; source identity, rejection and destination qualification remain upstream.",
                                   "All source and optional AI requests are intercepted; no publication, distribution or physical-device acceptance is claimed."])
        if args.legacy is not None:
            report["limitations"].append("The legacy reproduction keeps original app/core bytes and adds literal validity metadata that the old adapter ignores.")
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(status=report["status"], independent_requests=len(checks), browsers=list(browsers), legacy_only=args.legacy_only)))


if __name__ == "__main__":
    main()
