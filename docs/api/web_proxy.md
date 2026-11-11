# web_proxy

- [fn sipHash](#fn-siphash)
- [enum Policy](#enum-policy)
- [enum Outcome](#enum-outcome)
- [struct HealthOptions](#struct-healthoptions)
- [struct Balancer](#struct-balancer)
- [enum Hop](#enum-hop)
- [enum ForwardedSource](#enum-forwardedsource)
- [enum ClientIs](#enum-clientis)
- [struct Hops](#struct-hops)
- [fn clientOf](#fn-clientof)
- [struct ForwardingFields](#struct-forwardingfields)
- [fn forwardingFields](#fn-forwardingfields)
- [enum HashOn](#enum-hashon)
- [struct ProxyOptions](#struct-proxyoptions)
- [enum ProxySetupError](#enum-proxysetuperror)
- [struct ProxyBuilder](#struct-proxybuilder)
- [struct ReverseProxy](#struct-reverseproxy)
- [struct ProxyHandler](#struct-proxyhandler)
- [struct ProxyRoute](#struct-proxyroute)

## fn sipHash

```ceesafe
public fn sipHash(key: [u8; 16], data: Slice<u8>, c: u32, d: u32) -> u64
```

SipHash-c-d (Aumasson and Bernstein, "SipHash: a fast short-input PRF", 2012): `c` compression and `d`
finalization rounds, 64-bit output, keyed with `key` (little-endian, §2). The proxy uses SipHash-1-3.

## enum Policy

```ceesafe
public enum Policy {
    RoundRobin,
    LeastRequests,
    ConsistentHash,
}
```

How a backend is chosen.

## enum Outcome

```ceesafe
public enum Outcome {
    Answered,
    ServerError,
    Failed,
}
```

How a request went, for passive health.

## struct HealthOptions

```ceesafe
public struct HealthOptions {
    public consecutiveFailures: u32;
    public baseEjectionMs: u64;
    public maxEjectionMs: u64;
    public countServerErrors: bool;
    public panicMode: bool;
    public healthyAfter: u32;
    public unhealthyAfter: u32;
}
```

Passive and active health settings (Web ADR 0012 §3.3).

### HealthOptions.defaults

```ceesafe
public fn defaults() -> HealthOptions
```

5 failures; 30 s, doubling to 5 min; 5xx not counted; panic mode on; 2 good and 3 bad checks.

## struct Balancer

```ceesafe
public struct Balancer
```

The backends of one upstream and how to choose among them.

### Balancer.create

```ceesafe
public fn create(policy: Policy, health: HealthOptions, hashKey: [u8; 16], seed: u64) -> Balancer
```

An empty balancer. `hashKey` keys the consistent hash (random, so clients cannot choose colliding keys);
`seed` drives least-requests' draws.

### Balancer.add

```ceesafe
public fn add(borrow mut this, name: Slice<u8>, weight: u32) -> Result<usize, AllocError>
```

Adds a backend named `name` (its URL) with `weight` (at least 1); its index is the count before.

### Balancer.count

```ceesafe
public fn count(borrow this) -> usize
```

How many backends.

### Balancer.inFlight

```ceesafe
public fn inFlight(borrow this, i: usize) -> u64
```

Requests in flight to backend `i`.

### Balancer.usable

```ceesafe
public fn usable(borrow this, i: usize, nowMs: u64) -> bool
```

Whether backend `i` is usable at `nowMs`: healthy by the active checks, and not ejected.

### Balancer.pick

```ceesafe
public fn pick(borrow mut this, key: Slice<u8>, nowMs: u64, avoid: usize?) -> usize?
```

The backend for a request with hash key `key` (consistent hash only) at `nowMs`, other than `avoid` if another
is usable (a retry goes elsewhere); null when none is usable and panic mode is off. The request counts as in
flight on it until `finished`.

### Balancer.finished

```ceesafe
public fn finished(borrow mut this, i: usize, outcome: Outcome, nowMs: u64)
```

A request to backend `i` is over, with `outcome`, at `nowMs` (passive health, §3.3). Consecutive failures past
the threshold eject it for `baseEjectionMs` × 2^n (n: ejections in a row), up to `maxEjectionMs`, unless half the
backends (rounded down) are ejected already.

### Balancer.checked

```ceesafe
public fn checked(borrow mut this, i: usize, good: bool)
```

An active check of backend `i` came back `good` or not (§3.3): `healthyAfter` good results in a row mark it
healthy, `unhealthyAfter` bad ones unhealthy.

## enum Hop

```ceesafe
public enum Hop {
    Address(SocketAddress),
    Hidden,
    Malformed,
}
```

One hop of a forwarding chain, as a `for=` value or an `X-Forwarded-For` element names it.

## enum ForwardedSource

```ceesafe
public enum ForwardedSource {
    ForwardedFirst,
    XForwardedForFirst,
}
```

Which field says who the client is, when the peer is trusted.

## enum ClientIs

```ceesafe
public enum ClientIs {
    Address(SocketAddress),
    Hidden,
    Unknown,
}
```

Who the client is.

## struct Hops

```ceesafe
public struct Hops
```

The hops of a chain, left (the first proxy's client) to right (the last proxy).

### Hops.fromForwarded

```ceesafe
public fn fromForwarded(headers: borrow HeaderList) -> Result<Hops, AllocError>
```

The `for=` hops of every `Forwarded` field in `headers`, in order (RFC 7239 §4: several fields are one list).
An element without `for=`, or one that does not parse, is `Malformed`; empty elements are skipped (RFC 9110
§5.6.1.2). Commas inside a quoted string, escaped quotes included (§5.6.4), do not split. A field that ends
inside a quoted string cannot be split at all, so its last hop is `Malformed`: otherwise a client's unclosed
quote would swallow the hop a trusted proxy appended after it.

### Hops.fromXForwardedFor

```ceesafe
public fn fromXForwardedFor(headers: borrow HeaderList) -> Result<Hops, AllocError>
```

The hops of every `X-Forwarded-For` field in `headers`, in order: addresses, with or without a port (`[::1]:80`).

### Hops.count

```ceesafe
public fn count(borrow this) -> usize
```

How many hops.

### Hops.hop

```ceesafe
public fn hop(borrow this, i: usize) -> Hop
```

The `i`th hop, from the left.

## fn clientOf

```ceesafe
public fn clientOf(
    peer: SocketAddress?,
    headers: borrow HeaderList,
    trusted: borrow TrustedProxies,
    source: ForwardedSource,
) -> Result<ClientIs, AllocError>
```

Who the client is (Web ADR 0012 §3.4). A peer that is not trusted is the client, whatever its fields say. Past a
trusted peer the chain is read from the right: each trusted address is skipped, and the first untrusted one is the
client; a hidden hop is the client, unnamed; a malformed hop ends the walk with the last trusted address; if every
hop is trusted, the left-most is the client.

## struct ForwardingFields

```ceesafe
public struct ForwardingFields {
    public forwarded: Vector<u8>;
    public forwardedFor: Vector<u8>;
    public forwardedProto: Vector<u8>;
    public forwardedHost: Vector<u8>;
}
```

What the proxy sends upstream about the request's origin (Web ADR 0012 §3.4): each value, or empty to send none.

## fn forwardingFields

```ceesafe
public fn forwardingFields(
    peer: SocketAddress?,
    headers: borrow HeaderList,
    trusted: borrow TrustedProxies,
    scheme: StringView,
    host: Slice<u8>,
    forwarded: bool,
    xForwarded: bool,
) -> Result<ForwardingFields, AllocError>
```

The forwarding fields for a request that came from `peer` over `scheme` (`http` or `https`) for `host`. The
incoming fields are kept and extended only when `peer` is trusted; from any other peer they are replaced, never
extended. `forwarded` and `xForwarded` choose which families are produced.

## enum HashOn

```ceesafe
public enum HashOn {
    ClientAddress,
    Field(Vector<u8>),
}
```

What a consistent hash is computed over (Web ADR 0012 §3.2).

## struct ProxyOptions

```ceesafe
public struct ProxyOptions {
    public trusted: TrustedProxies;
    public source: ForwardedSource;
    public forwarded: bool;
    public xForwarded: bool;
    public scheme: Vector<u8>;
    public keepHost: bool;
    public policy: Policy;
    public hashOn: HashOn;
    public health: HealthOptions;
    public checkPath: Vector<u8>;
    public checkIntervalMs: u64;
    public checkTimeoutMs: u64;
    public retryPercent: u32;
    public retryMinimum: u32;
    public retryBufferBytes: usize;
    public maxAttempts: u32;
    public tunnelIdleMs: u64;
    public client: ClientOptions;
}
```

How the proxy treats requests (Web ADR 0012 §3.4-§3.8).

### ProxyOptions.defaults

```ceesafe
public fn defaults() -> ProxyOptions
```

No trusted proxies; both forwarding families; scheme "http"; the client's Host kept; round robin; passive health
and no active checks; 20 % retries plus 3, a 64 KiB replay buffer, 2 attempts; tunnels idle out after 5 min.

## enum ProxySetupError

```ceesafe
public enum ProxySetupError {
    BadBackend,
    NoBackends,
    OutOfMemory,
}
```

Why a proxy could not be built.

## struct ProxyBuilder

```ceesafe
public struct ProxyBuilder
```

Collects an upstream's backends, then builds the proxy.

### ProxyBuilder.create

```ceesafe
public fn create(options: ProxyOptions) -> ProxyBuilder
```

A proxy with `options` and, so far, no backend.

### ProxyBuilder.backend

```ceesafe
public fn backend(borrow mut this, url: StringView, weight: u32) -> Result<void, ProxySetupError>
```

Adds the backend at `url` (`http://10.0.0.1:8080`: scheme, host and port only) with `weight`.

### ProxyBuilder.build

```ceesafe
public fn build(this, tls: Box<dyn Connector + Send + Share>?) -> Result<ReverseProxy, ProxySetupError>
```

The proxy, its client making https connections with `tls` (null: http backends only).

### ProxyBuilder.buildWithNetwork

```ceesafe
public fn buildWithNetwork(
    this,
    tls: Box<dyn Connector + Send + Share>?,
    resolver: Box<dyn Resolver + Send + Share>,
    dialer: Box<dyn Dialer + Send + Share>,
) -> Result<ReverseProxy, ProxySetupError>
```

The same, its client resolving with `resolver` and dialing with `dialer` (tests: mocks; see
`Client.withNetwork`).

## struct ReverseProxy

```ceesafe
public struct ReverseProxy
```

A reverse proxy to one upstream. Serve requests with `handler()` (as a server's application or on a route), and
run `run` beside the server (Web ADR 0012 §3.1).

### ReverseProxy.handler

```ceesafe
public fn handler(borrow this) -> ProxyHandler
```

This proxy as a server's whole application.

### ReverseProxy.route

```ceesafe
public fn route<S: Send + Share>(borrow this) -> ProxyRoute<S>
```

The proxy as a route's handler, for a `Router<S>` of any state.

### ReverseProxy.run

```ceesafe
public async fn run(borrow this, drain: borrow Drain) -> Result<void, NetError>
```

Drives the upstream client's HTTP/2 connections and, if configured, the active health checks, until `drain`
starts.

## struct ProxyHandler

```ceesafe
public struct ProxyHandler
```

Serves requests for a `ReverseProxy`.

Implements `Application`.

## struct ProxyRoute

```ceesafe
public struct ProxyRoute<S>
```

A `ReverseProxy` on one route of a `Router<S>` (Web ADR 0012 §3: a route or the whole server can be a proxy). The
proxy reads none of the application's state; `S` only names the router it is mounted on.

Implements `Handler<S>`.
