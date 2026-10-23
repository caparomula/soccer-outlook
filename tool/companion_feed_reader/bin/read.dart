import 'dart:convert';
import 'dart:io';

import 'package:companion_feed_reader/local_files.dart';
import 'package:outlook_data/outlook_data.dart';

Future<void> main(List<String> arguments) async {
  final options = <String, String>{};
  for (var i = 0; i < arguments.length; i += 2) {
    if (i + 1 == arguments.length ||
        !{'--api-dir', '--schema-dir'}.contains(arguments[i]) ||
        options.containsKey(arguments[i])) {
      stderr.writeln(
        'Usage: read.dart --api-dir DIRECTORY --schema-dir contracts/outlook/v1',
      );
      exitCode = 64;
      return;
    }
    options[arguments[i]] = arguments[i + 1];
  }
  if (options.length != 2) {
    stderr.writeln(
      'Usage: read.dart --api-dir DIRECTORY --schema-dir contracts/outlook/v1',
    );
    exitCode = 64;
    return;
  }
  try {
    final schemas = await loadSchemas(options['--schema-dir']!);
    final files = await LocalPublicationFiles.open(options['--api-dir']!);
    final snapshot = await FeedReader(schemas)
        .read(await files.read('manifest.json', manifestByteLimit), files.read);
    stdout.writeln(jsonEncode(snapshot.summary()));
  } on Exception catch (error) {
    stdout.writeln(jsonEncode({'version': 1, 'error': 'invalid_publication'}));
    stderr.writeln(
      error is FeedValidationException ? error.code : 'local_input_unavailable',
    );
    exitCode = 65;
  }
}
