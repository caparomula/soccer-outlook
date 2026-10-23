"""Offline checks at the legacy builder / opt-in companion boundary."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import build
import companion_build
import schedule_data
from tests.test_discovery import fixture


NOW = datetime(2026, 10, 20, 18, tzinfo=timezone.utc)
REVISION = "a" * 40
ROOT = Path(__file__).resolve().parents[1]
try:
    import companion_contract
    import companion_export
    import companion_publication
except ImportError:
    companion_contract = None


class CompanionIntegration(unittest.TestCase):
    def modules(self):
        artifacts = {"catalog": {"test": "catalog"}, "schedule": {"test": "schedule"},
                     "policy": {"test": "policy"}, "editorial": None}
        exporter = SimpleNamespace(export_artifacts=Mock(return_value=artifacts),
                                   add_editorial=Mock(side_effect=lambda value, **kw: dict(value)))
        def restore(target, previous):
            shutil.copytree(previous, target, dirs_exist_ok=True)
        publisher = SimpleNamespace(restore_previous=Mock(side_effect=restore),
                                    publish=Mock(return_value={"publication_revision": 2}))
        return exporter, publisher

    def run_build(self, site, *, lightweight=False, enabled=False, complete=True,
                  previous_api="none", stage=None, exporter=None, publisher=None):
        match = fixture(kickoff=(NOW + timedelta(hours=1)).isoformat())
        if lightweight:
            schedule_data.publish(build, site, [match], {}, NOW - timedelta(minutes=30),
                dict(complete=True, sources={"requested": 1, "successful": 1}),
                standings_at=NOW - timedelta(hours=2))
        def collect(days, workers, quality):
            quality.sources.append(dict(league="eng.1", date=days[0].isoformat(), ok=True))
            quality.source_times[("eng.1", days[0].isoformat())] = dict(
                attempted_at=NOW + timedelta(seconds=1), validated_at=NOW + timedelta(seconds=2))
            if not complete:
                quality.sources.append(dict(league="mex.1", date=days[0].isoformat(), ok=False,
                                            reason="Unavailable"))
            return {"eng.1": {}}, {}, [] if complete else [("mex.1", days[0])]
        argv = ["build.py", "--out", str(site / "index.html"), "--no-logos", "--date", "2026-10-20",
                "--days-back", "0", "--days-ahead", "0", "--report", str(site / "report.json")]
        if lightweight:
            argv.append("--lightweight")
        if enabled:
            argv += ["--companion-feed", "--companion-previous-api", str(previous_api),
                     "--companion-producer-revision", REVISION]
        if stage:
            argv += ["--companion-stage", str(stage)]
        exporter, publisher = (exporter, publisher) if exporter else self.modules()
        with ExitStack() as stack:
            for context in (
                patch.dict("sys.modules", {"companion_export": exporter, "companion_publication": publisher}),
                patch.object(build, "fetch_scoreboards", side_effect=collect),
                patch.object(build, "fetch_standings", return_value={}),
                patch.object(build, "interpret_all", return_value=[match]),
                patch.object(build.broadcast_listings, "enrich", return_value={}),
                patch.object(build.fixture_comparison, "compare", return_value={}),
                patch.object(build, "audit", return_value=[]),
                patch.dict(build.STANDINGS, {}, clear=True), patch.dict(build.LEAGUE_LOGOS, {}, clear=True),
                patch.object(build, "TODAY", NOW.date()), patch("sys.argv", argv),
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
            ):
                stack.enter_context(context)
            clock = stack.enter_context(patch.object(build, "datetime", wraps=datetime))
            clock.now.return_value = NOW
            result = build.main()
        return result, exporter, publisher

    def test_opt_in_full_and_lightweight_export_original_standings_age_and_scopes(self):
        for lightweight in (False, True):
            with self.subTest(lightweight=lightweight), tempfile.TemporaryDirectory() as directory:
                result, exporter, publisher = self.run_build(Path(directory), lightweight=lightweight, enabled=True)
                self.assertEqual(result, 0)
                call = exporter.export_artifacts.call_args
                self.assertEqual(len(call.args[1]), 1)
                self.assertEqual(call.args[2], NOW)
                self.assertEqual(call.kwargs["standings_at"], NOW - timedelta(hours=2) if lightweight else NOW)
                self.assertEqual(call.args[3]["source_scopes"], [{
                    "source": "espn", "league_id": "eng.1", "date_zone": "America/New_York",
                    "requested_dates": ["2026-10-20"], "covered_dates": ["2026-10-20"], "complete": True,
                    "attempted_at": "2026-10-20T18:00:01Z", "validated_at": "2026-10-20T18:00:02Z"}])
                publisher.publish.assert_called_once()
                self.assertEqual(publisher.publish.call_args.kwargs["producer_revision"], REVISION)

    def test_default_does_not_export_or_change_legacy_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            result, exporter, publisher = self.run_build(old)
            self.assertEqual(result, 0)
            exporter.export_artifacts.assert_not_called()
            publisher.restore_previous.assert_not_called()
            publisher.publish.assert_not_called()
            self.assertFalse((old / "api").exists())
            self.assertEqual(self.run_build(new, enabled=True)[0], 0)
            for path in old.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (new / path.relative_to(old)).read_bytes(), str(path))

    def test_incomplete_enabled_build_keeps_prior_feed_and_page_for_both_paths(self):
        for lightweight in (False, True):
            with self.subTest(lightweight=lightweight), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prior, site = root / "prior", root / "site"
                prior.mkdir()
                original = b'{"publication_revision":7,"old":"content"}\n'
                (prior / "manifest.json").write_bytes(original)
                site.mkdir()
                (site / "index.html").write_text("last good page")
                result, exporter, publisher = self.run_build(site, lightweight=lightweight, enabled=True,
                    complete=False, previous_api=prior)
                self.assertEqual(result, 3)
                self.assertEqual((site / "api/v1/manifest.json").read_bytes(), original)
                self.assertEqual((prior / "manifest.json").read_bytes(), original)
                self.assertEqual((site / "index.html").read_text(), "last good page")
                exporter.export_artifacts.assert_not_called()
                publisher.publish.assert_not_called()

    def test_stage_waits_for_editorial_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            stage = site / "stage.json"
            result, exporter, publisher = self.run_build(site, enabled=True, stage=stage)
            self.assertEqual(result, 0)
            publisher.publish.assert_not_called()
            exporter.add_editorial.assert_not_called()
            story = site / "story.json"
            story.write_text('{"generated_at":"2026-10-20T17:00:00Z"}')
            staged = json.loads(stage.read_text())
            with patch.dict("sys.modules", {"companion_export": exporter, "companion_publication": publisher}):
                companion_build.finalize(staged, site / "api/v1", automatic_path=story,
                    shared_path=site / "shared-ai.json", published_at=NOW)
            publisher.publish.assert_called_once()
            self.assertEqual(exporter.add_editorial.call_args.kwargs["automatic_path"], story)
            self.assertEqual(publisher.publish.call_args.args[1]["schedule"], staged["artifacts"]["schedule"])

    def test_missing_previous_path_is_not_silently_a_new_sequence(self):
        _, publisher = self.modules()
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {"companion_publication": publisher}):
            with self.assertRaisesRegex(ValueError, "missing"):
                companion_build.prepare(Path(directory) / "output", str(Path(directory) / "missing"))
            publisher.restore_previous.assert_not_called()

    def test_options_fail_before_source_requests(self):
        for extra in (["--companion-feed"], ["--companion-producer-revision", REVISION],
                      ["--companion-feed", "--fragment", "--companion-previous-api", "none",
                       "--companion-producer-revision", REVISION]):
            with self.subTest(extra=extra), patch("sys.argv", ["build.py", *extra]), \
                 patch.object(build, "fetch_scoreboards") as fetch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    build.main()
                fetch.assert_not_called()

    def test_workflow_writers_share_gate_and_finalize_after_optional_story(self):
        full = (ROOT / ".github/workflows/refresh.yml").read_text()
        light = (ROOT / ".github/workflows/schedule.yml").read_text()
        for workflow in (full, light):
            self.assertIn("group: refresh", workflow)
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertIn("vars.COMPANION_FEED_ENABLED == 'true'", workflow)
            self.assertIn('--companion-stage work/companion-stage.json', workflow)
            self.assertIn('bash .github/scripts/check-current-revision.sh\n          python3 .github/scripts/companion-feed.py', workflow)
        self.assertLess(full.index('"$story_python" story.py'), full.index('name: Finalize opt-in companion'))
        self.assertIn('git archive FETCH_HEAD api | tar -x', full)
        self.assertIn('keeping the published site.', full)

    def test_disabled_workflow_keeps_unknown_history_fail_closed_and_allows_proven_bootstrap(self):
        workflow = (ROOT / ".github/workflows/refresh.yml").read_text()
        step = workflow.split("      - name: Read previous broadcast evidence and companion history\n", 1)[1].split("\n      - name:", 1)[0]
        script = "\n".join(line[10:] for line in step.split("        run: |\n", 1)[1].splitlines())
        for remote_status, expected in ((0, 1), (128, 1), (2, 0)):
            with self.subTest(remote_status=remote_status), tempfile.TemporaryDirectory() as directory:
                mocked = f'''git() {{
                    case "$1" in
                        fetch) return 128 ;;
                        ls-remote) return {remote_status} ;;
                        *) return 99 ;;
                    esac
                }}
                COMPANION_FEED_ENABLED=false
                '''
                result = subprocess.run(["bash", "-eu", "-o", "pipefail", "-c", mocked + script],
                                        cwd=directory, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                self.assertFalse((Path(directory) / "site").exists())

    @unittest.skipUnless(companion_contract, "install tests/requirements-companion.txt for real exporter checks")
    def test_real_full_then_lightweight_publication_restores_sequence_and_retained_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full, light = root / "full", root / "light"
            result, _, _ = self.run_build(full, enabled=True, exporter=companion_export, publisher=companion_publication)
            self.assertEqual(result, 0)
            first, original = companion_contract.validate_publication(full / "api/v1")
            self.assertEqual(first["publication_revision"], 1)
            result, _, _ = self.run_build(light, lightweight=True, enabled=True, previous_api=full / "api/v1",
                                         exporter=companion_export, publisher=companion_publication)
            self.assertEqual(result, 0)
            second, current = companion_contract.validate_publication(light / "api/v1")
            self.assertEqual(second["publication_revision"], 2)
            self.assertEqual([fixture["id"] for fixture in current["schedule"]["fixtures"]],
                             [fixture["id"] for fixture in original["schedule"]["fixtures"]])
            for descriptor in first["artifacts"].values():
                if descriptor:
                    self.assertEqual((full / "api/v1" / descriptor["path"]).read_bytes(),
                                     (light / "api/v1" / descriptor["path"]).read_bytes())
            previous_manifest = (light / "api/v1/manifest.json").read_bytes()
            self.assertEqual(self.run_build(light, lightweight=True, enabled=True, complete=False,
                previous_api=light / "api/v1", exporter=companion_export, publisher=companion_publication)[0], 3)
            self.assertEqual((light / "api/v1/manifest.json").read_bytes(), previous_manifest)


if __name__ == "__main__":
    unittest.main()
