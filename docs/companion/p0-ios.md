# P0 iOS native feasibility evidence

Recorded October 20, 2026. **The isolated iOS simulator transport and plugin
checks pass.** This does not pass all P0 gates or authorize distribution.

## Target and result

The disposable `companion_p0_ios` shell used Flutter 3.47.4 revision
`9584c6713b324636289d067944a46fd6b49df14b`, Dart 3.13.3, Xcode 27.1
(`27A9275`) and Swift Package Manager. Its bundle identifier was
`dev.socceroutlook.spike.companionP0Ios`. It was installed only on a new
`Soccer Outlook P0 Isolated 20261020` iPhone 17 Pro simulator running iOS 26.5
(`23F77`), arm64. The compiled `MinimumOSVersion` is **15.0**; that is a build
floor, not evidence of execution on iOS 15.

| Check | Native observation |
| --- | --- |
| Simulator debug build | Passed; initial build 43.5 seconds |
| Exact plugin versions | `sqflite` 2.4.4+2, `path_provider` 2.1.6, `flutter_timezone` 5.1.1 |
| Native paths and time zone | Documents/cache/temporary directories exist; IANA result `America/New_York` |
| SQLite plugin | Created table, committed a transaction, closed database, reopened and read the expected row; deleted disposable database |
| Public HTTPS | One GET; HTTP 200; 189 ms; 118,530 response bytes |
| Stalled native TLS request | Server received request and withheld all response headers; total timeout at 8,021 ms |
| Recovery after timeout | Next loopback request returned HTTP 200 |
| Native background/foreground | OS `hidden`/`paused` cancelled active operation; old check returned null; OS `resumed` caused exactly one fresh successful request |
| Static checks | Final native Dart analyzer passed; shell syntax and Python compilation passed |

The minimized public capture was received at `2026-10-20T18:55:00.319731Z`.
It retained the same three fixture identities as the earlier host capture:
`401915403`, `401915404` and `401915402`, including kickoff/team IDs and raw
statuses/scores. The first two were final and the third scheduled. It does not
demonstrate an in-progress update or a live-to-final transition.

HTTP `Date` was `Tue, 20 Oct 2026 18:55:00 GMT` and `Cache-Control` was
`max-age=4`. Source score observation time remains unknown. No source timestamp
was inferred from device receipt time or HTTP metadata.

## Evidence and unsuccessful attempts

- [Public native capture](../../tool/companion_live_probe/ios-native-public.jsonl)
  records successful HTTPS and plugin checks, followed by a test-harness
  recursion failure when the first draft constructed an `HttpClient` inside
  its own `HttpOverrides` factory. This whole run is not labelled passed.
- [Intermediate local-only attempt](../../tool/companion_live_probe/ios-native-failed-loopback.jsonl)
  records a second harness failure: a custom connection factory returns the
  socket directly, so a plain socket does not acquire TLS automatically.
- [Final local-only capture](../../tool/companion_live_probe/ios-native-local.jsonl)
  records the clean timeout, recovery and lifecycle run after correcting the
  factory to return a `SecureSocket`. It explicitly skipped the public request.
- [Independent verifier summary](../../tool/companion_live_probe/ios-native-summary.json)
  combines the separate successful public and local evidence and retains the
  public run's later harness failure. It checks required events and their
  values, native OS identity, source-time uncertainty, the cancelled original
  operation and exactly two lifecycle requests.

There was **one public iOS GET in total**, without retries or redirects.
The later rebuilds made only simulator-local requests. The core transport in
[`probe.dart`](../../tool/companion_live_probe/lib/probe.dart) was not modified.

The final local server and client run inside the simulator app. A scoped
`HttpOverrides` redirects only the test request's socket to loopback.
Its ephemeral certificate is trusted/pinned only in that test client; the pin
accepts only the exact generated certificate. No global trust store or
application transport-security exception is changed. The separate public GET
uses ordinary certificate validation and DNS/network connection behavior.

The runner foregrounded the simulator's Settings app while the probe request
was pending, then returned after three seconds of background time. The
request count stayed at one until the resume event and became two afterward.
The superseded completion returned null. This is actual OS lifecycle evidence
for this bounded window, not a long-duration background or arbitrary
reordering stress test.

## Reproduce in a new disposable target

Retained sources are the [runner](../../tool/companion_live_probe/ios-native-main.dart),
[preparation script](../../tool/companion_live_probe/ios-prepare-harness.sh),
[manifest](../../tool/companion_live_probe/ios-native-pubspec.yaml),
[lockfile](../../tool/companion_live_probe/ios-native-pubspec.lock) and
[simulator driver](../../tool/companion_live_probe/ios-run-harness.py).
Generated Xcode boilerplate, ephemeral certificate/private key, binaries and
build logs remain under ignored `work/p0-ios/`. The same Dart runner can be
copied into the Android disposable shell.

From the repository root, with the pinned Flutter SDK on `PATH`:

```sh
flutter create --empty --platforms=ios --no-pub \
  --project-name companion_p0_ios --org dev.socceroutlook.spike \
  work/p0-ios-repeat/app
cp tool/companion_live_probe/ios-native-pubspec.yaml \
  work/p0-ios-repeat/app/pubspec.yaml
cp tool/companion_live_probe/ios-native-pubspec.lock \
  work/p0-ios-repeat/app/pubspec.lock
sh tool/companion_live_probe/ios-prepare-harness.sh work/p0-ios-repeat/app
PUB_CACHE="$PWD/work/pub-cache" flutter pub get --enforce-lockfile \
  --directory work/p0-ios-repeat/app
xcrun simctl create 'Soccer Outlook P0 Isolated repeat' \
  com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro \
  com.apple.CoreSimulator.SimRuntime.iOS-26-5
```

Use the returned simulator ID explicitly. Boot it and wait with
`xcrun simctl bootstatus ID -b`. In the disposable project directory:

```sh
flutter build ios --simulator --debug --no-pub --no-codesign
```

This default build is local-only. From the repository root:

```sh
python3 tool/companion_live_probe/ios-run-harness.py \
  --device ID \
  --app work/p0-ios-repeat/app/build/ios/iphonesimulator/Runner.app \
  --output work/p0-ios-repeat/run --public-result skipped
```

For a deliberately authorized fresh single public capture, add
`--dart-define=RUN_PUBLIC_CAPTURE=true`, `--dart-define=LEAGUE=uefa.champions`
and an explicit `--dart-define=SCOREBOARD_DATE=YYYYMMDD` to the build command,
and use the driver's default `--public-result required`. A completed local
run alone never substitutes for a `public_https_pass` event. The driver refuses
to repeat a run when the app already contains an evidence file.

Verify the retained evidence without booting a simulator or making a request:

```sh
python3 tool/companion_live_probe/verify_native_evidence.py \
  --platform ios \
  --public tool/companion_live_probe/ios-native-public.jsonl \
  --local tool/companion_live_probe/ios-native-local.jsonl \
  --out work/p0-ios/summary.json
```

Shut down and delete only the dedicated simulator after copying evidence.
The recorded target was shut down and deleted after evidence extraction.
No personal device, existing app or signing account was modified, and no global
SDK upgrade or release build was performed. No production polling or source-use
permission follows from these results. Physical phones/tablets, iOS 15 runtime behavior, power use,
release performance, durable cache policy and the source distribution decision
remain outside this evidence.
