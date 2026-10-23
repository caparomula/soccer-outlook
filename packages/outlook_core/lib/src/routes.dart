import 'route_basis.dart';
import 'route_validity.dart';

export 'route_basis.dart';

/// A listed outlet or a usual-home rule, after source and policy normalization.
final class RouteCandidate {
  RouteCandidate({
    required this.label,
    required Iterable<String> serviceIds,
    required this.spanish,
    this.unknown = false,
    this.validity,
  }) : serviceIds = List.unmodifiable(serviceIds);

  final String label;
  final List<String> serviceIds;
  final bool spanish;

  /// An unrecognized outlet cannot grant access, even with contradictory IDs.
  final bool unknown;
  final RouteValidity? validity;
}

/// Explicit viewer choices. Neither platform storage nor owner defaults live here.
final class ViewerLineup {
  ViewerLineup({
    required Iterable<String> selectedServiceIds,
    required Iterable<String> serviceOrder,
    required Map<String, String> serviceNames,
  }) : selectedServiceIds = Set.unmodifiable(selectedServiceIds),
       serviceOrder = List.unmodifiable(serviceOrder),
       serviceNames = Map.unmodifiable(serviceNames);

  final Set<String> selectedServiceIds;
  final List<String> serviceOrder;
  final Map<String, String> serviceNames;

  int rankOf(String serviceId) {
    final index = serviceOrder.indexOf(serviceId);
    return index < 0 ? 99 : index;
  }
}

/// Providers align with the input route order; each list follows viewer rank.
final class RouteDecision {
  RouteDecision._({
    required this.serviceId,
    required this.basis,
    required this.outlet,
    required Iterable<Iterable<String>> listedProviders,
    required Iterable<String> usualProviders,
    required Iterable<RouteValidityDecision?> listedValidity,
    required this.usualValidity,
  }) : listedProviders = List.unmodifiable(
         listedProviders.map(List<String>.unmodifiable),
       ),
       usualProviders = List.unmodifiable(usualProviders),
       listedValidity = List.unmodifiable(listedValidity);

  final String? serviceId;
  final RouteBasis basis;
  final String outlet;
  final List<List<String>> listedProviders;
  final List<String> usualProviders;
  final List<RouteValidityDecision?> listedValidity;
  final RouteValidityDecision? usualValidity;
}

List<String> _providers(RouteCandidate route, ViewerLineup lineup) {
  if (route.unknown) return const [];
  // An explicit input position makes equal-rank ordering independent of sort
  // stability. This matches the browser's stable service-preference ordering.
  final eligible = route.serviceIds.indexed
      .where((entry) => lineup.selectedServiceIds.contains(entry.$2))
      .toList();
  eligible.sort((a, b) {
    final rank = lineup.rankOf(a.$2).compareTo(lineup.rankOf(b.$2));
    return rank == 0 ? a.$1.compareTo(b.$1) : rank;
  });
  return eligible.map((entry) => entry.$2).toList();
}

/// WATCH-ROUTES-1: listed access wins over a usual-home rule. Within listed access,
/// rank service preference first, the service's own outlet second, and English
/// before Spanish third. Equal keys retain the first source candidate.
RouteDecision selectRoute({
  required Iterable<RouteCandidate> listed,
  RouteCandidate? usual,
  required ViewerLineup lineup,
  DateTime? nowUtc,
}) {
  final routes = listed.toList();
  if (nowUtc != null && !nowUtc.isUtc)
    throw ArgumentError('UTC evaluation instant required');
  RouteValidityDecision? validity(RouteCandidate route, RouteBasis location) {
    final facts = route.validity;
    if (facts == null) return null;
    if (nowUtc == null)
      throw ArgumentError('Validity requires an explicit evaluation instant');
    if (facts.basis != location && facts.basis != RouteBasis.none) {
      throw ArgumentError('Validity basis differs from route location');
    }
    return evaluateRouteValidity(facts, nowUtc);
  }

  final listedValidity = routes
      .map((route) => validity(route, RouteBasis.listed))
      .toList();
  final usualValidity = usual == null ? null : validity(usual, RouteBasis.rule);
  final listedProviders = routes.indexed
      .map(
        (entry) => listedValidity[entry.$1]?.usable == false
            ? <String>[]
            : _providers(entry.$2, lineup),
      )
      .toList();
  final usualProviders = usual == null || usualValidity?.usable == false
      ? <String>[]
      : _providers(usual, lineup);
  int? bestKey;
  String? serviceId;
  var outlet = '';
  for (final entry in routes.indexed) {
    final route = entry.$2;
    if (route.unknown || listedValidity[entry.$1]?.usable == false) continue;
    for (final id in route.serviceIds) {
      if (!lineup.selectedServiceIds.contains(id)) continue;
      final key =
          lineup.rankOf(id) * 4 +
          (route.label == lineup.serviceNames[id] ? 0 : 2) +
          (route.spanish ? 1 : 0);
      if (bestKey == null || key < bestKey) {
        bestKey = key;
        serviceId = id;
        outlet = route.label;
      }
    }
  }
  var basis = serviceId == null ? RouteBasis.none : RouteBasis.listed;
  if (serviceId == null && usualProviders.isNotEmpty) {
    serviceId = usualProviders.first;
    outlet = usual!.label;
    basis = RouteBasis.rule;
  }
  return RouteDecision._(
    serviceId: serviceId,
    basis: basis,
    outlet: outlet,
    listedProviders: listedProviders,
    usualProviders: usualProviders,
    listedValidity: listedValidity,
    usualValidity: usualValidity,
  );
}
