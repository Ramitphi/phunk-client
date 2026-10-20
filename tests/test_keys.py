import base64

import pytest

from phunk import decode_key_request

# A real key ADD event's metadata, from dwr's (fid 3) first key, added Nov 2023.
REAL = (
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAjwAAAAAAAAAAAAAAA"
    "AALveQ3XmTo1/YR8BT7drpQNBVWWAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAZO55jgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABBmUVWQv9Ny0b1L47L0MPftfyiF8T0lECNeNpo2QK0Kv0H"
    "B3HHy1nUZ0J19bcxVHVt7jamCIeKonCMgLQe8kfOKBwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def test_decodes_real_metadata():
    r = decode_key_request(REAL)
    assert r.request_fid == 9152  # the main Farcaster app
    assert r.request_signer == "0x02ef790dd7993a35fd847c053eddae940d055596"
    assert len(r.signature) == 65
    assert r.deadline == 0x64EE798E


def word(n: int) -> bytes:
    return n.to_bytes(32, "big")


def test_decodes_handmade_metadata():
    sig = b"\x01" * 65
    body = word(42) + b"\x00" * 12 + b"\xab" * 20 + word(0x80) + word(1_700_000_000)
    body += word(len(sig)) + sig + b"\x00" * 31
    r = decode_key_request(word(0x20) + body)
    assert r.request_fid == 42
    assert r.request_signer == "0x" + "ab" * 20
    assert r.signature == sig
    assert r.deadline == 1_700_000_000
    # Raw bytes and base64 give the same answer.
    assert decode_key_request(base64.b64encode(word(0x20) + body).decode()) == r


@pytest.mark.parametrize("bad", [b"", b"\x00" * 31, word(0x20) + word(1)])
def test_rejects_garbage(bad):
    with pytest.raises(ValueError):
        decode_key_request(bad)
