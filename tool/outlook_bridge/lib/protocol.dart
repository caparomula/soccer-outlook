import 'dart:convert';

import 'package:outlook_core/outlook_core.dart';
import 'package:outlook_data/espn_decoder.dart';

final _calendar = SportsDayCalendar();
final _utcPattern = RegExp(
  r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$',
);
final _datePattern = RegExp(r'^(\d{4})-(\d{2})-(\d{2})$');
const _validityFields = {
  'basis',
  'retained',
  'verified_at',
  'expires_at',
  'kickoff_utc',
  'rights_until',
  'retention_seconds',
};

const maxProtocolLength = 4 * 1024 * 1024;
const invalidInputResponse = '{"version":1,"error":"invalid_input"}';

Never _invalid() => throw const FormatException('Invalid protocol input');

Map<String, dynamic> _object(
  Object? value,
  Set<String> required, [
  Set<String> optional = const {},
]) {
  if (value is! Map<String, dynamic> ||
      !value.keys.toSet().containsAll(required) ||
      value.keys.any(
        (key) => !required.contains(key) && !optional.contains(key),
      )) {
    _invalid();
  }
  return value;
}

String _string(Object? value) {
  if (value is! String || value.isEmpty || value.length > 1024) _invalid();
  return value;
}

List<dynamic> _list(Object? value, int limit) {
  if (value is! List<dynamic> || value.length > limit) _invalid();
  return value;
}

List<String> _strings(Object? value) => _list(value, 128).map(_string).toList();

DateTime _instant(Object? value) {
  if (value is! String || !_utcPattern.hasMatch(value)) _invalid();
  final parsed = DateTime.parse(value);
  if (parsed.toIso8601String().substring(0, 19) != value.substring(0, 19)) {
    _invalid();
  }
  return parsed;
}

RouteCandidate _route(Object? value) {
  final route = _object(
    value,
    {'label', 'service_ids', 'spanish'},
    {'unknown', 'validity'},
  );
  if (route['spanish'] is! bool ||
      (route.containsKey('unknown') && route['unknown'] is! bool))
    _invalid();
  return RouteCandidate(
    label: _string(route['label']),
    serviceIds: _strings(route['service_ids']),
    spanish: route['spanish'] as bool,
    unknown: route['unknown'] as bool? ?? false,
    validity: route.containsKey('validity')
        ? _routeValidity(route['validity'])
        : null,
  );
}

RouteBasis _basis(Object? value) => switch (value) {
  'listed' => RouteBasis.listed,
  'rule' => RouteBasis.rule,
  'none' => RouteBasis.none,
  _ => _invalid(),
};

FixtureSourceState _sourceState(Object? value) => switch (value) {
  'pre' => FixtureSourceState.pre,
  'in' => FixtureSourceState.inProgress,
  'post' => FixtureSourceState.post,
  'unknown' => FixtureSourceState.unknown,
  _ => _invalid(),
};

Map<String, Object?> _statusResult(DecodedMatchStatus value) => {
  'state': value.state.name,
  'phase': value.phase.wireName,
  'provider_state': value.providerState,
  'label': value.label,
  'played': value.played,
  'final': value.isFinal,
  'void': value.isVoid,
  'reported_scores': {
    'home': value.reportedScores.home,
    'away': value.reportedScores.away,
  },
  'scores': {'home': value.scores.home, 'away': value.scores.away},
};

RightsDate _rightsDate(Object? value) {
  if (value is! String) _invalid();
  final match = _datePattern.firstMatch(value);
  if (match == null) _invalid();
  return RightsDate(
    int.parse(match[1]!),
    int.parse(match[2]!),
    int.parse(match[3]!),
  );
}

RouteValidity _routeValidity(Object? raw) {
  final value = _object(raw, _validityFields);
  final retention = value['retention_seconds'];
  // JSON has one numeric type. Accept mathematical integers consistently in
  // both the VM decoder and JS, whose numbers do not retain a .0 distinction.
  if (retention is! num ||
      !retention.isFinite ||
      retention != retention.truncateToDouble() ||
      retention < 0 ||
      retention > 21600)
    _invalid();
  return RouteValidity(
    basis: _basis(value['basis']),
    retained: _boolean(value['retained']),
    verifiedAt: value['verified_at'] == null
        ? null
        : _instant(value['verified_at']),
    expiresAt: value['expires_at'] == null
        ? null
        : _instant(value['expires_at']),
    kickoffUtc: value['kickoff_utc'] == null
        ? null
        : _instant(value['kickoff_utc']),
    rightsUntil: value['rights_until'] == null
        ? null
        : _rightsDate(value['rights_until']),
    retentionSeconds: retention.toInt(),
  );
}

Map<String, Object?> _validityResult(RouteValidityDecision value) => {
  'usable': value.usable,
  'reason': value.reason?.wireName,
  'expires_at': value.expiresAt?.toIso8601String(),
};

ViewerLineup _lineup(Object? value) {
  final lineup = _object(value, {
    'selected_service_ids',
    'service_order',
    'service_names',
  });
  final names = lineup['service_names'];
  if (names is! Map<String, dynamic> || names.length > 128) _invalid();
  final serviceNames = <String, String>{};
  for (final entry in names.entries) {
    serviceNames[_string(entry.key)] = _string(entry.value);
  }
  return ViewerLineup(
    selectedServiceIds: _strings(lineup['selected_service_ids']),
    serviceOrder: _strings(lineup['service_order']),
    serviceNames: serviceNames,
  );
}

Map<String, Object> _day(SportsDay day) => {
  'date': day.date,
  'start_utc': day.startUtc.toIso8601String(),
  'end_utc': day.endUtc.toIso8601String(),
};

bool _boolean(Object? value) {
  if (value is! bool) _invalid();
  return value;
}

ViewerEligibilityInput _eligibilityInput(Map<String, dynamic> raw) {
  final value = _object(raw, {
    'id',
    'leagues',
    'preferences',
    'has_selected_services',
    'fixtures',
  });
  final preferences = _object(value['preferences'], {
    'choices',
    'paused',
    'solo_id',
  });
  final choices = preferences['choices'];
  if (choices is! Map<String, dynamic> || choices.length > 128) _invalid();
  final selected = <String, bool>{};
  for (final entry in choices.entries) {
    selected[_string(entry.key)] = _boolean(entry.value);
  }
  return ViewerEligibilityInput(
    leagues: _list(value['leagues'], 128).map((rawLeague) {
      final league = _object(rawLeague, {'id', 'default_followed'});
      return LeagueCatalogEntry(
        id: _string(league['id']),
        defaultFollowed: _boolean(league['default_followed']),
      );
    }),
    preferences: LeaguePreferences(
      choices: selected,
      paused: _strings(preferences['paused']),
      soloId: preferences['solo_id'] == null
          ? null
          : _string(preferences['solo_id']),
    ),
    hasSelectedServices: _boolean(value['has_selected_services']),
    fixtures: _list(value['fixtures'], 10000).map((rawFixture) {
      final fixture = _object(rawFixture, {
        'id',
        'league_id',
        'has_route',
        'in_window',
      });
      return EligibilityFixture(
        id: _string(fixture['id']),
        leagueId: _string(fixture['league_id']),
        hasRoute: _boolean(fixture['has_route']),
        inWindow: _boolean(fixture['in_window']),
      );
    }),
  );
}

/// A versioned JSON boundary shared by VM, compiled publisher and browser.
/// All business decisions live in outlook_core. A failed case rejects its batch.
String evaluate(String input) {
  try {
    if (input.length > maxProtocolLength) _invalid();
    final decoded = jsonDecode(input);
    if (decoded is! Map<String, dynamic> || decoded['version'] != 1) _invalid();
    final operation = decoded['operation'];
    if (operation != 'sports_day' &&
        operation != 'calendar_shift' &&
        operation != 'route_selection' &&
        operation != 'route_validity' &&
        operation != 'fixture_window' &&
        operation != 'live_freshness' &&
        operation != 'espn_status' &&
        operation != 'fixture_status' &&
        operation != 'viewer_eligibility')
      _invalid();
    _object(decoded, {
      'version',
      'operation',
      'cases',
      if (operation == 'route_selection') 'lineup',
    }, operation == 'route_selection' ? {'now_utc'} : {});
    final routeNow =
        operation == 'route_selection' && decoded.containsKey('now_utc')
        ? _instant(decoded['now_utc'])
        : null;
    final lineup = operation == 'route_selection'
        ? _lineup(decoded['lineup'])
        : null;
    final cases = _list(decoded['cases'], 1000);
    final ids = <String>{};
    final results = <Map<String, Object?>>[];
    for (final raw in cases) {
      if (raw is! Map<String, dynamic>) _invalid();
      final id = _string(raw['id']);
      if (!ids.add(id)) _invalid();
      if (operation == 'espn_status' || operation == 'fixture_status') {
        final value = _object(
          raw,
          {
            'id',
            if (operation == 'espn_status') ...[
              'event_status',
              'competition_status',
            ] else ...[
              'phase',
              'label',
            ],
            'home_score',
            'away_score',
          },
          {if (operation == 'fixture_status') 'state'},
        );
        final status = operation == 'espn_status'
            ? decodeEspnStatus(
                eventStatus: value['event_status'],
                competitionStatus: value['competition_status'],
                homeScore: value['home_score'],
                awayScore: value['away_score'],
              )
            : decodeFixtureStatus(
                state: value['state'],
                phase: value['phase'],
                label: value['label'],
                homeScore: value['home_score'],
                awayScore: value['away_score'],
              );
        results.add({'id': id, ..._statusResult(status)});
      } else if (operation == 'live_freshness') {
        final value = _object(raw, {
          'id',
          'now_utc',
          'source_state',
          'received_at',
        });
        final decision = evaluateSourceFreshness(
          SourceFreshnessFacts(
            sourceState: _sourceState(value['source_state']),
            receivedAt: value['received_at'] == null
                ? null
                : _instant(value['received_at']),
          ),
          _instant(value['now_utc']),
        );
        results.add({
          'id': id,
          'live_now': decision.liveNow,
          'last_known': decision.lastKnown,
          'reason': decision.reason?.wireName,
          'expires_at': decision.expiresAt?.toIso8601String(),
          'next_transition_at': decision.nextTransitionAt?.toIso8601String(),
        });
      } else if (operation == 'fixture_window') {
        final value = _object(raw, {
          'id',
          'now_utc',
          'time_zone',
          'kickoff_utc',
          'time_confirmed',
          'source_state',
          'received_at',
        });
        final decision = evaluateFixtureWindow(
          FixtureWindowFacts(
            kickoffUtc: _instant(value['kickoff_utc']),
            timeConfirmed: _boolean(value['time_confirmed']),
            sourceState: _sourceState(value['source_state']),
            receivedAt: value['received_at'] == null
                ? null
                : _instant(value['received_at']),
          ),
          _instant(value['now_utc']),
          _string(value['time_zone']),
        );
        results.add({
          'id': id,
          'day_index': decision.dayIndex,
          'bucket': decision.bucket?.wireName,
          'in_window': decision.inWindow,
          'in_focus': decision.inFocus,
          'source_in_progress': decision.sourceInProgress,
          'recent_live_receipt': decision.recentLiveReceipt,
        });
      } else if (operation == 'route_validity') {
        final value = _object(raw, {'id', 'now_utc', ..._validityFields});
        final facts = _routeValidity({
          for (final key in _validityFields) key: value[key],
        });
        results.add({
          'id': id,
          ..._validityResult(
            evaluateRouteValidity(facts, _instant(value['now_utc'])),
          ),
        });
      } else if (operation == 'viewer_eligibility') {
        final projection = projectViewerEligibility(_eligibilityInput(raw));
        results.add({
          'id': id,
          'active_solo_id': projection.activeSoloId,
          'solo_reset_reason': projection.soloResetReason?.wireName,
          'strip_league_ids': projection.stripLeagueIds,
          'leagues': projection.leagues
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
          'fixtures': projection.fixtures
              .map(
                (fixture) => {
                  'id': fixture.id,
                  'eligible': fixture.eligible,
                  'schedule_eligible': fixture.scheduleEligible,
                },
              )
              .toList(),
        });
      } else if (operation == 'route_selection') {
        final value = _object(raw, {'id', 'listed', 'usual'});
        final decision = selectRoute(
          listed: _list(value['listed'], 128).map(_route),
          usual: value['usual'] == null ? null : _route(value['usual']),
          lineup: lineup!,
          nowUtc: routeNow,
        );
        results.add({
          'id': id,
          'service_id': decision.serviceId,
          'basis': decision.basis.name,
          'outlet': decision.outlet,
          'listed_providers': decision.listedProviders,
          'usual_providers': decision.usualProviders,
          if (routeNow != null)
            'listed_validity': decision.listedValidity
                .map(
                  (validity) =>
                      validity == null ? null : _validityResult(validity),
                )
                .toList(),
          if (routeNow != null)
            'usual_validity': decision.usualValidity == null
                ? null
                : _validityResult(decision.usualValidity!),
        });
      } else if (operation == 'calendar_shift') {
        final value = _object(raw, {
          'id',
          'instant',
          'time_zone',
          'offset_days',
        });
        final offset = value['offset_days'];
        if (offset is! int || offset < -3660 || offset > 3660) _invalid();
        final day = _calendar.shift(
          _instant(value['instant']),
          _string(value['time_zone']),
          offset,
        );
        results.add({
          'id': id,
          ..._day(day),
          'year': day.year,
          'month': day.month,
          'day': day.day,
        });
      } else {
        final value = _object(raw, {'id', 'instant', 'reference', 'time_zone'});
        final zone = _string(value['time_zone']);
        final day = _calendar.containing(_instant(value['instant']), zone);
        final reference = _calendar.containing(
          _instant(value['reference']),
          zone,
        );
        results.add({
          'id': id,
          ..._day(day),
          'day_index': day.indexFrom(reference),
        });
      }
    }
    return jsonEncode({'version': 1, 'results': results});
  } on FormatException {
    // Never echo caller input or exception text into output or stderr.
    return invalidInputResponse;
  } on ArgumentError {
    return invalidInputResponse;
  }
}
