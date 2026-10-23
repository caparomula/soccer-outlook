"""Independent corpus and actual publisher-consumer checks for the P2 extraction."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tool.companion_p2_verify import BASELINE, ROOT, assert_response, load_cases, sha, verify_runtime


class P2Corpus(unittest.TestCase):
    def test_frozen_expectations_reproduce_the_captured_legacy_owner(self):
        corpus = json.loads(BASELINE.read_text())
        tree = ast.parse(corpus["legacy_evaluate_source"])
        self.assertEqual([node.name for node in tree.body], ["evaluate"])
        for lineup in corpus["lineups"]:
            environment = dict(SERVICE_RANK=lineup["service_order"], SERVICES=corpus["service_names"])
            exec(compile(tree, "test-only-frozen-legacy", "exec"), environment)
            for case in corpus["cases"]:
                def outlet(route):
                    return SimpleNamespace(label=route["label"], via=route["service_ids"], es=route["spanish"])
                value = environment["evaluate"]([outlet(route) for route in case["listed"]],
                                                outlet(case["usual"]) if case["usual"] else None,
                                                set(lineup["selected_service_ids"]))
                expected = case["expected"][lineup["id"]]
                self.assertEqual(value, (expected["service_id"] or "", expected["basis"], expected["outlet"]))

    def test_parity_does_not_accept_dropped_or_changed_fixtures(self):
        _, _, checks = load_cases()
        check = next(value for value in checks if value["id"] == "legacy:owner")
        for mutation in ("missing", "changed", "duplicate"):
            with self.subTest(mutation=mutation):
                value = deepcopy(check["expected"])
                if mutation == "missing":
                    value["results"].pop()
                elif mutation == "duplicate":
                    value["results"][-1] = value["results"][0]
                else:
                    value["results"][0]["basis"] = "listed"
                with self.assertRaises(AssertionError):
                    assert_response(check, value)

    def test_independent_examples_check_the_entire_provider_projection(self):
        _, _, checks = load_cases()
        route = next(value for value in checks if value["projection"] == "complete"
                     and value["request"]["operation"] == "route_selection")
        changed = deepcopy(route["expected"])
        changed["results"][0]["listed_providers"] = [["unexpected-service"]]
        with self.assertRaises(AssertionError):
            assert_response(route, changed)

    def test_publisher_adapter_matches_all_four_frozen_lineups(self):
        import build
        corpus = json.loads(BASELINE.read_text())
        for lineup in corpus["lineups"]:
            with patch.object(build, "SERVICE_RANK", lineup["service_order"]), \
                    patch.object(build, "SERVICES", corpus["service_names"]):
                for case in corpus["cases"]:
                    def outlet(route):
                        return build.Outlet(label=route["label"], via=route["service_ids"], es=route["spanish"])
                    actual = build.evaluate([outlet(route) for route in case["listed"]],
                                            outlet(case["usual"]) if case["usual"] else None,
                                            set(lineup["selected_service_ids"]))
                    expected = case["expected"][lineup["id"]]
                    self.assertEqual(actual, (expected["service_id"] or "", expected["basis"], expected["outlet"]),
                                     (lineup["id"], case["id"]))

    def test_proof_rejects_stale_sources_and_corrupt_executable_artifacts(self):
        pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
        with tempfile.TemporaryDirectory() as temporary, \
                patch("outlook_runtime.source_manifest", return_value={"owner.dart": "current"}):
            directory = Path(temporary)
            for name in ("outlook", "outlook.js"):
                (directory / name).write_text("compiled test artifact")
            manifest = dict(version=1, dart=pin, sources={"owner.dart": "current"},
                            artifacts={name: sha(directory / name) for name in ("outlook", "outlook.js")})
            (directory / "runtime.json").write_text(json.dumps(manifest))
            verify_runtime(directory)
            for changed in ("sources", "outlook", "outlook.js"):
                with self.subTest(changed=changed):
                    candidate = deepcopy(manifest)
                    if changed == "sources":
                        candidate["sources"]["owner.dart"] = "old"
                    else:
                        candidate["artifacts"][changed] = "0" * 64
                    (directory / "runtime.json").write_text(json.dumps(candidate))
                    with self.assertRaises(AssertionError):
                        verify_runtime(directory)


if __name__ == "__main__":
    unittest.main()
