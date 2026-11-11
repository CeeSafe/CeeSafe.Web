# web_http3

- [enum H3Error](#enum-h3error)
- [struct H3Errors](#struct-h3errors)
- [enum H3Event](#enum-h3event)
- [struct H3Out](#struct-h3out)
- [struct H3Fields](#struct-h3fields)
- [struct H3Connection](#struct-h3connection)
- [fn h3EventBufferSize](#fn-h3eventbuffersize)
- [interface H3Streams](#interface-h3streams)
- [struct H3Glue](#struct-h3glue)
- [struct Http3Library](#struct-http3library)
- [struct Http3Options](#struct-http3options)
- [struct Http3Server](#struct-http3server)

## enum H3Error

```ceesafe
public enum H3Error {
    NoMemory,
    BufferTooSmall(usize),
    BadFields,
    Failed(i32),
}
```

What went wrong.

## struct H3Errors

```ceesafe
public struct H3Errors
```

The classification of nghttp3's error codes, done by nghttp3 itself through the shim.

### H3Errors.closeCode

```ceesafe
public fn closeCode(e: borrow H3Error) -> u64
```

The HTTP/3 error code to close the connection with after `e` (RFC 9114 §8.1): nghttp3's
`nghttp3_err_infer_quic_app_error_code`, so H3_MESSAGE_ERROR (0x10e) for a malformed request; H3_INTERNAL_ERROR
(0x102) for memory.
Safety: the call takes a number and returns one.

## enum H3Event

```ceesafe
public enum H3Event {
    Field(i64, usize, usize, bool),
    FieldsEnd(i64, bool, bool),
    Data(i64, usize),
    End(i64),
    Consumed(i64, u64),
    StopSending(i64, u64),
    Reset(i64, u64),
    Closed(i64, u64),
    BodyRoom(i64),
    GoAway(u64),
}
```

What happened on the connection. A field's name and value, and DATA's bytes, are in the buffer `nextEvent` was
given.

## struct H3Out

```ceesafe
public struct H3Out {
    public stream: i64;
    public length: usize;
    public fin: bool;
}
```

Bytes `pull` copied for QUIC: their stream and count, and whether they end the stream.

## struct H3Fields

```ceesafe
public struct H3Fields
```

A field list for `respond` and `trailers`, in the shim's form: per field, a 2-byte name length and a 4-byte value
length (big-endian), then the name and the value. Names must be lowercase (RFC 9114 §4.2).

### H3Fields.create

```ceesafe
public fn create() -> H3Fields
```

An empty list.

### H3Fields.add

```ceesafe
public fn add(borrow mut this, name: Slice<u8>, value: Slice<u8>) -> Result<void, AllocError>
```

Appends a field.

### H3Fields.clear

```ceesafe
public fn clear(borrow mut this)
```

Empties the list.

## struct H3Connection

```ceesafe
public struct H3Connection {
    drop { … }
}
```

One HTTP/3 connection's state (nghttp3's), for a server, or, in this package's tests, a client.

### H3Connection.server

```ceesafe
public fn server(maxFieldSection: u64) -> Result<H3Connection, H3Error>
```

A server's connection; a request's head may be at most `maxFieldSection` bytes (RFC 9114 §4.2.2, from
`Limits.maxHeadBytes`).
Safety: the shim returns a fresh handle or null.

### H3Connection.bind

```ceesafe
public fn bind(borrow mut this, control: i64, encoder: i64, decoder: i64) -> Result<void, H3Error>
```

Binds our three unidirectional streams, opened on the QUIC connection: the control stream, then QPACK's
encoder and decoder streams (RFC 9114 §6.2.1, RFC 9204 §4.2).
Safety: the call takes numbers.

### H3Connection.read

```ceesafe
public fn read(borrow mut this, stream: i64, bytes: Slice<u8>, fin: bool, now: u64) -> Result<u64, H3Error>
```

Feeds bytes QUIC delivered on `stream` (and its end, with `fin`) at `now` (the caller's monotonic clock, in
nanoseconds, never decreasing): how many nghttp3 consumed, whose credit goes back to the peer now. DATA's
payload is not among them; its credit goes back as the handler reads it. `now` runs nghttp3's flood guard
(H3_EXCESSIVE_LOAD after 1 000 suspicious events, then 33 a second).
Safety: the shim reads at most `bytes.length` bytes and keeps no pointer to them.

### H3Connection.pull

```ceesafe
public fn pull(borrow mut this, out: SliceMut<u8>) -> Result<H3Out?, H3Error>
```

The next bytes nghttp3 has for QUIC, copied into `out`: null when there are none. Give them to
`QuicLink.writeStream`, then say how many it took with `wrote`.
Safety: the shim writes at most `out.length` bytes into `out`, and the two values.

### H3Connection.wrote

```ceesafe
public fn wrote(borrow mut this, stream: i64, n: u64) -> Result<void, H3Error>
```

QUIC took `n` of the bytes `pull` gave for `stream` (0 for a FIN alone).
Safety: the call takes numbers.

### H3Connection.block

```ceesafe
public fn block(borrow mut this, stream: i64)
```

QUIC holds `stream` full: `pull` offers nothing more for it until `unblock`.
Safety: the call takes numbers.

### H3Connection.unblock

```ceesafe
public fn unblock(borrow mut this, stream: i64) -> Result<void, H3Error>
```

QUIC has room on `stream` again (`QuicEvent.StreamWritable`).
Safety: the call takes numbers.

### H3Connection.respond

```ceesafe
public fn respond(borrow mut this, stream: i64, fields: borrow H3Fields, hasBody: bool) -> Result<void, H3Error>
```

Submits a response's head on `stream`, `:status` first. With `hasBody`, its body follows through `sendBody`;
without, the head ends the stream.
Safety: the shim reads the field list and keeps no pointer to it (nghttp3 copies it).

### H3Connection.sendBody

```ceesafe
public fn sendBody(borrow mut this, stream: i64, bytes: Slice<u8>, end: bool) -> Result<usize, H3Error>
```

Takes up to `bytes.length` body bytes for `stream`, and ends the body after them when `end`: how many were
taken, fewer when the stream holds 256 KiB not yet sent (wait for `BodyRoom`).
Safety: the shim copies at most `bytes.length` bytes.

### H3Connection.trailers

```ceesafe
public fn trailers(borrow mut this, stream: i64, fields: borrow H3Fields) -> Result<void, H3Error>
```

Ends `stream`'s body with trailer fields.
Safety: as `respond`.

### H3Connection.shutdownRead

```ceesafe
public fn shutdownRead(borrow mut this, stream: i64) -> Result<void, H3Error>
```

The peer reset `stream`, or asked us to stop sending on it: nghttp3 reads no more of it.
Safety: the call takes numbers.

### H3Connection.shutdownWrite

```ceesafe
public fn shutdownWrite(borrow mut this, stream: i64)
```

Our sending side of `stream` was reset: nghttp3 sends nothing more on it.
Safety: the call takes numbers.

### H3Connection.closeStream

```ceesafe
public fn closeStream(borrow mut this, stream: i64, code: u64) -> Result<void, H3Error>
```

QUIC closed `stream` with `code` (`QuicEvent.StreamClosed`).
Safety: the call takes numbers.

### H3Connection.goAwayNotice

```ceesafe
public fn goAwayNotice(borrow mut this) -> Result<void, H3Error>
```

Sends the first GOAWAY, which only announces the shutdown (RFC 9114 §5.2).
Safety: the call takes the handle.

### H3Connection.goAway

```ceesafe
public fn goAway(borrow mut this) -> Result<void, H3Error>
```

Sends the final GOAWAY, with the last stream accepted.
Safety: as `goAwayNotice`.

### H3Connection.drained

```ceesafe
public fn drained(borrow this) -> bool
```

After `goAway`, whether every stream is done and the connection may close.
Safety: as `goAwayNotice`.

### H3Connection.nextEvent

```ceesafe
public fn nextEvent(borrow mut this, buffer: SliceMut<u8>) -> Result<H3Event?, H3Error>
```

The next event, its data copied into `buffer` (`h3EventBufferSize()` bytes is always enough for body data; a
field may need up to the head's limit); null when there is none. Drain it after every `read` and `wrote`.
Safety: the shim writes at most `buffer.length` bytes into `buffer`, and the four values.

## fn h3EventBufferSize

```ceesafe
public fn h3EventBufferSize() -> usize
```

A buffer for `nextEvent` that holds any DATA event (the shim splits longer DATA).

## interface H3Streams

```ceesafe
public interface H3Streams {
    @effects()
    fn openUni(borrow mut this) -> Result<i64, QuicError>;
    @effects()
    fn write(borrow mut this, stream: i64, bytes: Slice<u8>, fin: bool) -> Result<usize, QuicError>;
    @effects()
    fn credit(borrow mut this, stream: i64, n: u64);
    @effects()
    fn stop(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>;
    @effects()
    fn reset(borrow mut this, stream: i64, code: u64) -> Result<void, QuicError>;
}
```

The QUIC streams an HTTP/3 connection runs on. The connection must give credit back on consumption
(`QuicConnection.creditOnConsume`, or `QuicServerOptions.creditOnRead = false`).
Each is one call into web_quic's shim, which does no I/O: they never block, as an async task requires.

## struct H3Glue

```ceesafe
public struct H3Glue
```

The glue's operations.

### H3Glue.start

```ceesafe
public fn start(h3: borrow mut H3Connection, quic: borrow mut dyn H3Streams) -> Result<void, H3Error>
```

Opens our control stream and QPACK's two, and binds them (RFC 9114 §6.2.1, RFC 9204 §4.2). Once the handshake
is done.

### H3Glue.onQuic

```ceesafe
public fn onQuic(
    h3: borrow mut H3Connection,
    quic: borrow mut dyn H3Streams,
    event: borrow QuicEvent,
    bytes: Slice<u8>,
    now: u64,
) -> Result<void, H3Error>
```

What one QUIC event means for HTTP/3. `bytes` is the event's data, for `StreamData`; `now` is the caller's
monotonic clock in nanoseconds (`H3Connection.read`).

### H3Glue.obey

```ceesafe
public fn obey(
    h3: borrow mut H3Connection,
    quic: borrow mut dyn H3Streams,
    event: borrow H3Event,
) -> Result<void, H3Error>
```

Carries out what nghttp3 asks of QUIC in `event`: credit for bytes it consumed late, STOP_SENDING, a reset.
Other events are the caller's.

### H3Glue.flush

```ceesafe
public fn flush(
    h3: borrow mut H3Connection,
    quic: borrow mut dyn H3Streams,
    out: SliceMut<u8>,
) -> Result<void, H3Error>
```

Moves what nghttp3 has into QUIC's streams, through `out` (at least a datagram's worth): until nghttp3 has
nothing more, or every stream it offers is full. A stream QUIC takes less of is blocked until
`StreamWritable`.

## struct Http3Library

```ceesafe
public struct Http3Library
```

The library `web_http3` runs on.

### Http3Library.nghttp3Version

```ceesafe
public fn nghttp3Version() -> u32
```

nghttp3's version, as `0xMMmmpp` (1.19.0 is `0x011300`).
Safety: the call takes no arguments and returns a number; nghttp3's static version record is read in C only.

## struct Http3Options

```ceesafe
public struct Http3Options {
    public quic: QuicServerOptions;
    public requestsPerConnection: u32;
    public resetsPerSecond: u32;
}
```

How HTTP/3 serves.

### Http3Options.defaults

```ceesafe
public fn defaults() -> Http3Options
```

QUIC's defaults, 100 requests a connection (as many streams as a client may open at first), and 200 resets a
second (as HTTP/2's).

## struct Http3Server

```ceesafe
public struct Http3Server
```

The HTTP/3 server.

### Http3Server.serve

```ceesafe
public async fn serve(
    socket: borrow AsyncUdpSocket,
    local: SocketAddress,
    tls: Arc<QuicTls>,
    app: Arc<Box<dyn Application + Send + Share>>,
    limits: Limits,
    drain: borrow Drain,
    options: Http3Options,
) -> Result<void, QuicError>
```

Serves `app` over HTTP/3 on `socket` (bound to `local`) with `tls` (a server's, offering ALPN `h3`), until
cancelled. `limits` bound each request as over HTTP/1.1 and HTTP/2; a request's head may be at most
`limits.maxHeadBytes` (RFC 9114 §4.2.2). `drain` is shared with each request's context.
