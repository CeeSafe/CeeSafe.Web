/*
 * libFuzzer harness for web_http3's shim (Web ADR 0005 §2.3, ADR 0014 §3.6): a server h3_conn driven as web_http3
 * drives it, with what the peer sends and what the server does read from the input. Invariants: no crash, no
 * sanitizer report, no leak; every call returns 0, a count, or one of nghttp3's negative codes; after a negative
 * h3_read, h3_pull or h3_wrote only h3_conn_free is called (the shim's contract).
 *
 * The input is a sequence of operations, each a byte whose low 3 bits choose it:
 *   0  the peer's bytes: a stream byte (one of STREAMS), a 2-byte length, a FIN byte, then the bytes (h3_read)
 *   1  QUIC takes what nghttp3 has: h3_pull with a capacity byte, h3_wrote with all of it or a part (h3_pull/h3_wrote)
 *   2  a response head on a request stream the peer opened, once per stream, with a body or without (h3_submit)
 *   3  body bytes for it: a length byte, an end bit (h3_send_body)
 *   4  trailers for it, which end it (h3_trailers)
 *   5  the server stops reading a stream, or QUIC closes it with a code, after which nothing more happens on it
 *      (h3_shutdown_read, h3_close_stream)
 *   6  QUIC blocks or unblocks a stream (h3_block, h3_unblock)
 *   7  GOAWAY: the notice, then the final one, each once; or whether it is drained (h3_goaway_notice, h3_goaway,
 *      h3_drained)
 * After each, every event is drained, the first with a buffer of 1 to 64 bytes so H3_EV_TOO_SMALL is exercised.
 *
 * Linux (libFuzzer):
 *   N=third_party/nghttp3-1.19.0; clang -g -O1 -fsanitize=fuzzer,address,undefined -Itests/fuzz/h3 -I$N/config \
 *     -I$N/lib/includes tests/fuzz/h3/fuzz_h3.c packages/web_http3/c/h3_shim.c $(ls packages/web_http3/c/vendor/lib_*) \
 *     -o fuzz_h3
 *   ./fuzz_h3 corpus/ tests/fuzz/h3/seeds/
 * Elsewhere (no libFuzzer): add -DH3_REPLAY and drop "fuzzer," from -fsanitize: the binary replays the files named
 * on its command line. With -DH3_REPLAY, `fuzz_h3 --seeds DIR` writes seeds made by a real nghttp3 client; with
 * -DH3_TRACE as well, each event is printed.
 *
 * ceesafe/foreign.h here stands in for CeeSafe's runtime: the shim's allocations go to malloc.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ceesafe/foreign.h"

void *ceesafe_alloc(size_t size, uint32_t allocator) {
    (void)allocator;
    return malloc(size);
}

void *ceesafe_realloc(void *p, size_t size, uint32_t allocator) {
    (void)allocator;
    return realloc(p, size);
}

void ceesafe_free(void *p, uint32_t allocator) {
    (void)allocator;
    free(p);
}

typedef struct h3_conn h3_conn;
h3_conn *h3_conn_server(uint64_t max_field_section);
h3_conn *h3_conn_client(uint64_t max_field_section);
void h3_conn_free(h3_conn *c);
int32_t h3_bind(h3_conn *c, int64_t control, int64_t encoder, int64_t decoder);
int32_t h3_failed(h3_conn *c);
int64_t h3_read(h3_conn *c, int64_t stream, const uint8_t *bytes, size_t len, int32_t fin, uint64_t now);
int32_t h3_shutdown_read(h3_conn *c, int64_t stream);
int32_t h3_close_stream(h3_conn *c, int64_t stream, uint64_t code);
int64_t h3_pull(h3_conn *c, int64_t *stream, int32_t *fin, uint8_t *out, size_t cap);
int32_t h3_wrote(h3_conn *c, int64_t stream, uint64_t n);
void h3_block(h3_conn *c, int64_t stream);
int32_t h3_unblock(h3_conn *c, int64_t stream);
int32_t h3_submit(h3_conn *c, int64_t stream, int32_t request, const uint8_t *fields, size_t len, int32_t has_body);
int64_t h3_send_body(h3_conn *c, int64_t stream, const uint8_t *bytes, size_t len, int32_t end);
int32_t h3_trailers(h3_conn *c, int64_t stream, const uint8_t *fields, size_t len);
void h3_shutdown_write(h3_conn *c, int64_t stream);
int32_t h3_goaway_notice(h3_conn *c);
int32_t h3_goaway(h3_conn *c);
int32_t h3_drained(h3_conn *c);
uint32_t h3_next_event(h3_conn *c, uint8_t *buf, size_t cap, int64_t *stream, uint32_t *flags, uint64_t *code,
                       size_t *length);

#define H3_EV_FIELDS_END 2u
#define H3_EV_TOO_SMALL 255u

/* The peer's streams: its requests (bidirectional 0, 4, 8, and 400, past the stream limit nghttp3 would see),
   its control, QPACK encoder and decoder streams (2, 6, 10) and one more unidirectional stream (14). The server's
   own control, encoder and decoder are 3, 7 and 11. Server-initiated IDs are not offered: QUIC (ngtcp2) refuses a
   peer's bytes on them before web_http3 sees any, and nghttp3 asserts it is never given them
   (nghttp3_conn_read_stream2). */
static const int64_t STREAMS[] = {0, 4, 8, 2, 6, 10, 14, 400};
#define NSTREAMS (sizeof STREAMS / sizeof STREAMS[0])

#define MAX_REQUESTS 8

typedef struct {
    const uint8_t *data;
    size_t len;
    size_t at;
} input;

static int take(input *in, uint8_t *b) {
    if (in->at >= in->len) {
        return 0;
    }
    *b = in->data[in->at++];
    return 1;
}

typedef struct {
    h3_conn *c;
    int dead; /* a negative h3_read, h3_pull or h3_wrote: only h3_conn_free from now on */
    uint64_t now;
    int64_t requests[MAX_REQUESTS]; /* request streams whose head the peer sent, in order */
    int answered[MAX_REQUESTS];     /* a head was submitted on it: web_http3 sends one per stream (headSent) */
    size_t nrequests;
    int64_t responded; /* the request stream last given a head with a body, or -1 */
    int goaway;        /* 0: none sent, 1: the notice, 2: the final one */
    int closed[NSTREAMS]; /* QUIC closed it (h3_close_stream): stream IDs are never reused (RFC 9000 §2.1), so no
                             more of the peer's bytes on it and no second close */
} server;

static uint8_t event_buf[1u << 17];

static void drain_events(server *s, uint8_t first_cap) {
    size_t cap = (size_t)(first_cap % 64u) + 1u;
    for (int i = 0; i < 100000; i++) {
        int64_t stream = -1;
        uint32_t flags = 0;
        uint64_t code = 0;
        size_t length = 0;
        uint32_t kind = h3_next_event(s->c, event_buf, cap, &stream, &flags, &code, &length);
        if (kind == 0) {
            return;
        }
        if (kind == H3_EV_TOO_SMALL) {
            if (length <= cap || length > sizeof event_buf) {
                abort(); /* the shim said it does not fit, so it must need more, and an event is bounded */
            }
            cap = sizeof event_buf;
            continue;
        }
        if (length > cap) {
            abort();
        }
#ifdef H3_TRACE
        fprintf(stderr, "event %u stream %lld flags %u code %llu length %zu\n", kind, (long long)stream, flags,
                (unsigned long long)code, length);
#endif
        /* A request's head, not its trailers (flags bit 0), as web_http3's server tells them apart: one request per
           stream, so one h3_submit (the nightly run of 2026-11-12 found two, which nghttp3 asserts on). */
        if (kind == H3_EV_FIELDS_END && (flags & 1u) == 0 && (stream & 3) == 0 && s->nrequests < MAX_REQUESTS) {
            s->requests[s->nrequests++] = stream;
        }
        cap = sizeof event_buf;
    }
}

static int64_t pick_request(server *s, uint8_t b) {
    return s->nrequests ? s->requests[b % s->nrequests] : -1;
}

/* A field list as h3_submit takes it: ":status" then up to two fields made of input bytes (2-byte name length,
   4-byte value length, name, value). Names the input supplies may be invalid; the shim must refuse them cleanly. */
static size_t fields(input *in, uint8_t *out, size_t cap, int response) {
    size_t n = 0;
    if (response) {
        const char *name = ":status", *value = "200";
        size_t nl = strlen(name), vl = strlen(value);
        out[n++] = 0;
        out[n++] = (uint8_t)nl;
        out[n++] = 0;
        out[n++] = 0;
        out[n++] = 0;
        out[n++] = (uint8_t)vl;
        memcpy(out + n, name, nl);
        n += nl;
        memcpy(out + n, value, vl);
        n += vl;
    }
    uint8_t count = 0;
    take(in, &count);
    for (uint8_t i = 0; i < count % 3u; i++) {
        uint8_t nl = 0, vl = 0;
        take(in, &nl);
        take(in, &vl);
        nl %= 16u;
        if (n + 6u + nl + vl > cap) {
            break;
        }
        out[n++] = 0;
        out[n++] = nl;
        out[n++] = 0;
        out[n++] = 0;
        out[n++] = 0;
        out[n++] = vl;
        for (size_t k = 0; k < (size_t)nl + vl; k++) {
            uint8_t b = 'a';
            take(in, &b);
            out[n++] = b;
        }
    }
    return n;
}

static void step(server *s, input *in, uint8_t op) {
    uint8_t a = 0, b = 0;
    switch (op & 7u) {
    case 0: {
        uint8_t which = 0, hi = 0, lo = 0, fin = 0;
        take(in, &which);
        take(in, &hi);
        take(in, &lo);
        take(in, &fin);
        size_t want = ((size_t)hi << 8) | lo;
        size_t left = in->len - in->at;
        size_t n = want < left ? want : left;
        if (s->closed[which % NSTREAMS]) {
            in->at += n; /* ngtcp2 drops frames for a stream it closed; web_http3 never sees them */
            break;
        }
        s->now += 1000000u;
        int64_t rv = h3_read(s->c, STREAMS[which % NSTREAMS], in->data + in->at, n, fin & 1, s->now);
#ifdef H3_TRACE
        fprintf(stderr, "read stream %lld len %zu fin %d -> %lld\n", (long long)STREAMS[which % NSTREAMS], n, fin & 1,
                (long long)rv);
#endif
        in->at += n;
        if (rv < 0) {
            s->dead = 1;
        } else if ((size_t)rv > n) {
            abort(); /* consumed more than it was given */
        }
        break;
    }
    case 1: {
        take(in, &a);
        take(in, &b);
        static uint8_t out[4096];
        size_t cap = a ? (size_t)a * 16u : sizeof out;
        for (int i = 0; i < 64 && !s->dead; i++) {
            int64_t stream = -1;
            int32_t fin = 0;
            int64_t n = h3_pull(s->c, &stream, &fin, out, cap);
            if (n < 0) {
                s->dead = 1;
                break;
            }
            if (stream < 0) {
                break;
            }
            if ((uint64_t)n > cap) {
                abort();
            }
            /* QUIC may take less than it was offered: b's low bit takes half. */
            uint64_t took = (b & 1u) && n > 1 ? (uint64_t)n / 2u : (uint64_t)n;
#ifdef H3_TRACE
            fprintf(stderr, "pull stream %lld got %lld fin %d, wrote %llu\n", (long long)stream, (long long)n, fin,
                    (unsigned long long)took);
#endif
            if (h3_wrote(s->c, stream, took) < 0) {
                s->dead = 1;
            }
            if (n == 0 && !fin) {
                break;
            }
        }
        break;
    }
    case 2: {
        take(in, &a);
        take(in, &b);
        if (s->nrequests == 0 || s->answered[a % s->nrequests]) {
            break;
        }
        s->answered[a % s->nrequests] = 1;
        int64_t stream = s->requests[a % s->nrequests];
        uint8_t f[512];
        size_t n = fields(in, f, sizeof f, 1);
        int has_body = b & 1u;
        int32_t rv = h3_submit(s->c, stream, 0, f, n, has_body);
#ifdef H3_TRACE
        fprintf(stderr, "submit stream %lld fields %zu body %d -> %d\n", (long long)stream, n, has_body, rv);
#endif
        if (rv == 0 && has_body) {
            s->responded = stream;
        }
        break;
    }
    case 3: {
        take(in, &a);
        if (s->responded < 0) {
            break;
        }
        size_t want = (size_t)(a >> 1) * 64u;
        size_t left = in->len - in->at;
        size_t n = want < left ? want : left;
        int64_t rv = h3_send_body(s->c, s->responded, in->data + in->at, n, a & 1u);
#ifdef H3_TRACE
        fprintf(stderr, "body stream %lld len %zu end %d -> %lld\n", (long long)s->responded, n, a & 1u, (long long)rv);
#endif
        in->at += n;
        if (rv > (int64_t)n) {
            abort();
        }
        break;
    }
    case 4: {
        if (s->responded < 0) {
            break;
        }
        uint8_t f[512];
        size_t n = fields(in, f, sizeof f, 0);
        int32_t rv = h3_trailers(s->c, s->responded, f, n);
#ifdef H3_TRACE
        fprintf(stderr, "trailers stream %lld -> %d\n", (long long)s->responded, rv);
#else
        (void)rv;
#endif
        s->responded = -1; /* trailers end the response: no body after them */
        break;
    }
    case 5: {
        take(in, &a);
        take(in, &b);
        int64_t stream = STREAMS[a % NSTREAMS];
        if (s->closed[a % NSTREAMS]) {
            break; /* ngtcp2 reports a stream's close once, and nothing on it after (glue.cee: StreamClosed) */
        }
#ifdef H3_TRACE
        fprintf(stderr, "%s stream %lld\n", (b & 1u) ? "close" : "shutdown_read", (long long)stream);
#endif
        if (b & 1u) {
            s->closed[a % NSTREAMS] = 1;
            (void)h3_close_stream(s->c, stream, 0x100u + (b >> 1));
        } else {
            (void)h3_shutdown_read(s->c, stream);
        }
        if (stream == s->responded) {
            s->responded = -1;
        }
        break;
    }
    case 6: {
        take(in, &a);
        take(in, &b);
        int64_t stream = pick_request(s, a);
        if (stream < 0) {
            break;
        }
#ifdef H3_TRACE
        fprintf(stderr, "%s stream %lld\n", (b & 1u) ? "block" : "unblock", (long long)stream);
#endif
        if (b & 1u) {
            h3_block(s->c, stream);
        } else {
            (void)h3_unblock(s->c, stream);
        }
        break;
    }
    case 7: {
        /* As web_http3's drain: the notice once, then the final GOAWAY once (nghttp3 asserts a GOAWAY's ID never
           grows, so neither may be repeated or reordered); h3_drained at any time. */
        take(in, &a);
        if ((a & 1u) && s->goaway < 2) {
            (void)(s->goaway == 0 ? h3_goaway_notice(s->c) : h3_goaway(s->c));
            s->goaway += 1;
        } else {
            (void)h3_drained(s->c);
        }
        break;
    }
    }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t len) {
    server s;
    memset(&s, 0, sizeof s);
    s.responded = -1;
    s.c = h3_conn_server(16384);
    if (!s.c) {
        return 0;
    }
    if (h3_bind(s.c, 3, 7, 11) != 0) {
        h3_conn_free(s.c);
        return 0;
    }
    input in = {data, len, 0};
    uint8_t op = 0;
    while (!s.dead && take(&in, &op)) {
        step(&s, &in, op);
        if (!s.dead) {
            drain_events(&s, op >> 3);
        }
        if (h3_failed(s.c)) {
            break;
        }
    }
    h3_conn_free(s.c);
    return 0;
}

#ifdef H3_REPLAY
/* ---- Seeds: what a real nghttp3 client sends, as operations 0 ---- */

static FILE *seed_out;

static void put_read(int64_t stream, const uint8_t *bytes, size_t len, int fin) {
    size_t which = 0;
    while (which < NSTREAMS && STREAMS[which] != stream) {
        which++;
    }
    if (which == NSTREAMS) {
        return;
    }
    for (size_t at = 0; at < len || (fin && at == 0 && len == 0);) {
        size_t n = len - at > 0xffffu ? 0xffffu : len - at;
        int last = at + n == len;
        uint8_t head[5] = {0, (uint8_t)which, (uint8_t)(n >> 8), (uint8_t)n, (uint8_t)(fin && last)};
        fwrite(head, 1, sizeof head, seed_out);
        fwrite(bytes + at, 1, n, seed_out);
        at += n;
        if (n == 0) {
            break;
        }
    }
}

/* A client request on stream 0: the given fields, and a body of `body` bytes if any. Every byte the client's
   nghttp3 produces is written as the peer's bytes, then `tail` (server operations) is appended. */
static int write_seed(const char *dir, const char *name, const uint8_t *f, size_t flen, size_t body,
                      const uint8_t *tail, size_t tail_len) {
    char path[1024];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    seed_out = fopen(path, "wb");
    if (!seed_out) {
        return 1;
    }
    h3_conn *c = h3_conn_client(16384);
    if (!c || h3_bind(c, 2, 6, 10) != 0 || h3_submit(c, 0, 1, f, flen, body > 0) != 0) {
        fclose(seed_out);
        return 1;
    }
    if (body) {
        static uint8_t buf[1u << 16];
        memset(buf, 'x', sizeof buf);
        size_t sent = 0;
        while (sent < body) {
            size_t n = body - sent < sizeof buf ? body - sent : sizeof buf;
            int64_t took = h3_send_body(c, 0, buf, n, sent + n == body);
            if (took <= 0) {
                break;
            }
            sent += (size_t)took;
            static uint8_t out[65536];
            for (int i = 0; i < 1000; i++) {
                int64_t stream = -1;
                int32_t fin = 0;
                int64_t m = h3_pull(c, &stream, &fin, out, sizeof out);
                if (m < 0 || stream < 0) {
                    break;
                }
                put_read(stream, out, (size_t)m, fin);
                h3_wrote(c, stream, (uint64_t)m);
            }
        }
    }
    static uint8_t out[65536];
    for (int i = 0; i < 1000; i++) {
        int64_t stream = -1;
        int32_t fin = 0;
        int64_t m = h3_pull(c, &stream, &fin, out, sizeof out);
        if (m < 0 || stream < 0) {
            break;
        }
        put_read(stream, out, (size_t)m, fin);
        h3_wrote(c, stream, (uint64_t)m);
    }
    fwrite(tail, 1, tail_len, seed_out);
    h3_conn_free(c);
    fclose(seed_out);
    return 0;
}

static size_t field(uint8_t *out, const char *name, const char *value) {
    size_t nl = strlen(name), vl = strlen(value), n = 0;
    out[n++] = (uint8_t)(nl >> 8);
    out[n++] = (uint8_t)nl;
    out[n++] = (uint8_t)(vl >> 24);
    out[n++] = (uint8_t)(vl >> 16);
    out[n++] = (uint8_t)(vl >> 8);
    out[n++] = (uint8_t)vl;
    memcpy(out + n, name, nl);
    n += nl;
    memcpy(out + n, value, vl);
    return n + vl;
}

static int seeds(const char *dir) {
    uint8_t f[1024];
    size_t n = 0;
    n += field(f + n, ":method", "GET");
    n += field(f + n, ":scheme", "https");
    n += field(f + n, ":authority", "localhost");
    n += field(f + n, ":path", "/");
    /* Respond with a body, send some, end it; then drain (1), GOAWAY (7). */
    const uint8_t respond[] = {1, 0, 0, 2, 0, 1, 0, 3, 9, 1, 0, 0, 7, 1, 1, 0, 0};
    int bad = write_seed(dir, "get", f, n, 0, respond, sizeof respond);
    uint8_t p[1024];
    size_t m = 0;
    m += field(p + m, ":method", "POST");
    m += field(p + m, ":scheme", "https");
    m += field(p + m, ":authority", "localhost");
    m += field(p + m, ":path", "/echo");
    m += field(p + m, "content-type", "application/octet-stream");
    m += field(p + m, "x-long", "a fairly long header value, so QPACK has a literal to encode and maybe Huffman");
    const uint8_t trailers[] = {1, 0, 1, 2, 0, 1, 0, 3, 8, 4, 1, 3, 4, 'x', '-', 't', 'z', 'q', 'v', 'w', 1, 0, 0};
    bad |= write_seed(dir, "post", p, m, 3000, trailers, sizeof trailers);
    const uint8_t reset[] = {5, 0, 1, 1, 0, 0};
    bad |= write_seed(dir, "post-reset", p, m, 200, reset, sizeof reset);
    return bad;
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "--seeds") == 0) {
        return seeds(argv[2]);
    }
    int bad = 0;
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (!f) {
            fprintf(stderr, "cannot open %s\n", argv[i]);
            bad = 1;
            continue;
        }
        fseek(f, 0, SEEK_END);
        long size = ftell(f);
        fseek(f, 0, SEEK_SET);
        uint8_t *buf = malloc(size > 0 ? (size_t)size : 1u);
        size_t got = buf ? fread(buf, 1, (size_t)size, f) : 0;
        fclose(f);
        if (buf) {
            LLVMFuzzerTestOneInput(buf, got);
            free(buf);
        }
        printf("replayed %s (%zu bytes)\n", argv[i], got);
    }
    return bad;
}
#endif
