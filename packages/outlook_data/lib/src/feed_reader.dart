import 'package:crypto/crypto.dart';

import 'schema_registry.dart';
import 'strict_json.dart';

/// Transport adapters must enforce [maxBytes] while reading, before buffering.
/// Paths are validated relative content-addressed object names, never URLs.
typedef ArtifactLoader = Future<List<int>> Function(String path, int maxBytes);
typedef JsonObject = Map<String, Object?>;

/// A complete, immutable required snapshot, plus separately validated editorial.
/// This is not a cache installer: ordering and transactions belong to P3.
class FeedSnapshot {
  FeedSnapshot._(
    this.manifest,
    this.catalog,
    this.schedule,
    this.policy,
    this.editorial,
    this.editorialState,
    this.editorialError,
  );
  final JsonObject manifest;
  final JsonObject catalog;
  final JsonObject schedule;
  final JsonObject policy;
  final JsonObject? editorial;

  /// absent = explicit withdrawal; invalid = descriptor failed; accepted = valid.
  final String editorialState;
  final String? editorialError;

  JsonObject summary() => {
    'version': 1,
    'publication_revision': manifest['publication_revision'],
    'policy_id': policy['policy_id'],
    'source_scopes': schedule['source_scopes'],
    'confirmed_horizon': schedule['confirmed_horizon'],
    'fixture_ids': [for (final f in _objects(schedule['fixtures'])) f['id']],
    'fixtures': [
      for (final f in _objects(schedule['fixtures']))
        {
          'id': f['id'],
          'home_team_id': f['home_team_id'],
          'away_team_id': f['away_team_id'],
          'timing': f['timing'],
          'status': f['status'],
          'outlook': f['outlook'],
          'routes': [
            for (final r in _objects(f['routes']))
              {
                'id': r['id'],
                'network_id': r['network_id'],
                'basis': r['basis'],
                'eligible_service_plan_ids': r['eligible_service_plan_ids'],
                'excluded_service_plan_ids': r['excluded_service_plan_ids'],
              },
          ],
        },
    ],
    'editorial': {'state': editorialState, 'error': editorialError},
  };
}

class FeedReader {
  FeedReader(this.schemas);
  final SchemaRegistry schemas;
  static const supportedPolicies = {
    'outlook-policy-p0-draft-1',
    'outlook-policy-p1-preview-1',
  };
  static const supportedRules = {
    'eligibility': 'watch-routes-v1-draft',
    'sports_day': 'local-sports-day-v1-draft',
    'ranking': 'personal-ranking-v1-draft',
    'editorial': 'editorial-lifetime-v1-draft',
    'rating_dots': 'six-dot-rounding-v1-draft',
    'freshness': 'source-freshness-v1-draft',
  };

  Future<FeedSnapshot> read(
    List<int> manifestBytes,
    ArtifactLoader load,
  ) async {
    final manifest = _object(
      decodeBoundedJson(manifestBytes, maxBytes: manifestByteLimit),
    );
    schemas.validate('manifest', manifest);
    final descriptors = _object(manifest['artifacts']);
    final required = <String, JsonObject>{};
    for (final kind in ['catalog', 'schedule', 'policy']) {
      required[kind] = await _artifact(kind, _object(descriptors[kind]), load);
    }
    final catalog = required['catalog']!;
    final schedule = required['schedule']!;
    final policy = required['policy']!;
    _requiredReferences(catalog, schedule, policy);
    JsonObject? editorial;
    var editorialState = 'absent';
    String? editorialError;
    if (descriptors['editorial'] != null) {
      try {
        editorial = await _artifact(
          'editorial',
          _object(descriptors['editorial']),
          load,
        );
        _editorialReferences(editorial, catalog, schedule, policy);
        editorialState = 'accepted';
      } on FeedValidationException catch (error) {
        editorial = null;
        editorialState = 'invalid';
        editorialError = error.code;
      }
    }
    return FeedSnapshot._(
      _freezeMap(manifest),
      _freezeMap(catalog),
      _freezeMap(schedule),
      _freezeMap(policy),
      editorial == null ? null : _freezeMap(editorial),
      editorialState,
      editorialError,
    );
  }

  Future<JsonObject> _artifact(
    String kind,
    JsonObject descriptor,
    ArtifactLoader load,
  ) async {
    final path = descriptor['path'] as String;
    final expectedDigest = descriptor['sha256'] as String;
    if (path != 'objects/$expectedDigest.json') _fail('descriptor_path_digest');
    // The manifest schema verifies the hard limit before any loader call.
    final size = (descriptor['bytes'] as num).toInt();
    List<int> bytes;
    try {
      bytes = await load(path, size);
    } on FeedValidationException {
      rethrow;
    } on Exception {
      _fail('artifact_unavailable');
    }
    if (bytes.length != size ||
        sha256.convert(bytes).toString() != expectedDigest) {
      _fail('artifact_integrity');
    }
    final data = _object(decodeBoundedJson(bytes, maxBytes: artifactByteLimit));
    schemas.validate(kind, data);
    if (data['content_revision'] != descriptor['content_revision'] ||
        data['schema_version'] != descriptor['schema_version'])
      _fail('artifact_revision');
    return data;
  }

  void _requiredReferences(
    JsonObject catalog,
    JsonObject schedule,
    JsonObject policy,
  ) {
    final leagues = _index(catalog['leagues']);
    final teams = _index(catalog['teams']);
    final networks = _index(catalog['networks']);
    final plans = _index(catalog['service_plans']);
    final assets = _index(catalog['assets']);
    final fixtures = _index(schedule['fixtures']);
    final evidence = _index(schedule['evidence']);
    final destinations = _index(schedule['destinations']);
    final vitals = _index(schedule['team_vitals']);
    if (schedule['catalog_revision'] != catalog['content_revision'] ||
        schedule['policy_id'] != policy['policy_id'])
      _fail('snapshot_binding');
    if (!supportedPolicies.contains(policy['policy_id']))
      _fail('unsupported_policy');
    final rules = _object(policy['rules']);
    for (final rule in supportedRules.entries) {
      if (rules[rule.key] != rule.value) _fail('unsupported_policy_rule');
    }
    final defaults = _object(policy['defaults']);
    for (final key in ['league_order', 'enabled_league_ids']) {
      _references(defaults[key], leagues);
    }
    for (final key in ['service_plan_order', 'enabled_service_plan_ids']) {
      _references(defaults[key], plans);
    }
    final migrationSources = <String>{};
    for (final migration in _objects(catalog['service_plan_migrations'])) {
      _reference(migration['to'], plans);
      if (!migrationSources.add(migration['from'] as String) ||
          migration['from'] == migration['to'])
        _fail('invalid_migration');
    }
    for (final collection in [leagues, teams, networks]) {
      for (final item in collection.values) {
        if (item['asset_id'] != null) _reference(item['asset_id'], assets);
      }
    }
    final scopedLeagues = <Object?>{};
    for (final scope in _objects(schedule['source_scopes'])) {
      _reference(scope['league_id'], leagues);
      scopedLeagues.add(scope['league_id']);
      final requested = _list(scope['requested_dates']).toSet();
      final covered = _list(scope['covered_dates']).toSet();
      if (scope['complete'] != true ||
          requested.length != covered.length ||
          !covered.containsAll(requested))
        _fail('incomplete_source_scope');
    }
    for (final item in evidence.values) {
      _reference(item['fixture_id'], fixtures);
      _references(item['team_ids'], teams);
    }
    for (final item in vitals.values) {
      _reference(item['team_id'], teams);
      _reference(item['league_id'], leagues);
      if (item['rank'] case final JsonObject rank) {
        if ((rank['position'] as num) > (rank['table_size'] as num))
          _fail('invalid_rank');
      }
    }
    for (final destination in destinations.values) {
      _references(destination['network_ids'], networks);
      _references(destination['service_plan_ids'], plans);
      if (destination['fixture_id'] != null)
        _reference(destination['fixture_id'], fixtures);
      if (destination['kind'] == 'match' && destination['fixture_id'] == null) {
        _fail('destination_binding');
      }
    }
    final routeIds = <String>{};
    for (final fixture in fixtures.values) {
      _reference(fixture['league_id'], leagues);
      if (!scopedLeagues.contains(fixture['league_id']))
        _fail('fixture_outside_scope');
      _reference(fixture['home_team_id'], teams);
      _reference(fixture['away_team_id'], teams);
      if (fixture['home_team_id'] == fixture['away_team_id'])
        _fail('same_team');
      _references(fixture['network_ids'], networks);
      final context = _object(fixture['context']);
      for (final side in ['home', 'away']) {
        final id = context['${side}_vitals_id'];
        if (id != null) {
          _reference(id, vitals);
          final item = vitals[id]!;
          if (item['team_id'] != fixture['${side}_team_id'] ||
              item['league_id'] != fixture['league_id'])
            _fail('vitals_binding');
        }
      }
      final links = _object(fixture['links']);
      if (links['table_league_id'] != null &&
          links['table_league_id'] != fixture['league_id']) {
        _fail('table_binding');
      }
      _references(links['destination_ids'], destinations);
      for (final id in _list(links['destination_ids'])) {
        _destinationBinding(destinations[id]!, fixture);
      }
      for (final route in _objects(fixture['routes'])) {
        if (!routeIds.add(route['id'] as String)) _fail('duplicate_id');
        if (!_list(fixture['network_ids']).contains(route['network_id']))
          _fail('route_network_binding');
        _references(route['eligible_service_plan_ids'], plans);
        _references(route['excluded_service_plan_ids'], plans);
        final eligible = _list(route['eligible_service_plan_ids']).toSet();
        if (_list(route['excluded_service_plan_ids']).any(eligible.contains))
          _fail('excluded_plan');
        for (final id in eligible) {
          if (plans[id]!['retired'] == true) _fail('retired_plan_eligible');
        }
        _references(route['evidence_ids'], evidence);
        _references(route['destination_ids'], destinations);
        for (final id in _list(route['destination_ids'])) {
          final destination = destinations[id]!;
          _destinationBinding(destination, fixture);
          if (destination['country'] != route['country'] ||
              !_list(destination['network_ids'])
                  .contains(route['network_id'])) {
            _fail('destination_route_binding');
          }
        }
        for (final id in _list(route['evidence_ids'])) {
          final observed = evidence[id]!;
          final observedTeams = _list(observed['team_ids']).toSet();
          if (observed['fixture_id'] != fixture['id'] ||
              !observedTeams.containsAll([
                fixture['home_team_id'],
                fixture['away_team_id'],
              ]) ||
              observed['kickoff'] != _object(fixture['timing'])['kickoff'] ||
              observed['country'] != route['country'] ||
              observed['verdict'] != 'accepted' ||
              observed['classification'] != 'live_match') {
            _fail('route_evidence_binding');
          }
        }
        if (route['retained'] == true) {
          final verified = DateTime.parse(route['verified_at'] as String);
          final expires = DateTime.parse(route['expires_at'] as String);
          final maxSeconds =
              _object(policy['freshness'])['retained_evidence_max_seconds']
                  as num;
          if (!verified.isBefore(expires) ||
              expires.difference(verified).inMicroseconds >
                  maxSeconds * 1000000) {
            _fail('retained_evidence_lifetime');
          }
        }
      }
    }
  }

  void _destinationBinding(JsonObject destination, JsonObject fixture) {
    if (destination['fixture_id'] != null &&
        destination['fixture_id'] != fixture['id']) {
      _fail('destination_fixture_binding');
    }
  }

  void _editorialReferences(
    JsonObject editorial,
    JsonObject catalog,
    JsonObject schedule,
    JsonObject policy,
  ) {
    if (editorial['catalog_revision'] != catalog['content_revision'] ||
        editorial['policy_id'] != policy['policy_id'])
      _fail('editorial_binding');
    final fixtures = _index(schedule['fixtures']);
    final leagues = _index(catalog['leagues']);
    for (final origin in ['automatic', 'shared']) {
      if (editorial[origin] == null) continue;
      final slot = _object(editorial[origin]);
      final started = DateTime.parse(
        slot[origin == 'automatic' ? 'generated_at' : 'prompted_at'] as String,
      );
      final expires = DateTime.parse(slot['expires_at'] as String);
      final publishedValue = slot['published_at'];
      final published = publishedValue == null
          ? null
          : DateTime.parse(publishedValue as String);
      final maxSeconds =
          _object(policy['freshness'])[origin == 'automatic'
                  ? 'automatic_max_seconds'
                  : 'personal_shared_max_seconds']
              as num;
      if (!started.isBefore(expires) ||
          (published != null && published.isBefore(started)) ||
          expires.difference(started).inMicroseconds > maxSeconds * 1000000)
        _fail('editorial_lifetime');
      if (slot['focus_until'] case final String focus) {
        if (expires.isAfter(DateTime.parse(focus)))
          _fail('editorial_focus_lifetime');
      }
      final items = [
        ..._objects(slot['headlines']),
        ..._objects(slot['matches']),
      ];
      _index(items);
      for (final item in items) {
        _reference(item['league_id'], leagues);
        final itemExpiry = DateTime.parse(item['expires_at'] as String);
        if (itemExpiry.isAfter(expires) || !started.isBefore(itemExpiry))
          _fail('editorial_item_lifetime');
        final references = <Object?>{};
        for (final reference in _objects(item['fixture_refs'])) {
          final id = reference['fixture_id'];
          _reference(id, fixtures);
          if (!references.add(id)) _fail('duplicate_editorial_reference');
          final fixture = fixtures[id]!;
          if (reference['kickoff'] != _object(fixture['timing'])['kickoff'] ||
              item['league_id'] != fixture['league_id'])
            _fail('editorial_fixture_binding');
        }
        if (origin == 'shared' &&
            (item['automatic_components'] != null ||
                item['automatic_weights'] != null)) {
          _fail('shared_components');
        }
      }
    }
  }
}

Never _fail(String code) => throw FeedValidationException(code);
JsonObject _object(Object? value) {
  if (value is! JsonObject) _fail('expected_object');
  return value;
}

List<Object?> _list(Object? value) => value as List<Object?>;
Iterable<JsonObject> _objects(Object? value) => _list(value).map(_object);
Map<String, JsonObject> _index(Object? values) {
  final result = <String, JsonObject>{};
  for (final item in _objects(values)) {
    final id = item['id'] as String;
    if (result.containsKey(id)) _fail('duplicate_id');
    result[id] = item;
  }
  return result;
}

void _reference(Object? id, Map<String, JsonObject> known) {
  if (!known.containsKey(id)) _fail('broken_reference');
}

void _references(Object? ids, Map<String, JsonObject> known) {
  for (final id in _list(ids)) {
    _reference(id, known);
  }
}

JsonObject _freezeMap(JsonObject value) => _freeze(value) as JsonObject;
Object? _freeze(Object? value) {
  if (value is JsonObject)
    return Map<String, Object?>.unmodifiable(
      value.map((key, item) => MapEntry(key, _freeze(item))),
    );
  if (value is List<Object?>)
    return List<Object?>.unmodifiable(value.map(_freeze));
  return value;
}
