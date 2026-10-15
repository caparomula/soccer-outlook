#!/usr/bin/env python3
"""Archive public scoring observations without changing earlier predictions.

Each JSON snapshot contains build.py's metrics, AI scores and source metadata, and
the default scoring formula's result. It contains no browser preferences, prompts,
prose, or viewing telemetry. Missing audience measurements remain null.

    python history.py --metrics work/metrics.json --story site/story.json \
        --shared site/shared-ai.json --out work/history/snapshots/RUN.json

The destination is immutable. Retrying identical source inputs is a no-op; different
inputs at the same destination fail. Repeated match IDs across snapshots are useful:
they record how the pre-match inputs and later observed results changed over time.
"""
import argparse
from contextlib import redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from story import WEIGHTS
from story_state import valid_rating

MAX_INPUT_BYTES = 32 * 1024 * 1024
ET = ZoneInfo("America/New_York")


def timestamp(value):
    """A timezone-bearing ISO timestamp; never assume the machine's timezone."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timestamp has no timezone")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("invalid timestamp or missing timezone") from None


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def score(value):
    return number(value) and 0 <= value <= 100


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON field")
            out[key] = value
        return out

    def constant(_):
        raise ValueError("non-finite JSON number")

    path = Path(path)
    # Bound both the file on disk and its expanded JSON; never decompress an
    # untrusted archive into an unlimited in-memory buffer.
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("JSON input exceeds 32 MiB")
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except EOFError:
        raise ValueError("truncated gzip JSON input") from None
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("JSON input exceeds 32 MiB")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def validate_metrics(value):
    """Reject incomplete metrics rather than recording a plausible empty history."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported metrics version")
    timestamp(value.get("observed_at"))
    settings = value.get("settings")
    if not isinstance(settings, dict) or any(not isinstance(settings.get(key), dict) for key in ("blend", "outlook", "ai")):
        raise ValueError("metrics require blend, outlook and AI settings")
    blend = settings["blend"]
    if any(not number(blend.get(key)) or blend[key] < 0 for key in ("ai", "outlook", "interest", "league_priority")):
        raise ValueError("invalid scoring blend")
    if blend["ai"] + blend["outlook"] <= 0 or blend["interest"] + blend["league_priority"] <= 0:
        raise ValueError("scoring blend must have positive totals")
    for key in ("league_order", "default_leagues", "default_services"):
        items = value.get(key)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items) or len(set(items)) != len(items):
            raise ValueError("invalid " + key)
    matches = value.get("matches")
    if not isinstance(matches, list):
        raise ValueError("metrics require a matches array")
    ids = set()
    for match in matches:
        if not isinstance(match, dict) or not isinstance(match.get("id"), str) or not match["id"] or match["id"] in ids:
            raise ValueError("invalid or duplicate metrics match ID")
        ids.add(match["id"])
        if not isinstance(match.get("league_id"), str) or match.get("state") not in ("pre", "in", "post"):
            raise ValueError("invalid fixture league or state")
        timestamp(match.get("kickoff_utc"))
        if type(match.get("forecast_eligible")) is not bool or type(match.get("time_confirmed")) is not bool:
            raise ValueError("metrics require explicit forecast/time eligibility")
        if not isinstance(match.get("observed"), dict) or not isinstance(match.get("outlook"), dict):
            raise ValueError("metrics require observed outcomes and Outlook components")
        outlook = match["outlook"]
        if outlook.get("score") is not None and not score(outlook["score"]):
            raise ValueError("invalid Outlook score")
        if not isinstance(outlook.get("parts"), dict) or set(outlook["parts"]) != {"stature", "close", "stakes", "tv", "goals"}:
            raise ValueError("all five Outlook parts must be recorded")
        if any(v is not None and (not number(v) or not 0 <= v <= 1) for v in outlook["parts"].values()):
            raise ValueError("invalid Outlook part")
        if not isinstance(outlook.get("inputs"), dict) or not isinstance(outlook.get("missing_components"), list):
            raise ValueError("Outlook raw inputs and missing components must be recorded")
    canonical(value)  # Also rejects non-finite values anywhere in the raw inputs.


def automatic_source(story, captured):
    """Keep only scalar provenance and scores, never the source's text or prompts."""
    meta = {"status": "missing", "timestamp_scope": "story_bundle", "rating_generated_at": None}
    if story is None:
        return meta, {}
    if not isinstance(story, dict) or type(story.get("version")) is not int or story["version"] != 1 or not isinstance(story.get("rankings"), dict):
        return dict(meta, status="invalid"), {}
    for key in ("generated_at", "date", "focus_until", "model", "requested_model", "effort", "kind"):
        if isinstance(story.get(key), str):
            meta[key] = story[key]
    try:
        written = timestamp(story.get("generated_at"))
        focus = timestamp(story["focus_until"]) if story.get("focus_until") else None
    except ValueError:
        return dict(meta, status="invalid"), {}
    stale = captured - written > timedelta(hours=30) or (focus is not None and focus <= captured)
    # A dated story without focus_until expires at the site's Eastern day boundary.
    if focus is None and isinstance(story.get("date"), str):
        stale |= story["date"].replace("-", "") != captured.astimezone(ET).strftime("%Y%m%d")
    meta["status"] = "future" if written > captured else "stale" if stale else "available"
    ratings = {}
    for mid, rating in story["rankings"].items():
        if not isinstance(mid, str) or not valid_rating(rating) or not score(rating.get("score")):
            continue
        ratings[mid] = {key: rating[key] for key in ("popularity", "gameplay", "impact", "score")}
        # Store rather than silently repair a published score; the raw dimensions remain usable.
        expected = round(sum(rating[key] * weight for key, weight in WEIGHTS), 1)
        ratings[mid]["score_matches_components"] = math.isclose(rating["score"], expected, abs_tol=0.000001)
    meta["valid_ratings"] = len(ratings)
    meta["rejected_ratings"] = len(story["rankings"]) - len(ratings)
    return meta, ratings


def shared_source(shared, captured):
    """Reuse the publication validator, retaining only the public numeric ratings."""
    meta = {"status": "missing", "timestamp_scope": "prompt_created_at", "rating_generated_at": None}
    if shared is None:
        return meta, {}
    if not isinstance(shared, dict) or not number(shared.get("created_at")):
        return dict(meta, status="invalid"), {}
    try:
        created = datetime.fromtimestamp(shared["created_at"] / 1000, timezone.utc)
        # Validate at its original creation time so valid-but-expired ratings can still
        # be recorded as stale observations. This never refreshes their timestamps.
        from build import write_shared_ai
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_bytes(canonical(shared))
            with redirect_stderr(io.StringIO()):
                valid = write_shared_ai(root / "public", created, source)
        if not valid:
            return dict(meta, status="invalid"), {}
    except (TypeError, ValueError, OverflowError, OSError):
        return dict(meta, status="invalid"), {}
    meta.update(created_at=iso(created), weight=shared["weight"], response_version=shared["response"]["version"],
                status="future" if created > captured else "stale" if captured >= created + timedelta(hours=24) else "available")
    fixtures = {item["id"]: item for item in shared["fixtures"]}
    ratings = {item["id"]: {"score": item["score"], "league_id": fixtures[item["id"]]["league_id"],
                             "kickoff_utc": fixtures[item["id"]]["kickoff"]} for item in shared["response"]["matches"]}
    meta["valid_ratings"] = len(ratings)
    return meta, ratings


def snapshot(metrics, story=None, shared=None, *, captured_at=None, source_errors=None, external_observations=None):
    validate_metrics(metrics)
    captured = captured_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ValueError("capture time must specify a timezone")
    observed = timestamp(metrics["observed_at"])
    if observed > captured:
        raise ValueError("metrics were observed after the archive capture time")
    out = deepcopy(metrics)
    out["captured_at"] = iso(captured)
    out["external_observations"] = deepcopy(external_observations or [])
    out["settings"]["ai_rating_weights"] = dict(WEIGHTS)
    auto_meta, automatic = automatic_source(story, captured)
    shared_meta, imported = shared_source(shared, captured)
    out["ai_sources"] = {"automatic": auto_meta, "shared": shared_meta}
    for name, reason in (source_errors or {}).items():
        if name in out["ai_sources"]:
            out["ai_sources"][name]["status"] = reason
    out["automatic_default_formula"] = {
        "ai_source": "automatic only; shared and browser overrides excluded",
        "interest": "weighted mean of available AI and Outlook scores using settings.blend; one score alone when the other is missing",
        "league_priority": "100 * (league_count - league_rank) / (league_count - 1), using the full default order; 100 for one league; 0 if absent",
        "pick": "weighted mean of interest and league_priority using settings.blend",
    }
    blend = out["settings"]["blend"]
    order = out["league_order"]
    for match in out["matches"]:
        kickoff = timestamp(match["kickoff_utc"])
        pre = match["forecast_eligible"] and match["state"] == "pre" and match["time_confirmed"] and observed < kickoff
        match["forecast_eligible"] = pre
        ai_pre = pre and captured < kickoff
        match["observed"].setdefault("viewership", None)
        match["observed"].setdefault("viewership_status", "no_data_source")
        match["ai"] = {"automatic": None, "shared": None}
        rating = automatic.get(match["id"])
        auto_available = bool(rating and auto_meta["status"] == "available" and out["settings"]["ai"].get("enabled") is True)
        if rating:
            match["ai"]["automatic"] = dict(rating, source="automatic", eligible_at_capture=bool(ai_pre and auto_available),
                                                available_at_capture=auto_available)
        imported_rating = imported.get(match["id"])
        if imported_rating and (imported_rating["league_id"] == match["league_id"] and timestamp(imported_rating["kickoff_utc"]) == kickoff):
            match["ai"]["shared"] = {"source": "shared", "score": imported_rating["score"],
                                       "available_at_capture": shared_meta["status"] == "available",
                                       "eligible_at_capture": bool(ai_pre and shared_meta["status"] == "available")}
        league_rank = order.index(match["league_id"]) + 1 if match["league_id"] in order else None
        priority = (100.0 if len(order) < 2 else 100 * (len(order) - league_rank) / (len(order) - 1)) if league_rank else 0.0
        own = match["outlook"]["score"]
        ai_score = rating["score"] if auto_available else None
        if own is not None and ai_score is not None:
            interest = (own * blend["outlook"] + ai_score * blend["ai"]) / (blend["outlook"] + blend["ai"])
        else:
            interest = own if own is not None else ai_score
        pick = ((interest * blend["interest"] + priority * blend["league_priority"]) /
                (blend["interest"] + blend["league_priority"])) if interest is not None else None
        match["automatic_default"] = {"league_rank": league_rank, "league_priority": priority, "interest": interest,
                                       "pick": pick, "ai_used": ai_score is not None,
                                       "forecast_eligible": bool(pre and (not auto_available or ai_pre))}
    # Hash the inputs before pruning optional AI prose. The hash permits an identical
    # retry after kickoff to preserve the original snapshot and its original cutoff.
    out["input_sha256"] = hashlib.sha256(canonical({"metrics": metrics, "story": story, "shared": shared,
                                                   "source_errors": source_errors or {},
                                                   "external_observations": external_observations or []})).hexdigest()
    return out


def write_snapshot(destination, value):
    """Atomically create an immutable file, never replacing an existing observation."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value)
    if destination.suffix == ".gz":
        raw = gzip.compress(raw, mtime=0)
    with tempfile.NamedTemporaryFile(prefix=".snapshot-", suffix=".tmp", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, destination)  # Atomic create-if-absent, on the same filesystem.
            return True
        except FileExistsError:
            previous = read_json(destination)
            if isinstance(previous, dict) and previous.get("input_sha256") == value["input_sha256"]:
                return False
            raise ValueError("refusing to overwrite a different history snapshot: " + str(destination)) from None
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--story", type=Path)
    parser.add_argument("--shared", type=Path)
    parser.add_argument("--observations", type=Path, help="optional sourced audience measurements for current or earlier fixtures")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        metrics = read_json(args.metrics)
        optional, errors = {}, {}
        for name, path in (("automatic", args.story), ("shared", args.shared)):
            optional[name] = None
            if path is None:
                continue
            try:
                optional[name] = read_json(path)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, UnicodeError, RecursionError):
                errors[name] = "invalid"
                print("WARN History: invalid optional " + name + " source; preserving metrics without its ratings", file=sys.stderr)
        captured = datetime.now(timezone.utc)
        external = []
        if args.observations:
            from observations import load
            external = load(args.observations, captured)
        value = snapshot(metrics, optional["automatic"], optional["shared"], source_errors=errors,
                         captured_at=captured, external_observations=external)
        written = write_snapshot(args.out, value)
        print(("Archived " if written else "Already archived ") + str(args.out))
        return 0
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        print("History archive failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
