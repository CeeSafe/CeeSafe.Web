/* Stands in for CeeSafe's runtime header (ceesafe/foreign.h, CeeSafe ADR 0060) when the shim is built alone for
   fuzzing: the same declarations, defined in fuzz_quic.c over malloc. */
#ifndef CEESAFE_FOREIGN_H
#define CEESAFE_FOREIGN_H

#include <stddef.h>
#include <stdint.h>

#define CEE_ALLOC_FOREIGN 2u

#define CEE_ALLOC_FOREIGN_INIT 3u

void *ceesafe_alloc(size_t size, uint32_t allocator);
void *ceesafe_realloc(void *p, size_t size, uint32_t allocator);
void ceesafe_free(void *p, uint32_t allocator);

#endif
