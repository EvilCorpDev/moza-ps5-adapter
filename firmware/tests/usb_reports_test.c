// SPDX-License-Identifier: MIT
#include "../core/usb_reports.h"
#include "../core/diagnostics.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void bounded_feature_reads(void) {
    auth_state s = {0}; auth_reset(&s);
    uint8_t out[80]; uint32_t generation = s.generation;
    for (size_t capacity = 0; capacity <= 64; ++capacity) {
        memset(out, 0xa5, sizeof(out));
        size_t n = usb_get_feature(&s, 3, out+1, capacity);
#ifdef MP_FIRMWARE_V2
        assert(n == 0);
#else
        assert(n == (capacity < 47 ? capacity : 47));
        if (n >= 5) assert(!memcmp(out+1, "\x21\x27\x03\x11\x06", 5));
        if (n >= 26) assert(out[18] == 0x0d && out[19] == 0x0d && out[24] == 0x0d && out[25] == 0x84 && out[26] == 3);
#endif
        assert(out[0] == 0xa5);
        for (size_t i = n+1; i < sizeof(out); ++i) assert(out[i] == 0xa5);
        assert(s.generation == generation && s.phase == AUTH_IDLE);
    }
    memset(out, 0xa5, sizeof(out));
    assert(usb_get_feature(&s, 0xf3, out+1, 6) == 0);
    assert(s.generation == generation);
    assert(usb_get_feature(&s, 0xf3, out+1, 7) == 7);
    assert(!memcmp(out+1, "\x00\x38\x38\x00\x00\x00\x00", 7));
    assert(out[0] == 0xa5 && out[8] == 0xa5 && s.generation == generation+1);
    assert(usb_get_feature(&s, 0xf2, out, 14) == 0);
    assert(usb_get_feature(&s, 0xf2, out, 15) == 15 && out[1] == 16);
    assert(usb_get_feature(&s, 0x99, out, sizeof(out)) == 0);
    assert(usb_get_feature(&s, 3, NULL, 47) == 0);
}
static void bounded_signature_read(void) {
    auth_state s = {0}; auth_reset(&s); s.phase = AUTH_READY; s.nonce_id = 9;
    for (size_t i = 0; i < sizeof(s.response); ++i) s.response[i] = (uint8_t)i;
    uint8_t guarded[65]; memset(guarded, 0xa5, sizeof(guarded));
    assert(usb_get_feature(&s, 0xf1, guarded+1, 62) == 0 && s.next_page == 0);
    assert(usb_get_feature(&s, 0xf1, guarded+1, 63) == 63 && s.next_page == 1);
    assert(guarded[0] == 0xa5 && guarded[64] == 0xa5);
    assert(guarded[1] == 9 && guarded[2] == 0 && guarded[3] == 0);
    assert(!memcmp(guarded+4, s.response, 56));
    uint8_t full[64] = {0xf1}; memcpy(full+1, guarded+1, 63);
    assert(mp_u32(full+60) == mp_crc32(full, 60));
}
static void input_layout(void) {
    uint8_t guarded[MP_INPUT_REPORT_SIZE+2]; memset(guarded, 0xa5, sizeof(guarded));
    uint8_t *report = guarded+1; input_neutral(report);
    assert(guarded[0] == 0xa5 && guarded[sizeof(guarded)-1] == 0xa5);
#if defined(MP_FIRMWARE_V3)
    assert(!memcmp(report, "\x80\x80\x80\x80\x08\x00\x00", 7));
    assert(mp_u16(report+42) == 0x8000);
    assert(mp_u16(report+44) == 0xffff && mp_u16(report+46) == 0xffff && mp_u16(report+48) == 0xffff);
    for (unsigned i = 50; i < 63; ++i) assert(report[i] == 0);
#endif
    uint8_t payload[12] = {0x34,0x12,0x78,0x56,0xbc,0x9a,0xf0,0xde,0x21,0x30,7,0};
    assert(input_apply(report, payload, sizeof(payload)));
#if MP_INPUT_REPORT_SIZE == 63
    assert(!memcmp(report+42, payload, 8));
    assert(report[4] == 0x17 && report[5] == 2 && report[6] == 3);
#else
    assert(report[0] == 0x17 && report[1] == 2 && report[2] == 3);
    assert(mp_u16(report+4) == 0x1234 && report[6] == 0x56 && report[7] == 0x9a && report[8] == 0xde);
#endif
    uint8_t before[MP_INPUT_REPORT_SIZE]; memcpy(before, report, sizeof(before));
    payload[10] = 9;
    assert(!input_apply(report, payload, sizeof(payload)) && !memcmp(before, report, sizeof(before)));
}
static void diagnostics_contract(void) {
    uint8_t hello[MP_HELLO_SIZE]; diagnostics_hello(hello, 1000000);
    assert(mp_u32(hello) == 1000000 && mp_u16(hello+4) == 1);
    assert(mp_u16(hello+6) == MP_FIRMWARE_VERSION && hello[8] == MP_USB_PROFILE_ID);
    assert(mp_u16(hello+9) == MP_REPORT_DESCRIPTOR_SIZE);
    usb_diagnostics stats = {1,2,3,4,5,6,7};
    uint8_t status[MP_STATUS_SIZE+1]; memset(status, 0xa5, sizeof(status));
    diagnostics_status(status, &stats);
    for (unsigned i = 0; i < 32; ++i) assert(status[i] == 0xa5);
    assert(status[64] == 0xa5 && status[32] == MP_USB_PROFILE_ID && status[33] == 1);
    assert(mp_u16(status+34) == MP_REPORT_DESCRIPTOR_SIZE);
    for (unsigned i = 0; i < 7; ++i) assert(mp_u32(status+36+4*i) == i+1);
}
int main(void) {
    bounded_feature_reads(); bounded_signature_read(); input_layout(); diagnostics_contract();
    printf("USB report tests passed (profile %d)\n", MP_USB_PROFILE_ID); return 0;
}
