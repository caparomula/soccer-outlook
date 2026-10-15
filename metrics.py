"""Capture the facts behind each score, independently of the page's filters and AI prompt.

This module makes no network or AI requests. build.py supplies its parsed matches and the
scoreboard events it actually fetched. history.py later adds the available AI observations.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def json_value(value):
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def source_version(root):
    revision = os.environ.get("GITHUB_SHA") or None
    if revision is None:
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
            if result.returncode == 0:
                revision = result.stdout.strip()
        except OSError:
            pass
    # Fingerprints also identify locally edited code whose HEAD alone would be misleading.
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
             for name in ("build.py", "metrics.py", "history.py", "story.py", "settings.toml", "rights.toml")
             if (root / name).is_file()}
    return revision, files


def snapshot(api, matches, raw_events, observed_at, *, build_started_at=None, source_revision=None,
             source_files=None, source_report=None):
    if observed_at.tzinfo is None:
        raise ValueError("Metric observation time must include a timezone")
    observed_at = observed_at.astimezone(timezone.utc)

    def team(value):
        return {key: item for key, item in asdict(value).items()
                if key not in ("logo_key", "logo_url", "color", "alt")}

    records = []
    for match in sorted(matches, key=lambda m: (m.utc, m.league, m.id)):
        event = raw_events.get(match.league, {}).get(match.id, {})
        comp = (api.dicts(event.get("competitions")) or [{}])[0]
        raw_status = event.get("status") or comp.get("status") or {}
        status_type = raw_status.get("type") or {}
        void = match.status.lower() in api.VOID_STATUSES or any(
            str(status_type.get(key, "")).lower() in api.VOID_STATUSES
            for key in ("description", "shortDetail", "detail"))
        parts = api.outlook_parts(match)
        attendance_raw = comp.get("attendance")
        # ESPN's absent/zero placeholders do not establish an empty stadium.
        count = api.finite_number(attendance_raw)
        attendance = int(count) if count is not None and count > 0 and count.is_integer() else None
        venue = comp.get("venue") or event.get("venue") or {}
        capacity_raw = venue.get("capacity")
        capacity_value = api.finite_number(capacity_raw)
        capacity = int(capacity_value) if capacity_value is not None and capacity_value > 0 and capacity_value.is_integer() else None
        competitors = {item.get("homeAway"): item for item in api.dicts(comp.get("competitors"))}
        raw_scores = {side: competitors.get(side, {}).get("score") for side in ("home", "away")}
        def actual_score(side):
            value = api.finite_number(raw_scores[side])
            if match.state not in ("in", "post") or void:
                return None
            return int(value) if value is not None and value >= 0 and value.is_integer() else None
        record = {
            "id": match.id, "league_id": match.league, "competition": match.comp,
            "kickoff_utc": match.utc.isoformat(), "time_confirmed": match.time_valid,
            "state": match.state, "status": match.status, "status_raw": raw_status,
            "forecast_eligible": bool(match.time_valid and match.state == "pre"
                                      and match.utc > observed_at and not void),
            "home": team(match.home), "away": team(match.away), "stage": match.stage,
            "note": match.note, "venue": match.venue,
            "venue_details": venue,
            "source_url": f"https://www.espn.com/soccer/match/_/gameId/{match.id}",
            "coverage": {
                "listed": [asdict(outlet) for outlet in match.outlets],
                "usual": asdict(match.rule) if match.rule else None,
                "default_service": match.service, "default_basis": match.basis,
                "default_outlet": match.outlet, "default_competition": api.shown_by_default(match),
                "source": (match.broadcast_source or "ESPN") if match.outlets else None,
                "source_url": match.broadcast_url or None,
                "check": match.broadcast_check or None,
                "check_source": match.broadcast_check_source or None,
                "check_url": match.broadcast_check_url or None,
                "checked_at": match.broadcast_checked_at or None,
                "checks": match.broadcast_checks,
                "evidence": match.broadcast_evidence,
                "espn_listings": comp.get("geoBroadcasts") or [],
            },
            "outlook": {
                "score": api.outlook_score(parts), "parts": parts,
                "missing_components": [name for name, value in parts.items() if value is None],
                "inputs": {
                    "stature": match.score, "competition_tier": api.LEAGUES[match.league]["tier"],
                    "draw_probability": match.draw, "goal_line": match.goal_line,
                    "odds": (api.dicts(comp.get("odds")) or [None])[0],
                },
            },
            "observed": {
                "attendance": attendance, "attendance_raw": attendance_raw,
                "attendance_status": "reported" if attendance is not None else "not_reported",
                "stadium_capacity": capacity, "capacity_raw": capacity_raw,
                "occupancy": attendance / capacity if attendance is not None and capacity is not None else None,
                "viewership": None, "viewership_status": "no_data_source",
                "home_score": actual_score("home"), "away_score": actual_score("away"), "scores_raw": raw_scores,
                "score_is_final": match.state == "post" and not void,
                "goals": match.goals,
            },
        }
        records.append(record)
    config = asdict(api.SETTINGS)
    return json_value({
        "version": 1, "observed_at": observed_at.isoformat(),
        "build_started_at": build_started_at.isoformat() if build_started_at else None,
        "source_revision": source_revision, "source_files": source_files or {},
        "source_report": source_report,
        "settings": {
            "blend": config["blend"], "outlook": config["outlook"],
            "dots": {"scale": 6, "score_max": 100, "rounding": "nearest_half_up"},
            "ai": {"enabled": config["ai"], **{name: config[name] for name in ("design", "model", "effort", "overview_model",
                                                    "overview_effort", "blurbs_model", "blurbs_effort")}},
        },
        "league_order": list(api.LEAGUES), "default_services": list(api.OWNER),
        "default_leagues": [league for league, info in api.LEAGUES.items() if not info.get("default_off")],
        "matches": records,
    })


def write(path, api, matches, raw_events, observed_at, *, build_started_at=None, source_report=None):
    revision, files = source_version(Path(api.__file__).resolve().parent)
    value = snapshot(api, matches, raw_events, observed_at, build_started_at=build_started_at,
                     source_revision=revision, source_files=files, source_report=source_report)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
