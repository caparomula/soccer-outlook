import 'dart:convert';

/// No payload text is included in errors, including malformed input.
class FeedValidationException implements Exception {
  const FeedValidationException(this.code);
  final String code;
  @override
  String toString() => 'FeedValidationException: $code';
}

const manifestByteLimit = 65536;
const artifactByteLimit = 8388608;

/// Decodes JSON with allocation limits applied while tokenizing, before schema
/// validation. Duplicate keys include escaped spellings of the same name.
Object? decodeBoundedJson(List<int> bytes, {required int maxBytes}) {
  if (bytes.length > maxBytes) {
    throw const FeedValidationException('byte_limit');
  }
  try {
    return _Decoder(utf8.decode(bytes, allowMalformed: false)).decode();
  } on FormatException {
    throw const FeedValidationException('invalid_json');
  }
}

class _Decoder {
  _Decoder(this.text);
  final String text;
  int position = 0;
  int nodes = 0;
  static final _number = RegExp(
    r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',
  );

  Never fail([String code = 'invalid_json']) =>
      throw FeedValidationException(code);

  Object? decode() {
    final result = value(0);
    space();
    if (position != text.length) fail();
    return result;
  }

  void space() {
    while (position < text.length) {
      final c = text.codeUnitAt(position);
      if (c != 32 && c != 9 && c != 10 && c != 13) break;
      position++;
    }
  }

  bool take(int code) {
    space();
    if (position < text.length && text.codeUnitAt(position) == code) {
      position++;
      return true;
    }
    return false;
  }

  Object? value(int depth) {
    if (depth > 64) fail('nesting_limit');
    if (++nodes > 1000000) fail('node_limit');
    space();
    if (position == text.length) fail();
    final code = text.codeUnitAt(position);
    if (code == 34) return string();
    if (code == 123) {
      position++;
      final result = <String, Object?>{};
      if (take(125)) return result;
      while (true) {
        space();
        if (position == text.length || text.codeUnitAt(position) != 34) fail();
        final key = string();
        if (result.containsKey(key)) fail('duplicate_key');
        if (result.length >= 100) fail('object_limit');
        if (!take(58)) fail();
        result[key] = value(depth + 1);
        if (take(125)) return result;
        if (!take(44)) fail();
      }
    }
    if (code == 91) {
      position++;
      final result = <Object?>[];
      if (take(93)) return result;
      while (true) {
        if (result.length >= 30000) fail('array_limit');
        result.add(value(depth + 1));
        if (take(93)) return result;
        if (!take(44)) fail();
      }
    }
    for (final literal in const {
      'true': true,
      'false': false,
      'null': null,
    }.entries) {
      if (text.startsWith(literal.key, position)) {
        position += literal.key.length;
        return literal.value;
      }
    }
    final match = _number.matchAsPrefix(text, position);
    if (match == null) fail();
    position = match.end;
    final token = match.group(0)!;
    if (!token.contains(RegExp(r'[.eE]'))) {
      final integer = int.tryParse(token);
      if (integer == null || integer.abs() > 9007199254740991)
        fail('unsafe_integer');
    }
    final number = num.tryParse(token);
    if (number == null || !number.isFinite) fail('non_finite_number');
    return number;
  }

  String string() {
    final start = position++;
    var escaped = false;
    while (position < text.length) {
      final code = text.codeUnitAt(position++);
      if (position - start > 65536) fail('string_limit');
      if (!escaped && code == 34) {
        final result = jsonDecode(text.substring(start, position)) as String;
        // JSON's escape syntax permits isolated UTF-16 surrogates; reject them
        // so native and web consumers receive the same Unicode string.
        for (var i = 0; i < result.length; i++) {
          final unit = result.codeUnitAt(i);
          if (unit >= 0xd800 && unit <= 0xdbff) {
            if (++i >= result.length) fail('invalid_unicode');
            final low = result.codeUnitAt(i);
            if (low < 0xdc00 || low > 0xdfff) fail('invalid_unicode');
          } else if (unit >= 0xdc00 && unit <= 0xdfff) {
            fail('invalid_unicode');
          }
        }
        return result;
      }
      if (!escaped && code == 92) {
        escaped = true;
      } else {
        escaped = false;
      }
    }
    fail();
  }
}
