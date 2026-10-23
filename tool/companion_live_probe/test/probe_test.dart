import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../lib/probe.dart';

void expect(bool value, String message) {
  if (!value) throw StateError(message);
}

Future<void> main() async {
  final input = {
    'events': [
      {
        'id': 'fixture-1',
        'date': '2026-10-20T19:00Z',
        'private': 'DO_NOT_RETAIN',
        'status': {
          'displayClock': '45',
          'type': {'state': 'in', 'description': 'Halftime'},
        },
        'competitions': [
          {
            'id': 'fixture-1',
            'competitors': [
              {
                'id': 'home',
                'homeAway': 'home',
                'score': '0',
                'team': {'id': 'home', 'displayName': 'Home'},
                'private': 'DO_NOT_RETAIN',
              },
              {
                'id': 'away',
                'homeAway': 'away',
                'team': {'id': 'away'},
              },
            ],
          },
        ],
      },
    ],
  };
  final projected = projectScoreboard(input);
  final encoded = jsonEncode(projected);
  expect(!encoded.contains('DO_NOT_RETAIN'), 'Unrelated fields leaked');
  final event = (projected['events'] as List).single as Map;
  expect(event['id'] == 'fixture-1', 'Identity changed');
  final competitors = (event['competitions'] as List).single['competitors'];
  expect(competitors[0]['score'] == '0', 'Zero score changed');
  expect(!competitors[1].containsKey('score'), 'Missing score was invented');
  expect(event['status']['type']['state'] == 'in', 'Wire status changed');
  expect(!encoded.contains('observed_at'), 'Source time was invented');
  final mutableCompetitors =
      (input['events']!.single['competitions'] as List).single['competitors']
          as List;
  mutableCompetitors[1]['score'] = {
    'value': 1.0,
    'displayValue': '1',
    'private': 'DO_NOT_RETAIN',
  };
  final objectScore = jsonEncode(projectScoreboard(input));
  expect(
    objectScore.contains('"displayValue":"1"') &&
        !objectScore.contains('DO_NOT_RETAIN'),
    'Object-shaped score was changed or leaked unrelated fields',
  );

  final capture = jsonDecode(
    File.fromUri(Platform.script.resolve('../captures/2026-10-20-host.json'))
        .readAsStringSync(),
  );
  final capturedEvents = capture['scoreboard']['events'] as List;
  expect(
    capturedEvents[0]['id'] == '401915403' &&
        capturedEvents[0]['status']['type']['state'] == 'post' &&
        capturedEvents[2]['status']['type']['state'] == 'pre',
    'Recorded real response identity/status changed',
  );
  expect(
    capture['runtime_platform'] == 'macos' &&
        capture['source_score_observed_at'] == null &&
        capture['response_received_at'] == '2026-10-20T18:38:20.108066Z',
    'Recorded host evidence was relabelled or freshness was renewed',
  );
  expect(
    scoreboardUri('eng.1', '20261020').query == 'dates=20261020&limit=200',
    'Request scope changed',
  );
  for (final date in ['20260230', '20261301', '2026-10-20']) {
    var rejected = false;
    try {
      scoreboardUri('eng.1', date);
    } on ArgumentError {
      rejected = true;
    }
    expect(rejected, 'Invalid date accepted: $date');
  }
  var rejected = false;
  try {
    projectScoreboard({'events': 'not an array'});
  } on FormatException {
    rejected = true;
  }
  expect(rejected, 'Malformed scoreboard accepted');

  final pending = <Completer<Map<String, Object?>>>[];
  var cancellations = 0;
  final session = ProbeSession(() {
    final completer = Completer<Map<String, Object?>>();
    pending.add(completer);
    return CaptureOperation(completer.future, () => cancellations++);
  });
  final old = session.check();
  expect(await session.check() == null, 'Duplicate check started');
  expect(pending.length == 1, 'Duplicate transport started');
  session.pause();
  expect(cancellations == 1, 'Pause did not cancel');
  expect(await session.check() == null, 'Paused session started a request');
  session.resume();
  final fresh = session.check();
  pending[1].complete({'score': '2'});
  expect((await fresh)?['score'] == '2', 'Resume did not accept fresh result');
  pending[0].complete({'score': '1'});
  expect(await old == null, 'Late pre-pause result was accepted');
  final failing = session.check();
  pending[2].completeError(TimeoutException('Synthetic timeout'));
  var timedOut = false;
  try {
    await failing;
  } on TimeoutException {
    timedOut = true;
  }
  expect(timedOut && !session.busy, 'Timeout did not release session');
  final disposed = session.check();
  session.dispose();
  pending[3].completeError(StateError('Connection cancelled'));
  expect(await disposed == null, 'Disposed session accepted an error');
  session.resume();
  expect(await session.check() == null, 'Disposed session restarted');
  stdout.writeln('PASS: wire projection, request scope, lifecycle ordering');
}
