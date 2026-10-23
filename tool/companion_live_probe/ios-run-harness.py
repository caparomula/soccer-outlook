#!/usr/bin/env python3
"""Install/run once on an explicitly created P0 simulator; capture OS lifecycle.

This executes real network traffic only if the app was built with
RUN_PUBLIC_CAPTURE=true. Never retries or rebuilds the app.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

BUNDLE = "dev.socceroutlook.spike.companionP0Ios"


def simctl(*args):
    return subprocess.check_output(["xcrun", "simctl", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--public-result", choices=("required", "skipped"),
                        default="required")
    args = parser.parse_args()
    devices = json.loads(simctl("list", "devices", "-j"))["devices"]
    device = next(
        (d for group in devices.values() for d in group if d["udid"] == args.device),
        None,
    )
    if not device or not device["name"].startswith("Soccer Outlook P0 Isolated "):
        raise SystemExit("Refusing a simulator that is not the dedicated P0 target")
    args.output.mkdir(parents=True, exist_ok=True)
    app_bundle = subprocess.check_output(
        ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier",
         str(args.app / "Info.plist")], text=True
    ).strip()
    if app_bundle != BUNDLE:
        raise SystemExit(f"Unexpected probe bundle: {app_bundle}")
    simctl("install", args.device, str(args.app))
    container = Path(simctl("get_app_container", args.device, BUNDLE, "data"))
    evidence = container / "Documents/p0-native.jsonl"
    if evidence.exists():
        raise SystemExit("Evidence already exists; refusing to repeat public capture")
    simctl(
        "launch",
        f"--stdout={args.output.resolve() / 'stdout.log'}",
        f"--stderr={args.output.resolve() / 'stderr.log'}",
        args.device, BUNDLE,
    )

    def read_events():
        if not evidence.exists():
            return []
        # Each native record is synchronous; ignore a partially visible last line.
        result = []
        for line in evidence.read_text().splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return result

    def wait_for(predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            records = read_events()
            failures = [
                r for r in records
                if r["event"] in {"harness_failed", "lifecycle_check_failed"}
            ]
            if failures:
                raise RuntimeError(json.dumps(failures))
            if any(predicate(record) for record in records):
                return
            time.sleep(0.1)
        raise TimeoutError("Native evidence did not reach expected phase")

    try:
        wait_for(
            lambda r: r["event"] == "local_request_received"
            and r.get("path") == "/p0/lifecycle",
            50,
        )
        simctl("launch", args.device, "com.apple.Preferences")
        wait_for(
            lambda r: r["event"] == "session_cancelled_on_native_pause",
            5,
        )
        time.sleep(3)
        simctl("launch", args.device, BUNDLE)
        wait_for(lambda r: r["event"] == "all_native_checks_complete", 15)
        events = {record["event"] for record in read_events()}
        expected_public = (
            "public_https_pass" if args.public_result == "required"
            else "public_https_skipped"
        )
        required = {
            expected_public, "sqlite_close_reopen_pass",
            "real_socket_timeout_pass", "after_timeout_recovery_pass",
            "native_pause_resume_pass", "all_native_checks_complete",
        }
        if not required <= events or "public_https_failed" in events:
            raise RuntimeError(f"Missing native successes: {sorted(required - events)}")
    finally:
        if evidence.exists():
            (args.output / "native-evidence.jsonl").write_bytes(evidence.read_bytes())
    print(json.dumps({
        "device": device["name"],
        "result": "completed",
        "evidence": str(args.output / "native-evidence.jsonl"),
    }))


if __name__ == "__main__":
    main()
