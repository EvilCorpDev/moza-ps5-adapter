from pathlib import Path
import sys
import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from verify_v3_firmware import descriptor_bytes, hid_layout, verify_descriptor, verify_uf2
from bin_to_uf2 import convert


def test_v3_descriptor_matches_pinned_reference_and_wire_sizes():
    data = descriptor_bytes(); verify_descriptor(data)
    reports, applications = hid_layout(data)
    assert applications[0] == (1, 4)
    assert reports[("input", 1)] // 8 + 1 == 64
    assert reports[("feature", 3)] // 8 + 1 == 48
    altered = bytearray(data); altered[3] = 5
    with pytest.raises(ValueError): verify_descriptor(altered)


@pytest.mark.parametrize("data", [bytes.fromhex("26 ff"), bytes.fromhex("a1 01"),
                                 bytes.fromhex("c0"), bytes.fromhex("fe"), bytes.fromhex("85 00")])
def test_malformed_hid_items_are_rejected(data):
    with pytest.raises(ValueError): hid_layout(data)


def test_uf2_payload_family_address_and_padding(tmp_path):
    binary = bytes(range(256)) + b"second block"
    source, target = tmp_path / "test.bin", tmp_path / "test.uf2"
    source.write_bytes(binary); convert(source, target)
    uf2 = target.read_bytes()
    assert verify_uf2(binary, uf2) == 2
    for offset in (8, 12, 28, 32, 512+32+len(b"second block"), 508):
        corrupt = bytearray(uf2); corrupt[offset] ^= 1
        with pytest.raises(ValueError): verify_uf2(binary, corrupt)
    with pytest.raises(ValueError): verify_uf2(binary, uf2[:-1])
