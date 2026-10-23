import struct
from moza_ps5.diagnostics import hello_text, status_text


def test_hello_old_and_v3_profile():
    old = struct.pack("<IHH", 1_000_000, 1, 0x100)
    assert "firmware=0.1.0" in hello_text(old)
    assert "profile=" not in hello_text(old)
    v3 = struct.pack("<IHHBH", 1_000_000, 1, 0x300, 3, 160)
    assert hello_text(v3).endswith("firmware=0.3.0 profile=v3 descriptor_bytes=160")
    assert "short HELLO" in hello_text(b"")


def test_status_old_extended_and_truncated():
    old = struct.pack("<4B7I", 3, 4, 9, 31, 0x12345678, 5, 6, 7, 8, 9, 10)
    text = status_text(old)
    assert "mounted=True donor_live=True phase=4 nonce_id=09 bitmap=1f" in text
    assert "auth_generation=12345678 good_pages=5 bad_pages=6 served=7 outputs=8 parser_errors=9 tx_drops=10" in text
    extended = old + struct.pack("<BBH7I", 3, 1, 160, 11, 12, 13, 14, 15, 16, 17)
    text = status_text(extended)
    assert text.endswith("feature_gets=11 feature_sets=12 definition_reads=13 f3_reads=14 input_reports_sent=15 descriptor_reads=16 unknown_features=17")
    assert "profile=v3 descriptor_bytes=160 diagnostics_schema=1" in text
    for length in range(64):
        text = status_text(extended[:length])
        if length < 8:
            assert text is None
        else:
            assert "feature_gets=" not in text


def test_unknown_diagnostics_schema_is_not_decoded():
    data = bytearray(64); data[32:36] = bytes((99, 2, 160, 0))
    text = status_text(data)
    assert "profile=99" in text and "diagnostics_schema=2" in text
    assert "feature_gets=" not in text
