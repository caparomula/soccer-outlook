import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

ViewerEligibilityInput _input(Map<String, dynamic> value) {
  final preferences = value['preferences'] as Map<String, dynamic>;
  return ViewerEligibilityInput(
    leagues: (value['leagues'] as List<dynamic>).map((dynamic raw) {
      final league = raw as Map<String, dynamic>;
      return LeagueCatalogEntry(
        id: league['id'] as String,
        defaultFollowed: league['default_followed'] as bool,
      );
    }),
    preferences: LeaguePreferences(
      choices: (preferences['choices'] as Map<String, dynamic>)
          .cast<String, bool>(),
      paused: (preferences['paused'] as List<dynamic>).cast<String>(),
      soloId: preferences['solo_id'] as String?,
    ),
    hasSelectedServices: value['has_selected_services'] as bool,
    fixtures: (value['fixtures'] as List<dynamic>).map((dynamic raw) {
      final fixture = raw as Map<String, dynamic>;
      return EligibilityFixture(
        id: fixture['id'] as String,
        leagueId: fixture['league_id'] as String,
        hasRoute: fixture['has_route'] as bool,
        inWindow: fixture['in_window'] as bool,
      );
    }),
  );
}

void main() {
  final corpus = jsonDecode(
    File('../../docs/companion/rules/eligibility-examples.json')
        .readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final raw in corpus['cases'] as List<dynamic>) {
    final example = raw as Map<String, dynamic>;
    test('VIEWER-ELIGIBILITY-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final original = jsonEncode(request);
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final decision = projectViewerEligibility(_input(value));
        results.add({
          'id': value['id'],
          'active_solo_id': decision.activeSoloId,
          'solo_reset_reason': decision.soloResetReason?.wireName,
          'strip_league_ids': decision.stripLeagueIds,
          'leagues': decision.leagues
              .map(
                (league) => {
                  'id': league.id,
                  'followed': league.followed,
                  'hidden_by_strip': league.hiddenByStrip,
                  'visible': league.visible,
                  'calendar_editorial_eligible':
                      league.calendarEditorialEligible,
                },
              )
              .toList(),
          'fixtures': decision.fixtures
              .map(
                (fixture) => {
                  'id': fixture.id,
                  'eligible': fixture.eligible,
                  'schedule_eligible': fixture.scheduleEligible,
                },
              )
              .toList(),
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
      expect(
        jsonEncode(request),
        original,
        reason: 'Projection must preserve caller preferences',
      );
    });
  }

  test('caller mutation cannot change eligibility inputs or decisions', () {
    final choices = {'a': true, 'absent': true};
    final paused = ['a', 'absent'];
    final leagues = [const LeagueCatalogEntry(id: 'a', defaultFollowed: false)];
    final fixtures = [
      const EligibilityFixture(
        id: 'fixture',
        leagueId: 'a',
        hasRoute: true,
        inWindow: true,
      ),
    ];
    final preferences = LeaguePreferences(
      choices: choices,
      paused: paused,
      soloId: 'a',
    );
    final input = ViewerEligibilityInput(
      leagues: leagues,
      preferences: preferences,
      hasSelectedServices: true,
      fixtures: fixtures,
    );
    choices.clear();
    paused.clear();
    leagues.clear();
    fixtures.clear();
    final decision = projectViewerEligibility(input);
    expect(decision.activeSoloId, 'a');
    expect(decision.fixtures.single.eligible, isTrue);
    expect(preferences.choices, {'a': true, 'absent': true});
    expect(preferences.paused, {'a', 'absent'});
    expect(preferences.choices.clear, throwsUnsupportedError);
    expect(preferences.paused.clear, throwsUnsupportedError);
    expect(input.leagues.clear, throwsUnsupportedError);
    expect(input.fixtures.clear, throwsUnsupportedError);
    expect(decision.leagues.clear, throwsUnsupportedError);
    expect(decision.fixtures.clear, throwsUnsupportedError);
    expect(decision.stripLeagueIds.clear, throwsUnsupportedError);
  });

  test('ambiguous duplicate league identities are rejected', () {
    expect(
      () => projectViewerEligibility(
        ViewerEligibilityInput(
          leagues: const [
            LeagueCatalogEntry(id: 'a', defaultFollowed: true),
            LeagueCatalogEntry(id: 'a', defaultFollowed: false),
          ],
          preferences: LeaguePreferences(choices: {}, paused: []),
          hasSelectedServices: true,
          fixtures: [],
        ),
      ),
      throwsArgumentError,
    );
  });

  test('ambiguous duplicate fixture identities are rejected', () {
    expect(
      () => projectViewerEligibility(
        ViewerEligibilityInput(
          leagues: const [LeagueCatalogEntry(id: 'a', defaultFollowed: true)],
          preferences: LeaguePreferences(choices: {}, paused: []),
          hasSelectedServices: true,
          fixtures: const [
            EligibilityFixture(
              id: 'x',
              leagueId: 'a',
              hasRoute: true,
              inWindow: true,
            ),
            EligibilityFixture(
              id: 'x',
              leagueId: 'a',
              hasRoute: false,
              inWindow: false,
            ),
          ],
        ),
      ),
      throwsArgumentError,
    );
  });

  test(
    'composed outlook derives routes and windows before viewer eligibility',
    () {
      final routes = [
        RouteCandidate(
          label: 'Service',
          serviceIds: ['service'],
          spanish: false,
        ),
      ];
      final snapshot = buildOutlook(
        OutlookInput(
          fixtures: [
            OutlookFixture(
              id: 'outside',
              leagueId: 'a',
              kickoffUtc: DateTime.utc(2026, 10, 24, 12),
              timeConfirmed: true,
              sourceState: FixtureSourceState.pre,
              receivedAt: null,
              listed: routes,
            ),
            OutlookFixture(
              id: 'no-route',
              leagueId: 'a',
              kickoffUtc: DateTime.utc(2026, 10, 20, 12),
              timeConfirmed: true,
              sourceState: FixtureSourceState.pre,
              receivedAt: null,
              listed: [],
            ),
            OutlookFixture(
              id: 'unknown',
              leagueId: 'unknown',
              kickoffUtc: DateTime.utc(2026, 10, 20, 12),
              timeConfirmed: true,
              sourceState: FixtureSourceState.pre,
              receivedAt: null,
              listed: routes,
            ),
          ],
          leagues: const [LeagueCatalogEntry(id: 'a', defaultFollowed: true)],
          leaguePreferences: LeaguePreferences(
            choices: {},
            paused: [],
            soloId: 'a',
          ),
          lineup: ViewerLineup(
            selectedServiceIds: ['service'],
            serviceOrder: ['service'],
            serviceNames: {'service': 'Service'},
          ),
          nowUtc: DateTime.utc(2026, 10, 20, 12),
          timeZone: 'UTC',
        ),
      );
      expect(snapshot.eligibility.activeSoloId, isNull);
      expect(
        snapshot.eligibility.soloResetReason,
        SoloResetReason.noEligibleFixture,
      );
      expect(
        snapshot.eligibility.leagues.single.calendarEditorialEligible,
        isTrue,
      );
      expect(snapshot.fixtures.map((fixture) => fixture.eligibility.eligible), [
        true,
        false,
        false,
      ]);
      expect(
        snapshot.fixtures.map(
          (fixture) => fixture.eligibility.scheduleEligible,
        ),
        [false, false, false],
      );
      expect(snapshot.fixtures.first.day.date, '2026-10-24');
      expect(snapshot.fixtures.first.window.bucket, isNull);
      expect(snapshot.fixtures.first.route.serviceId, 'service');
    },
  );
}
