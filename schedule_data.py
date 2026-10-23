"""Publish small schedule updates and reuse public standings between full builds.

No AI calls, authentication or subscriber data belong in these files. The context
is an expiring build cache; schedule.json is the versioned browser contract.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import outlook_runtime


VERSION = 1
MAX_CONTEXT_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_AGE = timedelta(hours=30)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def runtime_revision(root):
    """Reject updates that need a different renderer, rights table or score model."""
    root = Path(root)
    names = ["build.py", "rights.toml", "settings.toml", "watch-links.toml", "watch_links.py", "story_state.py", "provider_utils.py"]
    names += ["outlook_runtime.py", *outlook_runtime.source_manifest(root)]
    names += sorted(str(path.relative_to(root)) for path in (root / "web").glob("*")
                    if path.suffix in (".js", ".css", ".html"))
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0")
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def read_context(path, revision, now):
    """Ignore stale/incompatible caches; a lightweight build then keeps the old site."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_CONTEXT_BYTES + 1)
        if len(raw) > MAX_CONTEXT_BYTES:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != VERSION:
            return None
        generated, standings = timestamp(data.get("generated_at")), timestamp(data.get("standings_at"))
        if (data.get("runtime_revision") != revision or not generated or not standings or
                not timedelta(minutes=-5) <= now - generated <= MAX_CONTEXT_AGE or
                not timedelta(minutes=-5) <= now - standings <= MAX_CONTEXT_AGE):
            return None
        if (not isinstance(data.get("standings"), dict) or not isinstance(data.get("league_logos"), dict)
                or not isinstance(data.get("matches"), list) or len(data["matches"]) > 3000):
            return None
        for league, entry in data["standings"].items():
            if not isinstance(league, str) or not isinstance(entry, dict):
                return None
            if not isinstance(entry.get("by_team"), dict) or not isinstance(entry.get("tables"), list):
                return None
        if any(not isinstance(key, str) or not isinstance(url, str)
               for key, url in data["league_logos"].items()):
            return None
        if any(not isinstance(match, dict) for match in data["matches"]):
            return None
        return data
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return None


def write_json(path, data):
    """Atomic locally, and published together with the page by the workflow."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def public_health(report, observed_at):
    return dict(version=VERSION, generated_at=observed_at.isoformat(),
                fixture_sources_complete=report.get("complete") is True,
                fixture_sources=report.get("sources", {}),
                broadcasts=report.get("broadcast_health", {}),
                fixture_comparison=report.get("fixture_comparison", {}))


def publish(builder, output_dir, matches, logos, built_at, report, *, standings_at=None):
    """Write browser rows, human-readable source health and reusable public build context."""
    output_dir = Path(output_dir)
    revision = builder.RUNTIME_REVISION
    snapshot = dict(version=VERSION, generated_at=built_at.isoformat(), runtime_revision=revision,
                    complete=report.get("complete") is True,
                    rows=[builder.row_html(match, logos, now=built_at) for match in matches], logos=logos)
    write_json(output_dir / "schedule.json", snapshot)
    write_json(output_dir / "coverage.json", public_health(report, built_at))
    # Do not renew the standings timestamp when only schedules have been fetched.
    records = []
    for match in matches:
        record = asdict(match)
        record["utc"] = match.utc.isoformat()
        records.append(record)
    context = dict(version=VERSION, generated_at=built_at.isoformat(),
                   standings_at=(standings_at or built_at).isoformat(), runtime_revision=revision,
                   standings=builder.STANDINGS, league_logos=builder.LEAGUE_LOGOS, matches=records)
    # Failed fixture refreshes must not become the next reusable source of truth.
    if snapshot["complete"]:
        write_json(output_dir / "schedule-context.json", context)
    return snapshot


def summary(report):
    """A maintainer summary that never calls transport success complete coverage."""
    fixture = report.get("sources", {})
    lines = ["## Schedule source coverage", "",
             f"ESPN fixture requests: {fixture.get('successful', 0)}/{fixture.get('requested', 0)} usable.",
             "Broadcast coverage is assessed separately; a missing listing is not proof of no broadcast.", ""]
    health = report.get("broadcast_health", {})
    sources = health.get("sources", health) if isinstance(health, dict) else {}
    if isinstance(sources, dict):
        lines += ["| Source | Requests | Confirmed | Unresolved | Failures |", "| --- | ---: | ---: | ---: | ---: |"]
        for name, source in sources.items():
            if not isinstance(source, dict) or "requested" not in source:
                continue
            safe_name = re.sub(r"[\r\n|]", " ", str(name))
            lines.append(f"| {safe_name} | {source.get('requested', 0)} | {source.get('confirmed', 0)} | "
                         f"{source.get('unmatched', 0)} | {source.get('failed', 0)} |")
    comparison = report.get("fixture_comparison", {})
    if isinstance(comparison, dict):
        lines += ["", "Independent fixture comparison (limited to the sources and dates recorded in coverage.json):"]
        for key in ("missing_from_espn", "missing_from_secondary", "kickoff_conflicts", "ambiguous"):
            items = comparison.get(key, [])
            if isinstance(items, list):
                lines.append(f"- {key.replace('_', ' ')}: {len(items)}")
    return "\n".join(lines) + "\n"
