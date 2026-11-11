# web_http

- [struct Ascii](#struct-ascii)
- [fn trimWhitespace](#fn-trimwhitespace)
- [struct ChunkStep](#struct-chunkstep)
- [struct ChunkDecoder](#struct-chunkdecoder)
- [struct HttpDate](#struct-httpdate)
- [struct DateCache](#struct-datecache)
- [enum ResponseFraming](#enum-responseframing)
- [struct Encoder](#struct-encoder)
- [enum ProtocolError](#enum-protocolerror)
- [struct ProtocolErrors](#struct-protocolerrors)
- [enum HandlerError](#enum-handlererror)
- [interface Application](#interface-application)
- [struct Exchange](#struct-exchange)
- [struct FieldSpan](#struct-fieldspan)
- [struct HeaderBlock](#struct-headerblock)
- [enum HeaderName](#enum-headername)
- [struct HeaderNames](#struct-headernames)
- [struct HeaderList](#struct-headerlist)
- [struct Limits](#struct-limits)
- [enum Method](#enum-method)
- [struct Methods](#struct-methods)
- [struct Request](#struct-request)
- [enum BodyFraming](#enum-bodyframing)
- [enum FieldError](#enum-fielderror)
- [struct ResponseHead](#struct-responsehead)
- [enum Version](#enum-version)
- [struct StatusCode](#struct-statuscode)
- [struct Status](#struct-status)
- [interface StreamSink](#interface-streamsink)
- [struct NoStream](#struct-nostream)
- [struct NoTransport](#struct-notransport)
- [enum UrlError](#enum-urlerror)
- [struct Url](#struct-url)

## struct Ascii

```ceesafe
public struct Ascii
```

Byte classes and comparisons over ASCII.

### Ascii.equals

```ceesafe
public fn equals(a: Slice<u8>, b: StringView) -> bool
```

Whether `a` is exactly `b`'s bytes.

### Ascii.equalsIgnoringCase

```ceesafe
public fn equalsIgnoringCase(a: Slice<u8>, lower: StringView) -> bool
```

Whether `a` equals `lower`, ignoring ASCII case; `lower` must be lower case.

### Ascii.sameIgnoringCase

```ceesafe
public fn sameIgnoringCase(a: Slice<u8>, b: Slice<u8>) -> bool
```

Whether two byte strings are equal ignoring ASCII case.

### Ascii.lower

```ceesafe
public fn lower(b: u8) -> u8
```

`b` in lower case if it is an ASCII letter.

### Ascii.isTokenChar

```ceesafe
public fn isTokenChar(b: u8) -> bool
```

tchar (RFC 9110 §5.6.2): the bytes of a token.

### Ascii.isToken

```ceesafe
public fn isToken(s: Slice<u8>) -> bool
```

Whether every byte of `s` is a tchar, and there is at least one.

### Ascii.isFieldValueChar

```ceesafe
public fn isFieldValueChar(b: u8) -> bool
```

field-vchar / obs-text, or SP or HTAB (RFC 9110 §5.5): the bytes allowed inside a field value. NUL, CR, LF and
the other controls are not.

### Ascii.isWhitespace

```ceesafe
public fn isWhitespace(b: u8) -> bool
```

SP or HTAB.

### Ascii.parseDecimal

```ceesafe
public fn parseDecimal(s: Slice<u8>) -> u64?
```

A decimal number of 1 to 19 digits with nothing else, or null (no sign, no whitespace, no overflow).

## fn trimWhitespace

```ceesafe
public fn trimWhitespace<region r>(s: Slice<r, u8>) -> Slice<r, u8>
```

`s` without leading and trailing SP and HTAB (optional whitespace, RFC 9110 §5.6.3).

## struct ChunkStep

```ceesafe
public struct ChunkStep {
    public consumed: usize;
    public dataStart: usize;
    public dataLength: usize;
    public finished: bool;
    public trailerStart: usize;
    public trailerLength: usize;
}
```

What one `ChunkDecoder.decode` call found.

## struct ChunkDecoder

```ceesafe
public struct ChunkDecoder
```

Decodes one chunked body.

### ChunkDecoder.create

```ceesafe
public fn create(limits: borrow Limits) -> ChunkDecoder
```

A decoder at the start of a body, with `limits`' chunk, trailer and body limits.

### ChunkDecoder.isFinished

```ceesafe
public fn isFinished(borrow this) -> bool
```

Whether the whole body has been decoded.

### ChunkDecoder.bodyBytes

```ceesafe
public fn bodyBytes(borrow this) -> u64
```

The body bytes decoded so far.

### ChunkDecoder.decode

```ceesafe
public fn decode(borrow mut this, input: Slice<u8>, room: usize) -> Result<ChunkStep, ProtocolError>
```

Decodes from the start of `input` until it finds data (returned at once, at most `room` bytes of it),
finishes the body, or uses all of `input`. Call again with the bytes after `consumed`, and more as they
arrive. A `room` of 0 decodes framing only and stops at data.

## struct HttpDate

```ceesafe
public struct HttpDate
```

Formatting IMF-fixdate.

### HttpDate.format

```ceesafe
public fn format(unixSeconds: u64, out: borrow mut [u8; 29])
```

The 29-byte IMF-fixdate of `unixSeconds` into `out`, e.g. `Sun, 06 Nov 1994 08:49:37 GMT`.

### HttpDate.parse

```ceesafe
public fn parse(text: Slice<u8>, nowSeconds: u64) -> u64?
```

The Unix seconds of an HTTP date in any of RFC 9110 §5.6.7's three forms, or null if it is none of them or is
before 1970:
- IMF-fixdate, `Sun, 06 Nov 1994 08:49:37 GMT`;
- RFC 850, `Sunday, 06-Nov-94 08:49:37 GMT`, whose two-digit year is the one in the century that is not more than
  50 years after `nowSeconds` (RFC 9110 §5.6.7);
- asctime, `Sun Nov  6 08:49:37 1994`.
The weekday must be a day's name, and is not checked against the date. A second of 60 (a leap second) is taken
as 59.

## struct DateCache

```ceesafe
public struct DateCache
```

The `Date` field's value for a connection's responses, formatted again only when the second changes.

### DateCache.create

```ceesafe
public fn create(enabled: bool) -> DateCache
```

A cache that answers nothing unless `enabled`.

### DateCache.refresh

```ceesafe
public fn refresh(borrow mut this)
```

Reads the wall clock (`Clock.now()`).

### DateCache.at

```ceesafe
public fn at(borrow mut this, unixSeconds: i64)
```

Sets the time to `unixSeconds`; a time before 1970 is ignored.

### DateCache.bytes

```ceesafe
public fn bytes(borrow this) -> Slice<u8>?
```

The 29-byte IMF-fixdate, or null when disabled or not yet set.

## enum ResponseFraming

```ceesafe
public enum ResponseFraming {
    NoContent,
    Length(u64),
    Chunked,
    UntilClose,
}
```

How a response's content is delimited on the wire.

## struct Encoder

```ceesafe
public struct Encoder
```

Response encoding.

### Encoder.head

```ceesafe
public fn head(
    head: borrow ResponseHead,
    framing: ResponseFraming,
    keepAlive: bool,
    date: Slice<u8>?,
    out: borrow mut OwnedBuffer,
) -> Result<void, NetError>
```

Writes the status line, `Date` (if `date` is given and the head has none), the head's fields, the framing field and, unless
`keepAlive`, `Connection: close`, then the empty line. `Err(LimitExceeded)` if `out` has no room; nothing
partial is left in it then.

### Encoder.chunkHeader

```ceesafe
public fn chunkHeader(n: usize, out: borrow mut OwnedBuffer) -> Result<void, NetError>
```

A chunk's size line, `hex\r\n`, before `n` bytes of data (n > 0); the data is followed by `chunkEnd`.

### Encoder.chunkEnd

```ceesafe
public fn chunkEnd(out: borrow mut OwnedBuffer) -> Result<void, NetError>
```

The CRLF after a chunk's data.

### Encoder.lastChunk

```ceesafe
public fn lastChunk(out: borrow mut OwnedBuffer) -> Result<void, NetError>
```

The last chunk and an empty trailer section: `0\r\n\r\n`.

### Encoder.framing

```ceesafe
public fn framing(status: StatusCode, method: Method, version: Version, length: u64?) -> ResponseFraming
```

How a response is framed: no content for 1xx, 204 and 304; for HEAD, the length if known; otherwise the
length if known, else chunked for HTTP/1.1 and until-close for HTTP/1.0 (RFC 9112 §6.3, §7).

## enum ProtocolError

```ceesafe
public enum ProtocolError {
    RequestLineTooLong,
    BadRequestLine,
    BadStatusLine,
    UnsupportedVersion,
    HeadTooLarge,
    TooManyHeaders,
    BadHeader,
    BareLineFeed,
    ObsoleteFold,
    BadHost,
    BadContentLength,
    LengthAndEncoding,
    BadTransferEncoding,
    BadChunk,
    BadTrailer,
    BodyTooLarge,
    TimedOut,
}
```

Why a request was rejected by the protocol, before or while its handler ran.

## struct ProtocolErrors

```ceesafe
public struct ProtocolErrors
```

Mapping protocol errors to responses.

### ProtocolErrors.status

```ceesafe
public fn status(e: ProtocolError) -> StatusCode
```

The response status for `e`.

### ProtocolErrors.describe

```ceesafe
public fn describe(e: ProtocolError) -> StringView
```

A short static description, for logs.

## enum HandlerError

```ceesafe
public enum HandlerError {
    Status(StatusCode),
    Io(NetError),
    Internal,
}
```

How a handler failed (Web ADR 0007 §3.1).

## interface Application

```ceesafe
public interface Application {
    async fn handle(borrow this, request: borrow Request, exchange: borrow mut Exchange, connection: borrow ConnectionContext,) -> Result<void, HandlerError>;
    async fn beforeHead(borrow this, request: borrow Request, head: borrow mut ResponseHead) -> Result<void, HandlerError>;
}
```

What a connection's driver runs for each request: `web_server`'s server, or a test. Not generic, so a driver
needs no type parameter.

## struct Exchange

```ceesafe
public struct Exchange<region r>
```

One request's body and response, over its connection.

### Exchange.create

```ceesafe
public fn create(
    conn: borrow mut(r) dyn Transport,
    stream: borrow mut(r) dyn StreamSink,
    carry: borrow mut(r) OwnedBuffer,
    out: borrow mut(r) OwnedBuffer,
    pending: borrow mut(r) OwnedBuffer,
    response: borrow mut(r) ResponseHead,
    app: borrow(r) dyn Application,
    request: borrow(r) Request<r>,
    limits: borrow(r) Limits,
    date: borrow mut(r) DateCache,
    framing: BodyFraming,
    expectContinue: bool,
    keepAlive: bool,
) -> Exchange<r>
```

An exchange for `request`, whose body is framed by `framing`. `carry` holds the bytes received after the head;
`out` the responses not yet sent; `pending` and `response` are reset here. `keepAlive` is the request's own
verdict (version and Connection).

### Exchange.forStream

```ceesafe
public fn forStream(
    stream: borrow mut(r) dyn StreamSink,
    conn: borrow mut(r) dyn Transport,
    carry: borrow mut(r) OwnedBuffer,
    out: borrow mut(r) OwnedBuffer,
    pending: borrow mut(r) OwnedBuffer,
    response: borrow mut(r) ResponseHead,
    app: borrow(r) dyn Application,
    request: borrow(r) Request<r>,
    limits: borrow(r) Limits,
    date: borrow mut(r) DateCache,
    hasBody: bool,
) -> Exchange<r>
```

An exchange for a request that came on a multiplexed stream (Web ADR 0011 §3.2): its body is read and its
response written through `stream`. `conn` is a `NoTransport`, and `carry` and `out` are unused; `pending`
collects the body before the head is sent, as over HTTP/1.1. `hasBody` is false when the request ended with
its head (END_STREAM).

### Exchange.read

```ceesafe
public async fn read(borrow mut this, buffer: SliceMut<u8>) -> Result<usize, HandlerError>
```

Reads body bytes into `buffer`: at least one, or 0 at the end of the body. The first read sends `100
Continue` if the client asked for it (RFC 9110 §10.1.1). A malformed or too large body is
`Status(400 or 413)`, a client too slow `Status(408)`; either closes the connection afterwards.

### Exchange.trailers

```ceesafe
public fn trailers(borrow this) -> borrow HeaderBlock
```

The request's trailer fields (RFC 9110 §6.5): empty until `read` has returned 0, and empty if the request had
none. Over HTTP/1.1 they come from a chunked body's trailer section; over HTTP/2 from the HEADERS that ended the
stream (RFC 9113 §8.1). Names are lowercase.

### Exchange.trailer

```ceesafe
public fn trailer(borrow mut this, name: StringView, value: Slice<u8>) -> Result<void, HandlerError>
```

Adds a trailer field to the response (RFC 9110 §6.5), sent after its content: over HTTP/2 as HEADERS ending the
stream, over HTTP/1.1 in the last chunk of a chunked response. A response with a declared length over HTTP/1.1
has nowhere to put trailers (RFC 9112 §7.1.2), so they are dropped, as for a response without content. A name
that is not a token, a value with a control byte, and the fields that frame or route a message
(`content-length`, `transfer-encoding`, `host`, `connection`, `te`, `trailer`) are `Internal`.

### Exchange.readToEnd

```ceesafe
public async fn readToEnd(borrow mut this, into: borrow mut OwnedBuffer) -> Result<void, HandlerError>
```

Reads the rest of the body into `into`. `Status(413)` if it would exceed `into`'s room.

### Exchange.peerClosed

```ceesafe
public async fn peerClosed(borrow mut this) -> Result<void, HandlerError>
```

Returns when the client has closed or reset the connection; never while it is open and quiet, so it belongs
inside a `race` (Web ADR 0008 §3.1). The body must be read first (`Internal` otherwise). Bytes the client
sends meanwhile are kept for the driver; once that buffer is full this waits until it is cancelled, and a
failed write finds the close instead. Cancelling it loses nothing.

### Exchange.remaining

```ceesafe
public fn remaining(borrow this) -> u64?
```

The body bytes not yet read, if the body has a length (over HTTP/2, a declared `content-length`); 0 once it is
read; null for a chunked body or an HTTP/2 body without a declared length.

### Exchange.setStatus

```ceesafe
public fn setStatus(borrow mut this, status: StatusCode)
```

Sets the status; ignored once the head was sent.

### Exchange.header

```ceesafe
public fn header(borrow mut this, name: StringView, value: Slice<u8>) -> Result<void, HandlerError>
```

Adds a response field. `Internal` for a bad name or value, a framing field, or no room; after the head was
sent, `Internal` too.

### Exchange.setContentLength

```ceesafe
public fn setContentLength(borrow mut this, n: u64)
```

Declares the body's length, so it is sent with Content-Length even when it outgrows the buffer.

### Exchange.closeConnection

```ceesafe
public fn closeConnection(borrow mut this)
```

Asks for the connection to close after this response.

### Exchange.headSent

```ceesafe
public fn headSent(borrow this) -> bool
```

Whether the head has been sent (or queued to send).

### Exchange.write

```ceesafe
public async fn write(borrow mut this, bytes: Slice<u8>) -> Result<void, HandlerError>
```

Writes body bytes. They wait in the response buffer; a full buffer sends the head and the bytes so far. Over
HTTP/1.1, what is left of a write at least as large as the buffer, once the buffer is empty, goes to the wire
as it is, without a copy.

### Exchange.sendFile

```ceesafe
public async fn sendFile(
    borrow mut this,
    file: borrow mut File,
    offset: u64,
    length: u64,
) -> Result<void, HandlerError>
```

Writes `length` bytes of `file` from `offset` as body, after what was written before (Web ADR 0013 §3.3): the head
first if it was not sent, and the declared length checked, as `write`. Over HTTP/1.1 the transport sends the
file itself (a plain stream: `sendfile`, nothing copied); over HTTP/2 it goes as DATA in pieces of 16 KiB. It
goes in slices of 1 MiB, each within the write timeout, so a client must take 1 MiB per `writeTimeoutMs`; a
chunked response sends each slice as a chunk. A file that ends before `length` fails with `Io(Io(InvalidInput))`
and the response is never completed: the HTTP/1.1 connection closes without the last chunk, and an HTTP/2
stream is reset, so a client sees a transfer cut short, never a short success.

### Exchange.flush

```ceesafe
public async fn flush(borrow mut this) -> Result<void, HandlerError>
```

Sends everything written so far now: the head, and the body as it stands (a streaming response). The rest
of the response follows chunked unless a length was declared.

### Exchange.send

```ceesafe
public async fn send(borrow mut this, status: StatusCode, body: Slice<u8>) -> Result<void, HandlerError>
```

Sets `status`, the length of `body`, writes it: a whole small response in one call.

### Exchange.switchProtocols

```ceesafe
public async fn switchProtocols(borrow mut this, protocol: StringView) -> Result<void, HandlerError>
```

Answers 101 Switching Protocols to `protocol` (a token, such as `websocket`) with the fields the handler added,
and sends it now. Afterwards the connection belongs to the handler, through `readRaw` and `writeRaw`, and the
server closes it when the handler returns. `Internal` if anything was sent already, if the request has a body,
or if it is not HTTP/1.1.

### Exchange.readRaw

```ceesafe
public async fn readRaw(borrow mut this, buffer: SliceMut<u8>) -> Result<usize, HandlerError>
```

After `switchProtocols`: bytes from the client, those received after the request's head first. 0 at the end
of the stream. Cancelling it loses nothing (Web ADR 0001 §3.4).

### Exchange.writeRaw

```ceesafe
public async fn writeRaw(borrow mut this, bytes: Slice<u8>) -> Result<void, HandlerError>
```

After `switchProtocols`: writes `bytes` to the client, within `Limits.writeTimeoutMs`. A failed or cancelled
write leaves the connection unusable (Web ADR 0001 §3.4).

### Exchange.shutdownRaw

```ceesafe
public async fn shutdownRaw(borrow mut this) -> Result<void, HandlerError>
```

After `switchProtocols`: ends this side's writing to the client (TCP FIN, or TLS close_notify then FIN), which
may still send; a tunnel does this when its other side has finished sending (Web ADR 0012 §3.8).

### Exchange.push

```ceesafe
public async fn push(borrow mut this, path: StringView) -> Result<bool, HandlerError>
```

Pushes a response to a GET for `path` (RFC 9113 §8.4; Web ADR 0011 §3.2): the client is promised it, and the
application handles that request on its own stream as if the client had asked. False over HTTP/1.1, when the
client refuses pushes, or when the connection cannot make one now.

### Exchange.switched

```ceesafe
public fn switched(borrow this) -> bool
```

Whether the connection was switched to another protocol.

### Exchange.finish

```ceesafe
public async fn finish(borrow mut this) -> Result<void, HandlerError>
```

Ends the response after the handler returned `Ok`: a response that was never sent goes out whole with an
exact Content-Length; a chunked one gets its last chunk. A response shorter than its declared length closes
the connection. Streaming responses are flushed now.

### Exchange.respondError

```ceesafe
public async fn respondError(borrow mut this, status: StatusCode, close: bool) -> bool
```

Replaces an unsent response with an error response of `status` and no content, closing the connection if
`close`. False if a head was already sent: the connection must close instead.

### Exchange.finishBody

```ceesafe
public async fn finishBody(borrow mut this)
```

After the response: reads and discards what the handler left of the body, up to `Limits.drainBytes`, so the
next request can be read; past that, or on an error, the connection closes instead (Web ADR 0003 §3.3).

### Exchange.keepAlive

```ceesafe
public fn keepAlive(borrow this) -> bool
```

Whether the connection may carry another request.

### Exchange.streamed

```ceesafe
public fn streamed(borrow this) -> bool
```

Whether this response was streamed (flushed by the handler): the driver sends it before reading on.

## struct FieldSpan

```ceesafe
public struct FieldSpan {
    public nameAt: u32;
    public nameLength: u32;
    public valueAt: u32;
    public valueLength: u32;
    public sensitive: bool;
}
```

Where one field's name and value are in a `HeaderBlock`'s bytes.

## struct HeaderBlock

```ceesafe
@noncopy
public struct HeaderBlock
```

A decoded header list: the fields in order, their bytes in one buffer.

### HeaderBlock.create

```ceesafe
public fn create() -> HeaderBlock
```

An empty list.

### HeaderBlock.clear

```ceesafe
public fn clear(borrow mut this)
```

Empties the list, keeping its memory.

### HeaderBlock.count

```ceesafe
public fn count(borrow this) -> usize
```

How many fields.

### HeaderBlock.name

```ceesafe
public fn name(borrow this, i: usize) -> Slice<u8>
```

The `i`th field's name.

### HeaderBlock.value

```ceesafe
public fn value(borrow this, i: usize) -> Slice<u8>
```

The `i`th field's value.

### HeaderBlock.span

```ceesafe
public fn span(borrow this, i: usize) -> FieldSpan
```

The `i`th field's span.

### HeaderBlock.size

```ceesafe
public fn size(borrow this) -> u64
```

The list's size as RFC 9113 §6.5.2 counts it: each field's name and value, plus 32.

### HeaderBlock.allBytes

```ceesafe
public fn allBytes(borrow this) -> Slice<u8>
```

All the names and values, which the spans index.

### HeaderBlock.sensitiveByDefault

```ceesafe
public fn sensitiveByDefault(name: Slice<u8>) -> bool
```

Whether a field named `name` (lowercase) is sent "never indexed" unless its sender says otherwise: credentials
and cookies, whose values an attacker who shares the connection could otherwise probe through the
compression context (RFC 7541 §7.1.3; Web ADR 0012 §3.10).

### HeaderBlock.pushLines

```ceesafe
public fn pushLines(borrow mut this, lines: Slice<u8>) -> Result<void, AllocError>
```

Appends the fields of an HTTP/1.1 field section, `name:value` lines each ending in CR LF, as the chunked
decoder checked them (RFC 9112 §7.1.2): names lowercased, values without their leading and trailing whitespace
(RFC 9110 §5.5).

### HeaderBlock.pushAll

```ceesafe
public fn pushAll(borrow mut this, other: borrow HeaderBlock) -> Result<void, AllocError>
```

Appends a copy of every field of `other`.

### HeaderBlock.push

```ceesafe
public fn push(borrow mut this, name: Slice<u8>, value: Slice<u8>, sensitive: bool) -> Result<void, AllocError>
```

Appends a field.

## enum HeaderName

```ceesafe
public enum HeaderName {
    Host,
    ContentLength,
    TransferEncoding,
    Connection,
    Expect,
    Te,
    Trailer,
    Upgrade,
    KeepAlive,
    Date,
    ContentType,
    Accept,
    AcceptEncoding,
    UserAgent,
    Cookie,
    Authorization,
    Origin,
    Other,
}
```

The field names a server or its middleware looks at, recognised without regard to case; any other is `Other`.

## struct HeaderNames

```ceesafe
public struct HeaderNames
```

Recognising and naming fields.

### HeaderNames.classify

```ceesafe
public fn classify(bytes: Slice<u8>) -> HeaderName
```

The name for `bytes`, without regard to case.

### HeaderNames.text

```ceesafe
public fn text(n: HeaderName) -> StringView
```

The name in lower case, as HTTP/2 and HTTP/3 write it; "" for `Other`.

### HeaderNames.same

```ceesafe
public fn same(a: HeaderName, b: HeaderName) -> bool
```

Whether two names are the same (an enum has no `==`).

## struct HeaderList

```ceesafe
public struct HeaderList<region r>
```

A request's fields in arrival order: up to 64, each a name and a value that are views of the head buffer
(`Slice<r, u8>`, since a value may hold obs-text and need not be UTF-8). Lookup is linear.

### HeaderList.capacity

```ceesafe
public fn capacity() -> u32
```

The most fields a list holds; `Limits.maxHeaders` may set fewer.

### HeaderList.create

```ceesafe
public fn create(head: Slice<r, u8>) -> HeaderList<r>
```

An empty list over `head`, the buffer its fields will point into.

### HeaderList.push

```ceesafe
public fn push(
    borrow mut this,
    nameStart: usize,
    nameEnd: usize,
    valueStart: usize,
    valueEnd: usize,
    limit: u32,
) -> Result<void, ProtocolError>
```

Adds the field whose name is `head[nameStart..nameEnd]` and value `head[valueStart..valueEnd]` (already
checked by the parser). `Err(TooManyHeaders)` past `limit` or the capacity; `Err(BadHeader)` if a range is
not inside the buffer.

### HeaderList.count

```ceesafe
public fn count(borrow this) -> u32
```

How many fields.

### HeaderList.name

```ceesafe
public fn name(borrow this, i: u32) -> HeaderName
```

Field `i`'s name (0-based; `Other` past the end).

### HeaderList.nameBytes

```ceesafe
public fn nameBytes(borrow this, i: u32) -> Slice<r, u8>
```

Field `i`'s name as sent; empty past the end.

### HeaderList.value

```ceesafe
public fn value(borrow this, i: u32) -> Slice<r, u8>
```

Field `i`'s value, without surrounding whitespace; empty past the end.

### HeaderList.get

```ceesafe
public fn get(borrow this, name: HeaderName) -> Slice<r, u8>?
```

The first value of `name` (a recognised name), or null.

### HeaderList.getNamed

```ceesafe
public fn getNamed(borrow this, name: StringView) -> Slice<r, u8>?
```

The first value of the field named `name` (any name, lower case), or null.

### HeaderList.countOf

```ceesafe
public fn countOf(borrow this, name: HeaderName) -> u32
```

How many fields are named `name`.

### HeaderList.hasToken

```ceesafe
public fn hasToken(borrow this, name: HeaderName, token: StringView) -> bool
```

Whether a field named `name` lists `token` among its comma-separated elements, ignoring case (for
`Connection: close`, `Expect: 100-continue`); `token` is lower case.

## struct Limits

```ceesafe
public struct Limits {
    public maxRequestLineBytes: usize;
    public maxHeadBytes: usize;
    public maxHeaders: u32;
    public maxBodyBytes: u64;
    public maxChunkExtensionBytes: usize;
    public maxTrailerBytes: usize;
    public drainBytes: u64;
    public headerTimeoutMs: u64;
    public idleTimeoutMs: u64;
    public writeTimeoutMs: u64;
    public minBodyRate: u64;
    public minBodyRateGraceMs: u64;
    public writeBufferBytes: usize;
    public sendDate: bool;
}
```

What one connection's requests may cost. `defaults()` gives a server's usual values.

### Limits.defaults

```ceesafe
public fn defaults() -> Limits
```

8 KiB request line, 16 KiB head, 64 fields, 8 MiB body, 256-byte chunk extensions, 4 KiB trailers, 64 KiB
drain; 10 s for a head, 60 s idle, 30 s per write; 240 B/s after 5 s (Kestrel's minimum rate); 16 KiB of
output per flush; `Date` sent.

## enum Method

```ceesafe
public enum Method {
    Get,
    Head,
    Post,
    Put,
    Delete,
    Connect,
    Options,
    Trace,
    Patch,
    Extension,
}
```

A request method: the standard ones, or `Extension` for any other token (its text is `Request.methodToken()`).

## struct Methods

```ceesafe
public struct Methods
```

Parsing and naming methods.

### Methods.parse

```ceesafe
public fn parse(token: Slice<u8>) -> Method
```

The method for `token`, which the parser has checked is a token (RFC 9110 §5.6.2). Case-sensitive: `get` is an
extension method, not GET.

### Methods.text

```ceesafe
public fn text(m: Method) -> StringView
```

The method's token; "" for `Extension`.

### Methods.same

```ceesafe
public fn same(a: Method, b: Method) -> bool
```

Whether two methods are the same (an enum has no `==`).

### Methods.index

```ceesafe
public fn index(m: Method) -> u32
```

The method's number, 0 to 9, in declaration order: for tables indexed by method.

## struct Request

```ceesafe
public struct Request<region r>
```

A parsed request head. Every protocol produces this same type.

### Request.create

```ceesafe
public fn create(
    method: Method,
    methodToken: Slice<r, u8>,
    target: StringView<r>,
    version: Version,
    headers: HeaderList<r>,
) -> Request<r>
```

A request; `target` must be ASCII (the parser checks it).

### Request.sentEarly

```ceesafe
public fn sentEarly(this) -> Request<r>
```

This request, marked as having come in TLS early data (0-RTT): `earlyData()`. Set by HTTP/3's driver (Web ADR
0014 §3.7).

### Request.earlyData

```ceesafe
public fn earlyData(borrow this) -> bool
```

Whether the request came in TLS early data (QUIC's 0-RTT, RFC 9001 §4.6.1), before the handshake proved the
client: such a request may be a replay (RFC 8470 §1). The server answers 425 Too Early to any method but GET,
HEAD and OPTIONS itself; a handler whose GET has an effect can refuse it too (`Status.tooEarly()`).

### Request.method

```ceesafe
public fn method(borrow this) -> Method
```

The method.

### Request.methodToken

```ceesafe
public fn methodToken(borrow this) -> Slice<r, u8>
```

The method's token as sent (the only way to read an extension method).

### Request.target

```ceesafe
public fn target(borrow this) -> StringView<r>
```

The request target as sent: `/path?query`, an absolute URI, an authority (CONNECT) or `*`.

### Request.path

```ceesafe
public fn path(borrow this) -> StringView<r>
```

The target up to `?`.

### Request.query

```ceesafe
public fn query(borrow this) -> StringView<r>?
```

The target after `?`, or null without one.

### Request.version

```ceesafe
public fn version(borrow this) -> Version
```

The version.

### Request.headers

```ceesafe
public fn headers(borrow this) -> borrow HeaderList<r>
```

The fields.

## enum BodyFraming

```ceesafe
public enum BodyFraming {
    None,
    Length(u64),
    Chunked,
}
```

How a request's body is delimited (RFC 9112 §6.3).

## enum FieldError

```ceesafe
public enum FieldError {
    BadName,
    BadValue,
    Reserved,
    TooLarge,
}
```

Why a field could not be added to a response head.

## struct ResponseHead

```ceesafe
public struct ResponseHead
```

A response's status and fields, before they are sent.

### ResponseHead.create

```ceesafe
public fn create(capacity: usize) -> Result<ResponseHead, AllocError>
```

A head with room for `capacity` bytes of fields ("name: value\r\n" each), status 200.

### ResponseHead.reset

```ceesafe
public fn reset(borrow mut this)
```

Back to status 200 with no fields, for the next response.

### ResponseHead.status

```ceesafe
public fn status(borrow this) -> StatusCode
```

The status.

### ResponseHead.setStatus

```ceesafe
public fn setStatus(borrow mut this, status: StatusCode)
```

Sets the status.

### ResponseHead.add

```ceesafe
public fn add(borrow mut this, name: StringView, value: Slice<u8>) -> Result<void, FieldError>
```

Adds the field `name: value`. The name must be a token and not a framing field; the value is sent as given,
so it must be free of control bytes other than HTAB.

### ResponseHead.has

```ceesafe
public fn has(borrow this, name: StringView) -> bool
```

Whether a field named `name` (lower case) was added.

### ResponseHead.fieldBytes

```ceesafe
public fn fieldBytes(borrow this) -> Slice<u8>
```

The fields as they will be sent: "name: value\r\n" each.

### ResponseHead.setContentLength

```ceesafe
public fn setContentLength(borrow mut this, n: u64)
```

Declares the body's length, so the writer sends Content-Length instead of chunking.

### ResponseHead.contentLength

```ceesafe
public fn contentLength(borrow this) -> u64?
```

The declared length, if any.

### ResponseHead.closeConnection

```ceesafe
public fn closeConnection(borrow mut this)
```

Asks for the connection to close after this response.

### ResponseHead.closes

```ceesafe
public fn closes(borrow this) -> bool
```

Whether the connection closes after this response.

### ResponseHead.upgradeTo

```ceesafe
public fn upgradeTo(borrow mut this, protocol: StringView) -> Result<void, FieldError>
```

Adds `upgrade: <protocol>` and `connection: upgrade`, the fields of a 101 (RFC 9110 §7.8), which `add` keeps
from handlers; for `Exchange.switchProtocols` only. `protocol` must be a token.

## enum Version

```ceesafe
public enum Version {
    Http10,
    Http11,
    Http2,
    Http3,
}
```

An HTTP version.

## struct StatusCode

```ceesafe
public struct StatusCode
```

A response status code, 100 to 999 (RFC 9110 §15).

### StatusCode.of

```ceesafe
public fn of(code: u16) -> StatusCode?
```

The status `code`, or null outside 100..999.

### StatusCode.code

```ceesafe
public fn code(this) -> u16
```

The number.

### StatusCode.same

```ceesafe
public fn same(this, other: StatusCode) -> bool
```

Whether this is the same status as `other`.

### StatusCode.isInformational

```ceesafe
public fn isInformational(this) -> bool
```

1xx.

### StatusCode.allowsContent

```ceesafe
public fn allowsContent(this) -> bool
```

Whether a response with this status may have content: not 1xx, 204 or 304 (RFC 9110 §6.4.1).

### StatusCode.reason

```ceesafe
public fn reason(this) -> StringView
```

The reason phrase RFC 9110 §15 gives the code (and RFC 8470 for 425); "" for codes they do not name.

## struct Status

```ceesafe
public struct Status
```

The status codes a server writes itself.

### Status.continue100

```ceesafe
public fn continue100() -> StatusCode
```

100 Continue.

### Status.switchingProtocols

```ceesafe
public fn switchingProtocols() -> StatusCode
```

200 OK.

### Status.ok

```ceesafe
public fn ok() -> StatusCode
```

200 OK.

### Status.created

```ceesafe
public fn created() -> StatusCode
```

201 Created.

### Status.noContent

```ceesafe
public fn noContent() -> StatusCode
```

204 No Content.

### Status.partialContent

```ceesafe
public fn partialContent() -> StatusCode
```

206 Partial Content (RFC 9110 §15.3.7).

### Status.notModified

```ceesafe
public fn notModified() -> StatusCode
```

304 Not Modified.

### Status.badRequest

```ceesafe
public fn badRequest() -> StatusCode
```

400 Bad Request.

### Status.notFound

```ceesafe
public fn notFound() -> StatusCode
```

404 Not Found.

### Status.methodNotAllowed

```ceesafe
public fn methodNotAllowed() -> StatusCode
```

405 Method Not Allowed.

### Status.requestTimeout

```ceesafe
public fn requestTimeout() -> StatusCode
```

408 Request Timeout.

### Status.lengthRequired

```ceesafe
public fn lengthRequired() -> StatusCode
```

411 Length Required.

### Status.preconditionFailed

```ceesafe
public fn preconditionFailed() -> StatusCode
```

412 Precondition Failed (RFC 9110 §15.5.13).

### Status.contentTooLarge

```ceesafe
public fn contentTooLarge() -> StatusCode
```

413 Content Too Large.

### Status.uriTooLong

```ceesafe
public fn uriTooLong() -> StatusCode
```

414 URI Too Long.

### Status.rangeNotSatisfiable

```ceesafe
public fn rangeNotSatisfiable() -> StatusCode
```

416 Range Not Satisfiable (RFC 9110 §15.5.17).

### Status.expectationFailed

```ceesafe
public fn expectationFailed() -> StatusCode
```

417 Expectation Failed.

### Status.tooEarly

```ceesafe
public fn tooEarly() -> StatusCode
```

425 Too Early (RFC 8470 §5.2): the request came in early data, which may be replayed; retry it after the
handshake.

### Status.headerFieldsTooLarge

```ceesafe
public fn headerFieldsTooLarge() -> StatusCode
```

431 Request Header Fields Too Large.

### Status.internalServerError

```ceesafe
public fn internalServerError() -> StatusCode
```

500 Internal Server Error.

### Status.notImplemented

```ceesafe
public fn notImplemented() -> StatusCode
```

501 Not Implemented.

### Status.serviceUnavailable

```ceesafe
public fn serviceUnavailable() -> StatusCode
```

503 Service Unavailable.

### Status.versionNotSupported

```ceesafe
public fn versionNotSupported() -> StatusCode
```

505 HTTP Version Not Supported.

## interface StreamSink

```ceesafe
public interface StreamSink {
    async fn readBody(borrow mut this, into: SliceMut<u8>) -> Result<usize, HandlerError>;
    async fn sendHead(borrow mut this, fields: HeaderBlock, endStream: bool) -> Result<void, HandlerError>;
    async fn sendData(borrow mut this, bytes: Slice<u8>, endStream: bool) -> Result<void, HandlerError>;
    async fn push(borrow mut this, path: Slice<u8>) -> Result<bool, HandlerError>;
    @effects(alloc)
    fn copyTrailers(borrow this, into: borrow mut HeaderBlock) -> Result<void, AllocError>;
}
```

A request stream's body in, and its response out.

## struct NoStream

```ceesafe
public struct NoStream
```

The sink of an HTTP/1.1 exchange, which has no stream: every call is `Internal`, and nothing is pushed.

Implements `StreamSink`.

## struct NoTransport

```ceesafe
public struct NoTransport
```

The transport of a stream's exchange, which has none of its own: reads end, writes fail.

Implements `Transport`.

## enum UrlError

```ceesafe
public enum UrlError {
    BadScheme,
    BadHost,
    BadPort,
    Userinfo,
    BadCharacter,
    OutOfMemory,
}
```

Why a URL was refused.

## struct Url

```ceesafe
@noncopy
public struct Url
```

An absolute http or https URL: scheme, host, port and the request target (path and query; at least `/`).

### Url.parse

```ceesafe
public fn parse(text: Slice<u8>) -> Result<Url, UrlError>
```

Parses `text`. The scheme and host are lower-cased; the fragment is dropped; an empty path is `/`.

### Url.isSecure

```ceesafe
public fn isSecure(borrow this) -> bool
```

Whether the scheme is https.

### Url.host

```ceesafe
public fn host(borrow this) -> Slice<u8>
```

The host, lower-cased; an IPv6 address keeps its brackets (`[::1]`).

### Url.hostName

```ceesafe
public fn hostName(borrow this) -> Slice<u8>
```

The host as a name to resolve or verify: without an IPv6 address's brackets.

### Url.port

```ceesafe
public fn port(borrow this) -> u16
```

The port: the URL's, or the scheme's default (80, 443).

### Url.target

```ceesafe
public fn target(borrow this) -> Slice<u8>
```

The request target: the path and the query, at least `/`.

### Url.authority

```ceesafe
public fn authority(borrow this, out: borrow mut Vector<u8>) -> Result<void, UrlError>
```

The value of a request's `host` field: the host, and the port when it is not the scheme's default.

### Url.text

```ceesafe
public fn text(borrow this, out: borrow mut Vector<u8>) -> Result<void, UrlError>
```

The URL as text: `scheme://authority/target`.

### Url.copy

```ceesafe
public fn copy(borrow this) -> Result<Url, UrlError>
```

A copy.

### Url.sameOrigin

```ceesafe
public fn sameOrigin(borrow this, other: borrow Url) -> bool
```

Whether `other` has the same origin: scheme, host and port (RFC 6454).

### Url.resolve

```ceesafe
public fn resolve(borrow this, location: Slice<u8>) -> Result<Url, UrlError>
```

A redirect's `location` resolved against this URL (RFC 3986 §5.2): an absolute URL; `//host…` (this scheme);
`/path` (this origin); or a relative path (merged with this path's directory, dot segments removed).
