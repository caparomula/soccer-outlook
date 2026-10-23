#!/usr/bin/env python3
"""Run the lightweight production path using one retained collection, without networking.

The build clock and all original scoreboard and standings times are retained.
Only the publication revision/manifest time advance. This is branch validation,
not a second live collection or proof that old source bytes remain current.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build
from companion_contract import validate_publication
from tool.companion_collection_capture import digest, load_requests, parse, require, save, source_hashes, stamp
from tool.companion_feed_verify import read as dart_read


def replay(capture, output, dart):
    require(not output.exists(), "Use a fresh replay output directory")
    metadata = json.loads((capture / "capture.json").read_text())
    require(metadata["completed"] and metadata["sources_unchanged"], "Replay requires a completed stable capture")
    records, bodies = load_requests(capture)
    original = capture / "full"
    previous_manifest, previous_artifacts = validate_publication(original / "api/v1")
    context = json.loads((original / "schedule-context.json").read_text())
    when = parse(context["generated_at"])
    timings = json.loads((capture / "quality.json").read_text())["source_times"]
    source_times = {(row["league"], row["date"]): {key: parse(row[key]) if row[key] else None
                    for key in ("attempted_at", "validated_at")} for row in timings}
    pending = defaultdict(deque)
    for row in records:
        pending[row["url"]].append(row)
    consumed = []

    def transport(url, timeout=40, attempts=3):
        require(bool(pending[url]), "Replay requested an uncaptured URL; network fallback is forbidden")
        row = pending[url].popleft()
        consumed.append(row["id"])
        return bodies.get(row["id"])

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    original_fetch = build.fetch_scoreboards

    def fetch(days, workers, quality):
        result = original_fetch(days, workers, quality)
        require(set(quality.source_times) == set(source_times), "Replay requested a different scoreboard scope")
        quality.source_times = {key: dict(value) for key, value in source_times.items()}
        return result

    output.mkdir(parents=True)
    site = output / "site"
    shutil.copytree(original, site)
    argv = ["build.py", "--out", str(site / "index.html"), "--workers", "6", "--lightweight",
            "--previous-context", str(original / "schedule-context.json"), "--facts", str(site / "facts.json"),
            "--report", str(site / "report.json"), "--companion-feed", "--companion-previous-api", str(original / "api/v1"),
            "--companion-producer-revision", metadata["producer_revision"]]
    before = source_hashes()
    before[str(Path(__file__).resolve().relative_to(ROOT))] = digest(Path(__file__).read_bytes())
    report = dict(version=1, mode="offline_lightweight_replay", source_capture=str(capture),
                  source_build_instant=stamp(when), source_hashes_before=before, completed=False,
                  limitation="Original captured bytes and original build/source times; no new source collection or freshness claim.")
    save(output / "replay.json", report)
    try:
        with patch.object(build, "curl_bytes", transport), patch.object(build, "datetime", FixedClock), \
                patch.object(build, "fetch_scoreboards", fetch), patch.object(sys, "argv", argv), \
                patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True):
            code = build.main()
        require(code == 0, "Lightweight replay build failed")
        manifest, artifacts = validate_publication(site / "api/v1")
        require(manifest["publication_revision"] > previous_manifest["publication_revision"], "Replay did not increase revision")
        for kind in ("catalog", "schedule", "policy"):
            require(artifacts[kind] == previous_artifacts[kind], f"Replay changed original {kind} facts or ages")
        require(json.loads((site / "schedule-context.json").read_text())["standings_at"] == context["standings_at"], "Replay renewed standings age")
        parsed = dart_read(dart, site / "api/v1")
        require(parsed["publication_revision"] == manifest["publication_revision"], "Independent reader revision differs")
        report.update(completed=True, publication_revisions=[previous_manifest["publication_revision"], manifest["publication_revision"]],
                      consumed_captured_requests=consumed, unchanged_required_artifacts=True, dart_reader_accepted=True)
    except BaseException as error:
        report["exception"] = type(error).__name__
        raise
    finally:
        report["source_hashes_after"] = source_hashes()
        report["source_hashes_after"][str(Path(__file__).resolve().relative_to(ROOT))] = digest(Path(__file__).read_bytes())
        report["sources_unchanged"] = report["source_hashes_after"] == before
        save(output / "replay.json", report)
    require(report["sources_unchanged"], "Sources changed during replay")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dart", default="dart")
    args = parser.parse_args()
    report = replay(args.capture.resolve(), args.out.resolve(), args.dart)
    print(json.dumps({key: report[key] for key in ("mode", "completed", "publication_revisions", "unchanged_required_artifacts")}))


if __name__ == "__main__":
    main()
