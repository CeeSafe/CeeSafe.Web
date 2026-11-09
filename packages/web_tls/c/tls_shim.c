/*
 * web_tls's OpenSSL shim (Web ADR 0002 §3.2, amendment 1): OpenSSL over memory BIOs, so it never touches a socket;
 * CeeSafe does every read and write. The contract:
 *   - no pointer from CeeSafe is kept after a call returns;
 *   - no OpenSSL-owned pointer is returned; data is copied out;
 *   - every length is a size_t and is checked;
 *   - SSL_get_error is mapped to wt_status in one place (status_of);
 *   - no I/O, and no allocation beyond OpenSSL's;
 *   - OpenSSL allocates through CeeSafe's allocator for C code (CEE_ALLOC_FOREIGN, CeeSafe ADR 0060; Web ADR 0002
 *     amendment 5), so the allocation-failure sweep reaches OpenSSL's out-of-memory paths. Built without the CeeSafe
 *     runtime (the fuzz harness), WT_STANDALONE leaves OpenSSL its own allocator.
 * Every exported function is in the ADR's table.
 */
#include <openssl/core_names.h>
#include <openssl/decoder.h>
#include <openssl/encoder.h>
#include <openssl/err.h>
#include <openssl/kdf.h>
#include <openssl/pem.h>
#include <openssl/rand.h>
#include <openssl/ssl.h>
#include <openssl/x509v3.h>
#include <stdint.h>
#include <string.h>
#ifndef WT_STANDALONE
#include "ceesafe/foreign.h"
#endif

#if OPENSSL_VERSION_NUMBER < 0x30000000L
#error "web_tls needs OpenSSL 3.0 or later (Web ADR 0002 §3.1)"
#endif

/* Results below zero; byte counts are >= 0. */
enum {
    WT_DONE = 0,
    WT_WANT_READ = -1,
    WT_WANT_WRITE = -2,
    WT_CLOSED = -3,
    WT_ERROR = -4,
    WT_FULL = -5, /* wt_feed: the read BIO is at its limit */
};

/* Why the last WT_ERROR happened: web_core's TlsReason, in its order. */
enum {
    WT_REASON_NONE = 0,
    WT_REASON_HANDSHAKE = 1,
    WT_REASON_CERTIFICATE = 2,
    WT_REASON_PROTOCOL = 3,
    WT_REASON_ALERT = 4,
    WT_REASON_INTERNAL = 5,
};

/* The most ciphertext waiting in the read BIO: two full records (Web ADR 0002 §3.4). */
#define WT_READ_BIO_LIMIT (2 * (16384 + 2048 + 5))
/* The longest ALPN list a configuration holds, in wire form. */
#define WT_ALPN_MAX 64
/* The longest certificate list a peer may send (SSL_CTX_set_max_cert_list). */
#define WT_MAX_CERT_LIST (64 * 1024)
/* The most names a server's SNI table holds. */
#define WT_NAMES_MAX 64

typedef struct wt_alpn_list {
    unsigned char bytes[WT_ALPN_MAX];
    unsigned int length;
} wt_alpn_list;

/* A ticket key: its name (sent in the ticket) and the AES-256 and HMAC-SHA256 keys. */
typedef struct wt_ticket_key {
    unsigned char name[16];
    unsigned char aes[32];
    unsigned char hmac[32];
    int set;
} wt_ticket_key;

/* A server name and the configuration holding its certificate. */
typedef struct wt_name {
    char name[256];
    SSL_CTX *ctx;
} wt_name;

/* A server configuration's state beyond OpenSSL's (Web ADR 0002 amendment 3): the SNI table, the default certificate
   swapped by a reload, the ticket keys, and the roots for client certificates. Guarded by `lock`. */
typedef struct wt_front {
    CRYPTO_RWLOCK *lock;
    SSL_CTX *current; /* the default certificate after a reload; NULL: the front configuration's own */
    wt_name names[WT_NAMES_MAX];
    int count;
    wt_ticket_key keys[2]; /* [0] issues and accepts; [1], the previous, only accepts */
    X509_STORE *client_roots;
    STACK_OF(X509_NAME) *client_names;
} wt_front;

typedef struct wt_ctx {
    SSL *ssl;
    SSL_CTX *front; /* a server's configuration, as given to wt_ctx_new; NULL for a client */
    BIO *in;  /* ciphertext from the peer, fed by CeeSafe */
    BIO *out; /* ciphertext for the peer, taken by CeeSafe */
    int reason;
    unsigned long code;
    int handshaken;
} wt_ctx;

static int alpn_index = -1;

static void alpn_free(void *parent, void *ptr, CRYPTO_EX_DATA *ad, int idx, long argl, void *argp) {
    (void)parent; (void)ad; (void)idx; (void)argl; (void)argp;
    OPENSSL_free(ptr);
}

/* The ex_data index holding a configuration's ALPN list; made once. CRYPTO_get_ex_new_index is thread-safe. */
static int alpn_slot(void) {
    if (alpn_index < 0) {
        alpn_index = SSL_CTX_get_ex_new_index(0, NULL, NULL, NULL, alpn_free);
    }
    return alpn_index;
}

static int front_index = -1;

static void front_free(void *parent, void *ptr, CRYPTO_EX_DATA *ad, int idx, long argl, void *argp) {
    (void)parent; (void)ad; (void)idx; (void)argl; (void)argp;
    wt_front *f = ptr;
    if (f == NULL) {
        return;
    }
    for (int i = 0; i < f->count; i++) {
        SSL_CTX_free(f->names[i].ctx);
    }
    SSL_CTX_free(f->current);
    X509_STORE_free(f->client_roots);
    sk_X509_NAME_pop_free(f->client_names, X509_NAME_free);
    CRYPTO_THREAD_lock_free(f->lock);
    OPENSSL_cleanse(f->keys, sizeof f->keys);
    OPENSSL_free(f);
}

static int front_slot(void) {
    if (front_index < 0) {
        front_index = SSL_CTX_get_ex_new_index(0, NULL, NULL, NULL, front_free);
    }
    return front_index;
}

static wt_front *front_of(SSL_CTX *ctx) {
    return ctx == NULL ? NULL : SSL_CTX_get_ex_data(ctx, front_slot());
}

/* A server SSL's configuration (its "front" SSL_CTX, before any SNI switch), in a slot of the SSL's own, for the
   ticket callback. Not the SSL's app data: QUIC's SSLs carry ngtcp2's connection reference there (Web ADR 0014
   §3.4), so web_tls's callbacks must not read it. */
/* For web_quic's shim (Web ADR 0014 §3.4). */
void wt_init(void);
int32_t wt_ssl_use_front(SSL *ssl, SSL_CTX *ctx);

static int ssl_front_index = -1;

static int ssl_front_slot(void) {
    if (ssl_front_index < 0) {
        ssl_front_index = SSL_get_ex_new_index(0, NULL, NULL, NULL, NULL);
    }
    return ssl_front_index;
}

/* For web_quic's shim: binds a QUIC server SSL made from `ctx` (a web_tls server configuration) to it, as
   wt_ctx_new binds its own, so the ticket callback finds the configuration's keys. 1, or 0 on failure. */
int32_t wt_ssl_use_front(SSL *ssl, SSL_CTX *ctx) {
    return SSL_set_ex_data(ssl, ssl_front_slot(), ctx) ? 1 : 0;
}

static int new_ticket_key(wt_ticket_key *k) {
    k->set = RAND_bytes(k->name, sizeof k->name) > 0 && RAND_bytes(k->aes, sizeof k->aes) > 0 &&
             RAND_bytes(k->hmac, sizeof k->hmac) > 0;
    return k->set;
}

/* Lower-cases `in` into `out` (NUL-terminated); 0 if it is empty, too long, or not a host name or `*.` pattern. */
static int host_name(const uint8_t *in, size_t len, char *out, size_t cap) {
    if (len == 0 || len >= cap) {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        unsigned char b = in[i];
        int ok = (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9') || b == '-' || b == '.' || (b == '*' && i == 0);
        if (b >= 'A' && b <= 'Z') {
            b = (unsigned char)(b - 'A' + 'a');
            ok = 1;
        }
        if (!ok) {
            return 0;
        }
        out[i] = (char)b;
    }
    out[len] = '\0';
    return out[0] != '*' || (len > 2 && out[1] == '.');
}

/* Whether `pattern` ("api.example.com", or "*.example.com" for one more label) names `host`. */
static int name_matches(const char *pattern, const char *host) {
    if (pattern[0] != '*') {
        return OPENSSL_strcasecmp(pattern, host) == 0;
    }
    const char *dot = strchr(host, '.');
    return dot != NULL && dot != host && OPENSSL_strcasecmp(pattern + 1, dot) == 0;
}

/* The server's certificate for this handshake: the SNI table's entry for the name the client asked for (an exact
   name before a wildcard), else the default (a reloaded one, or the front configuration's own). OpenSSL calls this
   for every ClientHello, with or without SNI. */
static int pick_certificate(SSL *ssl, int *alert, void *arg) {
    (void)alert;
    wt_front *f = front_of(arg);
    if (f == NULL) {
        return SSL_TLSEXT_ERR_OK;
    }
    const char *host = SSL_get_servername(ssl, TLSEXT_NAMETYPE_host_name);
    SSL_CTX *chosen = NULL;
    if (!CRYPTO_THREAD_read_lock(f->lock)) {
        return SSL_TLSEXT_ERR_ALERT_FATAL;
    }
    if (host != NULL) {
        for (int i = 0; i < f->count && chosen == NULL; i++) {
            if (f->names[i].name[0] != '*' && name_matches(f->names[i].name, host)) {
                chosen = f->names[i].ctx;
            }
        }
        for (int i = 0; i < f->count && chosen == NULL; i++) {
            if (f->names[i].name[0] == '*' && name_matches(f->names[i].name, host)) {
                chosen = f->names[i].ctx;
            }
        }
    }
    if (chosen == NULL) {
        chosen = f->current;
    }
    if (chosen != NULL) {
        SSL_set_SSL_CTX(ssl, chosen); /* takes its own reference */
    }
    CRYPTO_THREAD_unlock(f->lock);
    return SSL_TLSEXT_ERR_OK;
}

/* Session tickets with the configuration's keys (OpenSSL 3's EVP callback): issued with the current key, accepted
   with the current or the previous one (then renewed: 2). */
static int ticket_key(SSL *ssl, unsigned char key_name[16], unsigned char *iv, EVP_CIPHER_CTX *cipher,
                      EVP_MAC_CTX *mac, int enc) {
    wt_front *f = front_of(SSL_get_ex_data(ssl, ssl_front_slot()));
    if (f == NULL || !CRYPTO_THREAD_read_lock(f->lock)) {
        return -1;
    }
    wt_ticket_key keys[2];
    memcpy(keys, f->keys, sizeof keys);
    CRYPTO_THREAD_unlock(f->lock);
    int result = -1;
    wt_ticket_key *k = NULL;
    if (enc) {
        k = &keys[0];
        if (k->set && RAND_bytes(iv, 16) > 0) {
            memcpy(key_name, k->name, 16);
            result = EVP_EncryptInit_ex(cipher, EVP_aes_256_cbc(), NULL, k->aes, iv) ? 1 : -1;
        }
    } else {
        result = 0; /* an unknown key: a full handshake */
        for (int i = 0; i < 2; i++) {
            if (keys[i].set && memcmp(key_name, keys[i].name, 16) == 0) {
                k = &keys[i];
                result = !EVP_DecryptInit_ex(cipher, EVP_aes_256_cbc(), NULL, k->aes, iv) ? -1 : (i == 0 ? 1 : 2);
            }
        }
    }
    if (result > 0) {
        OSSL_PARAM params[3];
        params[0] = OSSL_PARAM_construct_octet_string(OSSL_MAC_PARAM_KEY, k->hmac, sizeof k->hmac);
        params[1] = OSSL_PARAM_construct_utf8_string(OSSL_MAC_PARAM_DIGEST, (char *)"SHA256", 0);
        params[2] = OSSL_PARAM_construct_end();
        if (!EVP_MAC_CTX_set_params(mac, params)) {
            result = -1;
        }
    }
    OPENSSL_cleanse(keys, sizeof keys);
    return result;
}

/* The server's ALPN choice: its own first protocol the client also offers. No overlap: no ALPN, and the
   application protocol defaults to HTTP/1.1 (the server does not refuse the connection). */
static int alpn_select(SSL *ssl, const unsigned char **out, unsigned char *outlen, const unsigned char *in,
                       unsigned int inlen, void *arg) {
    (void)arg;
    wt_alpn_list *mine = SSL_CTX_get_ex_data(SSL_get_SSL_CTX(ssl), alpn_slot());
    if (mine == NULL || mine->length == 0) {
        return SSL_TLSEXT_ERR_NOACK;
    }
    unsigned char *chosen = NULL;
    if (SSL_select_next_proto(&chosen, outlen, mine->bytes, mine->length, in, inlen) != OPENSSL_NPN_NEGOTIATED) {
        return SSL_TLSEXT_ERR_NOACK;
    }
    *out = chosen;
    return SSL_TLSEXT_ERR_OK;
}

/* ---- Configurations (one SSL_CTX, shared by many connections; OpenSSL 3 makes that thread-safe) ---- */

/* The functions web_tls calls (src/ffi.cee), declared before their definitions (gcc's -Wmissing-prototypes). */
SSL_CTX *wt_cfg_new(int32_t server);
void wt_cfg_free(SSL_CTX *ctx);
int32_t wt_cfg_use_pem(SSL_CTX *ctx, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len);
int32_t wt_cfg_trust_pem(SSL_CTX *ctx, const uint8_t *pem, size_t len);
int32_t wt_cfg_trust_default(SSL_CTX *ctx);
int32_t wt_cfg_set_alpn(SSL_CTX *ctx, const uint8_t *wire, size_t len);
int32_t wt_cfg_add_name(SSL_CTX *front, const uint8_t *name, size_t name_len, const uint8_t *chain, size_t chain_len,
                        const uint8_t *key, size_t key_len);
int32_t wt_cfg_reload(SSL_CTX *front, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len);
int32_t wt_cfg_rotate_tickets(SSL_CTX *front);
int32_t wt_cfg_client_auth(SSL_CTX *front, int32_t required, const uint8_t *pem, size_t len);
SSL_SESSION *wt_ctx_session(wt_ctx *c);
void wt_session_free(SSL_SESSION *s);
int32_t wt_ctx_offer_session(wt_ctx *c, SSL_SESSION *s);
int32_t wt_ctx_resumed(wt_ctx *c);
wt_ctx *wt_ctx_new(SSL_CTX *cfg, int32_t server, const uint8_t *host, size_t host_len);
void wt_ctx_free(wt_ctx *c);
int64_t wt_feed(wt_ctx *c, const uint8_t *in, size_t len);
int64_t wt_take(wt_ctx *c, uint8_t *out, size_t cap);
uint64_t wt_pending(wt_ctx *c);
int32_t wt_handshake(wt_ctx *c);
int64_t wt_read(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_write(wt_ctx *c, const uint8_t *in, size_t len);
int32_t wt_shutdown(wt_ctx *c);
int64_t wt_alpn(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_sni(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_peer_cert_der(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_peer_subject(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_peer_names(wt_ctx *c, uint8_t *out, size_t cap);
int32_t wt_version(wt_ctx *c);
int32_t wt_reason(wt_ctx *c);
uint64_t wt_error(wt_ctx *c, uint8_t *out, size_t cap, size_t *len);
size_t wt_openssl_version(uint8_t *out, size_t cap);

/* A server (server != 0) or client configuration with the settings of Web ADR 0002 §3.3; NULL on failure. */
#ifndef WT_STANDALONE
/* Set only on the thread running OpenSSL's one-time initialisation, and only while it runs: those allocations go under
   CEE_ALLOC_FOREIGN_INIT (CeeSafe ADR 0061), never failed, since OpenSSL cannot survive a failure there (a failed
   allocation raises an error, which re-enters the pthread_once running the initialisation). */
static _Thread_local int initialising;

static uint32_t foreign_number(void) {
    return initialising ? CEE_ALLOC_FOREIGN_INIT : CEE_ALLOC_FOREIGN;
}

static void *foreign_malloc(size_t n, const char *file, int line) {
    (void)file;
    (void)line;
    return ceesafe_alloc(n, foreign_number());
}

static void *foreign_realloc(void *p, size_t n, const char *file, int line) {
    (void)file;
    (void)line;
    return ceesafe_realloc(p, n, foreign_number());
}

static void foreign_free(void *p, const char *file, int line) {
    (void)file;
    (void)line;
    ceesafe_free(p, CEE_ALLOC_FOREIGN);
}

/* Callbacks for the *_do_all_provided walks below: fetching is the point, so they do nothing. */
static void each_cipher(EVP_CIPHER *x, void *arg) { (void)x; (void)arg; }
static void each_md(EVP_MD *x, void *arg) { (void)x; (void)arg; }
static void each_mac(EVP_MAC *x, void *arg) { (void)x; (void)arg; }
static void each_kdf(EVP_KDF *x, void *arg) { (void)x; (void)arg; }
static void each_rand(EVP_RAND *x, void *arg) { (void)x; (void)arg; }
static void each_keymgmt(EVP_KEYMGMT *x, void *arg) { (void)x; (void)arg; }
static void each_signature(EVP_SIGNATURE *x, void *arg) { (void)x; (void)arg; }
static void each_keyexch(EVP_KEYEXCH *x, void *arg) { (void)x; (void)arg; }
static void each_kem(EVP_KEM *x, void *arg) { (void)x; (void)arg; }
static void each_asym(EVP_ASYM_CIPHER *x, void *arg) { (void)x; (void)arg; }
static void each_decoder(OSSL_DECODER *x, void *arg) { (void)x; (void)arg; }
static void each_encoder(OSSL_ENCODER *x, void *arg) { (void)x; (void)arg; }

static CRYPTO_ONCE route_once = CRYPTO_ONCE_STATIC_INIT;

/* OpenSSL accepts allocation functions only before its first allocation; a configuration is the first thing the shim
   makes, so its creation sets them. If something else in the process used OpenSSL first, OpenSSL refuses and keeps
   its own allocator, which is safe: every block is then freed by the allocator that made it. CRYPTO_THREAD_run_once
   allocates nothing. */
static void route_allocations(void) {
    if (!CRYPTO_set_mem_functions(foreign_malloc, foreign_realloc, foreign_free)) {
        return; /* OpenSSL already allocated: it keeps its own allocator */
    }
    initialising = 1;
    (void)OPENSSL_init_ssl(OPENSSL_INIT_LOAD_SSL_STRINGS | OPENSSL_INIT_LOAD_CRYPTO_STRINGS, NULL);
    /* OpenSSL finishes its global setup lazily: the default provider at the first fetch, and its table of algorithm
       names one kind at a time, at the first use of each (ciphers, digests, signatures, decoders, …). OpenSSL 3.0.22
       leaks the entries it was adding when an allocation fails there (Web ADR 0002 amendment 5.7). So the setup walks
       every kind once, here, outside the sweep, then makes and frees one configuration. */
    EVP_CIPHER_do_all_provided(NULL, each_cipher, NULL);
    EVP_MD_do_all_provided(NULL, each_md, NULL);
    EVP_MAC_do_all_provided(NULL, each_mac, NULL);
    EVP_KDF_do_all_provided(NULL, each_kdf, NULL);
    EVP_RAND_do_all_provided(NULL, each_rand, NULL);
    EVP_KEYMGMT_do_all_provided(NULL, each_keymgmt, NULL);
    EVP_SIGNATURE_do_all_provided(NULL, each_signature, NULL);
    EVP_KEYEXCH_do_all_provided(NULL, each_keyexch, NULL);
    EVP_KEM_do_all_provided(NULL, each_kem, NULL);
    EVP_ASYM_CIPHER_do_all_provided(NULL, each_asym, NULL);
    OSSL_DECODER_do_all_provided(NULL, each_decoder, NULL);
    OSSL_ENCODER_do_all_provided(NULL, each_encoder, NULL);
    SSL_CTX_free(SSL_CTX_new(TLS_method()));
    initialising = 0;
}
#endif

/* Routes OpenSSL's allocations through CeeSafe's (route_allocations), once per process. For web_quic's shim, whose
   first OpenSSL call (ngtcp2_crypto_ossl_init) may come before any configuration is made: OpenSSL takes allocation
   functions only before its first allocation (Web ADR 0014 §3.4). */
void wt_init(void) {
#ifndef WT_STANDALONE
    (void)CRYPTO_THREAD_run_once(&route_once, route_allocations);
#endif
}

SSL_CTX *wt_cfg_new(int32_t server) {
    wt_init();
    SSL_CTX *ctx = SSL_CTX_new(server ? TLS_server_method() : TLS_client_method());
    if (ctx == NULL) {
        return NULL;
    }
    uint64_t options = SSL_OP_NO_RENEGOTIATION | SSL_OP_NO_COMPRESSION;
    if (server) {
        options |= SSL_OP_CIPHER_SERVER_PREFERENCE;
    }
    SSL_CTX_set_options(ctx, options);
    SSL_CTX_set_mode(ctx, SSL_MODE_RELEASE_BUFFERS);
    SSL_CTX_set_max_cert_list(ctx, WT_MAX_CERT_LIST);
    if (!SSL_CTX_set_min_proto_version(ctx, TLS1_2_VERSION) || !SSL_CTX_set_max_proto_version(ctx, TLS1_3_VERSION) ||
        !SSL_CTX_set_cipher_list(ctx, "ECDHE+AESGCM:ECDHE+CHACHA20")) {
        SSL_CTX_free(ctx);
        return NULL;
    }
    wt_alpn_list *alpn = OPENSSL_zalloc(sizeof *alpn);
    if (alpn == NULL || !SSL_CTX_set_ex_data(ctx, alpn_slot(), alpn)) {
        OPENSSL_free(alpn);
        SSL_CTX_free(ctx);
        return NULL;
    }
    if (!server) {
        SSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, NULL);
        return ctx;
    }
    SSL_CTX_set_alpn_select_cb(ctx, alpn_select, NULL);
    /* Resumption and client certificates need a session id context; a context switch copies the new one's. */
    static const unsigned char sid[] = "web_tls";
    wt_front *f = OPENSSL_zalloc(sizeof *f);
    if (!SSL_CTX_set_session_id_context(ctx, sid, sizeof sid - 1) || f == NULL ||
        (f->lock = CRYPTO_THREAD_lock_new()) == NULL || !new_ticket_key(&f->keys[0]) ||
        !SSL_CTX_set_ex_data(ctx, front_slot(), f)) {
        if (f != NULL) {
            CRYPTO_THREAD_lock_free(f->lock);
        }
        OPENSSL_free(f);
        SSL_CTX_free(ctx);
        return NULL;
    }
    SSL_CTX_set_tlsext_servername_callback(ctx, pick_certificate);
    SSL_CTX_set_tlsext_servername_arg(ctx, ctx);
    SSL_CTX_set_tlsext_ticket_key_evp_cb(ctx, ticket_key);
    return ctx;
}

/* A configuration holding one certificate, for a server's SNI table or a reload: the front's ALPN list, no table of
   its own. NULL on failure. */
static SSL_CTX *certificate_ctx(SSL_CTX *front, const uint8_t *chain, size_t chain_len, const uint8_t *key,
                                size_t key_len);
static void copy_alpn(SSL_CTX *to, SSL_CTX *from);

/* Drops this handle; connections made from it keep the configuration alive (SSL_CTX is reference counted). */
void wt_cfg_free(SSL_CTX *ctx) {
    SSL_CTX_free(ctx);
}

/* The certificate chain (PEM, the leaf first) and its private key (PEM). 1 on success. */
int32_t wt_cfg_use_pem(SSL_CTX *ctx, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len) {
    if (chain_len > INT32_MAX || key_len > INT32_MAX) {
        return 0;
    }
    int ok = 0;
    BIO *b = BIO_new_mem_buf(chain, (int)chain_len);
    BIO *k = BIO_new_mem_buf(key, (int)key_len);
    X509 *leaf = NULL;
    EVP_PKEY *pkey = NULL;
    if (b == NULL || k == NULL) {
        goto done;
    }
    leaf = PEM_read_bio_X509(b, NULL, NULL, NULL);
    if (leaf == NULL || !SSL_CTX_use_certificate(ctx, leaf)) {
        goto done;
    }
    SSL_CTX_clear_chain_certs(ctx);
    for (;;) {
        X509 *extra = PEM_read_bio_X509(b, NULL, NULL, NULL);
        if (extra == NULL) {
            break;
        }
        if (!SSL_CTX_add0_chain_cert(ctx, extra)) {
            X509_free(extra);
            goto done;
        }
    }
    pkey = PEM_read_bio_PrivateKey(k, NULL, NULL, NULL);
    if (pkey == NULL || !SSL_CTX_use_PrivateKey(ctx, pkey) || !SSL_CTX_check_private_key(ctx)) {
        goto done;
    }
    ok = 1;
done:
    ERR_clear_error(); /* PEM_read_bio_X509 leaves "no start line" at the end of the chain */
    X509_free(leaf);
    EVP_PKEY_free(pkey);
    BIO_free(b);
    BIO_free(k);
    return ok;
}

/* Adds every certificate in `pem` to the configuration's trust store. The number added; 0 if none or on failure. */
int32_t wt_cfg_trust_pem(SSL_CTX *ctx, const uint8_t *pem, size_t len) {
    if (len > INT32_MAX) {
        return 0;
    }
    BIO *b = BIO_new_mem_buf(pem, (int)len);
    if (b == NULL) {
        return 0;
    }
    X509_STORE *store = SSL_CTX_get_cert_store(ctx);
    int32_t added = 0;
    for (;;) {
        X509 *cert = PEM_read_bio_X509(b, NULL, NULL, NULL);
        if (cert == NULL) {
            break;
        }
        if (X509_STORE_add_cert(store, cert)) {
            added++;
        }
        X509_free(cert);
    }
    ERR_clear_error();
    BIO_free(b);
    return added;
}

/* Trusts the system's default CA locations: OpenSSL's defaults (a bundle or directory on Unix), and on Windows the
 * system's certificate store too, which OpenSSL's defaults do not reach (its winstore loader, OpenSSL 3.2 and later;
 * Web ADR 0002 amendment 4). 1 on success. */
int32_t wt_cfg_trust_default(SSL_CTX *ctx) {
    if (!SSL_CTX_set_default_verify_paths(ctx)) {
        return 0;
    }
#if defined(_WIN32) && OPENSSL_VERSION_NUMBER >= 0x30200000L
    if (!SSL_CTX_load_verify_store(ctx, "org.openssl.winstore://")) {
        ERR_clear_error();
        return 0;
    }
#endif
    return 1;
}

/* The ALPN protocols, in wire form (each a length byte and the name), in preference order. 1 on success. */
int32_t wt_cfg_set_alpn(SSL_CTX *ctx, const uint8_t *wire, size_t len) {
    wt_alpn_list *alpn = SSL_CTX_get_ex_data(ctx, alpn_slot());
    if (alpn == NULL || len > WT_ALPN_MAX) {
        return 0;
    }
    for (size_t i = 0; i < len; i += 1 + (size_t)wire[i]) {
        if (wire[i] == 0 || i + 1 + wire[i] > len) {
            return 0;
        }
    }
    memcpy(alpn->bytes, wire, len);
    alpn->length = (unsigned int)len;
    wt_front *f = front_of(ctx);
    if (f != NULL && CRYPTO_THREAD_write_lock(f->lock)) { /* the certificates already added choose the same way */
        for (int i = 0; i < f->count; i++) {
            copy_alpn(f->names[i].ctx, ctx);
        }
        if (f->current != NULL) {
            copy_alpn(f->current, ctx);
        }
        CRYPTO_THREAD_unlock(f->lock);
    }
    /* The client offers them; SSL_CTX_set_alpn_protos returns 0 on success. */
    return SSL_CTX_set_alpn_protos(ctx, wire, (unsigned int)len) == 0 ? 1 : 0;
}

/* The certificate a server presents to clients that ask for `name` by SNI ("api.example.com", or "*.example.com"
   for any one label before it); a name already in the table is replaced. 1 on success. */
int32_t wt_cfg_add_name(SSL_CTX *front, const uint8_t *name, size_t name_len, const uint8_t *chain, size_t chain_len,
                        const uint8_t *key, size_t key_len) {
    wt_front *f = front_of(front);
    char host[256];
    if (f == NULL || !host_name(name, name_len, host, sizeof host)) {
        return 0;
    }
    SSL_CTX *ctx = certificate_ctx(front, chain, chain_len, key, key_len);
    if (ctx == NULL || !CRYPTO_THREAD_write_lock(f->lock)) {
        SSL_CTX_free(ctx);
        return 0;
    }
    int at = f->count;
    for (int i = 0; i < f->count; i++) {
        if (OPENSSL_strcasecmp(f->names[i].name, host) == 0) {
            at = i;
        }
    }
    int ok = at < WT_NAMES_MAX;
    SSL_CTX *old = NULL;
    if (ok) {
        old = at < f->count ? f->names[at].ctx : NULL;
        memcpy(f->names[at].name, host, strlen(host) + 1);
        f->names[at].ctx = ctx;
        if (at == f->count) {
            f->count++;
        }
    }
    CRYPTO_THREAD_unlock(f->lock);
    SSL_CTX_free(ok ? old : ctx);
    return ok;
}

/* Replaces the default certificate: handshakes from now on present it; connections already made keep theirs. 1 on
   success; on failure the old one stays. */
int32_t wt_cfg_reload(SSL_CTX *front, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len) {
    wt_front *f = front_of(front);
    if (f == NULL) {
        return 0;
    }
    SSL_CTX *ctx = certificate_ctx(front, chain, chain_len, key, key_len);
    if (ctx == NULL || !CRYPTO_THREAD_write_lock(f->lock)) {
        SSL_CTX_free(ctx);
        return 0;
    }
    SSL_CTX *old = f->current;
    f->current = ctx;
    CRYPTO_THREAD_unlock(f->lock);
    SSL_CTX_free(old);
    return 1;
}

/* A new ticket key: tickets are issued with it from now on; the one it replaces still opens tickets (they are
   renewed), the one before that no longer does. 1 on success. */
int32_t wt_cfg_rotate_tickets(SSL_CTX *front) {
    wt_front *f = front_of(front);
    wt_ticket_key next;
    if (f == NULL || !new_ticket_key(&next) || !CRYPTO_THREAD_write_lock(f->lock)) {
        return 0;
    }
    f->keys[1] = f->keys[0];
    f->keys[0] = next;
    CRYPTO_THREAD_unlock(f->lock);
    OPENSSL_cleanse(&next, sizeof next);
    return 1;
}

/* Asks clients for a certificate verified against the roots in `pem`: required (`required` != 0: a client without
   one fails the handshake) or optional (a client may send none; one it sends must verify). 1 on success. */
int32_t wt_cfg_client_auth(SSL_CTX *front, int32_t required, const uint8_t *pem, size_t len) {
    wt_front *f = front_of(front);
    if (f == NULL || len > INT32_MAX) {
        return 0;
    }
    BIO *b = BIO_new_mem_buf(pem, (int)len);
    X509_STORE *roots = X509_STORE_new();
    STACK_OF(X509_NAME) *names = sk_X509_NAME_new_null();
    int added = 0;
    if (b != NULL && roots != NULL && names != NULL) {
        for (;;) {
            X509 *cert = PEM_read_bio_X509(b, NULL, NULL, NULL);
            if (cert == NULL) {
                break;
            }
            X509_NAME *n = X509_NAME_dup(X509_get_subject_name(cert));
            if (X509_STORE_add_cert(roots, cert) && n != NULL && sk_X509_NAME_push(names, n)) {
                added++;
            } else {
                X509_NAME_free(n);
            }
            X509_free(cert);
        }
    }
    ERR_clear_error();
    BIO_free(b);
    if (added == 0 || !CRYPTO_THREAD_write_lock(f->lock)) {
        X509_STORE_free(roots);
        sk_X509_NAME_pop_free(names, X509_NAME_free);
        return 0;
    }
    X509_STORE_free(f->client_roots);
    sk_X509_NAME_pop_free(f->client_names, X509_NAME_free);
    f->client_roots = roots;
    f->client_names = names;
    CRYPTO_THREAD_unlock(f->lock);
    int mode = SSL_VERIFY_PEER | SSL_VERIFY_CLIENT_ONCE | (required ? SSL_VERIFY_FAIL_IF_NO_PEER_CERT : 0);
    SSL_CTX_set_verify(front, mode, NULL);
    return 1;
}

static void copy_alpn(SSL_CTX *to, SSL_CTX *from) {
    wt_alpn_list *src = SSL_CTX_get_ex_data(from, alpn_slot());
    wt_alpn_list *dst = SSL_CTX_get_ex_data(to, alpn_slot());
    if (src != NULL && dst != NULL) {
        *dst = *src;
    }
}

static SSL_CTX *certificate_ctx(SSL_CTX *front, const uint8_t *chain, size_t chain_len, const uint8_t *key,
                                size_t key_len) {
    SSL_CTX *ctx = wt_cfg_new(1);
    if (ctx == NULL) {
        return NULL;
    }
    copy_alpn(ctx, front);
    if (!wt_cfg_use_pem(ctx, chain, chain_len, key, key_len)) {
        SSL_CTX_free(ctx);
        return NULL;
    }
    return ctx;
}

/* ---- Client sessions (resumption) ---- */

/* The connection's session, if a later connection can resume it (TLS 1.3: once a ticket has arrived, after the
   handshake); NULL otherwise. The caller frees it with wt_session_free. */
SSL_SESSION *wt_ctx_session(wt_ctx *c) {
    SSL_SESSION *s = SSL_get1_session(c->ssl);
    if (s != NULL && !SSL_SESSION_is_resumable(s)) {
        SSL_SESSION_free(s);
        return NULL;
    }
    return s;
}

void wt_session_free(SSL_SESSION *s) {
    SSL_SESSION_free(s);
}

/* Offers `s` for resumption in this client connection's handshake. 1 on success. */
int32_t wt_ctx_offer_session(wt_ctx *c, SSL_SESSION *s) {
    return SSL_set_session(c->ssl, s) ? 1 : 0;
}

/* Whether the handshake resumed a session. */
int32_t wt_ctx_resumed(wt_ctx *c) {
    return SSL_session_reused(c->ssl) ? 1 : 0;
}

/* ---- Connections ---- */

/* A connection on `cfg`: a server's, or a client's for `host` (verified against the certificate's SAN, never its CN,
   and sent as SNI unless it is an IP address). NULL on failure. */
wt_ctx *wt_ctx_new(SSL_CTX *cfg, int32_t server, const uint8_t *host, size_t host_len) {
    char name[256];
    if (!server && (host_len == 0 || host_len >= sizeof name)) {
        return NULL;
    }
    wt_ctx *c = OPENSSL_zalloc(sizeof *c);
    if (c == NULL) {
        return NULL;
    }
    c->ssl = SSL_new(cfg);
    c->in = BIO_new(BIO_s_mem());
    c->out = BIO_new(BIO_s_mem());
    if (c->ssl == NULL || c->in == NULL || c->out == NULL) {
        goto fail;
    }
    BIO_set_mem_eof_return(c->in, -1); /* an empty read BIO means "want more", not the end */
    SSL_set_bio(c->ssl, c->in, c->out); /* the SSL owns both BIOs now */
    SSL_set_app_data(c->ssl, c);
    if (server) {
        c->front = cfg;
        if (!SSL_set_ex_data(c->ssl, ssl_front_slot(), cfg)) {
            goto fail_ssl; /* after SSL_set_bio: the SSL owns both BIOs */
        }
        wt_front *f = front_of(cfg);
        /* The client-certificate roots and the names sent in CertificateRequest belong to this connection, so a
           switch to an SNI entry's configuration (pick_certificate) keeps them. */
        if (f != NULL && CRYPTO_THREAD_read_lock(f->lock)) {
            int ok = 1;
            if (f->client_roots != NULL) {
                STACK_OF(X509_NAME) *names = SSL_dup_CA_list(f->client_names);
                ok = SSL_set1_verify_cert_store(c->ssl, f->client_roots) && names != NULL;
                if (ok) {
                    SSL_set_client_CA_list(c->ssl, names);
                } else {
                    sk_X509_NAME_pop_free(names, X509_NAME_free);
                }
            }
            CRYPTO_THREAD_unlock(f->lock);
            if (!ok) {
                goto fail_ssl;
            }
        }
        SSL_set_accept_state(c->ssl);
        return c;
    }
    memcpy(name, host, host_len);
    name[host_len] = '\0';
    if (memchr(name, '\0', host_len) != NULL) {
        goto fail_ssl;
    }
    X509_VERIFY_PARAM *param = SSL_get0_param(c->ssl);
    ASN1_OCTET_STRING *ip = a2i_IPADDRESS(name);
    if (ip != NULL) {
        ASN1_OCTET_STRING_free(ip);
        if (!X509_VERIFY_PARAM_set1_ip_asc(param, name)) {
            goto fail_ssl;
        }
    } else {
        X509_VERIFY_PARAM_set_hostflags(param, X509_CHECK_FLAG_NEVER_CHECK_SUBJECT | X509_CHECK_FLAG_NO_PARTIAL_WILDCARDS);
        if (!X509_VERIFY_PARAM_set1_host(param, name, host_len) || !SSL_set_tlsext_host_name(c->ssl, name)) {
            goto fail_ssl;
        }
    }
    SSL_set_connect_state(c->ssl);
    return c;
fail:
    BIO_free(c->in);
    BIO_free(c->out);
    SSL_free(c->ssl);
    OPENSSL_free(c);
    return NULL;
fail_ssl:
    SSL_free(c->ssl); /* frees both BIOs */
    OPENSSL_free(c);
    return NULL;
}

/* Frees the connection, its SSL and both BIOs. NULL is ignored. */
void wt_ctx_free(wt_ctx *c) {
    if (c == NULL) {
        return;
    }
    SSL_free(c->ssl);
    OPENSSL_free(c);
}

/* Records why OpenSSL failed: an alert from the peer, a certificate that did not verify, or a protocol or internal
   error, before or after the handshake. */
static void record_failure(wt_ctx *c) {
    unsigned long code = ERR_peek_last_error();
    c->code = code;
    long verify = SSL_get_verify_result(c->ssl);
    int reason = ERR_GET_REASON(code);
    if (verify != X509_V_OK && !c->handshaken) {
        c->reason = WT_REASON_CERTIFICATE;
    } else if (ERR_GET_LIB(code) == ERR_LIB_SSL && reason >= SSL_AD_REASON_OFFSET && reason < SSL_AD_REASON_OFFSET + 256) {
        c->reason = WT_REASON_ALERT;
    } else if (ERR_GET_LIB(code) == ERR_LIB_SSL || code == 0) {
        c->reason = c->handshaken ? WT_REASON_PROTOCOL : WT_REASON_HANDSHAKE;
    } else {
        c->reason = WT_REASON_INTERNAL;
    }
}

/* The one place SSL_get_error becomes a wt_status. */
static int status_of(wt_ctx *c, int ret) {
    switch (SSL_get_error(c->ssl, ret)) {
    case SSL_ERROR_NONE:
        return WT_DONE;
    case SSL_ERROR_WANT_READ:
        return WT_WANT_READ;
    case SSL_ERROR_WANT_WRITE:
        return WT_WANT_WRITE;
    case SSL_ERROR_ZERO_RETURN:
        return WT_CLOSED;
    default:
        record_failure(c);
        return WT_ERROR;
    }
}

/* Takes up to `len` bytes of ciphertext from the peer. The bytes taken (fewer when the read BIO nears its limit), or
   WT_FULL if it has no room. */
int64_t wt_feed(wt_ctx *c, const uint8_t *in, size_t len) {
    size_t pending = BIO_ctrl_pending(c->in);
    if (pending >= WT_READ_BIO_LIMIT) {
        return WT_FULL;
    }
    size_t n = WT_READ_BIO_LIMIT - pending;
    if (n > len) {
        n = len;
    }
    if (n == 0) {
        return 0;
    }
    int w = BIO_write(c->in, in, (int)n);
    if (w <= 0) {
        c->reason = WT_REASON_INTERNAL;
        return WT_ERROR;
    }
    return w;
}

/* Copies up to `cap` bytes of ciphertext for the peer into `out`. The bytes copied; 0 if none is pending. */
int64_t wt_take(wt_ctx *c, uint8_t *out, size_t cap) {
    if (cap > INT32_MAX) {
        cap = INT32_MAX;
    }
    if (cap == 0 || BIO_ctrl_pending(c->out) == 0) {
        return 0;
    }
    int r = BIO_read(c->out, out, (int)cap);
    return r > 0 ? r : 0;
}

/* The ciphertext waiting to be taken. */
uint64_t wt_pending(wt_ctx *c) {
    return BIO_ctrl_pending(c->out);
}

/* Runs the handshake as far as the fed ciphertext allows. */
int32_t wt_handshake(wt_ctx *c) {
    ERR_clear_error();
    int r = SSL_do_handshake(c->ssl);
    if (r == 1) {
        c->handshaken = 1;
        return WT_DONE;
    }
    return status_of(c, r);
}

/* Decrypts up to `cap` bytes into `out`. The bytes, or a status: WT_WANT_READ (feed more), WT_CLOSED (the peer's
   close_notify), WT_ERROR. */
int64_t wt_read(wt_ctx *c, uint8_t *out, size_t cap) {
    if (cap == 0) {
        return 0;
    }
    if (cap > INT32_MAX) {
        cap = INT32_MAX;
    }
    ERR_clear_error();
    size_t got = 0;
    int r = SSL_read_ex(c->ssl, out, cap, &got);
    if (r == 1) {
        return (int64_t)got;
    }
    return status_of(c, 0);
}

/* Encrypts all of `in` into the write BIO (memory BIOs never refuse, so a write is all or an error). `len`, or a
   status. */
int64_t wt_write(wt_ctx *c, const uint8_t *in, size_t len) {
    if (len == 0) {
        return 0;
    }
    ERR_clear_error();
    size_t done = 0;
    int r = SSL_write_ex(c->ssl, in, len, &done);
    if (r == 1) {
        return (int64_t)done;
    }
    return status_of(c, 0);
}

/* Queues close_notify. WT_DONE once both sides' are sent and received, WT_WANT_READ while the peer's is awaited. */
int32_t wt_shutdown(wt_ctx *c) {
    ERR_clear_error();
    int r = SSL_shutdown(c->ssl);
    if (r == 1) {
        return WT_DONE;
    }
    if (r == 0) {
        return WT_WANT_READ;
    }
    return status_of(c, r);
}

/* The negotiated ALPN protocol, copied into `out`: its length, 0 if none, or -needed if `cap` is too small. */
int64_t wt_alpn(wt_ctx *c, uint8_t *out, size_t cap) {
    const unsigned char *p = NULL;
    unsigned int n = 0;
    SSL_get0_alpn_selected(c->ssl, &p, &n);
    if (p == NULL || n == 0) {
        return 0;
    }
    if (n > cap) {
        return -(int64_t)n;
    }
    memcpy(out, p, n);
    return n;
}

/* The name the client asked for (SNI), copied into `out`: its length, 0 if none, or -needed. */
int64_t wt_sni(wt_ctx *c, uint8_t *out, size_t cap) {
    const char *name = SSL_get_servername(c->ssl, TLSEXT_NAMETYPE_host_name);
    if (name == NULL) {
        return 0;
    }
    size_t n = strlen(name);
    if (n > cap) {
        return -(int64_t)n;
    }
    memcpy(out, name, n);
    return (int64_t)n;
}

/* The verified peer's certificate, DER, copied into `out`: its length, 0 if none, or -needed. */
static X509 *verified_peer(wt_ctx *c);
int64_t wt_peer_cert_der(wt_ctx *c, uint8_t *out, size_t cap) {
    X509 *cert = verified_peer(c);
    if (cert == NULL) {
        return 0;
    }
    int n = i2d_X509(cert, NULL);
    int64_t result;
    if (n <= 0) {
        result = 0;
    } else if ((size_t)n > cap) {
        result = -(int64_t)n;
    } else {
        unsigned char *p = out;
        result = i2d_X509(cert, &p);
    }
    X509_free(cert);
    return result;
}

/* The peer's certificate, if it was sent and verified (a client's under client authentication; a server's always). */
static X509 *verified_peer(wt_ctx *c) {
    if (!c->handshaken || SSL_get_verify_result(c->ssl) != X509_V_OK) {
        return NULL;
    }
    return SSL_get1_peer_certificate(c->ssl);
}

/* Copies `n` bytes of `text` into `out`: n, or -n if `cap` is too small. */
static int64_t copy_out(const char *text, size_t n, uint8_t *out, size_t cap) {
    if (n > cap) {
        return -(int64_t)n;
    }
    memcpy(out, text, n);
    return (int64_t)n;
}

/* The verified peer's subject, RFC 2253 ("CN=test client"): its length, 0 if none, or -needed. */
int64_t wt_peer_subject(wt_ctx *c, uint8_t *out, size_t cap) {
    X509 *cert = verified_peer(c);
    if (cert == NULL) {
        return 0;
    }
    BIO *b = BIO_new(BIO_s_mem());
    int64_t result = 0;
    if (b != NULL && X509_NAME_print_ex(b, X509_get_subject_name(cert), 0, XN_FLAG_RFC2253) >= 0) {
        char *text = NULL;
        long n = BIO_get_mem_data(b, &text);
        result = n > 0 ? copy_out(text, (size_t)n, out, cap) : 0;
    }
    BIO_free(b);
    X509_free(cert);
    return result;
}

/* The verified peer's subject alternative names, "DNS:a.example, IP:127.0.0.1, email:x@example": their length, 0 if
   none, or -needed. */
int64_t wt_peer_names(wt_ctx *c, uint8_t *out, size_t cap) {
    X509 *cert = verified_peer(c);
    if (cert == NULL) {
        return 0;
    }
    GENERAL_NAMES *names = X509_get_ext_d2i(cert, NID_subject_alt_name, NULL, NULL);
    BIO *b = BIO_new(BIO_s_mem());
    int64_t result = 0;
    if (names != NULL && b != NULL) {
        for (int i = 0; i < sk_GENERAL_NAME_num(names); i++) {
            GENERAL_NAME *g = sk_GENERAL_NAME_value(names, i);
            if (i > 0) {
                BIO_puts(b, ", ");
            }
            GENERAL_NAME_print(b, g); /* "DNS:…", "IP Address:…", "email:…", "URI:…" */
        }
        char *text = NULL;
        long n = BIO_get_mem_data(b, &text);
        result = n > 0 ? copy_out(text, (size_t)n, out, cap) : 0;
    }
    GENERAL_NAMES_free(names);
    BIO_free(b);
    X509_free(cert);
    return result;
}

/* The protocol version: 0x0303 (TLS 1.2) or 0x0304 (TLS 1.3), 0 before the handshake. */
int32_t wt_version(wt_ctx *c) {
    return c->handshaken ? SSL_version(c->ssl) : 0;
}

/* Why the last WT_ERROR happened (WT_REASON_*). */
int32_t wt_reason(wt_ctx *c) {
    return c->reason;
}

/* The last error's OpenSSL code, its text (and a certificate's verification failure) copied into `out`, cut to `cap`
   and not NUL-terminated; `*len` gets the text's length. */
uint64_t wt_error(wt_ctx *c, uint8_t *out, size_t cap, size_t *len) {
    char text[256];
    text[0] = '\0';
    long verify = SSL_get_verify_result(c->ssl);
    if (verify != X509_V_OK && c->reason == WT_REASON_CERTIFICATE) {
        snprintf(text, sizeof text, "certificate: %s", X509_verify_cert_error_string(verify));
    } else if (c->code != 0) {
        ERR_error_string_n(c->code, text, sizeof text);
    }
    size_t n = strlen(text);
    if (n > cap) {
        n = cap;
    }
    memcpy(out, text, n);
    *len = n;
    return c->code;
}

/* The OpenSSL version text ("OpenSSL 3.6.4 25 Aug 2026"), copied into `out`; its length. */
size_t wt_openssl_version(uint8_t *out, size_t cap) {
    const char *v = OpenSSL_version(OPENSSL_VERSION);
    size_t n = strlen(v);
    if (n > cap) {
        n = cap;
    }
    memcpy(out, v, n);
    return n;
}
