import 'dart:convert';

import 'package:timezone/data/latest.dart' as zone_data;
import 'package:timezone/timezone.dart' as zones;

import 'strict_json.dart';

/// Evaluates the exact vocabulary used by the checked-in v1 schemas. Schemas
/// are trusted local application data, never downloaded from a manifest. New
/// vocabulary fails closed until this evaluator deliberately supports it.
class SchemaRegistry {
  SchemaRegistry(Map<String, Object?> schemas) : _schemas = Map.of(schemas) {
    for (final entry in _schemas.entries) {
      if (!RegExp(r'^[a-z]+\.schema\.json$').hasMatch(entry.key)) {
        throw const FeedValidationException('schema_registry_name');
      }
      _audit(entry.value);
    }
    zone_data.initializeTimeZones();
  }

  final Map<String, Object?> _schemas;
  final Map<String, RegExp> _patterns = {};
  static const _keywords = {
    r'$schema',
    r'$id',
    r'$defs',
    r'$ref',
    'title',
    'description',
    'type',
    'properties',
    'required',
    'additionalProperties',
    'maxProperties',
    'items',
    'minItems',
    'maxItems',
    'uniqueItems',
    'minLength',
    'maxLength',
    'pattern',
    'format',
    'minimum',
    'maximum',
    'const',
    'enum',
    'allOf',
    'anyOf',
    'if',
    'then',
    'else',
  };

  void _audit(Object? node) {
    if (node is bool) return;
    if (node is! Map<String, Object?> ||
        node.keys.any((k) => !_keywords.contains(k))) {
      throw const FeedValidationException('unsupported_schema_vocabulary');
    }
    for (final key in ['properties', r'$defs']) {
      final children = node[key];
      if (children is Map<String, Object?>) {
        for (final child in children.values) {
          _audit(child);
        }
      }
    }
    for (final key in ['items', 'additionalProperties', 'if', 'then', 'else']) {
      if (node.containsKey(key)) _audit(node[key]);
    }
    for (final key in ['allOf', 'anyOf']) {
      if (node[key] case final List<Object?> children) {
        for (final child in children) {
          _audit(child);
        }
      }
    }
  }

  void validate(String kind, Object? value) {
    final name = '$kind.schema.json';
    if (!_schemas.containsKey(name)) {
      throw const FeedValidationException('missing_schema');
    }
    _check(_schemas[name], value, name);
  }

  Never _fail() => throw const FeedValidationException('schema_validation');

  bool _matches(Object? schema, Object? value, String file) {
    try {
      _check(schema, value, file);
      return true;
    } on FeedValidationException {
      return false;
    }
  }

  void _check(Object? raw, Object? value, String file) {
    if (raw == true) return;
    if (raw == false) _fail();
    final schema = raw as Map<String, Object?>;
    if (schema[r'$ref'] case final String reference) {
      final parts = reference.split('#');
      final targetFile = parts.first.isEmpty ? file : parts.first;
      Object? target = _schemas[targetFile];
      if (target == null || parts.length > 2) _fail();
      if (parts.length == 2) {
        if (!parts[1].startsWith('/')) _fail();
        for (final part in parts[1].substring(1).split('/')) {
          if (target is! Map<String, Object?>) _fail();
          final key = part.replaceAll('~1', '/').replaceAll('~0', '~');
          if (!target.containsKey(key)) _fail();
          target = target[key];
        }
      }
      _check(target, value, targetFile);
      if (reference.endsWith(r'#/$defs/zone')) {
        try {
          zones.getLocation(value as String);
        } on zones.LocationNotFoundException {
          _fail();
        }
      }
      if (reference.endsWith(r'#/$defs/utc_interval')) {
        final interval = value as Map<String, Object?>;
        if (!DateTime.parse(interval['start'] as String)
            .isBefore(DateTime.parse(interval['end_exclusive'] as String)))
          _fail();
      }
      if (reference.endsWith(r'#/$defs/date_interval')) {
        final interval = value as Map<String, Object?>;
        final start = interval['start'] as String;
        final end = interval['end_inclusive'] as String;
        if (start.compareTo(end) > 0) _fail();
      }
    }
    if (schema['type'] case final String type) {
      final valid = switch (type) {
        'null' => value == null,
        'boolean' => value is bool,
        'string' => value is String,
        'number' => value is num && value.isFinite,
        'integer' =>
          value is num && value.isFinite && value == value.truncateToDouble(),
        'array' => value is List<Object?>,
        'object' => value is Map<String, Object?>,
        _ => false,
      };
      if (!valid) _fail();
    }
    if (schema.containsKey('const') && !_equal(value, schema['const'])) _fail();
    if (schema['enum'] case final List<Object?> choices) {
      if (!choices.any((choice) => _equal(value, choice))) _fail();
    }
    if (schema['allOf'] case final List<Object?> children) {
      for (final child in children) {
        _check(child, value, file);
      }
    }
    if (schema['anyOf'] case final List<Object?> children) {
      if (!children.any((child) => _matches(child, value, file))) _fail();
    }
    if (schema.containsKey('if')) {
      final branch = _matches(schema['if'], value, file) ? 'then' : 'else';
      if (schema.containsKey(branch)) _check(schema[branch], value, file);
    }
    if (value is Map<String, Object?>) {
      if (schema['maxProperties'] case final num max) {
        if (value.length > max) _fail();
      }
      if (schema['required'] case final List<Object?> required) {
        if (required.any((key) => !value.containsKey(key))) _fail();
      }
      final properties = schema['properties'] as Map<String, Object?>? ?? {};
      for (final entry in value.entries) {
        if (properties.containsKey(entry.key)) {
          _check(properties[entry.key], entry.value, file);
        } else if (schema.containsKey('additionalProperties')) {
          _check(schema['additionalProperties'], entry.value, file);
        }
      }
    }
    if (value is List<Object?>) {
      if (schema['minItems'] case final num min) {
        if (value.length < min) _fail();
      }
      if (schema['maxItems'] case final num max) {
        if (value.length > max) _fail();
      }
      if (schema['uniqueItems'] == true) {
        final seen = <String>{};
        for (final item in value) {
          if (!seen.add(jsonEncode(_canonical(item)))) _fail();
        }
      }
      if (schema.containsKey('items')) {
        for (final item in value) {
          _check(schema['items'], item, file);
        }
      }
    }
    if (value is String) {
      if (schema['minLength'] case final num min) {
        if (value.runes.length < min) _fail();
      }
      if (schema['maxLength'] case final num max) {
        if (value.runes.length > max) _fail();
      }
      if (schema['pattern'] case final String pattern) {
        if (!_patterns
            .putIfAbsent(pattern, () => RegExp(pattern))
            .hasMatch(value))
          _fail();
      }
      if (schema['format'] case final String format) {
        if (!_format(format, value)) _fail();
      }
    }
    if (value is num) {
      if (schema['minimum'] case final num min) {
        if (value < min) _fail();
      }
      if (schema['maximum'] case final num max) {
        if (value > max) _fail();
      }
    }
  }

  bool _format(String format, String value) {
    if (format == 'uri') {
      final uri = Uri.tryParse(value);
      if (uri == null ||
          uri.scheme != 'https' ||
          uri.host.isEmpty ||
          uri.userInfo.isNotEmpty ||
          uri.port < 1 ||
          uri.port > 65535)
        return false;
      // Source URLs may have benign query parameters, but must not publish
      // conventional account credentials. The producer still owns URL review.
      try {
        return !uri.queryParameters.keys.any(
          (key) => const {
            'token',
            'access_token',
            'api_key',
            'apikey',
            'password',
            'auth',
            'authorization',
            'session',
            'sessionid',
            'client_secret',
          }.contains(key.toLowerCase()),
        );
      } on FormatException {
        return false;
      }
    }
    if (format != 'date' && format != 'date-time') return false;
    final match = RegExp(
      format == 'date'
          ? r'^(\d{4})-(\d{2})-(\d{2})$'
          : r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$',
    ).firstMatch(value);
    if (match == null) return false;
    final year = int.parse(match[1]!);
    final month = int.parse(match[2]!);
    final day = int.parse(match[3]!);
    if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31)
      return false;
    final date = DateTime.utc(year, month, day);
    if (date.month != month || date.day != day) return false;
    return format == 'date' ||
        (int.parse(match[4]!) < 24 &&
            int.parse(match[5]!) < 60 &&
            int.parse(match[6]!) < 60);
  }
}

Object? _canonical(Object? value) {
  if (value is Map<String, Object?>) {
    return {
      for (final key in value.keys.toList()..sort())
        key: _canonical(value[key]),
    };
  }
  if (value is List<Object?>) return value.map(_canonical).toList();
  if (value is num && value == value.truncateToDouble()) return value.toInt();
  return value;
}

bool _equal(Object? a, Object? b) =>
    jsonEncode(_canonical(a)) == jsonEncode(_canonical(b));
