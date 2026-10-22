"""Just enough protobuf to build Farcaster messages, with no dependencies.

Field numbers come from snapchain's `proto/definitions/message.proto`. The
encoding follows the official TypeScript library (`@farcaster/core`, built
with ts-proto) byte for byte, so the hashes match theirs:

* scalars and bytes are written only when they are not zero / empty
* packed repeated numbers (mentions, scopes...) are always written, even
  when empty
* a oneof is written where it is declared in the message, not in field order

Only the message types a client needs are here: casts, reactions, links,
user data and key adds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# MessageType
CAST_ADD = 1
CAST_REMOVE = 2
REACTION_ADD = 3
REACTION_REMOVE = 4
LINK_ADD = 5
LINK_REMOVE = 6
USER_DATA_ADD = 11
KEY_ADD = 16
KEY_REMOVE = 17

# KeyRemoveBody.signature_type
REMOVE_BY_CUSTODY = 1
REMOVE_BY_SELF = 2

# ReactionType
LIKE = 1
RECAST = 2

# UserDataType
USER_DATA_PFP = 1
USER_DATA_DISPLAY = 2
USER_DATA_BIO = 3
USER_DATA_URL = 5
USER_DATA_USERNAME = 6

MAINNET = 1
HASH_SCHEME_BLAKE3 = 1
SIGNATURE_SCHEME_ED25519 = 1


def varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64  # protobuf int32/int64 negatives use ten bytes
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(num: int, wire: int) -> bytes:
    return varint((num << 3) | wire)


def f_int(num: int, value: int) -> bytes:
    """A varint field, skipped when zero."""
    return _tag(num, 0) + varint(value) if value else b""


def f_bytes(num: int, value: bytes) -> bytes:
    """A length-delimited field (bytes, string or message), skipped when empty."""
    return _tag(num, 2) + varint(len(value)) + value if value else b""


def f_msg(num: int, value: bytes) -> bytes:
    """A submessage that is set, written even if it encodes to nothing."""
    return _tag(num, 2) + varint(len(value)) + value


def f_str(num: int, value: str) -> bytes:
    return f_bytes(num, value.encode())


def f_packed(num: int, values: list[int]) -> bytes:
    """Packed repeated varints. Always written, like ts-proto does."""
    body = b"".join(varint(v) for v in values)
    return _tag(num, 2) + varint(len(body)) + body


# ------------------------------------------------------------------ bodies


@dataclass(frozen=True)
class CastId:
    fid: int
    hash: bytes  # 20 bytes

    def encode(self) -> bytes:
        return f_int(1, self.fid) + f_bytes(2, self.hash)


@dataclass(frozen=True)
class Embed:
    url: str | None = None
    cast_id: CastId | None = None

    def encode(self) -> bytes:
        if self.url is not None:
            return f_msg(1, self.url.encode())
        if self.cast_id is not None:
            return f_msg(2, self.cast_id.encode())
        return b""


@dataclass(frozen=True)
class CastAddBody:
    text: str = ""
    mentions: list[int] = field(default_factory=list)
    mentions_positions: list[int] = field(default_factory=list)  # UTF-8 byte offsets
    embeds: list[Embed] = field(default_factory=list)
    parent_cast_id: CastId | None = None
    parent_url: str | None = None
    type: int = 0  # 0 cast, 1 long cast, 2 10k cast

    def encode(self) -> bytes:
        out = f_packed(2, self.mentions)
        # oneof parent { parent_cast_id = 3; parent_url = 7; } sits here.
        if self.parent_cast_id is not None:
            out += f_msg(3, self.parent_cast_id.encode())
        elif self.parent_url is not None:
            out += f_msg(7, self.parent_url.encode())
        out += f_str(4, self.text)
        out += f_packed(5, self.mentions_positions)
        for e in self.embeds:
            out += f_msg(6, e.encode())
        out += f_int(8, self.type)
        return out


@dataclass(frozen=True)
class CastRemoveBody:
    target_hash: bytes

    def encode(self) -> bytes:
        return f_bytes(1, self.target_hash)


@dataclass(frozen=True)
class ReactionBody:
    type: int  # LIKE or RECAST
    target_cast_id: CastId | None = None
    target_url: str | None = None

    def encode(self) -> bytes:
        out = f_int(1, self.type)
        if self.target_cast_id is not None:
            out += f_msg(2, self.target_cast_id.encode())
        elif self.target_url is not None:
            out += f_msg(3, self.target_url.encode())
        return out


@dataclass(frozen=True)
class LinkBody:
    target_fid: int
    type: str = "follow"

    def encode(self) -> bytes:
        return f_str(1, self.type) + _tag(3, 0) + varint(self.target_fid)


@dataclass(frozen=True)
class UserDataBody:
    type: int
    value: str

    def encode(self) -> bytes:
        return f_int(1, self.type) + f_str(2, self.value)


@dataclass(frozen=True)
class KeyAddBody:
    key: bytes
    custody_signature: bytes
    deadline: int
    nonce: int
    metadata: bytes
    scopes: list[int]
    ttl: int
    key_type: int = 1  # Ed25519
    metadata_type: int = 1  # SignedKeyRequest

    def encode(self) -> bytes:
        return (
            f_bytes(1, self.key)
            + f_int(2, self.key_type)
            + f_bytes(3, self.custody_signature)
            + f_int(4, self.deadline)
            + f_int(5, self.nonce)
            + f_bytes(6, self.metadata)
            + f_int(7, self.metadata_type)
            + f_packed(9, self.scopes)
            + f_int(10, self.ttl)
        )


@dataclass(frozen=True)
class KeyRemoveBody:
    key: bytes
    signature_type: int  # REMOVE_BY_CUSTODY or REMOVE_BY_SELF
    deadline: int
    nonce: int
    signature: bytes = b""  # custody EIP-712; empty for self-revocation

    def encode(self) -> bytes:
        return (
            f_bytes(1, self.key)
            + f_bytes(2, self.signature)
            + f_int(3, self.signature_type)
            + f_int(4, self.deadline)
            + f_int(5, self.nonce)
        )


# MessageData.body oneof: message type -> field number
BODY_FIELD = {
    CAST_ADD: 5,
    CAST_REMOVE: 6,
    REACTION_ADD: 7,
    REACTION_REMOVE: 7,
    USER_DATA_ADD: 12,
    LINK_ADD: 14,
    LINK_REMOVE: 14,
    KEY_ADD: 19,
    KEY_REMOVE: 20,
}


def message_data(type: int, fid: int, timestamp: int, body: bytes, network: int = MAINNET) -> bytes:
    """Encode MessageData. `timestamp` is Farcaster time."""
    return (
        f_int(1, type)
        + f_int(2, fid)
        + f_int(3, timestamp)
        + f_int(4, network)
        + f_msg(BODY_FIELD[type], body)
    )


def message(data: bytes, hash: bytes, signature: bytes, signer: bytes) -> bytes:
    """Encode a signed Message. `data` goes in twice, as `data` and as
    `data_bytes`, like the official library does; the node hashes
    `data_bytes` when it is present."""
    return (
        f_msg(1, data)
        + f_bytes(2, hash)
        + f_int(3, HASH_SCHEME_BLAKE3)
        + f_bytes(4, signature)
        + f_int(5, SIGNATURE_SCHEME_ED25519)
        + f_bytes(6, signer)
        + f_bytes(7, data)
    )
