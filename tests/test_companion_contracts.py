"""P0 draft examples, not a production parser, exporter, or domain implementation."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
    from referencing import Registry, Resource
except ImportError:
    Draft202012Validator = None


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/outlook/v1"
EXAMPLES = CONTRACT / "examples"
CASES = ROOT / "tests/contracts/schedules"
MANIFEST_LIMIT = 64 * 1024
ARTIFACT_LIMIT = 8 * 1024 * 1024
FORMATS = FormatChecker() if Draft202012Validator else None

if FORMATS:
    @FORMATS.checks("date-time", raises=(ValueError, OverflowError))
    def valid_instant(value):
        # jsonschema's optional RFC3339 dependency is not installed by its base wheel.
        return not isinstance(value, str) or datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).utcoffset() is not None


def decode(raw, limit):
    """Test harness for parser requirements JSON Schema cannot express."""
    if len(raw) > limit:
        raise ValueError("decoded byte limit")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite number")
        return result

    def constant(value):
        raise ValueError("non-JSON numeric constant")

    return json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                      parse_constant=constant)


def id_set(items):
    values = [item["id"] for item in items]
    if len(set(values)) != len(values):
        raise ValueError("duplicate ID")
    return set(values)


def require_refs(values, known):
    if not set(values) <= known:
        raise ValueError("broken reference")


def verify_artifact(raw, descriptor):
    digest = hashlib.sha256(raw).hexdigest()
    if (len(raw) != descriptor["bytes"] or digest != descriptor["sha256"] or
            descriptor["path"] != f"objects/{digest}.json"):
        raise ValueError("artifact integrity")
    data = decode(raw, ARTIFACT_LIMIT)
    if data["content_revision"] != descriptor["content_revision"]:
        raise ValueError("artifact revision mismatch")
    return data


@unittest.skipUnless(Draft202012Validator, "install tests/contracts/requirements.txt")
class CompanionDraftContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {path.stem.removesuffix(".schema"): json.loads(path.read_text())
                       for path in CONTRACT.glob("*.schema.json")}
        cls.registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema))
            for schema in cls.schemas.values()
        )

    def validate(self, kind, data):
        Draft202012Validator(
            self.schemas[kind], registry=self.registry, format_checker=FORMATS
        ).validate(data)

    def publication(self):
        manifest = decode((EXAMPLES / "manifest.json").read_bytes(), MANIFEST_LIMIT)
        self.validate("manifest", manifest)
        artifacts = {}
        for kind, descriptor in manifest["artifacts"].items():
            if descriptor is None:
                continue
            raw = (EXAMPLES / descriptor["path"]).read_bytes()
            data = verify_artifact(raw, descriptor)
            self.validate(kind, data)
            self.assertEqual(data["content_revision"], descriptor["content_revision"])
            artifacts[kind] = data
        return manifest, artifacts

    def references(self, artifacts):
        """Cross-record fixture checks only; no viewer eligibility or ranking."""
        catalog, schedule, policy = (artifacts[key] for key in ("catalog", "schedule", "policy"))
        leagues, teams, networks, plans, assets = (
            id_set(catalog[key]) for key in ("leagues", "teams", "networks", "service_plans", "assets")
        )
        self.assertEqual(schedule["catalog_revision"], catalog["content_revision"])
        self.assertEqual(schedule["policy_id"], policy["policy_id"])
        for key, known in (("league_order", leagues), ("enabled_league_ids", leagues),
                           ("service_plan_order", plans), ("enabled_service_plan_ids", plans)):
            require_refs(policy["defaults"][key], known)
        for migration in catalog["service_plan_migrations"]:
            # An old saved ID may be absent from today's catalog; its target may not.
            require_refs([migration["to"]], plans)
        fixtures = id_set(schedule["fixtures"])
        fixtures_by_id = {fixture["id"]: fixture for fixture in schedule["fixtures"]}
        evidence_ids, destination_ids, vitals = (
            id_set(schedule[key]) for key in ("evidence", "destinations", "team_vitals")
        )
        evidence = {item["id"]: item for item in schedule["evidence"]}
        for collection in ("leagues", "teams", "networks"):
            for item in catalog[collection]:
                if item["asset_id"] is not None:
                    require_refs([item["asset_id"]], assets)
        for scope in schedule["source_scopes"]:
            require_refs([scope["league_id"]], leagues)
            require_refs(scope["covered_dates"], set(scope["requested_dates"]))
            if not scope["complete"] or set(scope["requested_dates"]) != set(scope["covered_dates"]):
                raise ValueError("incomplete required fixture collection")
        for item in evidence.values():
            require_refs([item["fixture_id"]], fixtures)
            require_refs(item["team_ids"], teams)
        route_ids = []
        for fixture in schedule["fixtures"]:
            require_refs([fixture["league_id"]], leagues)
            require_refs([fixture["home_team_id"], fixture["away_team_id"]], teams)
            if fixture["home_team_id"] == fixture["away_team_id"]:
                raise ValueError("same team on both sides")
            require_refs(fixture["network_ids"], networks)
            for key in ("home_vitals_id", "away_vitals_id"):
                if fixture["context"][key] is not None:
                    require_refs([fixture["context"][key]], vitals)
            require_refs(fixture["links"]["destination_ids"], destination_ids)
            table_league = fixture["links"]["table_league_id"]
            if table_league is not None:
                require_refs([table_league], leagues)
                if table_league != fixture["league_id"]:
                    raise ValueError("table link targets a different fixture league")
            for route in fixture["routes"]:
                route_ids.append({"id": route["id"]})
                require_refs([route["network_id"]], set(fixture["network_ids"]))
                require_refs(route["eligible_service_plan_ids"], plans)
                require_refs(route["excluded_service_plan_ids"], plans)
                require_refs(route["destination_ids"], destination_ids)
                require_refs(route["evidence_ids"], evidence_ids)
                if set(route["eligible_service_plan_ids"]) & set(route["excluded_service_plan_ids"]):
                    raise ValueError("excluded plan is eligible")
                for evidence_id in route["evidence_ids"]:
                    observed = evidence[evidence_id]
                    if (observed["fixture_id"] != fixture["id"] or
                            set(observed["team_ids"]) != {fixture["home_team_id"], fixture["away_team_id"]} or
                            observed["kickoff"] != fixture["timing"]["kickoff"] or
                            observed["country"] != route["country"] or
                            observed["verdict"] != "accepted" or observed["classification"] != "live_match"):
                        raise ValueError("route references rejected or mismatched evidence")
        id_set(route_ids)
        for item in schedule["destinations"]:
            require_refs(item["network_ids"], networks)
            require_refs(item["service_plan_ids"], plans)
            if item["fixture_id"] is not None:
                require_refs([item["fixture_id"]], fixtures)
        editorial = artifacts.get("editorial")
        if editorial:
            self.assertEqual(editorial["catalog_revision"], catalog["content_revision"])
            self.assertEqual(editorial["policy_id"], policy["policy_id"])
            for slot in (editorial["automatic"], editorial["shared"]):
                if slot is None:
                    continue
                id_set(slot["headlines"] + slot["matches"])
                for item in slot["headlines"] + slot["matches"]:
                    require_refs([item["league_id"]], leagues)
                    references = item["fixture_refs"]
                    referenced_ids = [reference["fixture_id"] for reference in references]
                    require_refs(referenced_ids, fixtures)
                    if len(set(referenced_ids)) != len(referenced_ids):
                        raise ValueError("duplicate editorial fixture reference")
                    for reference in references:
                        fixture = fixtures_by_id[reference["fixture_id"]]
                        if (reference["kickoff"] != fixture["timing"]["kickoff"] or
                                item["league_id"] != fixture["league_id"]):
                            raise ValueError("editorial fixture binding mismatch")

    def test_schemas_are_valid_draft_2020_12(self):
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)

    def test_complete_example_publication_has_real_integrity_descriptors(self):
        _, artifacts = self.publication()
        self.references(artifacts)

    def test_representative_schedules_validate(self):
        _, artifacts = self.publication()
        artifacts.pop("editorial")
        for path in sorted(CASES.glob("*.json")):
            with self.subTest(case=path.name):
                schedule = decode(path.read_bytes(), ARTIFACT_LIMIT)
                self.validate("schedule", schedule)
                self.references(dict(artifacts, schedule=schedule))

    def test_case_meanings_are_not_lost_in_normalization(self):
        load = lambda name: json.loads((CASES / f"{name}.json").read_text())
        self.assertEqual(load("complete-empty")["fixtures"], [])
        self.assertTrue(load("complete-empty")["fixture_sources_complete"])
        self.assertEqual(load("partial-broadcast")["broadcast_sources"][0]["state"], "partial")
        retained = load("partial-broadcast")["fixtures"][0]["routes"][0]
        self.assertEqual(retained["verified_at"], "2026-10-20T12:00:00Z")
        self.assertEqual(retained["expires_at"], "2026-10-20T18:00:00Z")
        self.assertIsNone(load("unknown-kickoff")["fixtures"][0]["timing"]["kickoff"])
        self.assertIsNone(load("unknown-kickoff")["fixtures"][0]["timing"]["scheduled_date_zone"])
        self.assertEqual(load("missing-scores")["fixtures"][0]["status"]["scores"]["match"],
                         {"home": 0, "away": None})
        excluded = load("excluded-route")["fixtures"][0]["routes"][0]
        self.assertEqual(excluded["eligible_service_plan_ids"], ["yttvspanishplus"])
        self.assertEqual(excluded["excluded_service_plan_ids"], ["yttv"])
        replay = load("replay")
        self.assertEqual(replay["fixtures"][0]["routes"], [])
        self.assertEqual(replay["evidence"][0]["classification"], "replay")
        self.assertEqual(load("unknown-field")["fixtures"][0]["status"]["state"], "unknown")
        self.assertEqual(load("usual-coverage")["fixtures"][0]["routes"][0]["basis"], "usual")

    def test_null_editorial_is_valid_but_omission_is_not(self):
        manifest, _ = self.publication()
        manifest["artifacts"]["editorial"] = None
        self.validate("manifest", manifest)
        del manifest["artifacts"]["editorial"]
        with self.assertRaises(ValidationError):
            self.validate("manifest", manifest)

    def test_additive_optional_fields_are_accepted(self):
        manifest, artifacts = self.publication()
        manifest["future_diagnostic"] = "unrecognized label"
        self.validate("manifest", manifest)
        for kind, value in artifacts.items():
            value["future_diagnostic"] = "unrecognized label"
            self.validate(kind, value)

    def test_personalized_fields_are_not_public_contract_fields(self):
        _, artifacts = self.publication()
        for field in ("viewer_preferences", "personal_ai", "final_pick_score", "rating_dots",
                      "personalized_picks"):
            schedule = deepcopy(artifacts["schedule"])
            schedule[field] = {}
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.validate("schedule", schedule)

    def test_unsafe_manifest_descriptors_are_rejected(self):
        manifest, _ = self.publication()
        for path in ("https://example.org/file.json", "../file.json", "/objects/file.json",
                     "objects/../file.json", "objects/" + "a" * 64 + ".json?token=secret"):
            candidate = deepcopy(manifest)
            candidate["artifacts"]["schedule"]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValidationError):
                self.validate("manifest", candidate)

    def test_corrupted_bytes_or_descriptor_fail_integrity(self):
        manifest, _ = self.publication()
        descriptor = manifest["artifacts"]["schedule"]
        raw = (EXAMPLES / descriptor["path"]).read_bytes()
        with self.assertRaises(ValueError):
            verify_artifact(raw + b" ", descriptor)
        for field, value in (("sha256", "0" * 64), ("bytes", len(raw) + 1),
                             ("content_revision", "unrelated-revision")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_artifact(raw, dict(descriptor, **{field: value}))

    def test_editorial_keeps_original_kickoff_across_schedule_revisions(self):
        _, artifacts = self.publication()
        schedule = artifacts["schedule"]
        schedule["content_revision"] = "schedule-refreshed-same-fixture"
        # An unrelated refresh does not invalidate still-matching editorial.
        self.references(artifacts)
        schedule["fixtures"][0]["timing"]["kickoff"] = "2026-10-21T18:00:00Z"
        schedule["evidence"][0]["kickoff"] = "2026-10-21T18:00:00Z"
        with self.assertRaisesRegex(ValueError, "editorial fixture binding mismatch"):
            self.references(artifacts)

    def test_editorial_binding_requires_original_kickoff_even_when_unknown(self):
        _, artifacts = self.publication()
        record = artifacts["editorial"]["shared"]["matches"][0]
        reference = record["fixture_refs"][0]
        del reference["kickoff"]
        with self.assertRaises(ValidationError):
            self.validate("editorial", artifacts["editorial"])
        reference["kickoff"] = None
        self.validate("editorial", artifacts["editorial"])
        with self.assertRaisesRegex(ValueError, "editorial fixture binding mismatch"):
            self.references(artifacts)
        artifacts["schedule"]["fixtures"][0]["timing"].update(confirmed=False, kickoff=None)
        artifacts["schedule"]["evidence"][0]["kickoff"] = None
        self.references(artifacts)

    def test_calendar_headlines_require_a_source_and_event_period(self):
        _, artifacts = self.publication()
        slot = artifacts["editorial"]["shared"]
        headline = deepcopy(slot["matches"][0])
        headline.update(id="shared:example-response:calendar-1", kind="headline",
                        fixture_refs=[], headline="Example sourced calendar notice",
                        rating_total=None, interest=50,
                        event_period={"start": "2026-10-21T12:00:00Z",
                                      "end_exclusive": "2026-10-21T13:00:00Z"})
        slot["headlines"] = [headline]
        with self.assertRaises(ValidationError):
            self.validate("editorial", artifacts["editorial"])
        headline["sources"] = [{"title": "Example calendar source",
                                "url": "https://example.org/calendar"}]
        self.validate("editorial", artifacts["editorial"])
        self.references(artifacts)
        headline["event_period"] = None
        with self.assertRaises(ValidationError):
            self.validate("editorial", artifacts["editorial"])

    def test_strict_decode_rejects_duplicate_keys_and_nonfinite_values(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e9999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode(raw, ARTIFACT_LIMIT)

    def test_payload_limits_apply_before_json_decoding(self):
        for limit in (MANIFEST_LIMIT, ARTIFACT_LIMIT):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                decode(b" " * (limit + 1), limit)

    def test_unsupported_schema_and_invalid_dates_fail(self):
        _, artifacts = self.publication()
        for change in (
            lambda value: value.update(schema_version=2),
            lambda value: value.update(fixture_sources_complete=False),
            lambda value: value["fixtures"][0]["timing"].update(kickoff="2026-02-30T12:00:00Z"),
            lambda value: value["fixtures"][0]["timing"].update(kickoff="2026-10-20T12:00:00-04:00"),
            lambda value: value["fixtures"][0]["timing"].update(confirmed=False),
            lambda value: value["fixtures"][0]["status"]["scores"]["match"].update(home="0"),
            lambda value: value["fixtures"][0]["outlook"].update(total=101),
            lambda value: value["fixtures"][0]["links"].update(match_page="https://name:password@example.org/"),
        ):
            candidate = deepcopy(artifacts["schedule"])
            change(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                self.validate("schedule", candidate)

    def test_broken_references_duplicate_ids_and_replay_routes_fail(self):
        _, artifacts = self.publication()
        for change in (
            lambda value: value["fixtures"].append(deepcopy(value["fixtures"][0])),
            lambda value: value["fixtures"][0].update(home_team_id="missing:team"),
            lambda value: value["fixtures"][0]["routes"][0].update(eligible_service_plan_ids=["missing:plan"]),
            lambda value: value["evidence"][0].update(classification="replay"),
            lambda value: value["evidence"][0].update(verdict="rejected"),
            lambda value: value["fixtures"][0]["routes"][0].update(excluded_service_plan_ids=["yttv"]),
            lambda value: value["source_scopes"][0].update(covered_dates=[], complete=False),
            lambda value: value["fixtures"][0]["links"].update(table_league_id="missing:league"),
        ):
            candidate = deepcopy(artifacts)
            change(candidate["schedule"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.references(candidate)

    def test_service_migration_requires_a_known_target(self):
        _, artifacts = self.publication()
        artifacts["catalog"]["service_plan_migrations"] = [
            {"from": "retired:example-plan", "to": "missing:plan", "equivalent": True}
        ]
        self.validate("catalog", artifacts["catalog"])
        with self.assertRaisesRegex(ValueError, "broken reference"):
            self.references(artifacts)
        artifacts["catalog"]["service_plan_migrations"][0]["to"] = "yttv"
        self.references(artifacts)

    def test_unknown_coverage_cannot_contain_eligible_plans(self):
        _, artifacts = self.publication()
        schedule = artifacts["schedule"]
        route = schedule["fixtures"][0]["routes"][0]
        route["basis"] = "unknown"
        with self.assertRaises(ValidationError):
            self.validate("schedule", schedule)
        route["eligible_service_plan_ids"] = []
        self.validate("schedule", schedule)

    def test_unknown_progress_does_not_delete_a_valid_route(self):
        schedule = json.loads((CASES / "unknown-field.json").read_text())
        self.validate("schedule", schedule)
        self.assertEqual(schedule["fixtures"][0]["routes"][0]["eligible_service_plan_ids"], ["yttv"])

    def test_fixture_count_limit(self):
        schedule = json.loads((CASES / "normal.json").read_text())
        schedule["fixtures"] *= 3001
        with self.assertRaises(ValidationError):
            self.validate("schedule", schedule)


if __name__ == "__main__":
    unittest.main()
