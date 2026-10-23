// SPDX-License-Identifier: MIT
#ifndef MP_USB_PROFILE_H
#define MP_USB_PROFILE_H

#if defined(MP_FIRMWARE_V2) && defined(MP_FIRMWARE_V3)
#error "Select either v2 or v3, not both"
#endif

#if defined(MP_FIRMWARE_V3)
#define MP_USB_PROFILE_ID 3
#if defined(MP_OUTPUT_STREAM)
#define MP_FIRMWARE_VERSION 0x0301
#else
#define MP_FIRMWARE_VERSION 0x0300
#endif
#define MP_INPUT_REPORT_SIZE 63
#define MP_INPUT_REPORT_ID 1
#define MP_REPORT_DESCRIPTOR_SIZE 160
#define MP_USB_EP_OUT 0x03
#define MP_USB_EP_IN 0x84
#define MP_USB_INTERVAL_MS 5
#define MP_USB_ATTRIBUTES 0x40
#define MP_USB_POWER_MA 200
#elif defined(MP_FIRMWARE_V2)
#define MP_USB_PROFILE_ID 2
#define MP_FIRMWARE_VERSION 0x0200
#define MP_INPUT_REPORT_SIZE 12
#define MP_INPUT_REPORT_ID 0
#define MP_REPORT_DESCRIPTOR_SIZE 156
// Retain the original v2 descriptor ordering for reproducible comparison.
#define MP_USB_EP_OUT 0x81
#define MP_USB_EP_IN 0x01
#define MP_USB_INTERVAL_MS 1
#define MP_USB_ATTRIBUTES 0
#define MP_USB_POWER_MA 100
#else
#define MP_USB_PROFILE_ID 1
#define MP_FIRMWARE_VERSION 0x0100
#define MP_INPUT_REPORT_SIZE 63
#define MP_INPUT_REPORT_ID 1
#define MP_REPORT_DESCRIPTOR_SIZE 481
#define MP_USB_EP_OUT 0x03
#define MP_USB_EP_IN 0x84
#define MP_USB_INTERVAL_MS 1
#define MP_USB_ATTRIBUTES 0
#define MP_USB_POWER_MA 100
#endif

#endif
