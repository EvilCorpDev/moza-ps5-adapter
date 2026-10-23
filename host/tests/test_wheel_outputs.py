from collections import Counter
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest
from moza_ps5 import linux_ffb, output_worker
from moza_ps5.linux_ffb import effect_bytes, scaled_force
from moza_ps5.moza_leds import FrameParser, SerialFrame, frame, legacy_led_mask, legacy_led_frame
from moza_ps5.protocol import OUTPUT, Packet
from moza_ps5.wheel_output import FullOutputGate, WheelCommand, decode_g29

FIXTURE = json.loads((Path(__file__).with_name('fixtures') / 'g29_acc_outputs.json').read_text())
TIMINGS = (75,79,82,85,87,88,89,90,92,94)


def report(command):
    return b'\x30' + command.ljust(31,b'\0')


def status(full=True):
    data = bytearray(76 if full else 64)
    data[0] = 3; data[32] = 3; data[33] = 1
    data[34:36] = (160).to_bytes(2,'little')
    if full: data[72] = 1
    return bytes(data)


def packet(seq=1, ms=100, data=None):
    data = report(b'\x11\x08\x81\x01') if data is None else data
    return Packet(OUTPUT, 12, seq, struct.pack('<IBB',ms,0,len(data))+data)


def test_every_actual_acc_output_decodes_with_independent_capture_counts():
    assert FIXTURE['source_sha256'] == '221e9b4f169ea28e9b34ecf5f7c323cf3aff89ed503f5193ab9bb7ad91690146'
    counts, masks = Counter(), Counter()
    for row in FIXTURE['samples']:
        command = decode_g29(row['report_id'],bytes.fromhex(row['hex']))
        counts[command.kind] += row['count']
        if command.kind == 'leds': masks[command.value] += row['count']
        if command.kind == 'constant': assert -128 <= command.value <= 127
    assert counts == {'leds':2862,'constant':2603,'damper':6,'unknown':3}
    assert masks == {0:2205,1:72,3:102,7:95,15:74,31:314}


@pytest.mark.parametrize('rid,data',[(0,b'\x11\x08\xff'),(9,report(b'\x11\x08\xff')),
                                  (0,report(b'\xf8\x12\x80')),
                                  (0,report(b'\x11\x08\xff')[:-1]+b'\1')])
def test_unrecognized_hid_shapes_never_create_force_or_leds(rid,data):
    assert decode_g29(rid,data).kind == 'unknown'


def test_report_id_five_and_stop_replace_slot_one():
    assert decode_g29(5,report(b'\x11\x08\x80')[1:]).value == 0
    assert decode_g29(0,report(b'\x13')).value == 0
    assert decode_g29(0,report(b'\xf3')).value == 0
    assert decode_g29(0,report(b'\x11\x0c')).kind == 'damper'


def test_full_stream_requires_capability_handles_gaps_and_resets():
    gate = FullOutputGate()
    gate.status(status(False),1)
    assert not gate.ready(1) and gate.accept(packet(),1) is None
    gate.status(status(),1)
    assert gate.accept(packet(),1).value == 1
    assert gate.accept(packet(3,110),1.01) is None
    assert gate.accept(packet(4,120),1.02).value == 1
    gate.event(4)
    assert not gate.ready(1.03)
    gate.status(status(),1.04)
    assert not gate.ready(1.04)
    gate.event(5); gate.status(status(),1.05)
    assert gate.ready(1.05)
    gate.reset(); assert not gate.ready(1.06)


def test_queued_output_clock_wrap_and_invalid_lengths():
    gate=FullOutputGate(); gate.status(status(),0)
    assert gate.accept(packet(0xffffffff,0xfffffff0),0)
    assert gate.accept(packet(0,0x10),.032)
    assert gate.accept(packet(1,0x20),.20) is None
    bad=packet(2,0x30); bad.payload=bad.payload[:-1]
    assert gate.accept(bad,.21) is None
    assert gate.rejected == 2


@pytest.mark.parametrize('pointer,size,ioctl_code',[(4,44,0x402c4580),(8,48,0x40304580)])
def test_linux_effect_matches_generic_32_and_64_bit_uapi(pointer,size,ioctl_code):
    data=effect_bytes(-1234,7,pointer)
    assert len(data)==size
    assert data[:18].hex()=='5200070000c0000000009600000000002efb'
    assert data[18:]==bytes(size-18)
    assert (1<<30)|(len(data)<<16)|(ord('E')<<8)|0x80 == ioctl_code


@pytest.mark.parametrize('gain',[float('nan'),float('inf'),-.01,.300001])
def test_gain_is_finite_and_bounded_before_device_access(gain):
    with pytest.raises(ValueError): scaled_force(127,gain)


def test_force_scale_and_polarity():
    assert scaled_force(-128,.1)==3277
    assert scaled_force(127,.1)==-3251
    assert scaled_force(127,.1,True)==3251
    assert scaled_force(0,.25)==0


def test_explicit_thirty_percent_constant_force_scaling():
    assert scaled_force(-128,.30) == 9830
    assert scaled_force(127,.30) == -9753
    assert scaled_force(127,.30,True) == 9753


def test_led_frames_and_captured_settings_replies():
    assert frame(0x41,0x13,b'\xfd\xde',bytes(4)).hex()=='7e064113fdde00000000c0'
    parser=FrameParser()
    replies=parser.feed(bytes.fromhex('7e02c0310401837e0bc031024b4f52555758595a5c5ee67e00c0317c'))
    assert replies == [SerialFrame(0xc0,0x31,b'\4\1'),
                       SerialFrame(0xc0,0x31,b'\2'+bytes(TIMINGS)),SerialFrame(0xc0,0x31,b'')]
    assert parser.errors==0


def test_serial_parser_fragmentation_stuffing_noise_and_checksum_rejection():
    encoded=frame(0xc0,0x31,b'\2',b'\x7e\0')
    parser=FrameParser(); replies=[]
    for value in b'debug line\n'+encoded:
        replies += parser.feed(bytes((value,)))
    assert replies==[SerialFrame(0xc0,0x31,b'\2\x7e\0')]
    bad=bytearray(encoded); bad[-1]^=1
    assert parser.feed(bytes(bad)+encoded)==replies and parser.errors >= 1


@pytest.mark.parametrize('mask,wire', [
    (0, '7e064113fdde00000000c0'),
    (1, '7e064113fdde00000003c3'),
    (3, '7e064113fdde0000000fcf'),
    (7, '7e064113fdde0000003fff'),
    (15, '7e064113fdde000000ffbf'),
    (31, '7e064113fdde000003ffc2'),
])
def test_g29_steps_match_physically_verified_esx_sweep_frames(mask,wire):
    assert legacy_led_frame(mask) == bytes.fromhex(wire)


@pytest.mark.parametrize('mask',[-1,32,None,1.5,True])
def test_legacy_bar_refuses_invalid_g29_masks(mask):
    with pytest.raises(ValueError): legacy_led_mask(mask)


def test_sparse_g29_led_pattern_preserves_corresponding_pairs():
    assert legacy_led_mask(0b10101) == 0b1100110011


def test_worker_preserves_sparse_led_pattern(monkeypatch):
    worker,forces,masks=worker_with_fake_devices(monkeypatch)
    worker.set_ready(True,1)
    worker.submit(WheelCommand('leds',0b10101),1)
    worker.tick(1.01)
    assert masks[-1] == 0b10101


def worker_with_fake_devices(monkeypatch):
    forces, masks=[] ,[]
    monkeypatch.setattr(output_worker,'LinuxConstantForce',lambda path:SimpleNamespace(
        apply=lambda level,**kw:forces.append((level,kw)),close=lambda:None))
    monkeypatch.setattr(output_worker,'LegacyLedBar',lambda path:SimpleNamespace(
        apply=lambda mask:masks.append(mask),close=lambda:None))
    monkeypatch.setattr(output_worker.Thread,'start',lambda self:None)
    return output_worker.OutputWorker(ffb_path='r5',led_path='cdc'),forces,masks


def test_worker_stops_on_stale_bridge_force_timeout_and_unknown_slot(monkeypatch):
    worker,forces,masks=worker_with_fake_devices(monkeypatch)
    worker.set_ready(True,1)
    worker.submit(WheelCommand('constant',127),1)
    worker.submit(WheelCommand('leds',31),1)
    worker.tick(1.01)
    assert forces[-1][0]==-3251 and masks[-1]==31
    worker.tick(1.11)
    assert forces[-1][0]==0 and masks[-1]==0 # Main bridge has stopped updating.
    worker.set_ready(True,1.16); worker.tick(1.16)
    assert forces[-1][0]==0
    worker.submit(WheelCommand('unsupported-slot-1',0),1.17); worker.tick(1.17)
    assert forces[-1][0]==0
    worker.set_ready(False,1.18); worker.set_ready(True,1.19); worker.tick(1.23)
    assert masks[-1]==0 # A reconnect cannot revive a previously held LED mask.


def test_dry_run_cannot_open_any_output_handle():
    with pytest.raises(ValueError): output_worker.OutputWorker(ffb_path='r5',dry_run=True)
    worker=output_worker.OutputWorker(dry_run=True)
    assert worker.force is worker.leds is worker.thread is None


def ff_environment(monkeypatch, *, vid=0x346e, constant=True, slots=10):
    opened,closed,uploads,writes=[],[],[],[]
    monkeypatch.setattr(linux_ffb.sys,'platform','linux')
    monkeypatch.setattr(linux_ffb.platform,'machine',lambda:'aarch64')
    monkeypatch.setattr(linux_ffb.os,'O_NONBLOCK',2048,raising=False)
    monkeypatch.setattr(linux_ffb.os,'open',lambda path,flags:opened.append((path,flags)) or 42)
    monkeypatch.setattr(linux_ffb.os,'close',lambda fd:closed.append(fd))
    monkeypatch.setattr(linux_ffb.os,'write',lambda fd,data:writes.append(data) or len(data))
    clock=SimpleNamespace(now=1.,delay=False,erased=[])
    monkeypatch.setattr(linux_ffb.time,'monotonic',lambda:clock.now)
    def ioctl(fd,code,data,mutate=True):
        number=code&255
        if number==2: data[:]=struct.pack('@HHHH',3,vid,4,273)
        elif number==0x35: data[:]=(1<<82 if constant else 0).to_bytes(16,'little')
        elif number==0x84: data[:]=struct.pack('@i',slots)
        elif number==0x80:
            uploads.append(bytes(data))
            struct.pack_into('<h',data,2,3)
            if clock.delay: clock.now+=1
        elif number==0x81:
            assert data==3
            clock.erased.append(data)
        else: raise AssertionError(code)
    import sys
    monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(ioctl=ioctl))
    return clock,opened,closed,uploads,writes


@pytest.mark.parametrize('vid,constant,slots',[(0x046d,True,10),(0x346e,False,10),(0x346e,True,0)])
def test_ff_backend_rejects_identity_or_missing_capability_without_writes(monkeypatch,vid,constant,slots):
    _,_,closed,uploads,writes=ff_environment(monkeypatch,vid=vid,constant=constant,slots=slots)
    with pytest.raises(ValueError): linux_ffb.LinuxConstantForce('r5')
    assert closed==[42] and uploads==writes==[]


def test_ff_backend_reuses_finite_effect_stops_erases_and_closes(monkeypatch):
    clock,opened,closed,uploads,writes=ff_environment(monkeypatch)
    device=linux_ffb.LinuxConstantForce('r5')
    assert opened[0][1]==linux_ffb.os.O_RDWR|2048 and not uploads
    device.apply(123,valid_until=1.15); device.apply(-234,valid_until=1.15)
    assert struct.unpack_from('<h',uploads[0],2)[0]==-1
    assert struct.unpack_from('<h',uploads[1],2)[0]==3
    assert all(struct.unpack_from('<H',data,10)[0]==150 for data in uploads)
    assert [struct.unpack('@llHHi',data)[2:] for data in writes]==[(21,3,1),(21,3,1)]
    device.close(); device.close()
    assert struct.unpack('@llHHi',writes[-1])[2:]==(21,3,0) and closed==[42]


def test_ff_backend_never_plays_an_effect_after_a_slow_upload_expires(monkeypatch):
    clock,_,closed,uploads,writes=ff_environment(monkeypatch)
    clock.delay=True
    device=linux_ffb.LinuxConstantForce('r5')
    device.apply(123,valid_until=1.15)
    assert len(uploads)==1 and not writes
    device.close(); assert closed==[42]


@pytest.mark.parametrize('encoded,level',[(0x01,3251),(0x80,0),(0xff,-3251)])
def test_captured_force_uses_physically_verified_polarity_and_releases_slot(monkeypatch,encoded,level):
    clock,_,closed,uploads,writes=ff_environment(monkeypatch)
    device=linux_ffb.LinuxConstantForce('r5')
    command=decode_g29(0,report(bytes((0x11,0x08,encoded,0x01))))
    device.apply(scaled_force(command.value,.10),valid_until=1.15)
    if level:
        assert struct.unpack_from('<H',uploads[-1],4)[0]==0xc000
        assert struct.unpack_from('<h',uploads[-1],16)[0]==level
        assert device.effect_id==3 and device.playing
        device.stop()
        assert struct.unpack('@llHHi',writes[-1])[2:]==(21,3,0)
        assert clock.erased==[3]
    else:
        assert not uploads and not writes
    assert device.effect_id==-1 and not device.playing
    device.close(); assert closed==[42]


def test_disconnect_erases_force_while_bridge_remains_alive_and_reconnect_stays_zero(monkeypatch):
    clock,_,closed,uploads,writes=ff_environment(monkeypatch)
    monkeypatch.setattr(output_worker.Thread,'start',lambda self:None)
    worker=output_worker.OutputWorker(ffb_path='r5')
    worker.set_ready(True,1)
    worker.submit(WheelCommand('constant',127),1)
    worker.tick(1.01)
    assert worker.applied_force==-3251 and worker.force.effect_id==3
    worker.set_ready(False,1.02); worker.tick(1.03)
    assert worker.applied_force==0 and worker.force.effect_id==-1
    assert clock.erased==[3]
    assert not closed and struct.unpack('@llHHi',writes[-1])[2:]==(21,3,0)
    assert 'uploaded effect erased' in worker.messages.get_nowait()
    worker.set_ready(True,1.04); worker.tick(1.05)
    assert len(uploads)==1 and worker.applied_force==0
    # Only a new accepted game command can allocate/play another effect.
    worker.submit(WheelCommand('constant',-127),1.06); worker.tick(1.07)
    assert len(uploads)==2 and struct.unpack_from('<h',uploads[-1],2)[0]==-1
    assert worker.applied_force==3251
    worker.force.close(); assert closed==[42]


def test_stop_attempts_erase_even_if_playback_stop_write_fails(monkeypatch):
    clock,_,closed,uploads,writes=ff_environment(monkeypatch)
    device=linux_ffb.LinuxConstantForce('r5')
    device.apply(123,valid_until=1.15)
    monkeypatch.setattr(device,'_play',lambda value:(_ for _ in ()).throw(OSError('stop failed')))
    with pytest.raises(OSError,match='stop failed'): device.stop()
    assert device.effect_id==-1 and not device.playing
    assert clock.erased==[3]
    device.close(); assert closed==[42]


@pytest.mark.parametrize('full_firmware',[False,True])
def test_bridge_output_opt_in_preserves_signing_and_never_uses_sampled_events(monkeypatch,full_firmware):
    from moza_ps5 import bridge
    from moza_ps5.protocol import CHALLENGE, EVENT, HELLO, INPUT, OUTPUT_CONTROL, RESPONSE, STATUS, StreamParser
    clock=SimpleNamespace(now=0.)
    link=SimpleNamespace(writes=[],first=True,sequence=0,closed=False)
    def read(count):
        link.sequence+=1
        data=Packet(STATUS,123,payload=status(full_firmware)).encode()
        # Include a full-force legacy sampled event: it must never drive output.
        data+=Packet(EVENT,123,payload=b'\3\0\x20\0'+report(b'\x11\x08\xff\x01')).encode()
        data+=Packet(OUTPUT,123,link.sequence,
                     struct.pack('<IBB',round(clock.now*1000),0,32)+report(b'\x11\x08\x81\x01')).encode()
        if link.first:
            link.first=False
            data=(Packet(HELLO,123,payload=struct.pack('<IHHBH',1_000_000,1,0x301,3,160)).encode()
                  +Packet(CHALLENGE,123,4,bytes((1,))+bytes(280)).encode()+data)
        return data
    link.read=read; link.write=lambda data:link.writes.append(data) or len(data)
    link.close=lambda:setattr(link,'closed',True)
    worker=SimpleNamespace(ready=False,submitted=[],closed=False,messages=output_worker.SimpleQueue())
    worker.set_ready=lambda ready,now:setattr(worker,'ready',ready)
    worker.invalidate=lambda:setattr(worker,'ready',False)
    worker.submit=lambda command,now:worker.submitted.append(command) if worker.ready else None
    worker.summary=lambda:'mock outputs'
    worker.close=lambda:setattr(worker,'closed',True)
    class Pending:
        def done(self): return clock.now>=2.1
        def result(self): return SimpleNamespace(nonce_id=1,signature_blob=bytes(1064),elapsed_s=2.1,f1_crc_mismatch_pages=[])
    class Executor:
        def __init__(self,**kw): pass
        def submit(self,*args): return Pending()
        def shutdown(self,**kw): pass
    def sleep(duration):
        clock.now+=.02
        if clock.now>2.4: raise KeyboardInterrupt
    monkeypatch.setattr(bridge.serial,'Serial',lambda *a,**kw:link)
    monkeypatch.setattr(bridge,'HidDonor',lambda path:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(bridge,'OutputWorker',lambda **kw:worker)
    monkeypatch.setattr(bridge,'ThreadPoolExecutor',Executor)
    monkeypatch.setattr(bridge.secrets,'randbits',lambda bits:123)
    monkeypatch.setattr(bridge.time,'monotonic',lambda:clock.now)
    monkeypatch.setattr(bridge.time,'sleep',sleep)
    assert bridge.run('serial','donor',outputs_dry_run=True)==0
    if full_firmware:
        assert worker.submitted and all(command.value==1 for command in worker.submitted)
    else:
        assert not worker.submitted
    packets=StreamParser().feed(b''.join(link.writes))
    assert sum(p.kind==RESPONSE for p in packets)==1
    controls=[p.payload for p in packets if p.kind==OUTPUT_CONTROL]
    assert controls[-1]==b'\0' and (b'\1' in controls)==full_firmware
    assert not any(p.kind==INPUT for p in packets) and worker.closed and link.closed
