"""Offline capture inputs must remain bound to the page's own runtime bytes."""
import base64
import hashlib
from pathlib import Path
import tempfile
import unittest

from tool.companion_publication_capture import capture


class BrowserResources(unittest.TestCase):
    def publication(self, site, raw=b"historical runtime, not the currently compiled build"):
        digest = hashlib.sha256(raw).digest()
        name = f"outlook-core-{digest.hex()}.js"
        integrity = "sha256-" + base64.b64encode(digest).decode()
        (site / "index.html").write_text(
            f'<script src="{name}" integrity="{integrity}"></script>')
        (site / name).write_bytes(raw)
        return name

    def test_loads_only_the_page_referenced_runtime_and_preserves_missing_json(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            name = self.publication(site)
            unrelated = "outlook-core-" + "0" * 64 + ".js"
            (site / unrelated).write_bytes(b"different runtime")
            records, responses = capture.load_browser_resources(site)
            self.assertEqual(set(responses), set(capture.FILES) | {name})
            self.assertEqual(responses[name], (site / name).read_bytes())
            self.assertEqual(records[name]["status"], 200)
            self.assertEqual(records["schedule.json"]["status"], 404)
            self.assertEqual(responses["schedule.json"], b"{}")

    def test_missing_or_changed_runtime_fails_without_selecting_another_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            name = self.publication(site)
            (site / name).unlink()
            with self.assertRaisesRegex(ValueError, "runtime is missing"):
                capture.load_browser_resources(site)
            (site / name).write_bytes(b"different runtime")
            with self.assertRaisesRegex(ValueError, "runtime changed"):
                capture.load_browser_resources(site)

    def test_reference_must_be_adjacent_and_match_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            name = self.publication(site)
            original = (site / "index.html").read_text()
            for reference in ("../" + name, "/" + name, "https://example.org/" + name,
                              name + "?version=other", name + "#other"):
                with self.subTest(reference=reference):
                    (site / "index.html").write_text(original.replace(name, reference))
                    with self.assertRaisesRegex(ValueError, "adjacent"):
                        capture.load_browser_resources(site)
            (site / "index.html").write_text(original.replace("sha256-", "sha384-"))
            with self.assertRaisesRegex(ValueError, "integrity"):
                capture.load_browser_resources(site)

    def test_runtime_symlink_cannot_escape_publication_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            site.mkdir()
            name = self.publication(site)
            (site / name).rename(root / name)
            (site / name).symlink_to(root / name)
            with self.assertRaisesRegex(ValueError, "escapes"):
                capture.load_browser_resources(site)

    def test_browser_rejects_missing_or_changed_runtime_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            name = self.publication(site)
            records, responses = capture.load_browser_resources(site)
            for invalid in (None, b"different runtime"):
                with self.subTest(invalid=invalid):
                    supplied = dict(responses)
                    if invalid is None:
                        supplied.pop(name)
                    else:
                        supplied[name] = invalid
                    with self.assertRaisesRegex(ValueError, "runtime (is missing|changed)"):
                        capture.browser_capture(site, records, supplied, "2026-10-07T17:00:00Z",
                                                "chromium", "America/New_York", 1280)

    def test_historical_inline_page_needs_no_external_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            (site / "index.html").write_text('<script>window.legacy = true;</script>')
            records, responses = capture.load_browser_resources(site)
            self.assertEqual(set(responses), set(capture.FILES))
            capture.validate_runtime_resource(records, responses)


if __name__ == "__main__":
    unittest.main()
