import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:outlook_data/outlook_data.dart';
import 'package:test/test.dart';

final contract = Directory('../../contracts/outlook/v1');
late SchemaRegistry registry;
JsonObject object(Object? value) => value as JsonObject;
JsonObject clone(JsonObject value) => object(jsonDecode(jsonEncode(value)));
List<Object?> list(Object? value) => value as List<Object?>;
JsonObject first(Object? value) => object(list(value).first);
Matcher get invalid => throwsA(isA<FeedValidationException>());

class Publication {
  Publication() {
    manifest = object(
      jsonDecode(
        File('${contract.path}/examples/manifest.json').readAsStringSync(),
      ),
    );
    for (final entry in object(manifest['artifacts']).entries) {
      if (entry.value == null) continue;
      final descriptor = object(entry.value);
      final bytes = File('${contract.path}/examples/${descriptor['path']}')
          .readAsBytesSync();
      artifacts[entry.key] = object(jsonDecode(utf8.decode(bytes)));
      raw[descriptor['path'] as String] = bytes;
    }
  }
  late JsonObject manifest;
  final artifacts = <String, JsonObject>{};
  final raw = <String, List<int>>{};
  final loads = <String>[];
  JsonObject get schedule => artifacts['schedule']!;
  JsonObject get fixture => first(schedule['fixtures']);
  JsonObject get route => first(fixture['routes']);
  JsonObject descriptor(String kind) =>
      object(object(manifest['artifacts'])[kind]);
  void repack(String kind, {String? text}) {
    final bytes = utf8.encode(text ?? jsonEncode(artifacts[kind]));
    final digest = sha256.convert(bytes).toString();
    final path = 'objects/$digest.json';
    object(manifest['artifacts'])[kind] = {
      'path': path,
      'sha256': digest,
      'bytes': bytes.length,
      'schema_version': 1,
      'content_revision': artifacts[kind]!['content_revision'],
    };
    raw[path] = bytes;
  }

  Future<FeedSnapshot> read() =>
      FeedReader(registry)
          .read(utf8.encode(jsonEncode(manifest)), (path, maxBytes) async {
            loads.add(path);
            if (!raw.containsKey(path))
              throw const FeedValidationException('artifact_unavailable');
            return raw[path]!;
          });
}

void main() {
  setUpAll(() {
    registry = SchemaRegistry({
      for (final file in contract.listSync().whereType<File>().where(
        (f) => f.path.endsWith('.schema.json'),
      ))
        file.uri.pathSegments.last: jsonDecode(file.readAsStringSync()),
    });
  });
  test(
    'canonical publication has immutable required and optional snapshots',
    () async {
      final snapshot = await Publication().read();
      expect(snapshot.summary()['fixture_ids'], ['espn:fixture:example-1']);
      expect(
        first(
          first(snapshot.schedule['fixtures'])['routes'],
        )['eligible_service_plan_ids'],
        ['yttv'],
      );
      expect(snapshot.editorialState, 'accepted');
      expect(() => snapshot.schedule['fixtures'] = [], throwsUnsupportedError);
      expect(
        () => list(snapshot.schedule['fixtures']).clear(),
        throwsUnsupportedError,
      );
      expect(
        () => first(snapshot.schedule['fixtures'])['id'] = 'changed',
        throwsUnsupportedError,
      );
    },
  );
  test(
    'independent synthetic schedules preserve intended distinctions',
    () async {
      for (final name in [
        'normal',
        'complete-empty',
        'unknown-kickoff',
        'missing-scores',
        'usual-coverage',
        'excluded-route',
        'partial-broadcast',
        'replay',
        'unknown-field',
      ]) {
        final p = Publication();
        p.artifacts['schedule'] = object(
          jsonDecode(
            File('../../tests/contracts/schedules/$name.json')
                .readAsStringSync(),
          ),
        );
        object(p.manifest['artifacts'])['editorial'] = null;
        p.repack('schedule');
        final snapshot = await p.read();
        if (name == 'complete-empty')
          expect(snapshot.summary()['fixture_ids'], isEmpty);
        if (name == 'unknown-kickoff')
          expect(
            object(first(snapshot.schedule['fixtures'])['timing'])['kickoff'],
            isNull,
          );
        if (name == 'missing-scores')
          expect(
            object(
              object(
                object(
                  first(snapshot.schedule['fixtures'])['status'],
                )['scores'],
              )['match'],
            ),
            {'home': 0, 'away': null},
          );
        if (name == 'excluded-route')
          expect(
            first(
              first(snapshot.schedule['fixtures'])['routes'],
            )['eligible_service_plan_ids'],
            ['yttvspanishplus'],
          );
        if (name == 'replay')
          expect(first(snapshot.schedule['fixtures'])['routes'], isEmpty);
        if (name == 'partial-broadcast')
          expect(
            first(snapshot.schedule['broadcast_sources'])['state'],
            'partial',
          );
      }
    },
  );
  test(
    'additive unknown fields accepted but reserved personal output fails',
    () async {
      final p = Publication();
      p.manifest['future'] = {'version': 2};
      p.fixture['future'] = ['optional'];
      p.repack('schedule');
      expect((await p.read()).summary()['fixture_ids'], [
        'espn:fixture:example-1',
      ]);
      p.fixture['rating_dots'] = 6;
      p.repack('schedule');
      await expectLater(p.read(), invalid);
    },
  );
  test(
    'optional absence differs from invalid and cannot invalidate schedule',
    () async {
      final p = Publication();
      p.raw.remove(p.descriptor('editorial')['path']);
      final unavailable = await p.read();
      expect(unavailable.editorialState, 'invalid');
      expect(unavailable.editorial, isNull);
      expect(unavailable.summary()['fixture_ids'], ['espn:fixture:example-1']);
      object(p.manifest['artifacts'])['editorial'] = null;
      expect((await p.read()).editorialState, 'absent');
      object(p.manifest['artifacts']).remove('editorial');
      await expectLater(p.read(), invalid);
    },
  );
  test(
    'corruption of any required artifact prevents a partial snapshot',
    () async {
      for (final kind in ['catalog', 'schedule', 'policy']) {
        final p = Publication();
        final path = p.descriptor(kind)['path'] as String;
        p.raw[path] = [...p.raw[path]!, 32];
        await expectLater(p.read(), invalid);
      }
    },
  );
  test(
    'unsafe paths and excessive descriptors fail before artifact loading',
    () async {
      for (final path in [
        '../outside.json',
        '/objects/file.json',
        'https://other.example/test.json',
        'objects/${'0' * 64}.json',
      ]) {
        final p = Publication();
        p.descriptor('catalog')['path'] = path;
        await expectLater(p.read(), invalid);
        expect(p.loads, isEmpty);
      }
      final p = Publication();
      p.descriptor('catalog')['bytes'] = artifactByteLimit + 1;
      await expectLater(p.read(), invalid);
      expect(p.loads, isEmpty);
    },
  );
  test(
    'schema and content revision disagreements reject required data',
    () async {
      for (final field in ['schema_version', 'content_revision']) {
        final p = Publication();
        p.descriptor('catalog')[field] = field == 'schema_version'
            ? 2
            : 'different';
        await expectLater(p.read(), invalid);
      }
      final p = Publication();
      p.schedule['catalog_revision'] = 'unrelated';
      p.repack('schedule');
      await expectLater(p.read(), invalid);
    },
  );
  test('unsupported policies and rule IDs fail explicitly', () async {
    for (final field in ['policy_id', 'rule']) {
      final p = Publication();
      if (field == 'rule') {
        object(p.artifacts['policy']!['rules'])['ranking'] = 'future-rule';
      } else {
        p.artifacts['policy']!['policy_id'] = 'future-policy';
        p.schedule['policy_id'] = 'future-policy';
        p.repack('schedule');
      }
      p.repack('policy');
      await expectLater(p.read(), invalid);
    }
  });
  test(
    'preview policy is accepted under the declared draft rule profile',
    () async {
      final p = Publication();
      p.artifacts['policy']!['policy_id'] = 'outlook-policy-p1-preview-1';
      p.schedule['policy_id'] = 'outlook-policy-p1-preview-1';
      p.repack('policy');
      p.repack('schedule');
      final snapshot = await p.read();
      expect(snapshot.policy['policy_id'], 'outlook-policy-p1-preview-1');
      expect(snapshot.editorialState, 'invalid');
    },
  );
  test(
    'duplicate IDs and missing references fail required transaction',
    () async {
      for (final mutate in <void Function(Publication)>[
        (p) => list(p.schedule['fixtures']).add(clone(p.fixture)),
        (p) => p.fixture['home_team_id'] = 'missing:team',
        (p) => p.fixture['away_team_id'] = p.fixture['home_team_id'],
        (p) => p.route['eligible_service_plan_ids'] = ['missing:plan'],
        (p) => p.route['network_id'] = 'missing:network',
        (p) => object(p.fixture['links'])['table_league_id'] = 'missing:league',
        (p) =>
            object(p.fixture['context'])['home_vitals_id'] = 'missing:vitals',
        (p) => p.route['evidence_ids'] = ['missing:evidence'],
        (p) => p.route['excluded_service_plan_ids'] = ['yttv'],
      ]) {
        final p = Publication();
        mutate(p);
        p.repack('schedule');
        await expectLater(p.read(), invalid);
      }
    },
  );
  test(
    'required scopes cover requested dates and use valid IANA zones',
    () async {
      for (final mutate in <void Function(JsonObject)>[
        (s) => s['complete'] = false,
        (s) => s['covered_dates'] = [],
        (s) => s['covered_dates'] = ['2026-10-22'],
        (s) => s['date_zone'] = 'America/Invented',
        (s) => s['requested_dates'] = ['2026-02-30'],
      ]) {
        final p = Publication();
        mutate(first(p.schedule['source_scopes']));
        p.repack('schedule');
        await expectLater(p.read(), invalid);
      }
    },
  );
  test(
    'replay, rejection or mismatched evidence cannot justify a route',
    () async {
      for (final mutate in <void Function(JsonObject)>[
        (e) => e['classification'] = 'replay',
        (e) => e['verdict'] = 'rejected',
        (e) => e['country'] = 'GB',
        (e) => e['kickoff'] = '2026-10-20T19:00:00Z',
      ]) {
        final p = Publication();
        mutate(first(p.schedule['evidence']));
        p.repack('schedule');
        await expectLater(p.read(), invalid);
      }
    },
  );
  test(
    'unknown enum, invalid date, unsafe URLs and inverted horizon fail',
    () async {
      for (final mutate in <void Function(Publication)>[
        (p) => object(p.fixture['status'])['state'] = 'future-state',
        (p) => object(p.fixture['timing'])['kickoff'] = '2026-02-30T12:00:00Z',
        (p) => object(p.fixture['timing'])['kickoff'] =
            '2026-10-20T18:00:00-04:00',
        (p) => object(p.fixture['links'])['match_page'] =
            'https://name:password@example.org/',
        (p) => object(p.fixture['links'])['match_page'] =
            'https://example.org/?access_token=hidden',
        (p) => object(p.fixture['links'])['match_page'] =
            'https://example.org:99999/',
        (p) => object(p.schedule['confirmed_horizon'])['end_exclusive'] =
            '2026-01-01T00:00:00Z',
      ]) {
        final p = Publication();
        mutate(p);
        p.repack('schedule');
        await expectLater(p.read(), invalid);
      }
    },
  );
  test(
    'editorial fixture references do not move with rescheduled kickoff',
    () async {
      final p = Publication();
      object(p.fixture['timing'])['kickoff'] = '2026-10-21T18:00:00Z';
      first(p.schedule['evidence'])['kickoff'] = '2026-10-21T18:00:00Z';
      p.repack('schedule');
      final snapshot = await p.read();
      expect(snapshot.editorialState, 'invalid');
      expect(snapshot.editorialError, 'editorial_fixture_binding');
      expect(snapshot.summary()['fixture_ids'], ['espn:fixture:example-1']);
    },
  );
  test('publication time cannot renew original editorial lifetime', () async {
    final p = Publication();
    object(p.artifacts['editorial']!['shared'])['expires_at'] =
        '2026-10-22T10:00:00Z';
    p.repack('editorial');
    final snapshot = await p.read();
    expect(snapshot.editorialState, 'invalid');
    expect(snapshot.editorialError, 'editorial_lifetime');
  });
  test('unknown original editorial publication time remains null', () async {
    final p = Publication();
    object(p.artifacts['editorial']!['shared'])['published_at'] = null;
    p.repack('editorial');
    final snapshot = await p.read();
    expect(snapshot.editorialState, 'accepted');
    expect(object(snapshot.editorial!['shared'])['published_at'], isNull);
  });
  test(
    'fixture count bound applies before cross-reference installation',
    () async {
      final p = Publication();
      p.schedule['fixtures'] = List.generate(3001, (_) => clone(p.fixture));
      p.repack('schedule');
      await expectLater(p.read(), invalid);
    },
  );
  test('strict decoder enforces total node budget across small containers', () {
    final row = '[${List.filled(30000, '0').join(',')}]';
    final text = '[${List.filled(34, row).join(',')}]';
    expect(
      () => decodeBoundedJson(utf8.encode(text), maxBytes: artifactByteLimit),
      throwsA(
        isA<FeedValidationException>().having(
          (e) => e.code,
          'code',
          'node_limit',
        ),
      ),
    );
  });
  test('malformed URL query encoding remains an optional failure', () async {
    final p = Publication();
    first(object(p.artifacts['editorial']!['shared'])['matches'])['sources'] = [
      {'title': 'example', 'url': 'https://example.org/?%FF=value'},
    ];
    p.repack('editorial');
    final snapshot = await p.read();
    expect(snapshot.editorialState, 'invalid');
    expect(snapshot.summary()['fixture_ids'], ['espn:fixture:example-1']);
  });
  test('unsupported schema vocabulary fails closed', () {
    expect(
      () => SchemaRegistry({
        'manifest.schema.json': {'type': 'object', 'not': <String, Object?>{}},
      }),
      invalid,
    );
  });
  test('strict JSON rejects duplicate keys, nonfinite and malformed data', () {
    for (final text in [
      r'{"a":1,"\u0061":2}',
      '{"a":NaN}',
      '{"a":Infinity}',
      '{"a":1e9999}',
      '{"a":9007199254740992}',
      '[01]',
      '[1,]',
      '{"x":true,}',
      'null garbage',
      r'"\ud800"',
      '"raw\nline"',
    ]) {
      expect(
        () => decodeBoundedJson(utf8.encode(text), maxBytes: artifactByteLimit),
        invalid,
        reason: text,
      );
    }
    expect(() => decodeBoundedJson([0xff], maxBytes: 1), invalid);
  });
  test('strict JSON bounds bytes, nesting, properties, arrays and strings', () {
    for (final text in [
      '${'[' * 66}0${']' * 66}',
      '{${List.generate(101, (i) => '"k$i":0').join(',')}}',
      '[${List.filled(30001, '0').join(',')}]',
      '"${'a' * 65537}"',
    ]) {
      expect(
        () => decodeBoundedJson(utf8.encode(text), maxBytes: artifactByteLimit),
        invalid,
      );
    }
    expect(
      () => decodeBoundedJson(
        List.filled(manifestByteLimit + 1, 32),
        maxBytes: manifestByteLimit,
      ),
      invalid,
    );
  });
  test('duplicate keys reject even when artifact digest matches', () async {
    final p = Publication();
    p.repack(
      'schedule',
      text: jsonEncode(p.schedule).replaceFirst('{', '{"schema_version":1,'),
    );
    await expectLater(p.read(), invalid);
  });
}
