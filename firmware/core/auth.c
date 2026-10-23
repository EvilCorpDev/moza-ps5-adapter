// SPDX-License-Identifier: MIT
#include "auth.h"
#include <string.h>

void auth_reset(auth_state *s) {
    ++s->generation;
    s->phase = AUTH_IDLE; s->bitmap = 0; s->next_page = 0; s->nonce_id = 0;
    memset(s->nonce, 0, sizeof(s->nonce)); memset(s->response, 0, sizeof(s->response));
}
bool auth_set_page(auth_state *s, const uint8_t *payload, size_t length, uint32_t now_ms) {
    uint8_t full[64];
    if (length != 63) { s->bad_pages++; return false; }
    full[0] = 0xf0; memcpy(full+1, payload, 63);
    uint8_t id = full[1], page = full[2];
    if (page > 4 || full[3] != 0 || mp_crc32(full, 60) != mp_u32(full+60)) {
        s->bad_pages++; return false;
    }
    // Page zero always starts a fresh transaction, even if the 8-bit nonce ID repeats.
    if (page == 0) {
        auth_reset(s); s->phase = AUTH_COLLECTING; s->nonce_id = id; s->started_ms = now_ms;
    }
    if (s->phase != AUTH_COLLECTING || id != s->nonce_id) { s->bad_pages++; return false; }
    // Exact retransmissions are harmless. Conflicting pages invalidate the transaction.
    if (s->bitmap & (1u << page)) {
        if (memcmp(s->nonce + 56u*page, full+4, 56)) {
            s->phase = AUTH_FAILED; s->bad_pages++; return false;
        }
    }
    memcpy(s->nonce + 56u*page, full+4, 56);
    s->bitmap |= (uint8_t)(1u << page); s->good_pages++;
    if (s->bitmap == 31) s->phase = AUTH_WAITING;
    return true;
}
bool auth_supply(auth_state *s, uint32_t token, const uint8_t *payload, size_t length) {
    if (token != s->generation || length != AUTH_RESPONSE_SIZE+1 || payload[0] != s->nonce_id) return false;
    if (s->phase == AUTH_READY || s->phase == AUTH_SERVED)
        return memcmp(s->response, payload+1, AUTH_RESPONSE_SIZE) == 0;
    if (s->phase != AUTH_WAITING) return false;
    memcpy(s->response, payload+1, AUTH_RESPONSE_SIZE); s->next_page = 0; s->phase = AUTH_READY;
    return true;
}
size_t auth_get(auth_state *s, uint8_t id, uint8_t *out, size_t capacity) {
    uint8_t full[64] = {0}; full[0] = id;
    if (id == 0xf3) {
        if (capacity < 7) return 0;
        static const uint8_t reset[] = {0,56,56,0,0,0,0};
        auth_reset(s); memcpy(out, reset, 7); return 7;
    }
    if (id == 0xf2) {
        if (capacity < 15) return 0;
        full[1] = s->nonce_id;
        full[2] = (s->phase == AUTH_READY || s->phase == AUTH_SERVED) ? 0 : 16;
        mp_put32(full+12, mp_crc32(full, 12)); memcpy(out, full+1, 15); return 15;
    }
    if (id == 0xf1) {
        if (capacity < 63 || (s->phase != AUTH_READY && s->phase != AUTH_SERVED) || s->next_page >= 19) return 0;
        full[1] = s->nonce_id; full[2] = s->next_page;
        memcpy(full+4, s->response + 56u*s->next_page, 56);
        mp_put32(full+60, mp_crc32(full, 60)); memcpy(out, full+1, 63);
        if (++s->next_page == 19) { s->next_page = 0; s->phase = AUTH_SERVED; s->served++; }
        return 63;
    }
    return 0;
}
void auth_tick(auth_state *s, uint32_t now_ms) {
    if ((s->phase == AUTH_COLLECTING || s->phase == AUTH_WAITING) &&
        (uint32_t)(now_ms - s->started_ms) > 10000) s->phase = AUTH_FAILED;
}
void input_neutral(uint8_t report[MP_INPUT_REPORT_SIZE]) {
#if MP_INPUT_REPORT_SIZE == 12
    // G29-style input report: hat + 22 buttons, 16-bit steering, three
    // 8-bit pedal/auxiliary axes, and three vendor bytes.
    memset(report, 0, MP_INPUT_REPORT_SIZE);
    report[0] = 8;                 // centered hat switch
    mp_put16(report + 4, 0x8000);  // centered steering (logical 0..65534)
    report[6] = report[7] = report[8] = 0;
    report[9] = report[10] = 0x80;
    report[11] = 0x94;
#elif defined(MP_FIRMWARE_V3)
    memset(report, 0, MP_INPUT_REPORT_SIZE);
    memset(report, 0x80, 4);
    report[4] = 8;
    mp_put16(report+42, 0x8000);
    for (unsigned i = 44; i < 50; i += 2) mp_put16(report+i, 0xffff);
#else
    memset(report, 0, MP_INPUT_REPORT_SIZE); memset(report, 0x80, 4); report[4] = 8;
    mp_put16(report+42, 0x8000);
    for (unsigned i = 44; i < 50; i += 2) mp_put16(report+i, 0xffff);
    mp_put16(report+51, 0xffff);
#endif
}
bool input_apply(uint8_t report[MP_INPUT_REPORT_SIZE], const uint8_t *payload, size_t length) {
    if (length != 12 || payload[10] > 8 || payload[11] || (mp_u16(payload+8) & 0xc000)) return false;
#if MP_INPUT_REPORT_SIZE == 12
    input_neutral(report);
    uint16_t buttons = mp_u16(payload+8);
    report[0] = (uint8_t)(payload[10] | ((buttons & 15) << 4));
    report[1] = (uint8_t)(buttons >> 4);
    report[2] = (uint8_t)((buttons >> 12) & 0xff);
    report[3] = 0;                 // UART input supplies only 14 buttons.
    memcpy(report + 4, payload, 2); // steering
    report[6] = payload[3];         // throttle
    report[7] = payload[5];         // brake
    report[8] = payload[7];         // clutch/auxiliary
#else
    input_neutral(report);
    memcpy(report+42, payload, 8);
    uint16_t buttons = mp_u16(payload+8);
    report[4] = (uint8_t)(payload[10] | ((buttons & 15) << 4));
    report[5] = (uint8_t)(buttons >> 4); report[6] = (uint8_t)((buttons >> 12) & 3);
#endif
    return true;
}
