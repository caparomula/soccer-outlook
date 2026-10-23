#!/usr/bin/env python3
"""Offline P1 producer -> independent Dart reader verification.

An optional historical context exercises captured Match shapes, with explicitly
synthetic date coverage; it cannot retroactively prove historical source scope.
No tool path downloads data, calls AI, deploys, or reads a personal store.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build
import companion_export as exporter
import companion_publication as publisher
from companion_contract import validate_publication
from tests.test_discovery import fixture

NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def sample():
    make = lambda key, **kw: fixture(key, kickoff="2026-10-20T19:00:00Z", **kw)
    matches = [make("scheduled"), make("live", state="in", status="33'"),
               make("final", state="post", status="FT", channel="ESPN+"),
               make("unknown", channel="Unmapped fixture channel"), make("unconfirmed",time_valid=False),
               make("excluded",channel="Universo"), make("replay"),
               make("usual",league="usa.1",channel=None)]
    matches[1].home.score, matches[1].away.score = "0", ""
    matches[6].broadcast_evidence = [dict(channel="USA Network",source="fixture source",url="https://example.com/replay",
        country="US",kind="replay",checked_at="2026-10-20T17:00:00Z")]
    matches[7].rule = build.usual_home("usa.1", matches[7].home.id)
    return matches


def replay(context):
    value = json.loads(context.read_text())
    matches = []
    for original in value["matches"]:
        record = dict(original)
        record["utc"] = datetime.fromisoformat(record["utc"])
        for side in ("home","away"):
            record[side] = build.Team(**record[side])
        record["outlets"] = [build.Outlet(**o) for o in record["outlets"]]
        record["rule"] = build.Outlet(**record["rule"]) if record["rule"] else None
        matches.append(build.Match(**record))
    return value, matches


def scopes(matches, instant):
    # Deterministic test input, not inferred historical proof of successful GETs.
    return dict(complete=True, source_scopes=[dict(source="espn",league_id=league,
        date_zone="America/New_York", requested_dates=dates,covered_dates=dates,complete=True,
        attempted_at=exporter.instant(instant),validated_at=exporter.instant(instant))
        for league, dates in ((league, sorted({m.utc.astimezone(build.ET).date().isoformat()
                                               for m in matches if m.league == league}))
                              for league in sorted({m.league for m in matches}))])


def editorial_inputs(output):
    """Fixed research times deliberately older than this test's publication."""
    binding = dict(id="scheduled", league_id="eng.1", kickoff_utc="2026-10-20T19:00:00Z")
    original = dict(generated_at="2026-10-20T12:00:00Z", prompted_at="2026-10-20T11:59:00Z",
                    expires_at="2026-10-21T12:00:00Z", fixture_bindings=[binding])
    automatic = dict(generated_at="2026-10-20T17:00:00Z", updated_at="2026-10-20T17:30:00Z",
        focus_until="2026-10-21T17:00:00Z", model="synthetic-offline-model", effort="test",
        score_calibration="synthetic-aggregation-1",
        rating_weights=dict(popularity=.5, gameplay=.35, impact=.15),
        rankings={"scheduled": dict(popularity=80, gameplay=60, impact=70, score=71.5,
                                     companion_origin=original)},
        league_blurbs=[dict(league_id="eng.1", match_ids=["scheduled"],
            headline="Synthetic bound league headline", text="Offline review text.", interest=70,
            sources=[dict(title="Synthetic source", url="https://example.org/synthetic-match")],
            companion_origin=original)])
    shared = dict(version=1, created_at=1792515600000, weight=70,
        fixtures=[dict(id="scheduled", league_id="eng.1", kickoff="2026-10-20T19:00:00Z")],
        response=dict(version=2, request_id="synthetic-shared-request", headlines=[],
            matches=[dict(id="scheduled", score=75, blurb="Synthetic shared preview.", sources=[])]))
    paths = {}
    for kind, value in (("automatic", automatic), ("shared", shared)):
        path = output / f"synthetic-{kind}.json"
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        paths[kind + "_path"] = path
    return paths


def check_editorial(artifacts):
    """Expected original times are literals, not derived from exporter output."""
    automatic = artifacts["editorial"]["automatic"]
    shared = artifacts["editorial"]["shared"]
    assert automatic["generated_at"] == "2026-10-20T17:00:00Z"
    assert automatic["published_at"] == "2026-10-20T17:30:00Z"
    assert automatic["expires_at"] == "2026-10-21T17:00:00Z"
    assert len(automatic["matches"]) == 1 and len(automatic["headlines"]) == 1
    for item in automatic["matches"] + automatic["headlines"]:
        assert item["fixture_refs"] == [{"fixture_id": "espn:fixture:scheduled", "kickoff": "2026-10-20T19:00:00Z"}]
        assert item["expires_at"] == "2026-10-21T12:00:00Z"
        assert item["provenance"] == dict(generated_at="2026-10-20T12:00:00Z",
            prompted_at="2026-10-20T11:59:00Z", expires_at="2026-10-21T12:00:00Z")
    assert shared["prompted_at"] == "2026-10-20T17:00:00Z"
    assert shared["published_at"] is None
    assert shared["expires_at"] == "2026-10-21T17:00:00Z"
    assert len(shared["matches"]) == 1
    assert shared["matches"][0]["expires_at"] == "2026-10-21T17:00:00Z"


def read(dart, directory, *, valid=True):
    process = subprocess.run([dart,"--suppress-analytics","run",
        str(ROOT/"tool/companion_feed_reader/bin/read.dart"),"--api-dir",str(directory),
        "--schema-dir",str(ROOT/"contracts/outlook/v1")],cwd=ROOT,text=True,capture_output=True,timeout=90)
    expected = 0 if valid else 65
    if process.returncode != expected:
        raise AssertionError(f"Reader exited {process.returncode}, expected {expected}: {process.stderr}")
    return json.loads(process.stdout)


def check_projection(matches, artifacts, parsed):
    assert parsed["fixture_ids"] == ["espn:fixture:"+str(m.id) for m in matches]
    assert parsed["source_scopes"] == artifacts["schedule"]["source_scopes"]
    for source, actual in zip(matches, parsed["fixtures"]):
        assert actual["home_team_id"] == "espn:team:" + str(source.home.id)
        assert actual["away_team_id"] == "espn:team:" + str(source.away.id)
        assert actual["timing"]["kickoff"] == (exporter.instant(source.utc) if source.time_valid else None)
        assert actual["status"]["observation"]["observed_at"] is None
        assert actual["outlook"]["total"] == build.outlook_score(build.outlook_parts(source))
        assert actual["outlook"]["components"] == build.outlook_parts(source)
        # Separate source facts from selection: every verified route retains all
        # canonical service-plan possibilities, irrespective of OWNER defaults.
        outlets = [*source.outlets,*([source.rule] if source.rule else [])]
        by_network = {exporter.NETWORK_IDS[o.label]:o for o in outlets if o.known}
        for route in actual["routes"]:
            outlet = by_network[route["network_id"]]
            if route["basis"] != "unknown":
                assert route["eligible_service_plan_ids"] == list(outlet.via)
            else:
                assert route["eligible_service_plan_ids"] == []


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=Path("work/companion-p1"))
    parser.add_argument("--dart",default="dart")
    parser.add_argument("--context",type=Path,help="optional historical public schedule-context.json; scope is synthetic")
    args=parser.parse_args()
    output=args.out.resolve()
    output.mkdir(parents=True,exist_ok=True)
    directory=output/"sample"/"api"/"v1"
    if directory.exists():
        raise SystemExit("Use a fresh --out directory to preserve previous evidence.")
    build.TODAY = NOW.astimezone(build.ET).date()
    revision=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    matches=sample()
    required=exporter.export_artifacts(build,matches,NOW,scopes(matches,NOW),standings_at=NOW-timedelta(hours=2))
    editorial_paths=editorial_inputs(output)
    artifacts=exporter.add_editorial(required,published_at=NOW,**editorial_paths)
    check_editorial(artifacts)
    manifest=publisher.publish(directory,artifacts,published_at=NOW,producer_revision=revision)
    check_editorial(validate_publication(directory)[1])
    result=read(args.dart,directory)
    assert result["editorial"] == {"state":"accepted","error":None}
    check_projection(matches,artifacts,result)
    by_id={f["id"]:f for f in result["fixtures"]}
    assert by_id["espn:fixture:live"]["status"]["scores"]["match"] == {"home":0,"away":None}
    assert by_id["espn:fixture:scheduled"]["status"]["scores"]["match"] == {"home":None,"away":None}
    assert by_id["espn:fixture:replay"]["routes"][0]["eligible_service_plan_ids"] == []
    assert "yttv" not in by_id["espn:fixture:excluded"]["routes"][0]["eligible_service_plan_ids"]
    # Reuse facts in a higher revision without changing observation content.
    before={d["path"]:(directory/d["path"]).read_bytes() for d in manifest["artifacts"].values() if d}
    later=exporter.add_editorial(required,published_at=NOW+timedelta(minutes=30),**editorial_paths)
    check_editorial(later)
    assert later == artifacts
    refreshed=publisher.publish(directory,later,published_at=NOW+timedelta(minutes=30),producer_revision=revision)
    assert refreshed["publication_revision"] == manifest["publication_revision"]+1
    assert all((directory/name).read_bytes()==raw for name,raw in before.items())
    accepted=read(args.dart,directory)
    assert accepted["publication_revision"] == 2
    assert accepted["editorial"] == {"state":"accepted","error":None}
    broken=output/"corrupt-required"
    shutil.copytree(directory,broken)
    (broken/refreshed["artifacts"]["schedule"]["path"]).write_bytes(b"{}\n")
    assert read(args.dart,broken,valid=False)["error"] == "invalid_publication"
    optional=output/"corrupt-optional"
    shutil.copytree(directory,optional)
    (optional/refreshed["artifacts"]["editorial"]["path"]).write_bytes(b"{}\n")
    recovered=read(args.dart,optional)
    assert recovered["editorial"] == {"state":"invalid","error":"artifact_integrity"}
    assert {k:v for k,v in recovered.items() if k!="editorial"} == {k:v for k,v in accepted.items() if k!="editorial"}
    check_projection(matches,artifacts,recovered)
    report=dict(status="P1 local producer/independent parser checkpoint; no deployment",
        source_revision=revision,sample_fixture_count=len(matches),publication_revisions=[1,2],
        source_times_preserved=True,required_corruption_rejected=True,
        optional_corruption_isolated=True,
        editorial=dict(automatic_accepted=True,shared_accepted=True,
            original_item_generation="2026-10-20T12:00:00Z",original_item_expiry="2026-10-21T12:00:00Z",
            original_shared_prompt="2026-10-20T17:00:00Z",original_shared_expiry="2026-10-21T17:00:00Z",
            unknown_shared_publication_time=None,republished_editorial_bytes_unchanged=True,
            evidence="Exact original times checked in exported and published artifact; independent Dart reader accepted its integrity-checked descriptor before and after republish."),
        sample_artifact_bytes={k:d["bytes"] for k,d in refreshed["artifacts"].items() if d},
        coverage_note="Synthetic tests supply explicit complete request scopes; no network collection was run")
    if args.context:
        context,captured=replay(args.context)
        build.STANDINGS=context["standings"]
        build.LEAGUE_LOGOS=context["league_logos"]
        when=datetime.fromisoformat(context["generated_at"])
        projected=exporter.export_artifacts(build,captured,when,scopes(captured,when),standings_at=context["standings_at"])
        target=output/"historical-projection"/"api"/"v1"
        recorded=publisher.publish(target,projected,published_at=when,producer_revision=revision)
        check_projection(captured,projected,read(args.dart,target))
        report["historical_projection"]=dict(fixtures=len(captured),source_sha256=hashlib.sha256(args.context.read_bytes()).hexdigest(),
            bytes={k:d["bytes"] for k,d in recorded["artifacts"].items() if d},
            limitation="Captured Match facts only. Explicit synthetic per-league date scopes; not proof of original request coverage, renderer equivalence, or a deployable historical feed.")
    paths=sorted([*ROOT.glob("companion_*.py"),*ROOT.glob("contracts/outlook/**/*.json"),
                  *ROOT.glob("packages/outlook_data/lib/**/*.dart"),*ROOT.glob("tool/companion_feed_reader/**/*.dart"),
                  *(ROOT/name for name in ("build.py","story.py","story_state.py","rights.toml","settings.toml",
                     "watch_links.py","watch-links.toml","tests/test_discovery.py","companion-toolchain.json",
                     "packages/outlook_data/pubspec.yaml","packages/outlook_data/pubspec.lock",
                     "tool/companion_feed_reader/pubspec.yaml","tool/companion_feed_reader/pubspec.lock")),
                  Path(__file__).resolve()])
    paths=[path for path in paths if ".dart_tool" not in path.relative_to(ROOT).parts]
    report["source_hashes"]={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="source_hashes"},indent=2))
    print(f"PASS: producer/reader round trip; evidence in {output}")


if __name__ == "__main__":
    main()
