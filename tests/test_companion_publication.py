"""P1 publication durability and independent malformed-input regressions."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import companion_contract as contract
    import companion_publication as publication
except ModuleNotFoundError as error:
    if error.name not in ("jsonschema", "referencing"):
        raise
    contract = publication = None


EXAMPLES = Path(__file__).resolve().parents[1] / "contracts/outlook/v1/examples"
NOW = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
PRODUCER = "9" * 40


def artifacts():
    manifest = json.loads((EXAMPLES / "manifest.json").read_text())
    return {kind: json.loads((EXAMPLES / descriptor["path"]).read_text())
            for kind, descriptor in manifest["artifacts"].items()}


def concurrent_publish(directory):
    return publication.publish(directory, artifacts(), published_at=NOW,
                               producer_revision=PRODUCER)["publication_revision"]


@unittest.skipUnless(publication, "install tests/requirements-companion.txt")
class CompanionPublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "api/v1"
        self.input = artifacts()

    def publish(self, value=None, when=NOW, **kwargs):
        return publication.publish(self.root, self.input if value is None else value,
                                   published_at=when, producer_revision=PRODUCER, **kwargs)

    def changed(self, marker):
        result = deepcopy(self.input)
        result["schedule"]["content_revision"] = f"schedule-{marker}"
        result["schedule"]["fixtures"][0]["context"]["notes"].append(marker)
        return result

    def test_first_publication_has_independently_checked_bytes_and_no_input_mutation(self):
        before = deepcopy(self.input)
        result = self.publish()
        self.assertEqual(result["publication_revision"], 1)
        self.assertEqual(result["published_at"], "2026-10-20T16:00:00Z")
        self.assertEqual(self.input, before)
        loaded, output = contract.validate_publication(self.root)
        self.assertEqual(loaded, result)
        self.assertEqual(output, self.input)
        self.assertEqual(json.loads((self.root / publication.STATE).read_text())["highest_revision"], 1)

    def test_concurrent_process_writers_allocate_distinct_ordered_revisions(self):
        with ProcessPoolExecutor(max_workers=4) as pool:
            revisions = list(pool.map(concurrent_publish, [str(self.root)] * 8))
        self.assertEqual(sorted(revisions), list(range(1, 9)))
        manifest, _, highest = publication._load_tree(self.root)
        self.assertEqual(manifest["publication_revision"], 8)
        self.assertEqual(highest, 8)

    def test_precommit_failure_preserves_current_snapshot_and_burns_revision(self):
        first = self.publish()
        original = (self.root / "manifest.json").read_bytes()
        write = publication._atomic_write

        def fail_manifest(path, raw):
            if path.name == "manifest.json":
                raise OSError("injected manifest write failure")
            return write(path, raw)

        with patch.object(publication, "_atomic_write", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "injected"):
                self.publish(self.changed("next"))
        self.assertEqual((self.root / "manifest.json").read_bytes(), original)
        self.assertEqual(contract.validate_publication(self.root)[0], first)
        self.assertEqual(self.publish()["publication_revision"], 3)

    def test_object_failure_never_changes_current_pointer(self):
        first = self.publish()
        with patch.object(publication, "_immutable_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.publish(self.changed("new"))
        self.assertEqual(contract.validate_publication(self.root)[0], first)

    def test_required_failure_is_rejected_before_any_mutation(self):
        first = self.publish()
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        broken = deepcopy(self.input)
        broken["schedule"]["source_scopes"][0]["complete"] = False
        with self.assertRaises(contract.ContractError):
            self.publish(broken)
        self.assertEqual({path.relative_to(self.root): path.read_bytes()
                          for path in self.root.rglob("*") if path.is_file()}, before)
        self.assertEqual(contract.validate_publication(self.root)[0], first)

    def test_incomplete_required_collection_never_publishes_empty_replacement(self):
        self.publish()
        broken = deepcopy(self.input)
        broken["schedule"]["fixtures"] = []
        broken["schedule"]["fixture_sources_complete"] = False
        with self.assertRaises(contract.ContractError):
            self.publish(broken)
        self.assertEqual(contract.validate_publication(self.root)[0]["publication_revision"], 1)

    def test_invalid_optional_editorial_is_explicitly_withdrawn_without_blocking_schedule(self):
        self.publish()
        changed = self.changed("fresh")
        changed["editorial"]["catalog_revision"] = "missing-catalog"
        result = self.publish(changed)
        self.assertEqual(result["publication_revision"], 2)
        self.assertIsNone(result["artifacts"]["editorial"])
        self.assertEqual(result["diagnostics"][0]["code"], "editorial_withdrawn")
        self.assertEqual(contract.validate_publication(self.root)[1]["schedule"], changed["schedule"])

    def test_null_editorial_is_a_valid_withdrawal_but_missing_field_is_invalid(self):
        value = dict(self.input, editorial=None)
        self.assertIsNone(self.publish(value)["artifacts"]["editorial"])
        del value["editorial"]
        with self.assertRaises(contract.ContractError):
            self.publish(value)

    def test_rejected_optional_input_never_leaks_into_public_diagnostics_or_objects(self):
        value = deepcopy(self.input)
        sentinel = "private-sentinel-review-canary"
        value["editorial"]["shared"]["matches"][0]["sources"] = [
            {"title": "source", "url": f"https://review-canary:{sentinel}@example.org/"}]
        result = self.publish(value)
        self.assertIsNone(result["artifacts"]["editorial"])
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(sentinel.encode(), path.read_bytes(), str(path))

    def test_republication_and_rollback_keep_source_ages_and_use_higher_revision(self):
        first = self.publish()
        self.publish(self.changed("new"), when=NOW + timedelta(hours=1))
        rollback = self.publish(when=NOW + timedelta(hours=2))
        self.assertEqual(rollback["publication_revision"], 3)
        self.assertEqual(rollback["artifacts"], first["artifacts"])
        self.assertEqual(rollback["content_revision"], first["content_revision"])
        self.assertEqual(contract.validate_publication(self.root)[1], self.input)

    def test_restore_complete_prior_state_and_reserved_revision(self):
        self.publish()
        publication._reserve(self.root, 9)
        destination = Path(self.temporary.name) / "restored"
        restored = publication.restore_previous(destination, self.root)
        self.assertEqual(restored["publication_revision"], 1)
        result = publication.publish(destination, self.input, published_at=NOW,
                                     producer_revision=PRODUCER)
        self.assertEqual(result["publication_revision"], 10)

    def test_publish_accepts_previous_api_directory(self):
        self.publish()
        destination = Path(self.temporary.name) / "next"
        result = publication.publish(destination, self.input, published_at=NOW,
                                     producer_revision=PRODUCER, previous_dir=self.root)
        self.assertEqual(result["publication_revision"], 2)
        self.assertEqual(len(list((destination / publication.HISTORY).glob("*.json"))), 1)

    def test_restore_does_not_rewind_newer_destination(self):
        self.publish()
        destination = Path(self.temporary.name) / "next"
        publication.restore_previous(destination, self.root)
        newer = publication.publish(destination, self.changed("new"), published_at=NOW,
                                    producer_revision=PRODUCER)
        self.assertEqual(publication.restore_previous(destination, self.root), newer)

    def test_same_revision_different_content_is_a_restore_conflict(self):
        self.publish()
        destination = Path(self.temporary.name) / "conflict"
        publication.publish(destination, self.changed("different"), published_at=NOW,
                            producer_revision=PRODUCER)
        with self.assertRaisesRegex(contract.ContractError, "conflicting"):
            publication.restore_previous(destination, self.root)

    def test_corrupt_durable_counter_is_not_silently_reset(self):
        first = self.publish()
        for raw in (b'{"highest_revision":0,"schema_version":1}', b'{broken',
                    b'{"highest_revision":true,"schema_version":1}'):
            with self.subTest(raw=raw):
                (self.root / publication.STATE).write_bytes(raw)
                with self.assertRaises(contract.ContractError):
                    self.publish()
                self.assertEqual(contract.validate_publication(self.root)[0], first)

    def test_corrupt_prior_object_or_history_fails_closed(self):
        first = self.publish()
        self.publish(self.changed("later"))
        history = publication._history_path(self.root, first)
        original = history.read_bytes()
        history.write_bytes(b"{}")
        destination = Path(self.temporary.name) / "rejected"
        with self.assertRaises(contract.ContractError):
            publication.restore_previous(destination, self.root)
        self.assertFalse((destination / "manifest.json").exists())
        history.write_bytes(original)
        (self.root / first["artifacts"]["schedule"]["path"]).write_bytes(b"corrupt")
        with self.assertRaises(contract.ContractError):
            publication.restore_previous(destination, self.root)
        self.assertFalse((destination / "manifest.json").exists())

    def test_retention_keeps_current_previous_and_exact_seven_day_boundary(self):
        first = self.publish(self.changed("day0"), when=NOW)
        second = self.publish(self.changed("day1"), when=NOW + timedelta(days=1))
        third = self.publish(self.changed("day7"), when=NOW + timedelta(days=7))
        fourth = self.publish(self.changed("day8"), when=NOW + timedelta(days=8))
        self.assertFalse((self.root / first["artifacts"]["schedule"]["path"]).exists())
        for manifest in (second, third, fourth):
            self.assertTrue((self.root / manifest["artifacts"]["schedule"]["path"]).exists())
        fifth = self.publish(self.changed("day20"), when=NOW + timedelta(days=20))
        _, retained, _ = publication._load_tree(self.root)
        self.assertEqual(set(retained), {fourth["publication_revision"], fifth["publication_revision"]})
        self.assertFalse((self.root / third["artifacts"]["schedule"]["path"]).exists())

    def test_collection_failure_reports_committed_success_and_keeps_usable_snapshot(self):
        self.publish()
        with patch.object(publication, "_collect", side_effect=OSError("deferred cleanup")):
            with self.assertWarnsRegex(RuntimeWarning, "committed; retention deferred"):
                result = self.publish(self.changed("new"))
        self.assertEqual(contract.validate_publication(self.root)[0], result)

    def test_interrupted_first_attempt_can_retry_with_a_new_revision(self):
        write = publication._atomic_write
        def fail_manifest(path, raw):
            if path.name == "manifest.json":
                raise OSError("injected")
            return write(path, raw)
        with patch.object(publication, "_atomic_write", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.publish()
        self.assertFalse((self.root / "manifest.json").exists())
        self.assertEqual(self.publish()["publication_revision"], 2)

    def test_symlinked_object_directory_is_rejected(self):
        self.root.mkdir(parents=True)
        (self.root / "objects").symlink_to(self.temporary.name, target_is_directory=True)
        with self.assertRaises(contract.ContractError):
            self.publish()
        self.assertFalse((self.root / "manifest.json").exists())

    def test_explicit_time_and_exact_producer_revision_are_required(self):
        for when, revision in ((NOW.replace(tzinfo=None), PRODUCER), (NOW, "main")):
            with self.assertRaises(contract.ContractError):
                publication.publish(self.root, self.input, published_at=when,
                                    producer_revision=revision)
        self.assertFalse(self.root.exists())

    def test_strict_json_rejects_ambiguous_or_unbounded_input(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}',
                    b'{"x":9007199254740992}', b'{"x":"\\ud800"}',
                    b'\xff', '{}'.encode('utf-16'), b'[' * 65 + b']' * 65):
            with self.subTest(raw=raw[:50]), self.assertRaises(contract.ContractError):
                contract.decode(raw)
        with self.assertRaises(contract.ContractError):
            contract.decode(b" " * (contract.MANIFEST_LIMIT + 1), contract.MANIFEST_LIMIT)
        for value in ({1: "not a string key"}, {"x": float("inf")}):
            with self.assertRaises(contract.ContractError):
                contract.encode(value)

    def test_missing_and_mismatched_references_are_rejected(self):
        def mismatched_vitals(value):
            fixture = value["schedule"]["fixtures"][0]
            value["schedule"]["team_vitals"].append({
                "id": "vitals:wrong-team", "team_id": fixture["away_team_id"],
                "league_id": fixture["league_id"], "observation": {
                    "source": "espn", "observed_at": "2026-10-20T12:00:00Z",
                    "provider_updated_at": None},
                "form": [], "record": None, "rank": None, "hometown": None, "leader": None,
            })
            fixture["context"]["home_vitals_id"] = "vitals:wrong-team"

        mutations = [
            lambda a: a["schedule"]["fixtures"][0].update(league_id="missing"),
            lambda a: a["schedule"].update(catalog_revision="other"),
            lambda a: a["schedule"]["fixtures"].append(deepcopy(a["schedule"]["fixtures"][0])),
            lambda a: a["schedule"]["confirmed_horizon"].update(end_exclusive=a["schedule"]["confirmed_horizon"]["start"]),
            mismatched_vitals,
        ]
        for mutate in mutations:
            value = deepcopy(self.input)
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(contract.ContractError):
                contract.validate_artifacts(value)

    def test_fixture_requires_a_complete_source_scope_for_its_league(self):
        value = deepcopy(self.input)
        league = deepcopy(value["catalog"]["leagues"][0])
        league["id"] = "other-league"
        value["catalog"]["leagues"].append(league)
        value["schedule"]["source_scopes"][0]["league_id"] = league["id"]
        with self.assertRaises(contract.ContractError):
            contract.validate_artifacts(value)


if __name__ == "__main__":
    unittest.main()
