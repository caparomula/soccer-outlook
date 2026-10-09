"""Exercise full-build publication, including optional webmaster ownership proofs."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from html.parser import HTMLParser
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import build


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.meta, self.links = {}, {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.meta[attrs.get("name")] = attrs.get("content")
        if tag == "link":
            self.links[attrs.get("rel")] = attrs.get("href")


class PublicBuild(unittest.TestCase):
    def run_build(self, directory, fragment=False):
        args = ["build.py", "--out", str(directory / "index.html"), "--no-logos",
                "--days-back", "0", "--days-ahead", "0", "--site-url", "https://example.com/soccer/"]
        if fragment:
            args.append("--fragment")
        with patch("sys.argv", args), patch.object(build, "curl_bytes", return_value=b'{"events":[]}'), \
                patch.object(build, "fetch_standings", return_value={}), patch.object(build, "audit", return_value=[]), \
                patch.dict(build.STANDINGS, {}, clear=True), patch.object(build, "TODAY", build.TODAY), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(), 0)

    def test_full_build_links_and_publishes_every_sitemap_page_with_verification(self):
        env = {"GOOGLE_SITE_VERIFICATION": "Google_test-123", "BING_SITE_VERIFICATION": "ABC123",
               "INDEXNOW_KEY": "0123456789abcdef"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(build.os.environ, env):
            folder = Path(tmp)
            self.run_build(folder)
            root = ET.parse(folder / "sitemap.xml").getroot()
            urls = root.findall("{*}url/{*}loc")
            self.assertEqual(len(urls), 4)
            home = (folder / "index.html").read_text()
            for node in urls:
                url = node.text
                route = url.removeprefix("https://example.com/soccer/")
                page = (folder / route / "index.html").read_text()
                metadata = Document(page)
                self.assertEqual(metadata.links["canonical"], url)
                self.assertEqual(metadata.meta["google-site-verification"], env["GOOGLE_SITE_VERIFICATION"])
                self.assertEqual(metadata.meta["msvalidate.01"], env["BING_SITE_VERIFICATION"])
                self.assertNotIn("noindex", page)
                self.assertNotIn("@@", page)
                if route:
                    self.assertIn(f'href="{url}"', home)
            self.assertEqual((folder / (env["INDEXNOW_KEY"] + ".txt")).read_text().strip(), env["INDEXNOW_KEY"])
            self.assertTrue((folder / "favicon.png").exists())

    def test_fragment_leaves_metadata_and_discovery_files_to_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.run_build(folder, fragment=True)
            self.assertEqual([p.name for p in folder.iterdir()], ["index.html"])
            self.assertNotIn('rel="canonical"', (folder / "index.html").read_text())

    def test_absent_verification_and_invalid_configuration(self):
        self.assertEqual(build.verification_meta({}), "")
        for token in ('<meta name="test">', 'abc" bad="x', 'token with spaces'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                build.verification_meta({"GOOGLE_SITE_VERIFICATION": token})
        for key in ("bad/key", "short", "x" * 129):
            with self.subTest(key=key), self.assertRaises(ValueError):
                build.indexnow_key({"INDEXNOW_KEY": key})

    def test_sitemap_escapes_custom_urls_and_dates_each_page(self):
        xml = build.sitemap("https://example.com/a&b/", datetime(2026, 10, 9, tzinfo=timezone.utc), ("mls/",))
        root = ET.fromstring(xml)
        self.assertEqual([node.text for node in root.findall("{*}url/{*}loc")],
                         ["https://example.com/a&b/", "https://example.com/a&b/mls/"])
        self.assertEqual({node.text for node in root.findall("{*}url/{*}lastmod")}, {"2026-10-09T00:00:00Z"})
