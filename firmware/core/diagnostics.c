// SPDX-License-Identifier: MIT
#include "diagnostics.h"
void diagnostics_hello(uint8_t out[MP_HELLO_SIZE], uint32_t baud) {
    mp_put32(out, baud);
    mp_put16(out+4, 1); // UART protocol version; prefix retained for older bridges.
    mp_put16(out+6, MP_FIRMWARE_VERSION);
    out[8] = MP_USB_PROFILE_ID;
    mp_put16(out+9, MP_REPORT_DESCRIPTOR_SIZE);
}
void diagnostics_status(uint8_t out[MP_STATUS_SIZE], const usb_diagnostics *stats) {
    // Caller supplies the existing 32-byte status prefix.
    out[32] = MP_USB_PROFILE_ID;
    out[33] = MP_DIAGNOSTICS_SCHEMA;
    mp_put16(out+34, MP_REPORT_DESCRIPTOR_SIZE);
    mp_put32(out+36, stats->feature_gets);
    mp_put32(out+40, stats->feature_sets);
    mp_put32(out+44, stats->definition_reads);
    mp_put32(out+48, stats->f3_reads);
    mp_put32(out+52, stats->input_reports_sent);
    mp_put32(out+56, stats->descriptor_reads);
    mp_put32(out+60, stats->unknown_features);
}
