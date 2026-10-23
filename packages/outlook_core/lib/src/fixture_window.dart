import 'sports_day.dart';

/// An accepted normalized status, not a raw-provider decoder or played verdict.
enum FixtureSourceState {
  pre('pre'),
  inProgress('in'),
  post('post'),
  // STATUS-ESPN-1 preserves uncertain progress without inventing a pre fact.
  // Its placement arithmetic is pre-like; it never grants receipt recency.
  unknown('unknown');

  const FixtureSourceState(this.wireName);
  final String wireName;
}

/// Schedule placement does not assert source progress or score freshness.
enum FixtureBucket {
  live('live'),
  earlier('earlier'),
  today('today'),
  tomorrow('tomorrow'),
  day2('day2'),
  day3('day3'),
  yesterday('yesterday'),
  dayMinus2('dayminus2'),
  dayMinus3('dayminus3');

  const FixtureBucket(this.wireName);
  final String wireName;
}

/// TIME-WINDOW-1 facts. A TBD time keeps its existing placeholder instant.
final class FixtureWindowFacts {
  FixtureWindowFacts({
    required this.kickoffUtc,
    required this.timeConfirmed,
    required this.sourceState,
    required this.receivedAt,
  }) {
    if (!kickoffUtc.isUtc || receivedAt != null && !receivedAt!.isUtc) {
      throw ArgumentError('UTC fixture instants required');
    }
  }

  final DateTime kickoffUtc;
  final bool timeConfirmed;
  final FixtureSourceState sourceState;

  /// Original receipt of the accepted status update; not provider observation.
  final DateTime? receivedAt;
}

final class FixtureWindowDecision {
  const FixtureWindowDecision._({
    required this.day,
    required this.dayIndex,
    required this.bucket,
    required this.inFocus,
    required this.sourceInProgress,
    required this.recentLiveReceipt,
  });

  final SportsDay day;
  final int dayIndex;
  final FixtureBucket? bucket;
  bool get inWindow => bucket != null;

  /// Independent of schedule clipping; a recent receipt may extend old focus.
  final bool inFocus;

  /// What the accepted source said, even when its age is unknown or stale.
  final bool sourceInProgress;
  final bool recentLiveReceipt;
}

/// TIME-WINDOW-1: coherent focus and bucket decisions using one supplied clock.
///
/// Exact comparisons retain the inclusive 125-minute focus boundary and strict
/// 155-minute same-day grace, 24-hour horizon and ten-minute receipt boundaries.
/// This does not decode statuses, renew receipts, extrapolate scores or decide
/// whether a live-only destination is usable. No system clock participates.
FixtureWindowDecision evaluateFixtureWindow(
  FixtureWindowFacts facts,
  DateTime nowUtc,
  String timeZone,
) {
  if (!nowUtc.isUtc) throw ArgumentError('UTC evaluation instant required');
  final calendar = SportsDayCalendar();
  final day = calendar.containing(facts.kickoffUtc, timeZone);
  final reference = calendar.containing(nowUtc, timeZone);
  final index = day.indexFrom(reference);
  final kickoff = facts.kickoffUtc;
  final inProgress = facts.sourceState == FixtureSourceState.inProgress;
  final received = facts.receivedAt;
  final recent =
      inProgress &&
      received != null &&
      !received.isAfter(nowUtc) &&
      nowUtc.isBefore(received.add(const Duration(minutes: 10)));
  final focus =
      facts.sourceState != FixtureSourceState.post &&
      kickoff.isBefore(nowUtc.add(const Duration(hours: 24))) &&
      (!kickoff.isBefore(nowUtc) ||
          !kickoff.isBefore(nowUtc.subtract(const Duration(minutes: 125))) &&
              (inProgress || facts.timeConfirmed) ||
          recent);

  FixtureBucket? bucket() {
    if (index < -3 || index > 3) return null;
    if (focus && facts.timeConfirmed && !kickoff.isAfter(nowUtc)) {
      return FixtureBucket.live;
    }
    if (index < 0) {
      return const [
        FixtureBucket.yesterday,
        FixtureBucket.dayMinus2,
        FixtureBucket.dayMinus3,
      ][-index - 1];
    }
    if (index == 0) {
      if (facts.sourceState == FixtureSourceState.post) {
        return FixtureBucket.earlier;
      }
      if (inProgress &&
          (nowUtc.isBefore(kickoff.add(const Duration(minutes: 155))) ||
              recent)) {
        return FixtureBucket.live;
      }
      if (facts.timeConfirmed && !nowUtc.isBefore(kickoff)) {
        return nowUtc.isBefore(kickoff.add(const Duration(minutes: 125)))
            ? FixtureBucket.live
            : FixtureBucket.earlier;
      }
    }
    return const [
      FixtureBucket.today,
      FixtureBucket.tomorrow,
      FixtureBucket.day2,
      FixtureBucket.day3,
    ][index];
  }

  return FixtureWindowDecision._(
    day: day,
    dayIndex: index,
    bucket: bucket(),
    inFocus: focus,
    sourceInProgress: inProgress,
    recentLiveReceipt: recent,
  );
}
