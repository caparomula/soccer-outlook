#!/usr/bin/env python3
"""Export frozen pre-match scores and later outcomes from a cloned history branch.

    python3 history_report.py --archive ../soccer-outlook-history --out work/history.csv

Only archived observations are read. Audience measurements with different scopes stay
in the source archive; this report does not pool them or calculate correlations.
"""
import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median
import sys

from history import iso, read_json, timestamp, validate_metrics


PARTS = ("stature", "close", "stakes", "tv", "goals")
DIMENSIONS = ("popularity", "gameplay", "impact")
FIELDS = (
    "league_id", "competition", "match_id", "kickoff_utc", "home_id", "home", "away_id", "away", "venue",
    "latest_observed_at", "state", "status", "forecast_observed_at", "forecast_captured_at", "forecast_snapshot",
    "source_revision", "outlook_calibration", "outlook_weights", "outlook_score", *("outlook_" + key for key in PARTS),
    "raw_stature", "draw_probability", "home_win_probability", "away_win_probability", "win_balance", "goal_line", "missing_components",
    *("ai_" + key for key in DIMENSIONS), "ai_score", "ai_model", "ai_requested_model", "ai_effort",
    "ai_bundle_generated_at", "ai_forecast_observed_at", "ai_forecast_captured_at", "ai_source_revision",
    "ai_score_calibration", "ai_rating_weights",
    "automatic_interest", "automatic_pick", "default_league_priority", "blend_outlook_score", "blend_ai_score",
    "blend_forecast_observed_at", "blend_forecast_captured_at", "blend_source_revision",
    "result_observed_at", "home_score", "away_score", "total_goals", "attendance", "attendance_observed_at",
    "attendance_source_url", "attendance_scope", "stadium_capacity", "capacity_source_url", "capacity_scope",
    "stadium_occupancy", "baseline_count", "baseline_median_attendance", "attendance_vs_baseline",
)


def count(value, *, positive=False):
    """An integer measurement, retaining a real zero but excluding placeholders when requested."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number < (1 if positive else 0):
        return None
    return int(number)


def venue_key(match):
    venue = match.get("venue_details") or {}
    identifier = venue.get("id")
    if isinstance(identifier, (str, int)) and not isinstance(identifier, bool) and str(identifier).strip():
        return "id:" + str(identifier).strip()
    name = venue.get("fullName") or match.get("venue")
    return "name:" + " ".join(name.casefold().split()) if isinstance(name, str) and name.strip() else None


def read_archive(path):
    root = Path(path)
    snapshots = root / "snapshots"
    if not snapshots.is_dir():
        raise ValueError("archive must contain a snapshots directory")
    paths = sorted(path for path in snapshots.rglob("*") if path.is_file() and path.name.endswith((".json", ".json.gz")))
    if not paths:
        raise ValueError("archive contains no snapshots")
    for source in paths:
        try:
            value = read_json(source)
            validate_metrics(value)
            timestamp(value.get("captured_at"))
        except (ValueError, TypeError, OSError, UnicodeError) as error:
            raise ValueError(f"invalid archive snapshot {source}: {error}") from error
        value["_archive_file"] = source.relative_to(root).as_posix()
        yield value


def observations(snapshots):
    grouped = {}
    unique, extra = {}, []
    for snapshot in snapshots:
        fingerprint = snapshot.get("input_sha256")
        if not fingerprint:
            extra.append(snapshot)
        elif fingerprint not in unique or timestamp(snapshot["captured_at"]) < timestamp(unique[fingerprint]["captured_at"]):
            unique[fingerprint] = snapshot
    for snapshot in [*unique.values(), *extra]:
        observed, captured = timestamp(snapshot["observed_at"]), timestamp(snapshot["captured_at"])
        if observed > captured:
            raise ValueError("archive observation follows its capture time")
        for match in snapshot["matches"]:
            entry = {"match": match, "snapshot": snapshot, "observed": observed, "captured": captured,
                     "kickoff": timestamp(match["kickoff_utc"])}
            grouped.setdefault((match["league_id"], match["id"]), []).append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda entry: (entry["observed"], entry["captured"],
                                       entry["snapshot"].get("_archive_file", "")))
    return grouped


def external_measurements(snapshots, grouped):
    """Keep distinct sourced figures and their first archive capture; do not sum audiences."""
    by_id, result, seen = {}, {}, {}
    for key in grouped:
        by_id.setdefault(key[1], []).append(key)
    for snapshot in snapshots:
        captured = timestamp(snapshot["captured_at"])
        for item in snapshot.get("external_observations", []):
            if item.get("metric") not in ("attendance", "stadium_capacity"):
                continue
            keys = [(item["league_id"], item["match_id"])] if item.get("league_id") else by_id.get(item["match_id"], [])
            if len(keys) != 1 or keys[0] not in grouped:
                continue
            fingerprint = json.dumps(item, sort_keys=True)
            if fingerprint in seen:
                seen[fingerprint]["captured"] = min(seen[fingerprint]["captured"], captured)
                continue
            entry = {"measurement": item, "observed": timestamp(item["observed_at"]), "captured": captured}
            seen[fingerprint] = entry
            result.setdefault(keys[0], []).append(entry)
    return result


def final_outcome(entries, external=(), *, before=None):
    """Latest known fixture identity, preserving earlier reported values when later data is absent.

    The cutoff is also applied to historical outcomes used in an attendance baseline:
    a result first collected after the target kickoff cannot enter that baseline.
    """
    visible = [entry for entry in entries if before is None or (entry["observed"] < before and entry["captured"] < before)]
    if not visible:
        return None
    latest = visible[-1]
    match, kickoff = latest["match"], latest["kickoff"]
    current = [entry for entry in visible if entry["kickoff"] == kickoff]
    complete = [entry for entry in current if entry["match"]["state"] == "post"
                and entry["match"].get("observed", {}).get("score_is_final") is True
                and entry["observed"] >= kickoff]
    # A subsequent cancellation or corrected pre-match state supersedes an old final.
    if match["state"] != "post" or match.get("observed", {}).get("score_is_final") is not True:
        complete = []
    home = away = result_at = None
    attendances, capacities = [], []
    for entry in complete:
        facts = entry["match"]["observed"]
        candidate = count(facts.get("attendance"), positive=True)
        if candidate is not None:
            attendances.append((entry["observed"], 0, candidate, entry["match"].get("source_url"), "ESPN reported match attendance"))
        home_value, away_value = count(facts.get("home_score")), count(facts.get("away_score"))
        if home_value is not None and away_value is not None:
            home, away, result_at = home_value, away_value, entry["observed"]
    for entry in current:
        if venue_key(entry["match"]) != venue_key(match):
            continue
        candidate = count(entry["match"].get("observed", {}).get("stadium_capacity"), positive=True)
        if candidate is None:
            candidate = count((entry["match"].get("venue_details") or {}).get("capacity"), positive=True)
        if candidate is not None:
            capacities.append((entry["observed"], 0, candidate, entry["match"].get("source_url"), "ESPN venue capacity"))
    for entry in external:
        if before is not None and (entry["observed"] >= before or entry["captured"] >= before):
            continue
        item = entry["measurement"]
        candidate = count(item["value"], positive=item["metric"] == "stadium_capacity")
        if candidate is None:
            continue
        if item["metric"] == "attendance" and (not complete or entry["observed"] < kickoff):
            continue
        values = attendances if item["metric"] == "attendance" else capacities
        values.append((entry["observed"], 1, candidate, item["source_url"], item["scope"]))
    attendance = max(attendances, key=lambda item: item[:2]) if attendances else None
    capacity = max(capacities, key=lambda item: item[:2]) if capacities else None
    return {"latest": latest, "kickoff": kickoff, "complete": bool(complete), "attendance": attendance[2] if attendance else None,
            "attendance_at": attendance[0] if attendance else None,
            "attendance_source_url": attendance[3] if attendance else None, "attendance_scope": attendance[4] if attendance else None,
            "capacity": capacity[2] if capacity else None,
            "capacity_source_url": capacity[3] if capacity else None, "capacity_scope": capacity[4] if capacity else None,
            "home_score": home, "away_score": away,
            "result_at": result_at}


def prior_attendance(grouped, venue_index, external, target_key, target):
    """Available prior home attendances in this competition and venue, never future data."""
    latest, kickoff = target["latest"], target["kickoff"]
    match = latest["match"]
    home_id, venue = (match.get("home") or {}).get("id"), venue_key(match)
    if not home_id or not venue:
        return []
    values = []
    for key in venue_index.get((target_key[0], home_id, venue), ()):
        if key == target_key:
            continue
        entries = grouped[key]
        previous = final_outcome(entries, external.get(key, ()), before=kickoff)
        if previous is None or previous["kickoff"] >= kickoff or previous["attendance"] is None:
            continue
        earlier = previous["latest"]["match"]
        if (earlier.get("home") or {}).get("id") == home_id and venue_key(earlier) == venue:
            values.append(previous["attendance"])
    return values


def automatic(entry):
    return (entry["match"].get("ai") or {}).get("automatic") or {}


def report_rows(snapshots):
    snapshots = list(snapshots)
    grouped = observations(snapshots)
    external = external_measurements(snapshots, grouped)
    venue_index = {}
    for key, entries in grouped.items():
        for entry in entries:
            match = entry["match"]
            venue_index.setdefault((key[0], (match.get("home") or {}).get("id"), venue_key(match)), set()).add(key)
    rows = []
    for key, entries in grouped.items():
        outcome = final_outcome(entries, external.get(key, ()))
        latest, kickoff = outcome["latest"], outcome["kickoff"]
        match = latest["match"]
        home, away = match.get("home") or {}, match.get("away") or {}
        row = dict.fromkeys(FIELDS)
        row.update(league_id=key[0], match_id=key[1], competition=match.get("competition"), kickoff_utc=iso(kickoff),
                   home_id=home.get("id"), home=home.get("name"), away_id=away.get("id"), away=away.get("name"),
                   venue=match.get("venue"), latest_observed_at=iso(latest["observed"]),
                   state=match["state"], status=match.get("status"))
        candidates = [entry for entry in entries if entry["kickoff"] == kickoff and entry["observed"] < kickoff
                      and entry["match"].get("forecast_eligible") is True
                      and entry["match"]["state"] == "pre" and entry["match"].get("time_confirmed") is True]
        if candidates:
            forecast = candidates[-1]
            prediction, source = forecast["match"], forecast["snapshot"]
            row.update(forecast_observed_at=iso(forecast["observed"]), forecast_captured_at=iso(forecast["captured"]),
                       forecast_snapshot=source.get("_archive_file"), source_revision=source.get("source_revision"),
                       outlook_calibration=source["settings"].get("outlook_calibration"),
                       outlook_weights=json.dumps(source["settings"]["outlook"]["weights"], sort_keys=True)
                       if source["settings"]["outlook"].get("weights") else None,
                       outlook_score=prediction["outlook"]["score"])
            row.update({"outlook_" + part: prediction["outlook"]["parts"].get(part) for part in PARTS})
            inputs = prediction["outlook"].get("inputs") or {}
            row.update(raw_stature=inputs.get("stature"), draw_probability=inputs.get("draw_probability"),
                       home_win_probability=inputs.get("home_win_probability"), away_win_probability=inputs.get("away_win_probability"),
                       win_balance=inputs.get("win_balance"),
                       goal_line=inputs.get("goal_line"), missing_components=";".join(prediction["outlook"].get("missing_components", [])))
            safe_ai_entries = [entry for entry in candidates if entry["captured"] < kickoff
                               and automatic(entry).get("eligible_at_capture") is True]
            if safe_ai_entries:
                ai_entry = safe_ai_entries[-1]
                ai = ai_entry["match"]["ai"]["automatic"]
                row.update({"ai_" + dimension: ai.get(dimension) for dimension in DIMENSIONS})
                meta = (ai_entry["snapshot"].get("ai_sources") or {}).get("automatic") or {}
                row.update(ai_score=ai.get("score"), ai_model=meta.get("model"), ai_requested_model=meta.get("requested_model"),
                           ai_effort=meta.get("effort"), ai_bundle_generated_at=meta.get("generated_at"),
                           ai_score_calibration=meta.get("score_calibration"),
                           ai_rating_weights=json.dumps(meta["rating_weights"], sort_keys=True) if meta.get("rating_weights") else None,
                           ai_forecast_observed_at=iso(ai_entry["observed"]), ai_forecast_captured_at=iso(ai_entry["captured"]),
                           ai_source_revision=ai_entry["snapshot"].get("source_revision"))
            safe_blends = [entry for entry in candidates if (entry["match"].get("automatic_default") or {}).get("forecast_eligible") is True
                           and ((entry["match"].get("automatic_default") or {}).get("ai_used") is not True or entry in safe_ai_entries)]
            if safe_blends:
                blend_entry = safe_blends[-1]
                blend = blend_entry["match"]["automatic_default"]
                row.update(automatic_interest=blend.get("interest"), automatic_pick=blend.get("pick"),
                           default_league_priority=blend.get("league_priority"), blend_outlook_score=blend_entry["match"]["outlook"]["score"],
                           blend_ai_score=blend_entry["match"]["ai"]["automatic"]["score"] if blend.get("ai_used") else None,
                           blend_forecast_observed_at=iso(blend_entry["observed"]), blend_forecast_captured_at=iso(blend_entry["captured"]),
                           blend_source_revision=blend_entry["snapshot"].get("source_revision"))
        row.update(home_score=outcome["home_score"], away_score=outcome["away_score"], attendance=outcome["attendance"],
                   stadium_capacity=outcome["capacity"],
                   attendance_source_url=outcome["attendance_source_url"], attendance_scope=outcome["attendance_scope"],
                   capacity_source_url=outcome["capacity_source_url"], capacity_scope=outcome["capacity_scope"],
                   result_observed_at=iso(outcome["result_at"]) if outcome["result_at"] else None,
                   attendance_observed_at=iso(outcome["attendance_at"]) if outcome["attendance_at"] else None)
        if row["home_score"] is not None and row["away_score"] is not None:
            row["total_goals"] = row["home_score"] + row["away_score"]
        if row["attendance"] is not None and row["stadium_capacity"] is not None:
            row["stadium_occupancy"] = row["attendance"] / row["stadium_capacity"]
        baseline = prior_attendance(grouped, venue_index, external, key, outcome)
        row["baseline_count"] = len(baseline)
        if baseline:
            row["baseline_median_attendance"] = median(baseline)
        if len(baseline) >= 3 and row["attendance"] is not None and row["baseline_median_attendance"] > 0:
            row["attendance_vs_baseline"] = row["attendance"] / row["baseline_median_attendance"]
        rows.append(row)
    return sorted(rows, key=lambda row: (row["kickoff_utc"], row["league_id"], row["match_id"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path, help="clone of the match-history branch")
    parser.add_argument("--out", required=True, type=Path, help="destination CSV; missing values are empty cells")
    args = parser.parse_args(argv)
    try:
        rows = report_rows(read_archive(args.archive))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Exported {len(rows)} matches to {args.out}")
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        print(f"History report failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
