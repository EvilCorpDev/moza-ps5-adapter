"""Decode the stable UART status prefix and optional USB diagnostics."""
from __future__ import annotations

PROFILES = {1: "baseline", 2: "v2", 3: "v3"}
EVENT_NAMES = {
    1: "mounted", 2: "unmounted", 3: "output", 4: "suspended", 5: "resumed",
    10: "auth-reset-read", 11: "wheel-definition-read", 12: "auth-status-read",
    14: "signature-page-read", 15: "nonce-page-write",
    30: "unknown-feature-read", 31: "unhandled-write",
}


def hello_text(payload: bytes) -> str:
    if len(payload) < 8:
        return "Pico link discovered (short HELLO)"
    baud = int.from_bytes(payload[:4], "little")
    protocol = int.from_bytes(payload[4:6], "little")
    version = int.from_bytes(payload[6:8], "little")
    text = (f"Pico link discovered baud={baud} protocol={protocol} "
            f"firmware=0.{version >> 8}.{version & 255}")
    if len(payload) >= 11:
        profile = PROFILES.get(payload[8], str(payload[8]))
        descriptor = int.from_bytes(payload[9:11], "little")
        text += f" profile={profile} descriptor_bytes={descriptor}"
    return text


def status_text(payload: bytes) -> str | None:
    if len(payload) < 8:
        return None
    phase = payload[1]
    generation = int.from_bytes(payload[4:8], "little")
    if len(payload) < 32:
        return f"Pico status phase={phase} auth_generation={generation:08x}"
    flags, _, nonce_id, bitmap = payload[:4]
    text = (f"Pico status mounted={bool(flags & 1)} donor_live={bool(flags & 2)} "
            f"phase={phase} nonce_id={nonce_id:02x} bitmap={bitmap:02x} "
            f"auth_generation={generation:08x}")
    for name, offset in (("good_pages", 8), ("bad_pages", 12), ("served", 16),
                         ("outputs", 20), ("parser_errors", 24), ("tx_drops", 28)):
        text += f" {name}={int.from_bytes(payload[offset:offset+4], 'little')}"
    if len(payload) >= 64:
        profile = PROFILES.get(payload[32], str(payload[32]))
        descriptor = int.from_bytes(payload[34:36], "little")
        text += f" profile={profile} descriptor_bytes={descriptor} diagnostics_schema={payload[33]}"
        if payload[33] == 1:
            for name, offset in (("feature_gets", 36), ("feature_sets", 40),
                                 ("definition_reads", 44), ("f3_reads", 48),
                                 ("input_reports_sent", 52), ("descriptor_reads", 56),
                                 ("unknown_features", 60)):
                text += f" {name}={int.from_bytes(payload[offset:offset+4], 'little')}"
    if len(payload) >= 76:
        for name, offset in (("output_sequence", 64), ("output_drops", 68), ("output_capabilities", 72)):
            text += f" {name}={int.from_bytes(payload[offset:offset+4], 'little')}"
    return text
