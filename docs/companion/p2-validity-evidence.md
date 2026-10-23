# P2 route-validity acceptance

The bounded `WATCH-VALIDITY-1` increment passed local acceptance on October 23,
2026. The publisher and browser now use the same Dart decision for retained
listing deadlines, explicit route expiry and inclusive Eastern usual-rights end
dates. P2 as a whole remains in progress; feed delivery and distribution remain
gated.

The [rule](rules/route-validity.md), [independent examples](rules/route-validity-examples.json)
and [validation record](evidence/p2-validity-validation-2026-10-23.json) define the
scope and bind the final sources, runtime, reports and logs. This extends the
[viewer-eligibility checkpoint](p2-eligibility-evidence.md).

## Accepted behavior

- Retained coverage ends at the earliest of the original observation plus the
  bounded retention allowance, kickoff plus four hours, or an earlier explicit
  expiry. The cutoff is exclusive and preserves microsecond precision. Reuse,
  reload and republishing do not renew the original age.
- Usual rights remain valid through their inclusive Eastern end date and expire
  at the following Eastern midnight. The supplied output clock takes precedence
  over a historical `--date`; an old date cannot revive expired coverage.
- Validity is evaluated before provider selection. Expiring one route removes
  its access and playback links while preserving an independently valid alternate.
  The browser updates schedule eligibility, strip/solo state, match editorial,
  card links and local prompt channels at the cutoff and on resume.
- Schedule refresh validates temporal metadata before replacement. An expired
  but well-formed route remains legitimate input; malformed metadata leaves the
  existing page intact. Pinned previews use their explicit clock.
- Fresh listings gain no invented universal TTL. Source, country, package,
  replay and destination qualification remain upstream. This increment does not
  migrate those owners or add a usual-rights start-date rule.

## Evidence

| Check | Result and scope |
| --- | --- |
| Core | 104 retained Dart tests, clean core/bridge analysis and formatting; all 26 checked source/test/example hashes and 16 compile-source hashes reverified October 23. No redundant rerun is claimed. |
| Protocol | 60 retained example groups, including 18 validity groups with 33 literal decisions; 16 malformed frames rejected. Fresh verifier runs 19 validity/composition envelopes through VM, compiled CLI, Chromium and WebKit, with 13 malformed validity requests rejected per target. |
| Browser validity | Seven lifecycle scenarios in each engine, including exact expiry with real provider disclosure/card links, an open More panel, alternate access, solo reset, prompt filtering, reload, snapshot rejection, Eastern midnight, pinned time and resume. Strict CSP is proven on the protocol page; the generated page retains its existing inline-script/style model. |
| Python | 725 tests pass on final sources, including 163 focused producer checks and seven verifier-integrity checks. |
| Existing browser gates | 112 Chromium checks, 40 WebKit smoke checks and 11 schedule-refresh checks per engine: 174 total. Route/calendar and eligibility migration verifiers also pass on final sources. |
| Offline builds | Full and lightweight replay cover 360 captured fixtures; original observation ages and required catalog/schedule/policy bytes remain unchanged. Both publications pass the independent Dart reader; revisions advance from 1 to 2. |
| Size | Compiled browser core: 194,771 gzip bytes against the unchanged 204,800-byte limit, leaving 10,029 bytes. Replayed pages: 2,764,515 bytes, below the existing 3,300,000-byte budget. All 448 embedded logos remain. |

Reports: [core](evidence/p2-validity-core-2026-10-23.json),
[browser/protocol](evidence/p2-validity-2026-10-23.json),
[route/calendar](evidence/p2-validity-route-calendar-2026-10-23.json),
[eligibility](evidence/p2-validity-eligibility-2026-10-23.json),
[build replay](evidence/p2-validity-replay-2026-10-23.json),
[core/browser-source review](evidence/p2-validity-core-review-2026-10-23.json), and
[independent producer review](evidence/p2-validity-python-review-2026-10-23.json).

Browser fixtures supply literal temporal inputs and synthetic HTTPS destinations
through the production renderer; no provider destination is opened. The captured
360-fixture replay establishes migration parity, not coverage of every synthetic
expiry case. The core compares exact microseconds; browser timers act at the
first representable millisecond at or after a fractional cutoff.

## Repairs and migration differences

The earlier incomplete acceptance record is preserved in the start snapshot and
its original logs. The final work resolved the optional-exporter import coupling
by moving the unchanged public-HTTPS helper into `provider_utils.py`; schedule
revision fingerprints include that shared dependency. Loopback tests passed with
local execution permission.

Independent producer review reproduced and repaired additional edge cases:

1. Reusing duplicate same-channel observations could discard the earlier cutoff.
   Retention now aggregates admitted same-channel observations, including
   already-expired observations, preserving the oldest verification and earliest
   deadline. Future-verification records cannot justify reuse.
2. Filtering malformed retained evidence could turn the remaining fresh record
   into renewed access during an outage. Qualified malformed retained claims now
   constrain that channel; definitively rejected sources do not poison a valid
   independent source, and other fresh channels remain available. The page and
   feed share temporal facts. Unrepresentable retained intervals become denied
   unknown feed routes without invented timestamps.
3. Preserving multiple temporal observations could create duplicate feed evidence
   references. References are deduplicated while the oldest verification and earliest
   effective cutoff remain available for conservative aggregation.

The [repair record](evidence/p2-validity-repairs-2026-10-23.json) retains the
failing-first logs and successive fixes. The independent reviewer rechecked 14
adversarial cases against final sources and confirmed both reported candidate
findings resolved.

The [old/new boundary proof](evidence/p2-validity-boundary-2026-10-23.json) records
one intentional difference: the old retention path accepted the exact six-hour
endpoint; the new owner rejects it. Both accept one microsecond before and reject
one microsecond after. The legacy source was recovered from the snapshot's
recorded Git commit because the unchanged file was absent from its dirty-file
archive; hashes and provenance are recorded.

The generated-page harness previously failed on detached filtered rows and an
incorrect fixture service ID. Its repaired checks inspect original DOM references
and connected replacement rows separately, using the production `para` ID.
The normal CI verifier now generates its fixtures without an ignored historical
capture; optional `--legacy` reproduction is explicit. The separate
[historical browser proof](evidence/p2-validity-legacy-2026-10-23.json) retains the
old app/core bytes and reproduces retained access surviving expiry and reload in
both engines. That report predates the final producer repairs and is historical
evidence only; the final browser/protocol report binds the current candidate.

## Preservation and remaining work

The 203-file dirty working state was archived and verified before these repairs.
HEAD remains `6200140feb9b3fe63b2609d09340355c6458edd0` on
`companion/implementation`; the index remains empty. No commit, push, deployment,
remote workflow, new source collection or live AI generation was performed.
Local logs, traces, captured pages and the snapshot remain under ignored `work/`;
compact reports are retained beside this document.

Full source qualification, shared status decoding and live freshness/window
classification, ranking, final carousel/pick selection and editorial lifetimes
remain later P2 work. P3 persistence and the native product remain later stages.
Physical-device, minimum-OS and release-performance acceptance are not claimed.

The [upstream licensing requirements](../data-licensing.md) still govern source
access, delivery, retention, AI use and artwork. Local engineering acceptance
does not establish those permissions. Feed delivery and distribution remain
gated under the user's explicit instruction.
