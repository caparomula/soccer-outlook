#!/usr/bin/env python3
"""Retain only the exact shared-core asset referenced by a fetched local page.

This reads local Git objects; it neither fetches nor executes published content.
A full rebuild adds its current asset separately, leaving previous plus current.
"""
import argparse
import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_CORE_BYTES = 4 * 1024 * 1024
ASSET = re.compile(r"outlook-core-([0-9a-f]{64})\.js")
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class RetentionError(ValueError):
    pass


class CoreReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.names = set()

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        sources = [value for key, value in attrs if key == "src"]
        matching = [value for value in sources if isinstance(value, str) and ASSET.fullmatch(value)]
        if matching and len(sources) != 1:
            raise RetentionError("Previous core script has ambiguous src attributes")
        self.names.update(matching)

    handle_startendtag = handle_starttag


def git_read(repo, *args):
    # A promisor/partial repository must fail locally rather than lazily fetch.
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "protocol.allow=never", *args],
            cwd=repo, env=environment, check=True, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RetentionError("Cannot read previous local publication objects") from error
    return result.stdout


def tree_blob(repo, tree, name, limit):
    """Check immutable object type/size before buffering its content."""
    listing = git_read(repo, "ls-tree", "-z", tree, "--", name)
    if not listing:
        return None
    records = listing.rstrip(b"\0").split(b"\0")
    if len(records) != 1:
        raise RetentionError("Previous publication path is ambiguous")
    try:
        metadata, path = records[0].split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        object_id = object_id.strip()
        if (path.decode("utf-8") != name or mode not in ("100644", "100755")
                or kind != "blob" or not OBJECT_ID.fullmatch(object_id)):
            raise ValueError("not a regular blob")
        size = int(git_read(repo, "cat-file", "-s", object_id))
    except (UnicodeError, ValueError) as error:
        raise RetentionError("Previous publication path is not a regular blob") from error
    if size < 0 or size > limit:
        raise RetentionError(f"Previous publication {name} exceeds its byte limit")
    raw = git_read(repo, "cat-file", "blob", object_id)
    if len(raw) != size:
        raise RetentionError("Previous publication blob size changed")
    return raw


def restore(ref, out, repo=None):
    repo = Path.cwd() if repo is None else Path(repo)
    try:
        tree = git_read(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{tree}").decode("ascii").strip()
    except UnicodeError as error:
        raise RetentionError("Previous publication has no valid local tree") from error
    if not OBJECT_ID.fullmatch(tree):
        raise RetentionError("Previous publication has no valid local tree")
    index = tree_blob(repo, tree, "index.html", MAX_INDEX_BYTES)
    if index is None:
        return None
    parser = CoreReferences()
    try:
        parser.feed(index.decode("utf-8"))
        parser.close()
    except UnicodeError as error:
        raise RetentionError("Previous index is not UTF-8") from error
    if not parser.names:
        return None
    if len(parser.names) != 1:
        raise RetentionError("Previous index references more than one core asset")
    name = next(iter(parser.names))
    raw = tree_blob(repo, tree, name, MAX_CORE_BYTES)
    if raw is None:
        raise RetentionError("Previous index references a missing core asset")
    if hashlib.sha256(raw).hexdigest() != ASSET.fullmatch(name).group(1):
        raise RetentionError("Previous core asset does not match its filename SHA-256")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=out, prefix=".outlook-retain-", delete=False) as stream:
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
        os.replace(temporary, out / name)
    finally:
        temporary.unlink(missing_ok=True)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="FETCH_HEAD", help="already-fetched local Git ref")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        name = restore(args.ref, args.out)
    except (OSError, RetentionError) as error:
        print(f"::error::Cannot retain previous Outlook core: {error}", file=sys.stderr)
        return 1
    print(f"Retained previous Outlook core: {name}" if name else "Previous page has no separate Outlook core to retain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
