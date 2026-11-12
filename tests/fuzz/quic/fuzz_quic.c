/*
 * libFuzzer harness for web_quic's shim over ngtcp2 (Web ADR 0005 §2.3, ADR 0014 §3.4, ADR 0015 §3): a client and a
 * server qs_conn, made and driven as web_quic makes and drives them, joined by a network the input controls. The
 * input delivers, drops, duplicates, reorders and corrupts their datagrams, moves the clock, injects datagrams of its
 * own, and opens, writes, resets and closes streams on either side.
 *
 * Packet protection is what keeps a fuzzer's bytes out of a QUIC connection. The first input byte chooses:
 *   - real protection (bit 0 clear): the shim as it ships. Corrupted and injected datagrams stop at the AEAD, so
 *     what is reached is header parsing, routing, Retry, Version Negotiation, stateless resets, loss, reordering and
 *     timers. Stream data must then arrive exactly as sent: each side writes a known pattern, and the other checks
 *     every byte, its order, and that nothing comes after a FIN or past what was written;
 *   - null protection (bit 0 set): the harness, which compiles the shim into itself, replaces its three packet
 *     protection callbacks (encrypt, decrypt, header protection) with ones that copy (the "null" AEAD of ngtcp2's own
 *     tests). The TLS handshake still runs, over CRYPTO frames anyone can now read and change, so a corrupted datagram
 *     or one the input writes reaches ngtcp2's frame parsing, its stream and flow-control state, and every callback
 *     of the shim, as an authenticated but hostile peer would.
 * The shim's source is not changed: the replacement is three macros, defined before it is included.
 *
 * Invariants: no crash, no sanitizer report, no leak (every allocation of the shim and ngtcp2 is counted, and must
 * be freed when both connections are); every call returns a length within its buffer, 0, or a negative ngtcp2 code;
 * every event and ID change is of a known kind and fits its buffer; with real protection, the stream data above.
 * After an error from qs_read, qs_write or qs_handle_expiry, a side is closed as web_quic closes it: qs_close (unless
 * the peer closed it or it timed out), then only qs_conn_free (src/server.cee).
 *
 * The input, after its first byte (bits: 0 null protection, 1 the listener requires a Retry, 2 and 3 the server's
 * and the client's stream credit given back only by qs_consume, 4 the first event of each drain read with a 16-byte
 * buffer, for QS_EV_TOO_SMALL, 5 a 2-byte number follows: that allocation fails, 6 the server accepts 0-RTT, as
 * web_quic's allowEarlyData makes it), is a sequence of operations, each
 * a byte whose low 4 bits choose it and whose high 4 bits are its argument `a` (bit 0 of `a`: the direction, client
 * to server or back, or the side):
 *   0  deliver up to (a >> 1) + 1 datagrams        8  inject a datagram: a length byte, then the bytes
 *   1  drop one                                    9  open a stream (a bit 1: unidirectional)
 *   2  corrupt one, then deliver it: a 2-byte      10 write the next pattern bytes on a stream: an index byte, a
 *      offset and an XOR byte                         length byte (bit 7: in 16 KiB units, else 64 bytes); a bit 1:
 *   3  deliver a copy, keeping it                     FIN
 *   4  move one to the back                        11 an index byte, then a bits 1-2: reset, reset the sending side,
 *   5  advance the clock: a byte of 1 ms units          stop reading, or give back a byte's worth of 256-byte credit
 *      (a bit 1: of 100 ms), then run timers due   12 the client moves to a new local port (a byte)
 *   6  write a short-header packet with the frames 13 close a side (a bit 1: with the application's code)
 *      that follow (a first-byte byte, a length    14 the listener's functions on a datagram that follows (a length
 *      byte, the frames), to that side's ID           byte): qs_packet_dcid, qs_version_negotiation, qs_retry_check,
 *   7  the same with a long header: a bits 1-2 its    qs_stateless_reset
 *      type (Initial, 0-RTT, Handshake), padded    15 ask a side what web_quic asks: qs_all_acked, qs_pto,
 *      to 1200 bytes                                   qs_peer_close, qs_alpn, qs_ticket and the rest; on
 *                                                      the client with a bit 1 set, reconnect instead (below)
 * After each operation, both sides write all they have (qs_write) and drain their events and ID changes.
 * Reconnecting (op 15, a = 3, 7, 11 or 15) is what a client of web_quic does on its next visit: it keeps the ticket
 * qs_ticket gives, both connections are freed, and a new client resumes with qs_resume from the newest ticket (a bit
 * 2: the first one, again: a replay; a bit 3: none, a full handshake). Streams it opens and writes before its
 * handshake ends go out as 0-RTT. Invariants of resumption, checked as each connection ends:
 *   - after both handshakes, the client and the server agree on qs_resumed and on qs_early_accepted;
 *   - a server without 0-RTT never accepts early data, and early data is accepted only on a resumed connection;
 *   - with 0-RTT on, a ticket resumes at most one connection: OpenSSL's tickets are single-use then, its replay
 *     protection (Web ADR 0014 §3.7 and amendment 5 item 5), and the replay gets a full handshake;
 *   - qs_resume takes a ticket of qs_ticket's: 1 when the server allows 0-RTT, else 0.
 * Early data the server rejects is sent again by ngtcp2 in 1-RTT, so the stream data check above holds through it.
 * The server's session cache, where the single-use tickets are, is emptied before each input.
 * Time and randomness are the input's: the clock starts at a fixed value, and OpenSSL's random bytes (and so ngtcp2's
 * and the handshake's) come from a generator seeded the same for every input, so an input replays exactly.
 *
 * Linux (libFuzzer), with OpenSSL 3.5 or later:
 *   T=third_party/ngtcp2-1.26.0; clang -g -O1 -fsanitize=fuzzer,address,undefined -DWT_STANDALONE \
 *     -Itests/fuzz/quic -Ipackages/web_quic/c -I$T/config -I$T/lib/includes -I$T/crypto/includes -I$T/lib -I$T/crypto \
 *     tests/fuzz/quic/fuzz_quic.c packages/web_tls/c/tls_shim.c packages/web_quic/c/vendor/[a-z]*.c -lssl -lcrypto \
 *     -o fuzz_quic
 *   C=packages/web_tls/tests/certs; CW_CERT=$C/server.pem CW_KEY=$C/server.key CW_CA=$C/ca.pem \
 *     ./fuzz_quic corpus/ tests/fuzz/quic/seeds/
 * Elsewhere (no libFuzzer): add -DQS_REPLAY and drop "fuzzer," from -fsanitize: the binary replays the files named on
 * its command line; `fuzz_quic --seeds DIR` writes the seeds. With -DQS_TRACE as well, each step is printed.
 */
#define OPENSSL_SUPPRESS_DEPRECATED 1 /* RAND_set_rand_method: the deterministic generator */
#define NGTCP2_STATICLIB 1
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- Null packet protection, chosen per input ---- */

static int fz_null;

static int fz_encrypt(uint8_t *dest, const ngtcp2_crypto_aead *aead, const ngtcp2_crypto_aead_ctx *ctx,
                      const uint8_t *plaintext, size_t plaintextlen, const uint8_t *nonce, size_t noncelen,
                      const uint8_t *aad, size_t aadlen) {
    if (!fz_null) {
        return ngtcp2_crypto_encrypt_cb(dest, aead, ctx, plaintext, plaintextlen, nonce, noncelen, aad, aadlen);
    }
    memmove(dest, plaintext, plaintextlen);
    memset(dest + plaintextlen, 0, aead->max_overhead);
    return 0;
}

static int fz_decrypt(uint8_t *dest, const ngtcp2_crypto_aead *aead, const ngtcp2_crypto_aead_ctx *ctx,
                      const uint8_t *ciphertext, size_t ciphertextlen, const uint8_t *nonce, size_t noncelen,
                      const uint8_t *aad, size_t aadlen) {
    if (!fz_null) {
        return ngtcp2_crypto_decrypt_cb(dest, aead, ctx, ciphertext, ciphertextlen, nonce, noncelen, aad, aadlen);
    }
    if (ciphertextlen < aead->max_overhead) {
        return NGTCP2_ERR_DECRYPT;
    }
    memmove(dest, ciphertext, ciphertextlen - aead->max_overhead);
    return 0;
}

static int fz_hp_mask(uint8_t *dest, const ngtcp2_crypto_cipher *hp, const ngtcp2_crypto_cipher_ctx *ctx,
                      const uint8_t *sample) {
    if (!fz_null) {
        return ngtcp2_crypto_hp_mask_cb(dest, hp, ctx, sample);
    }
    memset(dest, 0, NGTCP2_HP_MASKLEN);
    return 0;
}

#define ngtcp2_crypto_encrypt_cb fz_encrypt
#define ngtcp2_crypto_decrypt_cb fz_decrypt
#define ngtcp2_crypto_hp_mask_cb fz_hp_mask
#include "quic_shim.c"
#undef ngtcp2_crypto_encrypt_cb
#undef ngtcp2_crypto_decrypt_cb
#undef ngtcp2_crypto_hp_mask_cb

/* ---- CeeSafe's allocator for C, counted, with one allocation made to fail when the input asks ---- */

static long fz_live;
static unsigned long fz_allocs, fz_fail_at;

static int fz_fails(void) {
    fz_allocs += 1;
    return fz_fail_at != 0 && fz_allocs == fz_fail_at;
}

void *ceesafe_alloc(size_t size, uint32_t allocator) {
    (void)allocator;
    if (fz_fails()) {
        return NULL;
    }
    void *p = malloc(size);
    if (p) {
        fz_live += 1;
    }
    return p;
}

void *ceesafe_realloc(void *p, size_t size, uint32_t allocator) {
    (void)allocator;
    if (fz_fails()) {
        return NULL;
    }
    void *q = realloc(p, size);
    if (q && !p) {
        fz_live += 1;
    }
    return q;
}

void ceesafe_free(void *p, uint32_t allocator) {
    (void)allocator;
    if (p) {
        fz_live -= 1;
    }
    free(p);
}

/* web_tls's shim, linked with WT_STANDALONE (OpenSSL keeps its own allocator). */
SSL_CTX *wt_cfg_new(int32_t server);
void wt_cfg_free(SSL_CTX *ctx);
int32_t wt_cfg_use_pem(SSL_CTX *ctx, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len);
int32_t wt_cfg_set_alpn(SSL_CTX *ctx, const uint8_t *wire, size_t len);
int32_t wt_cfg_trust_pem(SSL_CTX *ctx, const uint8_t *pem, size_t len);

/* ---- Deterministic randomness: OpenSSL's, and through it ngtcp2's and the handshake's ---- */

static uint64_t fz_seed;

static int fz_rand_bytes(unsigned char *buf, int num) {
    for (int i = 0; i < num; i++) {
        fz_seed += 0x9e3779b97f4a7c15ull; /* splitmix64 */
        uint64_t z = fz_seed;
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
        buf[i] = (unsigned char)(z ^ (z >> 31));
    }
    return 1;
}

static int fz_rand_status(void) {
    return 1;
}

static RAND_METHOD fz_rand = {NULL, fz_rand_bytes, NULL, NULL, fz_rand_bytes, fz_rand_status};

/* ---- Failing loudly ---- */

#ifdef QS_TRACE
#  define TRACE(...) fprintf(stderr, __VA_ARGS__)
#else
#  define TRACE(...) ((void)0)
#endif

static void broken(const char *what, long a, long b) {
    fprintf(stderr, "fuzz_quic: invariant broken: %s (%ld, %ld)\n", what, a, b);
    abort();
}

/* ---- The two sides and the network between them ---- */

#define DGRAM 1452u /* web_quic's quicDatagramSize() */
#define QUEUE 64u   /* datagrams waiting per direction; past it, the newest is lost */
#define STREAMS 64
#define NOW0 1000000000000ull

typedef struct dgram {
    uint8_t *bytes;
    size_t len;
    uint8_t from[QS_ADDR_BYTES];
    uint32_t ecn;
} dgram;

typedef struct link {
    dgram q[QUEUE];
    size_t count;
} link;

typedef struct strm {
    int64_t id;
    int can_send;   /* ours, or the peer's bidirectional one; not reset or ended by us */
    int can_read;   /* the peer's, or our bidirectional one */
    int closed;     /* QS_EV_STREAM_CLOSE: nothing more is done with it (Web ADR 0014 §3.4) */
    uint64_t sent;  /* pattern bytes the shim took */
    uint64_t recv;  /* bytes received, all checked */
    int fin_recv;
} strm;

typedef struct side {
    qs_conn *c;
    int dead; /* closed: only qs_conn_free from now on */
    int server;
    uint8_t local[QS_ADDR_BYTES];
    uint8_t scid[20];
    uint8_t secret[32];
    strm s[STREAMS];
    int ns;
    uint64_t pn; /* the next packet number the harness writes to this side */
} side;

static SSL_CTX *server_cfg, *client_cfg;
static qs_tls *server_tls, *client_tls;
static side cl, sv;
static link to_server, to_client; /* to_server: datagrams the client sent */
static uint64_t now;
static uint8_t retry_key[32], retry_scid[20], client_dcid[18];
static int require_retry, small_first, server_manual, client_manual, server_early;

/* Resumption tickets the client has been given (qs_ticket), each once; `used`: a connection resumed with it. */
typedef struct ticket {
    uint8_t b[16384];
    size_t len;
    int used;
} ticket;
#define TICKETS 8
static ticket tickets[TICKETS];
static int ntickets;
static int cl_from; /* the ticket the client's connection resumed from, or -1 */
static const uint8_t *in;
static size_t in_len, at;
static uint8_t pattern[2u * 1024u * 1024u + 64u];

static unsigned take(void) {
    return at < in_len ? in[at++] : 0u;
}

static int more(void) {
    return at < in_len;
}

static void addr(uint8_t *out, uint8_t last, uint16_t port) {
    memset(out, 0, QS_ADDR_BYTES);
    out[0] = 4;
    out[1] = (uint8_t)(port >> 8);
    out[2] = (uint8_t)port;
    out[3] = 10;
    out[6] = last;
}

static uint8_t pat(int64_t id, uint64_t off) {
    return (uint8_t)(off * 31u + (uint64_t)id * 7u + (off >> 8));
}

static strm *find(side *x, int64_t id) {
    for (int i = 0; i < x->ns; i++) {
        if (x->s[i].id == id) {
            return &x->s[i];
        }
    }
    return NULL;
}

static strm *add(side *x, int64_t id) {
    strm *s = find(x, id);
    if (s || x->ns == STREAMS) {
        return s;
    }
    s = &x->s[x->ns++];
    memset(s, 0, sizeof *s);
    s->id = id;
    int ours = x->server ? (id & 1) == 1 : (id & 1) == 0;
    int uni = (id & 2) != 0;
    s->can_send = ours || !uni;
    s->can_read = !ours || !uni;
    return s;
}

static void push(link *l, const uint8_t *bytes, size_t len, const uint8_t *from, uint32_t ecn) {
    if (l->count == QUEUE || len == 0) {
        return;
    }
    dgram *d = &l->q[l->count];
    d->bytes = malloc(len);
    if (!d->bytes) {
        return;
    }
    memcpy(d->bytes, bytes, len);
    d->len = len;
    memcpy(d->from, from, QS_ADDR_BYTES);
    d->ecn = ecn;
    l->count += 1;
}

/* Removes the front datagram into *d (the caller frees its bytes): 0 if there is none. */
static int pop(link *l, dgram *d) {
    if (l->count == 0) {
        return 0;
    }
    *d = l->q[0];
    memmove(l->q, l->q + 1, (l->count - 1) * sizeof l->q[0]);
    l->count -= 1;
    return 1;
}

static void link_clear(link *l) {
    for (size_t i = 0; i < l->count; i++) {
        free(l->q[i].bytes);
    }
    l->count = 0;
}

/* ---- What web_quic does on each result ---- */

/* A side whose call failed with `rv` is closed: a CONNECTION_CLOSE first, unless the peer closed it (draining) or it
   timed out, as src/server.cee closes. */
static void fail(side *x, int64_t rv) {
    if (rv >= 0 || rv < -1000) {
        broken("not an ngtcp2 error code", (long)rv, x->server);
    }
    TRACE("  %s failed: %lld\n", x->server ? "server" : "client", (long long)rv);
    if (rv != NGTCP2_ERR_DRAINING && rv != NGTCP2_ERR_IDLE_CLOSE) {
        uint8_t out[DGRAM];
        int64_t n = qs_close(x->c, 0, 0, out, sizeof out, now);
        if (n > (int64_t)sizeof out) {
            broken("qs_close past its buffer", (long)n, x->server);
        }
        if (n > 0) {
            push(x->server ? &to_client : &to_server, out, (size_t)n, x->local, 0);
        }
    }
    x->dead = 1;
}

static void check_data(side *x, int64_t id, const uint8_t *data, size_t len, int fin) {
    strm *s = add(x, id);
    if (!s) {
        return;
    }
    if (s->fin_recv && (len > 0 || fin)) {
        broken("stream data after its FIN", (long)id, (long)len);
    }
    if (!fz_null) {
        side *peer = x->server ? &cl : &sv;
        strm *p = find(peer, id);
        if (!p) {
            broken("data on a stream the peer never wrote", (long)id, (long)len);
        }
        if (s->recv + len > p->sent) {
            broken("more stream data than was written", (long)id, (long)(s->recv + len));
        }
        for (size_t i = 0; i < len; i++) {
            if (data[i] != pat(id, s->recv + i)) {
                broken("stream data changed", (long)id, (long)(s->recv + i));
            }
        }
    }
    s->recv += len;
    if (fin) {
        s->fin_recv = 1;
    }
}

static void drain(side *x) {
    static uint8_t buf[QS_MAX_DATAGRAM];
    int first = 1;
    for (;;) {
        size_t cap = (first && small_first) ? 16u : sizeof buf;
        first = 0;
        int64_t stream = -2;
        uint32_t flags = 0;
        uint64_t code = 0;
        size_t length = 0;
        uint32_t kind = qs_next_event(x->c, buf, cap, &stream, &flags, &code, &length);
        if (kind == 0) {
            break;
        }
        if (kind == QS_EV_TOO_SMALL) {
            if (length <= cap || length > QS_MAX_DATAGRAM) {
                broken("QS_EV_TOO_SMALL with a length that fits, or too long", (long)length, (long)cap);
            }
            continue; /* the next pass has the full buffer */
        }
        if (length > cap) {
            broken("event data past its buffer", (long)length, (long)cap);
        }
        TRACE("  %s event %u stream %lld len %zu flags %u code %llu\n", x->server ? "server" : "client", kind,
              (long long)stream, length, flags, (unsigned long long)code);
        switch (kind) {
        case QS_EV_HANDSHAKE_DONE:
        case QS_EV_STREAMS_BIDI:
        case QS_EV_STREAM_WRITABLE:
        case QS_EV_EARLY:
        case QS_EV_STOP_SENDING:
            break;
        case QS_EV_STREAM_DATA:
            check_data(x, stream, buf, length, (flags & 1u) != 0);
            break;
        case QS_EV_STREAM_OPEN:
            (void)add(x, stream);
            break;
        case QS_EV_STREAM_RESET: {
            strm *s = add(x, stream);
            if (s) {
                s->fin_recv = 1; /* nothing more arrives on it */
            }
            break;
        }
        case QS_EV_STREAM_CLOSE: {
            strm *s = add(x, stream);
            if (s) {
                s->closed = 1;
                s->can_send = 0;
            }
            break;
        }
        default:
            broken("unknown event", (long)kind, x->server);
        }
    }
    uint8_t id[NGTCP2_MAX_CIDLEN];
    size_t length = 0;
    for (;;) {
        uint32_t kind = qs_next_id(x->c, id, sizeof id, &length);
        if (kind == 0) {
            break;
        }
        if ((kind != QS_EV_NEW_CID && kind != QS_EV_RETIRE_CID) || length > NGTCP2_MAX_CIDLEN || !x->server) {
            broken("unknown ID change, or one too long, or a client's", (long)kind, (long)length);
        }
    }
}

static int live(side *x) {
    return x->c && !x->dead;
}

/* Everything a side has to send, then its events. */
static void pump(side *x) {
    if (!live(x)) {
        return;
    }
    for (int i = 0; i < 32; i++) {
        uint8_t out[DGRAM];
        uint8_t to[QS_ADDR_BYTES];
        uint32_t ecn = 0;
        int64_t n = qs_write(x->c, out, sizeof out, now, &ecn, to);
        if (n < 0) {
            drain(x);
            fail(x, n);
            return;
        }
        if (n > (int64_t)sizeof out) {
            broken("qs_write past its buffer", (long)n, x->server);
        }
        if (n == 0) {
            break;
        }
        push(x->server ? &to_client : &to_server, out, (size_t)n, x->local, ecn);
    }
    drain(x);
}

static void feed(side *x, const dgram *d) {
    int32_t rv = qs_read(x->c, d->from, d->ecn, d->bytes, d->len, now);
    drain(x);
    if (rv != 0) {
        fail(x, rv);
    }
}

/* A datagram reaches the server's listener: routed to the connection, or, before there is one, through Version
   Negotiation, Retry and qs_conn_server; after it is gone, answered with a stateless reset (src/connection.cee). */
static void to_listener(const dgram *d) {
    if (live(&sv)) {
        feed(&sv, d);
        return;
    }
    uint8_t dcid[20], out[DGRAM];
    int32_t initial = 0;
    uint32_t version = 0;
    int32_t n = qs_packet_dcid(d->bytes, d->len, dcid, &initial, &version);
    if (n < -1 || n > 20) {
        broken("qs_packet_dcid", n, 0);
    }
    if (n < 0) {
        return;
    }
    if (sv.c) { /* closed: a stateless reset for a short header */
        if ((d->bytes[0] & 0x80) == 0) {
            int64_t m = qs_stateless_reset(sv.secret, dcid, (size_t)n, d->len, out, sizeof out);
            if (m < 0 || m > 43 || (m > 0 && (size_t)m >= d->len)) {
                broken("qs_stateless_reset", (long)m, (long)d->len);
            }
            if (m > 0) {
                push(&to_client, out, (size_t)m, sv.local, 0);
            }
        }
        return;
    }
    if (!initial) {
        if (version != 0 && version != NGTCP2_PROTO_VER_V1) {
            int64_t m = qs_version_negotiation(d->bytes, d->len, out, sizeof out);
            if (m < 0 || m > (int64_t)sizeof out) {
                broken("qs_version_negotiation", (long)m, 0);
            }
            if (m > 0) {
                push(&to_client, out, (size_t)m, sv.local, 0);
            }
        }
        return;
    }
    size_t outlen = 0, odcidlen = 0;
    uint8_t odcid[NGTCP2_MAX_CIDLEN], fresh[20];
    RAND_bytes(fresh, sizeof fresh); /* a new ID for each Retry, as QuicRetry.check draws (src/connection.cee) */
    int32_t r = qs_retry_check(retry_key, d->bytes, d->len, d->from, require_retry, fresh, now, out, sizeof out,
                               &outlen, odcid, &odcidlen);
    if (r > 3 || outlen > sizeof out || odcidlen > NGTCP2_MAX_CIDLEN || (r == 1) != (outlen > 0)) {
        broken("qs_retry_check", r, (long)outlen);
    }
    TRACE("  listener: Initial, retry check %d\n", r);
    if (r == 1) {
        push(&to_client, out, outlen, sv.local, 0);
        return;
    }
    if (r != 2 && r != 3) {
        return;
    }
    int32_t error = 0;
    sv.c = qs_conn_server(server_tls, d->bytes, d->len, sv.local, d->from, sv.scid, sizeof sv.scid, sv.secret, odcid,
                          r == 2 ? odcidlen : 0, now, &error);
    if (!sv.c) {
        if (error >= 0 && error != -1) {
            broken("qs_conn_server failed without an error", error, 0);
        }
        return;
    }
    if (server_manual) {
        qs_set_manual_credit(sv.c);
    }
    feed(&sv, d);
}

static void deliver(link *l, const dgram *d) {
    TRACE("  deliver %zu bytes to the %s\n", d->len, l == &to_server ? "server" : "client");
    if (l == &to_server) {
        to_listener(d);
    } else if (live(&cl)) {
        feed(&cl, d);
    }
}

/* ---- Datagrams the harness writes: a short or long header to `x`'s ID, then the frames, then a null AEAD tag ---- */

static size_t varint2(uint8_t *p, size_t v) {
    p[0] = (uint8_t)(0x40 | ((v >> 8) & 0x3f));
    p[1] = (uint8_t)v;
    return 2;
}

static void craft(side *x, int is_long, unsigned type, unsigned first, const uint8_t *frames, size_t flen) {
    static uint8_t b[DGRAM + 64];
    size_t pnlen = (first & 3u) + 1u;
    side *peer = x->server ? &cl : &sv;
    const uint8_t *dcid = x->scid;
    size_t dcidlen = sizeof x->scid;
    if (x->server && !sv.c) { /* before the server exists, the client's first destination ID */
        dcid = client_dcid;
        dcidlen = sizeof client_dcid;
    }
    if (flen > 1200) {
        flen = 1200;
    }
    size_t body = pnlen + flen;
    size_t pad = body < 4 ? 4 - body : 0; /* header protection samples 4 bytes past the packet number's start */
    size_t at_ = 0;
    if (is_long) {
        b[at_++] = (uint8_t)(0xc0 | ((type & 3u) << 4) | (first & 3u));
        b[at_++] = 0;
        b[at_++] = 0;
        b[at_++] = 0;
        b[at_++] = 1;
        b[at_++] = (uint8_t)dcidlen;
        memcpy(b + at_, dcid, dcidlen);
        at_ += dcidlen;
        b[at_++] = (uint8_t)sizeof peer->scid;
        memcpy(b + at_, peer->scid, sizeof peer->scid);
        at_ += sizeof peer->scid;
        if ((type & 3u) == 0) {
            b[at_++] = 0; /* no token */
        }
        if (x->server && (type & 3u) == 0 && at_ + 2 + body + pad + 16 < 1200) {
            pad = 1200 - (at_ + 2 + body + 16); /* a client's Initial fills 1200 bytes (RFC 9000 §14.1) */
        }
        at_ += varint2(b + at_, body + pad + 16);
    } else {
        b[at_++] = (uint8_t)(0x40 | (first & 0x07u)); /* fixed bit, key phase, packet number length */
        memcpy(b + at_, dcid, dcidlen);
        at_ += dcidlen;
    }
    uint64_t pn = x->pn++;
    for (size_t i = 0; i < pnlen; i++) {
        b[at_++] = (uint8_t)(pn >> (8 * (pnlen - 1 - i)));
    }
    memcpy(b + at_, frames, flen);
    at_ += flen;
    memset(b + at_, 0, pad + 16); /* PADDING frames, then the tag */
    at_ += pad + 16;
    dgram d = {b, at_, {0}, 0};
    memcpy(d.from, peer->local, QS_ADDR_BYTES);
    TRACE("  craft %s header, %zu bytes, to the %s\n", is_long ? "long" : "short", at_,
          x->server ? "server" : "client");
    deliver(x->server ? &to_server : &to_client, &d);
}

/* ---- Operations ---- */

static strm *pick(side *x) {
    unsigned i = take();
    return x->ns == 0 ? NULL : &x->s[i % (unsigned)x->ns];
}

static void timers(side *x) {
    if (!live(x)) {
        return;
    }
    uint64_t t = qs_expiry(x->c);
    if (t <= now) {
        int32_t rv = qs_handle_expiry(x->c, now);
        drain(x);
        if (rv != 0) {
            fail(x, rv);
        }
    }
}

static void query(side *x) {
    if (!x->c) {
        return;
    }
    uint8_t buf[4096];
    uint64_t code = 0;
    int32_t a = qs_all_acked(x->c), d = qs_handshake_done(x->c), p = qs_peer_close(x->c, &code);
    if ((a | d) & ~1 || p < 0 || p > 2) {
        broken("qs_all_acked, qs_handshake_done or qs_peer_close", a * 4 + d, p);
    }
    (void)qs_pto(x->c);
    (void)qs_expiry(x->c);
    if (qs_alpn(x->c, buf, 16) > 16 || (qs_resumed(x->c) | qs_early_accepted(x->c)) & ~1) {
        broken("qs_alpn, qs_resumed or qs_early_accepted", 0, 0);
    }
    if (!x->server) {
        int64_t n = qs_ticket(x->c, buf, sizeof buf);
        if (n < 0 || n > (int64_t)sizeof buf || (qs_retried(x->c) & ~1)) {
            broken("qs_ticket or qs_retried", (long)n, 0);
        }
    }
}

static void side_init(side *x, int server);

/* The client's ticket, kept once (a new one, or one it already has). */
static void keep_ticket(void) {
    static uint8_t buf[sizeof tickets[0].b];
    if (!cl.c) {
        return;
    }
    int64_t n = qs_ticket(cl.c, buf, sizeof buf);
    if (n < 0 || n > (int64_t)sizeof buf) {
        broken("qs_ticket", (long)n, 0);
    }
    if (n == 0) {
        return;
    }
    for (int i = 0; i < ntickets; i++) {
        if (tickets[i].len == (size_t)n && memcmp(tickets[i].b, buf, (size_t)n) == 0) {
            return;
        }
    }
    if (ntickets == TICKETS) {
        return;
    }
    memcpy(tickets[ntickets].b, buf, (size_t)n);
    tickets[ntickets].len = (size_t)n;
    tickets[ntickets].used = 0;
    ntickets++;
}

/* The invariants of resumption, as a pair of connections ends. */
static void settle(void) {
    if (!sv.c) {
        return;
    }
    int resumed = qs_resumed(sv.c), early = qs_early_accepted(sv.c);
    TRACE("  settle: from ticket %d of %d, resumed %d, early data accepted %d\n", cl_from, ntickets, resumed, early);
    if (early && (!server_early || !resumed)) {
        broken("early data accepted without 0-RTT on, or on a full handshake", server_early, resumed);
    }
    if (cl.c && qs_handshake_done(cl.c) && qs_handshake_done(sv.c) &&
        (qs_resumed(cl.c) != resumed || qs_early_accepted(cl.c) != early)) {
        broken("the client and the server disagree on resumption or early data", qs_resumed(cl.c) * 2 + resumed,
               qs_early_accepted(cl.c) * 2 + early);
    }
    if (resumed && cl_from >= 0) {
        if (server_early && tickets[cl_from].used) {
            broken("a single-use ticket resumed a second connection", cl_from, early);
        }
        tickets[cl_from].used = 1;
    }
}

/* A new client connection, resumed from tickets[from] unless `from` is -1; 0 if it could not be made. */
static int client_new(int from) {
    int32_t error = 0;
    cl.c = qs_conn_client(client_tls, (const uint8_t *)"localhost", 9, cl.local, sv.local, client_dcid,
                          sizeof client_dcid, cl.scid, sizeof cl.scid, cl.secret, now, &error);
    if (!cl.c) {
        if (error >= 0 && error != -1) {
            broken("qs_conn_client failed without an error", error, 0);
        }
        return 0;
    }
    if (client_manual) {
        qs_set_manual_credit(cl.c);
    }
    cl_from = -1;
    if (from >= 0) {
        int32_t r = qs_resume(cl.c, tickets[from].b, tickets[from].len);
        if (r < -1 || r > 1 || (fz_fail_at == 0 && r != server_early)) {
            broken("qs_resume on a ticket of qs_ticket's", r, server_early);
        }
        if (r >= 0) {
            cl_from = from;
        }
    }
    TRACE("  client: new connection, from ticket %d\n", cl_from);
    pump(&cl);
    return 1;
}

/* The client comes back: both connections go, and a new client resumes (a bit 2: from the first ticket; a bit 3:
   from none). */
static void reconnect(unsigned a) {
    keep_ticket();
    settle();
    qs_conn_free(cl.c);
    qs_conn_free(sv.c);
    link_clear(&to_server);
    link_clear(&to_client);
    side_init(&cl, 0);
    side_init(&sv, 1);
    RAND_bytes(client_dcid, sizeof client_dcid);
    cl.pn = sv.pn = 1u << 20;
    int from = (a & 8u) || ntickets == 0 ? -1 : (a & 4u) ? 0 : ntickets - 1;
    (void)client_new(from);
}

static void step(void) {
    unsigned op = take();
    unsigned a = op >> 4;
    side *x = (a & 1u) ? &cl : &sv; /* operations on a side: bit 0 set, the client */
    link *l = (a & 1u) ? &to_client : &to_server;
    dgram d;
    TRACE("op %u a %u (now %llu)\n", op & 15u, a, (unsigned long long)(now - NOW0));
    switch (op & 15u) {
    case 0:
        for (unsigned i = 0; i <= (a >> 1); i++) {
            if (!pop(l, &d)) {
                break;
            }
            deliver(l, &d);
            free(d.bytes);
        }
        break;
    case 1:
        if (pop(l, &d)) {
            free(d.bytes);
        }
        break;
    case 2: {
        unsigned off = take() << 8;
        off |= take();
        unsigned v = take();
        if (pop(l, &d)) {
            d.bytes[off % d.len] ^= (uint8_t)(v ? v : 1u);
            deliver(l, &d);
            free(d.bytes);
        }
        break;
    }
    case 3:
        if (l->count > 0) {
            dgram copy = l->q[0];
            deliver(l, &copy);
        }
        break;
    case 4:
        if (pop(l, &d)) {
            l->q[l->count++] = d;
        }
        break;
    case 5:
        now += (uint64_t)(take() + 1u) * ((a & 2u) ? 100000000ull : 1000000ull);
        timers(&cl);
        timers(&sv);
        break;
    case 6:
    case 7: {
        unsigned first = take();
        size_t len = take();
        if (len > in_len - at) {
            len = in_len - at;
        }
        side *target = (a & 1u) ? &cl : &sv;
        craft(target, (op & 15u) == 7, (a >> 1) & 3u, first, in + at, len);
        at += len;
        break;
    }
    case 8: {
        size_t len = take();
        if (len > in_len - at) {
            len = in_len - at;
        }
        if (len > 0) {
            dgram raw = {(uint8_t *)(uintptr_t)(in + at), len, {0}, 0};
            memcpy(raw.from, (a & 1u) ? sv.local : cl.local, QS_ADDR_BYTES);
            deliver(l, &raw);
        }
        at += len;
        break;
    }
    case 9:
        if (live(x) && x->ns < STREAMS) {
            int64_t id = (a & 2u) ? qs_open_uni(x->c) : qs_open_bidi(x->c);
            if (id >= 0) {
                if (find(x, id)) {
                    broken("a stream ID opened twice", (long)id, x->server);
                }
                (void)add(x, id);
            } else if (id < -1000) {
                broken("qs_open_* result", (long)id, 0);
            }
        }
        break;
    case 10: {
        strm *s = pick(x);
        unsigned n = take();
        size_t len = (n & 0x80u) ? ((n & 0x7fu) + 1u) * 16384u : ((n & 0x7fu) + 1u) * 64u;
        if (live(x) && s && s->can_send && !s->closed) {
            for (size_t i = 0; i < len; i++) {
                pattern[i] = pat(s->id, s->sent + i);
            }
            int fin = (a & 2u) != 0;
            int64_t took = qs_stream_write(x->c, s->id, pattern, len, fin);
            if (took < -1 || took > (int64_t)len) {
                broken("qs_stream_write", (long)took, (long)len);
            }
            if (took > 0) {
                s->sent += (uint64_t)took;
            }
            if (fin && took == (int64_t)len) {
                s->can_send = 0; /* ended */
            }
        }
        break;
    }
    case 11: {
        strm *s = pick(x);
        unsigned n = take();
        if (!live(x) || !s || s->closed) {
            break;
        }
        int32_t rv = 0;
        switch ((a >> 1) & 3u) {
        case 0:
            if (s->can_send) {
                rv = qs_stream_reset(x->c, s->id, n);
                s->can_send = 0;
            }
            break;
        case 1:
            if (s->can_send) {
                rv = qs_stream_reset_sending(x->c, s->id, n);
                s->can_send = 0;
            }
            break;
        case 2:
            if (s->can_read) {
                rv = qs_stream_stop_reading(x->c, s->id, n);
            }
            break;
        default:
            qs_consume(x->c, s->id, (uint64_t)n * 256u);
            break;
        }
        if (rv > 0 || rv < -1000) {
            broken("stream control result", rv, 0);
        }
        break;
    }
    case 12:
        if (live(&cl)) {
            uint8_t fresh[QS_ADDR_BYTES];
            addr(fresh, 1, (uint16_t)(6000u + take()));
            int32_t rv = qs_migrate(cl.c, fresh, now);
            if (rv == 0) {
                memcpy(cl.local, fresh, QS_ADDR_BYTES);
            } else if (rv > 0 || rv < -1000) {
                broken("qs_migrate result", rv, 0);
            }
        }
        break;
    case 13:
        if (live(x)) {
            uint8_t out[DGRAM];
            int64_t n = qs_close(x->c, (a & 2u) ? 1 : 0, take(), out, sizeof out, now);
            if (n > (int64_t)sizeof out || n < -1000) {
                broken("qs_close", (long)n, 0);
            }
            if (n > 0) {
                push(x->server ? &to_client : &to_server, out, (size_t)n, x->local, 0);
            }
            x->dead = 1;
        }
        break;
    case 14: {
        size_t len = take();
        if (len > in_len - at) {
            len = in_len - at;
        }
        const uint8_t *p = in + at;
        at += len;
        uint8_t dcid[20], out[DGRAM], odcid[NGTCP2_MAX_CIDLEN];
        int32_t initial = 0;
        uint32_t version = 0;
        int32_t n = qs_packet_dcid(p, len, dcid, &initial, &version);
        if (n < -1 || n > 20) {
            broken("qs_packet_dcid", n, 0);
        }
        int64_t m = qs_version_negotiation(p, len, out, sizeof out);
        if (m < 0 || m > (int64_t)sizeof out) {
            broken("qs_version_negotiation", (long)m, 0);
        }
        size_t outlen = 0, odcidlen = 0;
        int32_t r = qs_retry_check(retry_key, p, len, cl.local, (a >> 1) & 1u, retry_scid, now, out, sizeof out,
                                   &outlen, odcid, &odcidlen);
        if (r > 3 || outlen > sizeof out || odcidlen > NGTCP2_MAX_CIDLEN) {
            broken("qs_retry_check", r, (long)outlen);
        }
        if (n > 0) {
            m = qs_stateless_reset(sv.secret, dcid, (size_t)n, len, out, sizeof out);
            if (m < 0 || m > 43) {
                broken("qs_stateless_reset", (long)m, 0);
            }
        }
        break;
    }
    default:
        if (x == &cl && (a & 2u)) {
            reconnect(a);
        } else {
            query(x);
        }
        break;
    }
    pump(&cl);
    pump(&sv);
}

/* ---- Per input ---- */

static void side_init(side *x, int server) {
    memset(x, 0, sizeof *x);
    x->server = server;
    addr(x->local, server ? 2 : 1, server ? 443 : 5000);
    RAND_bytes(x->scid, sizeof x->scid);
    RAND_bytes(x->secret, sizeof x->secret);
}

static void run(const uint8_t *data, size_t size) {
    in = data;
    in_len = size;
    at = 0;
    fz_seed = 0x5eed;
    fz_live = 0;
    fz_allocs = 0;
    fz_fail_at = 0;
    now = NOW0;
    unsigned mode = take();
    fz_null = (mode & 1u) != 0;
    require_retry = (mode & 2u) != 0;
    small_first = (mode & 16u) != 0;
    if (mode & 32u) {
        fz_fail_at = (unsigned long)(take() << 8);
        fz_fail_at |= take();
    }
    side_init(&cl, 0);
    side_init(&sv, 1);
    server_manual = (mode & 4u) != 0;
    client_manual = (mode & 8u) != 0;
    server_early = (mode & 64u) != 0;
    ntickets = 0;
    cl_from = -1;
    SSL_CTX_flush_sessions_ex(server_cfg, 0); /* the single-use tickets of the last input */
    if (SSL_CTX_sess_number(server_cfg) != 0) {
        broken("the server's session cache did not empty", SSL_CTX_sess_number(server_cfg), 0);
    }
    RAND_bytes(retry_key, sizeof retry_key);
    RAND_bytes(retry_scid, sizeof retry_scid);
    RAND_bytes(client_dcid, sizeof client_dcid);
    cl.pn = sv.pn = 1u << 20; /* past any packet number the peer's own packets reach */

    server_tls = qs_tls_new(server_cfg, 1);
    client_tls = qs_tls_new(client_cfg, 0);
    if (server_tls && client_tls) {
        if (server_early) {
            qs_tls_allow_early(server_tls);
        }
        if (client_new(-1)) {
            for (int steps = 0; more() && steps < 4096; steps++) {
                step();
            }
        }
        settle();
    }
    qs_conn_free(cl.c);
    qs_conn_free(sv.c);
    qs_tls_free(server_tls);
    qs_tls_free(client_tls);
    link_clear(&to_server);
    link_clear(&to_client);
    if (fz_live != 0) {
        broken("the shim or ngtcp2 leaked allocations", fz_live, (long)fz_fail_at);
    }
}

/* ---- Setup, once ---- */

static uint8_t *slurp(const char *env, size_t *len) {
    const char *path = getenv(env);
    if (path == NULL) {
        fprintf(stderr, "fuzz_quic: set %s\n", env);
        exit(2);
    }
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        perror(path);
        exit(2);
    }
    static uint8_t buf[3][16384];
    static int which;
    uint8_t *b = buf[which++ % 3];
    *len = fread(b, 1, sizeof buf[0], f);
    fclose(f);
    return b;
}

static void setup(void) {
    if (RAND_set_rand_method(&fz_rand) != 1 || qs_init() != 0) {
        fprintf(stderr, "fuzz_quic: setup failed (randomness, or ngtcp2's OpenSSL backend)\n");
        exit(2);
    }
    size_t cl_, kl, al;
    uint8_t *chain = slurp("CW_CERT", &cl_);
    uint8_t *key = slurp("CW_KEY", &kl);
    uint8_t *ca = slurp("CW_CA", &al);
    server_cfg = wt_cfg_new(1);
    client_cfg = wt_cfg_new(0);
    static const uint8_t alpn[] = {2, 'h', '3'};
    if (server_cfg == NULL || client_cfg == NULL || !wt_cfg_use_pem(server_cfg, chain, cl_, key, kl) ||
        !wt_cfg_set_alpn(server_cfg, alpn, sizeof alpn) || !wt_cfg_set_alpn(client_cfg, alpn, sizeof alpn) ||
        wt_cfg_trust_pem(client_cfg, ca, al) < 1) {
        fprintf(stderr, "fuzz_quic: setup failed (TLS configurations)\n");
        exit(2);
    }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    static int ready;
    if (!ready) {
        setup();
        ready = 1;
    }
    run(data, size);
    return 0;
}

#ifdef QS_REPLAY
/* Seeds: operation scripts that complete a handshake and move stream data, so the fuzzer starts past the handshake
   in each mode. */
#define OP(op, a) (uint8_t)(((a) << 4) | (op))
#define TO_SERVER OP(0, 14) /* deliver up to 8 datagrams to the server */
#define TO_CLIENT OP(0, 15)
static const uint8_t HANDSHAKE[] = {TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT,
                                    OP(5, 0),  0x10,      TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT};
static const uint8_t STREAMS_[] = {
    OP(9, 1),  OP(10, 1), 0x00,      0x0F, OP(10, 3), 0x00, 0x4F, /* the client: a stream, 1 KiB, 5 KiB and FIN */
    TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT,
    OP(10, 2), 0x00,      0x81, /* the server: 32 KiB and FIN back on it */
    TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT,
    OP(9, 3),  OP(10, 1), 0x01,      0x05, OP(11, 1), 0x01, 0x07, /* the client: a unidirectional one, reset */
    TO_SERVER, TO_CLIENT, OP(15, 0), OP(15, 1),
};
static const uint8_t LOSS[] = {
    OP(1, 0), OP(1, 1),  OP(0, 0), OP(4, 1), OP(5, 0), 0x40, TO_CLIENT, TO_SERVER, /* loss, reordering */
    OP(2, 1), 0x00,      0x05,     0x07,     OP(3, 0), TO_CLIENT, /* a corrupted datagram, a duplicate */
    OP(5, 2), 0x20,      TO_SERVER, TO_CLIENT, OP(5, 2), 0xFF, TO_SERVER, TO_CLIENT, OP(5, 2), 0xFF, /* to idle */
};
static const uint8_t MIGRATE[] = {OP(12, 0), 0x07,      TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT,
                                  OP(9, 1),  OP(10, 1), 0x02,      0x05,      TO_SERVER, TO_CLIENT,
                                  OP(5, 0),  0x40,      TO_SERVER, TO_CLIENT};
static const uint8_t CLOSE[] = {OP(13, 3), 0x2A, TO_SERVER, TO_CLIENT, OP(5, 2), 0x40, TO_SERVER, TO_CLIENT};
/* After STREAMS_ (the ticket has come): the client comes back with it and writes 1 KiB and FIN before its handshake
   ends, in 0-RTT if the server allows it; then once more with the first ticket, a replay, which writes early too. */
static const uint8_t RESUME[] = {
    OP(15, 3), OP(9, 1),  OP(10, 3), 0x00,      0x0F, /* the newest ticket; a stream, 1 KiB and FIN, early */
    TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, OP(5, 0), 0x10, TO_SERVER, TO_CLIENT,
    OP(10, 2), 0x00,      0x01,      TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, /* the server answers on it */
    OP(15, 7), OP(9, 1),  OP(10, 3), 0x00,      0x0F, /* the first ticket again: a replay */
    TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, TO_SERVER, TO_CLIENT, OP(5, 0), 0x10, TO_SERVER, TO_CLIENT,
    TO_SERVER, TO_CLIENT, OP(15, 0),  OP(15, 1),
};
/* With null protection: a short-header packet to the server with PING, MAX_DATA, a STREAM frame with FIN on stream
   8, RESET_STREAM and STOP_SENDING on stream 12, and DATA_BLOCKED. */
static const uint8_t FRAMES[] = {0x01, 0x10, 0x44, 0x00, 0x0B, 0x08, 0x04, 'a', 'b', 'c', 'd',
                                 0x04, 0x0C, 0x07, 0x00, 0x05, 0x0C, 0x09, 0x14, 0x00};

static void seed(const char *dir, const char *name, uint8_t mode, const uint8_t *tail, size_t tail_len,
                 const uint8_t *extra, size_t extra_len) {
    uint8_t buf[512];
    size_t n = 0;
    buf[n++] = mode;
    memcpy(buf + n, HANDSHAKE, sizeof HANDSHAKE);
    n += sizeof HANDSHAKE;
    memcpy(buf + n, tail, tail_len);
    n += tail_len;
    memcpy(buf + n, extra, extra_len);
    n += extra_len;
    char path[1024];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(buf, 1, n, f) != n || fclose(f) != 0) {
        perror(path);
        exit(1);
    }
}

static void seeds(const char *dir) {
    static const uint8_t none[1];
    uint8_t craft_short[3 + sizeof FRAMES];
    craft_short[0] = OP(6, 0); /* to the server */
    craft_short[1] = 0x03; /* key phase 0, 4-byte packet number */
    craft_short[2] = (uint8_t)sizeof FRAMES;
    memcpy(craft_short + 3, FRAMES, sizeof FRAMES);
    seed(dir, "handshake", 0x00, none, 0, none, 0);
    seed(dir, "streams", 0x00, STREAMS_, sizeof STREAMS_, none, 0);
    seed(dir, "loss", 0x00, STREAMS_, sizeof STREAMS_, LOSS, sizeof LOSS);
    seed(dir, "migrate", 0x00, STREAMS_, sizeof STREAMS_, MIGRATE, sizeof MIGRATE);
    seed(dir, "close", 0x00, STREAMS_, sizeof STREAMS_, CLOSE, sizeof CLOSE);
    seed(dir, "retry", 0x02, STREAMS_, sizeof STREAMS_, none, 0);
    seed(dir, "credit", 0x0C, STREAMS_, sizeof STREAMS_, LOSS, sizeof LOSS);
    seed(dir, "null-streams", 0x01, STREAMS_, sizeof STREAMS_, none, 0);
    seed(dir, "null-frames", 0x11, craft_short, sizeof craft_short, STREAMS_, sizeof STREAMS_);
    seed(dir, "resume", 0x00, STREAMS_, sizeof STREAMS_, RESUME, sizeof RESUME);
    seed(dir, "early", 0x40, STREAMS_, sizeof STREAMS_, RESUME, sizeof RESUME);
    seed(dir, "early-retry", 0x42, STREAMS_, sizeof STREAMS_, RESUME, sizeof RESUME);
    seed(dir, "early-null", 0x41, STREAMS_, sizeof STREAMS_, RESUME, sizeof RESUME);
}

int main(int argc, char **argv) {
    if (argc == 3 && strcmp(argv[1], "--seeds") == 0) {
        seeds(argv[2]);
        return 0;
    }
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (!f) {
            perror(argv[i]);
            return 1;
        }
        static uint8_t buf[1u << 20];
        size_t n = fread(buf, 1, sizeof buf, f);
        fclose(f);
        LLVMFuzzerTestOneInput(buf, n);
        fprintf(stderr, "fuzz_quic: %s: ok (%zu bytes)\n", argv[i], n);
    }
    return 0;
}
#endif
