# web_static

Static files for a CeeSafe.Web server (Web ADR 0013 §3.5). `StaticFiles<S>` is a `Handler<S>` for GET and HEAD that
serves the regular files under a root.

```ceesafe
let files: Box<dyn Handler<App> + Send + Share> = Box.create(StaticFiles<App>.create("/srv/www", StaticOptions.defaults())?)?;
server.router().get("/assets/*path", files)?;
```

On a route ending in `*path`, the wildcard's text is looked up under the root; on any other route, the whole
request path is. `StaticOptions` has `index` (serve a directory as its `index.html`, on by default) and
`cacheControl` (a `Cache-Control` value for every 200, 206 and 304, none by default).

## What it answers

- **The file:**
  - 200 with `Content-Length`, `Content-Type`, `Accept-Ranges: bytes`, a strong `ETag` and `Last-Modified`.
  - The body goes with `Exchange.sendFile`: on plain HTTP/1.1 the kernel sends it (`sendfile`), and nothing is copied.
- **Conditionals,** in RFC 9110 §13.2.2's order:
  - `If-Match`, else `If-Unmodified-Since`, answered 412;
  - then `If-None-Match`, else `If-Modified-Since`, answered 304.
- **Range:**
  - One `bytes` range is 206 with `Content-Range`; past the end, 416 with `Content-Range: bytes */size`.
  - Several ranges, another unit or a malformed field: the whole file, 200. There is no `multipart/byteranges`
    (Web ADR 0013 §6.4).
  - `If-Range` lets the range through only on a strong `ETag` match or the exact `Last-Modified`.
  - HEAD ignores `Range`.
- **404 for anything that is not a regular file under the root, never 400,** so probing learns nothing:
  - a missing file;
  - a directory without `index.html`, or any directory with `index` off. There are no listings;
  - a device, socket or FIFO;
  - a path that names something outside the root.
- **405** with `Allow: GET, HEAD` for other methods, if the handler is mounted for them.

## The path rules, and symbolic links

The request path is percent-decoded once, then refused if it has any of:
- a zero byte, a backslash or a `:`. `:` is refused on every system, since it is how Windows names drives and streams;
- a `.` or `..` segment, `%2e%2e` and `%2f`-separated ones included;
- an empty segment other than the last.

The check is lexical, as nginx's is. **A symbolic link under the root is followed, even when it points outside the
root:** the files under the root are the operator's (Web ADR 0013 §6, answer 3). Do not put links under the root
that you do not mean to serve.

## Validators

- **The `ETag`** is `"<size in hex>-<modification time in nanoseconds, in hex>"`, the same idea as nginx's. Both are
  read from the open file (`fstat`), not its path, so they describe the bytes that are sent even if another file is
  renamed into place meanwhile (CeeSafe ADR 0078).
- **A false match is possible.** A file rewritten within the same nanosecond, at the same size, keeps its `ETag`.
  File systems with coarse timestamps (FAT's two seconds, some network file systems' one second) make that likelier.
- **The interpreter** cannot read a file's time. Run under `ceesafec run --interp`, responses have no validators,
  and conditionals are not applied.

Opening uses `File.openRegular` (CeeSafe 0.22.0), which never waits on a FIFO, so a request for one cannot hold a
worker.
