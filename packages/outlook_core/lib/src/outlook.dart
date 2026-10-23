import 'eligibility.dart';
import 'fixture_window.dart';
import 'routes.dart';
import 'sports_day.dart';

/// Normalized inputs for routes, fixture windows and base viewer eligibility.
final class OutlookFixture {
  OutlookFixture({
    required this.id,
    required this.leagueId,
    required this.kickoffUtc,
    required this.timeConfirmed,
    required this.sourceState,
    required this.receivedAt,
    required Iterable<RouteCandidate> listed,
    this.usual,
  }) : listed = List.unmodifiable(listed) {
    if (!kickoffUtc.isUtc || receivedAt != null && !receivedAt!.isUtc) {
      throw ArgumentError('UTC fixture instants required');
    }
  }

  final String id;
  final String leagueId;
  final DateTime kickoffUtc;

  final bool timeConfirmed;
  final FixtureSourceState sourceState;
  final DateTime? receivedAt;
  final List<RouteCandidate> listed;
  final RouteCandidate? usual;
}

final class OutlookInput {
  OutlookInput({
    required Iterable<OutlookFixture> fixtures,
    required Iterable<LeagueCatalogEntry> leagues,
    required this.leaguePreferences,
    required this.lineup,
    required this.nowUtc,
    required this.timeZone,
  }) : fixtures = List.unmodifiable(fixtures),
       leagues = List.unmodifiable(leagues) {
    if (!nowUtc.isUtc) throw ArgumentError('UTC now required');
  }

  final List<OutlookFixture> fixtures;
  final List<LeagueCatalogEntry> leagues;
  final LeaguePreferences leaguePreferences;
  final ViewerLineup lineup;
  final DateTime nowUtc;
  final String timeZone;
}

final class OutlookFixtureDecision {
  const OutlookFixtureDecision._(
    this.id,
    this.route,
    this.day,
    this.dayIndex,
    this.window,
    this.eligibility,
  );

  final String id;
  final RouteDecision route;
  final SportsDay day;
  final int dayIndex;
  final FixtureWindowDecision window;
  final FixtureEligibility eligibility;
}

/// A partial P2 snapshot combining routes, fixture windows and viewer eligibility.
/// Raw status decoding, provider-observation freshness, ratings and final surface
/// or editorial decisions remain outside this snapshot. Accepted source state
/// and original receipt facts are supplied; window membership is derived here.
final class OutlookSnapshot {
  OutlookSnapshot._(
    this.nowUtc,
    this.timeZone,
    this.referenceDay,
    this.eligibility,
    Iterable<OutlookFixtureDecision> fixtures,
  ) : fixtures = List.unmodifiable(fixtures);

  final DateTime nowUtc;
  final String timeZone;
  final SportsDay referenceDay;
  final ViewerEligibilityProjection eligibility;
  final List<OutlookFixtureDecision> fixtures;
}

/// Every changing input is supplied by the caller; no system clock is consulted.
OutlookSnapshot buildOutlook(OutlookInput input) {
  final calendar = SportsDayCalendar();
  final reference = calendar.containing(input.nowUtc, input.timeZone);
  final routes = input.fixtures
      .map(
        (fixture) => selectRoute(
          listed: fixture.listed,
          usual: fixture.usual,
          lineup: input.lineup,
          nowUtc: input.nowUtc,
        ),
      )
      .toList();
  final windows = input.fixtures
      .map(
        (fixture) => evaluateFixtureWindow(
          FixtureWindowFacts(
            kickoffUtc: fixture.kickoffUtc,
            timeConfirmed: fixture.timeConfirmed,
            sourceState: fixture.sourceState,
            receivedAt: fixture.receivedAt,
          ),
          input.nowUtc,
          input.timeZone,
        ),
      )
      .toList();
  final eligibility = projectViewerEligibility(
    ViewerEligibilityInput(
      leagues: input.leagues,
      preferences: input.leaguePreferences,
      hasSelectedServices: input.lineup.selectedServiceIds.isNotEmpty,
      fixtures: input.fixtures.indexed.map(
        (entry) => EligibilityFixture(
          id: entry.$2.id,
          leagueId: entry.$2.leagueId,
          hasRoute: routes[entry.$1].serviceId != null,
          inWindow: windows[entry.$1].inWindow,
        ),
      ),
    ),
  );
  return OutlookSnapshot._(
    input.nowUtc,
    input.timeZone,
    reference,
    eligibility,
    input.fixtures.indexed.map((entry) {
      final fixture = entry.$2;
      final window = windows[entry.$1];
      return OutlookFixtureDecision._(
        fixture.id,
        routes[entry.$1],
        window.day,
        window.dayIndex,
        window,
        eligibility.fixtures[entry.$1],
      );
    }),
  );
}
