# web_tls

- [enum TlsSetupError](#enum-tlssetuperror)
- [struct TlsConfig](#struct-tlsconfig)
- [fn opensslVersion](#fn-opensslversion)
- [struct TlsTransport](#struct-tlstransport)
- [struct TlsAcceptor](#struct-tlsacceptor)
- [struct TlsConnector](#struct-tlsconnector)
- [struct TlsSession](#struct-tlssession)

## enum TlsSetupError

```ceesafe
public enum TlsSetupError {
    BadCertificate,
    BadTrust,
    BadAlpn,
    Internal,
    BadSetting,
}
```

Why a configuration could not be made.

## struct TlsConfig

```ceesafe
@threadSafe
@noncopy
public struct TlsConfig {
    drop { … }
}
```

A TLS configuration: TLS 1.2 (ECDHE with AEAD only) and 1.3, no renegotiation, no compression. A server's has its
certificate; a client's verifies the server against its trust store and the name it connects to.
Thread-safe by this package's claim (Web ADR 0009 §1): OpenSSL 3 lets threads share an SSL_CTX, and the shim
changes its own state (SNI table, reload, ticket keys) under OpenSSL's lock.

### TlsConfig.server

```ceesafe
public fn server(chainPem: Slice<u8>, keyPem: Slice<u8>) -> Result<TlsConfig, TlsSetupError>
```

A server configuration presenting `chainPem` (the leaf certificate first, then intermediates) with `keyPem`.
Safety: wt_cfg_new returns a fresh SSL_CTX or null; the shim copies the PEM bytes and keeps no pointer to them.

### TlsConfig.client

```ceesafe
public fn client() -> Result<TlsConfig, TlsSetupError>
```

A client configuration that trusts nothing yet: add `trustPem` or `trustSystem`.
Safety: as `server`.

### TlsConfig.trustPem

```ceesafe
public fn trustPem(borrow mut this, pem: Slice<u8>) -> Result<void, TlsSetupError>
```

Trusts every certificate in `pem` as a root.
Safety: `raw` is this configuration's live SSL_CTX; the shim copies the bytes.

### TlsConfig.setCipherSuites13

```ceesafe
public fn setCipherSuites13(borrow mut this, list: StringView) -> Result<void, TlsSetupError>
```

Restricts TLS 1.3 to the cipher suites in `list`, OpenSSL's names, colon-separated and in preference order
(`"TLS_CHACHA20_POLY1305_SHA256"`).
Safety: as `trustPem`; the shim copies the list.

### TlsConfig.writeKeyLog

```ceesafe
public fn writeKeyLog(borrow mut this, path: StringView) -> Result<void, TlsSetupError>
```

Appends this configuration's TLS secrets to the key log at `path` (NSS's key log format, the one
`SSLKEYLOGFILE` names for curl and browsers), so captures of its connections can be decrypted. For tests and
debugging only: whoever reads the file can decrypt those connections. One file per process.
Safety: as `trustPem`; the shim copies the path.

### TlsConfig.trustSystem

```ceesafe
public fn trustSystem(borrow mut this) -> Result<void, TlsSetupError>
```

Trusts the system's roots: OpenSSL's default CA locations, and on Windows the system's certificate store
(OpenSSL 3.2 or later there).
Safety: as `trustPem`.

### TlsConfig.setAlpn

```ceesafe
public fn setAlpn(borrow mut this, protocols: Slice<Alpn>) -> Result<void, TlsSetupError>
```

The application protocols, in preference order: a server picks its first that the client offers (none in
common: no ALPN, so HTTP/1.1); a client offers them all. `Alpn.None` entries are skipped.
Safety: as `trustPem`; the wire list is a local array the shim copies.

### TlsConfig.addName

```ceesafe
public fn addName(
    borrow this,
    name: StringView,
    chainPem: Slice<u8>,
    keyPem: Slice<u8>,
) -> Result<void, TlsSetupError>
```

A server's certificate for clients asking for `name` by SNI: "api.example.com", or "*.example.com" for any one
label before it (an exact name wins over a wildcard). Clients asking for another name, or none, get the
default certificate. Adding a name already present replaces its certificate, for new handshakes only.
Safety: the shim copies the bytes and changes its table under OpenSSL's lock, so `borrow this` is enough while
connections are being made from this configuration.

### TlsConfig.reload

```ceesafe
public fn reload(borrow this, chainPem: Slice<u8>, keyPem: Slice<u8>) -> Result<void, TlsSetupError>
```

Replaces a server's default certificate (hot reload): handshakes from now on present it, connections already
made keep theirs. On failure the old one stays.
Safety: as `addName`.

### TlsConfig.rotateTicketKeys

```ceesafe
public fn rotateTicketKeys(borrow this) -> Result<void, TlsSetupError>
```

Starts a new session-ticket key: tickets are issued with it from now on; tickets from the key it replaces still
resume (and are renewed), older ones do not. A server rotates on a timer, at most every ticket lifetime.
Safety: as `addName`; the keys are made and kept in the shim, never in CeeSafe memory.

### TlsConfig.requireClientCertificates

```ceesafe
public fn requireClientCertificates(borrow mut this, rootsPem: Slice<u8>) -> Result<void, TlsSetupError>
```

Requires every client to present a certificate that verifies to a root in `rootsPem` (mTLS). A client without
one, or with one that does not verify, fails its handshake.
Safety: as `trustPem`.

### TlsConfig.requestClientCertificates

```ceesafe
public fn requestClientCertificates(borrow mut this, rootsPem: Slice<u8>) -> Result<void, TlsSetupError>
```

Asks clients for a certificate that verifies to a root in `rootsPem`, but lets a client without one in (its
connection has an empty `peerIdentity`). A certificate that does not verify still fails the handshake.
Safety: as `trustPem`.

### TlsConfig.clientCertificate

```ceesafe
public fn clientCertificate(
    borrow mut this,
    chainPem: Slice<u8>,
    keyPem: Slice<u8>,
) -> Result<void, TlsSetupError>
```

The certificate a client presents when a server asks for one.
Safety: as `trustPem`.

### TlsConfig.isServer

```ceesafe
public fn isServer(borrow this) -> bool
```

Whether this is a server's configuration.

### TlsConfig.quicContext

```ceesafe
public fn quicContext(borrow this) -> *mut void
```

The SSL_CTX, for `web_quic`'s shim (Web ADR 0014 §3.4), which takes a reference of its own
(`SSL_CTX_up_ref`) before this call's caller returns, so the context outlives this configuration if need be.
Not for other uses: the pointer is valid only while this configuration lives.

## fn opensslVersion

```ceesafe
public fn opensslVersion(out: SliceMut<u8>) -> usize
```

The OpenSSL in use, as its version text ("OpenSSL 3.6.4 25 Aug 2026"), written into `out`; its length.
Safety: the shim copies at most `out.length` bytes into `out`.

## struct TlsTransport

```ceesafe
@threadSafe
@noncopy
public struct TlsTransport {
    drop { … }
}
```

A TLS connection over a plain stream. Thread-safe by this package's claim (Web ADR 0009 §1): its SSL is used by one
task at a time (`borrow mut`), and only OpenSSL's read-only getters are reached through shared borrows.

### TlsTransport.accept

```ceesafe
public async fn accept(
    stream: PlainStream,
    config: borrow TlsConfig,
    deadline: Deadline,
) -> Result<TlsTransport, NetError>
```

A server connection over `stream` with `config`, its handshake finished by `deadline`.

### TlsTransport.connect

```ceesafe
public async fn connect(
    stream: PlainStream,
    config: borrow TlsConfig,
    host: StringView,
    deadline: Deadline,
) -> Result<TlsTransport, NetError>
```

A client connection over `stream` to `host` (a DNS name or an IP address, verified against the server's
certificate), its handshake finished by `deadline`.

### TlsTransport.connectResuming

```ceesafe
public async fn connectResuming(
    stream: PlainStream,
    config: borrow TlsConfig,
    host: StringView,
    session: borrow TlsSession,
    deadline: Deadline,
) -> Result<TlsTransport, NetError>
```

A client connection as `connect`, offering `session` for resumption; the server may decline it, and the
handshake is then a full one.
Safety: wt_ctx_offer_session takes its own reference to the session.

### TlsTransport.session

```ceesafe
public fn session(borrow this) -> TlsSession?
```

This client connection's session, to resume a later connection with; null if it cannot be resumed (TLS 1.3
sends its tickets after the handshake: read from the connection first).
Safety: wt_ctx_session returns a new reference or null; TlsSession frees it.

### TlsTransport.resumed

```ceesafe
public fn resumed(borrow this) -> bool
```

Whether the handshake resumed a session.
Safety: `conn` is this transport's live context.

### TlsTransport.version

```ceesafe
public fn version(borrow this) -> u32
```

The TLS version agreed: 0x0303 (1.2) or 0x0304 (1.3).
Safety: `conn` is this transport's live context.

### TlsTransport.failure

```ceesafe
public fn failure(borrow this, out: SliceMut<u8>) -> usize
```

The text of the last TLS failure (OpenSSL's, or the certificate check's) in `out`; its length.
Safety: the shim copies at most `out.length` bytes into `out` and writes the length into a local.

Implements `Transport`.

## struct TlsAcceptor

```ceesafe
@noncopy
public struct TlsAcceptor
```

The acceptor for TLS servers: a handshake with its configuration on every accepted stream.

### TlsAcceptor.configuration

```ceesafe
public fn configuration(borrow this) -> borrow TlsConfig
```

Its configuration, for `reload`, `addName` and `rotateTicketKeys` while it serves.

### TlsAcceptor.create

```ceesafe
public fn create(config: TlsConfig) -> Result<TlsAcceptor, TlsSetupError>
```

An acceptor presenting `config` (a server configuration).

Implements `Acceptor`.

## struct TlsConnector

```ceesafe
@noncopy
public struct TlsConnector
```

The connector for TLS clients: a handshake with its configuration, verifying the host.

### TlsConnector.create

```ceesafe
public fn create(config: TlsConfig) -> Result<TlsConnector, TlsSetupError>
```

A connector with `config` (a client configuration).

Implements `Connector`.

## struct TlsSession

```ceesafe
@threadSafe
@noncopy
public struct TlsSession {
    drop { … }
}
```

A client's TLS session, kept to resume a later connection to the same server (`TlsTransport.connectResuming`): a
shorter handshake without the certificate exchange.
Thread-safe by this package's claim: OpenSSL counts an SSL_SESSION's references atomically.
