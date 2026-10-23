# P0 implementation evidence

**Status: P0 technical spike passed, with the limitations below.** October 20,
2026. Shared-time interoperability, draft contracts, published comparisons and
native transport/plugin feasibility are demonstrated. Production website code,
publication workflows and deployed readers have not switched owners. The user
explicitly left distribution gated while authorizing continued local engineering.

## What is implemented

| P0 work | Evidence | Follow-through |
| --- | --- | --- |
| Ownership and requirements | [Inventory](p0-ownership.md): all 24 requirements mapped; [360-fixture publication capture](p0-publication.md) and browser defaults | P1 exporter reconciliation; P2 behavioral extraction |
| Toolchain | [Version pin](../../companion-toolchain.json), exact locks, successful [iOS](p0-ios.md)/[Android](p0-android.md) builds and plugin smoke | Physical devices and minimum-OS runtime checks before pilot |
| Shared operation | [Core](../../packages/outlook_core/lib/src/sports_day.dart), [browser/CLI verification](../../tool/outlook_bridge/README.md), enforced whole-core gzip budget | Remeasure each P2 rule family; physical performance acceptance |
| Live transport | [Native HTTPS, real stalled timeout/recovery and OS pause/resume](p0-live.md), minimized records and offline verifier | P2/P3 decoder, source freshness, stale UI and live transition/correction evidence; source permission before distribution |
| Feed contract | [Schemas/examples](../../contracts/outlook/v1/README.md), [decisions](p0-contract.md), reviewed contract tests | P1 production exporter/parser round trips; P3 semantic installation checks |
| Decision record | [OS/device targets, plugin choices, budgets, cache limits and support policy](p0-decisions.md) | Enforce runtime limits as the relevant packages are implemented |

P1 and P2 have not begun. These are feasibility implementations, not new
production rule owners or a public compatibility promise. P1's local exporter
is the next implementation slice. Closing the transport spike does not certify
source-age interpretation or a working production live experience.

## Shared-time experiment

The pure Dart operation implements the 4 a.m. calendar boundary from `TIME-1`,
with a supplied UTC instant and named IANA time zone. VM, browser and compiled
publisher executable import the same implementation. The JSON adapters contain
only validation and serialization.

Twenty domain tests pass: 17 independent calendar examples, rejected local
timestamps and unknown zones, plus a historical skipped-date regression. The
17-case corpus agrees in Dart VM, Python-to-native-CLI invocation, Chromium
151.0.7922.34 and WebKit 26.5. It also matches the actual existing page's calendar
functions, exposed through a test-only hook in a generated page. Both adapters
reject six invalid input batches; both CLI modes additionally reject malformed
UTF-8 with exit 65 and a structured error, without a stack trace.

The browser demo loads the `-O2 --csp` artifact under
`default-src 'none'; script-src 'self'; base-uri 'none'; object-src 'none'`.
Both browsers report no CSP violations or page errors. No inline script or
`unsafe-eval` allowance is needed for that demo. The current website still
embeds its own scripts and does not acquire this policy automatically.

The generated integration page is the real Python renderer's frozen nine-match
fixture with the bridge inserted, including its first call. All nine rows remain.
This proves static-site coexistence and measures overhead; it does not prove
that recommendations or rendering have migrated.

### Recorded measurements

The [initial machine-readable record](evidence/time-spike-2026-10-20.json)
includes source and artifact hashes, individual timing samples, expected results
and legacy results. The [continuation record](evidence/time-spike-continuation-2026-10-20.json)
adds the enforced 204,800-byte gzip budget and final verifier source. Generated
pages and executables remain in ignored `work/`; the command rebuilds them.
The following timing table preserves the initial measurement; host timings
vary under concurrent native builds and are not release acceptance evidence.

| Measurement | Result |
| --- | --- |
| Existing rendered fixture page | 374,542 bytes; 90,187 bytes gzip |
| Added compiled JS, including default IANA database | 933,379 bytes; 126,503 bytes gzip |
| Chromium median DOMContentLoaded, baseline / with first bridge call | 41.0 / 57.3 ms |
| WebKit median DOMContentLoaded, baseline / with first bridge call | 45.0 / 64.0 ms |
| Chromium first 17-case call, including database initialization | 8.7–9.4 ms |
| WebKit first 17-case call, including database initialization | 10–11 ms |
| Cold Dart VM batch, launched by Python | 257.6 ms |
| Cold compiled CLI batch, launched by Python | 139.4 ms |

Browser figures use seven localhost navigations per variant, fresh contexts,
blocked external requests and an already running desktop browser. They are
development observations, not mobile, cold-network or production-site budgets.
The native CLI numbers are one process launch each, not throughput estimates.
Batching publisher decisions remains necessary.

The time-only artifact uses 126,503 bytes of the 204,800-byte **whole-core**
gzip ceiling, leaving 78,297 bytes. Retain the complete IANA database and
remeasure as rules are added. A smaller variant needs an explicit supported
date/zone range and boundary tests; it must not silently extrapolate stale
zone rules. Physical startup/frame budgets remain unverified.

### Existing defects kept separate from extraction

Independent review supplied an Apia date-skip example:
`2011-12-30T11:00:00Z` is local December 31 at 1 a.m. after the jurisdiction
skipped December 30. The existing browser's `sportsDayStart` returns a future
boundary, so its interval does not contain the input. The first Dart draft also
failed containment. The spike now walks back to the preceding actual boundary:
December 29 at 4 a.m., ending December 31 at 4 a.m.

That regression passes against independently stated UTC boundaries. It is
outside the present schedule corpus and is deliberately separate from the 17
unchanged-parity cases. The website defect is recorded, not fixed here.
Any eventual production change needs a separate reviewed behavior decision.
Other Python/browser discrepancies, including AI expiry and score shapes,
remain in the [ownership inventory](p0-ownership.md).

## Environment and platform decisions

The local run used macOS 27.0.1, Apple Silicon, Flutter 3.47.4 revision
`9584c6713b324636289d067944a46fd6b49df14b`, and its Dart 3.13.3 SDK. Xcode reports
27.1 (`27A9275`). Android SDK directories include platforms 34, 36 and 36.1,
build tools 35.0.0/36.0.0/36.1.0 and a 37.0.0 release candidate. The final Android
build used an isolated workspace SDK with an additional API 35 compile
dependency required by `flutter_timezone`. The [platform report](p0-android.md)
records actual Java/Gradle/build tools. No global SDK upgrade was performed.

The spike pins `timezone` 0.11.2 with IANA data 2026e, verified from its installed
generated source, and Dart test 1.33.0. The package's older README database
description differs from its [current changelog](https://pub.dev/packages/timezone/changelog);
the installed data header is the measurement's authority.

Flutter's [current platform table](https://docs.flutter.dev/reference/supported-platforms)
supports iOS 15+ and Android API 24+ for Flutter 3.47. The native artifacts
confirm those build floors; execution on the oldest OS remains unverified. The
[stable SDK archive](https://docs.flutter.dev/install/archive) and
[Dart interop guidance](https://dart.dev/interop/js-interop/usage) informed the
pin and the exported-function approach.

`sqflite` 2.4.4+2, `path_provider` 2.1.6 and `flutter_timezone` 5.1.1 passed
native build and smoke checks on both current runtimes. Separate SQLite stores
are selected for downloaded content and personal preferences/imports. The
[decision record](p0-decisions.md) names physical phone/tablet targets, cache
caps, release performance budgets and feed-version support duration. These
decisions make later acceptance concrete; they do not claim unrun checks passed.

The host's default Python is 3.9.6, below this repository's requirement. This
run installed Python 3.13.16 and test dependencies in ignored workspace
directories, using `work/companion-venv/bin/python`. It downloaded test browsers
into `work/playwright/` and Dart dependencies into `work/pub-cache/`.
Use an existing Python 3.11+ environment elsewhere; no project code requires
these local absolute paths.

The bridge command for this prepared checkout is:

```sh
PUB_CACHE="$PWD/work/pub-cache" \
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/playwright" \
work/companion-venv/bin/python tool/outlook_bridge/verify.py \
  --dart /Users/chris/flutter/bin/cache/dart-sdk/bin/dart
```

## Checks and recovery

The final full Python suite passed all 586 tests, including 20 contract tests,
the core boundary check, six native-evidence tests, six publication-capture
tests and two size-budget tests. Twenty Dart domain tests, the standalone
live-session checks, analyzers and browser/CLI parity pass. Native generated
shells also pass analysis. Localhost socket/process access was needed for
browser tests and native tools; the initial sandbox failures were environmental.

Independent review added contract cases for changed kickoff identity,
unsourced calendar stories and missing references. Continuation review found
two acceptance-check defects: a reordered cancellation could masquerade as
OS-triggered behavior, and nonfinite JSON could bypass a numeric size check.
Both are fixed with failing-case regressions. The independent reviewer then
verified retained native records, their hashes, generated-source consistency
and the successful rechecks. Earlier native harness/controller failures are
retained in each platform report, separate from final clean local runs.

The new [companion CI workflow](../../.github/workflows/companion.yml) runs
contract/boundary checks, recorded native-evidence validation, offline
live-session checks and the browser/CLI spike with the pinned Dart SDK.
Its GitHub execution remains unverified; it
does not fetch ESPN, publish a site or distribute an app. Domain dependency
checks keep platform I/O and ambient clocks out of the core.

The published capture reconciles fixture identity across HTML, schedule and
context, plus allowlisted HTML/schedule facts. It preserves known differences,
partial source coverage and incomplete context-field comparisons. It is a
baseline for P1, not an assertion that every current output is correct.

Both dedicated native targets were shut down after extraction. Stop using
the ignored artifacts to abandon the spike; no production feed, browser
reader or persistence migration needs rolling back. Local P1 implementation
can continue. Distribution remains gated on source permission and the later
device, signing and release acceptance work.
