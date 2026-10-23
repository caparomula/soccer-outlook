import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

void main() {
  final corpus = jsonDecode(
    File('../../docs/companion/rules/fixture-window-examples.json')
        .readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final raw in corpus['cases'] as List<dynamic>) {
    final example = raw as Map<String, dynamic>;
    test('TIME-WINDOW-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final original = jsonEncode(request);
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final decision = evaluateFixtureWindow(
          FixtureWindowFacts(
            kickoffUtc: DateTime.parse(value['kickoff_utc'] as String),
            timeConfirmed: value['time_confirmed'] as bool,
            sourceState: FixtureSourceState.values.singleWhere(
              (state) => state.wireName == value['source_state'],
            ),
            receivedAt: value['received_at'] == null
                ? null
                : DateTime.parse(value['received_at'] as String),
          ),
          DateTime.parse(value['now_utc'] as String),
          value['time_zone'] as String,
        );
        results.add({
          'id': value['id'],
          'day_index': decision.dayIndex,
          'bucket': decision.bucket?.wireName,
          'in_window': decision.inWindow,
          'in_focus': decision.inFocus,
          'source_in_progress': decision.sourceInProgress,
          'recent_live_receipt': decision.recentLiveReceipt,
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
      expect(jsonEncode(request), original);
    });
  }

  final now = DateTime.utc(2026, 10, 23, 17);

  test('unknown phase preserves placement without asserting progress or receipt freshness', () {
    final pending = evaluateFixtureWindow(
      FixtureWindowFacts(
        kickoffUtc: now.subtract(const Duration(minutes: 1)),
        timeConfirmed: true,
        sourceState: FixtureSourceState.unknown,
        receivedAt: now,
      ),
      now,
      'America/New_York',
    );
    expect(pending.bucket, FixtureBucket.live);
    expect(pending.inFocus, isTrue);
    expect(pending.sourceInProgress, isFalse);
    expect(pending.recentLiveReceipt, isFalse);
    for (final confirmed in [true, false]) {
      final old = evaluateFixtureWindow(
        FixtureWindowFacts(
          kickoffUtc: now.subtract(const Duration(hours: 4)),
          timeConfirmed: confirmed,
          sourceState: FixtureSourceState.unknown,
          receivedAt: now,
        ),
        now,
        'America/New_York',
      );
      expect(
        old.bucket,
        confirmed ? FixtureBucket.earlier : FixtureBucket.today,
      );
      expect(old.inFocus, isFalse);
      expect(old.sourceInProgress, isFalse);
      expect(old.recentLiveReceipt, isFalse);
    }
  });

  final lineup = ViewerLineup(
    selectedServiceIds: ['service'],
    serviceOrder: ['service'],
    serviceNames: {'service': 'Service'},
  );
  OutlookInput input(
    OutlookFixture fixture,
    DateTime clock, {
    String? soloId,
  }) => OutlookInput(
    fixtures: [fixture],
    leagues: const [LeagueCatalogEntry(id: 'league', defaultFollowed: true)],
    leaguePreferences: LeaguePreferences(
      choices: {},
      paused: [],
      soloId: soloId,
    ),
    lineup: lineup,
    nowUtc: clock,
    timeZone: 'America/New_York',
  );

  test(
    'sports-day rollover removes the last solo fixture from the schedule',
    () {
      final fixture = OutlookFixture(
        id: 'oldest',
        leagueId: 'league',
        kickoffUtc: DateTime.utc(2026, 10, 20, 8),
        timeConfirmed: true,
        sourceState: FixtureSourceState.post,
        receivedAt: null,
        listed: [
          RouteCandidate(
            label: 'Service',
            serviceIds: ['service'],
            spanish: false,
          ),
        ],
      );
      final before = buildOutlook(
        input(fixture, DateTime.utc(2026, 10, 24, 7, 59), soloId: 'league'),
      );
      expect(before.fixtures.single.window.bucket, FixtureBucket.dayMinus3);
      expect(before.fixtures.single.window.inFocus, isFalse);
      expect(before.fixtures.single.eligibility.scheduleEligible, isTrue);
      expect(before.eligibility.activeSoloId, 'league');

      final at = buildOutlook(
        input(fixture, DateTime.utc(2026, 10, 24, 8), soloId: 'league'),
      );
      expect(at.fixtures.single.window.dayIndex, -4);
      expect(at.fixtures.single.window.bucket, isNull);
      expect(at.fixtures.single.eligibility.eligible, isTrue);
      expect(at.fixtures.single.eligibility.scheduleEligible, isFalse);
      expect(at.eligibility.stripLeagueIds, isEmpty);
      expect(at.eligibility.activeSoloId, isNull);
      expect(at.eligibility.soloResetReason, SoloResetReason.noEligibleFixture);
    },
  );

  test(
    'one clock expires route and receipt without rewriting source progress',
    () {
      final received = now.subtract(const Duration(minutes: 10));
      final fixture = OutlookFixture(
        id: 'observed',
        leagueId: 'league',
        kickoffUtc: now.subtract(const Duration(hours: 4)),
        timeConfirmed: true,
        sourceState: FixtureSourceState.inProgress,
        receivedAt: received,
        listed: [
          RouteCandidate(
            label: 'Service',
            serviceIds: ['service'],
            spanish: false,
            validity: RouteValidity(
              basis: RouteBasis.listed,
              retained: false,
              retentionSeconds: 21600,
              expiresAt: now,
            ),
          ),
        ],
      );
      final before = buildOutlook(
        input(fixture, now.subtract(const Duration(microseconds: 1))),
      );
      expect(before.fixtures.single.window.bucket, FixtureBucket.live);
      expect(before.fixtures.single.window.recentLiveReceipt, isTrue);
      expect(before.fixtures.single.route.serviceId, 'service');
      expect(before.fixtures.single.eligibility.scheduleEligible, isTrue);

      final at = buildOutlook(input(fixture, now));
      expect(at.fixtures.single.window.bucket, FixtureBucket.earlier);
      expect(at.fixtures.single.window.recentLiveReceipt, isFalse);
      expect(at.fixtures.single.window.sourceInProgress, isTrue);
      expect(at.fixtures.single.route.serviceId, isNull);
      expect(at.fixtures.single.eligibility.scheduleEligible, isFalse);
      expect(fixture.receivedAt, received);
      expect(fixture.sourceState, FixtureSourceState.inProgress);
      expect(
        buildOutlook(input(fixture, before.nowUtc))
            .fixtures
            .single
            .window
            .recentLiveReceipt,
        isTrue,
        reason:
            'Re-evaluation uses supplied facts and clock, not cached wall age',
      );
    },
  );

  test('direct window callers must supply UTC instants', () {
    FixtureWindowFacts facts(DateTime kickoff, DateTime? received) =>
        FixtureWindowFacts(
          kickoffUtc: kickoff,
          timeConfirmed: true,
          sourceState: FixtureSourceState.inProgress,
          receivedAt: received,
        );
    expect(() => facts(DateTime(2026), null), throwsArgumentError);
    expect(() => facts(now, DateTime(2026)), throwsArgumentError);
    expect(
      () => evaluateFixtureWindow(facts(now, null), DateTime(2026), 'UTC'),
      throwsArgumentError,
    );
  });

  test(
    'unsupported zones fail before an out-of-window result can hide them',
    () {
      expect(
        () => evaluateFixtureWindow(
          FixtureWindowFacts(
            kickoffUtc: DateTime.utc(2020),
            timeConfirmed: true,
            sourceState: FixtureSourceState.post,
            receivedAt: null,
          ),
          now,
          'Unknown/Nowhere',
        ),
        throwsArgumentError,
      );
    },
  );

  test('composed input rejects local receipt timestamps', () {
    expect(
      () => OutlookFixture(
        id: 'bad-receipt',
        leagueId: 'league',
        kickoffUtc: now,
        timeConfirmed: true,
        sourceState: FixtureSourceState.inProgress,
        receivedAt: DateTime(2026),
        listed: [],
      ),
      throwsArgumentError,
    );
  });
}
