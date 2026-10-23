// SPDX-License-Identifier: MIT
#ifndef MP_AUTH_H
#define MP_AUTH_H
#include "protocol.h"
#include "../usb_profile.h"
#define AUTH_NONCE_SIZE 280
#define AUTH_RESPONSE_SIZE 1064
enum auth_phase { AUTH_IDLE, AUTH_COLLECTING, AUTH_WAITING, AUTH_READY, AUTH_SERVED, AUTH_FAILED };
typedef struct {
    uint32_t generation, started_ms, good_pages, bad_pages, served;
    uint8_t phase, nonce_id, bitmap, next_page;
    uint8_t nonce[AUTH_NONCE_SIZE], response[AUTH_RESPONSE_SIZE];
} auth_state;
void auth_reset(auth_state *s);
bool auth_set_page(auth_state *s, const uint8_t *payload, size_t length, uint32_t now_ms);
bool auth_supply(auth_state *s, uint32_t token, const uint8_t *payload, size_t length);
// TinyUSB callback convention: buffers exclude the Report ID.
size_t auth_get(auth_state *s, uint8_t report_id, uint8_t *out, size_t capacity);
void auth_tick(auth_state *s, uint32_t now_ms);
void input_neutral(uint8_t report[MP_INPUT_REPORT_SIZE]);
bool input_apply(uint8_t report[MP_INPUT_REPORT_SIZE], const uint8_t *payload, size_t length);
#endif
