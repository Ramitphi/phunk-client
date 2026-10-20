"""Which app signed a message.

Every Farcaster message (cast, like, follow...) carries a `signer`: the
public key that signed it. An account adds keys on-chain through the
KeyGateway contract, and each ADD event carries ABI-encoded
`SignedKeyRequestMetadata`:

    struct SignedKeyRequestMetadata {
        uint256 requestFid;     // the app that asked for the key
        address requestSigner;  // the app's custody address
        bytes   signature;      // the app's signature over the request
        uint256 deadline;
    }

`requestFid` is itself an account, and its username names the app. So
message -> signer -> key ADD event -> requestFid -> app.

Off-chain ("gasless") keys have no on-chain event. The node lists them in
/v1/signersByFid with the app in `requestFid`.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

WORD = 32


@dataclass(frozen=True)
class KeyRequest:
    """Decoded SignedKeyRequestMetadata."""

    request_fid: int
    request_signer: str  # 0x-prefixed address
    signature: bytes
    deadline: int  # Unix time


def _word(buf: bytes, i: int) -> int:
    chunk = buf[i : i + WORD]
    if len(chunk) != WORD:
        raise ValueError("metadata is truncated")
    return int.from_bytes(chunk, "big")


def decode_key_request(metadata: str | bytes) -> KeyRequest:
    """Decode the `metadata` field of a key ADD event.

    Accepts the base64 string the node returns, or raw bytes. The value is
    `abi.encode(SignedKeyRequestMetadata)`; because the struct holds a
    dynamic `bytes` field, it starts with an offset word that points at the
    struct (normally 0x20).
    """
    buf = base64.b64decode(metadata) if isinstance(metadata, str) else bytes(metadata)
    base = _word(buf, 0)
    if base + 4 * WORD > len(buf):
        raise ValueError("metadata is not SignedKeyRequestMetadata")
    request_fid = _word(buf, base)
    signer = buf[base + WORD + 12 : base + 2 * WORD]
    sig_off = base + _word(buf, base + 2 * WORD)
    deadline = _word(buf, base + 3 * WORD)
    sig_len = _word(buf, sig_off)
    signature = buf[sig_off + WORD : sig_off + WORD + sig_len]
    if len(signature) != sig_len:
        raise ValueError("metadata signature is truncated")
    return KeyRequest(request_fid, "0x" + signer.hex(), signature, deadline)
