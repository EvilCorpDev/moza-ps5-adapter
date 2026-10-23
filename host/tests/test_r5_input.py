import json
from pathlib import Path
from types import SimpleNamespace
import struct

import pytest
from moza_ps5 import bridge, cli, r5_input
from moza_ps5.r5_input import Axis, InputState, LinuxR5Source, load_map
from moza_ps5.ps_button import V3InputGate, neutral_input
from moza_ps5.protocol import CHALLENGE, HELLO, INPUT, RESPONSE, STATUS, Packet, StreamParser

FIXTURE = json.loads((Path(__file__).with_name("fixtures") / "r5_es_srp_lite_capture.json").read_text())
MAPPING = load_map()
LEGACY_MAPPING = load_map(Path(__file__).with_name("fixtures") / "r5_profile_before_endpoints.json")


def unpack(payload):
    return struct.unpack("<5H2B", payload)


def axes_snapshot(snapshot):
    return {int(code): row["value"] for code, row in snapshot["absolute_axes"].items()}


@pytest.mark.parametrize("value,expected", [(-40000, 0), (-32768, 0), (0, 32768), (32767, 65535), (40000, 65535)])
def test_steering_full_signed_range(value, expected):
    assert MAPPING.axes["steering"].convert(value) == expected


@pytest.mark.parametrize("role,pressed", [("throttle", 31589), ("brake", 32569), ("clutch", 32509)])
def test_captured_pedal_endpoints_release_tolerance_and_inversion(role, pressed):
    axis = MAPPING.axes[role]
    assert axis.convert(-32768) == 65535
    assert axis.convert(-32615) == 65535  # Captured released clutch after button phases.
    assert axis.convert(pressed) == 0
    assert axis.convert(32767) == 0
    values = [axis.convert(x) for x in range(-32768, 32768, 256)]
    assert values == sorted(values, reverse=True)
    assert Axis(2, -32768, 32767, 32767, -32768, 256).convert(-32768) == 0


EXPECTED_BUTTONS = {"X":1, "A":2, "B":4, "Y":8, "Shift down":16, "Shift up":32,
                    "WIP":64, "N":128, "Box":256, "Start":512, "Radio":1024,
                    "Cam":2048, "Home":4096, "Menu":512}
HATS = {"D-pad up":0, "D-pad right":2, "D-pad down":4, "D-pad left":6}


@pytest.mark.parametrize("phase", FIXTURE["phases"], ids=lambda phase: phase["label"])
def test_every_real_capture_phase_replays_into_expected_controls(phase):
    assert FIXTURE["source_sha256"] == "324b585febbfcafeebe4584168c76017a1994617a54d2512ce561ccd604450f6"
    state = InputState()
    state.snapshot(axes_snapshot(phase["before"]), set(phase["before"]["pressed_keys"]))
    outputs = [unpack(MAPPING.payload(state.axes, state.pressed))]
    for _, _, kind, code, value in phase["events"]:
        state.event(kind, code, value)
        if kind == 0 and code == 0:
            outputs.append(unpack(MAPPING.payload(state.axes, state.pressed)))
    assert state.valid and state.syn_dropped == 0
    assert all(len(row) == 7 and row[-1] == 0 and 0 <= row[5] <= 8 for row in outputs)
    label = phase["label"]
    if label == "neutral":
        assert outputs == [(32821, 65535, 65535, 65535, 0, 8, 0)]
    elif label == "steering":
        assert min(row[0] for row in outputs) == 0
        assert max(row[0] for row in outputs) == 65535
    elif label in {"throttle", "brake", "clutch"}:
        index = {"throttle":1, "brake":2, "clutch":3}[label]
        assert min(row[index] for row in outputs) == 0
        assert max(row[index] for row in outputs) == 65535
    elif label.startswith("button:"):
        name = label.split(":", 1)[1]
        if name in HATS:
            assert {row[5] for row in outputs} == {8, HATS[name]}
            assert all(row[4] == 0 for row in outputs)
        else:
            assert {row[4] for row in outputs} == ({0, EXPECTED_BUTTONS[name]} if name in EXPECTED_BUTTONS else {0})
            assert all(row[5] == 8 for row in outputs)


def test_hat_diagonals_opposites_and_button_combinations():
    axes = axes_snapshot(FIXTURE["phases"][0]["before"])
    assert unpack(MAPPING.payload(axes, {292,293,288,301}))[4:6] == (34, 1)
    assert unpack(MAPPING.payload(axes, {292,294,293,295}))[5] == 8
    assert MAPPING.payload({}, {712}) == neutral_input()


@pytest.mark.parametrize("role,raw,previous,updated", [
    ("steering", -32652, 116, 0),  # Actual preview left endpoint.
    ("steering", 32671, 65439, 65535),  # Actual preview right endpoint.
    ("steering", -32656, 112, 0),  # Actual game left endpoint.
    ("steering", 32653, 65421, 65535),
    ("brake", 32481, 89, 0),  # Raw value consistent with logged 89 after integer rounding.
    ("clutch", 32479, 30, 0),
    ("steering", 0, 32768, 32768),
    ("brake", -32615, 65535, 65535),
    ("clutch", -32615, 65535, 65535),
])
def test_actual_preview_and_game_endpoint_residuals(role, raw, previous, updated):
    assert LEGACY_MAPPING.axes[role].convert(raw) == previous
    assert MAPPING.axes[role].convert(raw) == updated


def test_endpoint_tolerance_is_continuous_monotonic_and_optional():
    steer = MAPPING.axes["steering"]
    assert steer.convert(-32512) == 0 and steer.convert(-32511) == 1
    assert steer.convert(32510) == 65534 and steer.convert(32511) == 65535
    for role, axis in MAPPING.axes.items():
        values = [axis.convert(raw) for raw in range(-32768, 32768, 16)]
        assert values == sorted(values, reverse=role != "steering")
    brake = MAPPING.axes["brake"]
    assert brake.convert(32313) == 0 and brake.convert(32312) == 1
    assert Axis(2, -32768, 32767, 32767, -32768, 256, 256).convert(-32512) == 0
    assert Axis(0, -32768, 32767).convert(-32652) == 116  # Old/custom maps default to no trim.


def test_sync_loss_discards_old_events_until_fresh_snapshot():
    state = InputState(); state.snapshot({0:0}, set())
    state.event(1, 288, 1)
    assert not state.pressed  # Commit only at SYN_REPORT.
    state.event(0,0,0); assert state.pressed == {288}
    state.event(0,3,0)
    state.event(1, 301, 1); state.event(3, 2, 32767)
    assert not state.valid and state.dropping
    state.event(0,0,0)
    assert state.resync and not state.valid
    state.event(1, 301, 1); state.event(0,0,0)
    state.snapshot({0:12,2:-32768}, {289})
    assert state.valid and state.pressed == {289} and state.axes[2] == -32768
    assert state.syn_dropped == 1


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema="wrong"),
    lambda d: d["axes"]["steering"].update(code=True),
    lambda d: d["axes"]["steering"].update(minimum=32767),
    lambda d: d["axes"]["throttle"].update(pressed=-32768),
    lambda d: d["axes"]["brake"].update(code=2),
    lambda d: d["buttons"].update({"288":"unknown"}),
    lambda d: d["hat"].update(up=288),
    lambda d: d.update(buttons=[]),
    lambda d: d["axes"]["steering"].update(endpoint_deadzone=-1),
    lambda d: d["axes"]["steering"].update(endpoint_deadzone=True),
    lambda d: d["axes"]["steering"].update(endpoint_deadzone=32768),
    lambda d: d["axes"]["brake"].update(endpoint_deadzone=65081),
])
def test_bad_custom_map_is_rejected(tmp_path, change):
    data = json.loads(r5_input.DEFAULT_MAP.read_text())
    change(data)
    path = tmp_path / "map.json"; path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_map(path)


def status(flags=3, profile=3, schema=1, descriptor=160):
    result = bytearray(64); result[0] = flags
    result[32:36] = struct.pack("<BBH", profile,schema,descriptor)
    return bytes(result)


@pytest.mark.parametrize("payload", [bytes(32), status(flags=0),status(flags=1),status(flags=2),status(profile=2),status(schema=2),status(descriptor=156)])
def test_normal_input_requires_verified_mounted_v3_link(payload):
    gate = V3InputGate(); gate.status(payload, 0)
    assert not gate.can_send(0)


def test_input_gate_releases_on_stale_boot_unmount_and_suspend():
    gate = V3InputGate(); gate.status(status(), 0)
    assert gate.can_send(1.9) and not gate.can_send(2.01)
    gate.event(4); gate.status(status(), 2.1)
    assert not gate.can_send(2.1)
    gate.event(5); assert not gate.can_send(2.1)
    gate.status(status(), 2.2); assert gate.can_send(2.2)
    gate.event(2); assert not gate.can_send(2.3)
    gate.status(status(), 2.4); gate.new_boot(); assert not gate.can_send(2.4)


def source_environment(monkeypatch):
    monkeypatch.setattr(r5_input.sys, "platform", "linux")
    monkeypatch.setattr(r5_input.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(r5_input.os, "O_NONBLOCK", 2048, raising=False)
    opened, closed, chunks = [], [], []
    state = {"axes":{0:0,2:-32768,5:-32768,6:-32768},"pressed":set(),"vid":0x346e,"range":(-32768,32767)}
    monkeypatch.setattr(r5_input.os, "open", lambda path,flags: opened.append((path,flags)) or 42)
    monkeypatch.setattr(r5_input.os, "close", lambda fd: closed.append(fd))
    def read(fd,size):
        if chunks:
            item = chunks.pop(0)
            if isinstance(item, Exception): raise item
            return item
        raise BlockingIOError
    monkeypatch.setattr(r5_input.os, "read", read)
    def bits(codes,size):
        value = sum(1 << code for code in codes)
        return value.to_bytes(size,"little")
    def query(self,number,size):
        if number == 2: return struct.pack("@HHHH",3,state["vid"],4,273)
        if number == 0x23: return b"\x65" + bytes(7)
        if number == 0x21: return bits(FIXTURE["device"]["key_codes"],96)
        if number == 0x18: return bits(state["pressed"],96)
        return struct.pack("@iiiiii",state["axes"][number-0x40],*state["range"],255,4095,0)
    monkeypatch.setattr(LinuxR5Source,"_query",query)
    return state, chunks, opened, closed


def test_live_source_holds_steady_controls_disconnects_neutral_and_reconnects(monkeypatch):
    state, chunks, opened, closed = source_environment(monkeypatch)
    source = LinuxR5Source("/dev/input/event5",MAPPING)
    assert source.poll(0) == neutral_input()
    assert opened[0][1] == r5_input.os.O_RDONLY | 2048
    state["axes"][2] = 31589
    chunks.append(struct.pack("@llHHi",1,0,3,2,31589)+struct.pack("@llHHi",1,1,0,0,0))
    assert unpack(source.poll(.02))[1] == 0
    for at in (1,3,5):
        assert unpack(source.poll(at))[1] == 0 and source.live  # Silence is valid, snapshots check liveness.
    chunks.append(OSError("removed"))
    assert source.poll(5.01) == neutral_input() and not source.live
    assert source.poll(5.5) == neutral_input() and len(opened) == 1
    state["axes"][2] = -32768
    assert source.poll(6.02) == neutral_input() and source.live
    assert closed == [42]
    source.close(); assert closed == [42,42]


@pytest.mark.parametrize("problem", ["identity","range"])
def test_wrong_reconnected_device_or_range_never_drives_controls(monkeypatch,problem):
    state, chunks, opened, closed = source_environment(monkeypatch)
    if problem == "identity": state["vid"] = 0x046d
    else: state["range"] = (0,65535)
    source = LinuxR5Source("/dev/input/event5",MAPPING)
    assert source.poll(0) == neutral_input() and not source.live
    assert closed == [42]


@pytest.mark.parametrize("press_ps", [False,True])
def test_bridge_forwards_r5_during_signing_with_single_ps_writer_and_shutdown_neutral(monkeypatch,press_ps):
    clock = SimpleNamespace(now=0.0)
    actual_input = bytes.fromhex("00 90 00 00 ff ff ff ff 22 00 01 00")
    link = SimpleNamespace(writes=[], first=True, closed=False)
    def read(count):
        data = Packet(STATUS,123,payload=status()).encode()
        if link.first:
            link.first=False
            data = (Packet(HELLO,123,payload=struct.pack("<IHHBH",1_000_000,1,0x300,3,160)).encode()
                    +data+Packet(CHALLENGE,123,4,bytes((1,))+bytes(280)).encode())
        return data
    link.read=read; link.write=lambda data:link.writes.append(data) or len(data)
    link.close=lambda:setattr(link,"closed",True)
    source=SimpleNamespace(mapping=MAPPING,messages=[],live=True,frames=1,state=SimpleNamespace(syn_dropped=0),closed=False)
    source.poll=lambda now:actual_input
    source.close=lambda:setattr(source,"closed",True)
    class Pending:
        def done(self): return clock.now >= 2.1
        def result(self): return SimpleNamespace(nonce_id=1,signature_blob=bytes(1064),elapsed_s=2.1,f1_crc_mismatch_pages=[])
    class Executor:
        def __init__(self,**kwargs): pass
        def submit(self,*args): return Pending()
        def shutdown(self,**kwargs): pass
    def sleep(duration):
        clock.now += .05
        if clock.now > 2.6: raise KeyboardInterrupt
    monkeypatch.setattr(bridge,"LinuxR5Source",lambda *args:source)
    monkeypatch.setattr(bridge.serial,"Serial",lambda *args,**kwargs:link)
    monkeypatch.setattr(bridge,"HidDonor",lambda path:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(bridge,"ThreadPoolExecutor",Executor)
    monkeypatch.setattr(bridge.secrets,"randbits",lambda bits:123)
    monkeypatch.setattr(bridge.time,"monotonic",lambda:clock.now)
    monkeypatch.setattr(bridge.time,"sleep",sleep)
    assert bridge.run("serial","donor",press_ps=press_ps,r5_path="/dev/input/event5") == 0
    packets = StreamParser().feed(b"".join(link.writes))
    inputs = [p.payload for p in packets if p.kind == INPUT]
    assert actual_input in inputs and inputs[-1] == neutral_input()
    assert sum(p.kind == RESPONSE for p in packets) == 1
    if press_ps:
        assert neutral_input(True) in inputs
        assert inputs.index(actual_input) > inputs.index(neutral_input(True))
    assert link.closed and source.closed


def test_cli_opt_in_input_and_preview(monkeypatch):
    calls=[]
    monkeypatch.setattr(cli,"bridge",lambda *args,**kwargs:calls.append(kwargs) or 0)
    assert cli.main(["bridge","--donor-path","donor","--r5-input","/dev/input/event5","--r5-map","custom.json"]) == 0
    assert calls == [{"press_ps":False,"r5_path":"/dev/input/event5","r5_map":"custom.json"}]
    with pytest.raises(SystemExit): cli.main(["bridge","--donor-path","donor","--r5-map","custom.json"])
