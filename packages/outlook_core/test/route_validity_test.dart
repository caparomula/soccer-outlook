import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

RouteValidity _facts(Map<String, dynamic> value) {
  DateTime? instant(String key) =>
      value[key] == null ? null : DateTime.parse(value[key] as String);
  final date = (value['rights_until'] as String?)
      ?.split('-')
      .map(int.parse)
      .toList();
  return RouteValidity(
    basis: RouteBasis.values.byName(value['basis'] as String),
    retained: value['retained'] as bool,
    retentionSeconds: value['retention_seconds'] as int,
    verifiedAt: instant('verified_at'),
    expiresAt: instant('expires_at'),
    kickoffUtc: instant('kickoff_utc'),
    rightsUntil: date == null ? null : RightsDate(date[0], date[1], date[2]),
  );
}

void main() {
  final corpus = jsonDecode(
    File('../../docs/companion/rules/route-validity-examples.json')
        .readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final raw in corpus['cases'] as List<dynamic>) {
    final example = raw as Map<String, dynamic>;
    test('WATCH-VALIDITY-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final result = evaluateRouteValidity(
          _facts(value),
          DateTime.parse(value['now_utc'] as String),
        );
        results.add({
          'id': value['id'],
          'usable': result.usable,
          'reason': result.reason?.wireName,
          'expires_at': result.expiresAt?.toIso8601String(),
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
    });
  }

  final now = DateTime.parse('2026-10-20T19:40:19.958459Z');
  final lineup = ViewerLineup(
    selectedServiceIds: ['first', 'second'],
    serviceOrder: ['first', 'second'],
    serviceNames: {'first': 'First', 'second': 'Second'},
  );
  RouteCandidate listed(
    String label,
    String service, {
    DateTime? expiry,
    RouteBasis basis = RouteBasis.listed,
  }) => RouteCandidate(
    label: label,
    serviceIds: [service],
    spanish: false,
    validity: RouteValidity(
      basis: basis,
      retained: false,
      retentionSeconds: 21600,
      expiresAt: expiry,
    ),
  );
  RouteCandidate usual({RightsDate? until}) => RouteCandidate(
    label: 'Usual',
    serviceIds: ['first'],
    spanish: false,
    validity: RouteValidity(
      basis: RouteBasis.rule,
      retained: false,
      retentionSeconds: 21600,
      rightsUntil: until,
    ),
  );

  test('expired preferred candidate cannot grant providers or defeat an independent route', () {
    final result = selectRoute(
      listed: [
        listed('First', 'first', expiry: now),
        listed('Second', 'second'),
      ],
      lineup: lineup,
      nowUtc: now,
    );
    expect(result.serviceId, 'second');
    expect(result.basis, RouteBasis.listed);
    expect(result.listedProviders, [
      <String>[],
      ['second'],
    ]);
    expect(result.listedValidity.map((entry) => entry?.usable), [false, true]);
    expect(result.listedValidity.first?.reason, RouteValidityReason.expired);
    expect(
      result.listedValidity.first?.expiresAt?.toIso8601String(),
      '2026-10-20T19:40:19.958459Z',
    );
    expect(result.listedValidity.clear, throwsUnsupportedError);
  });

  test(
    'usual fallback must be explicitly supplied and independently valid',
    () {
      final candidates = [listed('First', 'first', expiry: now)];
      expect(
        selectRoute(listed: candidates, lineup: lineup, nowUtc: now).serviceId,
        isNull,
      );
      final available = selectRoute(
        listed: candidates,
        usual: usual(until: RightsDate(2026, 10, 20)),
        lineup: lineup,
        nowUtc: now,
      );
      expect(available.serviceId, 'first');
      expect(available.basis, RouteBasis.rule);
      expect(available.usualProviders, ['first']);
      expect(
        available.usualValidity?.expiresAt,
        DateTime.parse('2026-10-21T04:00:00Z'),
      );
      final expired = selectRoute(
        listed: candidates,
        usual: usual(until: RightsDate(2026, 10, 19)),
        lineup: lineup,
        nowUtc: now,
      );
      expect(expired.serviceId, isNull);
      expect(expired.usualProviders, isEmpty);
      expect(expired.usualValidity?.reason, RouteValidityReason.expired);
    },
  );

  test(
    'unknown temporal basis fails closed even with selected service IDs',
    () {
      final result = selectRoute(
        listed: [listed('First', 'first', basis: RouteBasis.none)],
        lineup: lineup,
        nowUtc: now,
      );
      expect(result.serviceId, isNull);
      expect(result.listedProviders, [<String>[]]);
      expect(
        result.listedValidity.single?.reason,
        RouteValidityReason.unknownBasis,
      );
    },
  );

  test('validity metadata cannot be silently evaluated without a clock', () {
    expect(
      () => selectRoute(listed: [listed('First', 'first')], lineup: lineup),
      throwsArgumentError,
    );
  });

  test('candidate location and validity basis cannot conflict', () {
    expect(
      () => selectRoute(
        listed: [usual(until: RightsDate(2026, 10, 20))],
        lineup: lineup,
        nowUtc: now,
      ),
      throwsArgumentError,
    );
    expect(
      () => selectRoute(
        listed: [],
        usual: listed('First', 'first'),
        lineup: lineup,
        nowUtc: now,
      ),
      throwsArgumentError,
    );
  });

  test('already-qualified inputs retain their existing decision without temporal metadata', () {
    final candidate = RouteCandidate(
      label: 'First',
      serviceIds: ['first'],
      spanish: false,
    );
    final result = selectRoute(
      listed: [candidate],
      lineup: lineup,
      nowUtc: now,
    );
    expect(result.serviceId, 'first');
    expect(result.listedProviders, [
      ['first'],
    ]);
    expect(result.listedValidity, [null]);
    expect(result.usualValidity, isNull);
  });

  test(
    'snapshot applies validity before composing fixture and strip eligibility',
    () {
      OutlookSnapshot snapshot(DateTime clock) => buildOutlook(
        OutlookInput(
          fixtures: [
            OutlookFixture(
              id: 'match',
              leagueId: 'league',
              kickoffUtc: now,
              timeConfirmed: true,
              sourceState: FixtureSourceState.pre,
              receivedAt: null,
              listed: [listed('First', 'first', expiry: now)],
            ),
          ],
          leagues: const [
            LeagueCatalogEntry(id: 'league', defaultFollowed: true),
          ],
          leaguePreferences: LeaguePreferences(choices: {}, paused: []),
          lineup: lineup,
          nowUtc: clock,
          timeZone: 'UTC',
        ),
      );
      final before = snapshot(DateTime.parse('2026-10-20T19:40:19.958458Z'));
      expect(before.fixtures.single.eligibility.scheduleEligible, isTrue);
      expect(before.eligibility.stripLeagueIds, ['league']);
      final at = snapshot(now);
      expect(at.fixtures.single.route.serviceId, isNull);
      expect(at.fixtures.single.eligibility.eligible, isFalse);
      expect(at.fixtures.single.eligibility.scheduleEligible, isFalse);
      expect(at.eligibility.stripLeagueIds, isEmpty);
    },
  );

  test(
    'direct callers cannot supply local clocks or out-of-range retention',
    () {
      expect(
        () => RouteValidity(
          basis: RouteBasis.listed,
          retained: false,
          retentionSeconds: -1,
        ),
        throwsArgumentError,
      );
      expect(
        () => RouteValidity(
          basis: RouteBasis.listed,
          retained: false,
          retentionSeconds: 21601,
        ),
        throwsArgumentError,
      );
      expect(
        () => RouteValidity(
          basis: RouteBasis.listed,
          retained: true,
          retentionSeconds: 21600,
          verifiedAt: DateTime(2026),
        ),
        throwsArgumentError,
      );
      expect(
        () => evaluateRouteValidity(
          RouteValidity(
            basis: RouteBasis.listed,
            retained: false,
            retentionSeconds: 21600,
          ),
          DateTime(2026),
        ),
        throwsArgumentError,
      );
      expect(() => RightsDate(2026, 2, 30), throwsArgumentError);
    },
  );
}
