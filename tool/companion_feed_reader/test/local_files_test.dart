import 'dart:io';

import 'package:companion_feed_reader/local_files.dart';
import 'package:outlook_data/outlook_data.dart';
import 'package:test/test.dart';

void main() {
  late Directory temporary;
  setUp(() async {
    temporary = await Directory.systemTemp.createTemp('companion-reader-test-');
  });
  tearDown(() async => temporary.delete(recursive: true));

  test('streaming reader enforces bound and rejects escaped symlink', () async {
    final root = await Directory('${temporary.path}/api').create();
    await File('${root.path}/small.json').writeAsString('{}');
    await File('${temporary.path}/outside.json').writeAsString('{}');
    await Link('${root.path}/escape.json')
        .create('${temporary.path}/outside.json');
    final files = await LocalPublicationFiles.open(root.path);
    expect(await files.read('small.json', 2), [123, 125]);
    for (final path in ['../outside.json', 'escape.json', '/outside.json']) {
      await expectLater(
        files.read(path, 10),
        throwsA(isA<FeedValidationException>()),
      );
    }
    await expectLater(
      files.read('small.json', 1),
      throwsA(isA<FeedValidationException>()),
    );
  });

  test(
    'local fixture round trip uses canonical schemas independently',
    () async {
      final files = await LocalPublicationFiles.open(
        '../../contracts/outlook/v1/examples',
      );
      final schemas = await loadSchemas('../../contracts/outlook/v1');
      final snapshot = await FeedReader(
        schemas,
      ).read(await files.read('manifest.json', manifestByteLimit), files.read);
      expect(snapshot.summary()['fixture_ids'], ['espn:fixture:example-1']);
      expect(snapshot.editorialState, 'accepted');
    },
  );
}
