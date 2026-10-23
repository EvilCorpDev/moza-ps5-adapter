// SPDX-License-Identifier: MIT
#include "../core/output_stream.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    output_stream s = {0}; uint8_t src[65], guarded[72];
    for (unsigned i=0;i<sizeof(src);i++) src[i]=(uint8_t)i;
    memset(guarded, 0xa5, sizeof(guarded));
    assert(!output_stream_report(&s, true, 0, 0, src, 32, guarded+1) && s.sequence==1);
    output_stream_control(&s, true, false, 0); assert(!output_stream_live(&s, true, 0));
    output_stream_control(&s, true, true, 0xfffffff0u);
    assert(output_stream_live(&s, true, 0x10u));
    size_t n=output_stream_report(&s, true, 0x10u, 5, src, 64, guarded+1);
    assert(n==70 && mp_u32(guarded+1)==0x10u && guarded[5]==5 && guarded[6]==64);
    assert(!memcmp(guarded+7, src, 64) && guarded[0]==0xa5 && guarded[71]==0xa5);
    assert(!output_stream_report(&s, true, 0x10u, 0, src, 65, guarded+1) && s.dropped==1);
    assert(!output_stream_report(&s, true, 0x10u, 0, NULL, 32, guarded+1) && s.dropped==2);
    assert(!output_stream_live(&s, true, 0x1e5u));
    output_stream_control(&s, true, true, 100);
    assert(output_stream_live(&s, true, 600) && !output_stream_live(&s, true, 601));
    assert(!output_stream_live(&s, false, 110));
    output_stream_reset(&s); assert(!output_stream_live(&s, true, 110));
    s.sequence=0xffffffffu; output_stream_control(&s, true, true, 200);
    assert(output_stream_report(&s, true, 200, 0, src, 32, guarded+1)==38 && s.sequence==0);
    puts("output stream tests passed"); return 0;
}
