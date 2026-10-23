import 'dart:convert';
import 'dart:io';

import 'package:outlook_data/espn_decoder.dart';
import 'package:test/test.dart';

Map<String, Object?> result(DecodedMatchStatus value) => {
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

void main() {
  final corpus = jsonDecode(
    File('../../docs/companion/rules/espn-status-examples.json')
        .readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final raw in corpus['cases'] as List<dynamic>) {
    final example = raw as Map<String, dynamic>;
    test('STATUS-ESPN-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final original = jsonEncode(request);
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final decision = request['operation'] == 'espn_status'
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
        results.add({'id': value['id'], ...result(decision)});
      }
      expect({'version': 1, 'results': results}, example['expected']);
      expect(jsonEncode(request), original);
    });
  }

  test('caller mutation cannot change accepted immutable status or scores', () {
    final type = <String, Object?>{'state': 'post', 'description': 'Final'};
    final status = <String, Object?>{'type': type};
    final score = <String, Object?>{'displayValue': '0'};
    final decoded = decodeEspnStatus(
      eventStatus: null,
      competitionStatus: status,
      homeScore: score,
      awayScore: 2,
    );
    type['description'] = 'Abandoned';
    score['displayValue'] = '7';
    status.clear();
    expect(decoded.state, DecodedMatchState.finished);
    expect(decoded.label, 'FT');
    expect(decoded.reportedScores.home, 0);
    expect(decoded.scores.away, 2);
  });

  test(
    'score parser consumes entire ASCII token and rejects nonfinite numbers',
    () {
      for (final value in <Object?>[
        '2\n',
        '2\r\n',
        '２',
        '+2',
        '2e0',
        double.nan,
        double.infinity,
        double.negativeInfinity,
      ]) {
        final decoded = decodeFixtureStatus(
          phase: 'in',
          label: "9'",
          homeScore: value,
          awayScore: '000',
        );
        expect(decoded.reportedScores.home, isNull, reason: '$value');
        expect(decoded.reportedScores.away, 0);
        expect(decoded.played, isTrue);
      }
    },
  );

  test(
    'recognition normalizes identities but preserves ordinary clock display',
    () {
      final decoded = decodeEspnStatus(
        eventStatus: null,
        competitionStatus: {
          'type': {
            'state': 'in',
            'name': ' status_in_progress ',
            'description': ' LIVE ',
            'completed': 'true',
          },
          'displayClock': " 63' ",
        },
        homeScore: 0,
        awayScore: 0,
      );
      expect(decoded.state, DecodedMatchState.live);
      expect(decoded.label, " 63' ");
      final wrongPhase = decodeFixtureStatus(
        phase: 'IN',
        label: 'Live',
        homeScore: 2,
        awayScore: 1,
      );
      expect(wrongPhase.state, DecodedMatchState.unknown);
      expect(wrongPhase.providerState, 'IN');
      expect(wrongPhase.scores.home, isNull);
    },
  );

  test('numeric IDs and notes cannot establish a final identity', () {
    final decoded = decodeEspnStatus(
      eventStatus: null,
      competitionStatus: {
        'type': {
          'state': 'post',
          'id': '28',
          'detail': 'FT',
          'completed': true,
        },
        'displayClock': 'FT',
      },
      homeScore: 2,
      awayScore: 1,
    );
    expect(decoded.state, DecodedMatchState.unknown);
    expect(decoded.phase, DecodedMatchPhase.post);
    expect(decoded.isFinal, isFalse);
    expect(decoded.reportedScores.home, 2);
    expect(decoded.scores.home, isNull);
  });

  test(
    'overlong identity and seam displays stay within feed string bounds',
    () {
      final long = 'x' * 101;
      final unsupported = decodeEspnStatus(
        eventStatus: null,
        competitionStatus: {
          'type': {'state': 'in', 'name': long, 'description': 'Live'},
        },
        homeScore: 1,
        awayScore: 0,
      );
      expect(unsupported.state, DecodedMatchState.unknown);
      expect(unsupported.label, 'Status unknown');
      final fallback = decodeFixtureStatus(
        phase: 'in',
        label: long,
        homeScore: 1,
        awayScore: 0,
      );
      expect(fallback.state, DecodedMatchState.live);
      expect(fallback.label, 'Live');
      final exact = decodeFixtureStatus(
        phase: 'in',
        label: 'x' * 100,
        homeScore: null,
        awayScore: null,
      );
      expect(exact.label.length, 100);
    },
  );

  test('exception identity does not depend on phase or completion flags', () {
    for (final phase in <Object?>[null, 'pre', 'in', 'post', 'unfamiliar', 3]) {
      final decoded = decodeEspnStatus(
        eventStatus: null,
        competitionStatus: {
          'type': {
            'state': phase,
            'name': 'STATUS_POSTPONED',
            'completed': true,
          },
        },
        homeScore: 2,
        awayScore: 1,
      );
      expect(decoded.state, DecodedMatchState.postponed);
      expect(decoded.phase, DecodedMatchPhase.post);
      expect(decoded.isVoid, isTrue);
      expect(decoded.played, isFalse);
      expect(decoded.reportedScores.home, 2);
      expect(decoded.scores.home, isNull);
    }
  });

  test(
    'bound canonical state requires its exact phase and ignores label identity',
    () {
      const phases = {
        'scheduled': ['pre'],
        'live': ['in'],
        'finished': ['post'],
        'postponed': ['post'],
        'cancelled': ['post'],
        'abandoned': ['post'],
        'unknown': ['post', 'unknown'],
      };
      for (final state in phases.keys) {
        for (final phase in <Object?>[
          'pre',
          'in',
          'post',
          'unknown',
          'IN',
          null,
          1,
        ]) {
          final decoded = decodeFixtureStatus(
            state: state,
            phase: phase,
            label: 'FT',
            homeScore: 0,
            awayScore: 1,
          );
          expect(
            decoded.state.name,
            phases[state]!.contains(phase) ? state : 'unknown',
            reason: '$state / $phase',
          );
        }
      }
      for (final state in <Object?>['LIVE', '', false, 0, [], {}]) {
        final decoded = decodeFixtureStatus(
          state: state,
          phase: 'in',
          label: 'Live',
          homeScore: 1,
          awayScore: 0,
        );
        expect(decoded.state, DecodedMatchState.unknown, reason: '$state');
        expect(decoded.scores.home, isNull);
      }
    },
  );

  test('bound live display stays literal while absent and oversized display fall back', () {
    for (final label in <Object?>[
      'Halftime',
      'FT',
      'Postponed',
      ' 72\' ',
      '',
      null,
      'x' * 101,
    ]) {
      final decoded = decodeFixtureStatus(
        state: 'live',
        phase: 'in',
        label: label,
        homeScore: {'displayValue': '0'},
        awayScore: '2',
      );
      expect(decoded.state, DecodedMatchState.live);
      expect(
        decoded.label,
        label is String && label.isNotEmpty && label.length <= 100
            ? label
            : 'Live',
      );
      expect(decoded.scores.home, 0);
      expect(decoded.scores.away, 2);
    }
  });
}
