import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

void main() {
  final calendar = SportsDayCalendar();
  final corpus = jsonDecode(
    File('../../tool/outlook_bridge/cases/sports_day.json').readAsStringSync(),
  ) as Map<String, dynamic>;
  for (final value in corpus['cases'] as List<dynamic>) {
    final example = value as Map<String, dynamic>;
    test(example['id'] as String, () {
      final zone = example['time_zone'] as String;
      final day = calendar.containing(
        DateTime.parse(example['instant'] as String),
        zone,
      );
      final reference = calendar.containing(
        DateTime.parse(example['reference'] as String),
        zone,
      );
      expect({
        'date': day.date,
        'start_utc': day.startUtc.toIso8601String(),
        'end_utc': day.endUtc.toIso8601String(),
        'day_index': day.indexFrom(reference),
      }, example['expected']);
    });
  }
  test('requires an explicit UTC instant', () {
    expect(
      () => calendar.containing(DateTime(2026, 10, 7), 'America/New_York'),
      throwsArgumentError,
    );
  });
  test('unknown location never silently becomes UTC', () {
    expect(
      () => calendar.containing(DateTime.utc(2026), 'Unknown/Nowhere'),
      throwsArgumentError,
    );
  });
  test('browser UTC alias has the same boundary as Etc/UTC', () {
    final instant = DateTime.parse('2026-10-20T03:59:59Z');
    final day = calendar.containing(instant, 'UTC');
    expect(day.date, '2026-10-19');
    expect(day.startUtc, DateTime.parse('2026-10-19T04:00:00Z'));
    expect(day.endUtc, calendar.containing(instant, 'Etc/UTC').endUtc);
  });
  for (final alias in {
    'Asia/Calcutta': '2026-10-19T22:30:00Z',
    'Europe/Kiev': '2026-10-20T01:00:00Z',
    'Asia/Katmandu': '2026-10-19T22:15:00Z',
    'US/Eastern': '2026-10-20T08:00:00Z',
    'GMT': '2026-10-20T04:00:00Z',
  }.entries) {
    test('browser-compatible IANA name ${alias.key}', () {
      final instant = DateTime.parse('2026-10-20T12:00:00Z');
      final day = calendar.containing(instant, alias.key);
      expect(day.date, '2026-10-20');
      expect(day.startUtc, DateTime.parse(alias.value));
      expect(calendar.shift(instant, alias.key, 0).startUtc, day.startUtc);
    });
  }
  test(
    'shift across a skipped date returns the actual normalized boundary',
    () {
      final shifted = calendar.shift(
        DateTime.parse('2011-12-29T14:00:00Z'),
        'Pacific/Apia',
        1,
      );
      expect(shifted.date, '2011-12-31');
      expect(shifted.startUtc, DateTime.parse('2011-12-30T14:00:00Z'));
      expect(shifted.endUtc, DateTime.parse('2011-12-31T14:00:00Z'));
    },
  );
  test('skipped local date still produces an interval containing the instant', () {
    // Apia jumped from December 29 to December 31 in 2011. This historical
    // regression is separate from the unchanged-current-browser parity corpus.
    final instant = DateTime.parse('2011-12-30T11:00:00Z');
    final day = calendar.containing(instant, 'Pacific/Apia');
    expect(day.date, '2011-12-29');
    expect(day.startUtc, DateTime.parse('2011-12-29T14:00:00Z'));
    expect(day.endUtc, DateTime.parse('2011-12-30T14:00:00Z'));
    expect(day.startUtc.isAfter(instant), isFalse);
    expect(day.endUtc.isAfter(instant), isTrue);
  });
}
