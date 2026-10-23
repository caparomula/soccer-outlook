# Draft companion contract

Unshipped contract draft, dated October 20, 2026. These schemas now support the
local P1 exporter and independent reader; they do **not** describe an available
endpoint or establish a supported public API. See the
[P0 review](../../../docs/companion/p0-contract.md),
[current P1 evidence](../../../docs/companion/p1-continuation-evidence.md) and
[operation guide](../../../docs/companion/p1-operation.md).

The JSON Schemas use Draft 2020-12. Load the local schema files into a registry;
their reserved `.invalid` IDs are identifiers, not network locations. Each
artifact schema references `common.schema.json`. The manifest names the required
catalog, schedule and policy, plus an explicit optional editorial descriptor or
null.

[examples/manifest.json](examples/manifest.json) references four complete,
content-addressed example artifacts. Digests and decoded byte counts match the
checked-in UTF-8 bytes, including the final newline. Fixtures, teams, timestamps
and evidence are **synthetic**. This small example catalog and its defaults are
not the current complete site catalog or lineup.

The [schedule cases](../../../tests/contracts/schedules) cover complete empty
data, a partial broadcast source with retained evidence, unknown kickoff and
unknown date zone, a missing score alongside a real zero, excluded packages,
replay rejection, additive unknown fields and qualified usual coverage.

Run the draft checks using a Python environment with
[the isolated test dependency](../../../tests/contracts/requirements.txt):

```sh
python -m pip install -r tests/contracts/requirements.txt
python -m unittest tests.test_companion_contracts -v
```

The existing standard suite reports these tests as skipped if `jsonschema` is
absent. A skip is not contract validation. These checks do not exercise a
production exporter, Dart parser, snapshot transaction, or viewing decision.

The separate P1 verifier runs the real exporter and independent reader:

```sh
python tool/companion_feed_verify.py --out work/companion-p1 --dart dart
```

Install the pinned dependencies and prepare both Dart packages as documented
in the operation guide first. The generated sample is synthetic; the optional
historical projection does not prove original collection coverage. The draft
allows unknown source observation and shared publication timestamps to be
null, with original receipt/prompt times retained separately. The stable
[network ID registry](../network-ids.json) assigns identity and delivery scope;
`rights.toml` remains the authority for aliases and plan carriage.
