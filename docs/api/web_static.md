# web_static

- [struct Validators](#struct-validators)
- [enum Precondition](#enum-precondition)
- [struct Conditionals](#struct-conditionals)
- [struct StaticOptions](#struct-staticoptions)
- [struct StaticFiles](#struct-staticfiles)
- [struct MediaTypes](#struct-mediatypes)
- [enum PathRefusal](#enum-pathrefusal)
- [struct StaticPaths](#struct-staticpaths)
- [enum RangeAsk](#enum-rangeask)
- [struct Ranges](#struct-ranges)

## struct Validators

```ceesafe
public struct Validators {
    public etag: [u8; 36];
    public etagLength: usize;
    public modifiedSeconds: u64;
}
```

A file's validators: its strong `ETag` and its modification time.

### Validators.of

```ceesafe
public fn of(size: u64, modified: UnixTime) -> Validators
```

The validators of a file of `size` bytes modified at `modified`.

### Validators.etagBytes

```ceesafe
public fn etagBytes(borrow this) -> Slice<u8>
```

The `ETag` value.

## enum Precondition

```ceesafe
public enum Precondition {
    Proceed,
    NotModified,
    Failed,
}
```

What the preconditions decide, before any `Range` is looked at.

## struct Conditionals

```ceesafe
public struct Conditionals
```

RFC 9110 §13.2.2's preconditions and §13.1.5's `If-Range`, for a file's `Validators`.

### Conditionals.evaluate

```ceesafe
public fn evaluate(headers: borrow HeaderList, validators: borrow Validators, nowSeconds: u64) -> Precondition
```

RFC 9110 §13.2.2's steps 1 to 4 for a GET or HEAD of a file with `validators`, given the request's fields:
1. `If-Match` that does not match (strong comparison; `*` matches any file) is 412.
2. Without `If-Match`, `If-Unmodified-Since` with a valid date before the file's time is 412.
3. `If-None-Match` that matches (weak comparison; `*` matches) is 304.
4. Without `If-None-Match`, `If-Modified-Since` with a valid date at or after the file's time is 304.
A date field with an invalid date is ignored (RFC 9110 §13.1.3, §13.1.4). `nowSeconds` is for RFC 850's
two-digit years.

### Conditionals.rangeAllowed

```ceesafe
public fn rangeAllowed(headers: borrow HeaderList, validators: borrow Validators, nowSeconds: u64) -> bool
```

RFC 9110 §13.1.5: whether a `Range` may be used. With no `If-Range`, it may. With one, only if it is an
entity-tag that strongly matches the file's, or an HTTP date exactly the file's `Last-Modified`. A weak
entity-tag never matches.

## struct StaticOptions

```ceesafe
public struct StaticOptions {
    public index: bool;
    public cacheControl: Vector<u8>;
}
```

How a `StaticFiles` answers.

### StaticOptions.defaults

```ceesafe
public fn defaults() -> StaticOptions
```

`index` on, no `Cache-Control`.

## struct StaticFiles

```ceesafe
public struct StaticFiles<S>
```

The files under a root, as a handler for any application state `S`. Mount it on a route that ends in `*path`
(`router.get("/assets/*path", …)`), or on a single path, whose whole request path is then looked up.

### StaticFiles.create

```ceesafe
public fn create(root: StringView, options: StaticOptions) -> Result<StaticFiles<S>, AllocError>
```

Serves the files under `root`, a directory path without a trailing `/` (one is dropped).

Implements `Handler<S>`.

## struct MediaTypes

```ceesafe
public struct MediaTypes
```

The `Content-Type` of a file, from its extension.

### MediaTypes.fallback

```ceesafe
public fn fallback() -> StringView
```

The fallback for an unknown extension (sent with `X-Content-Type-Options: nosniff`).

### MediaTypes.forName

```ceesafe
public fn forName(name: Slice<u8>) -> StringView
```

The media type for the file named `name` (its last segment, or a whole path), by the extension after its last
`.`, compared ignoring case; empty if the table has none, for which the caller sends `fallback()`.

## enum PathRefusal

```ceesafe
public enum PathRefusal {
    BadEscape,
    ForbiddenByte,
    DotSegment,
    EmptySegment,
    TooLong,
    OutOfMemory,
}
```

Why a request path names no file. Every one is answered 404, never 400, so probing learns nothing.

## struct StaticPaths

```ceesafe
public struct StaticPaths
```

The lexical rules for a request path under the root (Web ADR 0013 §3.5).

### StaticPaths.maxLength

```ceesafe
public fn maxLength() -> usize
```

The longest decoded path accepted, in bytes.

### StaticPaths.relative

```ceesafe
public fn relative(requestPath: Slice<u8>, out: borrow mut Vector<u8>) -> Result<void, PathRefusal>
```

The relative path, in `out`, that `requestPath` names under the root. A leading `/` is dropped. The path is
percent-decoded once, then refused if it has a zero byte, a backslash, a `:`, a `.` or `..` segment, or an
empty segment other than the last. An empty result is the root itself. A trailing `/` is kept, so the caller
can tell a directory's form.

## enum RangeAsk

```ceesafe
public enum RangeAsk {
    Whole,
    Part(u64, u64),
    Unsatisfiable,
}
```

What a `Range` field asks of a file of a given size.

## struct Ranges

```ceesafe
public struct Ranges
```

One `bytes` range of a file, from a `Range` field (RFC 9110 §14).

### Ranges.ask

```ceesafe
public fn ask(value: Slice<u8>, size: u64) -> RangeAsk
```

What `value` asks of a file of `size` bytes. The forms are `bytes=a-b`, `bytes=a-` and `bytes=-n` (RFC 9110
§14.1.2). The unit is case-insensitive, and white space around the range is allowed. A last position past the
end is clipped, and a suffix longer than the file is the whole file. A first position at or past the end, or
a suffix of 0, is unsatisfiable. A list of several ranges, another unit, `a-b` with `b < a`, or anything else
malformed is `Whole`.
