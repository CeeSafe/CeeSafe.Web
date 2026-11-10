/* web_http3's shim over nghttp3 (Web ADR 0014 §3.4, §3.6, amendment 4 item 3). The contract:
 *   - CeeSafe holds an opaque handle (h3_conn), made and freed here, and freed once by its owner;
 *   - every call takes byte buffers with explicit lengths; nothing here reads a clock or touches a socket, and no
 *     pointer from CeeSafe is kept after a call returns;
 *   - nghttp3's callbacks copy what they report into a queue in the handle, drained with h3_next_event after every
 *     call, so CeeSafe is never called from C. What waits there is bounded by QUIC's flow control: a request body's
 *     credit goes back only when its handler reads it (web_quic's creditOnConsume);
 *   - stream bytes nghttp3 produces are copied into web_quic's own chunks when QUIC takes them (h3_pull, then
 *     h3_wrote), so they are reported acknowledged at once and nghttp3 frees them;
 *   - a body CeeSafe sends is copied into chunks that never move, since nghttp3 keeps pointers into what its data
 *     reader hands it until it reports them acknowledged (acked_stream_data);
 *   - nghttp3 allocates through CeeSafe's allocator for C code (CEE_ALLOC_FOREIGN, CeeSafe ADR 0060).
 * Errors are nghttp3's negative codes; h3_app_error maps them to HTTP/3's (RFC 9114 §8.1), so no number is copied
 * by hand. A negative return from h3_read, h3_pull or h3_wrote is a connection error: nghttp3 allows only
 * h3_conn_free after it. */
#define NGHTTP3_STATICLIB 1
#include <nghttp3/nghttp3.h>
#include <stdint.h>
#include <string.h>
#include "ceesafe/foreign.h"

/* The functions CeeSafe calls (src/*.cee's extern block), declared before use. */
typedef struct h3_conn h3_conn;
uint32_t h3_nghttp3_version(void);
h3_conn *h3_conn_server(uint64_t max_field_section);
h3_conn *h3_conn_client(uint64_t max_field_section);
void h3_conn_free(h3_conn *c);
int32_t h3_bind(h3_conn *c, int64_t control, int64_t encoder, int64_t decoder);
uint64_t h3_app_error(int32_t rv);
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
uint32_t h3_next_event(h3_conn *c, uint8_t *buf, size_t cap, int64_t *stream, uint32_t *flags, uint64_t *code, size_t *length);

/* nghttp3's version, as 0xMMmmpp. */
uint32_t h3_nghttp3_version(void) {
    const nghttp3_info *info = nghttp3_version(0);
    return info ? (uint32_t)info->version_num : 0u;
}

/* ---- Memory ---- */

static void *h3_malloc(size_t n, void *ud) {
    (void)ud;
    return ceesafe_alloc(n ? n : 1u, CEE_ALLOC_FOREIGN);
}

static void h3_free_mem(void *p, void *ud) {
    (void)ud;
    ceesafe_free(p, CEE_ALLOC_FOREIGN);
}

static void *h3_calloc(size_t count, size_t size, void *ud) {
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

static void *h3_realloc(void *p, size_t n, void *ud) {
    (void)ud;
    return ceesafe_realloc(p, n ? n : 1u, CEE_ALLOC_FOREIGN);
}

static const nghttp3_mem h3_mem = {NULL, h3_malloc, h3_free_mem, h3_calloc, h3_realloc};

/* ---- The event queue ---- */

enum {
    H3_EV_FIELD = 1,       /* stream; flags bit 0: a trailer; data: name then value; code: the name's length */
    H3_EV_FIELDS_END = 2,  /* stream; flags bit 0: trailers, bit 1: the stream's end came with them */
    H3_EV_DATA = 3,        /* stream; data: body bytes, whose credit CeeSafe gives back as they are read */
    H3_EV_END = 4,         /* stream: the peer's side of it has ended */
    H3_EV_CONSUMED = 5,    /* stream; code: bytes nghttp3 consumed later (deferred_consume): give their credit back */
    H3_EV_STOP_SENDING = 6, /* stream; code: send STOP_SENDING with this code (web_quic's stopReading) */
    H3_EV_RESET = 7,       /* stream; code: reset our sending side with this code (web_quic's resetSending) */
    H3_EV_CLOSED = 8,      /* stream; code: nghttp3 is done with it */
    H3_EV_BODY_ROOM = 9,   /* stream: its body queue, once full, has room again */
    H3_EV_GOAWAY = 10,     /* code: the peer's GOAWAY ID */
    H3_EV_TOO_SMALL = 255, /* the next event's data does not fit the buffer given; nothing was taken */
};

#define H3_QUEUE_BYTES_LIMIT (32u * 1024u * 1024u)
#define H3_QUEUE_EVENTS_LIMIT 65536u
#define H3_EVENT_DATA_MAX 65527u /* the most one event carries; DATA longer than this is split */

typedef struct h3_event {
    uint32_t kind;
    uint32_t flags;
    int64_t stream;
    uint64_t code;
    size_t offset, length;
} h3_event;

typedef struct h3_queue {
    h3_event *events;
    size_t head, count, capacity;
    uint8_t *data;
    size_t used, room;
    int failed;
} h3_queue;

static int h3_push_one(h3_queue *q, uint32_t kind, uint32_t flags, int64_t stream, uint64_t code, const uint8_t *a,
                       size_t alen, const uint8_t *b, size_t blen) {
    size_t length = alen + blen;
    if (q->head + q->count == q->capacity) {
        if (q->head > 0) {
            memmove(q->events, q->events + q->head, q->count * sizeof *q->events);
            q->head = 0;
        } else {
            size_t want = q->capacity ? q->capacity * 2 : 64u;
            h3_event *grown = want > H3_QUEUE_EVENTS_LIMIT ? NULL : h3_realloc(q->events, want * sizeof *grown, NULL);
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
        uint8_t *grown = want > H3_QUEUE_BYTES_LIMIT ? NULL : h3_realloc(q->data, want, NULL);
        if (!grown) {
            q->failed = 1;
            return 0;
        }
        q->data = grown;
        q->room = want;
    }
    h3_event *e = &q->events[q->head + q->count];
    e->kind = kind;
    e->flags = flags;
    e->stream = stream;
    e->code = code;
    e->offset = q->used;
    e->length = length;
    if (alen) {
        memcpy(q->data + q->used, a, alen);
    }
    if (blen) {
        memcpy(q->data + q->used + alen, b, blen);
    }
    q->used += length;
    q->count += 1;
    return 1;
}

static int h3_push(h3_queue *q, uint32_t kind, uint32_t flags, int64_t stream, uint64_t code) {
    return h3_push_one(q, kind, flags, stream, code, NULL, 0, NULL, 0);
}

/* ---- A body to send: chunks that never move (see the contract above) ---- */

#define H3_CHUNK 16384u
#define H3_BODY_HOLD (256u * 1024u) /* bytes held per stream; past it, h3_send_body takes less */
#define H3_READ_VECS 16

typedef struct h3_chunk {
    struct h3_chunk *next;
    size_t length;
    uint8_t bytes[H3_CHUNK];
} h3_chunk;

typedef struct h3_body {
    int64_t id;
    h3_chunk *head, *tail;
    uint64_t base;  /* body offset of head->bytes[0] */
    uint64_t end;   /* body offset after the last byte held */
    uint64_t given; /* handed to nghttp3 so far */
    uint64_t acked; /* every byte below it nghttp3 reported acknowledged */
    int ended;      /* CeeSafe has given the last byte */
    int trailers;   /* trailers follow the body: the reader's EOF does not end the stream */
    int full;       /* h3_send_body took less than offered: say when there is room */
    struct h3_body *next;
} h3_body;

/* What the shim knows of a request stream's reading, so QUIC's close of a stream nghttp3 has not finished reading
   waits (h3_close_stream). */
typedef struct h3_track {
    int64_t id;
    int ended;    /* nghttp3 reported the stream's end (end_stream): everything on it was read */
    int reset;    /* the peer reset it: nothing more will be read */
    int deferred; /* QUIC closed it before nghttp3 finished: the close waits for `ended` */
    uint64_t code;
    struct h3_track *next;
} h3_track;

struct h3_conn {
    nghttp3_conn *conn;
    h3_queue queue;
    h3_body *bodies;
    h3_track *tracks;
};

static h3_track *h3_track_get(h3_conn *c, int64_t id, int make) {
    for (h3_track *t = c->tracks; t; t = t->next) {
        if (t->id == id) {
            return t;
        }
    }
    if (!make) {
        return NULL;
    }
    h3_track *t = ceesafe_alloc(sizeof *t, CEE_ALLOC_FOREIGN);
    if (!t) {
        return NULL;
    }
    memset(t, 0, sizeof *t);
    t->id = id;
    t->next = c->tracks;
    c->tracks = t;
    return t;
}

static void h3_track_drop(h3_conn *c, int64_t id) {
    for (h3_track **p = &c->tracks; *p; p = &(*p)->next) {
        if ((*p)->id == id) {
            h3_track *t = *p;
            *p = t->next;
            ceesafe_free(t, CEE_ALLOC_FOREIGN);
            return;
        }
    }
}

static h3_body *h3_find(h3_conn *c, int64_t id) {
    for (h3_body *b = c->bodies; b; b = b->next) {
        if (b->id == id) {
            return b;
        }
    }
    return NULL;
}

static void h3_body_free(h3_body *b) {
    while (b->head) {
        h3_chunk *ch = b->head;
        b->head = ch->next;
        h3_free_mem(ch, NULL);
    }
    h3_free_mem(b, NULL);
}

static void h3_body_drop(h3_conn *c, int64_t id) {
    for (h3_body **p = &c->bodies; *p; p = &(*p)->next) {
        if ((*p)->id == id) {
            h3_body *b = *p;
            *p = b->next;
            h3_body_free(b);
            return;
        }
    }
}

static h3_body *h3_body_new(h3_conn *c, int64_t id) {
    h3_body *b = h3_calloc(1, sizeof *b, NULL);
    if (!b) {
        return NULL;
    }
    b->id = id;
    b->next = c->bodies;
    c->bodies = b;
    return b;
}

/* nghttp3's data reader: vectors over the body's bytes not yet handed over; EOF once the last byte is handed and
   CeeSafe has ended the body; would-block while it has not. */
static nghttp3_ssize cb_read_data(nghttp3_conn *conn, int64_t stream, nghttp3_vec *vec, size_t veccnt,
                                  uint32_t *pflags, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    h3_body *b = h3_find(c, stream);
    if (!b) {
        *pflags |= NGHTTP3_DATA_FLAG_EOF; /* reset or closed meanwhile: nothing more */
        return 0;
    }
    size_t count = 0;
    uint64_t at = b->base;
    uint64_t reach = b->given;
    for (h3_chunk *ch = b->head; ch && count < veccnt; ch = ch->next) {
        uint64_t chunk_end = at + ch->length;
        if (reach < chunk_end) {
            vec[count].base = ch->bytes + (size_t)(reach - at);
            vec[count].len = (size_t)(chunk_end - reach);
            count += 1;
            reach = chunk_end;
        }
        at = chunk_end;
    }
    b->given = reach;
    if (b->given == b->end) {
        if (b->ended) {
            *pflags |= NGHTTP3_DATA_FLAG_EOF;
            if (b->trailers) {
                *pflags |= NGHTTP3_DATA_FLAG_NO_END_STREAM;
            }
        } else if (count == 0) {
            return NGHTTP3_ERR_WOULDBLOCK;
        }
    }
    return (nghttp3_ssize)count;
}

/* nghttp3 is done with `datalen` more bytes of the body: free the chunks wholly done with. */
static int cb_acked_stream_data(nghttp3_conn *conn, int64_t stream, uint64_t datalen, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    h3_body *b = h3_find(c, stream);
    if (!b) {
        return 0;
    }
    int was_full = b->end - b->base >= H3_BODY_HOLD || b->full;
    b->acked += datalen;
    while (b->head && b->base + b->head->length <= b->acked &&
           (b->head != b->tail || b->head->length == H3_CHUNK || b->base + b->head->length == b->end)) {
        h3_chunk *done = b->head;
        b->base += done->length;
        b->head = done->next;
        if (!b->head) {
            b->tail = NULL;
        }
        h3_free_mem(done, NULL);
    }
    if (was_full && b->full && b->end - b->base < H3_BODY_HOLD) {
        b->full = 0;
        return h3_push(&c->queue, H3_EV_BODY_ROOM, 0, stream, 0) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
    }
    return 0;
}

/* ---- What arrives ---- */

static int field(h3_conn *c, int64_t stream, nghttp3_rcbuf *name, nghttp3_rcbuf *value, uint32_t trailer) {
    nghttp3_vec n = nghttp3_rcbuf_get_buf(name);
    nghttp3_vec v = nghttp3_rcbuf_get_buf(value);
    return h3_push_one(&c->queue, H3_EV_FIELD, trailer, stream, n.len, n.base, n.len, v.base, v.len)
               ? 0
               : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_recv_header(nghttp3_conn *conn, int64_t stream, int32_t token, nghttp3_rcbuf *name,
                          nghttp3_rcbuf *value, uint8_t flags, void *ud, void *sud) {
    (void)conn;
    (void)token;
    (void)flags;
    (void)sud;
    return field(ud, stream, name, value, 0);
}

static int cb_recv_trailer(nghttp3_conn *conn, int64_t stream, int32_t token, nghttp3_rcbuf *name,
                           nghttp3_rcbuf *value, uint8_t flags, void *ud, void *sud) {
    (void)conn;
    (void)token;
    (void)flags;
    (void)sud;
    return field(ud, stream, name, value, 1);
}

static int cb_end_headers(nghttp3_conn *conn, int64_t stream, int fin, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    return h3_push(&c->queue, H3_EV_FIELDS_END, fin ? 2u : 0u, stream, 0) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_end_trailers(nghttp3_conn *conn, int64_t stream, int fin, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    return h3_push(&c->queue, H3_EV_FIELDS_END, fin ? 3u : 1u, stream, 0) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_recv_data(nghttp3_conn *conn, int64_t stream, const uint8_t *data, size_t len, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    while (len > H3_EVENT_DATA_MAX) {
        if (!h3_push_one(&c->queue, H3_EV_DATA, 0, stream, 0, data, H3_EVENT_DATA_MAX, NULL, 0)) {
            return NGHTTP3_ERR_CALLBACK_FAILURE;
        }
        data += H3_EVENT_DATA_MAX;
        len -= H3_EVENT_DATA_MAX;
    }
    return h3_push_one(&c->queue, H3_EV_DATA, 0, stream, 0, data, len, NULL, 0) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_deferred_consume(nghttp3_conn *conn, int64_t stream, size_t consumed, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    return h3_push(&c->queue, H3_EV_CONSUMED, 0, stream, consumed) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_end_stream(nghttp3_conn *conn, int64_t stream, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    h3_track *t = h3_track_get(c, stream, 1);
    if (!t) {
        return NGHTTP3_ERR_CALLBACK_FAILURE;
    }
    t->ended = 1;
    return h3_push(&c->queue, H3_EV_END, 0, stream, 0) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_stop_sending(nghttp3_conn *conn, int64_t stream, uint64_t code, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    return h3_push(&c->queue, H3_EV_STOP_SENDING, 0, stream, code) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_reset_stream(nghttp3_conn *conn, int64_t stream, uint64_t code, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    h3_body_drop(c, stream);
    return h3_push(&c->queue, H3_EV_RESET, 0, stream, code) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_stream_close(nghttp3_conn *conn, int64_t stream, uint64_t code, void *ud, void *sud) {
    (void)conn;
    (void)sud;
    h3_conn *c = ud;
    h3_body_drop(c, stream);
    h3_track_drop(c, stream);
    return h3_push(&c->queue, H3_EV_CLOSED, 0, stream, code) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

static int cb_shutdown(nghttp3_conn *conn, int64_t id, void *ud) {
    (void)conn;
    h3_conn *c = ud;
    return h3_push(&c->queue, H3_EV_GOAWAY, 0, -1, (uint64_t)id) ? 0 : NGHTTP3_ERR_CALLBACK_FAILURE;
}

/* ---- Connections ---- */

#define H3_QPACK_TABLE 4096u   /* §3.6 */
#define H3_QPACK_BLOCKED 16u   /* §3.6 */
#define H3_MAX_STREAMS 100u    /* web_quic's initial_max_streams_bidi */

static void h3_callbacks(nghttp3_callbacks *cb) {
    memset(cb, 0, sizeof *cb);
    cb->acked_stream_data = cb_acked_stream_data;
    cb->stream_close = cb_stream_close;
    cb->recv_data = cb_recv_data;
    cb->deferred_consume = cb_deferred_consume;
    cb->recv_header = cb_recv_header;
    cb->end_headers = cb_end_headers;
    cb->recv_trailer = cb_recv_trailer;
    cb->end_trailers = cb_end_trailers;
    cb->stop_sending = cb_stop_sending;
    cb->end_stream = cb_end_stream;
    cb->reset_stream = cb_reset_stream;
    cb->shutdown = cb_shutdown;
}

static h3_conn *h3_new(int server, uint64_t max_field_section) {
    h3_conn *c = h3_calloc(1, sizeof *c, NULL);
    if (!c) {
        return NULL;
    }
    nghttp3_callbacks cb;
    h3_callbacks(&cb);
    nghttp3_settings settings;
    nghttp3_settings_default(&settings);
    settings.max_field_section_size = max_field_section;
    settings.qpack_max_dtable_capacity = H3_QPACK_TABLE;
    settings.qpack_blocked_streams = H3_QPACK_BLOCKED;
    int rv = server ? nghttp3_conn_server_new(&c->conn, &cb, &settings, &h3_mem, c)
                    : nghttp3_conn_client_new(&c->conn, &cb, &settings, &h3_mem, c);
    if (rv != 0) {
        h3_free_mem(c, NULL);
        return NULL;
    }
    if (server) {
        nghttp3_conn_set_max_client_streams_bidi(c->conn, H3_MAX_STREAMS);
    }
    return c;
}

/* A server's connection; its field sections are limited to `max_field_section` bytes (RFC 9114 §4.2.2). */
h3_conn *h3_conn_server(uint64_t max_field_section) {
    return h3_new(1, max_field_section);
}

/* A client's connection, for web_http3's tests only (Web ADR 0014 amendment 4 item 5). */
h3_conn *h3_conn_client(uint64_t max_field_section) {
    return h3_new(0, max_field_section);
}

void h3_conn_free(h3_conn *c) {
    if (!c) {
        return;
    }
    nghttp3_conn_del(c->conn);
    while (c->bodies) {
        h3_body *b = c->bodies;
        c->bodies = b->next;
        h3_body_free(b);
    }
    while (c->tracks) {
        h3_track *t = c->tracks;
        c->tracks = t->next;
        ceesafe_free(t, CEE_ALLOC_FOREIGN);
    }
    h3_free_mem(c->queue.data, NULL);
    h3_free_mem(c->queue.events, NULL);
    h3_free_mem(c, NULL);
}

/* Binds the unidirectional streams CeeSafe opened: the control stream and QPACK's encoder and decoder streams. */
int32_t h3_bind(h3_conn *c, int64_t control, int64_t encoder, int64_t decoder) {
    int rv = nghttp3_conn_bind_control_stream(c->conn, control);
    if (rv != 0) {
        return rv;
    }
    return nghttp3_conn_bind_qpack_streams(c->conn, encoder, decoder);
}

/* ---- Errors ---- */

/* The HTTP/3 application error code to close the QUIC connection with after `rv` (RFC 9114 §8.1). */
uint64_t h3_app_error(int32_t rv) {
    return nghttp3_err_infer_quic_app_error_code(rv);
}

/* 1 if the queue could not grow (memory): the connection must close. */
int32_t h3_failed(h3_conn *c) {
    return c->queue.failed ? 1 : 0;
}

/* ---- In ---- */

/* Feeds `len` bytes QUIC delivered on `stream`, the stream's end with them if `fin`: how many nghttp3 consumed
   (framing and the control and QPACK streams; not DATA's payload, whose credit goes back as it is read), whose credit
   CeeSafe gives back at once; or nghttp3's error, which closes the connection. `now` is the caller's monotonic clock
   in nanoseconds: with it nghttp3's "glitch" rate limiter runs (H3_EXCESSIVE_LOAD past 1000 suspicious events, then
   33 a second; nghttp3_conn_read_stream2), which the version without a time leaves off. */
static int h3_close_now(h3_conn *c, int64_t stream, uint64_t code);

int64_t h3_read(h3_conn *c, int64_t stream, const uint8_t *bytes, size_t len, int32_t fin, uint64_t now) {
    nghttp3_ssize n = nghttp3_conn_read_stream2(c->conn, stream, bytes, len, fin ? 1 : 0, now);
    if (n >= 0 && c->queue.failed) {
        return NGHTTP3_ERR_NOMEM;
    }
    if (n < 0) {
        return (int64_t)n;
    }
    /* Closes QUIC made before nghttp3 had read everything (h3_close_stream): this read may have unblocked them (the
       QPACK encoder stream's instructions arrive), so those whose end has now come close. */
    for (h3_track *t = c->tracks; t;) {
        h3_track *next = t->next;
        if (t->deferred && t->ended) {
            int rv = h3_close_now(c, t->id, t->code);
            if (rv != 0) {
                return rv;
            }
        }
        t = next;
    }
    return (int64_t)n;
}

/* QUIC says the peer reset `stream` or asked us to stop sending on it: nghttp3 stops reading it. */
int32_t h3_shutdown_read(h3_conn *c, int64_t stream) {
    h3_track *t = h3_track_get(c, stream, 1);
    if (!t) {
        return NGHTTP3_ERR_NOMEM;
    }
    t->reset = 1;
    if (t->deferred) {
        return h3_close_now(c, stream, t->code);
    }
    return nghttp3_conn_shutdown_stream_read(c->conn, stream);
}

static int h3_close_now(h3_conn *c, int64_t stream, uint64_t code) {
    int rv = nghttp3_conn_close_stream(c->conn, stream, code);
    h3_body_drop(c, stream);
    h3_track_drop(c, stream);
    if (rv == 0 && c->queue.failed) {
        return NGHTTP3_ERR_NOMEM;
    }
    return rv == NGHTTP3_ERR_STREAM_NOT_FOUND ? 0 : rv;
}

/* QUIC closed `stream` with `code`. A bidirectional stream nghttp3 has not finished reading, and the peer did not
   reset, is closed once it has: QUIC delivered everything on it, but nghttp3 may hold a header block blocked on QPACK
   instructions that have yet to arrive on the encoder stream (RFC 9204 §2.1.2), and closing now would drop it
   (nghttp3_conn_close_stream deletes the stream as it is). h3_read closes it when its end comes. */
int32_t h3_close_stream(h3_conn *c, int64_t stream, uint64_t code) {
    if ((stream & 2) == 0) {
        h3_track *t = h3_track_get(c, stream, 1);
        if (!t) {
            return NGHTTP3_ERR_NOMEM;
        }
        if (!t->ended && !t->reset) {
            t->deferred = 1;
            t->code = code;
            return 0;
        }
    }
    return h3_close_now(c, stream, code);
}

/* ---- Out ---- */

/* The next bytes nghttp3 has for QUIC, copied into `out` (at most `cap`): their count, with the stream in *stream
   (-1 when nothing is waiting) and *fin set when they end it. Then h3_wrote says how many QUIC took. */
int64_t h3_pull(h3_conn *c, int64_t *stream, int32_t *fin, uint8_t *out, size_t cap) {
    nghttp3_vec vec[H3_READ_VECS];
    int64_t id = -1;
    int f = 0;
    nghttp3_ssize n = nghttp3_conn_writev_stream(c->conn, &id, &f, vec, H3_READ_VECS);
    if (n < 0) {
        return n;
    }
    *stream = id;
    size_t copied = 0;
    int whole = 1;
    for (nghttp3_ssize i = 0; i < n; i++) {
        size_t take = vec[i].len;
        if (take > cap - copied) {
            take = cap - copied;
            whole = 0;
        }
        memcpy(out + copied, vec[i].base, take);
        copied += take;
        if (!whole) {
            break;
        }
    }
    *fin = (f && whole) ? 1 : 0;
    if (c->queue.failed) {
        return NGHTTP3_ERR_NOMEM;
    }
    return (int64_t)copied;
}

/* QUIC took `n` of the bytes h3_pull gave for `stream` (and their FIN, with n = 0 when only a FIN was given). QUIC
   holds its own copy, so they are also reported acknowledged: nghttp3 frees them now. */
int32_t h3_wrote(h3_conn *c, int64_t stream, uint64_t n) {
    int rv = nghttp3_conn_add_write_offset(c->conn, stream, n);
    if (rv != 0) {
        return rv;
    }
    rv = nghttp3_conn_add_ack_offset(c->conn, stream, n);
    if (rv == 0 && c->queue.failed) {
        return NGHTTP3_ERR_NOMEM;
    }
    return rv;
}

/* QUIC holds `stream` full: nghttp3 offers it nothing more until h3_unblock. */
void h3_block(h3_conn *c, int64_t stream) {
    nghttp3_conn_block_stream(c->conn, stream);
}

int32_t h3_unblock(h3_conn *c, int64_t stream) {
    return nghttp3_conn_unblock_stream(c->conn, stream);
}

/* Field lists from CeeSafe: each field as a 2-byte name length and a 4-byte value length (big-endian), then the
   name, then the value. Names are lowercase (RFC 9114 §4.2). nghttp3 copies what it is given. */
static nghttp3_nv *h3_fields(const uint8_t *bytes, size_t len, size_t *count) {
    size_t n = 0;
    for (size_t at = 0; at < len;) {
        if (len - at < 6) {
            return NULL;
        }
        size_t nl = ((size_t)bytes[at] << 8) | bytes[at + 1];
        size_t vl = ((size_t)bytes[at + 2] << 24) | ((size_t)bytes[at + 3] << 16) | ((size_t)bytes[at + 4] << 8) |
                    bytes[at + 5];
        if (nl + vl > len - at - 6) {
            return NULL;
        }
        at += 6 + nl + vl;
        n += 1;
    }
    nghttp3_nv *nva = h3_calloc(n ? n : 1u, sizeof *nva, NULL);
    if (!nva) {
        return NULL;
    }
    size_t k = 0;
    for (size_t at = 0; at < len; k++) {
        size_t nl = ((size_t)bytes[at] << 8) | bytes[at + 1];
        size_t vl = ((size_t)bytes[at + 2] << 24) | ((size_t)bytes[at + 3] << 16) | ((size_t)bytes[at + 4] << 8) |
                    bytes[at + 5];
        nva[k].name = (uint8_t *)(uintptr_t)(bytes + at + 6);
        nva[k].namelen = nl;
        nva[k].value = (uint8_t *)(uintptr_t)(bytes + at + 6 + nl);
        nva[k].valuelen = vl;
        nva[k].flags = NGHTTP3_NV_FLAG_NONE;
        at += 6 + nl + vl;
    }
    *count = n;
    return nva;
}

static const nghttp3_data_reader h3_reader = {cb_read_data};

/* Submits a head on `stream`: a server's response (`request` 0) or a test client's request (`request` 1). With
   `has_body`, its body follows through h3_send_body; without, the head ends the stream. 0, NGHTTP3_ERR_INVALID_ARGUMENT
   for a malformed field list, or nghttp3's error. */
int32_t h3_submit(h3_conn *c, int64_t stream, int32_t request, const uint8_t *fields, size_t len, int32_t has_body) {
    size_t n = 0;
    nghttp3_nv *nva = h3_fields(fields, len, &n);
    if (!nva) {
        return len ? NGHTTP3_ERR_INVALID_ARGUMENT : NGHTTP3_ERR_NOMEM;
    }
    if (has_body && !h3_find(c, stream) && !h3_body_new(c, stream)) {
        h3_free_mem(nva, NULL);
        return NGHTTP3_ERR_NOMEM;
    }
    const nghttp3_data_reader *dr = has_body ? &h3_reader : NULL;
    int rv = request ? nghttp3_conn_submit_request(c->conn, stream, nva, n, dr, NULL)
                     : nghttp3_conn_submit_response(c->conn, stream, nva, n, dr);
    h3_free_mem(nva, NULL);
    if (rv != 0 && has_body) {
        h3_body_drop(c, stream);
    }
    return rv;
}

/* Takes up to `len` body bytes for `stream`, and ends the body after them if `end`: how many were taken (fewer when
   the stream holds H3_BODY_HOLD; H3_EV_BODY_ROOM says when to offer the rest), or a negative nghttp3 error. A FIN is
   taken only with the last byte. */
int64_t h3_send_body(h3_conn *c, int64_t stream, const uint8_t *bytes, size_t len, int32_t end) {
    h3_body *b = h3_find(c, stream);
    if (!b || b->ended) {
        return NGHTTP3_ERR_STREAM_NOT_FOUND;
    }
    uint64_t held = b->end - b->base;
    size_t room = held >= H3_BODY_HOLD ? 0 : (size_t)(H3_BODY_HOLD - held);
    size_t take = len < room ? len : room;
    size_t done = 0;
    while (done < take) {
        if (!b->tail || b->tail->length == H3_CHUNK) {
            h3_chunk *fresh = h3_malloc(sizeof *fresh, NULL);
            if (!fresh) {
                if (done == 0) {
                    return NGHTTP3_ERR_NOMEM;
                }
                break;
            }
            fresh->next = NULL;
            fresh->length = 0;
            if (b->tail) {
                b->tail->next = fresh;
            } else {
                b->head = fresh;
                b->base = b->end;
            }
            b->tail = fresh;
        }
        size_t n = H3_CHUNK - b->tail->length;
        if (n > take - done) {
            n = take - done;
        }
        memcpy(b->tail->bytes + b->tail->length, bytes + done, n);
        b->tail->length += n;
        b->end += n;
        done += n;
    }
    if (done < len) {
        b->full = 1;
    } else if (end) {
        b->ended = 1;
    }
    int rv = nghttp3_conn_resume_stream(c->conn, stream);
    if (rv != 0) {
        return rv;
    }
    return (int64_t)done;
}

/* Ends `stream`'s body with trailer fields (encoded as for h3_submit). */
int32_t h3_trailers(h3_conn *c, int64_t stream, const uint8_t *fields, size_t len) {
    h3_body *b = h3_find(c, stream);
    if (!b || b->ended) {
        return NGHTTP3_ERR_STREAM_NOT_FOUND;
    }
    size_t n = 0;
    nghttp3_nv *nva = h3_fields(fields, len, &n);
    if (!nva) {
        return len ? NGHTTP3_ERR_INVALID_ARGUMENT : NGHTTP3_ERR_NOMEM;
    }
    b->ended = 1;
    b->trailers = 1;
    int rv = nghttp3_conn_submit_trailers(c->conn, stream, nva, n);
    h3_free_mem(nva, NULL);
    if (rv != 0) {
        return rv;
    }
    return nghttp3_conn_resume_stream(c->conn, stream);
}

/* Our side of `stream` is reset (web_quic's resetSending): nghttp3 sends nothing more on it. */
void h3_shutdown_write(h3_conn *c, int64_t stream) {
    nghttp3_conn_shutdown_stream_write(c->conn, stream);
    h3_body_drop(c, stream);
}

/* GOAWAY: first a notice (the largest ID, RFC 9114 §5.2), then, once in-flight requests have had time to arrive, the
   final one with the last stream accepted. Each at most once, in that order: a GOAWAY's ID may not grow, and nghttp3
   asserts it (nghttp3_conn_shutdown), which tests/fuzz/h3 found when it called them in any order. */
int32_t h3_goaway_notice(h3_conn *c) {
    return nghttp3_conn_submit_shutdown_notice(c->conn);
}

int32_t h3_goaway(h3_conn *c) {
    return nghttp3_conn_shutdown(c->conn);
}

/* 1 when, after GOAWAY, every stream is done and the connection may close (nghttp3_conn_is_drained). */
int32_t h3_drained(h3_conn *c) {
    return nghttp3_conn_is_drained(c->conn) ? 1 : 0;
}

/* ---- Events ---- */

/* The next event: its kind (0 when there is none), its data copied into `buf` (at most `cap` bytes). An event whose
   data does not fit is left queued and reported as H3_EV_TOO_SMALL with its length in *length. */
uint32_t h3_next_event(h3_conn *c, uint8_t *buf, size_t cap, int64_t *stream, uint32_t *flags, uint64_t *code,
                       size_t *length) {
    h3_queue *q = &c->queue;
    if (q->count == 0) {
        q->head = 0;
        q->used = 0;
        return 0;
    }
    h3_event *e = &q->events[q->head];
    if (e->length > cap) {
        *length = e->length;
        return H3_EV_TOO_SMALL;
    }
    if (e->length > 0) { /* data is NULL until an event carries bytes; memcpy from NULL is undefined even for 0 */
        memcpy(buf, q->data + e->offset, e->length);
    }
    *stream = e->stream;
    *flags = e->flags;
    *code = e->code;
    *length = e->length;
    uint32_t kind = e->kind;
    q->head += 1;
    q->count -= 1;
    if (q->count == 0) {
        q->head = 0;
        q->used = 0;
    }
    return kind;
}
