# P2 viewer eligibility increment

October 20, 2026. This increment implements
[VIEWER-ELIGIBILITY-1](rules/viewer-eligibility.md): saved league choices,
pauses, temporary solo mode, strip membership and common fixture eligibility.
P2 remains in progress. Source permission and publication authorization remain
distribution gates; no companion feed or app has been deployed.

## Shared decisions and real consumers

The immutable `projectViewerEligibility` owner in
[`outlook_core`](../../packages/outlook_core/lib/src/eligibility.dart) accepts
the complete ordered league catalog, explicit choices, pauses, requested solo,
selected-service presence and fixture route/window facts. It returns league
visibility, resolved solo, strip membership, base fixture eligibility, schedule
eligibility and the separate calendar-editorial league gate.

The [browser](../../web/app.js) now reads those decisions for `followed`,
`hiddenByStrip`, `compHidden`, `passes`, `editorialPasses`, strip membership and
`personalLeagueEligible`. Schedule rows read `schedule_eligible`. Elsewhere and
live polling continue to use league visibility without acquiring a selected-route
requirement. Calendar editorial uses all known leagues, including leagues without
fixture pills. Existing carousel, pick and editorial candidate rules still run
after the shared base filter.

Python's `shown_by_default` and static filter-pill enabled state now call the
same core through [the verified runtime](../../outlook_runtime.py). Published
default policy facts retain their authoring owner. Static HTML still contains
every fetched row; this migration does not redesign its existing fallback.

`buildOutlook` now requires league catalog/preferences and fixture league/window
inputs and composes route, calendar and eligibility results. It derives route
access from `selectRoute`, but `inWindow` remains caller-owned: a computed day
index alone does not classify status or decide schedule membership.

The browser's single cached projection includes catalog/defaults/order,
preferences, selected-service presence and every fixture's route/window facts.
Window facts and bucket labels refresh before each draw. Even a cache hit
installs decisions on replacement DOM rows. Python caches immutable default-policy
tuples, so a changed default invalidates its answer. Saved preferences for absent
leagues remain intact. Input normalization and storage/UI actions stay in their
existing adapters; durable/atomic preference storage is still P3 work.

Independent review reproduced a UI regression introduced by using every catalog
league in the existing fold-state signature: selecting fixture-free leagues or
reordering paused leagues collapsed expanded results although the visible rows
were unchanged. A new browser regression failed for both cases before repair.
The signature now uses the sorted hidden leagues represented by current schedule
pills, while the shared eligibility projection still includes the full catalog.
Both cases pass in Chromium and WebKit. The failing/passing source hashes and logs
are retained in `work/p2-fold-review/` and the final validation record.

## Independent and captured evidence

The [eligibility report](evidence/p2-eligibility-2026-10-20.json) records:

- Sixteen independently authored examples, written before consumer replacement,
  across Dart VM, compiled CLI, Chromium and WebKit.
- Nine original-browser scenarios over 360 retained fixtures: **3,240 fixture
  comparisons** plus league, strip and calendar-editorial projections. The
  frozen answers preserve the original build clock and precede the new adapters.
- Ten production-page transitions per browser: initial view, pause, solo,
  restore, reload, explicit disable, empty fixture week, returning fixtures,
  enabling a default-off league and clearing services. These pages load the
  actual external script with matching content address and SHA-256 integrity.
- Fixture-free calendar prompt eligibility, preserved saved choices/pauses and
  unchanged factual row bindings throughout those transitions. Prompt creation
  is local; it makes no AI call. Neither browser reports script errors.
- Rejection of malformed payloads in VM, CLI and both browsers, plus strict CSP
  for the compiled core. The website retains its existing presentation scripts
  and styles; this does not claim strict CSP for the complete page.

The [core report](evidence/p2-eligibility-core-2026-10-20.json) records **78 Dart
tests**, clean core/bridge analyzers and formatting, 42 old/new protocol examples,
recovery after 13 malformed frames and an accepted 10,000-fixture case.
The [route/calendar regression report](evidence/p2-eligibility-route-calendar-2026-10-20.json)
retains all 1,440 previous route comparisons, 72 generated-page cases and four
failed/corrupt-asset checks against this increment's sources.

Unknown/disabled/expired solo, unknown fixture leagues and a contradictory route
with an empty service selection are explicitly marked defensive cases. They do
not claim unchanged legacy behavior for invalid inputs or a reproduced defect
in ordinary UI actions. The ordinary captured scenarios retain their answers.

## Builds, size and regression checks

The [offline full/lightweight replay](evidence/p2-eligibility-replay-2026-10-20.json)
uses the retained raw source responses at their original clock, with no network
fallback. All **360 identities and route selections**, required feed artifact
bytes and observation ages match the prior accepted publication. Both new
publications pass the independent Dart reader, and their revisions advance
from 1 to 2. The original capture remains unchanged.

Both generated pages are **2,670,253 bytes**, below the unchanged 3,300,000-byte
limit, with all **448 original logo rules**. The complete browser core is
**1,568,327 bytes / 192,798 gzip bytes**, below the 204,800-byte gzip limit.
Only **12,002 gzip bytes** remain; later families must remeasure the complete
artifact. These local checks do not certify physical-device performance.

The [final validation record](evidence/p2-eligibility-validation-2026-10-20.json)
binds these reports to the final sources and retained logs. **701 Python tests**
and **174 browser regression checks** pass: 112 Chromium, 40 WebKit smoke and
11 schedule checks per engine. The full Chromium suite loaded the test harness
before the checkpoint-wait repair described below; both affected tests also pass
separately against the final harness in both engines. The clean WebKit smoke run
uses that final harness. Production sources are identical across these runs.

Browser tracing also exposed a test deadline problem: the checkpoint test's
30-second response waiter expired while Playwright was still executing simulated
timer callbacks, then the expected poll received HTTP 200. The repaired test
records responses throughout clock advancement and bounds any remaining wait
afterward. It preserves every timer callback and still requires a new response
and request. Production polling is unchanged. Both affected tests pass in
Chromium and WebKit. A separate initial WebKit `networkidle` timeout occurred
before filter assertions; it did not recur in the focused checks or clean smoke
run. Its cause remains unknown, and its failed log is retained with the diagnosis
rather than attributed to the clock issue.

The companion workflow runs the new verifier and evidence tests alongside the
existing route/calendar proof, core unit tests and analyzers. Workflow changes
were checked locally; no remote workflow was run.

## Remaining P2 work and local state

Source-evidence expiry, shared status decoding, live freshness and window
classification remain separate families. Ranking, carousel/pick selection and
editorial content/lifetime semantics also remain. This increment shares their
base league/route gate without claiming their final candidates or scores.
P3 persistence/coordinated refresh, the native product and device acceptance
remain later work.

The pre-increment state is preserved in ignored `work/p2-eligibility-start/`:
177 changed/new files, an archive, tracked patch and SHA-256 manifest. Previous
evidence is retained. All changes remain local and uncommitted; no pushes,
deployments, AI generation or app distribution were performed.
