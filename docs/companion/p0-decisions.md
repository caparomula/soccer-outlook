# P0 engineering decisions

October 20, 2026. These decisions make the remaining implementation gates
concrete. They are development defaults, not evidence that an unrun check has
passed. The [machine-readable limits](../../tool/companion_p0_limits.json) are
the numeric owner; P2/P3 must translate approved limits into runtime policy
through the normal policy/configuration owners.

## Shared rules and browser cost

Continue the single pure Dart owner, native CLI and compiled JavaScript design.
The first operation agrees on its current corpus and works with strict CSP.
Keep the complete default IANA database for now. Its historical correctness is
useful, and it avoids introducing an undocumented truncated-date range merely
to improve a size result.

The gzip ceiling applies to the **complete eventual core**, including its
time-zone data, rather than to each extracted rule separately. The verifier
enforces it. The current time-only artifact consumes about 62% of that budget.
P2 must remeasure after every migrated family; exceeding the ceiling requires
an explicit optimization or architecture decision before browser replacement.

One bundled script may add one startup request. No Flutter web renderer,
install-time network dependency or hosted decision service is introduced.
Publisher calls stay batched. Current host timing observations are encouraging,
but cannot certify physical-device startup or frame budgets.

Use release builds for eventual reference-device measurements. Record at least
20 cached cold starts and enough full-snapshot evaluations to report a p95;
include the schema's maximum fixture count, all visible card roles, long text
and enabled live updates. Measure the first useful cached content separately
from completion of network refresh. A failed refresh cannot turn a cached start
into an indefinite spinner.

## Native platform and persistence choices

Use iOS 15 and Android API 24 as the deployment floors, based on the pinned
Flutter 3.47 support range. Current simulator/emulator execution demonstrates
the installed modern runtimes; it does not replace floor-version checks.
The reference models in the limits file are test targets, not an assertion
that those physical devices are present or a request to purchase them.
Use the lowest supported OS available on each reference device and a current
OS sample before pilot distribution.

The initial plugin set passed native build and smoke checks on the
[iOS simulator](p0-ios.md) and [Android emulator](p0-android.md):

| Plugin | Exact version | Responsibility |
| --- | --- | --- |
| [`sqflite`](https://pub.dev/packages/sqflite/versions/2.4.4+2) | 2.4.4+2 | Native SQLite transactions and migrations |
| [`path_provider`](https://pub.dev/packages/path_provider/versions/2.1.6) | 2.1.6 | App support/cache directory lookup |
| [`flutter_timezone`](https://pub.dev/packages/flutter_timezone/versions/5.1.1) | 5.1.1 | Read the device's IANA zone identifier |

Platform discovery stays outside the domain. Its result becomes an explicit
input to the same pinned `timezone` calendar implementation used by the CLI and
browser. Re-read the device zone on resume; reject an unsupported identifier
explicitly instead of silently treating it as UTC.

Use separate SQLite files for replaceable downloaded content and irreplaceable
preferences/imports. The latter can hold serialized versioned records, but must
commit them before reporting success. Both files have independent versions and
recovery. A content-cache repair must not open, delete or migrate the personal
store as a side effect. Plugin smoke tests prove API/build compatibility and a
simple reopen only; crash durability, migration failures and repository
notification order still belong to P3.

The [`shared_preferences` documentation](https://pub.dev/packages/shared_preferences)
does not guarantee disk persistence when a write returns. That is a poor fit
for the specified write-before-success behavior, so it is not selected for the
lineup or imported response.

## Storage and compatibility limits

Retain one current complete generation and one previous complete generation.
Account separately for retained content, transaction/download headroom, assets
and personal records. The decoded artifact/parser limits remain owned by the
v1 schemas; the cache budget does not permit accepting a larger artifact.
Deduplicate identical artifacts by digest. Assets may be evicted independently.
Failure to make room must preserve the current durable snapshot.

Keep the current and previous valid personal import, within the personal-store
cap. Never extend expiry when retaining either. Rejected input is not persisted.
The schema's input limits must be applied before growing a record. Diagnostics
are bounded, deliberate exports with prompts, personal bodies and credentials
redacted; no persistent visitor analytics are added.

Once distribution is authorized, support each released compatible feed major
for at least the recorded interval after the last compatible app distribution.
Before retiring it, provide the recorded advance notice and a supported upgrade
path. During a major migration, publish the current and previous schema
contracts. If the support interval would require more, postpone retirement or
the incompatible release rather than silently strand installed clients.
No such support clock has started for this unshipped companion.

Unsupported schema/policy preserves the last compatible snapshot and makes the
required update visible. A failed preference migration preserves the original
store. The seven-day server object-retention policy in the
[data contract](data-contract.md) is independent of the longer feed-version
support obligation.

## Live source remains a separate decision

The bounded native probes investigate technical access and lifecycle behavior.
They do not supply a license or a service-level commitment. The current
[ESPN terms link](https://support.espn.com/hc/en-us/articles/360035445091-Terms-of-Use)
points to the [Disney terms](https://disneytermsofuse.com/english/), whose general
license and automated-access restrictions leave distributed companion use
unresolved. No current official grant for the particular scoreboard endpoint
was found. The historical API-program announcement is not treated as one.

For public distribution, the concrete alternatives remain an applicable source
permission or a reviewed licensed provider integration. Choosing a commercial
feed would require verified league/field coverage, fixture identity mapping,
terms and a secure place for paid credentials; an app bundle cannot keep a
shared paid key secret. Published-only live information would change the
minute-level capability promised by the specification. A proxy alone does not
settle source usage.

The user explicitly selected **"Leave distribution gated; continue local
engineering"** on October 20, 2026. This authorizes further local development
with the existing source assumption; it is not evidence of source permission
or permission to publish/distribute the companion. Treat source authorization
as an open distribution gate rather than repeatedly blocking local P0/P1 work.

Keep this gate explicit while completing local engineering. No paid provider,
account, outbound inquiry, proxy service or public app distribution is created
by these decisions. If source choice changes, revise P0/P1's assumptions before
the dependent producer/consumer integration.
