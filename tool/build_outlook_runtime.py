#!/usr/bin/env python3
"""Compile the pinned shared owner for publisher and self-contained website.

Dependency acquisition is separate: run pub get --enforce-lockfile in the core
and bridge packages first. This command resolves offline and never publishes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from outlook_runtime import source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dart", default="dart")
    parser.add_argument("--out", type=Path, default=ROOT / "work/outlook-runtime")
    args = parser.parse_args()
    pin = json.loads((ROOT / "companion-toolchain.json").read_text())["dart"]
    version = subprocess.run([args.dart, "--version"], check=True, capture_output=True, text=True)
    if f"version: {pin} " not in version.stdout + version.stderr:
        raise RuntimeError(f"Expected pinned Dart {pin}")
    dart = [args.dart, "--suppress-analytics"]
    sources = source_manifest()
    for directory in (ROOT / "packages/outlook_core", ROOT / "packages/outlook_data", ROOT / "tool/outlook_bridge"):
        subprocess.run(dart + ["pub", "get", "--offline", "--enforce-lockfile"], cwd=directory, check=True)
    target = args.out.resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compile-", dir=target) as temporary:
        stage = Path(temporary)
        commands = [dart + ["compile", "js", "-O2", "--no-source-maps", "--csp",
                            "-o", str(stage / "outlook.js"), "web/bridge.dart"],
                    dart + ["compile", "exe", "bin/outlook.dart", "-o", str(stage / "outlook")]]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(subprocess.run, command, cwd=ROOT / "tool/outlook_bridge", check=True)
                       for command in commands]
            for future in futures:
                future.result()
        raw = (stage / "outlook.js").read_bytes()
        compressed = len(gzip.compress(raw, mtime=0))
        budget = json.loads((ROOT / "tool/companion_p0_limits.json").read_text())["budgets"]["browser_core_gzip_bytes"]
        if compressed > budget:
            raise RuntimeError(f"Shared browser core {compressed} exceeds {budget}-byte gzip budget")
        if source_manifest() != sources:
            raise RuntimeError("Core sources changed during compilation; rerun after edits complete")
        manifest = {"version": 1, "dart": pin, "sources": sources,
                    "browser_bytes": len(raw), "browser_gzip_bytes": compressed,
                    "browser_gzip_budget": budget,
                    "artifacts": {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                                  for name in ("outlook", "outlook.js")}}
        # Readers verify both hashes, so an interrupted replacement fails closed.
        for name in ("outlook", "outlook.js"):
            os.replace(stage / name, target / name)
        (stage / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(stage / "runtime.json", target / "runtime.json")
    print(f"Shared runtime ready: {target}; browser gzip {compressed}/{budget} bytes")


if __name__ == "__main__":
    main()
