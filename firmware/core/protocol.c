// SPDX-License-Identifier: MIT
#include "protocol.h"
#include <string.h>

uint16_t mp_u16(const uint8_t *p) { return (uint16_t)(p[0] | ((uint16_t)p[1] << 8)); }
uint32_t mp_u32(const uint8_t *p) { return mp_u16(p) | ((uint32_t)mp_u16(p+2) << 16); }
void mp_put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
void mp_put32(uint8_t *p, uint32_t v) { mp_put16(p, (uint16_t)v); mp_put16(p+2, (uint16_t)(v >> 16)); }
uint32_t mp_crc32(const uint8_t *data, size_t length) {
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned b = 0; b < 8; ++b)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
size_t mp_encode(const mp_packet *p, uint8_t *out, size_t capacity) {
    if (p->length > MP_MAX_PAYLOAD || capacity < MP_FRAME_MAX) return 0;
    uint8_t raw[MP_RAW_MAX];
    size_t n = 14u + p->length;
    raw[0] = 'M'; raw[1] = 'P'; raw[2] = MP_VERSION; raw[3] = p->kind;
    mp_put32(raw+4, p->session); mp_put32(raw+8, p->token);
    mp_put16(raw+12, p->length); memcpy(raw+14, p->payload, p->length);
    mp_put32(raw+n, mp_crc32(raw, n)); n += 4;
    size_t at = 1, code_at = 0; uint8_t code = 1;
    for (size_t i = 0; i < n; ++i) {
        if (raw[i] == 0) { out[code_at] = code; code_at = at++; code = 1; }
        else {
            out[at++] = raw[i];
            if (++code == 255) { out[code_at] = code; code_at = at++; code = 1; }
        }
    }
    out[code_at] = code; out[at++] = 0;
    return at;
}
bool mp_decode(const uint8_t *encoded, size_t length, mp_packet *p) {
    uint8_t raw[MP_RAW_MAX]; size_t in = 0, n = 0;
    if (length == 0 || length >= MP_FRAME_MAX) return false;
    while (in < length) {
        unsigned code = encoded[in++];
        if (!code || in + code - 1 > length || n + code - 1 > sizeof(raw)) return false;
        for (unsigned i = 1; i < code; ++i) {
            if (encoded[in] == 0) return false;
            raw[n++] = encoded[in++];
        }
        if (code < 255 && in < length) {
            if (n == sizeof(raw)) return false;
            raw[n++] = 0;
        }
    }
    if (n < 18 || raw[0] != 'M' || raw[1] != 'P' || raw[2] != MP_VERSION) return false;
    unsigned count = mp_u16(raw+12);
    if (count > MP_MAX_PAYLOAD || n != 18u + count || mp_crc32(raw, n-4) != mp_u32(raw+n-4)) return false;
    p->kind = raw[3]; p->session = mp_u32(raw+4); p->token = mp_u32(raw+8);
    p->length = (uint16_t)count; memcpy(p->payload, raw+14, count);
    return true;
}
bool mp_feed(mp_parser *s, uint8_t byte, mp_packet *p) {
    if (byte) {
        if (!s->discard) {
            if (s->length == sizeof(s->encoded)) { s->discard = true; s->errors++; }
            else s->encoded[s->length++] = byte;
        }
        return false;
    }
    bool ok = !s->discard && s->length && mp_decode(s->encoded, s->length, p);
    if (!ok && !s->discard && s->length) s->errors++;
    s->length = 0; s->discard = false;
    return ok;
}
