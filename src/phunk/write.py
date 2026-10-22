"""Write to Farcaster: give your client an app key, then cast, react and follow.

    pip install 'phunk-client[write]'

Two kinds of keys are involved:

* The **custody key** is the Ethereum key that owns the account (the one
  behind the seed phrase). It never signs casts. Here it signs exactly one
  thing: the authorization that adds an app key.
* The **app key** is an Ed25519 key that signs every cast, like and follow.
  Snapchain accepts "gasless" app keys: you submit a KEY_ADD message to a
  node, authorized by an EIP-712 signature from the custody key. No
  transaction, no gas.

A gasless app key is scoped (it may only sign the message types listed when
it was added) and has a sliding TTL: every message it signs pushes its
expiry out again, up to 90 days.

**Seed phrases are dangerous.** Whoever holds one owns the account. Use a
test account, only paste a seed phrase into software you trust, and never
save it. This module never stores or logs it.
"""
from __future__ import annotations

import os
import re
import time

try:
    import blake3
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from nacl.signing import SigningKey
except ImportError as e:  # pragma: no cover
    raise ImportError("writing needs extra packages: pip install 'phunk-client[write]'") from e

from . import proto
from .hub import Hub, NodeError
from .keys import KeyRequest, decode_key_request
from .timeutil import from_unix

# EIP-712 domain for KEY_ADD, KEY_REMOVE and the SignedKeyRequest metadata.
# From snapchain src/core/validations/key.rs. The chain id is cosmetic:
# nothing is verified on-chain.
KEY_DOMAIN = {"name": "Farcaster KeyAdd", "version": "1", "chainId": 1}
_DOMAIN_TYPE = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
]
_KEY_ADD_TYPE = [
    {"name": "fid", "type": "uint256"},
    {"name": "key", "type": "bytes"},
    {"name": "keyType", "type": "uint32"},
    {"name": "scopes", "type": "uint32[]"},
    {"name": "ttl", "type": "uint32"},
    {"name": "nonce", "type": "uint32"},
    {"name": "deadline", "type": "uint256"},
]
_KEY_REMOVE_TYPE = [
    {"name": "fid", "type": "uint256"},
    {"name": "key", "type": "bytes"},
    {"name": "nonce", "type": "uint32"},
    {"name": "deadline", "type": "uint256"},
]
_SIGNED_KEY_REQUEST_TYPE = [
    {"name": "requestFid", "type": "uint256"},
    {"name": "key", "type": "bytes"},
    {"name": "deadline", "type": "uint256"},
]

# What a client app key may sign by default: casts, reactions, follows and
# profile edits. KEY_ADD / KEY_REMOVE are never allowed as scopes.
DEFAULT_SCOPES = [
    proto.CAST_ADD,
    proto.CAST_REMOVE,
    proto.REACTION_ADD,
    proto.REACTION_REMOVE,
    proto.LINK_ADD,
    proto.LINK_REMOVE,
    proto.USER_DATA_ADD,
]
DEFAULT_TTL = 30 * 24 * 3600  # sliding; the protocol allows up to 90 days
MAX_TTL = 90 * 24 * 3600

# Seed phrases from Farcaster apps derive the custody key on the standard
# Ethereum path.
DEFAULT_PATH = "m/44'/60'/0'/0/0"


def now() -> int:
    """Current Farcaster time."""
    return from_unix(time.time())


def message_hash(data: bytes) -> bytes:
    """Farcaster message hash: the first 20 bytes of BLAKE3(data)."""
    return blake3.blake3(data).digest()[:20]


# ------------------------------------------------------------------ keys


class AppKey:
    """An Ed25519 app key. Treat `private_hex` like a password."""

    def __init__(self, private: bytes):
        if len(private) != 32:
            raise ValueError("an Ed25519 private key is 32 bytes")
        self._sk = SigningKey(private)

    @classmethod
    def generate(cls) -> "AppKey":
        return cls(os.urandom(32))

    @classmethod
    def from_hex(cls, value: str) -> "AppKey":
        return cls(bytes.fromhex(value.removeprefix("0x")))

    @property
    def public_key(self) -> bytes:
        return bytes(self._sk.verify_key)

    @property
    def public_hex(self) -> str:
        return "0x" + self.public_key.hex()

    @property
    def private_hex(self) -> str:
        return "0x" + bytes(self._sk).hex()

    def __repr__(self) -> str:  # never print the private key by accident
        return f"AppKey({self.public_hex})"

    def sign(self, type: int, fid: int, body: bytes, timestamp: int | None = None) -> bytes:
        """Build and sign a Message. Returns its protobuf bytes."""
        data = proto.message_data(type, fid, now() if timestamp is None else timestamp, body)
        h = message_hash(data)
        signature = self._sk.sign(h).signature
        return proto.message(data, h, signature, self.public_key)


class Custody:
    """The account's custody key, held only in memory."""

    def __init__(self, account):
        self._account = account

    @classmethod
    def from_mnemonic(cls, phrase: str, path: str = DEFAULT_PATH) -> "Custody":
        Account.enable_unaudited_hdwallet_features()
        return cls(Account.from_mnemonic(" ".join(phrase.split()), account_path=path))

    @classmethod
    def from_private_key(cls, key: str | bytes) -> "Custody":
        return cls(Account.from_key(key))

    @property
    def address(self) -> str:
        return self._account.address

    def __repr__(self) -> str:
        return f"Custody({self.address})"

    def sign_typed(self, primary: str, fields: list[dict], message: dict) -> bytes:
        typed = {
            "types": {"EIP712Domain": _DOMAIN_TYPE, primary: fields},
            "primaryType": primary,
            "domain": KEY_DOMAIN,
            "message": message,
        }
        return bytes(self._account.sign_message(encode_typed_data(full_message=typed)).signature)

    def sign_key_request(self, request_fid: int, key: bytes, deadline: int) -> bytes:
        """The app's SignedKeyRequest, ABI-encoded as SignedKeyRequestMetadata.

        It binds the requesting app's fid to the key. A client that is its
        own app uses the account's own fid and custody key here.
        """
        sig = self.sign_typed("SignedKeyRequest", _SIGNED_KEY_REQUEST_TYPE,
                              {"requestFid": request_fid, "key": key, "deadline": deadline})
        return encode_key_request(request_fid, self.address, sig, deadline)

    def sign_key_add(self, fid: int, key: bytes, scopes: list[int], ttl: int, nonce: int,
                     deadline: int, key_type: int = 1) -> bytes:
        """The custody authorization for a KEY_ADD."""
        return self.sign_typed("KeyAdd", _KEY_ADD_TYPE, {
            "fid": fid, "key": key, "keyType": key_type, "scopes": list(scopes),
            "ttl": ttl, "nonce": nonce, "deadline": deadline,
        })


    def sign_key_remove(self, fid: int, key: bytes, nonce: int, deadline: int) -> bytes:
        """The custody authorization for a KEY_REMOVE."""
        return self.sign_typed("KeyRemove", _KEY_REMOVE_TYPE,
                               {"fid": fid, "key": key, "nonce": nonce, "deadline": deadline})


def encode_key_request(request_fid: int, request_signer: str, signature: bytes, deadline: int) -> bytes:
    """abi.encode(SignedKeyRequestMetadata(requestFid, requestSigner, signature, deadline)).

    The inverse of `phunk.decode_key_request`.
    """
    word = lambda n: int(n).to_bytes(32, "big")  # noqa: E731
    addr = bytes.fromhex(request_signer.removeprefix("0x"))
    padded = signature + b"\x00" * (-len(signature) % 32)
    return (word(0x20) + word(request_fid) + b"\x00" * 12 + addr + word(0x80) + word(deadline)
            + word(len(signature)) + padded)


# --------------------------------------------------------------- KEY_ADD


def build_key_add(custody: Custody, fid: int, key: AppKey, *, nonce: int, deadline: int,
                  scopes: list[int] = DEFAULT_SCOPES, ttl: int = DEFAULT_TTL,
                  app_fid: int | None = None, app_custody: Custody | None = None,
                  metadata: bytes | None = None, timestamp: int | None = None) -> bytes:
    """A signed KEY_ADD message, ready to submit.

    `deadline` is Farcaster time. By default the account is its own app
    (`app_fid = fid`). To register the key under a separate app account,
    either pass `app_fid` and `app_custody`, or pass `metadata`: a
    SignedKeyRequest the app already signed (see `request_app_approval`).
    """
    if not 0 < ttl <= MAX_TTL:
        raise ValueError("ttl must be between 1 second and 90 days")
    if proto.KEY_ADD in scopes:
        raise ValueError("KEY_ADD can't be a scope")
    if metadata is None:
        app_fid = fid if app_fid is None else app_fid
        metadata = (app_custody or custody).sign_key_request(app_fid, key.public_key, deadline)
    custody_sig = custody.sign_key_add(fid, key.public_key, scopes, ttl, nonce, deadline)
    body = proto.KeyAddBody(key=key.public_key, custody_signature=custody_sig, deadline=deadline,
                            nonce=nonce, metadata=metadata, scopes=list(scopes), ttl=ttl)
    # The envelope is signed by the new key itself: proof you hold it.
    return key.sign(proto.KEY_ADD, fid, body.encode(), timestamp)


def recover_key_request(req: KeyRequest, key: bytes) -> str:
    """The address that signed a SignedKeyRequest for `key`."""
    typed = {
        "types": {"EIP712Domain": _DOMAIN_TYPE, "SignedKeyRequest": _SIGNED_KEY_REQUEST_TYPE},
        "primaryType": "SignedKeyRequest",
        "domain": KEY_DOMAIN,
        "message": {"requestFid": req.request_fid, "key": key, "deadline": req.deadline},
    }
    return Account.recover_message(encode_typed_data(full_message=typed), signature=req.signature)


def check_key_request(metadata: bytes, key: bytes, *, app_fid: int | None = None,
                      app_custody: str | None = None, at: int | None = None) -> KeyRequest:
    """Check an app's SignedKeyRequest before using it. Raises ValueError.

    It must name `app_fid` (if given), not be expired, carry a valid
    signature over this exact key, and be signed by `app_custody` (if
    given): the app account's current custody address.
    """
    req = decode_key_request(metadata)
    if app_fid is not None and req.request_fid != app_fid:
        raise ValueError(f"approval is for app fid {req.request_fid}, expected {app_fid}")
    if req.deadline <= (now() if at is None else at):
        raise ValueError("approval has expired")
    signer = recover_key_request(req, key)
    if signer.lower() != req.request_signer.lower():
        raise ValueError("approval signature does not match its signer")
    if app_custody is not None and signer.lower() != app_custody.lower():
        raise ValueError(f"approval signed by {signer}, but the app's custody address is {app_custody}")
    return req


async def request_app_approval(hub: Hub, url: str, key: AppKey) -> bytes:
    """Ask an app's signing service to approve `key`; return the checked metadata.

    The service holds the app account's custody key and answers
    `POST {"key": "0x..."}` with `{"metadata": "0x...", "request_fid": n}`.
    Only the public key is sent. The answer is checked against the app
    account's custody address on the node before it is used.
    """
    if hub.session is None:
        raise RuntimeError("use `async with Hub(...) as hub:`")
    try:
        async with hub.session.post(url, json={"key": key.public_hex}) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise NodeError(f"app signer {url}: HTTP {r.status}: {str(data)[:300]}")
    except (OSError, ValueError) as e:
        raise NodeError(f"app signer {url}: {e!r}") from e
    metadata = bytes.fromhex(str(data.get("metadata", "")).removeprefix("0x"))
    app_fid = int(data.get("request_fid") or decode_key_request(metadata).request_fid)
    check_key_request(metadata, key.public_key, app_fid=app_fid, app_custody=await hub.custody_address(app_fid))
    return metadata


async def add_app_key(hub: Hub, custody: Custody, *, fid: int | None = None,
                      scopes: list[int] = DEFAULT_SCOPES, ttl: int = DEFAULT_TTL,
                      key: AppKey | None = None, app_signer: str | None = None) -> tuple[int, AppKey]:
    """Create a gasless app key for the account behind `custody` and submit it.

    With `app_signer` (the URL of an app's signing service), the key is
    registered under that app, so other clients show "Sent from <app>".
    Without it, the account is its own app.

    Returns (fid, key). Save `key.private_hex` somewhere safe if you want to
    keep posting with it; the custody key is not needed again.
    """
    if fid is None:
        fid = await hub.fid_by_address(custody.address)
    key = key or AppKey.generate()
    metadata = await request_app_approval(hub, app_signer, key) if app_signer else None
    nonce = (await hub.signers(fid)).get("currentUserNonce", 0) + 1
    msg = build_key_add(custody, fid, key, nonce=nonce, deadline=now() + 3600, scopes=scopes, ttl=ttl,
                        metadata=metadata)
    await hub.submit(msg)
    return fid, key


# --------------------------------------------------------------- KEY_REMOVE
#
# Revoking a gasless app key. The key stops working at once; casts it already
# signed stay. On-chain keys can't be removed this way: that takes a
# transaction to the Key Registry.

_STORED_NONCE = re.compile(r"not greater than stored nonce (\d+)")


def build_key_remove_self(key: AppKey, fid: int, *, nonce: int, deadline: int,
                          timestamp: int | None = None) -> bytes:
    """A KEY_REMOVE that `key` signs for itself. No custody key needed.

    `nonce` must exceed the app nonce of the app that requested the key
    (`requesterFidNonces` in /v1/signersByFid)."""
    body = proto.KeyRemoveBody(key=key.public_key, signature_type=proto.REMOVE_BY_SELF,
                               deadline=deadline, nonce=nonce)
    return key.sign(proto.KEY_REMOVE, fid, body.encode(), timestamp)


def build_key_remove_custody(custody: Custody, fid: int, key: bytes, *, nonce: int, deadline: int,
                             envelope_key: AppKey | None = None, timestamp: int | None = None) -> bytes:
    """A KEY_REMOVE for any gasless key on the account, authorized by custody.

    `key` is the public key to remove. `nonce` must exceed `currentUserNonce`.
    The node only checks the custody signature, so the envelope can be
    signed by any Ed25519 key (`envelope_key`, a fresh one by default)."""
    sig = custody.sign_key_remove(fid, key, nonce, deadline)
    body = proto.KeyRemoveBody(key=key, signature_type=proto.REMOVE_BY_CUSTODY,
                               deadline=deadline, nonce=nonce, signature=sig)
    return (envelope_key or AppKey.generate()).sign(proto.KEY_REMOVE, fid, body.encode(), timestamp)


async def _submit_with_nonce(hub: Hub, build, nonce: int) -> int:
    """Submit build(nonce). If the node says the nonce is used, retry once
    with the stored nonce + 1 (for nodes that don't report app nonces)."""
    try:
        await hub.submit(build(nonce))
        return nonce
    except NodeError as e:
        m = _STORED_NONCE.search(str(e))
        if not m:
            raise
        nonce = int(m.group(1)) + 1
        await hub.submit(build(nonce))
        return nonce


async def revoke_key(hub: Hub, fid: int, key: AppKey) -> None:
    """Revoke a gasless app key you hold, signed by the key itself.

    No seed phrase needed. Raises NodeError if the node rejects it, for
    example because the key is on-chain or already gone."""
    found = [k for k in await hub.keys(fid) if k.key == key.public_hex and not k.onchain]
    if not found:
        raise ValueError(f"{key.public_hex} is not an active gasless key of fid {fid}")
    app_fid = found[-1].app_fid if found[-1].app_fid is not None else fid
    nonces = (await hub.signers(fid, [app_fid])).get("requesterFidNonces") or {}
    nonce = int(nonces.get(str(app_fid), 0)) + 1
    deadline = now() + 3600
    await _submit_with_nonce(hub, lambda n: build_key_remove_self(key, fid, nonce=n, deadline=deadline), nonce)


async def revoke_key_with_custody(hub: Hub, custody: Custody, key: bytes | str, *,
                                  fid: int | None = None) -> int:
    """Revoke any gasless key on the account behind `custody`. Returns the fid.

    Use this for a key you no longer hold (a lost laptop, an app you stopped
    using). `key` is the public key, bytes or 0x hex."""
    if isinstance(key, str):
        key = bytes.fromhex(key.removeprefix("0x"))
    if fid is None:
        fid = await hub.fid_by_address(custody.address)
    nonce = (await hub.signers(fid)).get("currentUserNonce", 0) + 1
    deadline = now() + 3600
    await _submit_with_nonce(
        hub, lambda n: build_key_remove_custody(custody, fid, key, nonce=n, deadline=deadline), nonce)
    return fid


# --------------------------------------------------------------- writing


_MENTION = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9.-]{0,30}[a-z0-9]|[a-z0-9])", re.IGNORECASE)


def _hash_bytes(h: str | bytes) -> bytes:
    return h if isinstance(h, bytes) else bytes.fromhex(h.removeprefix("0x"))


class Writer:
    """Post as one account with one app key.

        async with Hub("http://localhost:3381") as hub:
            me = Writer(hub, fid, AppKey.from_hex(os.environ["PHUNK_APP_KEY"]))
            cast_hash = await me.cast("hello from my own node")
            await me.like(3, "0x...")
            await me.follow(3)

    Each method submits one message and returns its hash ("0x...").
    """

    def __init__(self, hub: Hub, fid: int, key: AppKey):
        self.hub = hub
        self.fid = fid
        self.key = key

    async def _send(self, type: int, body) -> str:
        msg = self.key.sign(type, self.fid, body.encode())
        res = await self.hub.submit(msg)
        return res.get("hash") or "0x" + message_hash(proto_data(msg)).hex()

    async def mentions(self, text: str) -> tuple[str, list[int], list[int]]:
        """Turn "@name" in `text` into Farcaster mentions.

        Farcaster stores mentions outside the text: the "@name" is cut out
        and its fid saved with the byte offset where it was. Names that don't
        resolve stay as plain text.
        """
        out, fids, positions, last = "", [], [], 0
        for m in _MENTION.finditer(text):
            try:
                fid = await self.hub.fid_by_name(m.group(1))
            except Exception:
                continue
            out += text[last:m.start()]
            fids.append(fid)
            positions.append(len(out.encode()))
            last = m.end()
        return out + text[last:], fids, positions

    async def cast(self, text: str, *, reply_to: tuple[int, str] | None = None,
                   channel: str | None = None, embeds: list[str | tuple[int, str]] = (),
                   resolve_mentions: bool = True) -> str:
        """Post a cast.

        `reply_to` is (fid, hash) of the parent cast. `channel` is a channel
        URL such as "https://warpcast.com/~/channel/politics". `embeds` are URLs
        or (fid, hash) quotes, at most 2.
        """
        mentions, positions = [], []
        if resolve_mentions and "@" in text:
            text, mentions, positions = await self.mentions(text)
        if len(text.encode()) > 1024:
            raise ValueError("cast text is over 1024 bytes")
        body = proto.CastAddBody(
            text=text, mentions=mentions, mentions_positions=positions,
            embeds=[proto.Embed(url=e) if isinstance(e, str) else proto.Embed(cast_id=proto.CastId(e[0], _hash_bytes(e[1])))
                    for e in embeds],
            parent_cast_id=proto.CastId(reply_to[0], _hash_bytes(reply_to[1])) if reply_to else None,
            parent_url=channel if not reply_to else None,
        )
        return await self._send(proto.CAST_ADD, body)

    async def delete_cast(self, hash: str) -> str:
        return await self._send(proto.CAST_REMOVE, proto.CastRemoveBody(_hash_bytes(hash)))

    async def _react(self, type: int, kind: int, fid: int, hash: str) -> str:
        return await self._send(type, proto.ReactionBody(kind, proto.CastId(fid, _hash_bytes(hash))))

    async def like(self, fid: int, hash: str) -> str:
        return await self._react(proto.REACTION_ADD, proto.LIKE, fid, hash)

    async def unlike(self, fid: int, hash: str) -> str:
        return await self._react(proto.REACTION_REMOVE, proto.LIKE, fid, hash)

    async def recast(self, fid: int, hash: str) -> str:
        return await self._react(proto.REACTION_ADD, proto.RECAST, fid, hash)

    async def unrecast(self, fid: int, hash: str) -> str:
        return await self._react(proto.REACTION_REMOVE, proto.RECAST, fid, hash)

    async def follow(self, fid: int) -> str:
        return await self._send(proto.LINK_ADD, proto.LinkBody(fid))

    async def unfollow(self, fid: int) -> str:
        return await self._send(proto.LINK_REMOVE, proto.LinkBody(fid))

    async def set_profile(self, *, display: str | None = None, bio: str | None = None,
                          pfp: str | None = None, url: str | None = None,
                          username: str | None = None) -> list[str]:
        """Update profile fields. Each field is its own message.

        `username` must be a name this fid already owns (an fname from
        fnames.farcaster.xyz, or an ENS name it verified); the node checks."""
        fields = [(proto.USER_DATA_DISPLAY, display), (proto.USER_DATA_BIO, bio),
                  (proto.USER_DATA_PFP, pfp), (proto.USER_DATA_URL, url),
                  (proto.USER_DATA_USERNAME, username)]
        return [await self._send(proto.USER_DATA_ADD, proto.UserDataBody(t, v)) for t, v in fields if v is not None]


def proto_data(msg: bytes) -> bytes:
    """The MessageData bytes inside an encoded Message (its field 1)."""
    i = 0
    while i < len(msg):
        tag, i = _read_varint(msg, i)
        if tag & 7 == 2:
            n, i = _read_varint(msg, i)
            if tag >> 3 == 1:
                return msg[i:i + n]
            i += n
        else:
            _, i = _read_varint(msg, i)
    raise ValueError("no data in message")


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i
