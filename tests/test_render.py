"""The standalone page must include its assets regardless of the caller's cwd."""
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import build
from tests.page_fixture import render_page


class Tags(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class Rendering(unittest.TestCase):
    def test_document_and_fragment_include_assets_and_schedule(self):
        root = Path(build.__file__).resolve().parent
        for fragment in (False, True):
            with self.subTest(fragment=fragment):
                page = render_page(build, fragment=fragment)
                self.assertIn((root / "web/styles.css").read_text(encoding="utf-8"), page)
                self.assertIn((root / "web/app.js").read_text(encoding="utf-8"), page)
                self.assertNotRegex(page, r"@@[A-Z_]+@@")
                tags = Tags(page).tags
                rows = [attrs for tag, attrs in tags if tag == "li" and "row" in attrs.get("class", "").split()]
                self.assertEqual(len(rows), 9)
                self.assertFalse(any(tag == "script" and "src" in attrs for tag, attrs in tags))
                self.assertEqual(page.startswith("<!doctype html>"), not fragment)

    def test_loading_assets_from_another_working_directory(self):
        script = str(Path(build.__file__).resolve())
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, script, "--help"], cwd=cwd,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--fragment", result.stdout)
