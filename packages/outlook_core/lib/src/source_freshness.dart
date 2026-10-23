import 'fixture_window.dart';

/// LIVE-FRESHNESS-1 is a response timeout, not provider observation freshness.
const liveResponseTimeout = Duration(minutes: 3);

enum SourceFreshnessReason {
  notInProgress('not_in_progress'),
  missingReceipt('missing_receipt'),
  futureReceipt('future_receipt'),
  expired('expired');

  const SourceFreshnessReason(this.wireName);
  final String wireName;
}

/// Accepted source phase and original response receipt, without derived age.
final class SourceFreshnessFacts {
  SourceFreshnessFacts({required this.sourceState, required this.receivedAt}) {
    if (receivedAt != null && !receivedAt!.isUtc) {
      throw ArgumentError('UTC response receipt required');
    }
  }

  final FixtureSourceState sourceState;

  /// Receipt of the response supplying this fixture, never publication time.
  /// Null preserves absent provenance; it does not acquire an evaluation time.
  final DateTime? receivedAt;
}

final class SourceFreshnessDecision {
  const SourceFreshnessDecision._({
    required this.liveNow,
    required this.lastKnown,
    required this.reason,
    this.expiresAt,
    this.nextTransitionAt,
  });

  /// Whether the accepted in-progress response supports a current-live claim.
  final bool liveNow;

  /// An in-progress report whose retained score/clock needs qualification.
  final bool lastKnown;
  final SourceFreshnessReason? reason;
  final DateTime? expiresAt;

  /// Earliest future instant that can change this decision without new facts.
  final DateTime? nextTransitionAt;
}

/// LIVE-FRESHNESS-1: a current-live claim expires at the three-minute boundary.
///
/// This rule never reads a clock, extrapolates a score, rewrites source status,
/// or treats response receipt as the time at which a provider observed a score.
/// Consumers preserve the original facts and reevaluate at [nextTransitionAt]
/// from the returned decision, using their current supplied clock.
SourceFreshnessDecision evaluateSourceFreshness(
  SourceFreshnessFacts facts,
  DateTime nowUtc,
) {
  if (!nowUtc.isUtc) throw ArgumentError('UTC evaluation instant required');
  if (facts.sourceState != FixtureSourceState.inProgress) {
    return const SourceFreshnessDecision._(
      liveNow: false,
      lastKnown: false,
      reason: SourceFreshnessReason.notInProgress,
    );
  }
  final received = facts.receivedAt;
  if (received == null) {
    return const SourceFreshnessDecision._(
      liveNow: false,
      lastKnown: true,
      reason: SourceFreshnessReason.missingReceipt,
    );
  }
  final expires = received.add(liveResponseTimeout);
  if (received.isAfter(nowUtc)) {
    return SourceFreshnessDecision._(
      liveNow: false,
      lastKnown: true,
      reason: SourceFreshnessReason.futureReceipt,
      expiresAt: expires,
      nextTransitionAt: received,
    );
  }
  if (nowUtc.isBefore(expires)) {
    return SourceFreshnessDecision._(
      liveNow: true,
      lastKnown: false,
      reason: null,
      expiresAt: expires,
      nextTransitionAt: expires,
    );
  }
  return SourceFreshnessDecision._(
    liveNow: false,
    lastKnown: true,
    reason: SourceFreshnessReason.expired,
    expiresAt: expires,
  );
}
