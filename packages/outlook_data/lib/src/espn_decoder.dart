/// STATUS-ESPN-1 normalized source facts, not observation freshness or placement.
enum DecodedMatchState {
  scheduled,
  live,
  finished,
  postponed,
  cancelled,
  abandoned,
  unknown,
}

/// Compatibility placement phase. A post phase alone does not establish final.
enum DecodedMatchPhase {
  pre('pre'),
  inProgress('in'),
  post('post'),
  unknown('unknown');

  const DecodedMatchPhase(this.wireName);
  final String wireName;
}

final class ReportedScorePair {
  const ReportedScorePair(this.home, this.away);
  final int? home;
  final int? away;
}

/// Immutable source interpretation. Reported numeric facts remain separate from
/// whether this observation establishes ordinary live/completed score semantics.
final class DecodedMatchStatus {
  const DecodedMatchStatus._({
    required this.state,
    required this.phase,
    required this.providerState,
    required this.label,
    required this.reportedScores,
  });

  final DecodedMatchState state;
  final DecodedMatchPhase phase;
  final String providerState;
  final String label;
  final ReportedScorePair reportedScores;

  bool get played =>
      state == DecodedMatchState.live || state == DecodedMatchState.finished;
  bool get isFinal => state == DecodedMatchState.finished;
  bool get isVoid =>
      state == DecodedMatchState.postponed ||
      state == DecodedMatchState.cancelled ||
      state == DecodedMatchState.abandoned;
  ReportedScorePair get scores =>
      played ? reportedScores : const ReportedScorePair(null, null);
}

enum _Identity {
  scheduled(DecodedMatchState.scheduled),
  live(DecodedMatchState.live),
  halftime(DecodedMatchState.live),
  finished(DecodedMatchState.finished),
  postponed(DecodedMatchState.postponed),
  cancelled(DecodedMatchState.cancelled),
  abandoned(DecodedMatchState.abandoned);

  const _Identity(this.state);
  final DecodedMatchState state;
}

const _names = {
  'status_scheduled': _Identity.scheduled,
  'status_in_progress': _Identity.live,
  'status_first_half': _Identity.live,
  'status_second_half': _Identity.live,
  'status_halftime': _Identity.halftime,
  'status_full_time': _Identity.finished,
  'status_final': _Identity.finished,
  'status_canceled': _Identity.cancelled,
  'status_cancelled': _Identity.cancelled,
  'status_postponed': _Identity.postponed,
  'status_abandoned': _Identity.abandoned,
};
const _tokens = {
  'scheduled': _Identity.scheduled,
  'in progress': _Identity.live,
  'live': _Identity.live,
  'first half': _Identity.live,
  'second half': _Identity.live,
  'halftime': _Identity.halftime,
  'ht': _Identity.halftime,
  'full time': _Identity.finished,
  'full-time': _Identity.finished,
  'final': _Identity.finished,
  'ft': _Identity.finished,
  'canceled': _Identity.cancelled,
  'cancelled': _Identity.cancelled,
  'can': _Identity.cancelled,
  'postponed': _Identity.postponed,
  'ppd': _Identity.postponed,
  'abandoned': _Identity.abandoned,
  'abd': _Identity.abandoned,
};
final _scoreDigits = RegExp(r'^[0-9]{1,3}$');

Map<String, Object?> _object(Object? value) =>
    value is Map<String, Object?> ? value : const {};

String? _text(Object? value) =>
    value is String && value.length <= 100 ? value : null;

String? _display(Object? value) {
  final text = _text(value);
  return text != null && text.isNotEmpty ? text : null;
}

_Identity? _recognize(Object? value, Map<String, _Identity> table) {
  final text = _text(value);
  return text == null ? null : table[text.trim().toLowerCase()];
}

int? _score(Object? value) {
  // At most one displayValue projection; nested objects are not numeric facts.
  if (value is Map<String, Object?>) value = value['displayValue'];
  if (value is String) {
    final match = _scoreDigits.matchAsPrefix(value);
    if (match == null || match.end != value.length) return null;
    final number = int.parse(value);
    return number <= 100 ? number : null;
  }
  if (value is num &&
      value.isFinite &&
      value >= 0 &&
      value <= 100 &&
      value == value.truncateToDouble()) {
    return value.toInt();
  }
  return null;
}

/// Select a nonempty competition object, otherwise the event object, without
/// merging their fields. Malformed source values yield conservative facts;
/// protocol structure, size and identity validation belongs to the caller.
DecodedMatchStatus decodeEspnStatus({
  required Object? eventStatus,
  required Object? competitionStatus,
  required Object? homeScore,
  required Object? awayScore,
}) {
  final competition = _object(competitionStatus);
  final status = competition.isNotEmpty ? competition : _object(eventStatus);
  final type = _object(status['type']);
  final name = type['name'];
  final named = _recognize(name, _names);
  final unsupportedName =
      name != null &&
      !(name is String && name.trim().isEmpty && name.length <= 100) &&
      named == null;
  return _classify(
    phase: type['state'],
    identities: [
      named,
      _recognize(type['description'], _tokens),
      _recognize(type['shortDetail'], _tokens),
      _recognize(type['abbreviation'], _tokens),
    ],
    unsupportedName: unsupportedName,
    completed: type['completed'],
    liveLabel:
        _display(status['displayClock']) ?? _display(type['shortDetail']),
    homeScore: homeScore,
    awayScore: awayScore,
  );
}

/// Reclassify extracted facts, or preserve a supplied canonical [state] after
/// checking its phase. Bound live labels are display, never status identity.
/// Absent/null state retains legacy inference for older extracted fixtures.
DecodedMatchStatus decodeFixtureStatus({
  Object? state,
  required Object? phase,
  required Object? label,
  required Object? homeScore,
  required Object? awayScore,
}) => _classify(
  phase: phase,
  normalizedState: state,
  identities: state == null ? [_recognize(label, _tokens)] : const [],
  unsupportedName: false,
  completed: null,
  liveLabel: _display(label),
  homeScore: homeScore,
  awayScore: awayScore,
);

DecodedMatchStatus _classify({
  required Object? phase,
  Object? normalizedState,
  required List<_Identity?> identities,
  required bool unsupportedName,
  required Object? completed,
  required String? liveLabel,
  required Object? homeScore,
  required Object? awayScore,
}) {
  final provider = _text(phase) ?? '';
  final meanings = identities.nonNulls
      .map((identity) => identity.state)
      .toSet();
  var state = DecodedMatchState.unknown;
  if (normalizedState != null) {
    final supplied = DecodedMatchState.values
        .where((value) => value.name == normalizedState)
        .firstOrNull;
    final consistent = switch (supplied) {
      DecodedMatchState.scheduled => provider == 'pre',
      DecodedMatchState.live => provider == 'in',
      DecodedMatchState.finished ||
      DecodedMatchState.postponed ||
      DecodedMatchState.cancelled ||
      DecodedMatchState.abandoned => provider == 'post',
      DecodedMatchState.unknown => provider == 'post' || provider == 'unknown',
      null => false,
    };
    if (consistent) state = supplied!;
  } else if (!unsupportedName && meanings.length <= 1) {
    final identified = meanings.firstOrNull;
    if (identified == DecodedMatchState.postponed ||
        identified == DecodedMatchState.cancelled ||
        identified == DecodedMatchState.abandoned) {
      state = identified!;
    } else {
      final fromPhase = switch (provider) {
        'pre' => DecodedMatchState.scheduled,
        'in' => DecodedMatchState.live,
        'post' => DecodedMatchState.finished,
        _ => DecodedMatchState.unknown,
      };
      if (identified != null) {
        if (identified == fromPhase) state = identified;
      } else if (provider == 'pre' || provider == 'in') {
        state = fromPhase;
      }
      if (completed == true &&
              (state == DecodedMatchState.scheduled ||
                  state == DecodedMatchState.live) ||
          completed == false && state == DecodedMatchState.finished) {
        state = DecodedMatchState.unknown;
      }
    }
  }
  final placement = switch (state) {
    DecodedMatchState.scheduled => DecodedMatchPhase.pre,
    DecodedMatchState.live => DecodedMatchPhase.inProgress,
    DecodedMatchState.unknown =>
      provider == 'post' ? DecodedMatchPhase.post : DecodedMatchPhase.unknown,
    _ => DecodedMatchPhase.post,
  };
  final label = switch (state) {
    DecodedMatchState.scheduled => '',
    DecodedMatchState.live =>
      identities.contains(_Identity.halftime) ? 'HT' : liveLabel ?? 'Live',
    DecodedMatchState.finished => 'FT',
    DecodedMatchState.postponed => 'Postponed',
    DecodedMatchState.cancelled => 'Canceled',
    DecodedMatchState.abandoned => 'Abandoned',
    DecodedMatchState.unknown => 'Status unknown',
  };
  return DecodedMatchStatus._(
    state: state,
    phase: placement,
    providerState: provider,
    label: label,
    reportedScores: ReportedScorePair(_score(homeScore), _score(awayScore)),
  );
}
