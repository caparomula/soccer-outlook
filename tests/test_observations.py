from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import observations


class SourcedObservations(unittest.TestCase):
    now = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)

    def payload(self):
        return {"version": 1, "observations": [{
            "id": "fixture-us-average", "match_id": "12345", "metric": "average_viewers",
            "value": 850000, "unit": "viewers", "observed_at": "2026-10-15T17:00:00Z",
            "source_url": "https://example.org/audience-report", "scope": "US, English-language TV only",
        }]}

    def test_measurements_keep_units_scope_and_zero_without_changing_source(self):
        data = self.payload()
        original = deepcopy(data)
        result = observations.validate(data, self.now)
        self.assertEqual(result, data["observations"])
        self.assertEqual(data, original)
        result[0]["value"] = 1
        self.assertEqual(data, original)
        data["observations"][0].update(metric="attendance", unit="people", value=0)
        self.assertEqual(observations.validate(data, self.now)[0]["value"], 0)

    def test_ambiguous_unsupported_and_future_records_are_rejected(self):
        for changes in ({"metric": "popularity"}, {"unit": "people"}, {"scope": ""},
                        {"value": True}, {"value": -1}, {"value": 1.5}, {"value": float("nan")},
                        {"source_url": "javascript:alert(1)"}, {"source_url": "https://user:secret@example.org"},
                        {"observed_at": "2026-10-16T00:00:00Z"}, {"observed_at": "2026-10-15"},
                        {"source_published_at": "2026-10-15T18:00:00Z"}):
            with self.subTest(changes=changes):
                data = self.payload()
                data["observations"][0].update(changes)
                with self.assertRaises(ValueError):
                    observations.validate(data, self.now)
        data = self.payload()
        data["observations"].append(deepcopy(data["observations"][0]))
        with self.assertRaises(ValueError):
            observations.validate(data, self.now)

    def test_video_search_and_news_counts_need_a_comparable_window(self):
        data = self.payload()
        row = data["observations"][0]
        row.update(metric="highlights_views", unit="views")
        with self.assertRaises(ValueError):
            observations.validate(data, self.now)
        row.update(window_start="2026-10-14T17:00:00Z", window_end="2026-10-15T17:00:00Z")
        self.assertEqual(observations.validate(data, self.now)[0]["window_end"], row["window_end"])
        row["window_end"] = "2026-10-16T00:00:00Z"
        with self.assertRaises(ValueError):
            observations.validate(data, self.now)

    def test_absent_optional_file_is_empty_but_invalid_file_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.json"
            self.assertEqual(observations.load(path, self.now), [])
            path.write_text(json.dumps(self.payload()))
            self.assertEqual(len(observations.load(path, self.now)), 1)
            path.write_text('{"version":1,"version":1,"observations":[]}')
            with self.assertRaises(ValueError):
                observations.load(path, self.now)


if __name__ == "__main__":
    unittest.main()
