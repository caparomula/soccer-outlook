"""Offline external-runtime regression: python -m tests.browser_publication_capture."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
from tests.page_fixture import BUILT_AT, render_page
from tool.companion_publication_capture import capture


class ExternalRuntimeCapture(unittest.TestCase):
    def test_fresh_external_runtime_page_initializes_with_captured_bytes_only(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            html = render_page(build, inline_runtime=False)
            self.assertIn(build.CORE_SCRIPT_TAG, html)
            # Both same-origin uncaptured assets and external resources must stay blocked.
            html = html.replace("</body>", '<img src="uncaptured.png">'
                                '<img src="https://offline-capture.invalid/probe.png"></body>')
            (site / "index.html").write_text(html)
            name = build.outlook_runtime.publish_browser(site)
            with patch.object(build.outlook_runtime, "browser_asset", side_effect=AssertionError(
                    "An offline capture must not substitute the current runtime")):
                records, responses = capture.load_browser_resources(site)
                result = capture.browser_capture(
                    site, records, responses, BUILT_AT.isoformat(),
                    "chromium", "America/New_York", 1280)
            self.assertEqual(result["page_errors"], [])
            self.assertIn(name, result["fulfilled_resources"])
            self.assertEqual({row["id"] for row in result["row_outputs"]},
                             {"finished", "live", "upcoming", "midnight", "late", "dawn", "later"})
            self.assertEqual(result["tally"], "6")  # Six selected non-final fixtures.
            finished = next(row for row in result["row_outputs"] if row["id"] == "finished")
            self.assertEqual(finished["scores"], [
                {"value": "2", "hidden": False}, {"value": "1", "hidden": False}])
            self.assertEqual(result["blocked_origins"]["https://offline-capture.invalid"], 1)
            self.assertGreaterEqual(result["blocked_origins"]["https://caparomula.github.io"], 1)
            self.assertNotIn("uncaptured.png", result["fulfilled_resources"])


if __name__ == "__main__":
    unittest.main()
