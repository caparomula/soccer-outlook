# P2 migration reference

`legacy-route-selection-2026-10-20.json` freezes the 360 route inputs from the
complete P1 collection and the previous Python owner's answer under four
lineups: owner defaults, all plans in reverse preference order, an explicitly
empty selection, and a selected subset. Each default answer was also compared
with the original captured `Match` value before this file was written.

The source, original context, and pre-change archive hashes identify the
capture. The previous route function and browser sports-day helpers are retained
as test-only evidence. They are never loaded by production adapters and are not
a runtime fallback. The 17 unchanged calendar examples still live in
`tool/outlook_bridge/cases/sports_day.json`.

These historical answers prove agreement on the captured inputs; they do not
define correct behavior. Independently authored examples and reasons live in
`docs/companion/rules/`. The contradictory unknown-channel defense and the
historical Apia correction are explicit deltas, outside unchanged legacy
parity. No new source requests were made for this corpus.

Run `tool/companion_p2_verify.py` after preparing the pinned runtime with
`tool/build_outlook_runtime.py`. Evidence is written under ignored `work/`;
the verifier never rewrites expected answers.
