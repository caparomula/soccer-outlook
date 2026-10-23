#!/usr/bin/env python3
"""Finalize staged companion facts after optional story generation, without fetching."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import companion_build


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--api-dir", required=True, type=Path)
    parser.add_argument("--automatic", type=Path)
    parser.add_argument("--shared", type=Path)
    args = parser.parse_args(argv)
    staged = json.loads(args.stage.read_text(encoding="utf-8"))
    manifest = companion_build.finalize(staged, args.api_dir, automatic_path=args.automatic, shared_path=args.shared)
    print(f"Companion publication finalized: {manifest['publication_revision']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
