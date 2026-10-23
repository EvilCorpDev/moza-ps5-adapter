// SPDX-License-Identifier: MIT
#ifndef MP_DIAGNOSTICS_H
#define MP_DIAGNOSTICS_H
#include "protocol.h"
#include "../usb_profile.h"
#define MP_HELLO_SIZE 11u
#if defined(MP_OUTPUT_STREAM)
#define MP_STATUS_SIZE 76u
#else
#define MP_STATUS_SIZE 64u
#endif
#define MP_DIAGNOSTICS_SCHEMA 1u
typedef struct {
    uint32_t feature_gets, feature_sets, definition_reads, f3_reads;
    uint32_t input_reports_sent, descriptor_reads, unknown_features;
} usb_diagnostics;
void diagnostics_hello(uint8_t out[MP_HELLO_SIZE], uint32_t baud);
void diagnostics_status(uint8_t out[MP_STATUS_SIZE], const usb_diagnostics *stats);
#endif
