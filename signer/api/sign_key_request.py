"""The app's signing service: approve app keys so they count as "Sent from <app>".

Clients show which app a cast came from by reading the key that signed it.
Each key's KEY_ADD carries a SignedKeyRequest: the app account's custody key
signing (app fid, new public key, deadline). This service holds that custody
key and signs requests for any public key it is sent. The user's own custody
key still has to authorize the KEY_ADD, so an approval alone can't add a key
to anyone's account.

    POST /api/sign-key-request   {"key": "0x<32-byte Ed25519 public key>"}
      -> {"metadata": "0x...", "request_fid": 123, "request_signer": "0x...", "deadline": 190000000}
    POST /api/sign-key-request   {"key": "0x...", "onchain": true}
      -> the same, for a key added on chain (the Farcaster app's approval
         screen): signed for the SignedKeyRequestValidator on Optimism, with a
         Unix deadline
    GET  /api/sign-key-request
      -> {"request_fid": 123, "request_signer": "0x..."}

Settings (environment variables, never in code):
    PHUNK_APP_FID            the app account's fid
    PHUNK_APP_CUSTODY_KEY    its custody private key (0x...), or
    PHUNK_APP_MNEMONIC       its seed phrase (custody key on m/44'/60'/0'/0/0)

An ASGI app (`app`, named in signer/pyproject.toml). Deploys as its own
Vercel project (Root Directory `signer`), apart from phunk.social, so the
custody key sits in a project of its own. See signer/README.md. Needs only
eth-account.
"""
from __future__ import annotations

import json
import os
import re
import time

from eth_account import Account
from eth_account.messages import encode_typed_data

FARCASTER_EPOCH = 1609459200
TTL = 3600  # an approval is valid for an hour
KEY_DOMAIN = {"name": "Farcaster KeyAdd", "version": "1", "chainId": 1}
# The Farcaster app adds approved keys on Optimism (KeyGateway), whose
# SignedKeyRequestValidator checks this domain and a Unix deadline. Same
# values as @farcaster/core's SIGNED_KEY_REQUEST_VALIDATOR_EIP_712_DOMAIN.
ONCHAIN_DOMAIN = {"name": "Farcaster SignedKeyRequestValidator", "version": "1", "chainId": 10,
                  "verifyingContract": "0x00000000FC700472606ED4fA22623Acf62c60553"}
TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "SignedKeyRequest": [
        {"name": "requestFid", "type": "uint256"},
        {"name": "key", "type": "bytes"},
        {"name": "deadline", "type": "uint256"},
    ],
}
ONCHAIN_TYPES = {
    "EIP712Domain": TYPES["EIP712Domain"] + [{"name": "verifyingContract", "type": "address"}],
    "SignedKeyRequest": TYPES["SignedKeyRequest"],
}
KEY_HEX = re.compile(r"^0x[0-9a-fA-F]{64}$")
PATHS = {"/api/sign-key-request", "/api/sign_key_request"}
MAX_BODY = 1024


def load_account():
    """The app's custody account, from the environment."""
    key = os.environ.get("PHUNK_APP_CUSTODY_KEY")
    if key:
        return Account.from_key(key)
    phrase = os.environ.get("PHUNK_APP_MNEMONIC")
    if phrase:
        Account.enable_unaudited_hdwallet_features()
        return Account.from_mnemonic(" ".join(phrase.split()))
    raise RuntimeError("set PHUNK_APP_CUSTODY_KEY or PHUNK_APP_MNEMONIC")


def approve(key_hex: str, app_fid: int, account, now: float | None = None, onchain: bool = False) -> dict:
    """Sign a SignedKeyRequest for one public key. Pure, apart from the clock.

    Default: for a gasless KEY_ADD message (Farcaster-time deadline).
    onchain: for a key added on Optimism, as the Farcaster app does (Unix deadline)."""
    if not isinstance(key_hex, str) or not KEY_HEX.match(key_hex):
        raise ValueError("key must be a 0x-prefixed 32-byte Ed25519 public key")
    key = bytes.fromhex(key_hex[2:])
    unix = int(time.time() if now is None else now)
    deadline = unix + TTL if onchain else unix - FARCASTER_EPOCH + TTL
    types, domain = (ONCHAIN_TYPES, ONCHAIN_DOMAIN) if onchain else (TYPES, KEY_DOMAIN)
    typed = {"types": types, "primaryType": "SignedKeyRequest", "domain": domain,
             "message": {"requestFid": app_fid, "key": key, "deadline": deadline}}
    signature = bytes(account.sign_message(encode_typed_data(full_message=typed)).signature)
    return {
        "metadata": "0x" + encode_metadata(app_fid, account.address, signature, deadline).hex(),
        "request_fid": app_fid,
        "request_signer": account.address,
        "deadline": deadline,
    }


def encode_metadata(request_fid: int, request_signer: str, signature: bytes, deadline: int) -> bytes:
    """abi.encode(SignedKeyRequestMetadata(requestFid, requestSigner, signature, deadline))."""
    word = lambda n: int(n).to_bytes(32, "big")  # noqa: E731
    addr = bytes.fromhex(request_signer.removeprefix("0x"))
    padded = signature + b"\x00" * (-len(signature) % 32)
    return word(0x20) + word(request_fid) + b"\x00" * 12 + addr + word(0x80) + word(deadline) + word(len(signature)) + padded


def info() -> tuple[int, dict]:
    """GET: which app this signer approves keys for."""
    try:
        return 200, {"request_fid": int(os.environ["PHUNK_APP_FID"]), "request_signer": load_account().address}
    except (KeyError, RuntimeError, ValueError):
        return 500, {"error": "signer is not configured"}


def sign(raw: bytes) -> tuple[int, dict]:
    """POST: approve the public key in the JSON body."""
    if len(raw) > MAX_BODY:
        return 413, {"error": "body too large"}
    try:
        data = json.loads(raw or b"{}")
        app_fid = int(os.environ["PHUNK_APP_FID"])
        account = load_account()
    except (KeyError, RuntimeError):
        return 500, {"error": "signer is not configured"}
    except ValueError:
        return 400, {"error": "send JSON: {\"key\": \"0x...\"}"}
    try:
        if not isinstance(data, dict):
            data = {}
        return 200, approve(data.get("key"), app_fid, account, onchain=data.get("onchain") is True)
    except ValueError as e:
        return 400, {"error": str(e)}


HEADERS = [(b"content-type", b"application/json"), (b"access-control-allow-origin", b"*"),
           (b"access-control-allow-headers", b"Content-Type"), (b"cache-control", b"no-store")]


async def app(scope, receive, send):
    """ASGI entrypoint."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    method, path = scope["method"], scope["path"].rstrip("/")
    if path not in PATHS:
        status, body = 404, {"error": "not found"}
    elif method == "OPTIONS":
        status, body = 204, None
    elif method == "GET":
        status, body = info()
    elif method == "POST":
        raw, more = b"", True
        while more and len(raw) <= MAX_BODY:
            message = await receive()
            raw += message.get("body", b"")
            more = message.get("more_body", False)
        status, body = sign(raw)
    else:
        status, body = 405, {"error": "use GET or POST"}
    out = b"" if body is None else json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": HEADERS + [(b"content-length", str(len(out)).encode())]})
    await send({"type": "http.response.body", "body": out})
