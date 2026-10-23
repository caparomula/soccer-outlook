# P1 local feed evidence

This is the initial P1 checkpoint. The subsequent
[P1 continuation evidence](p1-continuation-evidence.md) records fresh source
reconciliation, bounded form normalization and the WebKit corrections. Its
current disposition supersedes the open work and failed browser gate below;
the original measurements and reports remain unchanged.

October 20, 2026. P1 now has an opt-in producer, durable local publication and
an independent Dart reader. P1 remains in progress: fresh source-to-rendered
reconciliation, verified normalized form and a separately authorized public
feed are still required by the [implementation plan](implementation-plan.md).
The user explicitly authorized local engineering while leaving source usage
permission as a distribution gate. No workflow was run remotely, no repository
variable was enabled, and no feed or app was deployed.

## Implemented boundary

| Owner | Implemented behavior |
| --- | --- |
| [`companion_export.py`](../../companion_export.py) | Projects unfiltered builder facts, canonical Outlook components, qualified routes/evidence, defaults and original editorial bindings into the preview contract. |
| [`companion_contract.py`](../../companion_contract.py) | Bounded strict JSON and local schema validation, integrity, identity, references and source coverage. |
| [`companion_publication.py`](../../companion_publication.py) | Locked publication, durable increasing revision reservations, immutable SHA-256 objects, atomic manifest replacement, validated restoration and retention. |
| [`companion_build.py`](../../companion_build.py) | Opt-in full/lightweight integration; staging before optional story generation and finalization afterward. |
| [`outlook_data`](../../packages/outlook_data/README.md) | Independent Dart required-artifact validation and isolated optional editorial validation from the same local schemas. |
| [`companion_feed_reader`](../../tool/companion_feed_reader/README.md) | Bounded local file loader and machine-readable facts/coverage/route summary. |

The exporter calls existing Python score and route owners. It does not select
a visitor's services, calculate personalized scores or change the website
reader. Preview policy `outlook-policy-p1-preview-1` names the existing draft
rule profiles; P2 still owns their shared implementation and consumer migration.

Both workflow paths are wired behind `COMPANION_FEED_ENABLED == 'true'`, which
remains disabled. Full builds restore the entire previous API tree and fail
closed if existing publication history cannot be fetched. Disabling new export
preserves that prior tree. Local tests cover the build paths and restoration
shell, not a GitHub deployment. See [operation and recovery](p1-operation.md).

## Evidence

The checkout is uncommitted over `9b977bb2b779b55bea84472e81f4ab00da423f1a`.
The [retained machine report](evidence/feed-spike-2026-10-20.json) records exact
source hashes; the base commit alone does not identify these new sources.
The pre-P1 dirty state is preserved under ignored `work/p1-start/` with an
archive, tracked patch and per-file hash manifest.

| Check | Result |
| --- | --- |
| Full Python suite | 657 tests passed using isolated Python 3.13.16. |
| Independent Dart reader | 23 parser tests and 2 local loader tests passed; both packages analyze cleanly on Dart 3.13.3. |
| Production exporter → publisher → Dart | Eight synthetic edge fixtures accepted; required facts and route IDs reconciled with builder inputs. |
| Republish | Revisions 1 then 2; unchanged objects retain exactly the same bytes and original source/editorial times. |
| Optional editorial | Both automatic and shared slots accepted; original generation/prompt and per-item expiry checked; reconversion 30 minutes later produces identical bytes. |
| Corruption | Damaged required artifact rejected; damaged optional editorial marked invalid while the required fixture snapshot remains usable. |
| Captured-shape projection | All 360 retained public fixtures accepted; identities, timing, canonical score components and route plans reconciled. Status is schema-validated, not independently decoded in this projection. Scope limitation below applies. |
| Publication fault tests | Interrupted writes, eight simultaneous processes, sequence reservation, conflicting restoration, retention and rollback to a new higher revision passed. |
| Default website outputs | Opt-out integration parity passed; legacy output does not import or invoke the companion publisher. |
| Chromium browser suite | All 111 tests passed on Chromium 151.0.7922.34. |
| Browser schedule refresh | All 11 tests passed in each of Chromium and WebKit (22 total). |

The WebKit 26.5 smoke run completed 39 tests with five modal-focus assertion
failures and two popup timeouts, across three test methods. It is **not a passing
WebKit gate**. All seven failures reproduce in targeted runs against both
`HEAD:build.py` and the working builder with unchanged web/test sources. The
league-vitals fixture renders byte-identically in both: 354,870 bytes,
SHA-256 `9378f84db3441570e8b14006d7e80a36ecfffe829c0dc792bf9d4e295601a3f3`.
The focus failures leave the open modal with focus on `BODY`; the popup cases
time out waiting for the new window. Changing external navigation fixture MIME
in an ignored runtime probe did not resolve them. No website or browser-test
changes were made to hide these baseline failures. The companion
CI file is prepared to run producer, Dart and retained P0 checks; it has not been
executed on GitHub by this task.

The [browser comparison report](evidence/p1-browser-comparison-2026-10-20.json)
retains source/render/log hashes and exact failure cases. Its referenced logs
are in ignored `work/companion-p1-browser-diagnosis/`. The targeted current
reproduction used a five-second popup timeout; the baseline and original full
smoke run used 30 seconds, so both timeout bounds are represented explicitly.

The eight-fixture generated sample includes scheduled/live/final records,
unknown channel and kickoff, an excluded package, rejected replay and usual
coverage. Its decoded catalog/schedule/policy/editorial sizes are respectively
18,367 / 26,380 / 1,782 / 2,791 bytes. The captured projection's catalog and
schedule are 221,449 and 1,277,799 bytes, below the 8 MiB per-artifact limit.
This is a measured capture, not a worst-case 3,000-fixture sizing proof.

Reproduce the offline round trip using the [operation guide](p1-operation.md).
The exact retained run used `work/companion-p1-final/`. Its optional
`--context work/companion-publication/schedule-context.json` input has SHA-256
`d334aa0bc0f50e52ea1fc7438b44c13fa04a0bdb84fcc066477a830e17637bd2`.
Generated publications and browser artifacts remain under ignored `work/`;
the small report is retained in this documentation.

## Independent review

Separate agents reviewed exporter semantics, publication safety, build
integration, editorial conversion and the Dart parser. Reproduced findings
were fixed before this checkpoint, including incorrect network delivery scope,
lost listing provenance, missing collection-date checks, optional error messages
that exposed rejected URL text, and malformed shared input affecting required
facts. Editorial regressions cover missing-versus-null original kickoff,
carried records inheriting new lifetimes, overlong blurb expiry and unbound
replacement prose inheriting an old origin. The Dart reader now also rejects
malformed percent-encoded query bytes inside the optional error boundary.

Fifteen independent exporter regression cases check these boundaries, including
DST collection intervals and noncontiguous/mixed-zone scope. Parser tests use
the independently authored P0 corpus and rehashed adversarial mutations; the
production round trip additionally checks real exporter bytes. A repeated-team
audit of the 360-fixture capture found no conflicting name, abbreviation, logo
or color fields that catalog deduplication would discard.

## Remaining limits and next work

1. The 360-fixture projection uses explicit **synthetic requested-date scopes**.
   The historical capture lacks the per-request evidence needed to prove them.
   It verifies producer/parser shape and factual projection, not original
   source completeness, same-run rendered equivalence or deployability. A new
   complete collection and matching rendered/source comparison remain P1 work.
2. Source form order is not yet independently established. The preview retains
   `raw_form`, marks its order unverified and exports no normalized form values.
   Rank and per-fixture vitals are exported; full standings tables are not.
3. Provider observation/update timestamps remain null when unavailable. Original
   collection receipt and standings ages are preserved separately. Unknown
   kickoff remains null; its normalized collection date is labeled explicitly.
   The shared production status/result decoder, currentness decisions and live
   correction ordering still require P2/P3 work.
4. Automatic editorial requires original per-entry bindings. Legacy unbound
   entries and cross-league prose that cannot be safely represented are omitted
   with diagnostics. Shared prompt age and automatic focus/generation limits
   are preserved. Page-wide automatic headlines remain website-only; bound
   league headlines and match stories are exported. Missing shared publication
   time stays null. No personal import or provider credential is published.
5. The Dart reader has no HTTP updater, snapshot database, preference store or
   application session. It validates a candidate; P3 must prove ordered,
   durable installation and last-good behavior. No screen uses this preview.
6. P0 physical/minimum-OS and release performance gates remain. Source permission
   and explicit publication authorization are still required before deployment.
   Local success does not establish a public API or start feed support lifetime.
7. Resolve the baseline WebKit modal-focus and external-popup failures before
   claiming the affected-browser exit check passes. The successful Chromium
   run and baseline reproduction narrow the issue; they do not replace a
   passing WebKit result.

The original [P0 evidence](p0-evidence.md) remains a historical checkpoint.
Its source hashes are not expected to match files subsequently changed in P1.
