#!/usr/bin/env python3
"""Check recorded P0 native evidence, without making network/device requests."""
import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    records = []
    for line in path.read_text().splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("event"), str):
                raise ValueError("Each evidence line must be a native event object")
            records.append(value)
    return records


def one(records, event):
    found = [record for record in records if record["event"] == event]
    if len(found) != 1:
        raise ValueError(f"Expected exactly one {event}, found {len(found)}")
    return found[0]


def verify(public_records, local_records, platform):
    if platform not in ("ios", "android"):
        raise ValueError("Native evidence requires ios or android")
    public_env = one(public_records, "environment")
    local_env = one(local_records, "environment")
    for environment in (public_env, local_env):
        if environment.get("platform") != platform:
            raise ValueError("Host or different-platform evidence cannot prove native access")
        if not environment.get("iana_zone"):
            raise ValueError("Missing native time-zone result")
        if not all(environment.get(key) is True for key in
                   ("documents_exists", "cache_exists", "temporary_exists")):
            raise ValueError("Native directory lookup did not pass")
    for key in ("os_version", "dart_version", "plugins"):
        if public_env.get(key) != local_env.get(key) or not local_env.get(key):
            raise ValueError(f"Native environment changed between captures: {key}")

    if any(record["event"] == "public_https_failed" for record in public_records):
        raise ValueError("Public HTTPS failed")
    capture = one(public_records, "public_https_pass").get("capture", {})
    if (capture.get("runtime_platform") != platform or capture.get("http_status") != 200 or
            not capture.get("source_url", "").startswith(
                "https://site.api.espn.com/apis/site/v2/sports/soccer/")):
        raise ValueError("Missing successful native public scoreboard request")
    if not isinstance(capture.get("scoreboard", {}).get("events"), list):
        raise ValueError("Missing wire projection")
    # This probe has no established source score timestamp. A receiving clock
    # or HTTP Date must never be substituted and presented as score freshness.
    if ("source_score_observed_at" not in capture or
            capture["source_score_observed_at"] is not None):
        raise ValueError("Unexpected claim of source score observation time")

    failed = {"harness_failed", "lifecycle_check_failed", "public_https_failed"}
    if any(record["event"] in failed for record in local_records):
        raise ValueError("The local transport/lifecycle run contains a failure")
    if one(local_records, "sqlite_close_reopen_pass").get("rows") != 1:
        raise ValueError("SQLite transaction/reopen did not retain the record")
    elapsed = one(local_records, "real_socket_timeout_pass").get("elapsed_ms")
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not 7900 <= elapsed <= 12000:
        raise ValueError("Stalled-socket timeout is outside the harness acceptance window")
    if one(local_records, "after_timeout_recovery_pass").get("http_status") != 200:
        raise ValueError("No recovery after the stalled socket")
    pauses = [i for i, record in enumerate(local_records)
              if record["event"] == "native_lifecycle" and record.get("state") in ("hidden", "paused")]
    if not pauses:
        raise ValueError("OS lifecycle transitions are missing")
    pause_index = pauses[0]
    resumes = [i for i, record in enumerate(local_records)
               if i > pause_index and record["event"] == "native_lifecycle" and record.get("state") == "resumed"]
    if not resumes:
        raise ValueError("OS resume did not follow backgrounding")
    resume_index = resumes[0]
    completion_records = [record for record in local_records
                          if record["event"] == "lifecycle_check_completed"]
    completions = {record.get("check"): record for record in completion_records}
    if len(completion_records) != 2 or set(completions) != {1, 2}:
        raise ValueError("Expected one original and one resumed check")
    if (completions[1].get("cancelled") is not True or
            completions[2].get("cancelled") is not False):
        raise ValueError("Original operation was accepted or resumed operation was lost")
    requests = [(i, record) for i, record in enumerate(local_records)
                if record["event"] == "local_request_received" and
                record.get("path") == "/p0/lifecycle"]
    if (len(requests) != 2 or requests[0][1].get("lifecycle_request_count") != 1 or
            requests[1][1].get("lifecycle_request_count") != 2 or
            not requests[0][0] < pause_index < resume_index < requests[1][0]):
        raise ValueError("Native lifecycle request order is inconsistent")
    if (local_records.index(completions[1]) <= pause_index or
            local_records.index(completions[2]) <= requests[1][0]):
        raise ValueError("Operation completion precedes its native trigger")
    cancellations = [i for i, record in enumerate(local_records)
                     if record["event"] == "session_cancelled_on_native_pause" and
                     record.get("busy") is False]
    if not any(pause_index < i < resume_index for i in cancellations):
        raise ValueError("No cancellation between OS background and resume")
    lifecycle = one(local_records, "native_pause_resume_pass")
    if lifecycle.get("requests") != 2 or lifecycle.get("old_check_rejected") is not True:
        raise ValueError("Resume started the wrong number of requests")
    one(local_records, "all_native_checks_complete")
    return {
        "platform": platform,
        "environment": {key: local_env[key] for key in
                        ("os_version", "dart_version", "iana_zone", "plugins")},
        "public_https": {
            "status": capture["http_status"],
            "elapsed_ms": capture.get("elapsed_ms"),
            "received_at": capture.get("response_received_at"),
            "source_score_observed_at": None,
        },
        "stalled_socket_timeout_ms": elapsed,
        "pause_resume_requests": 2,
        "public_run_other_failures": [record["event"] for record in public_records
                                     if record["event"] in failed],
        "limits": "Native simulator/emulator transport and plugin smoke only; "
                  "no physical-device performance, licensing, freshness, or cache durability claim",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("ios", "android"), required=True)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = verify(load(args.public), load(args.local), args.platform)
    result["evidence_sha256"] = {
        role: hashlib.sha256(path.read_bytes()).hexdigest()
        for role, path in (("public", args.public), ("local", args.local))
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"PASS: {args.platform} public transport, local timeout, recovery and OS lifecycle")


if __name__ == "__main__":
    main()
