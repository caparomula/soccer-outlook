import 'package:timezone/data/latest_all.dart' as database;
import 'package:timezone/timezone.dart' as tz;

/// Calendar result for TIME-1. Instants are UTC; the label is a local date.
final class SportsDay {
  const SportsDay._(
    this.year,
    this.month,
    this.day,
    this.startUtc,
    this.endUtc,
  );

  final int year;
  final int month;
  final int day;
  final DateTime startUtc;
  final DateTime endUtc;

  String get date =>
      '${year.toString().padLeft(4, '0')}-'
      '${month.toString().padLeft(2, '0')}-'
      '${day.toString().padLeft(2, '0')}';

  /// Count calendar labels, never elapsed 24-hour periods in the viewer's zone.
  int indexFrom(SportsDay reference) => DateTime.utc(year, month, day)
      .difference(DateTime.utc(reference.year, reference.month, reference.day))
      .inDays;
}

/// TIME-1: a sports day begins at 04:00 in a supplied IANA location.
///
/// Bundles the same pinned full IANA database, including names returned by older
/// browser Intl implementations, in the VM, publisher CLI and browser.
/// No system zone or wall clock participates in a result.
final class SportsDayCalendar {
  SportsDayCalendar() {
    if (!_initialized) {
      database.initializeTimeZones();
      _initialized = true;
    }
  }

  static bool _initialized = false;

  /// End of an inclusive civil date in a named zone. Unlike a sports day, this
  /// boundary is midnight; WATCH-VALIDITY-1 uses it for inclusive rights dates.
  DateTime civilDayEnd(int year, int month, int day, String timeZone) {
    final date = DateTime.utc(year, month, day);
    if (date.year != year || date.month != month || date.day != day) {
      throw ArgumentError('Invalid civil date');
    }
    if (timeZone != 'UTC' &&
        !tz.timeZoneDatabase.locations.containsKey(timeZone)) {
      throw ArgumentError('Unknown IANA location');
    }
    final location = timeZone == 'UTC' ? tz.UTC : tz.getLocation(timeZone);
    return tz.TZDateTime(location, year, month, day + 1).toUtc();
  }

  /// Shift calendar labels, not elapsed 24-hour durations, from the containing
  /// sports day. This is the heading date/boundary operation across DST changes.
  SportsDay shift(DateTime instantUtc, String timeZone, int offsetDays) {
    final reference = containing(instantUtc, timeZone);
    final date = DateTime.utc(
      reference.year,
      reference.month,
      reference.day + offsetDays,
    );
    final location = timeZone == 'UTC' ? tz.UTC : tz.getLocation(timeZone);
    final start = tz.TZDateTime(location, date.year, date.month, date.day, 4);
    final end = tz.TZDateTime(
      location,
      start.year,
      start.month,
      start.day + 1,
      4,
    );
    return SportsDay._(
      start.year,
      start.month,
      start.day,
      start.toUtc(),
      end.toUtc(),
    );
  }

  SportsDay containing(DateTime instantUtc, String timeZone) {
    if (!instantUtc.isUtc) {
      throw ArgumentError.value(instantUtc, 'instantUtc', 'UTC required');
    }
    if (timeZone != 'UTC' &&
        !tz.timeZoneDatabase.locations.containsKey(timeZone)) {
      throw ArgumentError.value(timeZone, 'timeZone', 'Unknown IANA location');
    }
    final location = timeZone == 'UTC' ? tz.UTC : tz.getLocation(timeZone);
    final local = tz.TZDateTime.from(instantUtc, location);
    // DateTime.utc is used only for calendar normalization (month/year edges).
    var date = DateTime.utc(
      local.year,
      local.month,
      local.day - (local.hour < 4 ? 1 : 0),
    );
    var start = tz.TZDateTime(location, date.year, date.month, date.day, 4);
    // A jurisdiction can skip a whole local date (Apia, December 2011).
    // Move to the preceding actual boundary if normalization moved it forward.
    while (start.isAfter(instantUtc)) {
      date = DateTime.utc(date.year, date.month, date.day - 1);
      start = tz.TZDateTime(location, date.year, date.month, date.day, 4);
    }
    final end = tz.TZDateTime(location, date.year, date.month, date.day + 1, 4);
    return SportsDay._(
      date.year,
      date.month,
      date.day,
      start.toUtc(),
      end.toUtc(),
    );
  }
}
