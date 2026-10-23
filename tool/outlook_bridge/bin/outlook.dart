import 'dart:convert';
import 'dart:io';

import 'package:outlook_bridge_spike/protocol.dart';

String _evaluateBytes(List<int> input) {
  try {
    return evaluate(utf8.decode(input));
  } on FormatException {
    return invalidInputResponse;
  }
}

Future<void> main(List<String> args) async {
  if (args.isNotEmpty && (args.length != 1 || args.single != '--serve')) {
    stdout.writeln(invalidInputResponse);
    exitCode = 65;
    return;
  }
  final serve = args.isNotEmpty;
  var bytes = <int>[];
  var oversized = false;
  await for (final chunk in stdin) {
    for (final byte in chunk) {
      if (serve && byte == 10) {
        stdout.writeln(
          oversized ? invalidInputResponse : _evaluateBytes(bytes),
        );
        bytes = <int>[];
        oversized = false;
      } else if (!oversized) {
        if (bytes.length >= maxProtocolLength) {
          oversized = true;
          bytes = <int>[];
        } else {
          bytes.add(byte);
        }
      }
    }
  }
  if (!serve || bytes.isNotEmpty || oversized) {
    final output = oversized ? invalidInputResponse : _evaluateBytes(bytes);
    stdout.writeln(output);
    if (!serve && output == invalidInputResponse) exitCode = 65;
  }
}
