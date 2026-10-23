import 'route_basis.dart';
import 'sports_day.dart';

/// An inclusive civil rights date, with no implied time or contractual start.
final class RightsDate {
  RightsDate(this.year, this.month, this.day) {
    final normalized = DateTime.utc(year, month, day);
    if (year < 0 ||
        year > 9999 ||
        normalized.year != year ||
        normalized.month != month ||
        normalized.day != day) {
      throw ArgumentError('Invalid rights date');
    }
  }

  final int year;
  final int month;
  final int day;
}

/// Qualified facts only: source, package, identity and rejection checks stay
/// upstream. Original verification and expiry are never renewed by this model.
final class RouteValidity {
  RouteValidity({
    required this.basis,
    required this.retained,
    required this.retentionSeconds,
    this.verifiedAt,
    this.expiresAt,
    this.kickoffUtc,
    this.rightsUntil,
  }) {
    if (retentionSeconds < 0 || retentionSeconds > 21600) {
      throw ArgumentError('Retention must be between zero and six hours');
    }
    for (final instant in [verifiedAt, expiresAt, kickoffUtc]) {
      if (instant != null && !instant.isUtc) {
        throw ArgumentError('UTC validity instants required');
      }
    }
  }

  final RouteBasis basis;
  final bool retained;
  final int retentionSeconds;
  final DateTime? verifiedAt;
  final DateTime? expiresAt;
  final DateTime? kickoffUtc;
  final RightsDate? rightsUntil;
}

enum RouteValidityReason {
  unknownBasis('unknown_basis'),
  invalidRetainedBasis('invalid_retained_basis'),
  missingVerification('missing_verification'),
  futureVerification('future_verification'),
  missingKickoff('missing_kickoff'),
  missingRightsUntil('missing_rights_until'),
  expired('expired');

  const RouteValidityReason(this.wireName);
  final String wireName;
}

final class RouteValidityDecision {
  const RouteValidityDecision._(this.usable, this.reason, this.expiresAt);

  final bool usable;
  final RouteValidityReason? reason;
  final DateTime? expiresAt;
}

RouteValidityDecision _invalid(RouteValidityReason reason) =>
    RouteValidityDecision._(false, reason, null);

/// WATCH-VALIDITY-1. DateTime comparisons/additions preserve microseconds in the
/// pinned Dart VM and JS implementations; do not replace them with JS Date or
/// integer epoch conversions. Every effective cutoff is end-exclusive.
RouteValidityDecision evaluateRouteValidity(
  RouteValidity facts,
  DateTime nowUtc,
) {
  if (!nowUtc.isUtc) throw ArgumentError('UTC evaluation instant required');
  if (facts.basis == RouteBasis.none)
    return _invalid(RouteValidityReason.unknownBasis);
  if (facts.retained && facts.basis != RouteBasis.listed) {
    return _invalid(RouteValidityReason.invalidRetainedBasis);
  }
  DateTime? cutoff = facts.expiresAt;
  void shorten(DateTime candidate) {
    if (cutoff == null || candidate.isBefore(cutoff!)) cutoff = candidate;
  }

  if (facts.retained) {
    final verified = facts.verifiedAt;
    if (verified == null)
      return _invalid(RouteValidityReason.missingVerification);
    if (verified.isAfter(nowUtc))
      return _invalid(RouteValidityReason.futureVerification);
    final kickoff = facts.kickoffUtc;
    if (kickoff == null) return _invalid(RouteValidityReason.missingKickoff);
    shorten(verified.add(Duration(seconds: facts.retentionSeconds)));
    shorten(kickoff.add(const Duration(hours: 4)));
  } else if (facts.basis == RouteBasis.rule) {
    final until = facts.rightsUntil;
    if (until == null) return _invalid(RouteValidityReason.missingRightsUntil);
    shorten(
      SportsDayCalendar().civilDayEnd(
        until.year,
        until.month,
        until.day,
        'America/New_York',
      ),
    );
  }
  final end = cutoff;
  if (end != null && !nowUtc.isBefore(end)) {
    return RouteValidityDecision._(false, RouteValidityReason.expired, end);
  }
  return RouteValidityDecision._(true, null, end);
}
