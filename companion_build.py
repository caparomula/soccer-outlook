"""Opt-in build orchestration for local companion artifacts and delayed editorial.

This module performs no network requests. GitHub workflows supply durable prior
state and serialize publishers; a build never derives its sequence from a clock.
"""
from datetime import datetime, timezone
import json
from pathlib import Path


def prepare(api_dir, previous_api):
    """Restore validated published history before any build output is replaced."""
    import companion_publication

    if previous_api == "none":
        return None
    previous = Path(previous_api)
    if not previous.is_dir():
        raise ValueError("Previous companion API directory is missing; use 'none' only for a new publication.")
    return companion_publication.restore_previous(Path(api_dir), previous)


def finish(builder, matches, built_at, report, api_dir, *, producer_revision,
           standings_at=None, stage_path=None):
    """Export every fixture once; optionally defer the manifest until story finishes."""
    import companion_export

    artifacts = companion_export.export_artifacts(builder, matches, built_at, report,
                                                  standings_at=standings_at)
    staged = dict(version=1, producer_revision=producer_revision, artifacts=artifacts)
    if stage_path:
        destination = Path(stage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(json.dumps(staged, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return None
    site = Path(api_dir).parent.parent
    return finalize(staged, api_dir, automatic_path=site / "story.json", shared_path=site / "shared-ai.json")


def finalize(staged, api_dir, *, automatic_path=None, shared_path=None, published_at=None):
    """Optional editorial failure cannot replace required facts or renew its lifetime."""
    import companion_export
    import companion_publication

    if not isinstance(staged, dict) or staged.get("version") != 1:
        raise ValueError("Unsupported companion staging document.")
    instant = published_at or datetime.now(timezone.utc)
    artifacts = companion_export.add_editorial(staged["artifacts"], automatic_path=automatic_path,
                                              shared_path=shared_path, published_at=instant)
    return companion_publication.publish(Path(api_dir), artifacts, published_at=instant,
                                         producer_revision=staged["producer_revision"])
