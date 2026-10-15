"""Lightweight updates must preserve honest freshness and fail without replacing good data."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import schedule_data
from tests.test_discovery import fixture


NOW = datetime(2026, 10, 15, 20, tzinfo=timezone.utc)
ROOT = Path(build.__file__).resolve().parent


class SchedulePublication(unittest.TestCase):
    def publish(self, folder, matches=(), now=NOW, **kwargs):
        return schedule_data.publish(build, folder, list(matches), {}, now,
            dict(complete=kwargs.pop("complete", True), sources={"requested": 1, "successful": 1}), **kwargs)

    def test_contract_includes_full_rows_and_matches_page_runtime(self):
        match = fixture(league="mex.1", channel="TUDN", kickoff="2026-10-17T03:05:00+00:00")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = self.publish(directory, [match])
            self.assertEqual(snapshot["version"], 1)
            self.assertTrue(snapshot["complete"])
            self.assertEqual(len(snapshot["rows"]), 1)
            self.assertIn('data-o=', snapshot["rows"][0])
            self.assertIn("TUDN", snapshot["rows"][0])
            page = build.build_page([match], {}, NOW, [], NOW.date())
            self.assertIn('data-runtime-revision="' + snapshot["runtime_revision"] + '"', page)
            self.assertIn('data-schedule-built="2026-10-15T20:00:00Z"', page)
            context = schedule_data.read_context(Path(directory) / "schedule-context.json",
                snapshot["runtime_revision"], NOW)
            self.assertEqual(context["matches"][0]["utc"], match.utc.isoformat())

    def test_reusing_standings_does_not_renew_their_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            self.publish(directory, now=NOW, standings_at=NOW - timedelta(hours=29))
            path = Path(directory) / "schedule-context.json"
            revision = schedule_data.runtime_revision(ROOT)
            self.assertIsNotNone(schedule_data.read_context(path, revision, NOW))
            self.assertIsNone(schedule_data.read_context(path, revision, NOW + timedelta(hours=2)))
            self.assertIsNone(schedule_data.read_context(path, "different-runtime", NOW))
            self.assertIsNone(schedule_data.read_context(path, revision, NOW - timedelta(minutes=6)))

    def test_incomplete_snapshot_cannot_overwrite_last_good_context(self):
        with tempfile.TemporaryDirectory() as directory:
            self.publish(directory, [fixture()])
            path = Path(directory) / "schedule-context.json"
            before = path.read_bytes()
            self.publish(directory, now=NOW + timedelta(minutes=30), complete=False)
            self.assertEqual(path.read_bytes(), before)
            snapshot = json.loads((Path(directory) / "schedule.json").read_text())
            self.assertFalse(snapshot["complete"])
            health = json.loads((Path(directory) / "coverage.json").read_text())
            self.assertFalse(health["fixture_sources_complete"])

    def test_healthy_empty_snapshot_is_publishable(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = self.publish(directory)
            self.assertEqual(snapshot["rows"], [])
            self.assertTrue(snapshot["complete"])
            self.assertTrue((Path(directory) / "schedule-context.json").exists())

    def test_malformed_oversized_and_stale_contexts_are_ignored(self):
        for extreme in ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"):
            self.assertIsNone(schedule_data.timestamp(extreme))
        revision = schedule_data.runtime_revision(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            for value in ("not json", "[]", '{"version":true}', "x" * 200):
                path.write_text(value)
                with patch.object(schedule_data, "MAX_CONTEXT_BYTES", 100):
                    self.assertIsNone(schedule_data.read_context(path, revision, NOW))
            self.publish(directory, now=NOW - timedelta(hours=31))
            self.assertIsNone(schedule_data.read_context(Path(directory) / "schedule-context.json", revision, NOW))

    def test_lightweight_build_reuses_standings_and_preserves_ai_file(self):
        now = datetime.now(timezone.utc)
        match = fixture(kickoff=(now + timedelta(hours=1)).isoformat())
        def scoreboards(days, workers, quality):
            quality.sources.append(dict(league="eng.1", date=days[0].isoformat(), ok=True))
            return {"eng.1": {}}, {}, []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "index.html"
            self.publish(directory, [match], now=now, standings_at=now - timedelta(hours=1))
            story = Path(directory) / "story.json"
            story.write_text('{"retained":"original AI response"}')
            with (patch.object(build, "fetch_scoreboards", side_effect=scoreboards),
                  patch.object(build, "fetch_standings") as standings,
                  patch.object(build, "interpret_all", return_value=[match]),
                  patch.object(build.broadcast_listings, "enrich", return_value={}),
                  patch.object(build.fixture_comparison, "compare", return_value={}),
                  patch.object(build, "audit", return_value=[]),
                  patch("sys.argv", ["build.py", "--lightweight", "--out", str(output), "--no-logos"]),
                  patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                self.assertEqual(build.main(), 0)
            standings.assert_not_called()
            self.assertEqual(story.read_text(), '{"retained":"original AI response"}')
            context = json.loads((Path(directory) / "schedule-context.json").read_text())
            self.assertEqual(context["standings_at"], (now - timedelta(hours=1)).isoformat())

    def test_failed_lightweight_fetch_keeps_published_page(self):
        now = datetime.now(timezone.utc)
        def scoreboards(days, workers, quality):
            quality.sources += [dict(league="eng.1", date=days[0].isoformat(), ok=True),
                                dict(league="mex.1", date=days[0].isoformat(), ok=False, reason="Unavailable")]
            return {}, {}, [("mex.1", days[0])]
        with tempfile.TemporaryDirectory() as directory:
            self.publish(directory, [fixture()], now=now)
            output = Path(directory) / "index.html"
            output.write_text("previous good page")
            with (patch.object(build, "fetch_scoreboards", side_effect=scoreboards),
                  patch.object(build, "interpret_all", return_value=[fixture()]),
                  patch.object(build.broadcast_listings, "enrich") as enrichment,
                  patch("sys.argv", ["build.py", "--lightweight", "--out", str(output)]),
                  patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                self.assertEqual(build.main(), 3)
            enrichment.assert_not_called()
            self.assertEqual(output.read_text(), "previous good page")

    def test_partial_full_build_keeps_prior_page_and_separately_loaded_context(self):
        now = datetime.now(timezone.utc)
        def scoreboards(days, workers, quality):
            quality.sources += [dict(league="eng.1", date=days[0].isoformat(), ok=True),
                                dict(league="mex.1", date=days[0].isoformat(), ok=False, reason="Unavailable")]
            return {}, {}, [("mex.1", days[0])]
        with tempfile.TemporaryDirectory() as directory:
            previous_dir = Path(directory) / "published"
            self.publish(previous_dir, [fixture()], now=now)
            previous_path = previous_dir / "schedule-context.json"
            previous_bytes = previous_path.read_bytes()
            output = Path(directory) / "site" / "index.html"
            output.parent.mkdir()
            output.write_text("previous good page")
            report_path = Path(directory) / "report.json"
            with (patch.object(build, "fetch_scoreboards", side_effect=scoreboards),
                  patch.object(build, "fetch_standings", return_value={}),
                  patch.object(build, "interpret_all", return_value=[fixture()]),
                  patch.object(build.broadcast_listings, "enrich") as enrichment,
                  patch("sys.argv", ["build.py", "--out", str(output), "--no-logos",
                                     "--previous-context", str(previous_path), "--report", str(report_path)]),
                  patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                self.assertEqual(build.main(), 3)
            enrichment.assert_not_called()
            self.assertEqual(output.read_text(), "previous good page")
            self.assertEqual(previous_path.read_bytes(), previous_bytes)
            self.assertFalse(json.loads(report_path.read_text())["publishable"])
            self.assertFalse((output.parent / "schedule.json").exists())

    def test_initial_full_build_without_context_keeps_existing_partial_source_policy(self):
        def scoreboards(days, workers, quality):
            quality.sources += [dict(league="eng.1", date=days[0].isoformat(), ok=True),
                                dict(league="mex.1", date=days[0].isoformat(), ok=False, reason="Unavailable")]
            return {}, {}, [("mex.1", days[0])]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "index.html"
            with (patch.object(build, "fetch_scoreboards", side_effect=scoreboards),
                  patch.object(build, "fetch_standings", return_value={}),
                  patch.object(build, "interpret_all", return_value=[fixture()]),
                  patch.object(build.broadcast_listings, "enrich", return_value={}),
                  patch.object(build.fixture_comparison, "compare", return_value={}),
                  patch.object(build, "audit", return_value=[]),
                  patch("sys.argv", ["build.py", "--out", str(output), "--no-logos"]),
                  patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                self.assertEqual(build.main(), 0)
            self.assertTrue(output.exists())
            self.assertFalse(json.loads((output.parent / "schedule.json").read_text())["complete"])
            self.assertFalse((output.parent / "schedule-context.json").exists())


if __name__ == "__main__":
    unittest.main()
