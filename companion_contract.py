"""Strict local v1 feed validation, independent of producer and test helpers.

Schema IDs are resolved only from the checked-in registry. No validation path
fetches a remote schema or source data.
"""
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


CONTRACT = Path(__file__).resolve().parent / "contracts/outlook/v1"
MANIFEST_LIMIT = 64 * 1024
ARTIFACT_LIMIT = 8 * 1024 * 1024
MAX_SAFE_INTEGER = 9007199254740991
MAX_DEPTH = 64
KINDS = ("catalog", "schedule", "policy", "editorial")
FORMATS = FormatChecker()


class ContractError(ValueError):
    """Malformed, inconsistent, or unsafe public data."""


@FORMATS.checks("date-time", raises=(ValueError, OverflowError))
def _valid_instant(value):
    return not isinstance(value, str) or datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ).utcoffset() is not None


def _json_values(value, depth=1):
    if depth > MAX_DEPTH:
        raise ContractError("JSON nesting limit")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ContractError("integer exceeds interoperable range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("non-finite number")
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("object key is not a string")
        for child in value.values():
            _json_values(child, depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _json_values(child, depth + 1)
        return
    raise ContractError("value is not JSON")


def encode(value, limit=ARTIFACT_LIMIT):
    """Deterministic strict UTF-8 JSON, including its single terminal newline."""
    try:
        _json_values(value)
        raw = (json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ContractError(str(error)) from error
    if len(raw) > limit:
        raise ContractError("decoded byte limit")
    return raw


def decode(raw, limit=ARTIFACT_LIMIT):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ContractError("decoded byte limit or non-byte input")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("duplicate object key")
            result[key] = value
        return result

    def constant(_):
        raise ContractError("non-JSON numeric constant")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=constant)
        _json_values(value)
        # A JSON escaped lone surrogate is not representable in interoperable UTF-8.
        encode(value, limit)
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ContractError(str(error)) from error


def read_bytes(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"missing or unsafe publication file: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ContractError("decoded byte limit")
    return raw


@lru_cache(maxsize=1)
def _validators():
    schemas = {path.stem.removesuffix(".schema"): decode(path.read_bytes())
               for path in CONTRACT.glob("*.schema.json")}
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {kind: Draft202012Validator(schema, registry=registry, format_checker=FORMATS)
            for kind, schema in schemas.items()}


def validate(kind, value):
    encode(value, MANIFEST_LIMIT if kind == "manifest" else ARTIFACT_LIMIT)
    try:
        _validators()[kind].validate(value)
    except ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path)
        raise ContractError(f"{kind}/{location}: {error.message}") from error


def _ids(items):
    values = [item["id"] for item in items]
    if len(set(values)) != len(values):
        raise ContractError("duplicate ID")
    return set(values)


def _refs(values, known):
    if not set(values) <= known:
        raise ContractError("broken reference")


def validate_references(artifacts):
    """Validate public identity bindings, never viewer entitlement or ranking."""
    catalog, schedule, policy = (artifacts[key] for key in ("catalog", "schedule", "policy"))
    leagues, teams, networks, plans, assets = (
        _ids(catalog[key]) for key in ("leagues", "teams", "networks", "service_plans", "assets")
    )
    if (schedule["catalog_revision"] != catalog["content_revision"] or
            schedule["policy_id"] != policy["policy_id"]):
        raise ContractError("schedule catalog or policy binding mismatch")
    for key, known in (("league_order", leagues), ("enabled_league_ids", leagues),
                       ("service_plan_order", plans), ("enabled_service_plan_ids", plans)):
        _refs(policy["defaults"][key], known)
    for migration in catalog["service_plan_migrations"]:
        _refs([migration["to"]], plans)
    fixtures = _ids(schedule["fixtures"])
    fixtures_by_id = {fixture["id"]: fixture for fixture in schedule["fixtures"]}
    evidence_ids, destination_ids, vitals = (
        _ids(schedule[key]) for key in ("evidence", "destinations", "team_vitals")
    )
    evidence = {item["id"]: item for item in schedule["evidence"]}
    vitals_by_id = {item["id"]: item for item in schedule["team_vitals"]}
    destinations = {item["id"]: item for item in schedule["destinations"]}
    for item in vitals_by_id.values():
        _refs([item["team_id"]], teams)
        _refs([item["league_id"]], leagues)
        if item["rank"] is not None and item["rank"]["position"] > item["rank"]["table_size"]:
            raise ContractError("standing rank exceeds its table size")
    for collection in ("leagues", "teams", "networks"):
        for item in catalog[collection]:
            if item["asset_id"] is not None:
                _refs([item["asset_id"]], assets)
    for scope in schedule["source_scopes"]:
        _refs([scope["league_id"]], leagues)
        _refs(scope["covered_dates"], set(scope["requested_dates"]))
        if not scope["complete"] or set(scope["requested_dates"]) != set(scope["covered_dates"]):
            raise ContractError("incomplete required fixture collection")
    scoped_leagues = {scope["league_id"] for scope in schedule["source_scopes"]}
    horizon = schedule["confirmed_horizon"]
    if horizon is not None and (
        datetime.fromisoformat(horizon["start"].replace("Z", "+00:00")) >=
        datetime.fromisoformat(horizon["end_exclusive"].replace("Z", "+00:00"))
    ):
        raise ContractError("confirmed horizon is not an increasing interval")
    for item in evidence.values():
        _refs([item["fixture_id"]], fixtures)
        _refs(item["team_ids"], teams)
    route_ids = []
    for fixture in schedule["fixtures"]:
        _refs([fixture["league_id"]], leagues)
        _refs([fixture["league_id"]], scoped_leagues)
        _refs([fixture["home_team_id"], fixture["away_team_id"]], teams)
        if fixture["home_team_id"] == fixture["away_team_id"]:
            raise ContractError("same team on both sides")
        _refs(fixture["network_ids"], networks)
        for key, team_key in (("home_vitals_id", "home_team_id"), ("away_vitals_id", "away_team_id")):
            if fixture["context"][key] is not None:
                _refs([fixture["context"][key]], vitals)
                item = vitals_by_id[fixture["context"][key]]
                if item["team_id"] != fixture[team_key] or item["league_id"] != fixture["league_id"]:
                    raise ContractError("fixture vitals binding mismatch")
        _refs(fixture["links"]["destination_ids"], destination_ids)
        for destination_id in fixture["links"]["destination_ids"]:
            if destinations[destination_id]["fixture_id"] not in (None, fixture["id"]):
                raise ContractError("fixture destination binding mismatch")
        table_league = fixture["links"]["table_league_id"]
        if table_league is not None:
            _refs([table_league], leagues)
            if table_league != fixture["league_id"]:
                raise ContractError("table link targets a different fixture league")
        for route in fixture["routes"]:
            route_ids.append({"id": route["id"]})
            _refs([route["network_id"]], set(fixture["network_ids"]))
            _refs(route["eligible_service_plan_ids"], plans)
            _refs(route["excluded_service_plan_ids"], plans)
            _refs(route["destination_ids"], destination_ids)
            for destination_id in route["destination_ids"]:
                destination = destinations[destination_id]
                if (destination["fixture_id"] not in (None, fixture["id"]) or
                        route["network_id"] not in destination["network_ids"]):
                    raise ContractError("route destination binding mismatch")
            _refs(route["evidence_ids"], evidence_ids)
            if set(route["eligible_service_plan_ids"]) & set(route["excluded_service_plan_ids"]):
                raise ContractError("excluded plan is eligible")
            for evidence_id in route["evidence_ids"]:
                observed = evidence[evidence_id]
                if (observed["fixture_id"] != fixture["id"] or
                        set(observed["team_ids"]) != {fixture["home_team_id"], fixture["away_team_id"]} or
                        observed["kickoff"] != fixture["timing"]["kickoff"] or
                        observed["country"] != route["country"] or
                        observed["verdict"] != "accepted" or observed["classification"] != "live_match"):
                    raise ContractError("route references rejected or mismatched evidence")
    _ids(route_ids)
    for item in schedule["destinations"]:
        _refs(item["network_ids"], networks)
        _refs(item["service_plan_ids"], plans)
        if item["fixture_id"] is not None:
            _refs([item["fixture_id"]], fixtures)
    editorial = artifacts.get("editorial")
    if editorial is not None:
        if (editorial["catalog_revision"] != catalog["content_revision"] or
                editorial["policy_id"] != policy["policy_id"]):
            raise ContractError("editorial catalog or policy binding mismatch")
        for slot in (editorial["automatic"], editorial["shared"]):
            if slot is None:
                continue
            _ids(slot["headlines"] + slot["matches"])
            for item in slot["headlines"] + slot["matches"]:
                _refs([item["league_id"]], leagues)
                references = item["fixture_refs"]
                referenced_ids = [reference["fixture_id"] for reference in references]
                _refs(referenced_ids, fixtures)
                if len(set(referenced_ids)) != len(referenced_ids):
                    raise ContractError("duplicate editorial fixture reference")
                for reference in references:
                    fixture = fixtures_by_id[reference["fixture_id"]]
                    if (reference["kickoff"] != fixture["timing"]["kickoff"] or
                            item["league_id"] != fixture["league_id"]):
                        raise ContractError("editorial fixture binding mismatch")


def validate_artifacts(artifacts):
    if not isinstance(artifacts, dict) or set(artifacts) != set(KINDS):
        raise ContractError("expected catalog, schedule, policy, and explicit editorial")
    for kind in KINDS:
        if kind == "editorial" and artifacts[kind] is None:
            continue
        validate(kind, artifacts[kind])
    validate_references(artifacts)


def verify_artifact(raw, descriptor):
    digest = hashlib.sha256(raw).hexdigest()
    if (len(raw) != descriptor["bytes"] or digest != descriptor["sha256"] or
            descriptor["path"] != f"objects/{digest}.json"):
        raise ContractError("artifact integrity mismatch")
    data = decode(raw)
    if (not isinstance(data, dict) or
            data.get("content_revision") != descriptor["content_revision"] or
            data.get("schema_version") != descriptor["schema_version"]):
        raise ContractError("artifact revision mismatch")
    return data


def validate_publication(api_dir, manifest=None):
    """Read a complete local snapshot with bounded reads; return manifest, artifacts."""
    root = Path(api_dir)
    if root.is_symlink() or (root / "objects").is_symlink():
        raise ContractError("unsafe publication directory")
    if manifest is None:
        manifest = decode(read_bytes(root / "manifest.json", MANIFEST_LIMIT), MANIFEST_LIMIT)
    validate("manifest", manifest)
    artifacts = {}
    for kind in KINDS:
        descriptor = manifest["artifacts"][kind]
        artifacts[kind] = (None if descriptor is None else verify_artifact(
            read_bytes(root / descriptor["path"], ARTIFACT_LIMIT), descriptor))
    validate_artifacts(artifacts)
    return manifest, artifacts
