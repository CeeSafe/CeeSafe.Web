/* web_quic's shim over ngtcp2 and its OpenSSL backend (Web ADR 0014 §3.4): plain functions over byte buffers and
   integers. CeeSafe holds only opaque handles; nothing here reads a clock or touches a socket. Stage 1: the versions. */
#define NGTCP2_STATICLIB 1
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_ossl.h>
#include <openssl/opensslv.h>
#include <openssl/crypto.h>
#include <stdint.h>

/* ngtcp2's OpenSSL backend uses OpenSSL's QUIC TLS API, added in 3.5 (Web ADR 0014 §3.3). */
#if OPENSSL_VERSION_NUMBER < 0x30500000L
#  error "web_quic needs OpenSSL 3.5 or later (Web ADR 0014 §3.3)"
#endif

/* ngtcp2's version, as 0xMMmmpp. */
uint32_t qs_ngtcp2_version(void) {
    const ngtcp2_info *info = ngtcp2_version(0);
    return info ? (uint32_t)info->version_num : 0u;
}

/* The OpenSSL the process runs with, as OpenSSL_version_num() (0xMNN00PP0L since 3.0). */
uint64_t qs_openssl_version(void) {
    return (uint64_t)OpenSSL_version_num();
}

/* ngtcp2's OpenSSL backend initialises once per process: 0, or a negative ngtcp2 error. */
int32_t qs_init(void) {
    return (int32_t)ngtcp2_crypto_ossl_init();
}
