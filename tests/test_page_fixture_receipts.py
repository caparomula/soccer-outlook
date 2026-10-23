"""Fixture receipt defaults follow an explicitly overridden build clock."""
from datetime import timedelta
import unittest
from unittest.mock import patch

import build
from tests import page_fixture
from tool.companion_collection_capture import PublicationHTML, unique_rows


class PageFixtureReceipts(unittest.TestCase):
    def test_overridden_build_clock_retains_its_receipt_default(self):
        built_at = page_fixture.BUILT_AT + timedelta(days=4)
        with patch.object(page_fixture, "BUILT_AT", built_at):
            html = page_fixture.render_page(build)
        rows = list(unique_rows(PublicationHTML(html).rows).values())
        self.assertTrue(rows)
        self.assertEqual({row["source_received_at"] for row in rows},
                         {built_at.isoformat().replace("+00:00", "Z")})

    def test_standard_receipt_constant_and_explicit_unknown_override(self):
        html = page_fixture.render_page(build, source_receipts={"live": None})
        rows = unique_rows(PublicationHTML(html).rows)
        self.assertEqual({row["source_received_at"] for identity, row in rows.items() if identity != "live"},
                         {page_fixture.SOURCE_RECEIPT})
        self.assertIsNone(rows["live"]["source_received_at"])
