"""Negative checks prevent a completion marker from masquerading as native proof."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "tool/companion_live_probe/verify_native_evidence.py"
SPEC = importlib.util.spec_from_file_location("native_evidence", SOURCE)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


def records():
    environment = {
        "event": "environment", "platform": "ios", "os_version": "testOS",
        "dart_version": "testDart", "iana_zone": "America/New_York",
        "plugins": {"sqlite": "test"}, "documents_exists": True,
        "cache_exists": True, "temporary_exists": True,
    }
    public = [environment, {
        "event": "public_https_pass", "capture": {
            "runtime_platform": "ios", "http_status": 200,
            "source_url": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
            "scoreboard": {"events": []}, "source_score_observed_at": None,
        },
    }]
    local = [deepcopy(environment),
             {"event": "sqlite_close_reopen_pass", "rows": 1},
             {"event": "real_socket_timeout_pass", "elapsed_ms": 8004},
             {"event": "after_timeout_recovery_pass", "http_status": 200},
             {"event": "local_request_received", "path": "/p0/lifecycle", "lifecycle_request_count": 1},
             {"event": "native_lifecycle", "state": "hidden"},
             {"event": "session_cancelled_on_native_pause", "busy": False},
             {"event": "lifecycle_check_completed", "check": 1, "cancelled": True},
             {"event": "native_lifecycle", "state": "resumed"},
             {"event": "local_request_received", "path": "/p0/lifecycle", "lifecycle_request_count": 2},
             {"event": "lifecycle_check_completed", "check": 2, "cancelled": False},
             {"event": "native_pause_resume_pass", "requests": 2, "old_check_rejected": True},
             {"event": "all_native_checks_complete"}]
    return public, local


class NativeEvidence(unittest.TestCase):
    def test_valid_split_runs_preserve_public_run_failure_context(self):
        public, local = records()
        public.append({"event": "harness_failed", "error": "later local check failed"})
        result = native.verify(public, local, "ios")
        self.assertEqual(result["public_run_other_failures"], ["harness_failed"])
        self.assertEqual(result["stalled_socket_timeout_ms"], 8004)

    def test_completion_without_public_success_is_not_a_pass(self):
        public, local = records()
        public[1]["event"] = "public_https_failed"
        with self.assertRaises(ValueError):
            native.verify(public, local, "ios")

    def test_host_transport_cannot_be_relabelled_as_native(self):
        public, local = records()
        public[0]["platform"] = "macos"
        with self.assertRaises(ValueError):
            native.verify(public, local, "ios")

    def test_failure_lost_cancellation_or_duplicate_request_rejects_run(self):
        for change in (
            lambda rows: rows.append({"event": "harness_failed"}),
            lambda rows: rows[2].update(elapsed_ms=25),
            lambda rows: rows[5].update(state="inactive"),
            lambda rows: (rows[5].update(state="resumed"), rows[8].update(state="hidden")),
            lambda rows: rows[7].update(cancelled=False),
            lambda rows: rows.append(deepcopy(rows[7])),
            lambda rows: rows[11].update(requests=3),
            lambda rows: rows.append({"event": "all_native_checks_complete"}),
            lambda rows: rows.insert(5, rows.pop(7)),
            lambda rows: rows.insert(8, rows.pop(9)),
            lambda rows: rows[6].update(busy=True),
        ):
            public, local = records()
            change(local)
            with self.subTest(change=change), self.assertRaises(ValueError):
                native.verify(public, local, "ios")

    def test_environment_drift_requires_new_evidence_pair(self):
        public, local = records()
        local[0]["plugins"]["sqlite"] = "different"
        with self.assertRaises(ValueError):
            native.verify(public, local, "ios")

    def test_transport_time_cannot_become_a_source_score_timestamp(self):
        public, local = records()
        public[1]["capture"]["source_score_observed_at"] = "2026-10-20T20:00:00Z"
        with self.assertRaises(ValueError):
            native.verify(public, local, "ios")


if __name__ == "__main__":
    unittest.main()
