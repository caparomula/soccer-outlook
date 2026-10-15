"""Shared manual AI defaults survive rebuilds without publishing private session data."""
from contextlib import redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
from tests import test_discovery_build


def envelope(now):
    return {
        "version": 1, "created_at": now.timestamp() * 1000, "weight": 70,
        "leagues": ["eng.1"],
        "fixtures": [{"id": "123456", "kickoff": (now + timedelta(hours=1)).isoformat(), "league_id": "eng.1"}],
        "response": {
            "version": 2, "request_id": "shared-request-123",
            "headlines": [{"league_id": "eng.1", "headline": "A returning midfielder changes the contest",
                           "lede": "", "interest": 80, "match_ids": ["123456"], "sources": [],
                           "expires_at": (now + timedelta(hours=12)).isoformat()}],
            "matches": [{"id": "123456", "score": 70, "blurb": "A specific match preview.", "sources": []}],
        },
    }


class SharedAI(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source.json"
        self.output = self.root / "site"
        self.output.mkdir()
        self.destination = self.output / "shared-ai.json"
        self.now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)

    def write_source(self, value):
        raw = json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n"
        self.source.write_bytes(raw)
        return raw

    def test_fresh_defaults_survive_rebuilds_without_renewing_timestamps(self):
        raw = self.write_source(envelope(self.now))
        for hours in (0, 10, 23):
            self.assertTrue(build.write_shared_ai(self.output, self.now + timedelta(hours=hours), self.source))
            self.assertEqual(self.destination.read_bytes(), raw)
        self.assertFalse(build.write_shared_ai(self.output, self.now + timedelta(hours=24), self.source))
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), raw, "Expiry must not alter durable source data")

    def test_absent_source_removes_stale_local_output(self):
        self.destination.write_text("old output")
        self.assertFalse(build.write_shared_ai(self.output, self.now, self.source))
        self.assertFalse(self.destination.exists())

    def test_private_or_malformed_sources_warn_and_remove_stale_output(self):
        good = envelope(self.now)
        cases = {"invalid JSON": b"{", "oversized": b" " * (build.SHARED_AI_MAX_BYTES + 1),
                 "private duplicate field": json.dumps(good).replace('"weight": 70', '"weight": {"preferences": "private"}, "weight": 70').encode()}
        for label, modify in (
            ("private preferences", lambda value: value.update(preferences="Private viewing preferences")),
            ("private source snapshot", lambda value: value["fixtures"][0].update(services=["Private provider"])),
            ("wrong version", lambda value: value.update(version=2)),
            ("future timestamp", lambda value: value.update(created_at=(self.now + timedelta(minutes=6)).timestamp() * 1000)),
            ("infinite timestamp", lambda value: value.update(created_at=float("inf"))),
            ("oversized integer", lambda value: value.update(created_at=10 ** 400)),
            ("invalid weight", lambda value: value.update(weight=-1)),
            ("private nested blurb", lambda value: value["response"]["matches"][0].update(blurb={"preferences": "private"})),
            ("private nested interest", lambda value: value["response"]["headlines"][0].update(interest={"preferences": "private"})),
            ("unsafe source", lambda value: value["response"]["headlines"][0].update(sources=[{"title": "Source", "url": "javascript:alert(1)"}])),
            ("credential source", lambda value: value["response"]["headlines"][0].update(sources=[{"title": "Source", "url": "https://name:password@example.com/"}])),
            ("invalid expiry", lambda value: value["response"]["headlines"][0].update(expires_at=(self.now + timedelta(hours=25)).isoformat())),
            ("cross-league reference", lambda value: (value["leagues"].append("esp.1"), value["response"]["headlines"][0].update(league_id="esp.1"))),
            ("duplicate fixtures", lambda value: value["fixtures"].append(deepcopy(value["fixtures"][0]))),
            ("missing match", lambda value: value["response"].update(matches=[])),
        ):
            value = deepcopy(good)
            modify(value)
            cases[label] = json.dumps(value).encode()
        for label, raw in cases.items():
            with self.subTest(label=label), redirect_stderr(io.StringIO()) as error:
                self.source.write_bytes(raw)
                self.destination.write_text("old output")
                self.assertFalse(build.write_shared_ai(self.output, self.now, self.source))
                self.assertFalse(self.destination.exists())
                self.assertIn("Not published:", error.getvalue())

    def test_legacy_overview_and_calendar_only_responses_are_publishable(self):
        value = envelope(self.now)
        value["response"].update(version=1, overview={"text": "A useful overview.", "match_ids": ["123456"], "sources": []})
        del value["response"]["headlines"]
        self.write_source(value)
        self.assertTrue(build.write_shared_ai(self.output, self.now, self.source))
        value = envelope(self.now)
        value["fixtures"] = []
        value["response"]["matches"] = []
        value["response"]["headlines"][0].update(match_ids=[], sources=[{"title": "League calendar", "url": "https://example.com/calendar"}])
        self.write_source(value)
        self.assertTrue(build.write_shared_ai(self.output, self.now, self.source))

    def test_source_cannot_be_overwritten_when_output_points_to_its_directory(self):
        source = self.output / "shared-ai.json"
        raw = json.dumps(envelope(self.now)).encode()
        source.write_bytes(raw)
        with redirect_stderr(io.StringIO()):
            self.assertFalse(build.write_shared_ai(self.output, self.now, source))
        self.assertEqual(source.read_bytes(), raw)

    def test_calendar_event_period_survives_publication_without_becoming_response_expiry(self):
        value = envelope(self.now)
        item = value["response"]["headlines"][0]
        item.update(match_ids=[], sources=[{"title": "League calendar", "url": "https://example.com/calendar"}],
                    event_start=(self.now + timedelta(days=2)).isoformat(), event_end=(self.now + timedelta(days=3)).isoformat())
        raw = self.write_source(value)
        self.assertTrue(build.write_shared_ai(self.output, self.now, self.source))
        self.assertEqual(self.destination.read_bytes(), raw)
        # The event can be later than this response's expiry; it does not extend the response lifetime.
        self.assertFalse(build.write_shared_ai(self.output, self.now + timedelta(hours=24), self.source))

    def test_invalid_calendar_event_periods_are_not_published(self):
        for label, timing in (
            ("missing end", {"event_start": "2026-10-14T12:00:00Z"}),
            ("missing start", {"event_end": "2026-10-15T12:00:00Z"}),
            ("naive", {"event_start": "2026-10-14T12:00:00", "event_end": "2026-10-15T12:00:00Z"}),
            ("impossible date", {"event_start": "2026-02-30T12:00:00Z", "event_end": "2026-10-15T12:00:00Z"}),
            ("reversed", {"event_start": "2026-10-15T12:00:00Z", "event_end": "2026-10-14T12:00:00Z"}),
            ("completed", {"event_start": "2026-10-12T12:00:00Z", "event_end": "2026-10-13T12:00:00Z"}),
            ("private value", {"event_start": {"preferences": "private"}, "event_end": "2026-10-15T12:00:00Z"}),
        ):
            with self.subTest(label=label), redirect_stderr(io.StringIO()):
                value = envelope(self.now)
                value["response"]["headlines"][0].update(match_ids=[], sources=[{"title": "Calendar", "url": "https://example.com/calendar"}], **timing)
                self.write_source(value)
                self.assertFalse(build.write_shared_ai(self.output, self.now, self.source))
                self.assertFalse(self.destination.exists())

    def test_full_build_publishes_defaults_but_fragment_does_not(self):
        raw = self.write_source(envelope(datetime.now(timezone.utc)))
        with patch.object(build, "SHARED_AI_SOURCE", self.source):
            test_discovery_build.PublicBuild.run_build(self, self.output)
            self.assertEqual(self.destination.read_bytes(), raw)
            fragment = self.root / "fragment"
            test_discovery_build.PublicBuild.run_build(self, fragment, fragment=True)
            self.assertEqual([item.name for item in fragment.iterdir()], ["index.html"])


if __name__ == "__main__":
    unittest.main()
