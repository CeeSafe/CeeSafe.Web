# web_ws

- [struct Base64](#struct-base64)
- [struct Deflater](#struct-deflater)
- [enum Opcode](#enum-opcode)
- [enum Role](#enum-role)
- [struct Opcodes](#struct-opcodes)
- [struct CloseCodes](#struct-closecodes)
- [struct FrameHead](#struct-framehead)
- [struct ParsedHead](#struct-parsedhead)
- [struct Frames](#struct-frames)
- [enum UpgradeRefusal](#enum-upgraderefusal)
- [enum HandshakeFailure](#enum-handshakefailure)
- [struct HandshakeAnswer](#struct-handshakeanswer)
- [struct Handshake](#struct-handshake)
- [struct DeflateAgreement](#struct-deflateagreement)
- [struct Extensions](#struct-extensions)
- [enum InflateError](#enum-inflateerror)
- [struct Inflater](#struct-inflater)
- [enum MessageKind](#enum-messagekind)
- [enum Incoming](#enum-incoming)
- [struct MessageReader](#struct-messagereader)
- [struct WebSocketOptions](#struct-websocketoptions)
- [enum Outbound](#enum-outbound)
- [enum Received](#enum-received)
- [struct WebSocketSender](#struct-websocketsender)
- [interface RawLink](#interface-rawlink)
- [struct WebSocket](#struct-websocket)
- [fn accept](#fn-accept)
- [fn open](#fn-open)
- [struct Sha1](#struct-sha1)
- [struct Utf8Validator](#struct-utf8validator)

## struct Base64

```ceesafe
public struct Base64
```

Base64 with the standard alphabet and padding (RFC 4648 §4).

### Base64.encode

```ceesafe
public fn encode(bytes: Slice<u8>, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

Appends the encoding of `bytes` to `out`.

### Base64.decode

```ceesafe
public fn decode(text: Slice<u8>, out: borrow mut Vector<u8>) -> Result<bool, AllocError>
```

Appends the bytes `text` encodes, and returns true, if it is canonical base64: a multiple of 4 characters from
the alphabet, with padding only at the end and zero bits under it (RFC 4648 §3.5). False otherwise; `out` may
then hold part of the bytes.

## struct Deflater

```ceesafe
public struct Deflater
```

permessage-deflate's compressor (RFC 7692): one fixed-Huffman block per message, without context takeover.

### Deflater.create

```ceesafe
public fn create(windowBits: u32) -> Deflater
```

A compressor whose back-references stay within 2^`windowBits` bytes (8-15).

### Deflater.compress

```ceesafe
public fn compress(borrow this, input: Slice<u8>, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

Appends `input`'s compressed form to `out`, without the final 00 00 FF FF (RFC 7692 §7.2.1).

## enum Opcode

```ceesafe
public enum Opcode {
    Continuation,
    Text,
    Binary,
    Close,
    Ping,
    Pong,
}
```

A frame's opcode (RFC 6455 §5.2).

## enum Role

```ceesafe
public enum Role {
    Server,
    Client,
}
```

Which end this is: a client masks what it sends, a server must not (§5.1).

## struct Opcodes

```ceesafe
public struct Opcodes
```

Opcodes on the wire.

### Opcodes.code

```ceesafe
public fn code(op: Opcode) -> u8
```

The opcode's 4-bit code.

### Opcodes.fromCode

```ceesafe
public fn fromCode(code: u8) -> Opcode?
```

The opcode, or null for the reserved ones (3-7, 11-15).

### Opcodes.isControl

```ceesafe
public fn isControl(op: Opcode) -> bool
```

Whether `op` is a control frame (Close, Ping, Pong), which may not be fragmented (§5.5).

## struct CloseCodes

```ceesafe
public struct CloseCodes
```

Close codes (§7.4.1) and which may appear on the wire (§7.4.2).

### CloseCodes.normal

```ceesafe
public fn normal() -> u16
```

1000: a normal close.

### CloseCodes.goingAway

```ceesafe
public fn goingAway() -> u16
```

1001: the endpoint is going away (a server shutting down, a page closed).

### CloseCodes.protocolError

```ceesafe
public fn protocolError() -> u16
```

1002: the peer broke the protocol.

### CloseCodes.unsupported

```ceesafe
public fn unsupported() -> u16
```

1003: a kind of data the endpoint cannot accept.

### CloseCodes.noStatus

```ceesafe
public fn noStatus() -> u16
```

Never sent: a Close without a code.

### CloseCodes.abnormal

```ceesafe
public fn abnormal() -> u16
```

Never sent: the connection ended without a Close.

### CloseCodes.invalidData

```ceesafe
public fn invalidData() -> u16
```

1007: data inconsistent with the message's type (text that is not UTF-8).

### CloseCodes.policy

```ceesafe
public fn policy() -> u16
```

1008: a message that violates the endpoint's policy.

### CloseCodes.tooBig

```ceesafe
public fn tooBig() -> u16
```

1009: a message too big to process.

### CloseCodes.extension

```ceesafe
public fn extension() -> u16
```

1010: the server did not agree to an extension the client needs.

### CloseCodes.internalError

```ceesafe
public fn internalError() -> u16
```

1011: an unexpected condition on the server (also an unanswered ping here).

### CloseCodes.mayBeSent

```ceesafe
public fn mayBeSent(code: u16) -> bool
```

Whether a Close frame may carry `code`: 1000-1003, 1007-1011 and 3000-4999.

## struct FrameHead

```ceesafe
public struct FrameHead {
    public fin: bool;
    public rsv1: bool;
    public opcode: Opcode;
    public masked: bool;
    public mask: [u8; 4];
    public length: u64;
}
```

A frame's head (RFC 6455 §5.2): the flags, the opcode, the mask and the payload's length.

## struct ParsedHead

```ceesafe
public struct ParsedHead {
    public head: FrameHead;
    public size: usize;
}
```

A frame head and how many bytes it took.

## struct Frames

```ceesafe
public struct Frames
```

Reading and writing frame heads, and masking payloads (§5.2-5.3).

### Frames.parseHead

```ceesafe
public fn parseHead(
    input: Slice<u8>,
    role: Role,
    maxFrameBytes: u64,
    rsv1Allowed: bool,
) -> Result<ParsedHead?, u16>
```

The frame head at the start of `input`: `Ok(null)` if more bytes are needed, `Err(code)` with the close code
for a frame that breaks §5: RSV bits not negotiated, a reserved opcode, the wrong masking for `role`, a
fragmented or long control frame, a 64-bit length with its top bit set (1002); a frame longer than
`maxFrameBytes` (1009).

### Frames.writeHead

```ceesafe
public fn writeHead(
    out: borrow mut Vector<u8>,
    fin: bool,
    rsv1: bool,
    opcode: Opcode,
    length: u64,
    mask: [u8; 4]?,
) -> Result<void, AllocError>
```

Appends a frame head to `out`, with the shortest length form; masked with `mask` if given (a client's frames).

### Frames.appendMasked

```ceesafe
public fn appendMasked(
    out: borrow mut Vector<u8>,
    payload: Slice<u8>,
    mask: [u8; 4],
    offset: u64,
) -> Result<void, AllocError>
```

Appends `payload` to `out`, masked with `mask` starting at the payload's byte `offset` (§5.3).

## enum UpgradeRefusal

```ceesafe
public enum UpgradeRefusal {
    BadRequest,
    Version,
    Origin,
}
```

Why an upgrade request is refused, and with which status.

## enum HandshakeFailure

```ceesafe
public enum HandshakeFailure {
    NotSwitched(u16),
    Upgrade,
    Accept,
    Extension,
    Subprotocol,
}
```

Why a client fails the connection after the server's answer (RFC 6455 §4.1, "the client MUST _Fail the WebSocket
Connection_").

## struct HandshakeAnswer

```ceesafe
public struct HandshakeAnswer {
    public deflate: DeflateAgreement?;
    public protocol: usize?;
}
```

What the server agreed to, once its answer has passed every check.

## struct Handshake

```ceesafe
public struct Handshake
```

The opening handshake's checks and computations (RFC 6455 §4).

### Handshake.acceptKey

```ceesafe
public fn acceptKey(key: Slice<u8>) -> Result<Vector<u8>, AllocError>
```

`Sec-WebSocket-Accept` for `key`: base64(SHA-1(key + the GUID)) (§4.2.2 step 5.4), 28 bytes.

### Handshake.checkAnswer

```ceesafe
public fn checkAnswer(
    status: u16,
    headers: borrow HeaderList,
    key: Slice<u8>,
    deflateOffered: bool,
    offered: borrow Vector<Vector<u8>>,
) -> Result<HandshakeAnswer, HandshakeFailure>
```

The client's check of the server's answer (§4.1): status 101, `Upgrade: websocket`, `Connection: upgrade`,
`Sec-WebSocket-Accept` matching `key` (the base64 text the client sent), extensions only as offered
(`deflateOffered`: permessage-deflate, once), and a subprotocol only from `offered`.

### Handshake.isUpgrade

```ceesafe
public fn isUpgrade(request: borrow Request) -> bool
```

Whether `request` is a WebSocket upgrade at all (`Upgrade: websocket`), before checking it fully.

### Handshake.check

```ceesafe
public fn check(
    request: borrow Request,
    secure: bool,
    allowedOrigins: borrow Vector<Vector<u8>>,
) -> Result<void, UpgradeRefusal>
```

Checks an upgrade request (§4.2.1): `GET` over HTTP/1.1 with a `Host`, `Upgrade: websocket`, `Connection:
upgrade`, a `Sec-WebSocket-Key` of 16 bytes in base64, `Sec-WebSocket-Version: 13`, and an allowed origin.
`secure` says whether the connection is TLS, for the origin's scheme.

### Handshake.originAllowed

```ceesafe
public fn originAllowed(
    origin: Slice<u8>,
    host: Slice<u8>,
    secure: bool,
    allowed: borrow Vector<Vector<u8>>,
) -> bool
```

Whether `origin` (`scheme://host[:port]`) is the server's own (`host` from the Host field, with `secure`'s
scheme; default ports ignored) or one of `allowed` (exact, ignoring ASCII case).

### Handshake.chooseSubprotocol

```ceesafe
public fn chooseSubprotocol(request: borrow Request, preferences: borrow Vector<Vector<u8>>) -> usize?
```

The index in `preferences` of the subprotocol to use: the first of the server's preferences that the client
listed in `Sec-WebSocket-Protocol` (any number of fields, comma-separated). Null if none.

## struct DeflateAgreement

```ceesafe
public struct DeflateAgreement {
    public serverNoContextTakeover: bool;
    public clientNoContextTakeover: bool;
    public serverMaxWindowBits: u32;
    public serverWindowNamed: bool;
    public clientMaxWindowBits: u32;
}
```

permessage-deflate as agreed (RFC 7692 §7.1).

## struct Extensions

```ceesafe
public struct Extensions
```

permessage-deflate's negotiation (RFC 7692 §7.1), for both ends.

### Extensions.negotiateDeflate

```ceesafe
public fn negotiateDeflate(request: borrow Request) -> DeflateAgreement?
```

The first permessage-deflate offer in `Sec-WebSocket-Extensions` (any number of fields) whose parameters are
all known, unrepeated and in range (§7.1: an offer that is not is declined, and the next one considered).

### Extensions.deflateOffer

```ceesafe
public fn deflateOffer(offer: Slice<u8>) -> DeflateAgreement?
```

One offer (`permessage-deflate; param; param=value`) if it is permessage-deflate and acceptable.

### Extensions.deflateAnswer

```ceesafe
public fn deflateAnswer(response: Slice<u8>) -> DeflateAgreement?
```

The client's side: a server's `Sec-WebSocket-Extensions` response to `clientOffer()`, the agreement if it is
permessage-deflate with acceptable parameters, else null and the client fails the connection (RFC 6455 §4.1
rule 5; RFC 7692 §7.1). `client_max_window_bits` must have a value in a response (§7.1.2.2).

### Extensions.clientOffer

```ceesafe
public fn clientOffer() -> StringView
```

The client's offer: permessage-deflate, letting the server choose our window (our inflater keeps 15 bits, so
any server window is fine).

### Extensions.deflateResponse

```ceesafe
public fn deflateResponse(agreed: borrow DeflateAgreement, out: borrow mut Vector<u8>) -> Result<void, AllocError>
```

The response's `Sec-WebSocket-Extensions` value for `agreed`.

## enum InflateError

```ceesafe
public enum InflateError {
    Corrupt,
    TooBig,
    OutOfMemory,
}
```

Why inflating failed.

## struct Inflater

```ceesafe
public struct Inflater
```

The inflater of one direction of a connection.

### Inflater.create

```ceesafe
public fn create(windowBits: u32, takeover: bool) -> Inflater
```

An inflater with a window of 2^`windowBits` bytes (8-15); `takeover` keeps it across messages.

### Inflater.inflate

```ceesafe
public fn inflate(
    borrow mut this,
    input: Slice<u8>,
    out: borrow mut Vector<u8>,
    limit: u64,
) -> Result<void, InflateError>
```

Inflates one message's data (`input`, with the 00 00 FF FF tail already appended) onto `out`, at most `limit`
bytes in all.

## enum MessageKind

```ceesafe
public enum MessageKind {
    Text,
    Binary,
}
```

A data message's type.

## enum Incoming

```ceesafe
public enum Incoming {
    NeedMore,
    Message(MessageKind),
    Ping,
    Pong,
    Close(u16),
}
```

What `MessageReader.next` found in the input.

## struct MessageReader

```ceesafe
public struct MessageReader
```

Assembles frames into messages and checks them (fragments, control frames, limits, UTF-8, close codes), without I/O.

### MessageReader.create

```ceesafe
public fn create(role: Role, maxFrameBytes: u64, maxMessageBytes: u64) -> MessageReader
```

A reader for this end's `role` (which masking it expects), with the frame and message limits (1009 past them).

### MessageReader.enableDeflate

```ceesafe
public fn enableDeflate(borrow mut this, takeover: bool)
```

Accepts compressed messages from now on (permessage-deflate agreed); `takeover` keeps the window across
messages (the client did not agree to `client_no_context_takeover`).

### MessageReader.control

```ceesafe
public fn control(borrow this) -> Slice<u8>
```

The last control frame's payload.

### MessageReader.closeReason

```ceesafe
public fn closeReason(borrow this) -> Slice<u8>
```

The last Close's reason (UTF-8, already checked).

### MessageReader.inMessage

```ceesafe
public fn inMessage(borrow this) -> bool
```

Whether a message has begun and not ended.

### MessageReader.next

```ceesafe
public fn next(
    borrow mut this,
    input: borrow mut OwnedBuffer,
    message: borrow mut Vector<u8>,
) -> Result<Incoming, u16>
```

Consumes frames from `input` until a message, a control frame, or the end of the bytes. A data frame's payload
is appended to `message`, which is cleared when a message begins. `Err(code)` for a violation: the close code
to send (1002 protocol, 1007 bad UTF-8, 1009 too big, 1011 out of memory).

## struct WebSocketOptions

```ceesafe
public struct WebSocketOptions {
    public maxFrameBytes: u64;
    public maxMessageBytes: u64;
    public fragmentBytes: usize;
    public pingIntervalMs: u64;
    public pongTimeoutMs: u64;
    public closeTimeoutMs: u64;
    public queueLength: usize;
    public subprotocols: Vector<Vector<u8>>;
    public allowedOrigins: Vector<Vector<u8>>;
    public deflate: bool;
}
```

How a WebSocket session behaves: limits, keep-alive, the queue, subprotocols, origins and compression.

### WebSocketOptions.defaults

```ceesafe
public fn defaults() -> WebSocketOptions
```

1 MiB frames, 16 MiB messages, 64 KiB fragments, a ping after 30 s quiet, 30 s for its pong, 5 s for the
close, a queue of 64, and permessage-deflate on.

## enum Outbound

```ceesafe
public enum Outbound {
    Text(Vector<u8>),
    Binary(Vector<u8>),
    Close(u16),
}
```

A message another task queued for the session to send.

## enum Received

```ceesafe
public enum Received {
    Message(MessageKind),
    Closed(u16),
}
```

What `receive` gives.

## struct WebSocketSender

```ceesafe
public struct WebSocketSender
```

A handle for other tasks to send through the session (`Send`): messages wait in a bounded queue, and the
session's task writes them.

### WebSocketSender.send

```ceesafe
public async fn send(borrow this, message: Outbound) -> Result<void, NetError>
```

Queues `message`, waiting for room. `Err(Closed)` once the session has ended.

### WebSocketSender.trySend

```ceesafe
public fn trySend(borrow this, message: Outbound) -> bool
```

Queues `message` if there is room: false when the queue is full or the session has ended.

### WebSocketSender.share

```ceesafe
public fn share(borrow this) -> WebSocketSender
```

Another handle to the same queue.

## interface RawLink

```ceesafe
public interface RawLink {
    async fn readSome(borrow mut this, into: SliceMut<u8>) -> Result<usize, HandlerError>;
    async fn writeAll(borrow mut this, bytes: Slice<u8>) -> Result<void, HandlerError>;
}
```

The bytes under a session: an upgraded exchange (the server's), or the client's own connection. The session is
generic over it, since an exchange is a scoped type, which implements an interface through a bound but not as
`dyn` (CeeSafe ADR 0054 §3.2).

## struct WebSocket

```ceesafe
public struct WebSocket<region r, L: RawLink>
```

A WebSocket session over `L`: an upgraded exchange (`accept`), or a client's connection (web_client's
`clientSession`).

### WebSocket.compressing

```ceesafe
public fn compressing(borrow this) -> bool
```

Whether permessage-deflate was agreed.

### WebSocket.subprotocol

```ceesafe
public fn subprotocol(borrow this) -> usize?
```

The index in `options.subprotocols` of the subprotocol agreed, if any.

### WebSocket.sender

```ceesafe
public fn sender(borrow this) -> WebSocketSender
```

A handle for other tasks to queue messages.

### WebSocket.receive

```ceesafe
public async fn receive(borrow mut this, into: borrow mut Vector<u8>) -> Result<Received, HandlerError>
```

The next message from the peer, into `into` (cleared first). Pings are answered, a Close is answered and
ends the session, and a violation sends the matching Close and ends it. While no frame is complete, queued
messages are sent and the keep-alive runs. After `Closed`, every call returns `Closed` again.

### WebSocket.send

```ceesafe
public async fn send(borrow mut this, kind: MessageKind, bytes: Slice<u8>) -> Result<void, HandlerError>
```

Sends a whole message: one frame, or fragments past `fragmentBytes`. `Err(Io(Closed))` once a Close was sent.

### WebSocket.close

```ceesafe
public async fn close(borrow mut this, code: u16) -> Result<void, HandlerError>
```

Starts the close handshake with `code` (§7.4; 1000 for a normal end). Keep calling `receive` until it
returns `Closed`: the peer's Close, or `closeTimeoutMs`.

## fn accept

```ceesafe
public async fn accept<region r>(
    exchange: borrow mut(r) Exchange<r>,
    request: borrow Request,
    options: borrow WebSocketOptions,
    secure: bool,
) -> Result<WebSocket<r, Exchange<r>>?, HandlerError>
```

Answers an upgrade request: checks it (Web ADR 0010 §3.1, §3.4), agrees the subprotocol, switches the exchange,
and returns the session. A request that is refused gets its response here (400, 403, or 426 with
`Sec-WebSocket-Version: 13`), and the result is null. `secure` says whether the connection is TLS (for the
origin's scheme).

## fn open

```ceesafe
public fn open<region r, L: RawLink>(
    link: borrow mut(r) L,
    role: Role,
    options: borrow WebSocketOptions,
    deflate: DeflateAgreement?,
    protocol: usize?,
) -> Result<WebSocket<r, L>, HandlerError>
```

A session over `link`, after a handshake that agreed `deflate` (if any) and the subprotocol at `protocol` (an index
into the offer's or the preferences' list). `role` is this end's: a client masks what it sends and refuses masked
frames; a server the reverse. web_client's `WebSocketClient` calls it after checking the server's 101.

## struct Sha1

```ceesafe
public struct Sha1
```

A SHA-1 computation: `update` with the input, then `finish`.

### Sha1.create

```ceesafe
public fn create() -> Sha1
```

An empty hash.

### Sha1.update

```ceesafe
public fn update(borrow mut this, bytes: Slice<u8>)
```

Adds `bytes` to the hash.

### Sha1.finish

```ceesafe
public fn finish(borrow mut this) -> [u8; 20]
```

The digest. The computation is spent afterwards.

## struct Utf8Validator

```ceesafe
public struct Utf8Validator
```

An incremental UTF-8 check (RFC 3629) that fails as soon as no valid completion exists.

### Utf8Validator.create

```ceesafe
public fn create() -> Utf8Validator
```

A validator at the start of a sequence.

### Utf8Validator.reset

```ceesafe
public fn reset(borrow mut this)
```

Starts again, for the next message.

### Utf8Validator.feed

```ceesafe
public fn feed(borrow mut this, bytes: Slice<u8>) -> bool
```

Feeds `bytes`; false once the input cannot be UTF-8, whatever follows.

### Utf8Validator.step

```ceesafe
public fn step(borrow mut this, b: u8) -> bool
```

Feeds one byte; false if the input cannot be UTF-8.

### Utf8Validator.complete

```ceesafe
public fn complete(borrow this) -> bool
```

Whether the input so far ends at a character boundary.
