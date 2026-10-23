import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

void main() {
  final corpus = jsonDecode(
    File('../../docs/companion/rules/live-response-freshness-examples.json')
        .readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final raw in corpus['cases'] as List<dynamic>) {
    final example = raw as Map<String, dynamic>;
    test('LIVE-FRESHNESS-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final original = jsonEncode(request);
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final decision = evaluateSourceFreshness(
          SourceFreshnessFacts(
            sourceState: FixtureSourceState.values.singleWhere(
              (state) => state.wireName == value['source_state'],
            ),
            receivedAt: value['received_at'] == null
                ? null
                : DateTime.parse(value['received_at'] as String),
          ),
          DateTime.parse(value['now_utc'] as String),
        );
        results.add({
          'id': value['id'],
          'live_now': decision.liveNow,
          'last_known': decision.lastKnown,
          'reason': decision.reason?.wireName,
          'expires_at': decision.expiresAt?.toIso8601String(),
          'next_transition_at': decision.nextTransitionAt?.toIso8601String(),
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
      expect(jsonEncode(request), original);
    });
  }

  final now = DateTime.utc(2026, 10, 23, 17);
  test('direct freshness callers must supply UTC instants', () {
    expect(
      () => SourceFreshnessFacts(
        sourceState: FixtureSourceState.inProgress,
        receivedAt: DateTime(2026),
      ),
      throwsArgumentError,
    );
    expect(
      () => evaluateSourceFreshness(
        SourceFreshnessFacts(
          sourceState: FixtureSourceState.pre,
          receivedAt: null,
        ),
        DateTime(2026),
      ),
      throwsArgumentError,
    );
  });

  test('re-evaluation preserves facts and uses only the supplied clock', () {
    final facts = SourceFreshnessFacts(
      sourceState: FixtureSourceState.inProgress,
      receivedAt: now,
    );
    final before = evaluateSourceFreshness(facts, now);
    final after = evaluateSourceFreshness(
      facts,
      now.add(const Duration(minutes: 3)),
    );
    expect(before.liveNow, isTrue);
    expect(after.liveNow, isFalse);
    expect(after.lastKnown, isTrue);
    expect(facts.sourceState, FixtureSourceState.inProgress);
    expect(facts.receivedAt, now);
    expect(evaluateSourceFreshness(facts, now).liveNow, isTrue);
  });
}
