"""Explicit foreign catalogues must not be mistaken for temporary source outages."""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import unittest

import apple_listings
import broadcast_listings
import build
import paramount_listings
import vix_listings
from tests.test_ligamx_listings import VIX_SAMPLE, match as vix_match, payload, vix_page
from tests.test_other_listings import APPLE, apple_page, game as other_match
from tests.test_paramount_listings import US_PAGE, match as paramount_match, page as paramount_page


class CountryRetention(unittest.TestCase):
    def cases(self):
        apple_foreign = deepcopy(APPLE)
        apple_foreign["data"][0]["intent"]["storefront"] = "ca"
        apple_canonical = deepcopy(APPLE)
        apple_canonical["data"][0]["data"]["canonicalURL"] = apple_listings.SCHEDULE_URL.replace("/us/", "/ca/")
        apple_missing = deepcopy(APPLE)
        del apple_missing["data"][0]["intent"]["storefront"]
        vix_foreign = payload(VIX_SAMPLE)
        vix_foreign["props"]["initialState"]["requestCountryCode"] = "MX"
        vix_missing = payload(VIX_SAMPLE)
        del vix_missing["props"]["initialState"]["requestCountryCode"]
        return [
            ("Apple TV", other_match("apple"), apple_listings.SCHEDULE_URL,
             {apple_listings.SCHEDULE_URL: apple_page()},
             [apple_page(apple_foreign), apple_page(apple_canonical)], apple_page(apple_missing)),
            ("Paramount+", paramount_match(), paramount_listings.SCHEDULE_URL,
             {paramount_listings.SCHEDULE_URL: US_PAGE,
              paramount_listings.EVENTS_URL.format(offset=0): paramount_page()},
             [US_PAGE.replace(b"property: 'US'", b"property: 'CA'"),
              US_PAGE.replace(b"locale: 'en-us'", b"locale: 'en-ca'"),
              US_PAGE.replace(b"international: false", b"international: true")],
             US_PAGE.replace(b"property: 'US', ", b"")),
            ("ViX", vix_match(), vix_listings.SCHEDULE_URL,
             {vix_listings.SCHEDULE_URL: VIX_SAMPLE}, [vix_page(vix_foreign)], vix_page(vix_missing)),
        ]

    def confirmed(self, source, original, pages):
        # First obtain real, timestamped evidence through the adapter and orchestration.
        now = original.utc - timedelta(hours=2)
        previous = deepcopy(original)
        previous.rule = None
        broadcast_listings.enrich(build, [previous], pages.get, now - timedelta(hours=1))
        self.assertEqual([outlet.label for outlet in previous.outlets], [source])
        self.assertEqual(previous.broadcast_evidence[0]["country"], "US")
        record = asdict(previous)
        record["utc"] = previous.utc.isoformat()
        return now, record

    def refresh(self, source, original, pages, url, response, now, record):
        current = deepcopy(original)
        current.rule = None
        responses = {**pages, url: response}
        broadcast_listings.enrich(build, [current], responses.get, now)
        check = next(item for item in current.broadcast_checks if item["source"] == source)
        retained = broadcast_listings.retain_recent_evidence(build, [current], [record], now)
        return current, check, retained

    def test_explicit_foreign_catalogue_blocks_retention_after_real_enrichment(self):
        for source, original, url, pages, foreign, _ in self.cases():
            now, record = self.confirmed(source, original, pages)
            for response in foreign:
                with self.subTest(source=source, response=response[:100]):
                    current, check, retained = self.refresh(source, original, pages, url, response, now, record)
                    self.assertEqual((check["status"], check["reason"]), ("unavailable", "wrong_country"))
                    self.assertEqual(retained["retained_channels"], 0)
                    self.assertFalse(current.outlets)
                    self.assertFalse(current.broadcast_evidence)

    def test_missing_country_malformed_page_and_transport_failure_remain_bounded_outages(self):
        for source, original, url, pages, _, missing_country in self.cases():
            now, record = self.confirmed(source, original, pages)
            for response in (missing_country, b"malformed response", None):
                with self.subTest(source=source, response=response):
                    current, check, retained = self.refresh(source, original, pages, url, response, now, record)
                    self.assertEqual(check["reason"], "fetch_or_parse_failed")
                    self.assertEqual(retained["retained_channels"], 1)
                    self.assertEqual([outlet.label for outlet in current.outlets], [source])
                    self.assertTrue(current.broadcast_evidence[0]["retained"])
                    self.assertEqual(current.broadcast_evidence[0]["checked_at"],
                                     record["broadcast_evidence"][0]["checked_at"])


if __name__ == "__main__":
    unittest.main()
