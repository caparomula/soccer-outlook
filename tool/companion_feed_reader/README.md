# Offline companion feed reader

Read a locally generated `api/v1` directory through the independent Dart data
boundary. No command here fetches or publishes anything.

From the repository root, with the pinned Dart 3.13.3 executable on `PATH`:

```sh
PUB_CACHE="$PWD/work/pub-cache" dart --suppress-analytics pub get --enforce-lockfile --directory packages/outlook_data
PUB_CACHE="$PWD/work/pub-cache" dart --suppress-analytics pub get --enforce-lockfile --directory tool/companion_feed_reader
PUB_CACHE="$PWD/work/pub-cache" dart --suppress-analytics run tool/companion_feed_reader/bin/read.dart \
  --api-dir contracts/outlook/v1/examples \
  --schema-dir contracts/outlook/v1
```

Substitute the generated directory containing `manifest.json` for `--api-dir`.
Schemas are the checked-in application registry, not data taken from the feed.
The CLI prints one compact JSON object containing publication revision, policy
ID, exact source scopes and confirmed horizon, fixture IDs, and each fixture's
team IDs, timing, status, Outlook facts and factual route IDs/basis/plan sets.
Optional editorial state is reported separately. Invalid required input prints
`{"version":1,"error":"invalid_publication"}`, a sanitized error code to stderr,
and exits 65; invalid usage exits 64. An invalid optional editorial artifact is
reported separately in the otherwise successful summary.

The adapter checks file length and enforces the limit again while streaming.
Paths resolve inside the supplied directory; a symlink escaping that directory
is rejected. Digest and byte checks detect data changed during a local read.
This is a local immutable-snapshot adapter, not a concurrent HTTP/cache client.
See the [data package](../../packages/outlook_data/README.md) for parser limits
and the work still owned by P3.

Run the two Dart suites from their package directories:

```sh
cd packages/outlook_data
PUB_CACHE="../../work/pub-cache" dart --suppress-analytics test
cd ../../tool/companion_feed_reader
PUB_CACHE="../../work/pub-cache" dart --suppress-analytics test
```
