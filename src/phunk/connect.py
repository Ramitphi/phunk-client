"""Connect: follow and like from the mini app with a key the person approves once.

1. The page makes an Ed25519 key in the browser. It never leaves the browser.
2. `start()` asks the app's signing service (signer/) to sign a
   SignedKeyRequest for that public key, then opens a signed key request with
   Farcaster's API. The answer carries a deeplink.
3. The page opens the approval link. The person approves the key in their
   Farcaster app, which adds it to their account.
4. `status()` says when that is done, and for which fid.
5. The page signs follows and likes with the key. `check()` lets only those
   through the relay (`POST /api/submit`) to the node, which checks the
   signature and that the key belongs to the fid.

No seed phrase and no custody key: the person's own Farcaster app approves.
Other clients show these follows and likes as "Sent from <app account>".

    POST {farcaster}/v2/signed-key-requests  {key, requestFid, signature, deadline}
      -> {"result": {"signedKeyRequest": {"token", "deeplinkUrl", "state", ...}}}
    GET  {farcaster}/v2/signed-key-request?token=...
      -> {"result": {"signedKeyRequest": {"state": "pending" | "approved" | "completed", "userFid"?}}}
"""
from __future__ import annotations

import re
import time

import aiohttp

from . import proto
from .keys import decode_key_request

FARCASTER_API = "https://api.farcaster.xyz"
# The https form of the approval deeplink: the Farcaster app opens it on a
# phone, and mini app hosts open https links more reliably than farcaster://.
APPROVE_URL = "https://client.farcaster.xyz/deeplinks/signed-key-request?token="
KEY_HEX = re.compile(r"^0x[0-9a-fA-F]{64}$")
TOKEN = re.compile(r"^0x[0-9a-fA-F]{8,128}$")
UNIX_2020 = 1577836800
MAX_MESSAGE = 1024  # a follow or a like is ~150 bytes
TIMEOUT = aiohttp.ClientTimeout(total=20)


class ConnectError(Exception):
    """Something the page should show, with the HTTP status to answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def _json(resp: aiohttp.ClientResponse) -> dict:
    try:
        data = await resp.json(content_type=None)
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _farcaster_error(data: dict) -> str:
    errors = data.get("errors") or []
    if errors and isinstance(errors[0], dict) and errors[0].get("message"):
        return str(errors[0]["message"])[:200]
    return "Farcaster didn't accept the request"


async def start(session: aiohttp.ClientSession, signer_url: str, key: str,
                api: str = FARCASTER_API) -> dict:
    """Open a signed key request for `key` (0x + 64 hex) under the app account.

    Returns {"token", "deeplink", "url", "app_fid"}. The page opens `url`
    (the same approval as `deeplink`, as an https link)."""
    if not isinstance(key, str) or not KEY_HEX.match(key):
        raise ConnectError("key must be a 0x-prefixed 32-byte Ed25519 public key")
    try:
        # onchain: the Farcaster app adds the key on Optimism, which wants a
        # Unix deadline and the SignedKeyRequestValidator's signature domain.
        async with session.post(signer_url, json={"key": key, "onchain": True}, timeout=TIMEOUT) as r:
            approval = await _json(r)
            if r.status != 200 or not approval.get("metadata"):
                raise ConnectError("the phunk signer is not answering; try again soon", 502)
    except aiohttp.ClientError as e:
        raise ConnectError("the phunk signer is not answering; try again soon", 502) from e
    try:
        req = decode_key_request(bytes.fromhex(str(approval["metadata"]).removeprefix("0x")))
    except ValueError as e:
        raise ConnectError("the phunk signer sent something odd; try again soon", 502) from e
    if req.deadline < UNIX_2020:  # a Farcaster-time deadline: the signer predates onchain mode
        raise ConnectError("the phunk signer needs an update; try again soon", 502)
    body = {"key": key, "requestFid": req.request_fid, "signature": "0x" + req.signature.hex(),
            "deadline": req.deadline}
    try:
        async with session.post(f"{api}/v2/signed-key-requests", json=body, timeout=TIMEOUT) as r:
            data = await _json(r)
            if r.status != 200:
                raise ConnectError(_farcaster_error(data), 502)
    except aiohttp.ClientError as e:
        raise ConnectError("Farcaster is not answering; try again soon", 502) from e
    skr = (data.get("result") or {}).get("signedKeyRequest") or {}
    token, deeplink = skr.get("token"), skr.get("deeplinkUrl")
    if not (isinstance(token, str) and TOKEN.match(token) and isinstance(deeplink, str)):
        raise ConnectError("Farcaster sent an answer phunk doesn't understand", 502)
    return {"token": token, "deeplink": deeplink, "url": APPROVE_URL + token,
            "app_fid": req.request_fid}


async def status(session: aiohttp.ClientSession, token: str, api: str = FARCASTER_API) -> dict:
    """Where a signed key request stands: {"state", "fid"}; fid once approved."""
    if not isinstance(token, str) or not TOKEN.match(token):
        raise ConnectError("bad token")
    try:
        async with session.get(f"{api}/v2/signed-key-request", params={"token": token},
                               timeout=TIMEOUT) as r:
            data = await _json(r)
            if r.status != 200:
                raise ConnectError(_farcaster_error(data), 404 if r.status in (400, 404) else 502)
    except aiohttp.ClientError as e:
        raise ConnectError("Farcaster is not answering; try again soon", 502) from e
    skr = (data.get("result") or {}).get("signedKeyRequest") or {}
    fid = skr.get("userFid")
    return {"state": str(skr.get("state") or "pending"), "fid": int(fid) if fid else None}


# ------------------------------------------------------------- the relay

def _fields(buf: bytes) -> dict[int, list]:
    """A tiny protobuf reader: {field number: [values]} (varints and bytes)."""
    out: dict[int, list] = {}
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        num, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise ValueError("truncated")
            value, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"wire type {wire}")
        out.setdefault(num, []).append(value)
    return out


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("bad varint")
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def check(message: bytes) -> tuple[int, int]:
    """Let only follows, unfollows, likes and unlikes through. Returns (type, fid).

    Raises ConnectError otherwise. The node does the real checks (signature,
    key, fid); this keeps the relay from carrying anything else."""
    if not message or len(message) > MAX_MESSAGE:
        raise ConnectError("not a follow or a like")
    try:
        msg = _fields(message)
        data = (msg.get(7) or msg.get(1) or [b""])[0]
        md = _fields(data)
        kind = md.get(1, [0])[0]
        fid = md.get(2, [0])[0]
        if kind in (proto.REACTION_ADD, proto.REACTION_REMOVE):
            body = _fields(md.get(7, [b""])[0])
            ok = body.get(1, [0])[0] == proto.LIKE
        elif kind in (proto.LINK_ADD, proto.LINK_REMOVE):
            body = _fields(md.get(14, [b""])[0])
            ok = body.get(1, [b""])[0] == b"follow"
        else:
            ok = False
    except (ValueError, IndexError, TypeError) as e:
        raise ConnectError("not a Farcaster message") from e
    if not ok or not fid:
        raise ConnectError("phunk only relays follows and likes")
    return kind, fid


class RateLimit:
    """At most `n` calls per `per` seconds per key (an IP). Per instance."""

    def __init__(self, n: int, per: float):
        self.n, self.per, self.seen = n, per, {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        recent = [t for t in self.seen.get(key, ()) if now - t < self.per]
        if len(recent) >= self.n:
            self.seen[key] = recent
            return False
        recent.append(now)
        self.seen[key] = recent
        if len(self.seen) > 10000:  # don't grow without end
            self.seen = {k: v for k, v in self.seen.items() if v and now - v[-1] < self.per}
        return True
