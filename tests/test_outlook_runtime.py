"""Adversarial publisher adapter checks; helpers are local and terminate quickly."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import outlook_runtime as runtime


def response(service="alpha", providers=None):
    return {"version": 1, "results": [{
        "id": "route", "service_id": service,
        "basis": "listed" if service else "none", "outlet": "Shared" if service else "",
        "listed_providers": [providers if providers is not None else [service]],
        "usual_providers": [],
    }]}


class RuntimeAdapterTest(unittest.TestCase):
    def setUp(self):
        runtime.close()
        runtime.artifacts.cache_clear()
        runtime._routes.cache_clear()
        runtime.calendar_shift.cache_clear()
        self.addCleanup(runtime.close)
        self.addCleanup(runtime.artifacts.cache_clear)
        self.addCleanup(runtime._routes.cache_clear)
        self.addCleanup(runtime.calendar_shift.cache_clear)

    def helper_failure(self, script, request, message):
        """Substitute a bounded local child at the subprocess boundary, not I/O."""
        real_popen = subprocess.Popen
        children = []

        def start(_command, **kwargs):
            child = real_popen([sys.executable, "-u", "-c", script], **kwargs)
            children.append(child)
            return child

        started = time.monotonic()
        try:
            with patch.object(runtime, "artifacts", return_value=Path("/unused-test-runtime")), \
                    patch.object(runtime.subprocess, "Popen", side_effect=start), \
                    patch.object(runtime, "TIMEOUT_SECONDS", 0.15):
                with self.assertRaisesRegex(RuntimeError, message):
                    runtime.evaluate(request)
            self.assertIsNone(runtime._process)
            self.assertEqual(runtime._pending, b"")
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll(), "timed-out helper must be reaped")
            self.assertTrue(children[0].stdin.closed)
            self.assertTrue(children[0].stdout.closed)
            # Cleanup may wait for a helper to exit; each helper itself has a
            # short fixed lifetime so a broken adapter cannot strand this test.
            self.assertLess(time.monotonic() - started, 5)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=2)
                for stream in (child.stdin, child.stdout):
                    if stream and not stream.closed:
                        stream.close()

    def test_deadline_covers_a_child_that_never_reads_a_large_request(self):
        # Far larger than a pipe buffer. A blocking write waits until this helper
        # exits and reports a closed pipe instead of the adapter's deadline.
        self.helper_failure(
            "import time; time.sleep(1)",
            {"payload": "x" * (1024 * 1024)},
            "timed out",
        )

    def test_trickled_response_cannot_restart_the_total_deadline(self):
        # Every byte arrives well inside 150 ms, but the complete line takes
        # about a second. A per-read timeout would incorrectly accept it.
        self.helper_failure(
            "import sys,time\n"
            "sys.stdin.readline()\n"
            "for character in '{\"version\":1,\"results\":[]}\\n':\n"
            " sys.stdout.write(character); sys.stdout.flush(); time.sleep(0.04)\n",
            {"version": 1, "operation": "route_selection", "cases": []},
            "timed out",
        )

    def test_request_limit_rejects_before_spawning_a_child(self):
        with patch.object(runtime.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "request exceeds limit"):
                runtime.evaluate({"payload": "x" * runtime.MAX_BYTES})
        spawn.assert_not_called()

    def test_microsecond_calendar_boundaries(self):
        import story_state

        # New York is UTC-04 on October 21. Before 04:00 its sports-day label
        # remains October 20; four calendar labels later is October 24. Just
        # after 04:00 the label is October 21 and its fourth following day is 25.
        cases = [
            (datetime(2026, 10, 21, 7, 59, 59, 999999, tzinfo=timezone.utc),
             datetime(2026, 10, 24, 8, tzinfo=timezone.utc)),
            (datetime(2026, 10, 21, 8, 0, 0, 1, tzinfo=timezone.utc),
             datetime(2026, 10, 25, 8, tzinfo=timezone.utc)),
        ]
        for instant, expected in cases:
            with self.subTest(instant=instant):
                self.assertEqual(runtime.calendar_shift(instant, "America/New_York", 4), expected)
                self.assertEqual(story_state.window_end(instant), expected)

    def test_complete_oversized_response_is_rejected_and_child_reaped(self):
        with patch.object(runtime, "MAX_BYTES", 1024):
            self.helper_failure(
                "import sys\n"
                "sys.stdin.readline()\n"
                "sys.stdout.write('x'*2048+'\\n'); sys.stdout.flush()\n",
                {"request": "small"},
                "response exceeds limit",
            )

    def test_missing_stale_and_corrupt_artifacts_fail_closed(self):
        sources = {"owner.dart": "current-source-digest"}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            binaries = {"outlook": b"native fixture", "outlook.js": b"browser fixture"}
            manifest = {"version": 1, "sources": sources, "artifacts": {
                name: hashlib.sha256(value).hexdigest() for name, value in binaries.items()
            }}
            with patch.dict("os.environ", {"OUTLOOK_RUNTIME_DIR": str(directory)}), \
                    patch.object(runtime, "source_manifest", return_value=sources):
                for mutation in ("missing-manifest", "malformed-manifest", "stale-sources",
                                 "missing-outlook", "missing-outlook.js",
                                 "corrupt-outlook", "corrupt-outlook.js"):
                    with self.subTest(mutation=mutation):
                        for name, content in binaries.items():
                            (directory / name).write_bytes(content)
                        (directory / "runtime.json").write_text(json.dumps(manifest))
                        candidate = deepcopy(manifest)
                        if mutation == "missing-manifest":
                            (directory / "runtime.json").unlink()
                        elif mutation == "malformed-manifest":
                            (directory / "runtime.json").write_text("{")
                        elif mutation == "stale-sources":
                            candidate["sources"]["owner.dart"] = "old-source-digest"
                            (directory / "runtime.json").write_text(json.dumps(candidate))
                        elif mutation.startswith("missing-"):
                            (directory / mutation.removeprefix("missing-")).unlink()
                        else:
                            (directory / mutation.removeprefix("corrupt-")).write_bytes(b"corrupted")
                        runtime.artifacts.cache_clear()
                        with self.assertRaisesRegex(RuntimeError, "missing or stale"):
                            runtime.artifacts()
                for name, content in binaries.items():
                    (directory / name).write_bytes(content)
                (directory / "runtime.json").write_text(json.dumps(manifest))
                runtime.artifacts.cache_clear()
                self.assertEqual(runtime.artifacts(), directory)

    def test_changed_selection_and_order_do_not_reuse_a_prior_decision(self):
        listed = [{"label": "Shared", "service_ids": ["alpha", "beta"], "spanish": False}]
        names = {"alpha": "Alpha", "beta": "Beta"}
        answers = [response("alpha", ["alpha", "beta"]), response("beta", ["beta"]),
                   response("beta", ["beta", "alpha"])]
        with patch.object(runtime, "evaluate", side_effect=answers) as evaluate:
            first = runtime.routes(listed, None, ["alpha", "beta"], ["alpha", "beta"], names)
            selected = runtime.routes(listed, None, ["beta"], ["alpha", "beta"], names)
            reordered = runtime.routes(listed, None, ["alpha", "beta"], ["beta", "alpha"], names)
            again = runtime.routes(listed, None, ["beta", "alpha"], ["beta", "alpha"], names)
        self.assertEqual(first["service_id"], "alpha")
        self.assertEqual(selected["listed_providers"], [["beta"]])
        self.assertEqual(reordered["listed_providers"], [["beta", "alpha"]])
        self.assertEqual(again, reordered)
        self.assertEqual(evaluate.call_count, 3)
        self.assertEqual(evaluate.call_args_list[1].args[0]["lineup"]["selected_service_ids"], ["beta"])
        self.assertEqual(evaluate.call_args_list[2].args[0]["lineup"]["service_order"], ["beta", "alpha"])

    def test_returned_nested_mutation_does_not_poison_a_cached_decision(self):
        listed = [{"label": "Shared", "service_ids": ["alpha", "beta"], "spanish": False}]
        names = {"alpha": "Alpha", "beta": "Beta"}
        expected = response("alpha", ["alpha", "beta"])
        with patch.object(runtime, "evaluate", return_value=deepcopy(expected)) as evaluate:
            value = runtime.routes(listed, None, ["alpha", "beta"], ["alpha", "beta"], names)
            value["listed_providers"][0].clear()
            value["usual_providers"].append("invented")
            value["service_id"] = "invented"
            after = runtime.routes(listed, None, ["alpha", "beta"], ["alpha", "beta"], names)
        self.assertEqual(after, expected["results"][0])
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(listed[0]["service_ids"], ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
