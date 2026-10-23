#!/usr/bin/env python3
"""Run the P0 harness on its dedicated emulator and retain only JSON evidence.

The APK must already be installed. Each invocation starts a fresh process; an
APK built with RUN_PUBLIC_CAPTURE=true makes one public GET per invocation.
There are no automatic retries. This never discovers or chooses a user device.
"""

import argparse
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--server-port", type=int, default=5038)
    parser.add_argument("--serial", default="emulator-5580")
    parser.add_argument("--avd", default="soccer_p0_api36_1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-public-capture", action="store_true")
    args = parser.parse_args()
    if not args.serial.startswith("emulator-"):
        parser.error("Only a dedicated emulator is supported")
    adb = [args.adb, "-P", str(args.server_port), "-s", args.serial]

    def command(*parts):
        return subprocess.check_output(
            [*adb, *parts], text=True, stderr=subprocess.STDOUT, timeout=20
        ).strip()

    avd = command("emu", "avd", "name").splitlines()[0]
    if avd != args.avd or command("shell", "getprop", "ro.kernel.qemu") != "1":
        parser.error("Target is not the explicitly selected P0 emulator")
    package = "dev.socceroutlook.spike.companion_live_probe"
    activity = f"{package}/.MainActivity"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Evidence output exists; choose a new path")
    command("shell", "am", "force-stop", package)
    # Read the app-owned file: Android may split long Flutter logcat JSON lines.
    evidence_path = "app_flutter/p0-native.jsonl"
    command(
        "shell", "run-as", package, "rm", "-f", evidence_path
    )
    saw_background = False
    saw_complete = False
    failures = []
    records = []
    command("shell", "am", "start", "-W", "-n", activity)
    deadline = time.monotonic() + 90
    with args.output.open("x") as evidence:
        while time.monotonic() < deadline and not saw_complete:
            try:
                contents = command("exec-out", "run-as", package, "cat", evidence_path)
            except subprocess.CalledProcessError as error:
                if "No such file or directory" not in error.output:
                    raise
                time.sleep(0.2)
                continue
            try:
                observed = [json.loads(line) for line in contents.splitlines()]
            except json.JSONDecodeError:
                # A file read can overlap the final synchronous append.
                time.sleep(0.2)
                continue
            if observed[:len(records)] != records:
                raise RuntimeError("App evidence was replaced during the run")
            for record in observed[len(records):]:
                records.append(record)
                evidence.write(json.dumps(record, separators=(",", ":")) + "\n")
                evidence.flush()
                print(record["event"], flush=True)
                if record["event"].endswith("_failed"):
                    failures.append(record)
                if (
                    record["event"] == "local_request_received"
                    and record["path"] == "/p0/lifecycle"
                    and record["lifecycle_request_count"] == 1
                    and not saw_background
                ):
                    saw_background = True
                    command("shell", "input", "keyevent", "KEYCODE_HOME")
                    time.sleep(3)
                    command("shell", "am", "start", "-W", "-n", activity)
                saw_complete = record["event"] == "all_native_checks_complete"
            if failures:
                break
            time.sleep(0.2)
    required = {
        "environment",
        "sqlite_close_reopen_pass",
        "real_socket_timeout_pass",
        "after_timeout_recovery_pass",
        "native_pause_resume_pass",
        "all_native_checks_complete",
    }
    if args.require_public_capture:
        required.add("public_https_pass")
    missing = required - {record["event"] for record in records}
    if failures or missing or not saw_background:
        raise RuntimeError(
            f"Native check incomplete: failures={len(failures)}, "
            f"missing={sorted(missing)}, backgrounded={saw_background}"
        )
    print(f"Native checks passed; evidence: {args.output}")


if __name__ == "__main__":
    main()
