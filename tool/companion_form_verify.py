#!/usr/bin/env python3
"""Bounded ESPN form evidence; default replay is offline and independent of build.py.

Capture fetches three scoreboards and eight summaries, retaining raw responses
only in the requested work directory. The minimized evidence contains numerical
facts and source receipts, never treats an HTTP date as a source observation,
and deliberately preserves contradictions instead of selecting agreeing teams.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/contracts/form/espn-2026-10-20.json"
CASES = {
    "eng.1": ("401879256", "401878768", "401879255", "401878767"),
    "usa.1": ("761903", "761890"),
    "mex.1": ("401876926", "401876925"),
}
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parsed(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Evidence time must include its zone")
    return result


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url, path):
    attempted = stamp()
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Evidence response exceeds bounded size")
        receipt = dict(url=url, attempted_at=attempted, received_at=stamp(),
                       source_observed_at=None, status=response.status,
                       response_date=response.headers.get("Date"),
                       sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                       raw_file=path.name)
    value = json.loads(raw)
    path.write_bytes(raw)
    write(path.with_suffix(".receipt.json"), receipt)
    return value, receipt


def capture(output):
    # No overwrite: repeat captures belong to a new directory, with new receipts.
    output.mkdir(parents=True, exist_ok=False)
    evidence = dict(version=1, captured_at=stamp(), source_observed_at=None,
        scope="Sixteen team observations from eight fixtures in three leagues; not universal provider validation.",
        requests=[], cases=[])
    for league, event_ids in CASES.items():
        board, receipt = fetch(f"{BASE}/{league}/scoreboard?dates=20261024&limit=1000",
                               output / f"{league}-scoreboard.json")
        evidence["requests"].append(receipt)
        events = {str(event["id"]): event for event in board["events"]}
        for event_id in event_ids:
            original = events[event_id]
            summary, detail_receipt = fetch(f"{BASE}/{league}/summary?event={event_id}",
                                            output / f"{league}-{event_id}.json")
            evidence["requests"].append(detail_receipt)
            header = {str(item["team"]["id"]): item for item in summary["header"]["competitions"][0]["competitors"]}
            histories = {str(item["team"]["id"]): item for item in summary["lastFiveGames"]}
            for competitor in original["competitions"][0]["competitors"]:
                team_id = str(competitor["team"]["id"])
                results = [{key: event.get(key) for key in (
                    "id", "gameDate", "homeTeamId", "awayTeamId", "homeTeamScore", "awayTeamScore",
                    "homeAggregateScore", "awayAggregateScore", "homeShootoutScore", "awayShootoutScore",
                    "gameResult", "leagueName", "competitionName")}
                    for event in histories[team_id]["events"]]
                evidence["cases"].append(dict(
                    league_id=league, fixture_id=event_id, fixture_date=original["date"],
                    team_id=team_id, team_name=competitor["team"]["displayName"],
                    raw_form=competitor.get("form"), summary_form=header[team_id].get("form"),
                    scoreboard_url=receipt["url"], summary_url=detail_receipt["url"],
                    results=results))
    evidence["completed_at"] = stamp()
    write(output / "evidence.json", evidence)
    return evidence


def score_result(event, team_id):
    """Derive the result from score fields, not ESPN's form or gameResult string.

    Shootouts and aggregate ties need a separate semantic decision; do not
    silently turn their progression winner into a match result in this probe.
    """
    if str(team_id) not in (event["homeTeamId"], event["awayTeamId"]):
        raise ValueError("Dated result does not involve the claimed team")
    for name in ("homeAggregateScore", "awayAggregateScore", "homeShootoutScore", "awayShootoutScore"):
        if event.get(name) not in (None, "", "0", 0):
            raise ValueError("Aggregate and shootout result semantics are outside this evidence")
    if any(not re.fullmatch(r"[0-9]+", str(event[key])) for key in ("homeTeamScore", "awayTeamScore")):
        raise ValueError("Dated result lacks a numeric match score")
    home, away = int(event["homeTeamScore"]), int(event["awayTeamScore"])
    ours, theirs = (home, away) if team_id == event["homeTeamId"] else (away, home)
    return "W" if ours > theirs else "L" if ours < theirs else "D"


def verify(evidence):
    if evidence.get("version") != 1 or evidence.get("source_observed_at") is not None:
        raise ValueError("Unsupported evidence or invented source observation")
    requests = {item["url"]: item for item in evidence["requests"]}
    cases = []
    for case in evidence["cases"]:
        receipt = requests[case["summary_url"]]
        if receipt["source_observed_at"] is not None or receipt["status"] != 200:
            raise ValueError("Evidence receipt is not a successful unknown-age source response")
        if parsed(receipt["attempted_at"]) > parsed(receipt["received_at"]):
            raise ValueError("Evidence receipt precedes its attempt")
        if case["raw_form"] != case["summary_form"]:
            raise ValueError("Scoreboard and summary disagree within this capture")
        if not re.fullmatch(r"[WDL]{5}", case["raw_form"]):
            raise ValueError("This evidence needs exactly five ordinary result codes")
        events = sorted(case["results"], key=lambda event: parsed(event["gameDate"]))
        if len(events) != 5 or len({event["id"] for event in events}) != 5:
            raise ValueError("This evidence needs five distinct dated results")
        cutoff = min(parsed(receipt["received_at"]), parsed(case["fixture_date"]))
        if any(parsed(event["gameDate"]) >= cutoff for event in events):
            raise ValueError("A supposed previous result is in the future")
        results = [score_result(event, case["team_id"]) for event in events]
        if results != [event["gameResult"] for event in events]:
            raise ValueError("Independent numerical scores disagree with reported results")
        chronological = "".join(results)
        mismatches = [index for index, (source, result) in enumerate(zip(case["raw_form"], chronological[::-1]))
                      if source != result]
        same, reverse = case["raw_form"] == chronological, case["raw_form"] == chronological[::-1]
        classification = "ambiguous_palindrome" if same and reverse else "oldest_first" if same else "newest_first" if reverse else "inconsistent"
        cases.append(dict(league_id=case["league_id"], fixture_id=case["fixture_id"],
            team_id=case["team_id"], team_name=case["team_name"], raw_form=case["raw_form"],
            dated_results_oldest_first=chronological, classification=classification,
            mismatched_indexes_if_newest_first=mismatches,
            oldest_result=events[0]["gameDate"], newest_result=events[-1]["gameDate"],
            competitions=sorted({event["competitionName"] for event in events})))
    counts = {kind: sum(case["classification"] == kind for case in cases)
              for kind in ("oldest_first", "newest_first", "ambiguous_palindrome", "inconsistent")}
    return dict(version=1, team_observations=len(cases), source_requests=len(requests), counts=counts,
        partial_alignment=dict(
            oldest_four_agree_if_newest_first=sum(all(index == 0 for index in case["mismatched_indexes_if_newest_first"]) for case in cases),
            only_newest_result_disagrees=sum(case["mismatched_indexes_if_newest_first"] == [0] for case in cases)),
        conclusion="Observed agreements support newest-first, but contradictory source values prevent an unqualified normalized last-five history.",
        cases=cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=FIXTURE, help="offline minimized evidence")
    parser.add_argument("--capture", type=Path, help="explicit network capture into a new directory")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    evidence = capture(args.capture) if args.capture else json.loads(args.evidence.read_text())
    report = verify(evidence)
    if args.report:
        write(args.report, report)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
