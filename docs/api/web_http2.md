# web_http2

- [enum H2Event](#enum-h2event)
- [struct H2Guards](#struct-h2guards)
- [struct H2Connection](#struct-h2connection)
- [fn clientPreface](#fn-clientpreface)
- [struct FrameTypes](#struct-frametypes)
- [struct FrameFlags](#struct-frameflags)
- [struct ErrorCodes](#struct-errorcodes)
- [struct SettingIds](#struct-settingids)
- [struct FrameHeader](#struct-frameheader)
- [struct Frames](#struct-frames)
- [enum HpackError](#enum-hpackerror)
- [struct HpackDecoder](#struct-hpackdecoder)
- [struct HpackEncoder](#struct-hpackencoder)
- [fn writeInteger](#fn-writeinteger)
- [struct Huffman](#struct-huffman)
- [fn serveH2](#fn-serveh2)
- [struct H2Settings](#struct-h2settings)
- [struct RequestShape](#struct-requestshape)
- [struct Requests](#struct-requests)
- [struct ResponseShape](#struct-responseshape)
- [struct Responses](#struct-responses)

## enum H2Event

```ceesafe
public enum H2Event {
    NeedMore,
    Request(u32, bool),
    Response(u32, bool),
    Data(u32, bool),
    Trailers(u32),
    TooLarge(u32),
    Reset(u32, u32),
    StreamError(u32, u32),
    WindowOpened(u32),
    GoAway(u32, u32),
}
```

What the connection found in its input.

## struct H2Guards

```ceesafe
public struct H2Guards {
    public maxBlockBytes: u32;
    public resetsPerInterval: u32;
    public emptyFramesPerInterval: u32;
    public intervalMs: u64;
    public maxOwedAcks: u32;
    public encoderTableBytes: u32;
}
```

The guards' budgets (Web ADR 0011 §3.4).

### H2Guards.defaults

```ceesafe
public fn defaults(maxHeaderListSize: u32) -> H2Guards
```

Guards for a server whose header lists may be `maxHeaderListSize` bytes: header blocks up to twice that, 200
resets and 1 000 empty frames a second, 1 000 owed acknowledgements, an encoder table of 4 096 bytes.

## struct H2Connection

```ceesafe
@noncopy
public struct H2Connection
```

One side of an HTTP/2 connection: the server's (`create`) or the client's (`client`).

### H2Connection.create

```ceesafe
public fn create(local: H2Settings, guards: H2Guards) -> Result<H2Connection, AllocError>
```

A server's connection that announces `local`; `start` queues the server's preface.

### H2Connection.client

```ceesafe
public fn client(local: H2Settings, guards: H2Guards) -> Result<H2Connection, AllocError>
```

A client's connection that announces `local` (its SETTINGS_ENABLE_PUSH is sent as 0: we accept no push);
`start` queues the client's preface. Open requests with `openRequest`.

### H2Connection.start

```ceesafe
public fn start(borrow mut this, connectionWindow: u32) -> Result<void, AllocError>
```

Queues our preface (§3.4): for a client, the connection preface string first; then SETTINGS, and a
WINDOW_UPDATE raising the connection's window to `connectionWindow`.

### H2Connection.output

```ceesafe
public fn output(borrow this) -> Slice<u8>
```

The bytes to write to the peer.

### H2Connection.outputWritten

```ceesafe
public fn outputWritten(borrow mut this)
```

The output was written.

### H2Connection.lastStreamId

```ceesafe
public fn lastStreamId(borrow this) -> u32
```

The highest stream the client opened (GOAWAY's last stream).

### H2Connection.activeStreams

```ceesafe
public fn activeStreams(borrow this) -> u32
```

The client's streams not yet closed.

### H2Connection.streamSendWindow

```ceesafe
public fn streamSendWindow(borrow this, id: u32) -> i64
```

What we may still send on `id` (its window; 0 if it is not open), before the connection's window.

### H2Connection.connectionSendWindow

```ceesafe
public fn connectionSendWindow(borrow this) -> i64
```

What we may still send on the connection as a whole.

### H2Connection.remoteOpen

```ceesafe
public fn remoteOpen(borrow this, id: u32) -> bool
```

Whether the client may still send on `id`: open, or half-closed on our side only.

### H2Connection.peerSettings

```ceesafe
public fn peerSettings(borrow this) -> H2Settings
```

The peer's settings, as they stand.

### H2Connection.next

```ceesafe
public fn next(
    borrow mut this,
    input: borrow mut OwnedBuffer,
    head: borrow mut HeaderBlock,
    data: borrow mut Vector<u8>,
    nowMs: u64,
) -> Result<H2Event, u32>
```

The next event from `input` (consumed as frames are handled): a request's head and trailers go to `head`, body
bytes to `data`. `Err(code)` is a connection error: send `goAway(code)`, write the output, and close.

### H2Connection.consumed

```ceesafe
public fn consumed(borrow mut this, id: u32, n: usize) -> Result<void, u32>
```

The handler read `n` body bytes of `id`: their window is given back (WINDOW_UPDATE once half is due).

### H2Connection.sendAllowance

```ceesafe
public fn sendAllowance(borrow this, id: u32) -> usize
```

How many DATA bytes may go out on `id` now: the smaller window, at most one frame.

### H2Connection.sendHeaders

```ceesafe
public fn sendHeaders(borrow mut this, id: u32, fields: borrow HeaderBlock, endStream: bool) -> Result<void, u32>
```

Queues a HEADERS block for `id`: the response's head, or its trailers with `endStream`.

### H2Connection.sendData

```ceesafe
public fn sendData(borrow mut this, id: u32, bytes: Slice<u8>, endStream: bool) -> Result<void, u32>
```

Queues DATA on `id`: at most `sendAllowance(id)` bytes (any number, even none, with `endStream`).

### H2Connection.resetStream

```ceesafe
public fn resetStream(borrow mut this, id: u32, code: u32) -> Result<void, u32>
```

Resets `id` (a handler failed or was cancelled): RST_STREAM, and the stream closed.

### H2Connection.promise

```ceesafe
public fn promise(borrow mut this, parent: u32, request: borrow HeaderBlock) -> Result<u32?, u32>
```

Promises a pushed response on `parent` (§8.4): PUSH_PROMISE with the promised request's head, and a stream
reserved for its response. Null if the client disabled push, the connection is going away, the parent is not
open to us, or the client's SETTINGS_MAX_CONCURRENT_STREAMS is reached.

### H2Connection.ping

```ceesafe
public fn ping(borrow mut this, data: [u8; 8]) -> Result<void, u32>
```

Queues a PING (§6.7) carrying `data`. The driver sends one to probe a client that has stopped sending: writing
it to a peer that has gone makes the peer's TCP reset the connection, so a later write fails.

### H2Connection.openRequest

```ceesafe
public fn openRequest(
    borrow mut this,
    fields: borrow HeaderBlock,
    endStream: bool,
    headRequest: bool,
) -> Result<u32, u32>
```

Client role: opens a stream for a request whose head is `fields` (pseudo-fields first, RFC 9113 §8.3.1), with
END_STREAM when `endStream` (no body). `headRequest` says the method is HEAD. The new stream's ID, or
`Err(refusedStream)` when the peer's SETTINGS_MAX_CONCURRENT_STREAMS is reached, the peer is going away or the
IDs are used up: open another connection.

### H2Connection.isGoingAway

```ceesafe
public fn isGoingAway(borrow this) -> bool
```

Whether either side has sent GOAWAY: no new stream.

### H2Connection.streamsLeft

```ceesafe
public fn streamsLeft(borrow this) -> u32
```

Client role: how many more streams the peer allows now (SETTINGS_MAX_CONCURRENT_STREAMS less those open).

### H2Connection.goAway

```ceesafe
public fn goAway(borrow mut this, code: u32) -> Result<void, u32>
```

Queues GOAWAY with `code` and the last stream the client opened; new streams are ignored from now on.

## fn clientPreface

```ceesafe
public fn clientPreface() -> StringView
```

The connection preface a client sends first (§3.4).

## struct FrameTypes

```ceesafe
public struct FrameTypes
```

Frame types (§6).

### FrameTypes.data

```ceesafe
public fn data() -> u8
```

DATA (§6.1): body bytes.

### FrameTypes.headers

```ceesafe
public fn headers() -> u8
```

HEADERS (§6.2): opens a stream with a header block, or ends it with trailers.

### FrameTypes.priority

```ceesafe
public fn priority() -> u8
```

PRIORITY (§6.3): deprecated priority signals, read and ignored.

### FrameTypes.rstStream

```ceesafe
public fn rstStream() -> u8
```

RST_STREAM (§6.4): ends a stream at once, with an error code.

### FrameTypes.settings

```ceesafe
public fn settings() -> u8
```

SETTINGS (§6.5): the sender's parameters, or an acknowledgement.

### FrameTypes.pushPromise

```ceesafe
public fn pushPromise() -> u8
```

PUSH_PROMISE (§6.6): a stream the server will push, with its request's header block.

### FrameTypes.ping

```ceesafe
public fn ping() -> u8
```

PING (§6.7): eight bytes to echo back.

### FrameTypes.goAway

```ceesafe
public fn goAway() -> u8
```

GOAWAY (§6.8): the connection is ending; the last stream processed and an error code.

### FrameTypes.windowUpdate

```ceesafe
public fn windowUpdate() -> u8
```

WINDOW_UPDATE (§6.9): more room in a stream's or the connection's flow-control window.

### FrameTypes.continuation

```ceesafe
public fn continuation() -> u8
```

CONTINUATION (§6.10): the rest of a header block.

## struct FrameFlags

```ceesafe
public struct FrameFlags
```

Frame flags (§6): which apply depends on the frame type.

### FrameFlags.endStream

```ceesafe
public fn endStream() -> u8
```

DATA, HEADERS: the sender's last frame on the stream.

### FrameFlags.ack

```ceesafe
public fn ack() -> u8
```

SETTINGS, PING: an acknowledgement.

### FrameFlags.endHeaders

```ceesafe
public fn endHeaders() -> u8
```

HEADERS, PUSH_PROMISE, CONTINUATION: the header block ends here.

### FrameFlags.padded

```ceesafe
public fn padded() -> u8
```

DATA, HEADERS, PUSH_PROMISE: a pad length and padding are present.

### FrameFlags.priority

```ceesafe
public fn priority() -> u8
```

HEADERS: the (deprecated) priority fields are present.

## struct ErrorCodes

```ceesafe
public struct ErrorCodes
```

Error codes (§7), carried by RST_STREAM and GOAWAY.

### ErrorCodes.noError

```ceesafe
public fn noError() -> u32
```

NO_ERROR: a graceful end, not an error.

### ErrorCodes.protocolError

```ceesafe
public fn protocolError() -> u32
```

PROTOCOL_ERROR: a generic protocol violation.

### ErrorCodes.internalError

```ceesafe
public fn internalError() -> u32
```

INTERNAL_ERROR: the endpoint failed on its own.

### ErrorCodes.flowControlError

```ceesafe
public fn flowControlError() -> u32
```

FLOW_CONTROL_ERROR: the peer broke flow control.

### ErrorCodes.settingsTimeout

```ceesafe
public fn settingsTimeout() -> u32
```

SETTINGS_TIMEOUT: SETTINGS were not acknowledged in time.

### ErrorCodes.streamClosed

```ceesafe
public fn streamClosed() -> u32
```

STREAM_CLOSED: a frame came on a half-closed or closed stream.

### ErrorCodes.frameSizeError

```ceesafe
public fn frameSizeError() -> u32
```

FRAME_SIZE_ERROR: a frame of the wrong size.

### ErrorCodes.refusedStream

```ceesafe
public fn refusedStream() -> u32
```

REFUSED_STREAM: the stream was not processed, so the request may be retried.

### ErrorCodes.cancel

```ceesafe
public fn cancel() -> u32
```

CANCEL: the stream is no longer needed.

### ErrorCodes.compressionError

```ceesafe
public fn compressionError() -> u32
```

COMPRESSION_ERROR: the HPACK context cannot be kept.

### ErrorCodes.connectError

```ceesafe
public fn connectError() -> u32
```

CONNECT_ERROR: a CONNECT tunnel was reset or closed.

### ErrorCodes.enhanceYourCalm

```ceesafe
public fn enhanceYourCalm() -> u32
```

ENHANCE_YOUR_CALM: the peer is generating too much load (the flood guards).

### ErrorCodes.inadequateSecurity

```ceesafe
public fn inadequateSecurity() -> u32
```

INADEQUATE_SECURITY: the transport's security is below what HTTP/2 requires.

### ErrorCodes.http11Required

```ceesafe
public fn http11Required() -> u32
```

HTTP_1_1_REQUIRED: the request must be retried over HTTP/1.1.

## struct SettingIds

```ceesafe
public struct SettingIds
```

Setting identifiers (§6.5.2).

### SettingIds.headerTableSize

```ceesafe
public fn headerTableSize() -> u16
```

SETTINGS_HEADER_TABLE_SIZE: the largest HPACK dynamic table the sender's decoder allows.

### SettingIds.enablePush

```ceesafe
public fn enablePush() -> u16
```

SETTINGS_ENABLE_PUSH: 0 forbids server push.

### SettingIds.maxConcurrentStreams

```ceesafe
public fn maxConcurrentStreams() -> u16
```

SETTINGS_MAX_CONCURRENT_STREAMS: the most streams the sender lets the peer open.

### SettingIds.initialWindowSize

```ceesafe
public fn initialWindowSize() -> u16
```

SETTINGS_INITIAL_WINDOW_SIZE: each new stream's flow-control window.

### SettingIds.maxFrameSize

```ceesafe
public fn maxFrameSize() -> u16
```

SETTINGS_MAX_FRAME_SIZE: the largest frame payload the sender accepts.

### SettingIds.maxHeaderListSize

```ceesafe
public fn maxHeaderListSize() -> u16
```

SETTINGS_MAX_HEADER_LIST_SIZE: the largest header list the sender accepts.

## struct FrameHeader

```ceesafe
public struct FrameHeader {
    public length: u32;
    public kind: u8;
    public flags: u8;
    public stream: u32;
}
```

A frame's header (§4.1).

## struct Frames

```ceesafe
public struct Frames
```

Reading and writing frames.

### Frames.parseHeader

```ceesafe
public fn parseHeader(bytes: Slice<u8>) -> FrameHeader?
```

The header at the start of `bytes`, if all 9 bytes are there.

### Frames.writeHeader

```ceesafe
public fn writeHeader(
    out: borrow mut Vector<u8>,
    length: u32,
    kind: u8,
    flags: u8,
    stream: u32,
) -> Result<void, AllocError>
```

Appends a frame header; the reserved bit is sent as 0.

### Frames.writeSettings

```ceesafe
public fn writeSettings(
    out: borrow mut Vector<u8>,
    ids: Slice<u16>,
    values: Slice<u32>,
) -> Result<void, AllocError>
```

A SETTINGS frame with these (id, value) pairs, in order (§6.5).

### Frames.writeSettingsAck

```ceesafe
public fn writeSettingsAck(out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

An empty SETTINGS frame with ACK (§6.5.3).

### Frames.writePing

```ceesafe
public fn writePing(out: borrow mut Vector<u8>, data: [u8; 8], ack: bool) -> Result<void, AllocError>
```

A PING with these 8 bytes, or its acknowledgement (§6.7).

### Frames.writeGoAway

```ceesafe
public fn writeGoAway(
    out: borrow mut Vector<u8>,
    lastStream: u32,
    code: u32,
    debug: Slice<u8>,
) -> Result<void, AllocError>
```

GOAWAY with the last stream processed, an error code and debug data (§6.8).

### Frames.writeWindowUpdate

```ceesafe
public fn writeWindowUpdate(out: borrow mut Vector<u8>, stream: u32, increment: u32) -> Result<void, AllocError>
```

WINDOW_UPDATE for a stream, or the connection (stream 0) (§6.9).

### Frames.writeRstStream

```ceesafe
public fn writeRstStream(out: borrow mut Vector<u8>, stream: u32, code: u32) -> Result<void, AllocError>
```

RST_STREAM with an error code (§6.4).

### Frames.writeDataHeader

```ceesafe
public fn writeDataHeader(
    out: borrow mut Vector<u8>,
    stream: u32,
    length: u32,
    endStream: bool,
) -> Result<void, AllocError>
```

A DATA frame's header; the caller writes the payload (§6.1).

### Frames.writeHeaders

```ceesafe
public fn writeHeaders(
    out: borrow mut Vector<u8>,
    stream: u32,
    block: Slice<u8>,
    endStream: bool,
    maxFrame: u32,
) -> Result<void, AllocError>
```

A header block as HEADERS and as many CONTINUATIONs as `maxFrame` needs (§6.2, §6.10).

### Frames.writePushPromise

```ceesafe
public fn writePushPromise(
    out: borrow mut Vector<u8>,
    stream: u32,
    promised: u32,
    block: Slice<u8>,
    maxFrame: u32,
) -> Result<void, AllocError>
```

A PUSH_PROMISE for `promised` on `stream`, with its block split as `writeHeaders` does (§6.6).

## enum HpackError

```ceesafe
public enum HpackError {
    BadInteger,
    Truncated,
    BadHuffman,
    BadIndex,
    BadTableSizeUpdate,
    TooLarge,
    OutOfMemory,
}
```

Why a header block could not be decoded. Every one is a connection error, COMPRESSION_ERROR (RFC 9113 §4.3), except
`TooLarge`, after which the block was still decoded, so the dynamic table stays in step with the peer's.

## struct HpackDecoder

```ceesafe
@noncopy
public struct HpackDecoder
```

Decodes header blocks (§3), keeping the dynamic table between them.

### HpackDecoder.create

```ceesafe
public fn create(tableLimit: u32, maxListSize: u64) -> Result<HpackDecoder, AllocError>
```

A decoder whose dynamic table may grow to `tableLimit` bytes (what we announce as SETTINGS_HEADER_TABLE_SIZE),
for header lists up to `maxListSize` (SETTINGS_MAX_HEADER_LIST_SIZE).

### HpackDecoder.tableSize

```ceesafe
public fn tableSize(borrow this) -> u32
```

The dynamic table's size, as §4.1 counts it.

### HpackDecoder.tableCount

```ceesafe
public fn tableCount(borrow this) -> usize
```

How many entries the dynamic table holds.

### HpackDecoder.decode

```ceesafe
public fn decode(borrow mut this, block: Slice<u8>, out: borrow mut HeaderBlock) -> Result<void, HpackError>
```

Decodes one complete header block (the HEADERS or PUSH_PROMISE fragment and its CONTINUATIONs) into `out`
(cleared first).

## struct HpackEncoder

```ceesafe
@noncopy
public struct HpackEncoder
```

Encodes header fields (§6), keeping a dynamic table in step with the peer's decoder (Web ADR 0012 §3.10).

- A field found whole in the static or dynamic table is sent as an index (§6.1).
- Otherwise its value is sent as a literal, with the name's index if a table has the name, and the field is added to
  the dynamic table (§6.2.1) when it costs at most half the table, so one large field does not flush the rest.
- A `sensitive` field is never indexed (§6.2.3, §7.1.3), and its value is never looked up in the dynamic table.
- When the table's size changes (`setLimit`), the next block starts with dynamic table size updates (§4.2, §6.3):
  the smallest size since the last block, then the new size.

### HpackEncoder.create

```ceesafe
public fn create(cap: u32) -> Result<HpackEncoder, AllocError>
```

An encoder whose table starts at 4 096 bytes (SETTINGS_HEADER_TABLE_SIZE's initial value, RFC 9113 §6.5.2) and
never grows past `cap`. Below 4 096, the first block announces `cap`.

### HpackEncoder.withoutHuffman

```ceesafe
public fn withoutHuffman(this) -> HpackEncoder
```

The same encoder without Huffman coding: string literals are sent as they are (RFC 7541 Appendix C.3 and C.5).

### HpackEncoder.setLimit

```ceesafe
public fn setLimit(borrow mut this, peerLimit: u32)
```

The peer's SETTINGS_HEADER_TABLE_SIZE: the table may use up to this much (and up to `cap`) from the next block.

### HpackEncoder.tableSize

```ceesafe
public fn tableSize(borrow this) -> u32
```

The table's current size in bytes, as §4.1 counts it.

### HpackEncoder.tableLimit

```ceesafe
public fn tableLimit(borrow this) -> u32
```

The table's current maximum size.

### HpackEncoder.begin

```ceesafe
public fn begin(borrow mut this, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

Starts a block in `out`: the size updates owed since the last block, if any.

### HpackEncoder.encode

```ceesafe
public fn encode(
    borrow mut this,
    name: Slice<u8>,
    value: Slice<u8>,
    sensitive: bool,
    out: borrow mut Vector<u8>,
) -> Result<void, AllocError>
```

Appends one field to `out` (after `begin`).

### HpackEncoder.field

```ceesafe
public fn field(
    name: Slice<u8>,
    value: Slice<u8>,
    sensitive: bool,
    out: borrow mut Vector<u8>,
) -> Result<void, AllocError>
```

Appends one field to `out` without a table: an exact static match as an index; otherwise the value as a literal
(Huffman-coded unless longer), with the name's static index if it has one, without indexing, or never indexed if
`sensitive`. For tests and clients that keep no encoder state.

## fn writeInteger

```ceesafe
public fn writeInteger(out: borrow mut Vector<u8>, flags: u8, n: u32, value: u64) -> Result<void, AllocError>
```

Appends `value` with an `n`-bit prefix (§5.1) after the high bits `flags`.

## struct Huffman

```ceesafe
public struct Huffman
```

The static Huffman code for string literals.

### Huffman.encodedLength

```ceesafe
public fn encodedLength(bytes: Slice<u8>) -> usize
```

How many bytes `bytes` takes Huffman-coded.

### Huffman.encode

```ceesafe
public fn encode(bytes: Slice<u8>, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

Appends the Huffman coding of `bytes` to `out`, the last byte padded with the most significant bits of EOS
(all ones, §5.2).

### Huffman.decode

```ceesafe
public fn decode(bytes: Slice<u8>, out: borrow mut Vector<u8>) -> Result<bool, AllocError>
```

Appends the decoding of `bytes` to `out`: `Ok(false)` if they are not a valid coding (§5.2: EOS in the
string, or padding longer than 7 bits or not all ones).

## fn serveH2

```ceesafe
public async fn serveH2(
    conn: borrow mut dyn Transport,
    connection: borrow ConnectionContext,
    app: borrow Arc<Box<dyn Application + Send + Share>>,
    limits: Limits,
    already: Slice<u8>,
) -> Result<void, NetError>
```

Serves HTTP/2 on `conn` (after `already`, bytes read before it was known to be HTTP/2: the client preface by prior
knowledge) until the client goes, the connection fails, or the server drains. Each request's handler runs `app` in
its own task.

## struct H2Settings

```ceesafe
public struct H2Settings {
    public headerTableSize: u32;
    public enablePush: bool;
    public maxConcurrentStreams: u32;
    public initialWindowSize: u32;
    public maxFrameSize: u32;
    public maxHeaderListSize: u32;
}
```

One endpoint's settings.

### H2Settings.initial

```ceesafe
public fn initial() -> H2Settings
```

The values an endpoint assumes until its peer's SETTINGS arrive (§6.5.2): no limit is "unlimited".

### H2Settings.server

```ceesafe
public fn server(maxHeaderListSize: u32) -> H2Settings
```

What the server announces (Web ADR 0011 §3.3), with `maxHeaderListSize` from the server's limits.

### H2Settings.client

```ceesafe
public fn client(maxHeaderListSize: u32) -> H2Settings
```

What a client announces (Web ADR 0012 §7): no push, and `maxHeaderListSize` from the client's limits.

## struct RequestShape

```ceesafe
public struct RequestShape {
    public contentLength: u64?;
}
```

What a request's head says about its body.

## struct Requests

```ceesafe
public struct Requests
```

Request and trailer checks.

### Requests.checkRequest

```ceesafe
public fn checkRequest(head: borrow HeaderBlock) -> RequestShape?
```

Checks a request's head (§8.2, §8.3.1): field names in lower case with no forbidden byte; values without NUL,
CR, LF or surrounding whitespace; the pseudo-headers first, each at most once, only request ones; `:method`,
`:scheme` and a non-empty `:path` (only `:authority` with CONNECT); no connection-specific field; `te` only
"trailers"; one well-formed `content-length` at most. Null if malformed.

### Requests.checkTrailers

```ceesafe
public fn checkTrailers(head: borrow HeaderBlock) -> bool
```

Checks a trailer section (§8.1): no pseudo-header, and the same rules for names and values as a request's.

## struct ResponseShape

```ceesafe
public struct ResponseShape {
    public status: u16;
    public contentLength: u64?;
}
```

What a response's head says.

## struct Responses

```ceesafe
public struct Responses
```

Response checks (RFC 9113 §8.3.2), for the client role.

### Responses.checkResponse

```ceesafe
public fn checkResponse(head: borrow HeaderBlock) -> ResponseShape?
```

Checks a response's head: one `:status` of three digits, first, and no other pseudo-header; the same rules as a
request's for names, values and connection-specific fields; one well-formed `content-length` at most. Null if
malformed (§8.1.1).
