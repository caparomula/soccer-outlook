import 'dart:io';
import 'dart:typed_data';

import 'package:outlook_data/outlook_data.dart';

/// Bounded reads also reject symlinks escaping the explicitly supplied root.
/// This CLI reads a fixed local snapshot; concurrent file mutation is detected
/// by digest checks, not handled as a publication transaction.
class LocalPublicationFiles {
  LocalPublicationFiles._(this.root);
  final String root;

  static Future<LocalPublicationFiles> open(String path) async =>
      LocalPublicationFiles._(await Directory(path).resolveSymbolicLinks());

  Future<List<int>> read(String path, int maxBytes) async {
    if (path.startsWith('/') ||
        path.split('/').contains('..') ||
        path.contains('\\')) {
      throw const FeedValidationException('unsafe_local_path');
    }
    final file = File('$root/$path');
    final actual = await file.resolveSymbolicLinks();
    if (!actual.startsWith('$root${Platform.pathSeparator}')) {
      throw const FeedValidationException('unsafe_local_path');
    }
    if (await file.length() > maxBytes)
      throw const FeedValidationException('byte_limit');
    final bytes = BytesBuilder(copy: false);
    await for (final chunk in file.openRead()) {
      if (bytes.length + chunk.length > maxBytes) {
        throw const FeedValidationException('byte_limit');
      }
      bytes.add(chunk);
    }
    return bytes.takeBytes();
  }
}

Future<SchemaRegistry> loadSchemas(String directory) async {
  final files = await LocalPublicationFiles.open(directory);
  final schemas = <String, Object?>{};
  for (final kind in [
    'common',
    'manifest',
    'catalog',
    'schedule',
    'policy',
    'editorial',
  ]) {
    final name = '$kind.schema.json';
    schemas[name] = decodeBoundedJson(
      await files.read(name, 1048576),
      maxBytes: 1048576,
    );
  }
  return SchemaRegistry(schemas);
}
