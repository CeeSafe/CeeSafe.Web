/* web_http3's shim over nghttp3 (Web ADR 0014 §3.4, §3.6): plain functions over byte buffers and integers. CeeSafe
   holds only opaque handles. Stage 1: the version. */
#define NGHTTP3_STATICLIB 1
#include <nghttp3/nghttp3.h>
#include <stdint.h>

/* nghttp3's version, as 0xMMmmpp. */
uint32_t h3_nghttp3_version(void) {
    const nghttp3_info *info = nghttp3_version(0);
    return info ? (uint32_t)info->version_num : 0u;
}
