# Outlook data preview

The narrow [`espn_decoder.dart`](lib/espn_decoder.dart) entry point owns
[STATUS-ESPN-1](../../docs/companion/rules/espn-status.md): source status, reported
match-score parsing and separate played/final verdicts. It has no clock,
transport, storage or feed-reader dependency. Both publisher CLI and browser
consume it; provider observation freshness and live reconciliation remain later
work. The feed reader below continues to validate published facts independently.

Independent Dart validation for the local P1 feed. This package consumes the
canonical `contracts/outlook/v1/*.schema.json` registry, supplied as trusted
application data; it never resolves remote schemas. The generic evaluator
supports the vocabulary currently used there and rejects unknown schema
keywords. Field definitions are not copied from the Python producer.

`FeedReader.read(manifestBytes, loader)` returns an immutable required
catalog/schedule/policy snapshot only after all three artifacts pass integrity,
structure, identity and supported-policy checks. Optional editorial returns
`accepted`, `absent` (explicit withdrawal), or `invalid`, with a sanitized error
code. A missing or corrupt editorial artifact does not discard required facts.
The local adapter waits for its bounded file read; future transports must bound
request duration as well as bytes.

This boundary preserves factual scores and qualified routes. It performs no
viewer filtering, ranking, score calculation, HTTP requests, cache installation,
publication ordering, entitlement selection, or editorial eligibility at a
visitor's current time. P3 still owns concurrent refresh, previous editorial
retention, and atomic durable installation. Accepted policy IDs are the explicit
P0 draft and P1 preview profiles, each requiring the six declared draft rule IDs;
this is not a production compatibility commitment.

The decoder checks UTF-8 byte limits (64 KiB manifest; 8 MiB per artifact), depth
64, one million value nodes, 100 properties per object, 30,000 array elements,
and 65,536 encoded characters per string during tokenization. Duplicate keys,
including escaped aliases, non-finite values, integer literals beyond the
JavaScript-safe range, malformed UTF-8 and isolated surrogate escapes fail.
Schema validation applies tighter per-field limits afterward. These limits also
cover unknown additive fields.

Cross-artifact validation checks IDs, catalog/policy revisions, exact source
scope completeness, team/vitals/table identity, route evidence and destination
bindings, explicit exclusions, plan retirement, retained-evidence lifetime, and
original editorial fixture/kickoff and maximum lifetime. It validates actual
IANA zones and UTC dates, credential-free HTTPS authority, valid port ranges,
and conventional credential query keys. Producer allowlisting and source URL
review remain necessary; a parser cannot determine whether every arbitrary URL
path or query value is confidential.

The local CLI is in [`tool/companion_feed_reader`](../../tool/companion_feed_reader/README.md).
Dart 3.13.3, crypto 3.0.7, timezone 0.11.2 and test 1.33.0 are pinned. Tests use the
independently authored P0 corpus, rehash adversarial mutations, and assert
consumer outcomes without importing a Python encoder.
