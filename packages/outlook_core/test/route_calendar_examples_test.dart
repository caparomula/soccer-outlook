import 'dart:convert';
import 'dart:io';

import 'package:outlook_core/outlook_core.dart';
import 'package:test/test.dart';

List<dynamic> _examples(String name) =>
    (jsonDecode(
          File('../../docs/companion/rules/$name-examples.json')
              .readAsStringSync(),
        ) as Map<String, dynamic>)['cases']
        as List<dynamic>;

RouteCandidate _route(dynamic input) {
  final value = input as Map<String, dynamic>;
  return RouteCandidate(
    label: value['label'] as String,
    serviceIds: (value['service_ids'] as List<dynamic>).cast<String>(),
    spanish: value['spanish'] as bool,
    unknown: value['unknown'] as bool? ?? false,
  );
}

void main() {
  // These examples were authored independently before running this core. They
  // provide literal expected outcomes, rather than reimplementing its ranking.
  for (final raw in _examples('route')) {
    final example = raw as Map<String, dynamic>;
    test('ROUTE-1 ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final viewer = request['lineup'] as Map<String, dynamic>;
      final lineup = ViewerLineup(
        selectedServiceIds: (viewer['selected_service_ids'] as List<dynamic>)
            .cast<String>(),
        serviceOrder: (viewer['service_order'] as List<dynamic>).cast<String>(),
        serviceNames: (viewer['service_names'] as Map<String, dynamic>)
            .cast<String, String>(),
      );
      final results = <Map<String, Object?>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final decision = selectRoute(
          listed: (value['listed'] as List<dynamic>).map(_route),
          usual: value['usual'] == null ? null : _route(value['usual']),
          lineup: lineup,
        );
        results.add({
          'id': value['id'],
          'service_id': decision.serviceId,
          'basis': decision.basis.name,
          'outlet': decision.outlet,
          'listed_providers': decision.listedProviders,
          'usual_providers': decision.usualProviders,
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
    });
  }
  final calendar = SportsDayCalendar();
  for (final raw in _examples('sports-day')) {
    final example = raw as Map<String, dynamic>;
    test('TIME-1 shift ${example['id']}', () {
      final request = example['request'] as Map<String, dynamic>;
      final results = <Map<String, Object>>[];
      for (final rawCase in request['cases'] as List<dynamic>) {
        final value = rawCase as Map<String, dynamic>;
        final day = calendar.shift(
          DateTime.parse(value['instant'] as String),
          value['time_zone'] as String,
          value['offset_days'] as int,
        );
        results.add({
          'id': value['id'] as String,
          'date': day.date,
          'start_utc': day.startUtc.toIso8601String(),
          'end_utc': day.endUtc.toIso8601String(),
          'year': day.year,
          'month': day.month,
          'day': day.day,
        });
      }
      expect({'version': 1, 'results': results}, example['expected']);
    });
  }
}
