from __future__ import annotations
import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import secrets
import sys
import time

import serial

from .auth import sign_nonce
from .hid import HidDonor
from .diagnostics import EVENT_NAMES, hello_text, status_text
from .ps_button import PSButtonPulse, V3InputGate, INTERVAL_S, neutral_input
from .r5_input import LinuxR5Source, load_map, input_text
from .wheel_output import FullOutputGate
from .output_worker import OutputWorker
from .protocol import (ACK, AUTH_ERROR, CHALLENGE, HEARTBEAT, HELLO, OPEN, INPUT,
                       EVENT, OUTPUT, OUTPUT_CONTROL, Packet, RESPONSE, STATUS, StreamParser)


def challenge_nonce(payload: bytes) -> tuple[int, bytes]:
    """Decode the Pico's 280-byte nonce buffer and discard page-4 padding."""
    if len(payload) != 281 or any(payload[257:]):
        raise ValueError("invalid challenge payload")
    return payload[0], payload[1:257]


def run(serial_path: str, donor_path: str, baud: int = 1_000_000, *, press_ps: bool = False,
        r5_path: str | None = None, r5_map: str | None = None,
        ffb: bool = False, ffb_gain: float = .10, ffb_invert: bool = False,
        led_path: str | None = None, outputs_dry_run: bool = False,
        ffb_damper: bool = False) -> int:
    if ffb_damper and not ffb:
        raise ValueError("--ffb-damper requires --ffb")
    if r5_map is not None and r5_path is None:
        raise ValueError("--r5-map requires --r5-input")
    if (ffb or led_path) and r5_path is None:
        raise ValueError("Hardware outputs require --r5-input")
    if outputs_dry_run and (ffb or led_path):
        raise ValueError("--outputs-dry-run cannot be combined with hardware outputs")
    source = LinuxR5Source(r5_path, load_map(r5_map)) if r5_path is not None else None
    input_gate = V3InputGate()
    link = serial.Serial(serial_path, baudrate=baud, timeout=0, write_timeout=0.5)
    donor = worker = None
    output_gate = FullOutputGate()
    try:
        donor = HidDonor(donor_path)
        if ffb or led_path or outputs_dry_run:
            worker = OutputWorker(ffb_path=r5_path if ffb else None, led_path=led_path,
                                  gain=ffb_gain, invert=ffb_invert, dry_run=outputs_dry_run,
                                  damper=ffb_damper)
    except BaseException:
        if donor is not None: donor.close()
        if source is not None: source.close()
        link.close()
        raise
    parser = StreamParser()
    session = secrets.randbits(32) or 1
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raion-auth")
    pending: Future | None = None
    pending_token: int | None = None
    ps_pulse = PSButtonPulse() if press_ps else None
    ps_logged_stage = None
    last_beat = last_open = last_status = 0.0
    last_input_send = float("-inf")
    next_input_log = 0.0
    last_output_control = float("-inf")
    next_output_log = 0.0
    print(f"bridge session {session:08x}; " +
          (f"full outputs requested: FFB={ffb} LEDs={bool(led_path)} dry_run={outputs_dry_run}" if worker else
           "FFB output is captured and discarded"), flush=True)
    if ffb:
        print(f"FFB: constant force, experimental_damper={ffb_damper}, gain={ffb_gain:.2f}, invert={ffb_invert}, finite 150 ms effects; requires output-capable firmware", flush=True)
    if press_ps:
        print("PS-button experiment enabled: one 500 ms pulse per v3 mount/link recovery; steering/pedals stay neutral", flush=True)
    if source is not None:
        print(f"R5 forwarding enabled path={r5_path}; map={source.mapping.name}; only verified v3 links receive input", flush=True)
    try:
        while True:
            now = time.monotonic()
            if now - last_open >= 1.0:
                link.write(Packet(OPEN, session).encode()); last_open = now
            if now - last_beat >= .25:
                link.write(Packet(HEARTBEAT, session, payload=b"\1").encode()); last_beat = now
            if now - last_status >= 2.0:
                last_status = now
            previous_errors = parser.errors
            packets = parser.feed(link.read(4096))
            if worker and parser.errors != previous_errors:
                output_gate.reset(); worker.invalidate()
            for packet in packets:
                if packet.session != session and packet.kind != HELLO:
                    continue
                if packet.kind == HELLO:
                    input_gate.new_boot()
                    if worker:
                        output_gate.reset(); worker.invalidate()
                    if ps_pulse is not None:
                        ps_pulse.new_boot(); ps_logged_stage = None
                    link.write(Packet(OPEN, session).encode())
                    print(hello_text(packet.payload), flush=True)
                elif packet.kind == CHALLENGE:
                    if pending is not None and not pending.done():
                        print("dropping overlapping challenge", flush=True)
                        continue
                    try:
                        nonce_id, nonce = challenge_nonce(packet.payload)
                    except ValueError:
                        print("invalid nonce padding", flush=True)
                        link.write(Packet(AUTH_ERROR, session, packet.token).encode())
                        continue
                    print(f"challenge {packet.token:08x} id={nonce_id:02x}", flush=True)
                    pending = executor.submit(sign_nonce, donor, nonce, nonce_id)
                    pending_token = packet.token
                elif packet.kind == STATUS:
                    input_gate.status(packet.payload, now)
                    if worker: output_gate.status(packet.payload, now)
                    if ps_pulse is not None and ps_pulse.status(packet.payload, now):
                        ps_logged_stage = None
                        print("PS-button pulse armed: verified v3, USB mounted, bridge heartbeat live; waiting 1 second", flush=True)
                    text = status_text(packet.payload)
                    if text is not None:
                        print(text, flush=True)
                elif packet.kind == EVENT:
                    if len(packet.payload) >= 4:
                        code, report_id = packet.payload[:2]
                        input_gate.event(code)
                        if worker:
                            output_gate.event(code)
                            if code in (1, 2, 4, 5): worker.invalidate()
                        if ps_pulse is not None:
                            ps_pulse.event(code)
                        event_length = int.from_bytes(packet.payload[2:4], "little")
                        data = packet.payload[4:]
                        print(
                            f"Pico event code={code} name={EVENT_NAMES.get(code, 'unknown')} report_id={report_id:#04x} "
                            f"length={event_length} data={data.hex(' ')}",
                            flush=True,
                        )
                elif packet.kind == OUTPUT and worker:
                    command = output_gate.accept(packet, now)
                    if command is None:
                        worker.invalidate()
                    else:
                        worker.submit(command, now)
            if source is not None:
                input_payload = source.poll(now)
                for message in source.messages: print(message, flush=True)
                source.messages.clear()
                if now >= next_input_log:
                    print(input_text(input_payload, source.live, source.frames, source.state.syn_dropped), flush=True)
                    next_input_log = now + 2
            if worker:
                ready = output_gate.ready(now) and (source is None or source.live)
                worker.set_ready(ready, now)
                if now - last_output_control >= .25:
                    link.write(Packet(OUTPUT_CONTROL, session, payload=bytes((int(ready),))).encode())
                    last_output_control = now
                while not worker.messages.empty(): print(worker.messages.get(), flush=True)
                if now >= next_output_log:
                    print(worker.summary() + f" stream_rejected={output_gate.rejected} stream={output_gate.reason}", flush=True)
                    next_output_log = now + 2
            if ps_pulse is not None:
                pulse = ps_pulse.tick(now)
                if pulse is not None:
                    payload, stage = pulse
                    link.write(Packet(INPUT, session, payload=payload).encode())
                    if stage != ps_logged_stage:
                        print(f"PS-button {'pressed (500 ms)' if stage == 'press' else 'released'} via UART; USB/console acceptance unverified", flush=True)
                        ps_logged_stage = stage
            if (source is not None and input_gate.can_send(now)
                    and (ps_pulse is None or ps_pulse.stage == "done")
                    and now - last_input_send >= INTERVAL_S):
                link.write(Packet(INPUT, session, payload=input_payload).encode())
                last_input_send = now
            if pending is not None and pending.done():
                token = pending_token
                try:
                    result = pending.result()
                    payload = bytes((result.nonce_id,)) + result.signature_blob
                    link.write(Packet(RESPONSE, session, token, payload).encode())
                    crc_note = ""
                    if result.f1_crc_mismatch_pages:
                        crc_note = f"; F1 CRC fields absent on pages {list(result.f1_crc_mismatch_pages)}"
                    print(f"Raion signed challenge in {result.elapsed_s:.3f}s; response sent{crc_note}", flush=True)
                except Exception as exc:
                    print(f"Raion authentication failed: {exc}", file=sys.stderr, flush=True)
                    link.write(Packet(AUTH_ERROR, session, token).encode())
                pending = None
                pending_token = None
            time.sleep(.002)
    except KeyboardInterrupt:
        return 0
    finally:
        if worker:
            worker.close()
            try: link.write(Packet(OUTPUT_CONTROL, session, payload=b"\0").encode())
            except (OSError, serial.SerialException): pass
        if source is not None:
            source.close()
            if input_gate.can_send(time.monotonic()):
                try:
                    link.write(Packet(INPUT, session, payload=neutral_input()).encode())
                except (OSError, serial.SerialException):
                    pass  # Pico's existing 200 ms input watchdog also releases controls.
        executor.shutdown(wait=False, cancel_futures=True)
        donor.close(); link.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pi-side Raion authentication bridge")
    ap.add_argument("--serial", default="/dev/serial0")
    ap.add_argument("--donor-path", required=True)
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--press-ps", action="store_true", help="experimental 500 ms neutral PS-button pulse on each verified v3 USB mount")
    ap.add_argument("--r5-input", help="opt-in R5 evdev path, for example /dev/input/event5")
    ap.add_argument("--r5-map", help="custom captured input-map JSON; requires --r5-input")
    ap.add_argument("--outputs-dry-run", action="store_true", help="decode complete G29 output without opening output devices")
    ap.add_argument("--ffb", action="store_true", help="opt-in stage-one R5 constant force; requires --r5-input and output-capable firmware")
    ap.add_argument("--ffb-gain", type=float, default=.10, help="force scale, default 0.10, maximum 0.30")
    ap.add_argument("--ffb-invert", action="store_true", help="diagnostic override: reverse the R5-verified default polarity")
    ap.add_argument("--ffb-damper", action="store_true", help="experimental G29 damper translation; requires --ffb")
    ap.add_argument("--r5-leds", help="R5 USB CDC serial path for the ESX rev bar")
    args = ap.parse_args(argv)
    if args.r5_map and not args.r5_input: ap.error("--r5-map requires --r5-input")
    return run(args.serial, args.donor_path, args.baud, press_ps=args.press_ps,
               r5_path=args.r5_input, r5_map=args.r5_map, ffb=args.ffb, ffb_gain=args.ffb_gain,
               ffb_damper=args.ffb_damper,
               ffb_invert=args.ffb_invert, led_path=args.r5_leds, outputs_dry_run=args.outputs_dry_run)
