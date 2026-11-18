// Relic 2.0: the routes of benchmarks/servers/conform.py's contract on 127.0.0.1:CW_PORT (8090 by default), with an
// isolate per core. CW_ADAPTER picks the adapter: `io` (relic_io, on dart:io's HttpServer; `app.serve`) or `native`
// (relic_native, Relic 2.0's HTTP server in Zig; `app.serveNative`). Both speak HTTP/1.0 and 1.1 only, so there is
// no h2c. With CW_CERT and CW_KEY (PEM), the port speaks TLS instead: relic_io through dart:io's SecurityContext
// (BoringSSL), relic_native through its NativeTls (tls.zig, TLS 1.3 only). Both agree on `http/1.1` by ALPN.
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:relic/relic.dart';
import 'package:relic_native/relic_native.dart';

Uint8List alphabet(int count) =>
    Uint8List.fromList([for (var i = 0; i < count; i++) 97 + i % 26]);

// Made once per isolate; each response sends them. The MIME type is given, so Relic does not sniff each body.
final small = Uint8List.fromList(utf8.encode('hello, world\n'));
final fourK = alphabet(4096);
final sixtyFourK = alphabet(65536);
final piece = alphabet(16384);
const echoLimit = 1 << 20;

Response fixed(Uint8List bytes) =>
    Response.ok(body: Body.fromData(bytes, mimeType: MimeType.octetStream));

Stream<Uint8List> pieces() async* {
  for (var i = 0; i < 64; i++) {
    yield piece;
  }
}

Future<Response> echo(Request req) async {
  final out = BytesBuilder(copy: false);
  await for (final chunk in req.read(maxLength: echoLimit)) {
    out.add(chunk);
  }
  return Response.ok(
    body: Body.fromData(out.takeBytes(), mimeType: MimeType.octetStream),
  );
}

RelicApp routes() => RelicApp()
  ..get('/small', (req) => fixed(small))
  ..get('/4k', (req) => fixed(fourK))
  ..get('/64k', (req) => fixed(sixtyFourK))
  ..get(
    '/stream',
    (req) => Response.ok(
      body: Body.fromDataStream(pieces(), mimeType: MimeType.octetStream),
    ),
  )
  ..get(
    '/json',
    (req) => Response.ok(
      body: Body.fromString(
        jsonEncode({'message': 'Hello, World!'}),
        mimeType: MimeType.json,
      ),
    ),
  )
  ..post('/echo', echo);

/// relic_io over TLS in this isolate: its own SecurityContext, the port bound shared with the other isolates'.
/// `serve(noOfIsolates: n, securityContext: c)` cannot be used: it sends `c` to each isolate it spawns, and Dart
/// refuses to send a SecurityContext between isolates ("object is unsendable"), so the isolates are spawned here and
/// bind the port as `serve` would, shared.
Future<void> serveIoTls(List<Object> portCertKey) async {
  final [port as int, cert as String, key as String] = portCertKey;
  await routes().serve(
    address: InternetAddress.loopbackIPv4,
    port: port,
    shared: true,
    securityContext: SecurityContext()
      ..useCertificateChain(cert)
      ..usePrivateKey(key)
      ..setAlpnProtocols(['http/1.1'], true),
  );
}

Future<void> main() async {
  final port = int.tryParse(Platform.environment['CW_PORT'] ?? '') ?? 8090;
  final adapter = Platform.environment['CW_ADAPTER'] ?? 'io';
  final isolates = Platform.numberOfProcessors;
  final address = InternetAddress.loopbackIPv4;
  final cert = Platform.environment['CW_CERT'] ?? '';
  final key = Platform.environment['CW_KEY'] ?? '';
  final tls = cert.isNotEmpty && key.isNotEmpty;
  if (adapter == 'native') {
    await routes().serveNative(
      address: address,
      port: port,
      noOfIsolates: isolates,
      tls: tls
          ? NativeTls(
              certificateChain: File(cert).readAsBytesSync(),
              privateKey: File(key).readAsBytesSync(),
            )
          : null,
    );
  } else if (tls) {
    await Future.wait([
      for (var i = 1; i < isolates; i++)
        Isolate.spawn(serveIoTls, <Object>[port, cert, key]),
    ]);
    await serveIoTls([port, cert, key]);
  } else {
    await routes().serve(address: address, port: port, noOfIsolates: isolates);
  }
  stdout.writeln(
    'relic ($adapter) on ${address.address}:$port${tls ? ' over TLS' : ''}, '
    '$isolates isolates',
  );
}
