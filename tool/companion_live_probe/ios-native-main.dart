// Disposable native P0 harness; copy as lib/main.dart with probe.dart.
// Also usable on Android. Never included in the production application.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import 'probe.dart';

void main() => runApp(const MaterialApp(home: NativeProbe()));

class NativeProbe extends StatefulWidget {
  const NativeProbe({super.key});

  @override
  State<NativeProbe> createState() => _NativeProbeState();
}

class _NativeProbeState extends State<NativeProbe> with WidgetsBindingObserver {
  File? _evidence;
  HttpServer? _server;
  SecurityContext? _clientContext;
  String? _localCertificatePem;
  ProbeSession? _session;
  int _lifecycleRequests = 0;
  int _lifecycleChecks = 0;
  bool _waitingPause = false;
  bool _sawPause = false;
  bool _resumeStarted = false;
  String _state = 'Starting isolated native checks';

  void record(String event, [Map<String, Object?> data = const {}]) {
    final value = {
      'event': event,
      'at': DateTime.now().toUtc().toIso8601String(),
      ...data,
    };
    final line = jsonEncode(value);
    // One compact record per line, also retained in the app's documents.
    // ignore: avoid_print
    print('P0_NATIVE $line');
    _evidence?.writeAsStringSync('$line\n', mode: FileMode.append, flush: true);
    if (mounted) setState(() => _state = event);
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => runChecks());
  }

  Future<void> runChecks() async {
    try {
      final documents = await getApplicationDocumentsDirectory();
      _evidence = File('${documents.path}/p0-native.jsonl');
      _evidence!.writeAsStringSync('');
      final cache = await getApplicationCacheDirectory();
      final temporary = await getTemporaryDirectory();
      final zone = await FlutterTimezone.getLocalTimezone();
      record('environment', {
        'platform': Platform.operatingSystem,
        'os_version': Platform.operatingSystemVersion,
        'dart_version': Platform.version,
        'iana_zone': zone.identifier,
        'documents_exists': documents.existsSync(),
        'cache_exists': cache.existsSync(),
        'temporary_exists': temporary.existsSync(),
        'plugins': {
          'sqflite': '2.4.4+2',
          'path_provider': '2.1.6',
          'flutter_timezone': '5.1.1',
        },
      });
      final path = '${temporary.path}/companion-p0-smoke.sqlite';
      await deleteDatabase(path);
      var database = await openDatabase(
        path,
        version: 1,
        onCreate: (db, version) async {
          await db.execute(
            'CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)',
          );
        },
      );
      await database.transaction((txn) async {
        await txn.insert('probe', {'id': 1, 'value': 'persisted'});
      });
      await database.close();
      database = await openDatabase(path);
      final rows = await database.query('probe');
      if (rows.length != 1 || rows.single['value'] != 'persisted') {
        throw StateError('SQLite close/reopen did not retain transaction');
      }
      await database.close();
      await deleteDatabase(path);
      record('sqlite_close_reopen_pass', {'rows': rows.length});

      if (const bool.fromEnvironment('RUN_PUBLIC_CAPTURE')) {
        try {
          final capture = await beginCapture(
            scoreboardUri(
              const String.fromEnvironment(
                'LEAGUE',
                defaultValue: 'uefa.champions',
              ),
              const String.fromEnvironment('SCOREBOARD_DATE'),
            ),
          ).result;
          record('public_https_pass', {'capture': capture});
        } catch (error) {
          record('public_https_failed', {'error': '$error'});
        }
      } else {
        record('public_https_skipped');
      }

      final cert = (await rootBundle.load('assets/localhost-cert.pem')).buffer
          .asUint8List();
      final key = (await rootBundle.load('assets/localhost-key.pem')).buffer
          .asUint8List();
      _localCertificatePem = utf8.decode(cert).trim();
      final serverContext = SecurityContext()
        ..useCertificateChainBytes(cert)
        ..usePrivateKeyBytes(key);
      _clientContext = SecurityContext(withTrustedRoots: false)
        ..setTrustedCertificatesBytes(cert);
      _server = await HttpServer.bindSecure(
        InternetAddress.loopbackIPv4,
        0,
        serverContext,
      );
      _server!.listen((request) {
        final isLifecycle = request.uri.path == '/p0/lifecycle';
        if (isLifecycle) _lifecycleRequests++;
        record('local_request_received', {
          'path': request.uri.path,
          'lifecycle_request_count': _lifecycleRequests,
        });
        if (request.uri.path == '/p0/timeout') {
          // Intentionally never send response headers.
          return;
        }
        if (isLifecycle && _lifecycleRequests == 1) {
          // This connection must be cancelled when the native app backgrounds.
          return;
        }
        request.response.headers.contentType = ContentType.json;
        request.response.write('{"events":[]}');
        unawaited(request.response.close());
      });
      final stopwatch = Stopwatch()..start();
      try {
        await localCapture('/p0/timeout').result;
        throw StateError('Stalled connection unexpectedly completed');
      } on TimeoutException {
        final elapsed = stopwatch.elapsedMilliseconds;
        if (elapsed < 7900 || elapsed > 12000) {
          throw StateError(
            'Timeout outside expected native range: $elapsed ms',
          );
        }
        record('real_socket_timeout_pass', {'elapsed_ms': elapsed});
      }
      final recovered = await localCapture('/p0/recovery').result;
      record('after_timeout_recovery_pass', {
        'http_status': recovered['http_status'],
      });
      _session = ProbeSession(() => localCapture('/p0/lifecycle'));
      _waitingPause = true;
      record('ready_for_native_background');
      unawaited(checkLifecycle());
    } catch (error, stack) {
      record('harness_failed', {
        'error': '$error',
        'stack': stack.toString().split('\n').take(5).join('\n'),
      });
    }
  }

  CaptureOperation localCapture(String path) {
    // Keep beginCapture unchanged and its HTTPS/host checks intact. This scope
    // alone trusts the disposable certificate and connects to loopback.
    // The public capture above uses the ordinary OS trust/connection path.
    final client = HttpClient(context: _clientContext);
    client.findProxy = (_) => 'DIRECT';
    client.connectionFactory = (uri, proxyHost, proxyPort) =>
        SecureSocket.startConnect(
          InternetAddress.loopbackIPv4,
          _server!.port,
          context: _clientContext,
          // Apple can reject a self-signed test leaf even when installed in
          // this context. Pin only this disposable loopback certificate.
          onBadCertificate: (cert) => cert.pem.trim() == _localCertificatePem,
        );
    return HttpOverrides.runZoned(
      () => beginCapture(Uri.https('site.api.espn.com', path)),
      createHttpClient: (_) => client,
    );
  }

  Future<void> checkLifecycle() async {
    final check = ++_lifecycleChecks;
    try {
      final result = await _session!.check();
      record('lifecycle_check_completed', {
        'check': check,
        'cancelled': result == null,
        'request_count': _lifecycleRequests,
      });
      if (check == 1 && result != null) {
        throw StateError('Backgrounded operation was accepted');
      }
      if (check == 2) {
        if (result == null || _lifecycleRequests != 2 || !_sawPause) {
          throw StateError('Resume did not accept exactly one fresh operation');
        }
        record('native_pause_resume_pass', {
          'requests': _lifecycleRequests,
          'old_check_rejected': true,
        });
        _waitingPause = false;
        _session!.dispose();
        await _server!.close(force: true);
        record('all_native_checks_complete');
      }
    } catch (error) {
      record('lifecycle_check_failed', {'check': check, 'error': '$error'});
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    record('native_lifecycle', {
      'state': state.name,
      'lifecycle_request_count': _lifecycleRequests,
    });
    if (!_waitingPause) return;
    if (state == AppLifecycleState.hidden ||
        state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      _sawPause = true;
      _session!.pause();
      record('session_cancelled_on_native_pause', {'busy': _session!.busy});
    } else if (state == AppLifecycleState.resumed &&
        _sawPause &&
        !_resumeStarted) {
      _resumeStarted = true;
      _session!.resume();
      unawaited(checkLifecycle());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _session?.dispose();
    unawaited(_server?.close(force: true));
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Disposable P0 native evidence')),
    body: Padding(padding: const EdgeInsets.all(24), child: Text(_state)),
  );
}
