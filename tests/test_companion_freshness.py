"""Independent live-freshness literals through the real publisher adapter."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import outlook_runtime as runtime
from tool.companion_freshness_verify import invalid_requests, load_checks, parse_args
from tool.companion_p2_verify import assert_response


class FreshnessEvidence(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        runtime.close()

    def test_independent_literals_through_native_publisher_adapter(self):
        for check in load_checks():
            with self.subTest(group=check["id"]):
                cases = check["request"]["cases"]
                before = deepcopy(cases)
                self.assertEqual(runtime.live_freshness(cases), check["expected"]["results"])
                self.assertEqual(cases, before)

    def test_aware_datetime_mapping_retains_microsecond_cutoff(self):
        received = datetime(2026, 10, 23, 13, tzinfo=ZoneInfo("America/New_York"))
        exact = received + timedelta(minutes=3)
        cases = [dict(id="before", source_state="in", received_at=received,
                      now_utc=exact - timedelta(microseconds=1)),
                 dict(id="exact", source_state="in", received_at=received, now_utc=exact)]
        before = deepcopy(cases)
        self.assertEqual(runtime.live_freshness(cases), [
            dict(id="before", live_now=True, last_known=False, reason=None,
                 expires_at="2026-10-23T17:03:00.000Z", next_transition_at="2026-10-23T17:03:00.000Z"),
            dict(id="exact", live_now=False, last_known=True, reason="expired",
                 expires_at="2026-10-23T17:03:00.000Z", next_transition_at=None),
        ])
        self.assertEqual(cases, before)

    def test_naive_datetimes_never_acquire_a_machine_timezone(self):
        for field in ("received_at", "now_utc"):
            case = dict(id="naive", source_state="in", received_at=None, now_utc="2026-10-23T17:00:00Z")
            case[field] = datetime(2026, 10, 23, 17)
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.live_freshness([case])

    def test_protocol_rejections_leave_the_persistent_adapter_usable(self):
        checks = load_checks()
        for request in invalid_requests(checks):
            with self.subTest(request=request), self.assertRaisesRegex(RuntimeError, "rejected"):
                runtime.live_freshness(request["cases"])
        self.assertEqual(runtime.live_freshness(checks[0]["request"]["cases"]), checks[0]["expected"]["results"])

    def test_adapter_rejects_missing_or_reordered_result_identities(self):
        cases = [dict(id=identity, source_state="in", received_at=None, now_utc="2026-10-23T17:00:00Z")
                 for identity in ("one", "two")]
        for answers in ([dict(id="one")], [dict(id="two"), dict(id="one")]):
            with self.subTest(answers=answers), patch.object(runtime, "evaluate", return_value=dict(version=1, results=answers)):
                with self.assertRaisesRegex(RuntimeError, "identities"):
                    runtime.live_freshness(cases)

    def test_literal_oracle_rejects_live_claim_and_timer_mutations(self):
        for check in load_checks():
            for index, answer in enumerate(check["expected"]["results"]):
                for field in ("live_now", "last_known"):
                    with self.subTest(group=check["id"], index=index, field=field):
                        mutant = deepcopy(check["expected"])
                        mutant["results"][index][field] = not answer[field]
                        with self.assertRaises(AssertionError):
                            assert_response(check, mutant)
                if answer["next_transition_at"] is not None:
                    mutant = deepcopy(check["expected"])
                    mutant["results"][index]["next_transition_at"] = None
                    with self.assertRaises(AssertionError):
                        assert_response(check, mutant)

    def test_default_protocol_proof_requires_both_browser_engines(self):
        self.assertEqual(parse_args([]).browser, "both")


if __name__ == "__main__":
    unittest.main()
