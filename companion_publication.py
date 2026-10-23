"""Local immutable companion feed writer; this module never deploys a site.

``manifest.json`` is the only current-snapshot pointer. Artifacts, prior
manifests, and a durable revision reservation are synced before its atomic
replacement. A failed attempt may consume a revision and leave unused objects;
it cannot make an existing snapshot incomplete. Callers must additionally
serialize remote deployments and reject superseded source commits.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import tempfile
import warnings

from companion_contract import (
    ARTIFACT_LIMIT, KINDS, MANIFEST_LIMIT, MAX_SAFE_INTEGER, ContractError,
    decode, encode, read_bytes, validate, validate_artifacts, validate_publication,
)


STATE = "publication-state.json"
HISTORY = "manifests"
RETENTION = timedelta(days=7)
MAX_HISTORY = 16384
_REVISION_FILE = re.compile(r"^[0-9]{20}\.json$")
_OBJECT_FILE = re.compile(r"^[0-9a-f]{64}\.json$")


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ContractError("published_at must be an aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ContractError(f"unsafe publication directory: {path.name}")
    path.mkdir(parents=True, exist_ok=True)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _writer(root):
    _directory(root)
    lock_path = root / ".publication.lock"
    if lock_path.is_symlink():
        raise ContractError("unsafe writer lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write(path, raw):
    if path.is_symlink():
        raise ContractError(f"unsafe publication file: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _immutable_write(path, raw, limit):
    if path.exists() or path.is_symlink():
        if read_bytes(path, limit) != raw:
            raise ContractError(f"immutable file conflict: {path.name}")
    else:
        _atomic_write(path, raw)


def _history_path(root, manifest):
    return root / HISTORY / f"{manifest['publication_revision']:020d}.json"


def _load_tree(root):
    """Validate all durable state before any mutation; no guessed counter reset."""
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ContractError("unsafe publication root")
    for name in ("objects", HISTORY):
        target = root / name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ContractError("unsafe publication storage")
    current_path = root / "manifest.json"
    current = None
    manifests = {}
    if current_path.exists() or current_path.is_symlink():
        current, _ = validate_publication(root)
        manifests[current["publication_revision"]] = current
    history = root / HISTORY
    if history.exists():
        paths = list(history.iterdir())
        if len(paths) > MAX_HISTORY:
            raise ContractError("publication history limit")
        for path in paths:
            # Ignore only our interrupted atomic-write temporary files.
            if path.name.startswith(".pending-") and not path.is_symlink():
                continue
            if not _REVISION_FILE.fullmatch(path.name):
                raise ContractError("unrecognized publication history file")
            manifest = decode(read_bytes(path, MANIFEST_LIMIT), MANIFEST_LIMIT)
            validate_publication(root, manifest)
            revision = manifest["publication_revision"]
            if path != _history_path(root, manifest):
                raise ContractError("history revision filename mismatch")
            if revision in manifests and encode(manifests[revision]) != encode(manifest):
                raise ContractError("equal publication revision has different content")
            manifests[revision] = manifest
    highest = max(manifests, default=0)
    state_path = root / STATE
    if state_path.exists() or state_path.is_symlink():
        state = decode(read_bytes(state_path, MANIFEST_LIMIT), MANIFEST_LIMIT)
        if (not isinstance(state, dict) or set(state) != {"schema_version", "highest_revision"} or
                type(state["schema_version"]) is not int or state["schema_version"] != 1 or
                type(state["highest_revision"]) is not int or
                not highest <= state["highest_revision"] <= MAX_SAFE_INTEGER):
            raise ContractError("invalid durable publication revision state")
        highest = state["highest_revision"]
    elif ((current is None and manifests) or
          (current is not None and any(revision > current["publication_revision"] for revision in manifests)) or
          (current is None and (root / "objects").exists() and any((root / "objects").iterdir()))):
        raise ContractError("objects/history without a matching revision reservation")
    return current, manifests, highest


def _reserve(root, revision):
    _atomic_write(root / STATE, encode({"schema_version": 1, "highest_revision": revision},
                                      MANIFEST_LIMIT))


def _prepare_directories(root):
    _directory(root / "objects")
    _directory(root / HISTORY)
    _sync_directory(root)


def _restore_locked(root, previous):
    current, manifests, highest = _load_tree(root)
    prior, prior_manifests, prior_highest = _load_tree(previous)
    if prior is None:
        raise ContractError("previous publication has no manifest")
    for revision in manifests.keys() & prior_manifests.keys():
        if encode(manifests[revision]) != encode(prior_manifests[revision]):
            raise ContractError("conflicting previous publication revision")
    if current is not None and current["publication_revision"] >= prior["publication_revision"]:
        if prior_highest > highest:
            _reserve(root, prior_highest)
        return current
    _prepare_directories(root)
    # Preserve destination history as well as the complete retained source tree.
    _reserve(root, max(highest, prior_highest))
    for manifest in prior_manifests.values():
        for kind in KINDS:
            descriptor = manifest["artifacts"][kind]
            if descriptor is not None:
                raw = read_bytes(previous / descriptor["path"], ARTIFACT_LIMIT)
                if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
                    raise ContractError("previous object changed while restoring")
                _immutable_write(root / descriptor["path"], raw, ARTIFACT_LIMIT)
        if manifest is not prior:
            _immutable_write(_history_path(root, manifest), encode(manifest, MANIFEST_LIMIT),
                             MANIFEST_LIMIT)
    if current is not None:
        _immutable_write(_history_path(root, current), encode(current, MANIFEST_LIMIT), MANIFEST_LIMIT)
    _atomic_write(root / "manifest.json", encode(prior, MANIFEST_LIMIT))
    return prior


def restore_previous(api_dir, previous_dir):
    """Restore validated prior api/v1 output, preserving any newer local snapshot.

    Use before export when a disabled/failed optional export must carry forward
    the existing feed. Invalid prior state fails closed instead of restarting at 1.
    """
    root, previous = Path(api_dir), Path(previous_dir)
    if root.resolve() == previous.resolve():
        with _writer(root):
            current, _, _ = _load_tree(root)
            if current is None:
                raise ContractError("previous publication has no manifest")
            return current
    with _writer(root):
        return _restore_locked(root, previous)


def _collect(root, current, manifests):
    """Best-effort post-commit collection; retain current, previous, and seven days."""
    current_revision = current["publication_revision"]
    previous_revision = max((revision for revision in manifests if revision < current_revision), default=None)
    cutoff = _instant(current["published_at"]) - RETENTION
    keep = {current_revision, previous_revision}
    keep.update(revision for revision, manifest in manifests.items()
                if _instant(manifest["published_at"]) >= cutoff)
    retained = [manifest for revision, manifest in manifests.items() if revision in keep] + [current]
    objects = {manifest["artifacts"][kind]["path"] for manifest in retained
               for kind in KINDS if manifest["artifacts"][kind] is not None}
    # Delete manifests first. An interrupted collection can only retain extra objects.
    for revision, manifest in manifests.items():
        path = _history_path(root, manifest)
        if revision not in keep and path.exists():
            path.unlink()
    _sync_directory(root / HISTORY)
    for path in (root / "objects").iterdir():
        if _OBJECT_FILE.fullmatch(path.name) and f"objects/{path.name}" not in objects:
            if path.is_symlink():
                raise ContractError("unsafe unreferenced object")
            path.unlink()
    _sync_directory(root / "objects")


def publish(api_dir, artifacts, *, published_at, producer_revision, previous_dir=None):
    """Validate and atomically commit one local feed, returning its manifest.

    An invalid optional editorial artifact is withdrawn explicitly and reported
    in additive manifest diagnostics. Required artifacts always fail closed.
    Input dictionaries and all source observation/expiry fields are untouched.
    """
    timestamp = _utc(published_at)
    if not isinstance(producer_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", producer_revision):
        raise ContractError("producer_revision must be a full lowercase Git commit")
    if not isinstance(artifacts, dict) or set(artifacts) != set(KINDS):
        raise ContractError("expected catalog, schedule, policy, and explicit editorial")
    required = dict(artifacts, editorial=None)
    validate_artifacts(required)
    diagnostics = []
    prepared = dict(artifacts)
    try:
        validate_artifacts(prepared)
    except ContractError:
        prepared["editorial"] = None
        # Schema error text can embed rejected URLs, credentials, or private
        # editorial input. Only stable, non-input-derived diagnostics are public.
        diagnostics.append({"code": "editorial_withdrawn", "reason": "editorial validation failed"})
    raw_artifacts = {kind: encode(value) for kind, value in prepared.items() if value is not None}
    descriptors = {}
    for kind in KINDS:
        if prepared[kind] is None:
            descriptors[kind] = None
            continue
        raw = raw_artifacts[kind]
        digest = hashlib.sha256(raw).hexdigest()
        descriptors[kind] = {"path": f"objects/{digest}.json", "sha256": digest,
                             "bytes": len(raw), "schema_version": 1,
                             "content_revision": prepared[kind]["content_revision"]}
    root = Path(api_dir)
    with _writer(root):
        if previous_dir is not None and Path(previous_dir).resolve() != root.resolve():
            _restore_locked(root, Path(previous_dir))
        current, manifests, highest = _load_tree(root)
        if highest == MAX_SAFE_INTEGER:
            raise ContractError("publication revision exhausted")
        revision = highest + 1
        manifest = {"schema_version": 1, "kind": "outlook_manifest",
                    "content_revision": "publication-" + hashlib.sha256(encode(descriptors)).hexdigest(),
                    "publication_revision": revision, "published_at": timestamp,
                    "producer_revision": producer_revision, "artifacts": descriptors}
        if diagnostics:
            manifest["diagnostics"] = diagnostics
        validate("manifest", manifest)
        _prepare_directories(root)
        _reserve(root, revision)
        for kind, raw in raw_artifacts.items():
            _immutable_write(root / descriptors[kind]["path"], raw, ARTIFACT_LIMIT)
        if current is not None:
            _immutable_write(_history_path(root, current), encode(current, MANIFEST_LIMIT), MANIFEST_LIMIT)
        # Final pointer replacement is the only operation that changes active content.
        _atomic_write(root / "manifest.json", encode(manifest, MANIFEST_LIMIT))
        try:
            _collect(root, manifest, manifests)
        except (OSError, ContractError) as error:
            warnings.warn(f"companion publication committed; retention deferred: {error}", RuntimeWarning)
        return manifest
