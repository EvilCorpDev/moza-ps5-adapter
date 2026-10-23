// SPDX-License-Identifier: MIT
// Experimental compact G29-style wheel report plus the observed PS
// authentication feature reports. This is not a Logitech firmware dump.
#ifndef MP_PS_DESCRIPTOR_V2_H
#define MP_PS_DESCRIPTOR_V2_H
#include <stdint.h>

static const uint8_t ps_report_descriptor_v2[] = {
    // G29-style interrupt input report, without a report ID (12 bytes).
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x04,        // Usage (Joystick)
    0xA1, 0x01,        // Collection (Application)
    0x09, 0x39,        //   Usage (Hat switch)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x07,        //   Logical Maximum (7)
    0x35, 0x00,        //   Physical Minimum (0)
    0x46, 0x3B, 0x01,  //   Physical Maximum (315)
    0x65, 0x14,        //   Unit (English rotation)
    0x75, 0x04,        //   Report Size (4)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x42,        //   Input (Data,Var,Abs,Null)
    0x65, 0x00,        //   Unit (None)
    0x05, 0x09,        //   Usage Page (Button)
    0x19, 0x01,        //   Usage Minimum (Button 1)
    0x29, 0x16,        //   Usage Maximum (Button 22)
    0x15, 0x00,        //   Logical Minimum (0)
    0x25, 0x01,        //   Logical Maximum (1)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x16,        //   Report Count (22)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x75, 0x01,        //   Report Size (1)
    0x95, 0x06,        //   Report Count (6) padding
    0x81, 0x03,        //   Input (Constant,Var,Abs)
    0x05, 0x01,        //   Usage Page (Generic Desktop)
    0x09, 0x30,        //   Usage (X / steering)
    0x15, 0x00,        //   Logical Minimum (0)
    0x27, 0xFF, 0xFF, 0x00, 0x00, // Logical Maximum (65535)
    0x35, 0x00,        //   Physical Minimum (0)
    0x47, 0xFF, 0xFF, 0x00, 0x00, // Physical Maximum (65535)
    0x75, 0x10,        //   Report Size (16)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x09, 0x32,        //   Usage (Z / throttle)
    0x09, 0x35,        //   Usage (Rz / brake)
    0x09, 0x31,        //   Usage (Y / clutch)
    0x15, 0x00,        //   Logical Minimum (0)
    0x26, 0xFF, 0x00,  //   Logical Maximum (255)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x03,        //   Report Count (3)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x06, 0x00, 0xFF,  //   Usage Page (Vendor Defined)
    0x09, 0x01,        //   Usage (1)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x02,        //   Report Count (2)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    0x09, 0x01,        //   Usage (1)
    0x95, 0x01,        //   Report Count (1)
    0x81, 0x02,        //   Input (Data,Var,Abs)
    // Logitech-style seven-byte force-feedback output placeholder.
    0x09, 0x01,        //   Usage (1)
    0x95, 0x07,        //   Report Count (7)
    0x91, 0x02,        //   Output (Data,Var,Abs)
    0xC0,              // End Collection

    // Authentication feature reports observed on the Raion donor.
    0x06, 0xF0, 0xFF,  // Usage Page (Vendor Defined 0xFFF0)
    0x09, 0x40,        // Usage (authentication collection)
    0xA1, 0x01,        // Collection (Application)
    0x85, 0xF0,        //   Report ID F0
    0x09, 0x47,        //   Usage (nonce page)
    0x75, 0x08,        //   Report Size (8)
    0x95, 0x3F,        //   Report Count (63)
    0xB1, 0x02,        //   Feature (Data,Var,Abs)
    0x85, 0xF1,        //   Report ID F1
    0x09, 0x48,        //   Usage (signed response page)
    0x95, 0x3F,        //   Report Count (63)
    0xB1, 0x02,        //   Feature (Data,Var,Abs)
    0x85, 0xF2,        //   Report ID F2
    0x09, 0x49,        //   Usage (authentication status)
    0x95, 0x0F,        //   Report Count (15)
    0xB1, 0x02,        //   Feature (Data,Var,Abs)
    0x85, 0xF3,        //   Report ID F3
    0x09, 0x4A,        //   Usage (authentication reset)
    0x95, 0x07,        //   Report Count (7)
    0xB1, 0x02,        //   Feature (Data,Var,Abs)
    0xC0               // End Collection
};

#endif
