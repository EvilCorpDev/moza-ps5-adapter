// SPDX-License-Identifier: MIT
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/irq.h"
#include "tusb.h"
#include "core/auth.h"
#include "core/usb_reports.h"
#include "core/diagnostics.h"
#include "core/output_stream.h"
#include <string.h>

#define LINK_BAUD 1000000u
#define RX_SIZE 4096u
#define TX_SIZE 8192u
static uint8_t rx_ring[RX_SIZE], tx_ring[TX_SIZE], input_report[MP_INPUT_REPORT_SIZE];
static volatile unsigned rx_head, rx_tail;
static volatile uint32_t rx_overflow;
static unsigned tx_head, tx_tail;
static mp_parser parser;
static mp_packet incoming, outgoing;
static auth_state auth;
static usb_diagnostics usb_stats;
static uint32_t last_f2_event;
static bool f2_event_sent;
static uint32_t session, last_heartbeat, last_input, tx_drops, outputs, last_output_event;
static bool donor_live, mounted;
#if defined(MP_OUTPUT_STREAM)
static output_stream output_state;
static bool suspended;
#endif
static uint32_t now_ms(void) { return to_ms_since_boot(get_absolute_time()); }

static void uart_rx(void) {
    while (uart_is_readable(uart0)) {
        uint8_t b = (uint8_t)uart_getc(uart0);
        unsigned next = (rx_head+1) & (RX_SIZE-1);
        if (next == rx_tail) ++rx_overflow;
        else { rx_ring[rx_head] = b; __dmb(); rx_head = next; }
    }
}
static void flush_tx(void) {
    while (tx_tail != tx_head && uart_is_writable(uart0)) {
        uart_putc_raw(uart0, tx_ring[tx_tail]); tx_tail = (tx_tail+1) & (TX_SIZE-1);
    }
}
static bool send_packet(uint8_t kind, uint32_t token, const uint8_t *data, uint16_t len) {
    static uint8_t frame[MP_FRAME_MAX];
    outgoing.kind = kind; outgoing.session = session; outgoing.token = token; outgoing.length = len;
    if (len) memcpy(outgoing.payload, data, len);
    size_t n = mp_encode(&outgoing, frame, sizeof(frame));
    unsigned free = (tx_tail - tx_head - 1) & (TX_SIZE-1);
    if (!n || n > free || ((kind == MP_EVENT || kind == MP_OUTPUT) && free < n+2048)) { ++tx_drops; return false; }
    for (size_t i = 0; i < n; ++i) { tx_ring[tx_head] = frame[i]; tx_head = (tx_head+1) & (TX_SIZE-1); }
    return true;
}
static void event(uint8_t code, uint8_t report_id, const uint8_t *data, uint16_t len) {
    uint8_t p[36] = {code, report_id}; mp_put16(p+2, len);
    unsigned n = len > 32 ? 32 : len;
    if (n && data) memcpy(p+4, data, n);
    else if (n) memset(p+4, 0, n);
    send_packet(MP_EVENT, auth.generation, p, (uint16_t)(n+4));
}
static void invalidate(void) {
    auth_reset(&auth); input_neutral(input_report); donor_live = false;
#if defined(MP_OUTPUT_STREAM)
    output_stream_reset(&output_state);
#endif
}
static void hello(void) {
    uint8_t p[MP_HELLO_SIZE]; diagnostics_hello(p, LINK_BAUD);
    send_packet(MP_HELLO, 0, p, sizeof(p));
}
static void status(void) {
    uint8_t p[MP_STATUS_SIZE] = { (mounted ? 1 : 0) | (donor_live ? 2 : 0), auth.phase, auth.nonce_id, auth.bitmap };
    mp_put32(p+4, auth.generation); mp_put32(p+8, auth.good_pages); mp_put32(p+12, auth.bad_pages);
    mp_put32(p+16, auth.served); mp_put32(p+20, outputs);
    mp_put32(p+24, parser.errors + rx_overflow); mp_put32(p+28, tx_drops);
    diagnostics_status(p, &usb_stats);
#if defined(MP_OUTPUT_STREAM)
    mp_put32(p+64, output_state.sequence); mp_put32(p+68, output_state.dropped);
    mp_put32(p+72, 1u | (output_stream_live(&output_state, mounted && donor_live && !suspended, now_ms()) ? 2u : 0u));
#endif
    send_packet(MP_STATUS, auth.generation, p, sizeof(p));
}
static void handle(const mp_packet *p) {
    uint32_t now = now_ms();
    if (p->kind == MP_OPEN && p->session && p->length == 0) {
        if (session != p->session) { invalidate(); session = p->session; hello(); }
        last_heartbeat = now; status(); return;
    }
    if (!session || p->session != session) return;
    switch (p->kind) {
#if defined(MP_OUTPUT_STREAM)
    case MP_OUTPUT_CONTROL:
        if (p->length == 1 && p->payload[0] <= 1)
            output_stream_control(&output_state, p->payload[0] != 0, mounted && donor_live && !suspended, now);
        break;
#endif
    case MP_HEARTBEAT:
        if (p->length != 1 || p->payload[0] > 1) return;
        last_heartbeat = now;
        if (donor_live && !p->payload[0]) invalidate();
        donor_live = p->payload[0] != 0;
        break;
    case MP_INPUT:
        if (donor_live && input_apply(input_report, p->payload, p->length)) last_input = now;
        break;
    case MP_RESPONSE: {
        uint8_t accepted = donor_live && auth_supply(&auth, p->token, p->payload, p->length);
        send_packet(MP_ACK, p->token, &accepted, 1); break;
    }
    case MP_AUTH_ERROR:
        if (p->token == auth.generation && auth.phase == AUTH_WAITING) auth.phase = AUTH_FAILED;
        break;
    default: break;
    }
}

static void reset_output_link(void) {
#if defined(MP_OUTPUT_STREAM)
    output_stream_reset(&output_state);
#endif
}
void tud_mount_cb(void) {
    mounted = true; reset_output_link();
#if defined(MP_OUTPUT_STREAM)
    suspended = false;
#endif
    event(1, 0, NULL, 0);
}
void tud_umount_cb(void) { mounted = false; reset_output_link(); auth_reset(&auth); input_neutral(input_report); event(2, 0, NULL, 0); }
void tud_suspend_cb(bool remote_wakeup_en) {
    (void)remote_wakeup_en; reset_output_link(); input_neutral(input_report);
#if defined(MP_OUTPUT_STREAM)
    suspended = true;
#endif
    event(4, 0, NULL, 0);
}
void tud_resume_cb(void) {
#if defined(MP_OUTPUT_STREAM)
    suspended = false;
#endif
    event(5, 0, NULL, 0);
}
void mp_usb_report_descriptor_requested(void) { ++usb_stats.descriptor_reads; }
uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t id, hid_report_type_t type, uint8_t *buf, uint16_t len) {
    (void)instance;
    if (type == HID_REPORT_TYPE_INPUT && id == MP_INPUT_REPORT_ID) {
        unsigned n = len < sizeof(input_report) ? len : sizeof(input_report); memcpy(buf, input_report, n); return (uint16_t)n;
    }
    if (type != HID_REPORT_TYPE_FEATURE) return 0;
    ++usb_stats.feature_gets;
#if !defined(MP_FIRMWARE_V2)
    if (id == 3) {
        ++usb_stats.definition_reads;
        event(11, id, NULL, len);
        return (uint16_t)usb_get_feature(&auth, id, buf, len);
    }
#endif
    if (id >= 0xf1 && id <= 0xf3) {
        uint8_t previous_page = auth.next_page, previous_phase = auth.phase;
        uint16_t n = (uint16_t)usb_get_feature(&auth, id, buf, len);
        if (id == 0xf3) { ++usb_stats.f3_reads; event(10, id, NULL, len); }
        if (id == 0xf1) {
            uint8_t metadata[4] = { auth.nonce_id, previous_page, previous_phase, auth.phase };
            event(14, id, metadata, sizeof(metadata));
        }
        if (id == 0xf2 && (!f2_event_sent || (uint32_t)(now_ms()-last_f2_event) >= 500)) {
            uint8_t metadata[2] = { auth.nonce_id, auth.phase };
            f2_event_sent = true; last_f2_event = now_ms();
            event(12, id, metadata, sizeof(metadata));
        }
        return n;
    }
    ++usb_stats.unknown_features;
    event(30, id, NULL, len); return 0;
}
void tud_hid_set_report_cb(uint8_t instance, uint8_t id, hid_report_type_t type, const uint8_t *buf, uint16_t len) {
    (void)instance;
    if (type == HID_REPORT_TYPE_FEATURE) {
        ++usb_stats.feature_sets;
        if (id == 0xf0) {
            bool accepted = auth_set_page(&auth, buf, len, now_ms());
            uint8_t metadata[4] = { accepted, len > 0 ? buf[0] : 0, len > 1 ? buf[1] : 0, auth.phase };
            event(15, id, metadata, sizeof(metadata));
            return;
        }
        ++usb_stats.unknown_features;
    }
    if (type == HID_REPORT_TYPE_OUTPUT) {
        ++outputs;
        uint32_t now = now_ms();
#if defined(MP_OUTPUT_STREAM)
        uint8_t data[MP_OUTPUT_HEADER_SIZE + MP_OUTPUT_DATA_MAX];
        size_t n = output_stream_report(&output_state, mounted && donor_live && !suspended,
                                        now, id, buf, len, data);
        if (n && !send_packet(MP_OUTPUT, output_state.sequence, data, (uint16_t)n)) ++output_state.dropped;
#endif
        if ((uint32_t)(now-last_output_event) >= 50) {
            last_output_event = now;
            event(3, id, buf, len);
        }
        // This firmware only forwards USB output; the Pi's opt-in worker owns hardware output.
        return;
    }
    event(31, id, NULL, len);
}
int main(void) {
    gpio_init(PICO_DEFAULT_LED_PIN); gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
    input_neutral(input_report); auth_reset(&auth);
    uart_init(uart0, LINK_BAUD); gpio_set_function(0, GPIO_FUNC_UART); gpio_set_function(1, GPIO_FUNC_UART);
    uart_set_hw_flow(uart0, false, false); uart_set_format(uart0, 8, 1, UART_PARITY_NONE);
    uart_set_fifo_enabled(uart0, true);
    irq_set_exclusive_handler(UART0_IRQ, uart_rx); irq_set_enabled(UART0_IRQ, true);
    uart_set_irq_enables(uart0, true, false);
    tusb_init();
    uint32_t last_status = 0, last_challenge = 0, last_report = 0, sent_generation = 0;
    uint8_t counter = 0;
    for (;;) {
        tud_task(); flush_tx();
        // Bound work per iteration so UART traffic never starves USB.
        for (unsigned i = 0; i < 256 && rx_tail != rx_head; ++i) {
            uint8_t b = rx_ring[rx_tail]; __dmb(); rx_tail = (rx_tail+1) & (RX_SIZE-1);
            if (mp_feed(&parser, b, &incoming)) handle(&incoming);
        }
        uint32_t now = now_ms(); auth_tick(&auth, now);
        if (session && (uint32_t)(now-last_heartbeat) > 1500) { invalidate(); session = 0; }
        if ((uint32_t)(now-last_input) > 200) input_neutral(input_report);
        if ((uint32_t)(now-last_status) >= 500) {
            last_status = now;
            if (!session) hello();
            else status();
        }
        if (session && donor_live && auth.phase == AUTH_WAITING &&
            (auth.generation != sent_generation || (uint32_t)(now-last_challenge) >= 500)) {
            uint8_t p[AUTH_NONCE_SIZE+1]; p[0] = auth.nonce_id; memcpy(p+1, auth.nonce, AUTH_NONCE_SIZE);
            if (send_packet(MP_CHALLENGE, auth.generation, p, sizeof(p))) { sent_generation = auth.generation; last_challenge = now; }
        }
        if ((uint32_t)(now-last_report) >= 5 && tud_hid_ready()) {
            last_report = now;
#if MP_INPUT_REPORT_SIZE == 63
            input_report[6] = (input_report[6] & 3) | ((counter++ & 63) << 2);
#else
            (void)counter;
#endif
            if (tud_hid_report(MP_INPUT_REPORT_ID, input_report, sizeof(input_report))) ++usb_stats.input_reports_sent;
        }
        // Fast blink: no Pi. Slow: linked. Solid: signature supplied, NOT PS5 acceptance.
        gpio_put(PICO_DEFAULT_LED_PIN, auth.phase == AUTH_SERVED ? true : ((now / (session ? 500 : 100)) & 1));
        tight_loop_contents();
    }
}
