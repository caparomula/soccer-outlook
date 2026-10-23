import 'dart:convert';
import 'dart:io';

import '../lib/probe.dart';

Future<void> main(List<String> args) async {
  if (args.length != 2) {
    stderr.writeln('Usage: dart capture.dart LEAGUE YYYYMMDD');
    exitCode = 64;
    return;
  }
  try {
    final capture = await beginCapture(scoreboardUri(args[0], args[1])).result;
    stdout.writeln(const JsonEncoder.withIndent('  ').convert(capture));
  } catch (error) {
    stderr.writeln('Probe failed: $error');
    exitCode = 1;
  }
}
