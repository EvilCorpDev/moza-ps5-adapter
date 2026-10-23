// SPDX-License-Identifier: MIT
#ifndef MP_PROTOCOL_H
#define MP_PROTOCOL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MP_VERSION 1
#define MP_MAX_PAYLOAD 1100
#define MP_RAW_MAX (14 + MP_MAX_PAYLOAD + 4)
#define MP_FRAME_MAX (MP_RAW_MAX + MP_RAW_MAX / 254 + 2)
enum mp_kind {
    MP_HELLO = 1, MP_OPEN = 2, MP_STATUS = 3, MP_HEARTBEAT = 4,
    MP_INPUT = 5, MP_CHALLENGE = 10, MP_RESPONSE = 11,
    MP_ACK = 12, MP_AUTH_ERROR = 13, MP_EVENT = 20,
    MP_OUTPUT = 21, MP_OUTPUT_CONTROL = 22
};
typedef struct {
    uint8_t kind;
    uint32_t session, token;
    uint16_t length;
    uint8_t payload[MP_MAX_PAYLOAD];
} mp_packet;
typedef struct {
    uint8_t encoded[MP_FRAME_MAX];
    size_t length;
    bool discard;
    uint32_t errors;
} mp_parser;
uint16_t mp_u16(const uint8_t *p);
uint32_t mp_u32(const uint8_t *p);
void mp_put16(uint8_t *p, uint16_t v);
void mp_put32(uint8_t *p, uint32_t v);
uint32_t mp_crc32(const uint8_t *data, size_t length);
size_t mp_encode(const mp_packet *p, uint8_t *out, size_t capacity);
bool mp_decode(const uint8_t *encoded, size_t length, mp_packet *p);
bool mp_feed(mp_parser *parser, uint8_t byte, mp_packet *packet);
#endif
