/// A competition in the caller's established display order.
final class LeagueCatalogEntry {
  const LeagueCatalogEntry({required this.id, required this.defaultFollowed});

  final String id;
  final bool defaultFollowed;
}

/// Saved choices and pauses are never modified by a temporary solo projection.
/// Preferences for absent catalog entries remain caller-owned and are ignored.
final class LeaguePreferences {
  LeaguePreferences({
    required Map<String, bool> choices,
    required Iterable<String> paused,
    this.soloId,
  }) : choices = Map.unmodifiable(choices),
       paused = Set.unmodifiable(paused);

  final Map<String, bool> choices;
  final Set<String> paused;
  final String? soloId;
}

/// Route availability and window membership have explicit upstream owners.
/// Window membership currently includes the caller's status/bucket decisions.
final class EligibilityFixture {
  const EligibilityFixture({
    required this.id,
    required this.leagueId,
    required this.hasRoute,
    required this.inWindow,
  });

  final String id;
  final String leagueId;
  final bool hasRoute;
  final bool inWindow;
}

final class ViewerEligibilityInput {
  ViewerEligibilityInput({
    required Iterable<LeagueCatalogEntry> leagues,
    required this.preferences,
    required this.hasSelectedServices,
    required Iterable<EligibilityFixture> fixtures,
  }) : leagues = List.unmodifiable(leagues),
       fixtures = List.unmodifiable(fixtures);

  final List<LeagueCatalogEntry> leagues;
  final LeaguePreferences preferences;
  final bool hasSelectedServices;
  final List<EligibilityFixture> fixtures;
}

enum SoloResetReason {
  unknown,
  disabled,
  noEligibleFixture;

  String get wireName => switch (this) {
    unknown => 'unknown',
    disabled => 'disabled',
    noEligibleFixture => 'no_eligible_fixture',
  };
}

final class LeagueEligibility {
  const LeagueEligibility._({
    required this.id,
    required this.followed,
    required this.hiddenByStrip,
    required this.visible,
    required this.calendarEditorialEligible,
  });

  final String id;
  final bool followed;
  final bool hiddenByStrip;
  final bool visible;

  /// A league-level calendar/editorial gate, independent of fixture availability.
  /// Match-specific editorial status, freshness and content validity are separate.
  final bool calendarEditorialEligible;
}

final class FixtureEligibility {
  const FixtureEligibility._({
    required this.id,
    required this.eligible,
    required this.scheduleEligible,
  });

  final String id;
  final bool eligible;
  final bool scheduleEligible;
}

final class ViewerEligibilityProjection {
  ViewerEligibilityProjection._({
    required this.activeSoloId,
    required this.soloResetReason,
    required Iterable<String> stripLeagueIds,
    required Iterable<LeagueEligibility> leagues,
    required Iterable<FixtureEligibility> fixtures,
  }) : stripLeagueIds = List.unmodifiable(stripLeagueIds),
       leagues = List.unmodifiable(leagues),
       fixtures = List.unmodifiable(fixtures);

  final String? activeSoloId;
  final SoloResetReason? soloResetReason;
  final List<String> stripLeagueIds;
  final List<LeagueEligibility> leagues;
  final List<FixtureEligibility> fixtures;
}

/// VIEWER-ELIGIBILITY-1: combine catalog defaults, viewer choices, saved pauses,
/// temporary solo and already-qualified route/window facts without platform state.
ViewerEligibilityProjection projectViewerEligibility(
  ViewerEligibilityInput input,
) {
  final followed = <String, bool>{};
  for (final league in input.leagues) {
    if (followed.containsKey(league.id))
      throw ArgumentError('Duplicate league id');
    followed[league.id] =
        input.preferences.choices[league.id] ?? league.defaultFollowed;
  }
  final fixtureIds = <String>{};
  final scheduled = <String>{};
  for (final fixture in input.fixtures) {
    if (!fixtureIds.add(fixture.id))
      throw ArgumentError('Duplicate fixture id');
    if (input.hasSelectedServices && fixture.hasRoute && fixture.inWindow) {
      scheduled.add(fixture.leagueId);
    }
  }
  // Pause/solo must not remove their own restore controls from the strip.
  final strip = input.leagues
      .where((league) => followed[league.id]! && scheduled.contains(league.id))
      .map((league) => league.id)
      .toList();
  var activeSolo = input.preferences.soloId;
  SoloResetReason? reset;
  if (activeSolo != null) {
    if (!followed.containsKey(activeSolo)) {
      reset = SoloResetReason.unknown;
    } else if (!followed[activeSolo]!) {
      reset = SoloResetReason.disabled;
    } else if (!scheduled.contains(activeSolo)) {
      reset = SoloResetReason.noEligibleFixture;
    }
    if (reset != null) activeSolo = null;
  }
  final leagues = <LeagueEligibility>[];
  final visible = <String, bool>{};
  for (final league in input.leagues) {
    final hidden = activeSolo != null
        ? league.id != activeSolo
        : input.preferences.paused.contains(league.id);
    final shown = followed[league.id]! && !hidden;
    visible[league.id] = shown;
    leagues.add(
      LeagueEligibility._(
        id: league.id,
        followed: followed[league.id]!,
        hiddenByStrip: hidden,
        visible: shown,
        calendarEditorialEligible: shown && input.hasSelectedServices,
      ),
    );
  }
  return ViewerEligibilityProjection._(
    activeSoloId: activeSolo,
    soloResetReason: reset,
    stripLeagueIds: strip,
    leagues: leagues,
    fixtures: input.fixtures.map((fixture) {
      final eligible =
          input.hasSelectedServices &&
          fixture.hasRoute &&
          (visible[fixture.leagueId] ?? false);
      return FixtureEligibility._(
        id: fixture.id,
        eligible: eligible,
        scheduleEligible: eligible && fixture.inWindow,
      );
    }),
  );
}
