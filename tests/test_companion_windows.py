"""Integrity and mutation checks for the independent fixture-window proof."""
from copy import deepcopy
import unittest

from tool.companion_p2_verify import assert_response
from tool.companion_window_verify import invalid_requests, load_checks, parse_args


class WindowEvidence(unittest.TestCase):
    def test_default_proof_is_portable_and_includes_both_engines(self):
        args = parse_args([])
        self.assertEqual(args.browser, "both")
        self.assertFalse(hasattr(args, "legacy"))
        checks, baseline = load_checks()
        self.assertEqual(len(baseline["cases"]), 456)
        preserved = next(check for check in checks if check["id"].startswith("legacy:"))
        self.assertEqual(len(preserved["request"]["cases"]), 453)

    def test_literal_boundaries_keep_distinct_focus_placement_and_source_facts(self):
        checks, _ = load_checks()
        by_id = {check["id"]: check for check in checks}
        focus = by_id["independent:inclusive-125-minute-composition"]["expected"]["results"]
        grace = by_id["independent:exclusive-155-minute-same-day-grace"]["expected"]["results"]
        receipt = by_id["independent:exclusive-ten-minute-receipt"]["expected"]["results"]
        self.assertEqual([row["in_focus"] for row in focus], [True, True, False])
        self.assertEqual([row["bucket"] for row in focus], ["live", "live", "earlier"])
        self.assertEqual([row["in_focus"] for row in grace], [False, False, False])
        self.assertEqual([row["bucket"] for row in grace], ["live", "earlier", "earlier"])
        self.assertTrue(all(row["source_in_progress"] for row in grace))
        self.assertEqual([row["recent_live_receipt"] for row in receipt], [True, False, False])

    def test_oracle_rejects_boundary_and_status_conflation_mutations(self):
        checks, _ = load_checks()
        by_id = {check["id"]: check for check in checks}
        mutations = [
            ("inclusive-125-minute-composition", 1, dict(bucket="earlier", in_focus=False)),
            ("exclusive-155-minute-same-day-grace", 1, dict(bucket="live")),
            ("exclusive-ten-minute-receipt", 1, dict(recent_live_receipt=True)),
            ("scheduled-placement", 1, dict(source_in_progress=True)),
            ("focus-is-independent-of-window-clipping", 0, dict(in_focus=False)),
            ("four-am-crossing", 3, dict(bucket="live")),
            ("future-receipt-does-not-extend", 0, dict(recent_live_receipt=True, bucket="live")),
        ]
        for identity, index, changes in mutations:
            with self.subTest(identity=identity):
                check = by_id["independent:" + identity]
                mutant = deepcopy(check["expected"])
                mutant["results"][index].update(changes)
                with self.assertRaises(AssertionError):
                    assert_response(check, mutant)

    def test_clock_corrections_preserve_original_recorded_defects(self):
        checks, baseline = load_checks()
        corrected = next(check for check in checks if check["id"] == "independent:three-declared-clock-corrections")
        original = [case for case in baseline["cases"] if case["intentional_delta"]]
        self.assertEqual(len(original), 3)
        self.assertTrue(all(case["legacy"]["bucket"] == "live" for case in original))
        self.assertTrue(all(result["bucket"] == "earlier" for result in corrected["expected"]["results"]))
        with self.assertRaises(AssertionError):
            assert_response(corrected, dict(version=1, results=[case["legacy"] for case in original]))

    def test_required_null_receipt_rejection_changes_only_presence(self):
        checks, _ = load_checks()
        missing = next(request for request in invalid_requests(checks) if "received_at" not in request["cases"][0])
        restored = deepcopy(missing["cases"][0])
        restored["received_at"] = None
        self.assertEqual(restored, checks[0]["request"]["cases"][0])


if __name__ == "__main__":
    unittest.main()
