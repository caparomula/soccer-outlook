"""Independent numerical-result evidence, including contradictory source values."""
from copy import deepcopy
import json
import unittest

from tool import companion_form_verify as probe


class CompanionFormEvidence(unittest.TestCase):
    def setUp(self):
        self.evidence = json.loads(probe.FIXTURE.read_text())

    def test_bounded_capture_supports_newest_first_but_preserves_contradictions(self):
        report = probe.verify(self.evidence)
        self.assertEqual(report["team_observations"], 16)
        self.assertEqual(report["source_requests"], 11)
        self.assertEqual(report["counts"], dict(oldest_first=0, newest_first=4,
                                                ambiguous_palindrome=0, inconsistent=12))
        self.assertEqual(report["partial_alignment"], dict(oldest_four_agree_if_newest_first=15,
                                                           only_newest_result_disagrees=11))
        confirmed = {case["team_name"]: (case["raw_form"], case["dated_results_oldest_first"])
                     for case in report["cases"] if case["classification"] == "newest_first"}
        self.assertEqual(confirmed, {
            "Chelsea": ("WWLDW", "WDLWW"),
            "Toluca": ("DLDLW", "WLDLD"),
            "CF Montréal": ("DLLWL", "LWLLD"),
            "Nashville SC": ("DWWWW", "WWWWD"),
        })
        # This is not a universal same-field reversal oracle: the contradictory
        # Guadalajara chronology remains visible and cannot be silently repaired.
        guadalajara = next(case for case in report["cases"] if case["team_name"] == "Guadalajara")
        self.assertEqual(guadalajara["raw_form"], "DDLDW")
        self.assertEqual(guadalajara["dated_results_oldest_first"], "DLWDD")
        self.assertEqual(guadalajara["classification"], "inconsistent")
        self.assertEqual(guadalajara["mismatched_indexes_if_newest_first"], [2, 3, 4])

    def test_dates_determine_chronology_not_source_array_order(self):
        expected = probe.verify(self.evidence)
        for case in self.evidence["cases"]:
            case["results"].reverse()
        self.assertEqual(probe.verify(self.evidence), expected)

    def test_reported_result_cannot_override_numerical_score(self):
        case = next(case for case in self.evidence["cases"] if case["team_name"] == "Chelsea")
        case["results"][0]["gameResult"] = "L"
        with self.assertRaisesRegex(ValueError, "numerical scores"):
            probe.verify(self.evidence)

    def test_raw_form_is_not_an_oracle_for_independent_result(self):
        case = next(case for case in self.evidence["cases"] if case["team_name"] == "Chelsea")
        case["raw_form"] = case["summary_form"] = "LLLLL"
        actual = next(item for item in probe.verify(self.evidence)["cases"] if item["team_name"] == "Chelsea")
        self.assertEqual(actual["dated_results_oldest_first"], "WDLWW")
        self.assertEqual(actual["classification"], "inconsistent")

    def test_unrelated_team_or_future_result_cannot_support_order(self):
        for mutation in ("team", "date"):
            with self.subTest(mutation=mutation):
                evidence = deepcopy(self.evidence)
                event = evidence["cases"][0]["results"][0]
                if mutation == "team":
                    event["homeTeamId"] = event["awayTeamId"] = "unrelated"
                else:
                    event["gameDate"] = "2100-01-01T00:00:00Z"
                with self.assertRaises(ValueError):
                    probe.verify(evidence)

    def test_receipt_cannot_be_promoted_to_source_observation(self):
        for request in self.evidence["requests"]:
            self.assertIsNone(request["source_observed_at"])
        self.evidence["source_observed_at"] = self.evidence["captured_at"]
        with self.assertRaisesRegex(ValueError, "invented source observation"):
            probe.verify(self.evidence)

    def test_shootout_does_not_silently_change_match_form_semantics(self):
        self.evidence["cases"][0]["results"][0]["homeShootoutScore"] = "4"
        with self.assertRaisesRegex(ValueError, "semantics"):
            probe.verify(self.evidence)


if __name__ == "__main__":
    unittest.main()
