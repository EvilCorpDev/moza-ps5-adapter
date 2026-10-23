// SPDX-License-Identifier: MIT
#ifndef MP_USB_REPORTS_H
#define MP_USB_REPORTS_H
#include "auth.h"

// TinyUSB application payloads exclude the report ID.
size_t usb_get_feature(auth_state *state, uint8_t id, uint8_t *out, size_t capacity);

#endif
