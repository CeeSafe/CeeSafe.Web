# web_http1

- [enum ResponseBody](#enum-responsebody)
- [struct ParsedResponse](#struct-parsedresponse)
- [fn parseResponseHead](#fn-parseresponsehead)
- [struct RequestWriter](#struct-requestwriter)
- [struct HeadScanner](#struct-headscanner)
- [struct Head](#struct-head)
- [fn parseHead](#fn-parsehead)
- [fn serve](#fn-serve)
- [fn serveFrom](#fn-servefrom)

## enum ResponseBody

```ceesafe
public enum ResponseBody {
    Empty,
    Length(u64),
    Chunked,
    UntilClose,
}
```

How a response's body is framed (RFC 9112 §6.3).

## struct ParsedResponse

```ceesafe
public struct ParsedResponse<region r> {
    public status: u16;
    public reason: Slice<r, u8>;
    public version: Version;
    public headers: HeaderList<r>;
    public body: ResponseBody;
    public keepAlive: bool;
}
```

A parsed response head; its views borrow the input.

## fn parseResponseHead

```ceesafe
public fn parseResponseHead<region r>(
    input: Slice<r, u8>,
    start: usize,
    end: usize,
    method: Method,
    limits: borrow Limits,
) -> Result<ParsedResponse<r>, ProtocolError>
```

Parses the complete response head `input[start..end]` (from a `HeadScanner`), the response to a `method` request.
Errors: `BadStatusLine`; a version other than HTTP/1.x (`UnsupportedVersion`); the field errors of `parseFields`;
both Content-Length and Transfer-Encoding (`LengthAndEncoding`), differing Content-Lengths (`BadContentLength`), or
a Transfer-Encoding other than `chunked` (`BadTransferEncoding`: this client decodes no other).

## struct RequestWriter

```ceesafe
public struct RequestWriter
```

Writes a request head into `out`, checking every part as it goes, so no caller's text can end a line early or
smuggle a field: `start`, then `field` for each field, then `finish`.

### RequestWriter.start

```ceesafe
public fn start(
    out: borrow mut Vector<u8>,
    method: Slice<u8>,
    target: Slice<u8>,
    host: Slice<u8>,
) -> RequestWriter
```

The request line and `host` (RFC 9112 §3, §3.2): `method` a token, `target` an origin-form target (visible
ASCII, starting with `/`), `host` the authority.

### RequestWriter.field

```ceesafe
public fn field(borrow mut this, out: borrow mut Vector<u8>, name: Slice<u8>, value: Slice<u8>)
```

A field: `name` a token, `value` field-value characters only (no CR, LF or NUL). Framing fields belong to
`finish`, and `host` to `start`: those names are refused here.

### RequestWriter.finish

```ceesafe
public fn finish(borrow mut this, out: borrow mut Vector<u8>, length: u64?, declareZero: bool) -> bool
```

The framing and the empty line: `length` for a body of known length (0 writes `content-length: 0` only if
`declareZero`, as for POST and PUT), null for a chunked body. False if any part was refused or `out` could not
grow: then `out` holds no usable head.

## struct HeadScanner

```ceesafe
public struct HeadScanner
```

Finds the end of a request head as its bytes arrive, without scanning any byte twice. A connection keeps one
per request and calls `scan` with the whole buffered input each time more arrives.

### HeadScanner.create

```ceesafe
public fn create() -> HeadScanner
```

A scanner at the start of a request.

### HeadScanner.scan

```ceesafe
public fn scan(borrow mut this, input: Slice<u8>, limits: borrow Limits) -> Result<bool, ProtocolError>
```

Scans `input` (the buffered bytes, starting where the request starts; longer each call). True once the
empty line that ends the head has arrived: then `end()` is the head's length. Errors: a bare LF
(`BareLineFeed`); a request line longer than the limit (`RequestLineTooLong`); a head longer than the limit
(`HeadTooLarge`). Empty lines before the request line are skipped (RFC 9112 §2.2).

### HeadScanner.start

```ceesafe
public fn start(borrow this) -> usize
```

Where the head starts (after skipped empty lines).

### HeadScanner.end

```ceesafe
public fn end(borrow this) -> usize
```

Where the head ends (after its empty line); 0 until `scan` returned true.

## struct Head

```ceesafe
public struct Head<region r> {
    public request: Request<r>;
    public framing: BodyFraming;
    public keepAlive: bool;
    public expectContinue: bool;
}
```

A parsed head: the request, how its body is framed, and what the connection should do.

## fn parseHead

```ceesafe
public fn parseHead<region r>(
    input: Slice<r, u8>,
    start: usize,
    end: usize,
    limits: borrow Limits,
) -> Result<Head<r>, ProtocolError>
```

Parses the complete head `input[start..end]` (from a `HeadScanner`: it ends with CRLF CRLF). The request's views
borrow `input`. Any other range is an error, never a panic.

## fn serve

```ceesafe
public async fn serve(
    conn: borrow mut dyn Transport,
    connection: borrow ConnectionContext,
    app: borrow dyn Application,
    limits: borrow Limits,
) -> Result<void, NetError>
```

Serves HTTP/1.1 on `conn` until the client closes or asks to, a protocol error, a timeout, or the server's drain
(an idle connection closes at once; one in a request finishes it first). A peer's reset or a failed write is
`Err`; every other end is `Ok`.

## fn serveFrom

```ceesafe
public async fn serveFrom(
    conn: borrow mut dyn Transport,
    connection: borrow ConnectionContext,
    app: borrow dyn Application,
    limits: borrow Limits,
    already: Slice<u8>,
) -> Result<void, NetError>
```

`serve`, for a connection whose first bytes, `already`, were read to choose the protocol (Web ADR 0011 §3.2: a
cleartext connection that does not start with HTTP/2's preface): they are the start of the first request.
