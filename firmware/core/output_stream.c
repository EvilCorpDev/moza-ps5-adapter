// SPDX-License-Identifier: MIT
#include "output_stream.h"
#include <string.h>
void output_stream_reset(output_stream *s) { s->enabled = false; }
void output_stream_control(output_stream *s, bool enable, bool live, uint32_t now) {
    s->enabled = enable && live; s->control_at = now;
}
bool output_stream_live(const output_stream *s, bool live, uint32_t now) {
    return s->enabled && live && (uint32_t)(now-s->control_at) <= MP_OUTPUT_CONTROL_TTL_MS;
}
size_t output_stream_report(output_stream *s, bool live, uint32_t now, uint8_t id,
                            const uint8_t *data, uint16_t length,
                            uint8_t out[MP_OUTPUT_HEADER_SIZE + MP_OUTPUT_DATA_MAX]) {
    ++s->sequence; // A failed transmission is visible as a gap in the next packet.
    if (!output_stream_live(s, live, now)) return 0;
    if (!data || !length || length > MP_OUTPUT_DATA_MAX) { ++s->dropped; return 0; }
    mp_put32(out, now); out[4] = id; out[5] = (uint8_t)length;
    memcpy(out+MP_OUTPUT_HEADER_SIZE, data, length);
    return MP_OUTPUT_HEADER_SIZE + length;
}
