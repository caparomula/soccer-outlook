"""Independent evidence and publisher consumer checks for league eligibility."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tool.companion_eligibility_verify import BASELINE, frozen_check, load_checks
from tool.companion_p2_verify import assert_response


class EligibilityEvidence(unittest.TestCase):
    def test_frozen_browser_capture_has_nine_complete_source_scenarios(self):
        baseline = json.loads(BASELINE.read_text())
        self.assertEqual(len(baseline["scenarios"]), 9)
        identities = None
        for scenario in baseline["scenarios"]:
            rows = scenario["projection"]["fixtures"]
            current = {row["id"] for row in rows}
            self.assertEqual(len(rows), 360)
            self.assertEqual(len(current), 360)
            if identities is not None:
                self.assertEqual(current, identities)
            identities = current
        by_id = {scenario["id"]: scenario["projection"] for scenario in baseline["scenarios"]}
        for name in ("solo_over_saved_pause", "restore_saved_pause"):
            self.assertEqual(by_id[name]["paused"], by_id["pause_eligible"]["paused"])
        self.assertEqual(by_id["restore_saved_pause"]["fixtures"], by_id["pause_eligible"]["fixtures"])

    def test_comparison_detects_fixture_and_league_projection_regressions(self):
        case = frozen_check(json.loads(BASELINE.read_text())["scenarios"][0])
        for changed in ("fixture", "league", "strip", "solo"):
            with self.subTest(changed=changed):
                value = deepcopy(case["expected"])
                answer = value["results"][0]
                if changed == "fixture":
                    answer["fixtures"][0]["eligible"] = not answer["fixtures"][0]["eligible"]
                elif changed == "league":
                    answer["leagues"][0]["followed"] = not answer["leagues"][0]["followed"]
                elif changed == "strip":
                    answer["strip_league_ids"].pop()
                else:
                    answer["active_solo_id"] = "not-the-original-selection"
                with self.assertRaises(AssertionError):
                    assert_response(case, value)

    def test_independent_oracles_are_complete_and_separate_from_frozen_answers(self):
        checks = load_checks()
        self.assertGreater(sum(check["id"].startswith("independent:") for check in checks), 10)
        self.assertEqual(sum(check["id"].startswith("legacy:") for check in checks), 9)
        self.assertTrue(all(check["projection"] == "complete" for check in checks))

    def test_default_policy_change_invalidates_publisher_decision_cache(self):
        import build
        catalog = {"eng.1": dict(build.LEAGUES["eng.1"], default_off=True)}
        match = SimpleNamespace(league="eng.1")
        with patch.object(build, "LEAGUES", catalog):
            self.assertFalse(build.shown_by_default(match))
            catalog["eng.1"]["default_off"] = False
            self.assertTrue(build.shown_by_default(match))
            catalog["eng.1"]["default_off"] = True
            self.assertFalse(build.shown_by_default(match))


if __name__ == "__main__":
    unittest.main()
