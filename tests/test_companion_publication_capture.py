"""Offline P0 publication evidence checks; not tests of a new production reader."""
import json
from pathlib import Path
import tempfile
import unittest

from tool.companion_publication_capture.capture import (
    FILES, PublicationHTML, digest, load_raw, row_fact,
)

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tool/companion_publication_capture/captures/2026-10-20.json"


class CaptureBoundaryChecks(unittest.TestCase):
    def test_unknown_kickoff_does_not_promote_placeholder_and_zero_is_preserved(self):
        html = PublicationHTML(
            '<li class="row" data-id="one" data-tv="0" data-utc="2026-10-20T00:00:00Z" '
            'data-outlook="0" data-outlook-parts=\'{"stature":0,"close":null}\' '
            'data-o="[]" data-home="A &amp; B" data-secret="not copied"></li>')
        fact = row_fact(html.rows[0])
        self.assertIsNone(fact["kickoff"])
        self.assertFalse(fact["time_confirmed"])
        self.assertEqual(fact["home"], "A & B")
        self.assertEqual(fact["outlook_total"], 0)
        self.assertEqual(fact["outlook_parts_percent"], {"stature": 0, "close": None})
        self.assertNotIn("data-secret", json.dumps(fact))

    def test_replay_refuses_changed_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            records = {}
            for name in FILES:
                (path / name).write_bytes(b"original")
                records[name] = {"sha256": digest(b"original")}
            (path / "capture-metadata.json").write_text(json.dumps(records))
            load_raw(path)
            (path / "schedule.json").write_bytes(b"altered")
            with self.assertRaisesRegex(ValueError, "Capture changed"):
                load_raw(path)


class CapturedPublicationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = json.loads(CAPTURE.read_text())

    def test_actual_publication_is_coherent_but_broadcasts_are_not_all_complete(self):
        publication = self.capture["publication"]
        self.assertEqual(publication["fixture_counts"], {"html": 360, "schedule": 360, "context": 360})
        self.assertEqual(len(set(publication["fixture_ids"])), 360)
        self.assertTrue(all(not value for value in publication["differences"].values()))
        self.assertEqual(len(set(publication["runtime_revisions"].values())), 1)
        self.assertEqual(publication["source_health"]["fixture_sources"],
                         {"requested": 306, "successful": 306, "failed": []})
        self.assertEqual(publication["source_health"]["broadcasts"]["ESPN+"]["status"], "unavailable")
        self.assertFalse(publication["source_health"]["fixture_comparison"]["complete"])
        self.assertIn("uefa.champions", publication["populated_standings_leagues"])

    def test_browser_matrix_preserves_observed_picks_and_default_text_choice(self):
        outputs = self.capture["browsers"]
        self.assertEqual(len(outputs), 6)
        all_ids = set(self.capture["publication"]["fixture_ids"])
        self.assertEqual(len(self.capture["static_browser"]["visible_fixture_ids"]), 360)
        for output in outputs:
            self.assertEqual(output["page_errors"], [])
            self.assertEqual(output["tally"], "44")
            self.assertFalse(output["defaults"]["ai_text"])
            self.assertTrue(output["headlines_hidden"])
            self.assertEqual(output["carousel"]["selected_ids"], ["401915404"])
            self.assertEqual(output["picks"]["fixture_ids"], ["401915399", "401915312", "401915313"])
            selected = set(output["nonhidden_fixture_ids"])
            self.assertEqual(len(selected), 88)
            self.assertEqual(len(output["visible_fixture_ids"]), 44)
            self.assertTrue(selected <= all_ids)
            self.assertFalse(selected & set(output["parked_fixture_ids"]))
            self.assertTrue(set(output["picks"]["fixture_ids"]) <= selected)
            self.assertEqual(output["fulfilled_resources"],
                             ["index.html", "schedule.json", "shared-ai.json", "story.json"])
        for chromium, webkit in zip(outputs[:3], outputs[3:]):
            self.assertEqual(chromium["schedule_groups"], webkit["schedule_groups"])
            self.assertEqual(chromium["picks"], webkit["picks"])
            self.assertEqual(chromium["carousel"], webkit["carousel"])

    def test_actual_legacy_score_shape_is_not_the_proposed_nullable_feed_score(self):
        scores = self.capture["representative_context_scores"]["401915399"]
        self.assertEqual(scores, {"home": "0", "away": "0"})
        rows = self.capture["representative_browser_rows"]["chromium_eastern"]
        row = next(row for row in rows if row["id"] == "401915399")
        self.assertEqual(row["scores"], [{"value": "", "hidden": True}, {"value": "", "hidden": True}])
        self.assertIn("Pick score", row["rating"])
        self.assertEqual(self.capture["editorial"]["shared-ai.json"],
                         {"http_status": 404, "present": False})

    def test_zone_changes_a_real_late_result_bucket_without_changing_total(self):
        eastern = self.capture["browsers"][0]
        pacific = self.capture["browsers"][2]
        groups = lambda output: {group["bucket"]: group["fixture_ids"]
                                 for group in output["schedule_groups"]}
        self.assertEqual(len(groups(eastern)["dayminus2"]), 12)
        self.assertEqual(len(groups(pacific)["dayminus2"]), 11)
        self.assertEqual(len(groups(eastern)["dayminus3"]), 28)
        self.assertEqual(len(groups(pacific)["dayminus3"]), 29)
        self.assertEqual(set(eastern["nonhidden_fixture_ids"]), set(pacific["nonhidden_fixture_ids"]))


if __name__ == "__main__":
    unittest.main()
