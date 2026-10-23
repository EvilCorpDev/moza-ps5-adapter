// SPDX-License-Identifier: MIT
#include "tusb.h"
#include "usb_profile.h"
#ifdef MP_FIRMWARE_V3
#include "ps_report_descriptor_v3.h"
#define MP_REPORT_DESCRIPTOR ps_report_descriptor_v3
#elif defined(MP_FIRMWARE_V2)
#include "ps_report_descriptor_v2.h"
#define MP_REPORT_DESCRIPTOR ps_report_descriptor_v2
#else
#include "ps_report_descriptor.h"
#define MP_REPORT_DESCRIPTOR ps_report_descriptor
#endif
#include <string.h>

_Static_assert(sizeof(MP_REPORT_DESCRIPTOR) == MP_REPORT_DESCRIPTOR_SIZE, "Unexpected report descriptor length");
void mp_usb_report_descriptor_requested(void);

// Experimental G29-compatible identity. This is not a licensed Logitech product.
static const tusb_desc_device_t device = {
    .bLength = sizeof(tusb_desc_device_t), .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200, .bDeviceClass = 0, .bDeviceSubClass = 0, .bDeviceProtocol = 0,
    .bMaxPacketSize0 = 64, .idVendor = 0x046d, .idProduct = 0xc260, .bcdDevice = 0x0100,
    .iManufacturer = 1, .iProduct = 2, .iSerialNumber = 0, .bNumConfigurations = 1
};
static const uint8_t configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, 1, 0, TUD_CONFIG_DESC_LEN + TUD_HID_INOUT_DESC_LEN, MP_USB_ATTRIBUTES, MP_USB_POWER_MA),
    TUD_HID_INOUT_DESCRIPTOR(0, 0, HID_ITF_PROTOCOL_NONE, sizeof(MP_REPORT_DESCRIPTOR), MP_USB_EP_OUT, MP_USB_EP_IN, 64, MP_USB_INTERVAL_MS)
};
uint8_t const *tud_descriptor_device_cb(void) { return (const uint8_t *)&device; }
uint8_t const *tud_descriptor_configuration_cb(uint8_t index) { return index ? NULL : configuration; }
uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    if (instance) return NULL;
    mp_usb_report_descriptor_requested();
    return MP_REPORT_DESCRIPTOR;
}
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    static uint16_t buf[64];
    const char *s = index == 1 ? "Logitech" : index == 2 ? "G29 Driving Force Racing Wheel" : NULL;
    if (index == 0) { buf[0] = 0x0304; buf[1] = 0x0409; return buf; }
    if (!s) return NULL;
    size_t n = strlen(s); if (n > 63) n = 63;
    for (size_t i = 0; i < n; ++i) buf[i+1] = (uint8_t)s[i];
    buf[0] = (uint16_t)(0x0300 | (2*n+2)); return buf;
}
