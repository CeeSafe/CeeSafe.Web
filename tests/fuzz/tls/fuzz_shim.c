/*
 * libFuzzer harness for web_tls's shim (Web ADR 0002 §5): arbitrary ciphertext through wt_feed, wt_handshake and
 * wt_read, as a server or as a client mid-handshake. Invariants: no crash, no sanitizer report, no leak, and every
 * result is a byte count or a known status.
 *
 * The first input byte chooses the side (bit 0) and the chunk size the rest is fed in (bits 1-7, 1 to 128 bytes), so
 * the fuzzer also explores how records split across reads.
 *
 * Linux (libFuzzer):
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -I. tests/fuzz/tls/fuzz_shim.c packages/web_tls/c/tls_shim.c \
 *     -lssl -lcrypto -o fuzz_shim
 *   C=packages/web_tls/tests/certs; CW_CERT=$C/server.pem CW_KEY=$C/server.key CW_CA=$C/ca.pem \
 *     ./fuzz_shim corpus/ tests/fuzz/tls/seeds/
 * Elsewhere (no libFuzzer): add -DWT_REPLAY and drop "fuzzer," from -fsanitize: the binary replays the files named on
 * its command line.
 * Seeds: with -DWT_REPLAY -DWT_SEEDS, `fuzz_shim --seeds DIR` runs a real client and server against each other in
 * memory and writes what each received, chunk by chunk, as seeds for the other side (TLS 1.3, and TLS 1.2).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct ssl_ctx_st SSL_CTX;
typedef struct wt_ctx wt_ctx;

SSL_CTX *wt_cfg_new(int32_t server);
int32_t wt_cfg_use_pem(SSL_CTX *ctx, const uint8_t *chain, size_t chain_len, const uint8_t *key, size_t key_len);
int32_t wt_cfg_set_alpn(SSL_CTX *ctx, const uint8_t *wire, size_t len);
int32_t wt_cfg_trust_pem(SSL_CTX *ctx, const uint8_t *pem, size_t len);
wt_ctx *wt_ctx_new(SSL_CTX *cfg, int32_t server, const uint8_t *host, size_t host_len);
void wt_ctx_free(wt_ctx *c);
int64_t wt_feed(wt_ctx *c, const uint8_t *in, size_t len);
int64_t wt_take(wt_ctx *c, uint8_t *out, size_t cap);
int32_t wt_handshake(wt_ctx *c);
int64_t wt_read(wt_ctx *c, uint8_t *out, size_t cap);
int64_t wt_write(wt_ctx *c, const uint8_t *in, size_t len);
int64_t wt_alpn(wt_ctx *c, uint8_t *out, size_t cap);

static SSL_CTX *server_cfg;
static SSL_CTX *client_cfg;

static uint8_t *slurp(const char *env, size_t *len) {
    const char *path = getenv(env);
    if (path == NULL) {
        fprintf(stderr, "fuzz_shim: set %s\n", env);
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
    size_t cl, kl, al;
    uint8_t *chain = slurp("CW_CERT", &cl);
    uint8_t *key = slurp("CW_KEY", &kl);
    uint8_t *ca = slurp("CW_CA", &al);
    server_cfg = wt_cfg_new(1);
    client_cfg = wt_cfg_new(0);
    static const uint8_t alpn[] = {2, 'h', '2', 8, 'h', 't', 't', 'p', '/', '1', '.', '1'};
    if (server_cfg == NULL || client_cfg == NULL || !wt_cfg_use_pem(server_cfg, chain, cl, key, kl) ||
        !wt_cfg_set_alpn(server_cfg, alpn, sizeof alpn) || !wt_cfg_set_alpn(client_cfg, alpn, sizeof alpn) ||
        wt_cfg_trust_pem(client_cfg, ca, al) < 1) {
        fprintf(stderr, "fuzz_shim: setup failed\n");
        exit(2);
    }
}

static void drain(wt_ctx *c) {
    uint8_t out[4096];
    while (wt_take(c, out, sizeof out) > 0) {
    }
}

static void check(int64_t r) {
    if (r < -5) {
        fprintf(stderr, "fuzz_shim: unknown status %lld\n", (long long)r);
        abort();
    }
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (server_cfg == NULL) {
        setup();
    }
    if (size == 0) {
        return 0;
    }
    int server = data[0] & 1;
    size_t chunk = (size_t)(data[0] >> 1) + 1;
    data++;
    size--;
    wt_ctx *c = wt_ctx_new(server ? server_cfg : client_cfg, server, (const uint8_t *)"localhost", 9);
    if (c == NULL) {
        return 0;
    }
    int done = 0;
    if (!server) {
        check(wt_handshake(c)); /* the ClientHello, so the input is read as the server's reply */
        drain(c);
    }
    uint8_t plain[16384];
    size_t at = 0;
    while (at < size) {
        size_t n = size - at < chunk ? size - at : chunk;
        int64_t fed = wt_feed(c, data + at, n);
        check(fed);
        if (fed <= 0) {
            break;
        }
        at += (size_t)fed;
        if (!done) {
            int32_t r = wt_handshake(c);
            check(r);
            drain(c);
            if (r == 0) {
                done = 1;
                uint8_t name[16];
                check(wt_alpn(c, name, sizeof name));
                check(wt_write(c, (const uint8_t *)"x", 1));
                drain(c);
            } else if (r != -1 && r != -2) {
                break;
            }
        }
        if (done) {
            for (;;) {
                int64_t r = wt_read(c, plain, sizeof plain);
                check(r);
                drain(c);
                if (r <= 0) {
                    break;
                }
            }
        }
    }
    wt_ctx_free(c);
    return 0;
}

#ifdef WT_SEEDS
#include <openssl/ssl.h>

static void save(const char *dir, const char *name, int side, const uint8_t *bytes, size_t n) {
    char path[1024];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (f == NULL) {
        perror(path);
        exit(2);
    }
    uint8_t first = (uint8_t)(side | (127 << 1)); /* 128-byte chunks */
    fwrite(&first, 1, 1, f);
    fwrite(bytes, 1, n, f);
    fclose(f);
}

/* Runs a client and a server to the end of their handshake and one exchange, collecting what each received. */
static void transcript(const char *dir, const char *tag) {
    static uint8_t to_server[65536], to_client[65536];
    size_t ns = 0, nc = 0;
    wt_ctx *s = wt_ctx_new(server_cfg, 1, NULL, 0);
    wt_ctx *c = wt_ctx_new(client_cfg, 0, (const uint8_t *)"localhost", 9);
    uint8_t buf[16384];
    int sd = 0, cd = 0;
    for (int round = 0; round < 10 && !(sd && cd); round++) {
        if (!cd) cd = wt_handshake(c) == 0;
        int64_t n;
        while ((n = wt_take(c, buf, sizeof buf)) > 0) {
            memcpy(to_server + ns, buf, (size_t)n);
            ns += (size_t)n;
            wt_feed(s, buf, (size_t)n);
        }
        if (!sd) sd = wt_handshake(s) == 0;
        while ((n = wt_take(s, buf, sizeof buf)) > 0) {
            memcpy(to_client + nc, buf, (size_t)n);
            nc += (size_t)n;
            wt_feed(c, buf, (size_t)n);
        }
    }
    wt_write(c, (const uint8_t *)"GET / HTTP/1.1\r\n\r\n", 18);
    int64_t n;
    while ((n = wt_take(c, buf, sizeof buf)) > 0) {
        memcpy(to_server + ns, buf, (size_t)n);
        ns += (size_t)n;
    }
    char name[64];
    snprintf(name, sizeof name, "server-%s", tag);
    save(dir, name, 1, to_server, ns);
    snprintf(name, sizeof name, "client-%s", tag);
    save(dir, name, 0, to_client, nc);
    printf("fuzz_shim: %s: handshake %s, %zu bytes to the server, %zu to the client\n", tag,
           sd && cd ? "done" : "NOT done", ns, nc);
    wt_ctx_free(s);
    wt_ctx_free(c);
}
#endif

#ifdef WT_REPLAY
int main(int argc, char **argv) {
#ifdef WT_SEEDS
    if (argc == 3 && strcmp(argv[1], "--seeds") == 0) {
        setup();
        transcript(argv[2], "tls13");
        SSL_CTX_set_max_proto_version(client_cfg, TLS1_2_VERSION);
        transcript(argv[2], "tls12");
        return 0;
    }
#endif
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "rb");
        if (f == NULL) {
            perror(argv[i]);
            return 2;
        }
        static uint8_t buf[1 << 20];
        size_t n = fread(buf, 1, sizeof buf, f);
        fclose(f);
        LLVMFuzzerTestOneInput(buf, n);
    }
    printf("fuzz_shim: replayed %d inputs\n", argc - 1);
    return 0;
}
#endif
