// P0 transport evidence only. This is not the shared ESPN domain decoder.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

const responseLimit = 2 * 1024 * 1024;
const requestTimeout = Duration(seconds: 8);

Uri scoreboardUri(String league, String date) {
  if (!RegExp(r'^[a-z0-9]+(?:\.[a-z0-9]+)+$').hasMatch(league) ||
      !RegExp(r'^\d{8}$').hasMatch(date)) {
    throw ArgumentError('Expected a league such as eng.1 and YYYYMMDD');
  }
  final parsed = DateTime.tryParse(
    '${date.substring(0, 4)}-${date.substring(4, 6)}-${date.substring(6, 8)}',
  );
  if (parsed == null ||
      '${parsed.year.toString().padLeft(4, '0')}'
              '${parsed.month.toString().padLeft(2, '0')}'
              '${parsed.day.toString().padLeft(2, '0')}' !=
          date) {
    throw ArgumentError('Invalid calendar date');
  }
  return Uri.https(
    'site.api.espn.com',
    '/apis/site/v2/sports/soccer/$league/scoreboard',
    {'dates': date, 'limit': '200'},
  );
}

Map<String, Object?> _fields(Map value, List<String> names) => {
  for (final name in names)
    if (value[name] is String ||
        value[name] is num ||
        value[name] is bool ||
        value[name] == null && value.containsKey(name))
      name: value[name],
};

Map<String, Object?> _status(Object? value) {
  if (value is! Map) return {};
  return {
    ..._fields(value, ['clock', 'displayClock', 'period']),
    'type': value['type'] is Map
        ? _fields(value['type'], [
            'id',
            'name',
            'state',
            'completed',
            'description',
            'detail',
            'shortDetail',
          ])
        : <String, Object?>{},
  };
}

/// Allowlisted wire fields, without inferring live/final/void or score freshness.
/// The first three events are sufficient to inspect a transport response.
Map<String, Object?> projectScoreboard(Object? decoded) {
  if (decoded is! Map || decoded['events'] is! List) {
    throw const FormatException('Expected a scoreboard events array');
  }
  final events = decoded['events'] as List;
  return {
    'event_count': events.length,
    'events': events.take(3).map((event) {
      if (event is! Map) throw const FormatException('Invalid event');
      return {
        ..._fields(event, ['id', 'uid', 'date']),
        'status': _status(event['status']),
        'competitions': [
          for (final competition
              in event['competitions'] is List
                  ? event['competitions'] as List
                  : const [])
            if (competition is Map)
              {
                ..._fields(competition, ['id', 'uid', 'date']),
                'status': _status(competition['status']),
                'competitors': [
                  for (final competitor
                      in competition['competitors'] is List
                          ? competition['competitors'] as List
                          : const [])
                    if (competitor is Map)
                      {
                        ..._fields(competitor, [
                          'id',
                          'homeAway',
                          'score',
                          'winner',
                        ]),
                        if (competitor['score'] is Map)
                          'score': _fields(competitor['score'], [
                            'value',
                            'displayValue',
                          ]),
                        'team': competitor['team'] is Map
                            ? _fields(competitor['team'], [
                                'id',
                                'displayName',
                                'abbreviation',
                              ])
                            : <String, Object?>{},
                      },
                ],
              },
        ],
      };
    }).toList(),
  };
}

class CaptureOperation {
  CaptureOperation(this.result, this.cancel);
  final Future<Map<String, Object?>> result;
  final void Function() cancel;
}

/// One credential-free GET, no retries/redirects, bounded body and total timeout.
CaptureOperation beginCapture(Uri uri) {
  if (uri.scheme != 'https' || uri.host != 'site.api.espn.com') {
    throw ArgumentError('This probe only requests the public ESPN HTTPS host');
  }
  final client = HttpClient()..connectionTimeout = requestTimeout;
  final startedAt = DateTime.now().toUtc();
  final watch = Stopwatch()..start();
  var cancelled = false;
  void cancel() {
    cancelled = true;
    client.close(force: true);
  }

  Future<Map<String, Object?>> fetch() async {
    try {
      final request = await client.getUrl(uri);
      request.followRedirects = false;
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      request.headers.set(HttpHeaders.cacheControlHeader, 'no-cache');
      final response = await request.close();
      if (response.statusCode != HttpStatus.ok) {
        throw HttpException('HTTP ${response.statusCode}', uri: uri);
      }
      final bytes = <int>[];
      await for (final chunk in response) {
        if (bytes.length + chunk.length > responseLimit) {
          throw const FormatException('Scoreboard exceeds 2 MiB');
        }
        bytes.addAll(chunk);
      }
      if (cancelled) throw StateError('Cancelled');
      final receivedAt = DateTime.now().toUtc();
      return {
        'probe_version': 1,
        'runtime_platform': Platform.operatingSystem,
        'runtime_version': Platform.operatingSystemVersion,
        'source_url': uri.toString(),
        'http_status': response.statusCode,
        'request_started_at': startedAt.toIso8601String(),
        'response_received_at': receivedAt.toIso8601String(),
        'elapsed_ms': watch.elapsedMilliseconds,
        'response_bytes': bytes.length,
        'headers': {
          for (final header in [
            'date',
            'age',
            'cache-control',
            'last-modified',
            'content-type',
            'access-control-allow-origin',
          ])
            if (response.headers.value(header) != null)
              header: response.headers.value(header),
        },
        // Receipt time and HTTP Date are not the score's source observation time.
        'source_score_observed_at': null,
        'source_score_observed_at_reason':
            'No score-level observation timestamp is established by this probe',
        'scoreboard': projectScoreboard(jsonDecode(utf8.decode(bytes))),
      };
    } finally {
      client.close(force: true);
    }
  }

  final result = fetch().timeout(
    requestTimeout,
    onTimeout: () {
      cancel();
      throw TimeoutException(
        'Total request exceeded 8 seconds',
        requestTimeout,
      );
    },
  );
  return CaptureOperation(result, cancel);
}

/// Cancels on pause/dispose and rejects late completions from obsolete batches.
/// Resume permits one fresh check; there is deliberately no background loop.
class ProbeSession {
  ProbeSession(this.start);
  final CaptureOperation Function() start;
  CaptureOperation? _active;
  int _generation = 0;
  bool _paused = false;
  bool _disposed = false;
  bool get busy => _active != null;

  Future<Map<String, Object?>?> check() async {
    if (_paused || _disposed || busy) return null;
    final generation = _generation;
    final operation = start();
    _active = operation;
    try {
      final result = await operation.result;
      return generation == _generation && !_paused && !_disposed
          ? result
          : null;
    } catch (_) {
      if (generation != _generation || _paused || _disposed) return null;
      rethrow;
    } finally {
      if (identical(_active, operation)) _active = null;
    }
  }

  void pause() {
    _paused = true;
    _generation++;
    _active?.cancel();
    _active = null;
  }

  void resume() {
    if (!_disposed) _paused = false;
  }

  void dispose() {
    pause();
    _disposed = true;
  }
}
