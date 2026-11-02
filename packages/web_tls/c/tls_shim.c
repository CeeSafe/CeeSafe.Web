/*
 * web_tls's OpenSSL shim (Web ADR 0002 §3.2, amendment 1): OpenSSL over memory BIOs, so it never touches a socket;
 * CeeSafe does every read and write. The contract:
 *   - no pointer from CeeSafe is kept after a call returns;
 *   - no OpenSSL-owned pointer is returned; data is copied out;
 *   - every length is a size_t and is checked;
 *   - SSL_get_error is mapped to wt_status in one place (status_of);
 *   - no I/O, and no allocation beyond OpenSSL's.
 * Every exported function is in the ADR's table.
 */
#include <openssl/err.h>
#include <openssl/pem.h>
#include <openssl/ssl.h>
#include <openssl/x509v3.h>
#include <stdint.h>
#include <string.h>

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

typedef struct wt_alpn_list {
    unsigned char bytes[WT_ALPN_MAX];
    unsigned int length;
} wt_alpn_list;

typedef struct wt_ctx {
    SSL *ssl;
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

/* A server (server != 0) or client configuration with the settings of Web ADR 0002 §3.3; NULL on failure. */
SSL_CTX *wt_cfg_new(int32_t server) {
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
    if (server) {
        SSL_CTX_set_alpn_select_cb(ctx, alpn_select, NULL);
    } else {
        SSL_CTX_set_verify(ctx, SSL_VERIFY_PEER, NULL);
    }
    return ctx;
}

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

/* Trusts the system's default CA locations. 1 on success. */
int32_t wt_cfg_trust_default(SSL_CTX *ctx) {
    return SSL_CTX_set_default_verify_paths(ctx) ? 1 : 0;
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
    /* The client offers them; SSL_CTX_set_alpn_protos returns 0 on success. */
    return SSL_CTX_set_alpn_protos(ctx, wire, (unsigned int)len) == 0 ? 1 : 0;
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
    if (server) {
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

/* The peer's certificate, DER, copied into `out`: its length, 0 if none, or -needed. */
int64_t wt_peer_cert_der(wt_ctx *c, uint8_t *out, size_t cap) {
    X509 *cert = SSL_get1_peer_certificate(c->ssl);
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
