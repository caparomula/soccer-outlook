# P0 foreground transport probe

This isolated spike issues one public ESPN scoreboard GET, retains a small
allowlisted projection, and exercises cancellation/late-response handling. It is
not the production live adapter or shared status decoder. It never determines
whether a score is fresh, a fixture is eligible, or a package carries a match.

The retained evidence includes the initial macOS request and isolated native
iOS/Android runs. See [transport findings](../../docs/companion/p0-live.md),
[iOS reproduction](../../docs/companion/p0-ios.md) and
[Android reproduction](../../docs/companion/p0-android.md).
Source permission remains a distribution gate; the user authorized continued
local engineering.

## Offline checks

Use the P0-pinned Dart 3.13.3 SDK. The installed SDK can be invoked directly
without asking the Flutter wrapper to update its cache:

```sh
export DASH__SUPPRESS_ANALYTICS=true
/Users/chris/flutter/bin/cache/dart-sdk/bin/dart tool/companion_live_probe/test/probe_test.dart
/Users/chris/flutter/bin/cache/dart-sdk/bin/dart analyze tool/companion_live_probe/lib
/Users/chris/flutter/bin/cache/dart-sdk/bin/dart analyze tool/companion_live_probe/bin
/Users/chris/flutter/bin/cache/dart-sdk/bin/dart analyze tool/companion_live_probe/test
```

The checks require no packages or network. They cover preservation of identity,
wire status and zero/missing scores; omission of unrelated fields; request date
validation; a single active check; pause cancellation; resume; rejection of a
late response; release after a synthetic timeout; and disposal. A synthetic
timeout verifies session behavior, not an actual socket's timeout.

## One host request

Do not make this part of normal tests or CI. Revisit the source-usage decision
before adding traffic beyond this bounded spike.

```sh
/Users/chris/flutter/bin/cache/dart-sdk/bin/dart \
  tool/companion_live_probe/bin/capture.dart uefa.champions 20261020
```

The arguments are an ESPN league and an explicit scoreboard date, following the
publisher's date convention. Choose a date with independently identified
fixtures when repeating the experiment. There are no retries or redirects.
Requests time out after eight seconds and accept at most 2 MiB. No cookies,
authorization, API key, or user preferences are sent. Only three events are
retained; the total event count is recorded. HTTP receipt time is deliberately
separate from `source_score_observed_at`, which remains unknown.

## Native evidence and reproduction

The linked platform reports describe the disposable shells, exact dependency
locks and dedicated target drivers. Both use [the same Dart harness](ios-native-main.dart);
its filename reflects the first platform tested. Default builds make only local
TLS requests. A public capture requires an explicit build flag and date.
Each recorded platform made one public GET; later harness repairs ran locally.

The harness exercises SQLite transaction/close/reopen, native directories and
IANA discovery, an actual stalled TLS socket, recovery, and OS background/resume.
Its local certificate pin is scoped to its test connection; the public request
uses normal certificate validation. Generated apps, private test keys and build
caches stay under ignored `work/`. No signing account or shared trust store is
needed.

The [evidence verifier](verify_native_evidence.py) checks captured events and
ordering without contacting a source or launching a device:

```sh
python3 tool/companion_live_probe/verify_native_evidence.py \
  --platform ios \
  --public tool/companion_live_probe/ios-native-public.jsonl \
  --local tool/companion_live_probe/ios-native-local.jsonl \
  --out work/p0-ios/summary.json
```

Use the corresponding Android paths from its report. The verifier requires a
successful public event separately from completion of local checks. Earlier
harness failures remain visible in the retained records.

The original [manual Flutter UI](flutter/main.dart) remains available for
interactive investigation. It is not the automated native evidence runner.
Production freshness thresholds and stale presentation, cache crash durability,
identity/kickoff reconciliation, final-score catch-up and later authoritative
corrections remain P2/P3 work. Simulator/emulator success does not certify
physical-device performance or minimum-OS execution.
