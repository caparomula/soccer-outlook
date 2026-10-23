"""Producer provenance requires explicit timezones before emitting UTC stamps."""
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

import build
from tests import test_render


class ProducerReceiptStamps(unittest.TestCase):
    def test_interpret_all_rejects_naive_worker_receipt(self):
        event = test_render.Markup.event()
        quality = build.BuildQuality(event_receipts={
            ("fifa.friendly.w", event["id"]): datetime(2026, 10, 23, 13)})
        with self.assertRaisesRegex(ValueError, "aware"):
            build.interpret_all({"fifa.friendly.w": {event["id"]: event}}, quality)

    def test_companion_scopes_rejects_naive_timestamps(self):
        aware = datetime(2026, 10, 23, 13, tzinfo=ZoneInfo("America/New_York"))
        naive = aware.replace(tzinfo=None)
        for field in ("fallback_time", "attempted_at", "validated_at"):
            with self.subTest(field=field):
                quality = build.BuildQuality(sources=[
                    {"league": "eng.1", "date": "2026-10-23", "ok": True}])
                if field != "fallback_time":
                    quality.source_times[("eng.1", "2026-10-23")] = {field: naive}
                with self.assertRaisesRegex(ValueError, "aware"):
                    quality.companion_scopes(naive if field == "fallback_time" else aware)

    def test_aware_receipts_and_scope_times_preserve_the_original_instant(self):
        received = datetime(2026, 10, 23, 13, 0, 1, 123456,
                            tzinfo=ZoneInfo("America/New_York"))
        validated = received.replace(minute=5)
        event = test_render.Markup.event()
        quality = build.BuildQuality(
            sources=[{"league": "fifa.friendly.w", "date": "2026-10-23", "ok": True}],
            event_receipts={("fifa.friendly.w", event["id"]): received},
            source_times={("fifa.friendly.w", "2026-10-23"): {
                "attempted_at": received, "validated_at": validated}})
        matches = build.interpret_all({"fifa.friendly.w": {event["id"]: event}}, quality)
        self.assertEqual(matches[0].source_received_at, "2026-10-23T17:00:01.123456Z")
        scope = quality.companion_scopes(received)
        self.assertEqual(scope[0]["attempted_at"], "2026-10-23T17:00:01.123456Z")
        self.assertEqual(scope[0]["validated_at"], "2026-10-23T17:05:01.123456Z")


if __name__ == "__main__":
    unittest.main()
