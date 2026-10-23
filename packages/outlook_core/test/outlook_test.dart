import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

void main() {
  final kickoff = DateTime.parse('2026-11-01T09:00:00Z');
  final now = DateTime.parse('2026-10-31T08:00:00Z');

  test('composed snapshot uses supplied sports-day clock across DST', () {
    final snapshot = buildOutlook(
      OutlookInput(
        leagues: const [
          LeagueCatalogEntry(id: 'league', defaultFollowed: true),
        ],
        leaguePreferences: LeaguePreferences(choices: {}, paused: []),
        fixtures: [
          OutlookFixture(
            leagueId: 'league',
            timeConfirmed: true,
            sourceState: FixtureSourceState.pre,
            receivedAt: null,
            id: 'match',
            kickoffUtc: kickoff,
            listed: [
              RouteCandidate(label: 'TNT', serviceIds: ['max'], spanish: false),
            ],
          ),
        ],
        lineup: ViewerLineup(
          selectedServiceIds: ['max'],
          serviceOrder: ['max'],
          serviceNames: {'max': 'HBO Max'},
        ),
        nowUtc: now,
        timeZone: 'America/New_York',
      ),
    );
    expect(snapshot.referenceDay.date, '2026-10-31');
    expect(snapshot.referenceDay.endUtc.difference(now).inHours, 25);
    expect(snapshot.nowUtc, now);
    expect(snapshot.timeZone, 'America/New_York');
    expect(snapshot.fixtures.single.id, 'match');
    expect(snapshot.fixtures.single.day.date, '2026-11-01');
    expect(snapshot.fixtures.single.dayIndex, 1);
    expect(snapshot.fixtures.single.window.bucket, FixtureBucket.tomorrow);
    expect(snapshot.fixtures.single.window.inFocus, isFalse);
    expect(snapshot.fixtures.single.window.day, snapshot.fixtures.single.day);
    expect(snapshot.fixtures.single.route.serviceId, 'max');
    expect(snapshot.fixtures.single.route.basis, RouteBasis.listed);
    expect(snapshot.fixtures.single.eligibility.eligible, isTrue);
    expect(snapshot.fixtures.single.eligibility.scheduleEligible, isTrue);
    expect(snapshot.eligibility.stripLeagueIds, ['league']);
    expect(snapshot.fixtures.clear, throwsUnsupportedError);
  });

  test('caller mutations cannot alter immutable input or result', () {
    final providers = ['first', 'second'];
    final selected = ['first', 'second'];
    final order = ['second', 'first'];
    final names = {'first': 'First', 'second': 'Second'};
    final route = RouteCandidate(
      label: 'Channel',
      serviceIds: providers,
      spanish: false,
    );
    final listed = [route];
    final lineup = ViewerLineup(
      selectedServiceIds: selected,
      serviceOrder: order,
      serviceNames: names,
    );
    final fixture = OutlookFixture(
      leagueId: 'league',
      timeConfirmed: true,
      sourceState: FixtureSourceState.pre,
      receivedAt: null,
      id: 'match',
      kickoffUtc: kickoff,
      listed: listed,
    );
    final fixtures = [fixture];
    final input = OutlookInput(
      leagues: const [LeagueCatalogEntry(id: 'league', defaultFollowed: true)],
      leaguePreferences: LeaguePreferences(choices: {}, paused: []),
      fixtures: fixtures,
      lineup: lineup,
      nowUtc: now,
      timeZone: 'UTC',
    );
    providers.clear();
    selected.clear();
    order.clear();
    names.clear();
    listed.clear();
    fixtures.clear();
    final decision = buildOutlook(input).fixtures.single.route;
    expect(decision.serviceId, 'second');
    expect(decision.listedProviders, [
      ['second', 'first'],
    ]);
    expect(route.serviceIds.clear, throwsUnsupportedError);
    expect(lineup.serviceOrder.clear, throwsUnsupportedError);
    expect(lineup.serviceNames.clear, throwsUnsupportedError);
    expect(lineup.selectedServiceIds.clear, throwsUnsupportedError);
    expect(fixture.listed.clear, throwsUnsupportedError);
    expect(input.fixtures.clear, throwsUnsupportedError);
    expect(decision.listedProviders.clear, throwsUnsupportedError);
    expect(decision.listedProviders.single.clear, throwsUnsupportedError);
    expect(decision.usualProviders.clear, throwsUnsupportedError);
  });

  test(
    'selected IDs absent from preference order retain first equal-rank route',
    () {
      final result = selectRoute(
        listed: [
          RouteCandidate(
            label: 'Channel',
            serviceIds: ['z', 'a'],
            spanish: false,
          ),
          RouteCandidate(label: 'Later', serviceIds: ['a'], spanish: false),
        ],
        lineup: ViewerLineup(
          selectedServiceIds: ['a', 'z'],
          serviceOrder: [],
          serviceNames: {},
        ),
      );
      expect(result.serviceId, 'z');
      expect(result.outlet, 'Channel');
      expect(result.listedProviders, [
        ['z', 'a'],
        ['a'],
      ]);
    },
  );

  test('fixture and snapshot reject implicit local clocks', () {
    expect(
      () => OutlookFixture(
        leagueId: 'league',
        timeConfirmed: true,
        sourceState: FixtureSourceState.pre,
        receivedAt: null,
        id: 'x',
        kickoffUtc: DateTime(2026),
        listed: [],
      ),
      throwsArgumentError,
    );
    expect(
      () => OutlookInput(
        leagues: const [
          LeagueCatalogEntry(id: 'league', defaultFollowed: true),
        ],
        leaguePreferences: LeaguePreferences(choices: {}, paused: []),
        fixtures: [],
        lineup: ViewerLineup(
          selectedServiceIds: [],
          serviceOrder: [],
          serviceNames: {},
        ),
        nowUtc: DateTime(2026),
        timeZone: 'UTC',
      ),
      throwsArgumentError,
    );
  });

  test('unknown timezone fails even for an empty snapshot', () {
    expect(
      () => buildOutlook(
        OutlookInput(
          leagues: const [
            LeagueCatalogEntry(id: 'league', defaultFollowed: true),
          ],
          leaguePreferences: LeaguePreferences(choices: {}, paused: []),
          fixtures: [],
          lineup: ViewerLineup(
            selectedServiceIds: [],
            serviceOrder: [],
            serviceNames: {},
          ),
          nowUtc: now,
          timeZone: 'Unknown/Nowhere',
        ),
      ),
      throwsArgumentError,
    );
  });
}
