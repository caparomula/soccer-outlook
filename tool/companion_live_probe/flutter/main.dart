// Copy alongside lib/probe.dart in a disposable Flutter shell (see README).
import 'dart:convert';

import 'package:flutter/material.dart';

import 'probe.dart';

void main() => runApp(const MaterialApp(home: ProbeScreen()));

class ProbeScreen extends StatefulWidget {
  const ProbeScreen({super.key});
  @override
  State<ProbeScreen> createState() => _ProbeScreenState();
}

class _ProbeScreenState extends State<ProbeScreen> with WidgetsBindingObserver {
  late final ProbeSession _session;
  String _output = 'Select Check once to make one request.';
  bool _started = false;
  int _viewGeneration = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _session = ProbeSession(
      () => beginCapture(
        scoreboardUri(
          const String.fromEnvironment('LEAGUE', defaultValue: 'eng.1'),
          const String.fromEnvironment('SCOREBOARD_DATE'),
        ),
      ),
    );
  }

  Future<void> _check() async {
    if (_session.busy) return;
    final generation = ++_viewGeneration;
    setState(() {
      _started = true;
      _output = 'Checking…';
    });
    try {
      final result = await _session.check();
      if (!mounted || generation != _viewGeneration) return;
      setState(() {
        _output = result == null
            ? 'Check cancelled; previous response is obsolete.'
            : const JsonEncoder.withIndent('  ').convert(result);
      });
    } catch (error) {
      if (mounted && generation == _viewGeneration) {
        setState(() => _output = 'Check failed: $error');
      }
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _session.resume();
      if (_started) _check();
    } else if (state == AppLifecycleState.hidden ||
        state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      _session.pause();
      _viewGeneration++;
    }
    // Inactive can still be visible (for example, an open system overlay).
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _session.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('P0 foreground transport probe')),
    body: SafeArea(
      child: Column(
        children: [
          ElevatedButton(
            onPressed: _session.busy ? null : _check,
            child: const Text('Check once'),
          ),
          const Padding(
            padding: EdgeInsets.all(8),
            child: Text(
              'Receipt time is not source score freshness. '
              'Returning from pause makes one fresh check.',
            ),
          ),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(12),
              child: SelectableText(_output),
            ),
          ),
        ],
      ),
    ),
  );
}
