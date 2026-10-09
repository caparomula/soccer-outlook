#!/usr/bin/env python3
"""Notify IndexNow after Pages has published the expected, validated sitemap.

The verification key is public, not an API secret. Its file lives inside the site
directory because this project shares a GitHub Pages hostname with other sites.
Only the URLs in the published sitemap are submitted; visitor filters stay local.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET


ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_SITEMAP_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 20
RETRY_DELAYS = (5, 15)
XML_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
DEFAULT_KEY_FILE = Path(__file__).resolve().parents[2] / "web/indexnow-key.txt"


class IndexNowError(Exception):
    """An actionable validation or delivery failure without key/response contents."""


class RetryableError(IndexNowError):
    """A temporary fetch failure or deployment still propagating through the CDN."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # In particular, never follow a changed verification URL to another host.
        raise IndexNowError("An HTTPS request redirected; check the production site URL.")


def _url_parts(value):
    if (not isinstance(value, str) or not value or not value.isascii()
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise IndexNowError("Site and sitemap URLs must be absolute HTTPS URLs without whitespace.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise IndexNowError("A site or sitemap URL is malformed.") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or port not in (None, 443)
            or parts.query or parts.fragment):
        raise IndexNowError("Site and sitemap URLs must use HTTPS without credentials, queries or fragments.")
    decoded_path = unquote(parts.path)
    if ("\\" in value or "\\" in decoded_path or "%" in decoded_path
            or any(c.isspace() or ord(c) < 32 for c in decoded_path)
            or any(segment in (".", "..") for segment in decoded_path.split("/"))):
        raise IndexNowError("A site or sitemap URL contains an unsafe path.")
    return parts


def site_url(value):
    parts = _url_parts(value)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") + "/", "", ""))


def sitemap_urls(document, site):
    """Parse an ordinary URL sitemap, restricted to this HTTPS project directory."""
    if not document or len(document) > MAX_SITEMAP_BYTES:
        raise IndexNowError("The sitemap is empty or exceeds the size limit.")
    if b"\x00" in document or b"<!DOCTYPE" in document.upper() or b"<!ENTITY" in document.upper():
        raise IndexNowError("The sitemap must not contain XML entity declarations.")
    try:
        root = ET.fromstring(document)
    except (ET.ParseError, LookupError, ValueError) as exc:
        raise IndexNowError("The published sitemap is not valid XML.") from exc
    prefix = "{" + XML_NAMESPACE + "}"
    if root.tag != prefix + "urlset":
        raise IndexNowError("The sitemap must be a standard URL sitemap.")
    base = _url_parts(site)
    urls, seen = [], set()
    for entry in root:
        locations = entry.findall(prefix + "loc")
        if entry.tag != prefix + "url" or len(locations) != 1 or not locations[0].text:
            raise IndexNowError("Each sitemap entry must contain exactly one URL.")
        value = locations[0].text.strip()
        parts = _url_parts(value)
        if (parts.hostname.lower() != base.hostname.lower()
                or not unquote(parts.path).startswith(unquote(base.path))):
            raise IndexNowError("The sitemap contains a URL outside the production site directory.")
        if value not in seen:
            urls.append(value)
            seen.add(value)
        if len(urls) > 10000:
            raise IndexNowError("The sitemap exceeds IndexNow's 10,000 URL limit.")
    if not urls or len(urls) > 10000:
        raise IndexNowError("The sitemap must contain between 1 and 10,000 distinct URLs.")
    return urls


def load_key(path):
    key = os.environ.get("INDEXNOW_KEY", "").strip()
    if not key:
        try:
            key = Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise IndexNowError("The IndexNow key file could not be read.") from exc
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", key):
        raise IndexNowError("The IndexNow key must contain 8–128 letters, digits or hyphens.")
    return key


def request_bytes(request, *, max_bytes, accepted=(200,)):
    """Use Python's verified HTTPS defaults and reject redirects and oversized files."""
    try:
        with build_opener(NoRedirects()).open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read(max_bytes + 1)
    except HTTPError as exc:
        if exc.code == 429:
            raise IndexNowError("The server rate limited the request; a later deployment can try again.") from exc
        if exc.code >= 500 or (exc.code == 404 and request.get_method() == "GET"):
            raise RetryableError(f"The server returned HTTP {exc.code}; publication may still be propagating.") from exc
        raise IndexNowError(f"The server rejected the request (HTTP {exc.code}).") from exc
    except (URLError, OSError) as exc:
        raise RetryableError("An HTTPS request failed or timed out.") from exc
    if status not in accepted:
        raise IndexNowError(f"The server returned an unexpected status (HTTP {status}).")
    if len(body) > max_bytes:
        raise IndexNowError("The server response exceeds the size limit.")
    return status, body


def published_urls(site, key, expected=None):
    # A digest query avoids a cached previous sitemap without changing any URL
    # submitted to search engines. Exact bytes still have to match the deployment.
    query = "?indexnow=" + hashlib.sha256(expected).hexdigest()[:16] if expected else ""
    headers = {"User-Agent": "Soccer-Outlook-IndexNow/1.0", "Cache-Control": "no-cache"}
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            _, key_body = request_bytes(Request(site + key + ".txt" + query, headers=headers), max_bytes=512)
            if key_body.strip() != key.encode("ascii"):
                raise RetryableError("The published verification key does not match the configured key.")
            _, document = request_bytes(Request(site + "sitemap.xml" + query, headers=headers),
                                        max_bytes=MAX_SITEMAP_BYTES)
            if expected is not None and document != expected:
                raise RetryableError("The public sitemap does not match the completed deployment yet.")
            return sitemap_urls(document, site)
        except RetryableError:
            if attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def submit(site, key, urls):
    payload = {"host": urlsplit(site).hostname, "key": key,
               "keyLocation": site + key + ".txt", "urlList": urls}
    request = Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8",
                               "User-Agent": "Soccer-Outlook-IndexNow/1.0"}, method="POST")
    # Submit once. A failed/limited notification can wait for the next successful
    # deployment; it must not delay or invalidate an otherwise healthy website.
    status, _ = request_bytes(request, max_bytes=65536, accepted=(200, 202))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", required=True, help="The production HTTPS site directory")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE,
                        help="Public verification key; INDEXNOW_KEY overrides this file")
    parser.add_argument("--expected-sitemap", type=Path,
                        help="Sitemap from the completed deployment, checked against the public copy")
    args = parser.parse_args(argv)
    try:
        site = site_url(args.site_url)
        key = load_key(args.key_file)
        if key is None:
            print("IndexNow skipped: no verification key is configured.")
            return 0
        expected = None
        if args.expected_sitemap:
            try:
                expected = args.expected_sitemap.read_bytes()
            except OSError as exc:
                raise IndexNowError("The deployment's expected sitemap could not be read.") from exc
            sitemap_urls(expected, site)
        urls = published_urls(site, key, expected)
        status = submit(site, key, urls)
        outcome = "accepted" if status == 200 else "received for verification"
        print(f"IndexNow: {len(urls)} URL(s) {outcome}. Search engines decide whether and when to index them.")
        return 0
    except IndexNowError as exc:
        print(f"IndexNow notification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
