import 'dart:js_interop';

import 'package:outlook_bridge_spike/protocol.dart';

@JS('outlookSportsDay')
external set _sportsDay(JSFunction function);

@JS('outlookDecisions')
external set _decisions(JSFunction function);

void main() {
  final dispatch = ((JSString input) => evaluate(input.toDart).toJS).toJS;
  _sportsDay = dispatch;
  _decisions = dispatch;
}
