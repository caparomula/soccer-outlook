"""Independent oracle and proof-fixture integrity checks for route validity."""
from copy import deepcopy
from contextlib import redirect_stderr
from html import escape
from io import StringIO
import json
from pathlib import Path
import unittest

from tool.companion_p2_verify import assert_response
from tool.companion_validity_verify import (ALTERNATE_LINK, EXPIRY, RETAINED_LINK, RowTags, composition_checks,
                                            invalid_requests, load_checks, parse_args, stamp_routes)


class ValidityEvidence(unittest.TestCase):
    def test_ci_does_not_require_an_ignored_historical_capture(self):
        args = parse_args([])
        self.assertIsNone(args.legacy)
        self.assertFalse(args.legacy_only)
        self.assertEqual(args.browser, "both")

    def test_legacy_only_requires_an_explicit_capture(self):
        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            parse_args(["--legacy-only"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires an explicit --legacy", errors.getvalue())
        args = parse_args(["--legacy-only", "--legacy", "/tmp/frozen-page"])
        self.assertEqual(args.legacy, Path("/tmp/frozen-page"))

    def test_literal_microsecond_examples_distinguish_before_at_and_after(self):
        checks = {check["id"]: check for check in load_checks()}
        ordinary = checks["independent:retained-six-hour-boundary"]["expected"]["results"]
        fractional = checks["independent:fractional-verification-cutoff"]["expected"]["results"]
        self.assertEqual([answer["usable"] for answer in ordinary], [True, False, False])
        self.assertEqual([answer["usable"] for answer in fractional], [True, False, False])
        self.assertEqual({answer["expires_at"] for answer in ordinary}, {"2026-10-20T18:00:00.000Z"})
        self.assertEqual({answer["expires_at"] for answer in fractional}, {"2026-10-20T18:00:00.000001Z"})

    def test_oracle_rejects_legacy_inclusive_boundary_and_fraction_truncation(self):
        checks = {check["id"]: check for check in load_checks()}
        for name, mutate in (
            ("independent:retained-six-hour-boundary", lambda rows: rows[1].update(usable=True, reason=None)),
            ("independent:fractional-verification-cutoff", lambda rows: rows[0].update(usable=False, reason="expired", expires_at="2026-10-20T18:00:00.000Z")),
            ("independent:reuse-does-not-renew", lambda rows: rows[1].update(usable=True, reason=None, expires_at="2026-10-21T01:00:00.000Z")),
        ):
            with self.subTest(name=name):
                expected = deepcopy(checks[name]["expected"])
                mutate(expected["results"])
                with self.assertRaises(AssertionError):
                    assert_response(checks[name], expected)

    def test_route_composition_oracle_detects_expired_provider_leak(self):
        check = composition_checks()[0]
        leaked = deepcopy(check["expected"])
        leaked["results"][0]["listed_providers"][0] = ["espnplus"]
        with self.assertRaises(AssertionError):
            assert_response(check, leaked)
        invented = deepcopy(check["expected"])
        invented["results"][2].update(service_id="apple", basis="rule", outlet="Apple TV")
        with self.assertRaises(AssertionError):
            assert_response(check, invented)

    def test_missing_field_rejection_fixture_has_no_other_invalid_fact(self):
        checks = load_checks()
        missing = next(request for request in invalid_requests(checks)
                       if request["operation"] == "route_validity" and
                       "retention_seconds" not in request["cases"][0])
        restored = deepcopy(missing)
        restored["cases"][0]["retention_seconds"] = 21600
        self.assertEqual(restored["cases"][0], checks[0]["request"]["cases"][0])

    def test_stamping_changes_only_temporal_inputs_and_explicit_alternate(self):
        def attr(value):
            return escape(json.dumps(value), quote=True)
        listing = attr([dict(l="ESPN+", v=["espnplus"], f=0, e=0)])
        rule = attr(dict(l="Apple TV", v=["apple"]))
        script = '<script>window.preserved="original app bytes";</script>'
        html = script + ''.join(
            f'<li class="row" data-id="{key}" data-utc="2026-10-07T18:00:00Z" data-o="{listing}"><span>Preserved row</span></li>'
            for key in ("retained", "alternative"))
        html += f'<li class="row" data-id="usual" data-utc="2026-10-08T18:00:00Z" data-o="[]" data-r="{rule}">Preserved usual</li>'
        stamped = stamp_routes(html)
        self.assertTrue(stamped.startswith(script))
        self.assertEqual(stamped.count("Preserved row"), 2)
        rows = {attrs["data-id"]: attrs for _tag, attrs in RowTags(stamped).rows}
        retained = json.loads(rows["retained"]["data-o"])
        alternate = json.loads(rows["alternative"]["data-o"])
        self.assertEqual(retained[0]["t"]["verified_at"], "2026-10-07T11:00:02Z")
        self.assertEqual(retained[0]["t"]["expires_at"], EXPIRY)
        self.assertEqual(retained[0]["v"], ["espnplus"])
        self.assertEqual(len(alternate), 2)
        self.assertEqual(alternate[1]["v"], ["para"])
        self.assertIsNone(alternate[1]["t"]["expires_at"])
        self.assertEqual(json.loads(rows["retained"]["data-watch"])["ESPN+"]["espnplus"]["url"], RETAINED_LINK)
        self.assertEqual(json.loads(rows["alternative"]["data-watch"])["Paramount+"]["para"]["url"], ALTERNATE_LINK)
        self.assertEqual(json.loads(rows["usual"]["data-r"])["t"]["rights_until"], "2026-10-07")


if __name__ == "__main__":
    unittest.main()
