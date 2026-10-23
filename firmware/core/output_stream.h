// SPDX-License-Identifier: MIT
#ifndef MP_OUTPUT_STREAM_H
#define MP_OUTPUT_STREAM_H
#include "protocol.h"
#define MP_OUTPUT_DATA_MAX 64u
#define MP_OUTPUT_HEADER_SIZE 6u
#define MP_OUTPUT_CONTROL_TTL_MS 500u
typedef struct {
    bool enabled;
    uint32_t control_at, sequence, dropped;
} output_stream;
void output_stream_reset(output_stream *s);
void output_stream_control(output_stream *s, bool enable, bool live, uint32_t now);
bool output_stream_live(const output_stream *s, bool live, uint32_t now);
size_t output_stream_report(output_stream *s, bool live, uint32_t now, uint8_t id,
                            const uint8_t *data, uint16_t length,
                            uint8_t out[MP_OUTPUT_HEADER_SIZE + MP_OUTPUT_DATA_MAX]);
#endif
