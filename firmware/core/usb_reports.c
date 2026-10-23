// SPDX-License-Identifier: MIT
#include "usb_reports.h"
#include <string.h>

size_t usb_get_feature(auth_state *state, uint8_t id, uint8_t *out, size_t capacity) {
    if (!out || !capacity) return 0;
#if !defined(MP_FIRMWARE_V2)
    if (id == 3) {
        // Existing MIT GP2040-CE wheel definition; unchanged protocol bytes.
        static const uint8_t definition[47] = {
            0x21,0x27,0x03,0x11,0x06,0,0,0,0,0,0,0,0,0,0,0,0,0x0d,0x0d,0,0,0,0,
            0x0d,0x84,0x03,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0
        };
        size_t n = capacity < sizeof(definition) ? capacity : sizeof(definition);
        memcpy(out, definition, n);
        return n;
    }
#endif
    if (id >= 0xf1 && id <= 0xf3) return auth_get(state, id, out, capacity);
    return 0;
}
