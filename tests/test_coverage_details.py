"""Broadcast provenance in Details stays attached to its channel and original check."""
from html.parser import HTMLParser
import unittest

import build
from tests.test_discovery import fixture


class Details(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.paragraphs = []
        self.links = []
        self.tags = []
        self.active = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "p" and attributes.get("class") == "detail__broadcast":
            self.active = []
        if tag == "a":
            self.links.append(attributes.get("href"))

    def handle_data(self, data):
        if self.active is not None:
            self.active.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.active is not None:
            self.paragraphs.append("".join(self.active))
            self.active = None


def evidence(channel="TUDN", source="TUDN", **changes):
    return dict(channel=channel, source=source, url="https://www.tudn.com/fixture",
                country="US", kind="match_listing", checked_at="2026-10-15T18:10:00+00:00", **changes)


class CoverageDetails(unittest.TestCase):
    def test_retained_listing_shows_original_time_and_retention_limit(self):
        match = fixture(league="mex.1", channel="TUDN")
        match.broadcast_evidence = [evidence(retained=True)]
        match.broadcast_check = "unavailable"
        match.broadcast_check_source = "TUDN"
        match.broadcast_check_url = "https://www.tudn.com/results"
        match.broadcast_checked_at = "2026-10-15T20:00:00+00:00"
        markup = build.detail_html(match, {})
        details = Details(markup)
        self.assertIn("TUDN · TUDN broadcast listing · originally checked Oct 15, 2026, 2:10 pm ET · saved listing",
                      details.paragraphs)
        self.assertIn("at most 6 hours from their original check", markup)
        self.assertNotIn("4:00 pm", markup)
        self.assertNotIn("usual arrangement", markup)
        self.assertIn("https://www.tudn.com/fixture", details.links)
        self.assertNotIn("https://www.tudn.com/results", details.links)

    def test_each_channel_has_its_own_sources_and_times_without_legacy_misattribution(self):
        match = fixture(league="mex.1", channel="TUDN")
        match.outlets.append(build.map_outlet("ViX", "mex.1"))
        fox = dict(evidence(source="FOX Sports"), url="https://www.foxsports.com/fixture")
        vix = dict(evidence(channel="ViX", source="ViX"), url="https://vix.com/es-es/deportes",
                   checked_at="2026-10-15T18:15:00Z")
        match.broadcast_evidence = [evidence(), fox, vix, dict(vix)]
        match.broadcast_source, match.broadcast_url = "ViX", "https://vix.com/es-es/deportes"
        details = Details(build.detail_html(match, {}))
        self.assertEqual(len(details.paragraphs), 3)
        self.assertTrue(details.paragraphs[0].startswith("TUDN · TUDN broadcast listing"))
        self.assertTrue(details.paragraphs[1].startswith("TUDN · FOX Sports broadcast listing"))
        self.assertTrue(details.paragraphs[2].startswith("ViX · ViX broadcast listing"))
        self.assertIn("2:15 pm ET", details.paragraphs[2])
        self.assertEqual(details.links.count("https://vix.com/es-es/deportes"), 1)
        self.assertFalse(any("TUDN · ViX" in paragraph for paragraph in details.paragraphs))

    def test_source_text_is_escaped_and_source_links_require_safe_https(self):
        for url in ("javascript:alert(1)", "http://www.tudn.com/fixture", "//www.tudn.com/fixture",
                    "https://user:secret@www.tudn.com/fixture", 'https://example.com/" onmouseover="bad',
                    "https://example.com/\nfixture", "https://[invalid"):
            with self.subTest(url=url):
                match = fixture(league="mex.1", channel="TUDN", link="")
                match.broadcast_evidence = [dict(evidence(source='<img src=x onerror="bad">'), url=url)]
                markup = build.detail_html(match, {})
                details = Details(markup)
                self.assertNotIn(url, details.links)
                self.assertFalse(any(tag == "img" or any(name.startswith("on") for name in attrs)
                                     for tag, attrs in details.tags))
                self.assertIn("&lt;img", markup)

    def test_wrong_country_kind_removed_channel_or_undated_retention_are_not_evidence(self):
        for change in ({"country": "MX"}, {"kind": "usual_home"}, {"channel": "ViX"},
                       {"retained": True, "checked_at": "invalid"}):
            with self.subTest(change=change):
                match = fixture(league="mex.1", channel="TUDN")
                match.broadcast_evidence = [dict(evidence(), **change)]
                details = Details(build.detail_html(match, {}))
                self.assertEqual(details.paragraphs, [])
                self.assertNotIn("https://www.tudn.com/fixture", details.links)

    def test_legacy_fallback_explanation_only_calls_a_real_fallback_usual(self):
        for channel in ("", "TUDN"):
            match = fixture(league="mex.1", channel=channel)
            match.rule = build.map_outlet("ViX", "mex.1") if not channel else None
            match.broadcast_check = "unavailable"
            match.broadcast_check_source = "ViX"
            match.broadcast_check_url = "https://vix.com/es-es/deportes"
            markup = build.detail_html(match, {})
            self.assertIn("could not be verified automatically", markup)
            self.assertEqual("Coverage shown is the usual arrangement" in markup, not bool(channel))

    def test_legacy_source_url_is_validated_too(self):
        match = fixture(league="mex.1", channel="TUDN")
        match.broadcast_source = "TUDN"
        match.broadcast_url = "javascript:alert(1)"
        self.assertNotIn(match.broadcast_url, Details(build.detail_html(match, {})).links)


if __name__ == "__main__":
    unittest.main()
