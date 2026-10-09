"""IndexNow only announces URLs whose verification file and deployment are public."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("indexnow", ROOT / ".github/scripts/indexnow.py")
indexnow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(indexnow)
SITE = "https://example.github.io/soccer/"
KEY = "example-public-key-1234"


def sitemap(*urls):
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f'<urlset xmlns="{indexnow.XML_NAMESPACE}">{entries}</urlset>'.encode()


class IndexNow(unittest.TestCase):
    def test_urls_are_limited_to_the_production_https_directory(self):
        good = sitemap(SITE, SITE + "leagues/mls/", SITE)
        self.assertEqual(indexnow.sitemap_urls(good, SITE), [SITE, SITE + "leagues/mls/"])
        for value in ("http://example.github.io/soccer/", "https://other.example/soccer/",
                      "https://example.github.io/soccer-other/", "https://example.github.io/",
                      SITE + "../other/", SITE + "%2e%2e/other/", SITE + "%252e%252e/other/",
                      SITE + "x?filter=all", SITE + "#tag", "https://user@example.github.io/soccer/",
                      "https://example.github.io:8080/soccer/", SITE + "x%5cy/", SITE + "x%0ay/"):
            with self.subTest(value=value), self.assertRaises(indexnow.IndexNowError):
                indexnow.sitemap_urls(sitemap(value), SITE)

    def test_malformed_unsupported_empty_and_entity_sitemaps_are_rejected(self):
        for document in (b"<unfinished", b"<html>404</html>", sitemap(), b"",
                         b'<?xml version="1.0" encoding="unknown"?>' + sitemap(SITE),
                         b'<?xml version="1.0" encoding="UTF-7"?>' + sitemap(SITE),
                         b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url/></urlset>',
                         b'<!DOCTYPE x [<!ENTITY y "bad">]>' + sitemap(SITE),
                         b"x" * (indexnow.MAX_SITEMAP_BYTES + 1)):
            with self.subTest(document=document[:60]), self.assertRaises(indexnow.IndexNowError):
                indexnow.sitemap_urls(document, SITE)

    def test_site_url_normalization_and_invalid_settings(self):
        self.assertEqual(indexnow.site_url(SITE.rstrip("/")), SITE)
        for value in ("/soccer/", "http://example.com/", SITE + "?x=1", SITE + "#x",
                      "https://example.com:invalid/", "https://[unfinished/", "https://example.com/x y/"):
            with self.subTest(value=value), self.assertRaises(indexnow.IndexNowError):
                indexnow.site_url(value)

    def test_key_file_environment_override_and_missing_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(indexnow.os.environ, {}, clear=True):
            path = Path(folder) / "key.txt"
            self.assertIsNone(indexnow.load_key(path))
            path.write_text(KEY + "\n")
            self.assertEqual(indexnow.load_key(path), KEY)
            with patch.dict(indexnow.os.environ, {"INDEXNOW_KEY": "other-valid-key"}):
                self.assertEqual(indexnow.load_key(path), "other-valid-key")
            for invalid in ("short", "with spaces and no", "bad/slash/key", "a" * 129):
                path.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(indexnow.IndexNowError):
                    indexnow.load_key(path)

    def test_public_key_location_and_expected_sitemap_are_verified(self):
        expected = sitemap(SITE, SITE + "leagues/mls/")
        with patch.object(indexnow, "request_bytes", side_effect=[(200, KEY.encode()), (200, expected)]) as request:
            self.assertEqual(indexnow.published_urls(SITE, KEY, expected), [SITE, SITE + "leagues/mls/"])
        self.assertTrue(request.call_args_list[0].args[0].full_url.startswith(SITE + KEY + ".txt?indexnow="))
        self.assertTrue(request.call_args_list[1].args[0].full_url.startswith(SITE + "sitemap.xml?indexnow="))

    def test_mismatched_key_or_stale_sitemap_never_get_submitted(self):
        expected = sitemap(SITE)
        cases = [[(200, b"wrong-key")] * 3,
                 [(200, KEY.encode()), (200, sitemap(SITE + "old/"))] * 3]
        for responses in cases:
            with self.subTest(responses=responses), patch.object(indexnow, "request_bytes", side_effect=responses), \
                    patch.object(indexnow.time, "sleep") as sleep, self.assertRaises(indexnow.RetryableError):
                indexnow.published_urls(SITE, KEY, expected)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 15])

    def test_temporary_publication_failure_retries_but_rate_limits_do_not(self):
        expected = sitemap(SITE)
        with patch.object(indexnow, "request_bytes", side_effect=[indexnow.RetryableError("HTTP 404"),
                          (200, KEY.encode()), (200, expected)]), patch.object(indexnow.time, "sleep") as sleep:
            self.assertEqual(indexnow.published_urls(SITE, KEY, expected), [SITE])
            sleep.assert_called_once_with(5)
        with patch.object(indexnow, "request_bytes", side_effect=indexnow.IndexNowError("rate limited")) as request, \
                patch.object(indexnow.time, "sleep") as sleep, self.assertRaises(indexnow.IndexNowError):
            indexnow.published_urls(SITE, KEY, expected)
        request.assert_called_once()
        sleep.assert_not_called()

    def test_successful_200_and_pending_202_submit_the_project_key_location(self):
        for status in (200, 202):
            with self.subTest(status=status), patch.object(indexnow, "request_bytes", return_value=(status, b"")) as request:
                self.assertEqual(indexnow.submit(SITE, KEY, [SITE]), status)
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, "https://api.indexnow.org/indexnow")
            self.assertEqual(sent.get_method(), "POST")
            self.assertEqual(json.loads(sent.data), {"host": "example.github.io", "key": KEY,
                                                    "keyLocation": SITE + KEY + ".txt", "urlList": [SITE]})
            self.assertEqual(request.call_args.kwargs["accepted"], (200, 202))

    def test_transport_uses_timeout_and_rejects_bad_statuses_and_large_responses(self):
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b"okay"
        with patch.object(indexnow, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(indexnow.request_bytes(Request(SITE), max_bytes=4), (200, b"okay"))
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 20)
            response.read.return_value = b"too-long"
            with self.assertRaises(indexnow.IndexNowError):
                indexnow.request_bytes(Request(SITE), max_bytes=4)
            for status in (400, 403, 422, 429, 500, 503):
                opener.return_value.open.side_effect = HTTPError(SITE, status, "private response", None, None)
                with self.subTest(status=status), self.assertRaises(indexnow.IndexNowError) as raised:
                    indexnow.request_bytes(Request(SITE), max_bytes=4)
                self.assertNotIn("private response", str(raised.exception))
                if status == 429:
                    self.assertNotIsInstance(raised.exception, indexnow.RetryableError)
            opener.return_value.open.side_effect = URLError("connection failed")
            with self.assertRaises(indexnow.RetryableError):
                indexnow.request_bytes(Request(SITE), max_bytes=4)

    def test_redirects_are_never_followed(self):
        with self.assertRaises(indexnow.IndexNowError):
            indexnow.NoRedirects().redirect_request(Request(SITE), None, 302, "redirect", {}, "https://elsewhere.example/")

    def test_main_skips_missing_key_and_rejects_bad_expected_sitemap_before_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(indexnow.os.environ, {}, clear=True), \
                patch.object(indexnow, "request_bytes") as request, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            key_path = Path(folder) / "key.txt"
            args = ["--site-url", SITE, "--key-file", str(key_path)]
            self.assertEqual(indexnow.main(args), 0)
            key_path.write_text(KEY)
            expected = Path(folder) / "sitemap.xml"
            expected.write_bytes(sitemap("https://other.example/"))
            self.assertEqual(indexnow.main(args + ["--expected-sitemap", str(expected)]), 1)
            request.assert_not_called()

    def test_main_failure_does_not_log_the_verification_key(self):
        stderr = io.StringIO()
        with patch.dict(indexnow.os.environ, {"INDEXNOW_KEY": KEY}), \
                patch.object(indexnow, "published_urls", side_effect=indexnow.IndexNowError("Published key mismatch.")), \
                patch.object(indexnow, "submit") as submit, redirect_stderr(stderr):
            self.assertEqual(indexnow.main(["--site-url", SITE]), 1)
        self.assertNotIn(KEY, stderr.getvalue())
        submit.assert_not_called()

    def test_main_checks_deployed_files_before_successful_submission(self):
        stdout = io.StringIO()
        expected = sitemap(SITE)
        with tempfile.TemporaryDirectory() as folder, patch.dict(indexnow.os.environ, {"INDEXNOW_KEY": KEY}), \
                patch.object(indexnow, "request_bytes", side_effect=[(200, KEY.encode()), (200, expected), (202, b"")]) as request, \
                redirect_stdout(stdout):
            path = Path(folder) / "sitemap.xml"
            path.write_bytes(expected)
            self.assertEqual(indexnow.main(["--site-url", SITE, "--expected-sitemap", str(path)]), 0)
        self.assertEqual([call.args[0].get_method() for call in request.call_args_list], ["GET", "GET", "POST"])
        self.assertIn("received for verification", stdout.getvalue())
        self.assertNotIn(KEY, stdout.getvalue())
