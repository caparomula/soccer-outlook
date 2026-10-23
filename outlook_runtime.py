"""Verified local adapters to shared Dart source facts and domain rules.

Prepare artifacts with tool/build_outlook_runtime.py. There is no Python rule
fallback: a missing/stale runtime fails before generating a mixed-rule page.
"""
import atexit
import base64
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
from threading import RLock
from time import monotonic


ROOT = Path(__file__).resolve().parent
_lock = RLock()
_process = None
_pending = b""
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 15


def source_manifest(root=ROOT):
    root = Path(root)
    names = ["companion-toolchain.json", "tool/build_outlook_runtime.py"]
    for package in ("packages/outlook_core", "packages/outlook_data", "tool/outlook_bridge"):
        names += [f"{package}/pubspec.yaml", f"{package}/pubspec.lock"]
        for directory in ("lib", "bin", "web"):
            names += [str(path.relative_to(root)) for path in
                      (root / package / directory).rglob("*.dart")]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(names)}


@lru_cache(maxsize=1)
def artifacts():
    directory = Path(os.environ.get("OUTLOOK_RUNTIME_DIR", ROOT / "work/outlook-runtime")).resolve()
    try:
        manifest = json.loads((directory / "runtime.json").read_text())
        if manifest["sources"] != source_manifest():
            raise ValueError("source fingerprint differs")
        for name in ("outlook", "outlook.js"):
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest["artifacts"][name]:
                raise ValueError(f"{name} digest differs")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError("Shared Outlook runtime missing or stale; run "
                           "python tool/build_outlook_runtime.py --dart dart") from error
    return directory


def browser_script():
    return (artifacts() / "outlook.js").read_text(encoding="utf-8")


def browser_asset():
    raw = (artifacts() / "outlook.js").read_bytes()
    digest = hashlib.sha256(raw).digest()
    return f"outlook-core-{digest.hex()}.js", "sha256-" + base64.b64encode(digest).decode(), raw


def publish_browser(directory):
    """Install the immutable script before a page can reference its name."""
    name, _, raw = browser_asset()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".outlook-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary, directory / name)
    finally:
        temporary.unlink(missing_ok=True)
    return name


def close():
    global _process, _pending
    with _lock:
        process, _process, _pending = _process, None, b""
        if process is not None:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()


atexit.register(close)


def evaluate(request):
    """One bounded batch over a serialized, persistent native CLI process."""
    global _process, _pending
    payload = json.dumps(request, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(payload) > MAX_BYTES:
        raise ValueError("Shared Outlook request exceeds limit")
    with _lock:
        if _process is None:
            _process = subprocess.Popen([str(artifacts() / "outlook"), "--serve"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(_process.stdin.fileno(), False)
            os.set_blocking(_process.stdout.fileno(), False)
        try:
            remaining = memoryview(payload)
            deadline = monotonic() + TIMEOUT_SECONDS
            with selectors.DefaultSelector() as selector:
                selector.register(_process.stdin, selectors.EVENT_WRITE)
                selector.register(_process.stdout, selectors.EVENT_READ)
                while remaining or b"\n" not in _pending:
                    timeout = deadline - monotonic()
                    if timeout <= 0:
                        raise RuntimeError("Shared Outlook runtime timed out")
                    events = selector.select(timeout=timeout)
                    if not events:
                        raise RuntimeError("Shared Outlook runtime timed out")
                    for key, _ in events:
                        if key.fileobj is _process.stdin:
                            try:
                                written = os.write(_process.stdin.fileno(), remaining[:65536])
                            except BlockingIOError:
                                continue
                            if not written:
                                raise RuntimeError("Shared Outlook runtime closed its request stream")
                            remaining = remaining[written:]
                            if not remaining:
                                selector.unregister(_process.stdin)
                        else:
                            try:
                                part = os.read(_process.stdout.fileno(), 65536)
                            except BlockingIOError:
                                continue
                            if not part:
                                raise RuntimeError("Shared Outlook runtime exited without a result")
                            _pending += part
                            if len(_pending) > MAX_BYTES:
                                raise RuntimeError("Shared Outlook response exceeds limit")
            line, _pending = _pending.split(b"\n", 1)
            result = json.loads(line)
            if not isinstance(result, dict) or result.get("version") != 1 or "error" in result:
                raise RuntimeError("Shared Outlook runtime rejected the request")
            return result
        except (OSError, ValueError, RuntimeError):
            close()
            raise


@lru_cache(maxsize=4096)
def _routes(encoded):
    result = evaluate(json.loads(encoded))["results"]
    if len(result) != 1 or result[0]["id"] != "route":
        raise RuntimeError("Shared Outlook route result does not match request")
    return json.dumps(result[0], separators=(",", ":"))


def utc_stamp(value):
    """Preserve original microseconds when mapping an aware instant to the wire."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware route evaluation instant is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def routes(listed, usual, selected, order, names, *, now=None):
    """Map already-qualified producer facts; cache identical immutable batches."""
    request = {"version": 1, "operation": "route_selection",
               "lineup": {"selected_service_ids": sorted(set(selected)),
                          "service_order": list(order), "service_names": dict(names)},
               "cases": [{"id": "route", "listed": listed, "usual": usual}]}
    if now is not None:
        request["now_utc"] = utc_stamp(now)
    return json.loads(_routes(json.dumps(request, sort_keys=True, separators=(",", ":"))))


@lru_cache(maxsize=4096)
def _route_validity(encoded):
    values = evaluate(json.loads(encoded))["results"]
    if len(values) != 1 or values[0]["id"] != "validity":
        raise RuntimeError("Shared Outlook validity result does not match request")
    value = values[0]
    if value["expires_at"] is not None:
        value["expires_at"] = utc_stamp(datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")))
    return json.dumps(value, separators=(",", ":"))


def route_validity(facts, now):
    """Read shared temporal decisions without caching mutable caller objects."""
    request = {"version": 1, "operation": "route_validity", "cases": [
        dict(facts, id="validity", now_utc=utc_stamp(now))]}
    return json.loads(_route_validity(json.dumps(request, sort_keys=True, separators=(",", ":"))))


def fixture_windows(cases):
    """Project explicit fixture facts in one shared batch, without a local clock.

    UTC strings pass through protocol validation. Aware datetime inputs preserve
    their original microseconds; caller-owned mappings are never modified.
    Python's static and AI candidate-window policies do not call this adapter.
    """
    inputs = []
    for case in cases:
        value = dict(case)
        for field in ("now_utc", "kickoff_utc", "received_at"):
            if isinstance(value.get(field), datetime):
                value[field] = utc_stamp(value[field])
        inputs.append(value)
    values = evaluate({"version": 1, "operation": "fixture_window", "cases": inputs})["results"]
    if len(values) != len(inputs) or [value.get("id") for value in values] != [case.get("id") for case in inputs]:
        raise RuntimeError("Shared Outlook fixture window identities do not match request")
    return values


def _evaluate_status(encoded):
    values = evaluate(json.loads(encoded))["results"]
    if len(values) != 1 or values[0].get("id") != "status":
        raise RuntimeError("Shared Outlook status result identity does not match request")
    return json.dumps(values[0], separators=(",", ":"))


@lru_cache(maxsize=4096)
def _status(encoded):
    return _evaluate_status(encoded)


def _status_request(operation, facts):
    request = {"version": 1, "operation": operation, "cases": [dict(facts, id="status")]}
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # Arbitrary provider metadata may be large. Retain at most 16 KiB per key;
    # larger valid frames still reach the decoder unchanged, without caching.
    evaluate_status = _status if len(encoded.encode("utf-8")) <= 16384 else _evaluate_status
    return json.loads(evaluate_status(encoded))


def espn_status(event_status, competition_status, home_score, away_score):
    """Decode original source fields; cache immutable inputs, never caller objects."""
    return _status_request("espn_status", dict(event_status=event_status,
        competition_status=competition_status, home_score=home_score, away_score=away_score))


def fixture_status(phase, label, home_score, away_score, state=None):
    """Project direct/rehydrated fixture facts through the same shared owner."""
    facts = dict(phase=phase, label=label, home_score=home_score, away_score=away_score)
    if state is not None:
        facts["state"] = state
    return _status_request("fixture_status", facts)


@lru_cache(maxsize=64)
def _default_leagues(defaults):
    """Cache the immutable publisher preference projection, including policy."""
    values = evaluate({"version": 1, "operation": "viewer_eligibility", "cases": [{
        "id": "defaults",
        "leagues": [{"id": key, "default_followed": value} for key, value in defaults],
        "preferences": {"choices": {}, "paused": [], "solo_id": None},
        "has_selected_services": False, "fixtures": [],
    }]})["results"]
    if len(values) != 1 or values[0]["id"] != "defaults":
        raise RuntimeError("Shared Outlook league result does not match request")
    leagues = values[0]["leagues"]
    if [value["id"] for value in leagues] != [key for key, _ in defaults]:
        raise RuntimeError("Shared Outlook league identities do not match request")
    return tuple((value["id"], value["followed"]) for value in leagues)


def default_followed(league_id, defaults):
    """Read the shared default-view decision without retaining mutable policy."""
    return dict(_default_leagues(tuple(defaults.items()))).get(league_id, False)


@lru_cache(maxsize=256)
def calendar_shift(instant, time_zone, offset_days):
    """Return the shared shifted boundary as an aware UTC datetime."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("An aware calendar instant is required")
    # The bridge uses millisecond instants. Truncation preserves which side of
    # an exact local 04:00 boundary a Python microsecond instant lies on.
    stamp = instant.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    result = evaluate({"version": 1, "operation": "calendar_shift", "cases": [
        {"id": "boundary", "instant": stamp, "time_zone": time_zone, "offset_days": offset_days}]})
    values = result["results"]
    if len(values) != 1 or values[0]["id"] != "boundary":
        raise RuntimeError("Shared Outlook calendar result does not match request")
    return datetime.fromisoformat(values[0]["start_utc"].replace("Z", "+00:00"))
