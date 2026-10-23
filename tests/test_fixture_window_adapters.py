"""Explicit clock/identity mapping through the real production fixture-window CLI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import outlook_runtime as runtime


EXAMPLES = Path(__file__).resolve().parents[1] / "docs/companion/rules/fixture-window-examples.json"


def fixture(case_id="fixture", **changes):
    value = dict(id=case_id, now_utc="2026-10-23T17:00:00Z", time_zone="America/New_York",
                 kickoff_utc="2026-10-23T14:55:00Z", time_confirmed=True,
                 source_state="pre", received_at=None)
    value.update(changes)
    return value


def expected(case_id, bucket, focus, *, progress=False, recent=False, day=0):
    return dict(id=case_id, day_index=day, bucket=bucket, in_window=bucket is not None,
                in_focus=focus, source_in_progress=progress, recent_live_receipt=recent)


class FixtureWindowAdapters(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        runtime.close()

    def test_independently_authored_complete_examples_through_native_cli(self):
        examples = json.loads(EXAMPLES.read_text())
        self.assertEqual(len(examples["cases"]), 18)
        for group in examples["cases"]:
            with self.subTest(group=group["id"]):
                self.assertTrue(group["because"])
                self.assertEqual(runtime.fixture_windows(group["request"]["cases"]), group["expected"]["results"])

    def test_aware_datetime_mapping_preserves_microsecond_boundary_and_input(self):
        now = datetime(2026, 10, 23, 13, tzinfo=ZoneInfo("America/New_York"))
        kickoff = now - timedelta(minutes=125)
        cases = [fixture("exact", now_utc=now, kickoff_utc=kickoff),
                 fixture("after", now_utc=now + timedelta(microseconds=1), kickoff_utc=kickoff)]
        before = deepcopy(cases)
        self.assertEqual(runtime.fixture_windows(cases),
                         [expected("exact", "live", True), expected("after", "earlier", False)])
        self.assertEqual(cases, before)

    def test_receipt_age_uses_each_supplied_clock_and_preserves_absence(self):
        cases = [fixture("missing", kickoff_utc="2026-10-23T13:00:00Z", source_state="in"),
                 fixture("recent", kickoff_utc="2026-10-23T13:00:00Z", source_state="in",
                         received_at=datetime(2026, 10, 23, 12, 55, tzinfo=ZoneInfo("America/New_York"))),
                 fixture("expired", now_utc="2026-10-23T17:05:00Z", kickoff_utc="2026-10-23T13:00:00Z",
                         source_state="in", received_at="2026-10-23T16:55:00Z")]
        self.assertEqual(runtime.fixture_windows(cases), [expected("missing", "earlier", False, progress=True),
            expected("recent", "live", True, progress=True, recent=True), expected("expired", "earlier", False, progress=True)])

    def test_batch_timezone_and_identity_order_do_not_leak_between_cases(self):
        cases = [fixture("west", now_utc="2026-10-23T10:00:00Z", kickoff_utc="2026-10-23T07:00:00Z", time_zone="America/Los_Angeles"),
                 fixture("east", now_utc="2026-10-23T10:00:00Z", kickoff_utc="2026-10-23T07:00:00Z")]
        wanted = [expected("west", "earlier", False), expected("east", "yesterday", False, day=-1)]
        first = runtime.fixture_windows(cases)
        self.assertEqual(first, wanted)
        first[0]["bucket"] = "tampered"
        self.assertEqual(runtime.fixture_windows(list(reversed(cases))), list(reversed(wanted)))

    def test_count_and_order_mismatches_are_rejected_before_mapping_rows(self):
        cases = [fixture("one"), fixture("two")]
        answers = [expected("one", "live", True), expected("two", "live", True)]
        for response in (answers[:1], list(reversed(answers))):
            with self.subTest(response=response), patch.object(runtime, "evaluate", return_value={"version": 1, "results": response}):
                with self.assertRaisesRegex(RuntimeError, "identities"):
                    runtime.fixture_windows(cases)

    def test_negative_wire_inputs_fail_without_local_rule_fallback(self):
        for change in (dict(source_state="unrecognized"), dict(time_confirmed=1), dict(received_at="not-a-time"),
                       dict(time_zone="Not/A_Zone")):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "rejected"):
                runtime.fixture_windows([fixture(**change)])
        self.assertEqual(runtime.fixture_windows([fixture()]), [expected("fixture", "live", True)])

    def test_naive_datetime_does_not_acquire_a_machine_timezone(self):
        for field in ("now_utc", "kickoff_utc", "received_at"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.fixture_windows([fixture(**{field: datetime(2026, 10, 23, 17)})])


if __name__ == "__main__":
    unittest.main()
