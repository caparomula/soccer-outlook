# ESPN form-order evidence

`espn-2026-10-20.json` contains minimized factual excerpts from eleven public ESPN
responses: three October 24 scoreboards and eight game summaries, covering sixteen
teams in `eng.1`, `usa.1`, and `mex.1`. Each request records its URL, SHA-256, byte
count, attempt time and receipt time. Source observation time is unknown (`null`);
HTTP response dates and collection times are not substituted for it. Original
responses are retained locally in `work/companion-form-evidence/final/`.
The capture ran from 2026-10-20T19:36:42.808116Z to 19:36:46.015587Z.

The replay derives W/D/L independently from each dated result's numeric home and
away scores, sorts by explicit UTC dates and compares that history with the raw
scoreboard `competitors[].form` and the summary header's corresponding value.
It does not import `build.py`, use its normalization, or treat ESPN's separate
`gameResult` field as the expected result. That field is checked against scores.
Aggregate and shootout semantics are outside this sample and cause rejection.

| Team | Raw source form | Dated scores, oldest first | Finding |
| --- | --- | --- | --- |
| Chelsea | WWLDW | WDLWW | Exact newest-first match |
| Toluca | DLDLW | WLDLD | Exact newest-first match |
| CF Montréal | DLLWL | LWLLD | Exact newest-first match |
| Nashville SC | DWWWW | WWWWD | Exact newest-first match |
| Guadalajara | DDLDW | DLWDD | Neither direction matches |

Four non-palindromic sequences match newest-first; none match oldest-first.
Twelve sequences disagree with the dated results in both directions. Eleven of
those twelve differ from the newest-first sequence only in the first result;
Guadalajara differs in three positions. All sixteen raw scoreboard and summary
header form strings agree with one another. Chelsea's confirming history includes
a Carabao Cup game, so the agreement is not limited to league-only results.
Thus fifteen of sixteen observations agree on all four oldest positions under
the newest-first interpretation. The report retains each mismatch's zero-based
index to make this narrower finding reproducible.

This supports **an inference that the form field is newest-first** across the
sampled leagues. It does not prove that every reported result is correct, establish
the timestamp or scope of an arbitrary form string, or justify replacing source
values with the dated summary history. The capture contains current responses
for future fixtures, not historical snapshots as of each fixture's kickoff.
No global production normalization is changed by this probe. This is enough
empirical direction evidence to reverse **source-reported** W/D/L values for an
oldest-to-newest contract if the producer retains the raw value and distinguishes
source-reported values from a verified history. The source's form as-of time and
precise competition scope must remain unknown. Correcting discrepancies, asserting
that the current fixture supplies the form timestamp, or claiming every result
was corroborated would exceed this evidence. The canonical companion documents
record whether the producer adopts that bounded convention.

Offline reproduction:

```sh
work/companion-venv/bin/python tool/companion_form_verify.py
work/companion-venv/bin/python -m unittest tests.test_companion_form_evidence
```

Explicit bounded recapture (requires network; use a new output directory):

```sh
work/companion-venv/bin/python tool/companion_form_verify.py \
  --capture work/companion-form-recapture \
  --report work/companion-form-recapture/report.json
```

Recapture uses the same eleven URLs, so later responses may have different form
values and dated histories. Preserve the old evidence; do not overwrite this
fixture or its expected observations merely to make a replay pass. The command
reports disagreement as evidence, not as a network or parser failure.
