/* web_quic's shim over ngtcp2 and its OpenSSL backend (Web ADR 0014 §3.4). The contract:
 *   - CeeSafe holds opaque handles (qs_tls, qs_conn), each made and freed here, and freed once by its owner;
 *   - every call takes byte buffers with explicit lengths and the time in nanoseconds; nothing here reads a clock or
 *     touches a socket, and no pointer from CeeSafe is kept after a call returns;
 *   - ngtcp2's callbacks copy what they report into a queue in the handle, drained with qs_next_event after every
 *     call (§2, option A), so CeeSafe is never called from C. Its bound is flow control's: a stream's credit is
 *     given back when CeeSafe takes its data, so what waits in the queue never passes the connection's window;
 *   - ngtcp2 keeps no copy of stream data it sends: the shim holds each stream's unacknowledged bytes until the peer
 *     acknowledges them (acked_stream_data_offset), and bounds them per stream;
 *   - ngtcp2 allocates through CeeSafe's allocator for C code (CEE_ALLOC_FOREIGN, CeeSafe ADR 0060), so the
 *     allocation-failure sweep and the leak check reach it; OpenSSL already does, through web_tls's TlsConfig;
 *   - ngtcp2's rule for its OpenSSL backend: SSL_set_app_data(ssl, NULL) before SSL_free (§3.3).
 * Errors are ngtcp2's negative codes, passed through; CeeSafe maps them (src/errors.cee). */
#define NGTCP2_STATICLIB 1
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_ossl.h>
#include <openssl/opensslv.h>
#include <openssl/crypto.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <stdint.h>
#include <string.h>
#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
#else
#  include <netinet/in.h>
#  include <sys/socket.h>
#endif
#include "ceesafe/foreign.h"

/* ngtcp2's OpenSSL backend uses OpenSSL's QUIC TLS API, added in 3.5 (Web ADR 0014 §3.3). */
#if OPENSSL_VERSION_NUMBER < 0x30500000L
#  error "web_quic needs OpenSSL 3.5 or later (Web ADR 0014 §3.3)"
#endif

/* ---- Versions and initialisation ---- */

/* ngtcp2's version, as 0xMMmmpp. */
uint32_t qs_ngtcp2_version(void) {
    const ngtcp2_info *info = ngtcp2_version(0);
    return info ? (uint32_t)info->version_num : 0u;
}

/* The OpenSSL the process runs with, as OpenSSL_version_num() (0xMNN00PP0L since 3.0). */
uint64_t qs_openssl_version(void) {
    return (uint64_t)OpenSSL_version_num();
}

/* web_tls's shim, linked into the same program: routes OpenSSL's allocations through CeeSafe's, once. */
void wt_init(void);

/* ngtcp2's OpenSSL backend initialises once per process: 0, or a negative ngtcp2 error. OpenSSL's allocator is
   routed first, since OpenSSL accepts one only before its first allocation, which ngtcp2_crypto_ossl_init makes. */
int32_t qs_init(void) {
    wt_init();
    return (int32_t)ngtcp2_crypto_ossl_init();
}

/* ---- Memory ---- */

static void *qs_malloc(size_t n, void *ud) {
    (void)ud;
    return ceesafe_alloc(n ? n : 1u, CEE_ALLOC_FOREIGN);
}

static void qs_free(void *p, void *ud) {
    (void)ud;
    ceesafe_free(p, CEE_ALLOC_FOREIGN);
}

static void *qs_calloc(size_t count, size_t size, void *ud) {
    (void)ud;
    if (size != 0 && count > SIZE_MAX / size) {
        return NULL;
    }
    size_t n = count * size;
    void *p = ceesafe_alloc(n ? n : 1u, CEE_ALLOC_FOREIGN);
    if (p) {
        memset(p, 0, n);
    }
    return p;
}

static void *qs_realloc(void *p, size_t n, void *ud) {
    (void)ud;
    return ceesafe_realloc(p, n ? n : 1u, CEE_ALLOC_FOREIGN);
}

static const ngtcp2_mem qs_mem = {NULL, qs_malloc, qs_free, qs_calloc, qs_realloc};

/* ---- Addresses: 19 bytes from CeeSafe, family (4 or 6), port (big-endian), then 16 address bytes (IPv4 in the
   first 4) ---- */

#define QS_ADDR_BYTES 19u

typedef struct qs_addr {
    struct sockaddr_storage storage;
    ngtcp2_socklen length;
} qs_addr;

static int qs_addr_from(qs_addr *out, const uint8_t *in) {
    memset(out, 0, sizeof *out);
    uint16_t port = (uint16_t)((in[1] << 8) | in[2]);
    if (in[0] == 4) {
        struct sockaddr_in *a = (struct sockaddr_in *)&out->storage;
        a->sin_family = AF_INET;
        a->sin_port = htons(port);
        memcpy(&a->sin_addr, in + 3, 4);
        out->length = (ngtcp2_socklen)sizeof *a;
        return 1;
    }
    if (in[0] == 6) {
        struct sockaddr_in6 *a = (struct sockaddr_in6 *)&out->storage;
        a->sin6_family = AF_INET6;
        a->sin6_port = htons(port);
        memcpy(&a->sin6_addr, in + 3, 16);
        out->length = (ngtcp2_socklen)sizeof *a;
        return 1;
    }
    return 0;
}

/* ---- TLS contexts: web_tls's SSL_CTX, with a reference of our own ---- */

/* web_tls's shim (packages/web_tls/c/tls_shim.c), linked into the same program: binds a server SSL to the
   configuration it came from, so web_tls's ticket callback finds the configuration's keys without reading the SSL's
   app data, which holds ngtcp2's connection reference here. */
int32_t wt_ssl_use_front(SSL *ssl, SSL_CTX *ctx);

typedef struct qs_tls {
    SSL_CTX *ctx;
    int server;
} qs_tls;

/* `ctx` is web_tls's TlsConfig.quicContext(); null on failure. */
qs_tls *qs_tls_new(void *ctx, int32_t server) {
    if (!ctx) {
        return NULL;
    }
    qs_tls *t = qs_calloc(1, sizeof *t, NULL);
    if (!t) {
        return NULL;
    }
    if (SSL_CTX_up_ref((SSL_CTX *)ctx) != 1) {
        qs_free(t, NULL);
        return NULL;
    }
    t->ctx = (SSL_CTX *)ctx;
    t->server = server != 0;
    return t;
}

void qs_tls_free(qs_tls *t) {
    if (t) {
        SSL_CTX_free(t->ctx);
        qs_free(t, NULL);
    }
}

/* ---- The event queue ---- */

enum {
    QS_EV_HANDSHAKE_DONE = 1,
    QS_EV_STREAM_DATA = 2,     /* stream, data; flags bit 0: the stream's FIN */
    QS_EV_STREAM_OPEN = 3,     /* stream: opened by the peer */
    QS_EV_STREAM_CLOSE = 4,    /* stream, code; flags bit 0: the code is the application's */
    QS_EV_STREAM_RESET = 5,    /* stream, code: the peer reset its sending side */
    QS_EV_STOP_SENDING = 6,    /* stream, code: the peer asked us to stop sending */
    QS_EV_STREAM_WRITABLE = 7, /* stream: its flow-control window grew */
    QS_EV_STREAMS_BIDI = 8,    /* code: the bidirectional streams we may open, now */
    QS_EV_NEW_CID = 9,         /* data: a connection ID we issued, which the listener must route to us */
    QS_EV_RETIRE_CID = 10,     /* data: a connection ID of ours the peer retired */
    QS_EV_TOO_SMALL = 255,     /* not an event: qs_next_event's buffer is smaller than the next event's data */
};

#define QS_MAX_DATAGRAM 65527u /* the largest UDP payload over IPv4, and the most data one event carries */
#define QS_MAX_DATA (16u * 1024u * 1024u) /* the connection's flow-control window, initial_max_data (qs_params) */
/* The queue's limits. Stream data waits there only until CeeSafe takes it, and its credit is given back only then,
   so it never passes QS_MAX_DATA; the rest of the events' data is connection IDs. Past these limits the shim fails
   the connection with NGTCP2_ERR_NOMEM; flow control means they are not reached. */
#define QS_QUEUE_BYTES_LIMIT (QS_MAX_DATA + 1024u * 1024u)
#define QS_QUEUE_EVENTS_LIMIT 65536u

typedef struct qs_event {
    uint32_t kind;
    uint32_t flags;
    int64_t stream;
    uint64_t code;
    size_t offset; /* into the queue's data */
    size_t length;
} qs_event;

/* Events in order, [head, head + count) of `events`; their data in order in `data`. Both grow as needed and start
   over when the queue empties, which it does after every drain. */
typedef struct qs_queue {
    qs_event *events;
    size_t head, count, capacity;
    uint8_t *data;
    size_t used, room;
    int failed; /* a push could not grow the queue: the connection fails with NGTCP2_ERR_NOMEM */
} qs_queue;

static int qs_push_one(qs_queue *q, uint32_t kind, uint32_t flags, int64_t stream, uint64_t code,
                       const uint8_t *bytes, size_t length) {
    if (q->head + q->count == q->capacity) {
        if (q->head > 0) {
            memmove(q->events, q->events + q->head, q->count * sizeof *q->events);
            q->head = 0;
        } else {
            size_t want = q->capacity ? q->capacity * 2 : 64u;
            if (want > QS_QUEUE_EVENTS_LIMIT) {
                q->failed = 1;
                return 0;
            }
            qs_event *grown = qs_realloc(q->events, want * sizeof *grown, NULL);
            if (!grown) {
                q->failed = 1;
                return 0;
            }
            q->events = grown;
            q->capacity = want;
        }
    }
    if (length > q->room - q->used) {
        size_t want = q->room ? q->room : 65536u;
        while (want - q->used < length) {
            want *= 2;
        }
        if (want > QS_QUEUE_BYTES_LIMIT) {
            q->failed = 1;
            return 0;
        }
        uint8_t *grown = qs_realloc(q->data, want, NULL);
        if (!grown) {
            q->failed = 1;
            return 0;
        }
        q->data = grown;
        q->room = want;
    }
    qs_event *e = &q->events[q->head + q->count];
    e->kind = kind;
    e->flags = flags;
    e->stream = stream;
    e->code = code;
    e->offset = q->used;
    e->length = length;
    if (length) {
        memcpy(q->data + q->used, bytes, length);
    }
    q->used += length;
    q->count += 1;
    return 1;
}

/* Queues an event; stream data longer than QS_MAX_DATAGRAM as several, the FIN with the last, so every event's
   data fits qs_next_event's buffer of QS_MAX_DATAGRAM bytes. */
static int qs_push(qs_queue *q, uint32_t kind, uint32_t flags, int64_t stream, uint64_t code, const uint8_t *bytes,
                   size_t length) {
    while (kind == QS_EV_STREAM_DATA && length > QS_MAX_DATAGRAM) {
        if (!qs_push_one(q, kind, flags & ~1u, stream, code, bytes, QS_MAX_DATAGRAM)) {
            return 0;
        }
        bytes += QS_MAX_DATAGRAM;
        length -= QS_MAX_DATAGRAM;
    }
    return qs_push_one(q, kind, flags, stream, code, bytes, length);
}

/* ---- Streams we send on: bytes held until acknowledged ---- */

#define QS_STREAM_HOLD (1024u * 1024u) /* bytes held per stream; past it, qs_stream_write takes less */
#define QS_CHUNK 16384u
#define QS_VECS 8 /* chunks handed to ngtcp2 per packet written */

/* A stream's bytes are kept in chunks that never move: ngtcp2 keeps pointers into the bytes it was given until they
   are acknowledged, to retransmit them. A chunk is filled in order and freed only once all its bytes are
   acknowledged; nothing is moved or reallocated while ngtcp2 may hold a pointer into it. */
typedef struct qs_chunk {
    struct qs_chunk *next;
    size_t length;
    uint8_t bytes[QS_CHUNK];
} qs_chunk;

typedef struct qs_stream {
    int64_t id;
    qs_chunk *head, *tail; /* the oldest chunk held, whose bytes[0] is stream offset `base`, and the newest */
    uint64_t base;         /* stream offset of head->bytes[0] */
    uint64_t end;          /* stream offset after the last byte held */
    uint64_t acked;        /* every byte below it is acknowledged */
    uint64_t sent;         /* stream offset handed to ngtcp2 so far */
    int fin;               /* the application ended the stream */
    int fin_sent;
    struct qs_stream *next;
} qs_stream;

static void qs_chunks_free(qs_stream *s) {
    while (s->head) {
        qs_chunk *c = s->head;
        s->head = c->next;
        qs_free(c, NULL);
    }
    s->tail = NULL;
}

/* ---- Connections ---- */

typedef struct qs_conn {
    ngtcp2_conn *conn;
    ngtcp2_crypto_conn_ref ref;
    ngtcp2_crypto_ossl_ctx *ossl;
    SSL *ssl;
    qs_addr local, remote;
    ngtcp2_path path;
    ngtcp2_ccerr last_error;
    uint8_t reset_secret[32]; /* for stateless-reset tokens of the IDs we issue */
    qs_stream *streams;
    qs_queue queue;
    int manual_credit; /* stream data's credit goes back on qs_consume, not when CeeSafe takes the event */
} qs_conn;

static ngtcp2_conn *qs_get_conn(ngtcp2_crypto_conn_ref *ref) {
    return ((qs_conn *)ref->user_data)->conn;
}

static qs_stream *qs_find(qs_conn *c, int64_t id) {
    for (qs_stream *s = c->streams; s; s = s->next) {
        if (s->id == id) {
            return s;
        }
    }
    return NULL;
}

static qs_stream *qs_stream_get(qs_conn *c, int64_t id) {
    qs_stream *s = qs_find(c, id);
    if (s) {
        return s;
    }
    s = qs_calloc(1, sizeof *s, NULL);
    if (!s) {
        return NULL;
    }
    s->id = id;
    s->next = c->streams;
    c->streams = s;
    return s;
}

static void qs_stream_drop(qs_conn *c, int64_t id) {
    for (qs_stream **p = &c->streams; *p; p = &(*p)->next) {
        if ((*p)->id == id) {
            qs_stream *s = *p;
            *p = s->next;
            qs_chunks_free(s);
            qs_free(s, NULL);
            return;
        }
    }
}

/* ---- ngtcp2's callbacks: each records an event, or does the bookkeeping, and calls nothing in CeeSafe ---- */

static void cb_rand(uint8_t *dest, size_t len, const ngtcp2_rand_ctx *ctx) {
    (void)ctx;
    if (RAND_bytes(dest, (int)len) != 1) {
        memset(dest, 0, len); /* OpenSSL's DRBG does not fail once seeded; this only keeps the memory defined */
    }
}

static int cb_new_cid(ngtcp2_conn *conn, ngtcp2_cid *cid, ngtcp2_stateless_reset_token *token, size_t cidlen,
                      void *ud) {
    (void)conn;
    qs_conn *c = ud;
    if (RAND_bytes(cid->data, (int)cidlen) != 1) {
        return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    cid->datalen = cidlen;
    if (ngtcp2_crypto_generate_stateless_reset_token(token->data, c->reset_secret, sizeof c->reset_secret, cid) !=
        0) {
        return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    return qs_push(&c->queue, QS_EV_NEW_CID, 0, -1, 0, cid->data, cidlen) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_remove_cid(ngtcp2_conn *conn, const ngtcp2_cid *cid, void *ud) {
    (void)conn;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_RETIRE_CID, 0, -1, 0, cid->data, cid->datalen) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_handshake_completed(ngtcp2_conn *conn, void *ud) {
    (void)conn;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_HANDSHAKE_DONE, 0, -1, 0, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_recv_stream_data(ngtcp2_conn *conn, uint32_t flags, int64_t stream, uint64_t offset,
                               const uint8_t *data, size_t len, void *ud, void *sud) {
    (void)offset;
    (void)sud;
    qs_conn *c = ud;
    uint32_t fin = (flags & NGTCP2_STREAM_DATA_FLAG_FIN) ? 1u : 0u;
    (void)conn;
    /* The credit is given back when CeeSafe takes the bytes (qs_next_event), so the queue's data stays within the
       connection's window. One datagram can release far more than its own bytes: ngtcp2 delivers what it held out of
       order behind a lost packet once that packet arrives. */
    return qs_push(&c->queue, QS_EV_STREAM_DATA, fin, stream, 0, data, len) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_stream_open(ngtcp2_conn *conn, int64_t stream, void *ud) {
    (void)conn;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_STREAM_OPEN, 0, stream, 0, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_stream_close(ngtcp2_conn *conn, uint32_t flags, int64_t stream, uint64_t code, void *ud, void *sud) {
    (void)sud;
    qs_conn *c = ud;
    qs_stream_drop(c, stream);
    /* A stream the peer opened is done: it may open one more (RFC 9000 §4.6). Without this, a peer could open only
       initial_max_streams_bidi streams (100) in the connection's whole life. Bit 0 of an ID is set for streams a
       server opens (§2.1), and bit 1 for unidirectional ones. */
    int peer_opened = ngtcp2_conn_is_server(conn) ? (stream & 1) == 0 : (stream & 1) == 1;
    if (peer_opened) {
        if (stream & 2) {
            ngtcp2_conn_extend_max_streams_uni(conn, 1);
        } else {
            ngtcp2_conn_extend_max_streams_bidi(conn, 1);
        }
    }
    uint32_t has = (flags & NGTCP2_STREAM_CLOSE_FLAG_APP_ERROR_CODE_SET) ? 1u : 0u;
    return qs_push(&c->queue, QS_EV_STREAM_CLOSE, has, stream, code, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_stream_reset(ngtcp2_conn *conn, int64_t stream, uint64_t final_size, uint64_t code, void *ud,
                           void *sud) {
    (void)conn;
    (void)final_size;
    (void)sud;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_STREAM_RESET, 0, stream, code, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_stop_sending(ngtcp2_conn *conn, int64_t stream, uint64_t code, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_STOP_SENDING, 0, stream, code, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_acked(ngtcp2_conn *conn, int64_t stream, uint64_t offset, uint64_t len, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    qs_conn *c = ud;
    qs_stream *s = qs_find(c, stream);
    /* ngtcp2 reports the acknowledged prefix growing (ngtcp2_acked_stream_data_offset): a range that starts past
       what is acknowledged so far is not a prefix, and frees nothing. */
    if (!s || offset > s->acked || offset + len <= s->acked) {
        return 0;
    }
    int was_full = s->end - s->base >= QS_STREAM_HOLD;
    s->acked = offset + len;
    /* Free the chunks every byte of which is acknowledged; the rest stay where they are. */
    int freed = 0;
    while (s->head && s->base + s->head->length <= s->acked &&
           (s->head != s->tail || s->head->length == QS_CHUNK || s->base + s->head->length == s->end)) {
        qs_chunk *done = s->head;
        s->base += done->length;
        s->head = done->next;
        if (!s->head) {
            s->tail = NULL;
        }
        qs_free(done, NULL);
        freed = 1;
    }
    if (was_full && freed) {
        /* The stream refused bytes while it held QS_STREAM_HOLD; there is room again. */
        return qs_push(&c->queue, QS_EV_STREAM_WRITABLE, 0, stream, 0, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
    }
    return 0;
}

static int cb_extend_stream(ngtcp2_conn *conn, int64_t stream, uint64_t max, void *ud, void *sud) {
    (void)conn;
    (void)max;
    (void)sud;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_STREAM_WRITABLE, 0, stream, 0, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static int cb_extend_bidi(ngtcp2_conn *conn, uint64_t max, void *ud) {
    (void)conn;
    qs_conn *c = ud;
    return qs_push(&c->queue, QS_EV_STREAMS_BIDI, 0, -1, max, NULL, 0) ? 0 : NGTCP2_ERR_CALLBACK_FAILURE;
}

static void qs_callbacks(ngtcp2_callbacks *cb, int server) {
    memset(cb, 0, sizeof *cb);
    if (server) {
        cb->recv_client_initial = ngtcp2_crypto_recv_client_initial_cb;
    } else {
        cb->client_initial = ngtcp2_crypto_client_initial_cb;
        cb->recv_retry = ngtcp2_crypto_recv_retry_cb;
    }
    cb->recv_crypto_data = ngtcp2_crypto_recv_crypto_data_cb;
    cb->encrypt = ngtcp2_crypto_encrypt_cb;
    cb->decrypt = ngtcp2_crypto_decrypt_cb;
    cb->hp_mask = ngtcp2_crypto_hp_mask_cb;
    cb->update_key = ngtcp2_crypto_update_key_cb;
    cb->delete_crypto_aead_ctx = ngtcp2_crypto_delete_crypto_aead_ctx_cb;
    cb->delete_crypto_cipher_ctx = ngtcp2_crypto_delete_crypto_cipher_ctx_cb;
    cb->version_negotiation = ngtcp2_crypto_version_negotiation_cb;
    cb->get_path_challenge_data2 = ngtcp2_crypto_get_path_challenge_data2_cb;
    cb->rand = cb_rand;
    cb->get_new_connection_id2 = cb_new_cid;
    cb->remove_connection_id = cb_remove_cid;
    cb->handshake_completed = cb_handshake_completed;
    cb->recv_stream_data = cb_recv_stream_data;
    cb->stream_open = cb_stream_open;
    cb->stream_close = cb_stream_close;
    cb->stream_reset = cb_stream_reset;
    /* recv_stop_sending is the peer's STOP_SENDING (ngtcp2 1.24). stream_stop_sending is not: it reports our own
       shutdown of a stream's reading, which CeeSafe made and needs no event for. */
    cb->recv_stop_sending = cb_stop_sending;
    cb->acked_stream_data_offset = cb_acked;
    cb->extend_max_stream_data = cb_extend_stream;
    cb->extend_max_local_streams_bidi = cb_extend_bidi;
}

/* The transport parameters both sides send (Web ADR 0014 §3.5's defaults). */
static void qs_params(ngtcp2_transport_params *p) {
    ngtcp2_transport_params_default(p);
    p->initial_max_streams_bidi = 100;
    p->initial_max_streams_uni = 3;
    p->initial_max_stream_data_bidi_local = 1024 * 1024;
    p->initial_max_stream_data_bidi_remote = 1024 * 1024;
    p->initial_max_stream_data_uni = 1024 * 1024;
    p->initial_max_data = 16 * 1024 * 1024;
    p->max_idle_timeout = 30 * NGTCP2_SECONDS;
}

static qs_conn *qs_conn_alloc(const uint8_t *local, const uint8_t *remote, const uint8_t *secret) {
    qs_conn *c = qs_calloc(1, sizeof *c, NULL);
    if (!c) {
        return NULL;
    }
    if (!qs_addr_from(&c->local, local) || !qs_addr_from(&c->remote, remote)) {
        qs_free(c, NULL);
        return NULL;
    }
    c->path.local.addr = (ngtcp2_sockaddr *)&c->local.storage;
    c->path.local.addrlen = c->local.length;
    c->path.remote.addr = (ngtcp2_sockaddr *)&c->remote.storage;
    c->path.remote.addrlen = c->remote.length;
    memcpy(c->reset_secret, secret, sizeof c->reset_secret);
    c->ref.get_conn = qs_get_conn;
    c->ref.user_data = c;
    ngtcp2_ccerr_default(&c->last_error);
    return c;
}

/* The TLS half: an SSL from the context, configured for QUIC, and handed to ngtcp2. */
static int qs_attach_tls(qs_conn *c, qs_tls *t, const uint8_t *host, size_t host_len) {
    if (ngtcp2_crypto_ossl_ctx_new(&c->ossl, NULL) != 0) {
        return 0;
    }
    c->ssl = SSL_new(t->ctx);
    if (!c->ssl) {
        return 0;
    }
    ngtcp2_crypto_ossl_ctx_set_ssl(c->ossl, c->ssl);
    if (t->server) {
        if (ngtcp2_crypto_ossl_configure_server_session(c->ssl) != 0 || wt_ssl_use_front(c->ssl, t->ctx) != 1) {
            return 0;
        }
        SSL_set_app_data(c->ssl, &c->ref);
        SSL_set_accept_state(c->ssl);
    } else {
        if (ngtcp2_crypto_ossl_configure_client_session(c->ssl) != 0) {
            return 0;
        }
        SSL_set_app_data(c->ssl, &c->ref);
        SSL_set_connect_state(c->ssl);
        if (host_len > 0) {
            char name[256];
            if (host_len >= sizeof name) {
                return 0;
            }
            memcpy(name, host, host_len);
            name[host_len] = 0;
            if (SSL_set_tlsext_host_name(c->ssl, name) != 1 || SSL_set1_host(c->ssl, name) != 1) {
                return 0;
            }
        }
    }
    ngtcp2_conn_set_tls_native_handle(c->conn, c->ossl);
    return 1;
}

void qs_conn_free(qs_conn *c);

/* A client connection to `remote` from `local`, for `host` (its SNI and the name verified). `dcid` and `scid` are
   CeeSafe's random IDs; `secret` (32 bytes) derives the stateless-reset tokens of the IDs it issues. Null on failure,
   with ngtcp2's error (or -1) in *error. */
qs_conn *qs_conn_client(qs_tls *t, const uint8_t *host, size_t host_len, const uint8_t *local, const uint8_t *remote,
                        const uint8_t *dcid, size_t dcid_len, const uint8_t *scid, size_t scid_len,
                        const uint8_t *secret, uint64_t now, int32_t *error) {
    *error = -1;
    if (!t || t->server || dcid_len < NGTCP2_MIN_INITIAL_DCIDLEN || dcid_len > NGTCP2_MAX_CIDLEN ||
        scid_len > NGTCP2_MAX_CIDLEN) {
        return NULL;
    }
    qs_conn *c = qs_conn_alloc(local, remote, secret);
    if (!c) {
        return NULL;
    }
    ngtcp2_callbacks callbacks;
    qs_callbacks(&callbacks, 0);
    ngtcp2_settings settings;
    ngtcp2_settings_default(&settings);
    settings.initial_ts = now;
    ngtcp2_transport_params params;
    qs_params(&params);
    ngtcp2_cid d, s;
    ngtcp2_cid_init(&d, dcid, dcid_len);
    ngtcp2_cid_init(&s, scid, scid_len);
    int rv = ngtcp2_conn_client_new(&c->conn, &d, &s, &c->path, NGTCP2_PROTO_VER_V1, &callbacks, &settings, &params,
                                    &qs_mem, c);
    if (rv != 0) {
        *error = rv;
        qs_conn_free(c);
        return NULL;
    }
    if (!qs_attach_tls(c, t, host, host_len)) {
        qs_conn_free(c);
        return NULL;
    }
    *error = 0;
    return c;
}

/* A server connection for the client Initial `pkt` (`len` bytes), which arrived at `local` from `remote`. `scid` is
   the server's ID for it. Null on failure: *error is NGTCP2_ERR_INVALID_ARGUMENT if `pkt` is no Initial to accept. */
qs_conn *qs_conn_server(qs_tls *t, const uint8_t *pkt, size_t len, const uint8_t *local, const uint8_t *remote,
                        const uint8_t *scid, size_t scid_len, const uint8_t *secret, uint64_t now, int32_t *error) {
    *error = -1;
    if (!t || !t->server || scid_len > NGTCP2_MAX_CIDLEN) {
        return NULL;
    }
    ngtcp2_pkt_hd hd;
    int rv = ngtcp2_accept(&hd, pkt, len);
    if (rv != 0) {
        *error = rv;
        return NULL;
    }
    qs_conn *c = qs_conn_alloc(local, remote, secret);
    if (!c) {
        return NULL;
    }
    ngtcp2_callbacks callbacks;
    qs_callbacks(&callbacks, 1);
    ngtcp2_settings settings;
    ngtcp2_settings_default(&settings);
    settings.initial_ts = now;
    settings.token = hd.token;
    settings.tokenlen = hd.tokenlen;
    ngtcp2_transport_params params;
    qs_params(&params);
    params.original_dcid = hd.dcid;
    params.original_dcid_present = 1;
    ngtcp2_cid s;
    ngtcp2_cid_init(&s, scid, scid_len);
    rv = ngtcp2_conn_server_new(&c->conn, &hd.scid, &s, &c->path, hd.version, &callbacks, &settings, &params,
                                &qs_mem, c);
    if (rv != 0) {
        *error = rv;
        qs_conn_free(c);
        return NULL;
    }
    if (!qs_attach_tls(c, t, NULL, 0)) {
        qs_conn_free(c);
        return NULL;
    }
    *error = 0;
    return c;
}

void qs_conn_free(qs_conn *c) {
    if (!c) {
        return;
    }
    if (c->ssl) {
        SSL_set_app_data(c->ssl, NULL); /* ngtcp2's rule for its OpenSSL backend (Web ADR 0014 §3.3) */
        SSL_free(c->ssl);
    }
    if (c->ossl) {
        ngtcp2_crypto_ossl_ctx_del(c->ossl);
    }
    if (c->conn) {
        ngtcp2_conn_del(c->conn);
    }
    while (c->streams) {
        qs_stream *s = c->streams;
        c->streams = s->next;
        qs_chunks_free(s);
        qs_free(s, NULL);
    }
    qs_free(c->queue.data, NULL);
    qs_free(c->queue.events, NULL);
    qs_free(c, NULL);
}

/* ---- Reading and writing datagrams ---- */

/* Feeds one datagram (`len` bytes, ECN mark `ecn`) that arrived from `remote`. 0, or ngtcp2's error,
   after which the connection is closing (qs_close writes its CONNECTION_CLOSE) or, for NGTCP2_ERR_DRAINING, the peer
   closed it. */
int32_t qs_read(qs_conn *c, const uint8_t *remote, uint32_t ecn, const uint8_t *pkt, size_t len, uint64_t now) {
    if (len > QS_MAX_DATAGRAM) {
        return NGTCP2_ERR_INVALID_ARGUMENT;
    }
    qs_addr from;
    if (!qs_addr_from(&from, remote)) {
        return NGTCP2_ERR_INVALID_ARGUMENT;
    }
    ngtcp2_path path = c->path;
    path.remote.addr = (ngtcp2_sockaddr *)&from.storage;
    path.remote.addrlen = from.length;
    ngtcp2_pkt_info pi = {(uint8_t)ecn};
    int rv = ngtcp2_conn_read_pkt(c->conn, &path, &pi, pkt, len, now);
    if (c->queue.failed) {
        rv = NGTCP2_ERR_NOMEM; /* the queue could not grow: memory, since flow control bounds it */
    }
    if (rv != 0 && rv != NGTCP2_ERR_DRAINING) {
        ngtcp2_ccerr_set_liberr(&c->last_error, rv, NULL, 0);
    }
    return rv;
}

/* The next datagram to send, into `out` (`cap` bytes): its length, 0 if there is nothing to send now, or ngtcp2's
   error. Each call writes one packet, with stream data from at most one stream. */
int64_t qs_write(qs_conn *c, uint8_t *out, size_t cap, uint64_t now, uint32_t *ecn) {
    ngtcp2_pkt_info pi;
    memset(&pi, 0, sizeof pi);
    for (qs_stream *s = c->streams; s; s = s->next) {
        if (s->sent == s->end && !(s->fin && !s->fin_sent)) {
            continue;
        }
        /* The unsent bytes, from the chunk holding `sent`, up to QS_VECS chunks. */
        ngtcp2_vec v[QS_VECS];
        size_t count = 0;
        uint64_t at = s->base;
        uint64_t reach = s->sent;
        for (qs_chunk *ch = s->head; ch && count < QS_VECS; ch = ch->next) {
            uint64_t chunk_end = at + ch->length;
            if (reach < chunk_end) {
                v[count].base = ch->bytes + (size_t)(reach - at);
                v[count].len = (size_t)(chunk_end - reach);
                count += 1;
                reach = chunk_end;
            }
            at = chunk_end;
        }
        ngtcp2_ssize taken = 0;
        /* The FIN only with the stream's last bytes: never when the vectors stop short of the end. */
        uint32_t flags = (s->fin && reach == s->end) ? NGTCP2_WRITE_STREAM_FLAG_FIN : 0;
        ngtcp2_ssize n = ngtcp2_conn_writev_stream(c->conn, &c->path, &pi, out, cap, &taken, flags, s->id, v, count,
                                                   now);
        if (n == NGTCP2_ERR_STREAM_DATA_BLOCKED || n == NGTCP2_ERR_STREAM_SHUT_WR ||
            n == NGTCP2_ERR_STREAM_NOT_FOUND) {
            continue;
        }
        if (n < 0) {
            ngtcp2_ccerr_set_liberr(&c->last_error, (int)n, NULL, 0);
            return n;
        }
        if (taken >= 0) {
            s->sent += (uint64_t)taken;
            if (s->fin && s->sent == s->end && (flags & NGTCP2_WRITE_STREAM_FLAG_FIN)) {
                s->fin_sent = 1;
            }
        }
        if (n > 0) {
            *ecn = pi.ecn;
            return n;
        }
    }
    ngtcp2_ssize n = ngtcp2_conn_write_pkt(c->conn, &c->path, &pi, out, cap, now);
    if (n < 0) {
        ngtcp2_ccerr_set_liberr(&c->last_error, (int)n, NULL, 0);
        return n;
    }
    *ecn = pi.ecn;
    return n;
}

/* A CONNECTION_CLOSE for the last error (or NO_ERROR, or the application's `code` when `app` is set), into `out`:
   its length, or ngtcp2's error. */
int64_t qs_close(qs_conn *c, int32_t app, uint64_t code, uint8_t *out, size_t cap, uint64_t now) {
    if (app) {
        ngtcp2_ccerr_set_application_error(&c->last_error, code, NULL, 0);
    }
    ngtcp2_pkt_info pi;
    memset(&pi, 0, sizeof pi);
    return ngtcp2_conn_write_connection_close(c->conn, &c->path, &pi, out, cap, &c->last_error, now);
}

/* When the connection next needs qs_handle_expiry, in nanoseconds (UINT64_MAX: never). */
uint64_t qs_expiry(qs_conn *c) {
    return ngtcp2_conn_get_expiry(c->conn);
}

/* Runs the timers due at `now`: 0, or ngtcp2's error (NGTCP2_ERR_IDLE_CLOSE: the idle timeout passed). */
int32_t qs_handle_expiry(qs_conn *c, uint64_t now) {
    int rv = ngtcp2_conn_handle_expiry(c->conn, now);
    if (rv != 0 && rv != NGTCP2_ERR_IDLE_CLOSE) {
        ngtcp2_ccerr_set_liberr(&c->last_error, rv, NULL, 0);
    }
    return rv;
}

/* Whether the handshake has completed. */
int32_t qs_handshake_done(qs_conn *c) {
    return ngtcp2_conn_get_handshake_completed(c->conn) ? 1 : 0;
}

/* The ALPN the handshake chose into `out`: its length (0 if none). */
size_t qs_alpn(qs_conn *c, uint8_t *out, size_t cap) {
    const unsigned char *p = NULL;
    unsigned int n = 0;
    SSL_get0_alpn_selected(c->ssl, &p, &n);
    if (!p || n > cap) {
        return 0;
    }
    memcpy(out, p, n);
    return n;
}

/* What an error code means to CeeSafe, so ngtcp2's numbers stay in C: 2 draining, 3 idle timeout, 4 no memory, 5 no
   stream may be opened now, 6 not an Initial to accept, 0 anything else. */
int32_t qs_error_kind(int32_t code) {
    switch (code) {
    case NGTCP2_ERR_DRAINING: return 2;
    case NGTCP2_ERR_IDLE_CLOSE: return 3;
    case NGTCP2_ERR_NOMEM: return 4;
    case NGTCP2_ERR_STREAM_ID_BLOCKED: return 5;
    case NGTCP2_ERR_INVALID_ARGUMENT: return 6;
    default: return 0;
    }
}

/* ---- Streams ---- */

/* Opens a bidirectional stream: its ID, or ngtcp2's error (NGTCP2_ERR_STREAM_ID_BLOCKED: wait for
   QS_EV_STREAMS_BIDI). */
int64_t qs_open_bidi(qs_conn *c) {
    int64_t id;
    int rv = ngtcp2_conn_open_bidi_stream(c->conn, &id, NULL);
    return rv != 0 ? rv : id;
}

/* Opens a unidirectional stream: its ID, or ngtcp2's error (STREAM_ID_BLOCKED when the peer's limit is reached). */
int64_t qs_open_uni(qs_conn *c) {
    int64_t id;
    int rv = ngtcp2_conn_open_uni_stream(c->conn, &id, NULL);
    return rv != 0 ? rv : id;
}

/* From now on, stream data's flow-control credit goes back to the peer only through qs_consume (Web ADR 0014
   amendment 4 item 2). The queue stays bounded by flow control: what is not consumed is never credited. */
void qs_set_manual_credit(qs_conn *c) {
    c->manual_credit = 1;
}

/* Gives the peer back `n` bytes of credit on `stream` and on the connection: the user has consumed them. A closed
   stream has no window to extend, which is not an error. */
void qs_consume(qs_conn *c, int64_t stream, uint64_t n) {
    if (n == 0) {
        return;
    }
    (void)ngtcp2_conn_extend_max_stream_offset(c->conn, stream, n);
    ngtcp2_conn_extend_max_offset(c->conn, n);
}

/* Takes up to `len` bytes to send on `stream` (`fin`: and ends it after them); how many were taken, which is less
   when the stream already holds QS_STREAM_HOLD unacknowledged bytes; or -1 without memory. The bytes are sent by
   qs_write. A FIN is taken only with the last byte. */
int64_t qs_stream_write(qs_conn *c, int64_t stream, const uint8_t *bytes, size_t len, int32_t fin) {
    qs_stream *s = qs_stream_get(c, stream);
    if (!s || s->fin) {
        return -1;
    }
    uint64_t held = s->end - s->base;
    size_t room = held >= QS_STREAM_HOLD ? 0 : (size_t)(QS_STREAM_HOLD - held);
    size_t take = len < room ? len : room;
    size_t done = 0;
    while (done < take) {
        if (!s->tail || s->tail->length == QS_CHUNK) {
            qs_chunk *fresh = qs_malloc(sizeof *fresh, NULL);
            if (!fresh) {
                if (done == 0) {
                    return -1;
                }
                break; /* what was taken stays taken */
            }
            fresh->next = NULL;
            fresh->length = 0;
            if (s->tail) {
                s->tail->next = fresh;
            } else {
                s->head = fresh;
                s->base = s->end;
            }
            s->tail = fresh;
        }
        size_t n = QS_CHUNK - s->tail->length;
        if (n > take - done) {
            n = take - done;
        }
        /* Only past the bytes already given to ngtcp2: appending never moves them. */
        memcpy(s->tail->bytes + s->tail->length, bytes + done, n);
        s->tail->length += n;
        s->end += n;
        done += n;
    }
    take = done;
    if (fin && take == len) {
        s->fin = 1;
    }
    return (int64_t)take;
}

/* Resets our sending side of `stream` with the application's `code`, and asks the peer to stop sending. */
int32_t qs_stream_reset(qs_conn *c, int64_t stream, uint64_t code) {
    /* ngtcp2 first, then the bytes: after the reset it sends none of them again. */
    int rv = ngtcp2_conn_shutdown_stream(c->conn, 0, stream, code);
    qs_stream_drop(c, stream);
    return rv;
}

/* Resets only our sending side of `stream` (RESET_STREAM), leaving its receiving side open. */
int32_t qs_stream_reset_sending(qs_conn *c, int64_t stream, uint64_t code) {
    int rv = ngtcp2_conn_shutdown_stream_write(c->conn, 0, stream, code);
    qs_stream_drop(c, stream);
    return rv;
}

/* Asks the peer to stop sending on `stream` (STOP_SENDING), leaving our sending side open. */
int32_t qs_stream_stop_reading(qs_conn *c, int64_t stream, uint64_t code) {
    return ngtcp2_conn_shutdown_stream_read(c->conn, 0, stream, code);
}

/* ---- Routing, before there is a connection ---- */

/* A datagram's destination connection ID, into `out` (20 bytes): its length, or -1 if the datagram is not a QUIC packet
   ngtcp2 can read. Short headers are read with our IDs' length, 20. *initial is 1 for a version 1 long-header Initial,
   the only packet that may make a connection (RFC 9000 §17.2.2); *version is the long header's version (0 for a short
   header). */
int32_t qs_packet_dcid(const uint8_t *pkt, size_t len, uint8_t *out, int32_t *initial, uint32_t *version) {
    *initial = 0;
    *version = 0;
    ngtcp2_version_cid vc;
    int rv = ngtcp2_pkt_decode_version_cid(&vc, pkt, len, 20);
    if (rv != 0 && rv != NGTCP2_ERR_VERSION_NEGOTIATION) {
        return -1;
    }
    if (vc.dcidlen > 20) {
        return -1;
    }
    memcpy(out, vc.dcid, vc.dcidlen);
    *version = vc.version;
    /* Long header (bit 7), version 1, packet type 0 (bits 5-4): an Initial. */
    if (len > 0 && (pkt[0] & 0x80) && vc.version == NGTCP2_PROTO_VER_V1 && ((pkt[0] & 0x30) >> 4) == 0) {
        *initial = 1;
    }
    return (int32_t)vc.dcidlen;
}

/* ---- Events ---- */

/* The next event: its kind (0: none), with its stream, flags and code written out, and its data copied into `buf`
   (`cap` bytes) with its length in *length. An event's data is never more than QS_MAX_DATAGRAM bytes; if it is more
   than `cap`, nothing is taken: the kind is QS_EV_TOO_SMALL and *length the room it needs. */
uint32_t qs_next_event(qs_conn *c, uint8_t *buf, size_t cap, int64_t *stream, uint32_t *flags, uint64_t *code,
                       size_t *length) {
    qs_queue *q = &c->queue;
    if (q->count == 0) {
        q->head = 0;
        q->used = 0;
        return 0;
    }
    qs_event *e = &q->events[q->head];
    if (e->length > cap) {
        *length = e->length;
        return QS_EV_TOO_SMALL;
    }
    size_t n = e->length;
    memcpy(buf, q->data + e->offset, n);
    *stream = e->stream;
    *flags = e->flags;
    *code = e->code;
    *length = n;
    uint32_t kind = e->kind;
    if (kind == QS_EV_STREAM_DATA && n > 0 && !c->manual_credit) {
        /* CeeSafe has the bytes: their credit goes back to the peer now (flow control bounds the queue). A stream
           already closed has no window to extend, which ngtcp2 reports and nothing needs. */
        (void)ngtcp2_conn_extend_max_stream_offset(c->conn, e->stream, n);
        ngtcp2_conn_extend_max_offset(c->conn, n);
    }
    q->head += 1;
    q->count -= 1;
    if (q->count == 0) {
        q->head = 0;
        q->used = 0;
    }
    return kind;
}
