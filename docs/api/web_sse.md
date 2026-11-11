# web_sse

- [enum EventError](#enum-eventerror)
- [struct Event](#struct-event)
- [struct Events](#struct-events)
- [fn lastEventId](#fn-lasteventid)
- [enum Overflow](#enum-overflow)
- [enum PublishError](#enum-publisherror)
- [enum QueueError](#enum-queueerror)
- [struct EventQueue](#struct-eventqueue)
- [struct StreamOptions](#struct-streamoptions)
- [enum StreamEnd](#enum-streamend)
- [interface EventSource](#interface-eventsource)
- [fn serveEvents](#fn-serveevents)
- [struct EventStream](#struct-eventstream)

## enum EventError

```ceesafe
public enum EventError {
    Invalid,
    OutOfMemory,
}
```

Why an event could not be made.

## struct Event

```ceesafe
public struct Event
```

One event: its data, and optionally an id, a type and a reconnection time. It owns its bytes, so it can cross a
channel to the task writing the stream.

### Event.create

```ceesafe
public fn create(data: Slice<u8>) -> Result<Event, EventError>
```

An event whose data is `data`, of the default type `message`. `data` may hold several lines (LF, CR or CRLF);
the client receives them joined with LF.

### Event.withId

```ceesafe
public fn withId(this, id: Slice<u8>) -> Result<Event, EventError>
```

The same event with the id `id`, which the client sends back as `Last-Event-ID` when it reconnects. An empty
id resets the client's last id.

### Event.withType

```ceesafe
public fn withType(this, name: Slice<u8>) -> Result<Event, EventError>
```

The same event with the type `name` (the client's `addEventListener(name, …)`).

### Event.withRetry

```ceesafe
public fn withRetry(this, ms: u64) -> Event
```

The same event, telling the client to wait `ms` milliseconds before reconnecting.

### Event.data

```ceesafe
public fn data(borrow this) -> Slice<u8>
```

The data.

## struct Events

```ceesafe
public struct Events
```

Encoding events and reading what a reconnecting client sent.

### Events.encode

```ceesafe
public fn encode(e: borrow Event, out: borrow mut Vector<u8>) -> Result<void, EventError>
```

Appends the wire form of `e` to `out`: `event:`, `id:` and `retry:` lines, one `data:` line per line of the
data, then an empty line. Each field is written with a space after its colon, which the client strips, so data
starting with a space keeps it. `Err(OutOfMemory)` if `out` could not grow; `out` may then hold part of it.

### Events.heartbeat

```ceesafe
public fn heartbeat() -> StringView
```

The heartbeat: a comment line, which clients ignore (§9.2.6).

## fn lastEventId

```ceesafe
public fn lastEventId<region r>(request: borrow Request<r>) -> Slice<r, u8>?
```

The `Last-Event-ID` a reconnecting client sent, if any.

## enum Overflow

```ceesafe
public enum Overflow {
    Block,
    Disconnect,
    DropOldest,
}
```

What `publish` does when the queue is full.

## enum PublishError

```ceesafe
public enum PublishError {
    Closed,
    Disconnected,
    Cancelled,
}
```

Why `publish` did not queue an event.

## enum QueueError

```ceesafe
public enum QueueError {
    BadCapacity,
    OutOfMemory,
}
```

Why a queue could not be made.

## struct EventQueue

```ceesafe
@noncopy
public struct EventQueue
```

A bounded queue of events with an overflow policy. Every handle may publish and take; `share` makes another
handle to the same queue, for the producer.

### EventQueue.create

```ceesafe
public fn create(capacity: usize, policy: Overflow) -> Result<EventQueue, QueueError>
```

A queue holding up to `capacity` events.

### EventQueue.share

```ceesafe
public fn share(borrow this) -> EventQueue
```

Another handle to the same queue.

### EventQueue.capacity

```ceesafe
public fn capacity(borrow this) -> usize
```

How many events the queue holds when full.

### EventQueue.publish

```ceesafe
public async fn publish(borrow this, e: Event) -> Result<void, PublishError>
```

Queues `e` under the queue's policy.

### EventQueue.next

```ceesafe
public async fn next(borrow this) -> Result<Event?, AsyncError>
```

The next event, waiting while the queue is empty; null once it is closed and empty.

### EventQueue.tryNext

```ceesafe
public fn tryNext(borrow this) -> Event?
```

The next event if one is queued, else null at once, whether the queue is empty or closed and empty.

### EventQueue.close

```ceesafe
public fn close(borrow this)
```

No more events: the stream ends after those queued, and later `publish` calls fail with `Closed`.

## struct StreamOptions

```ceesafe
public struct StreamOptions {
    public heartbeatMs: u64;
}
```

How a stream behaves.

### StreamOptions.defaults

```ceesafe
public fn defaults() -> StreamOptions
```

A 15 s heartbeat: below the 30–60 s idle timeouts common in proxies and load balancers.

## enum StreamEnd

```ceesafe
public enum StreamEnd {
    Finished,
    ClientClosed,
}
```

How a stream ended without an error.

## interface EventSource

```ceesafe
public interface EventSource<C> {
    async fn produce(borrow this, queue: borrow EventQueue, context: borrow C);
}
```

Produces a stream's events for `serveEvents`. `C` is what the producer reads besides its own fields (the
application's state, say), lent for the stream's duration.

## fn serveEvents

```ceesafe
public async fn serveEvents<C>(
    exchange: borrow mut Exchange,
    queue: borrow EventQueue,
    options: borrow StreamOptions,
    source: borrow dyn EventSource<C>,
    context: borrow C,
) -> Result<StreamEnd, HandlerError>
```

Runs `source` and `EventStream.run` over `queue` together. When `source` returns, the queue is closed and the stream
ends after the queued events. When the stream ends first (the client left, or a write failed), `source` is
cancelled at once, wherever it is waiting.

## struct EventStream

```ceesafe
public struct EventStream
```

Writes event streams.

### EventStream.run

```ceesafe
public async fn run(
    exchange: borrow mut Exchange,
    queue: borrow EventQueue,
    options: borrow StreamOptions,
) -> Result<StreamEnd, HandlerError>
```

Sends the response head (`text/event-stream`, no length, so HTTP/1.1 frames it chunked), then each event from
`queue` as it comes, a heartbeat after each quiet `options.heartbeatMs`, until the queue ends or the client
closes. On any end other than the queue's own, it closes `queue`, so a producer blocked in `publish` wakes
with `Closed`.
