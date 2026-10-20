"""The app's signing service: approve app keys so they count as "Sent from <app>".

Clients show which app a cast came from by reading the key that signed it.
Each key's KEY_ADD carries a SignedKeyRequest: the app account's custody key
signing (app fid, new public key, deadline). This service holds that custody
key and signs requests for any public key it is sent. The user's own custody
key still has to authorize the KEY_ADD, so an approval alone can't add a key
to anyone's account.

    POST /api/sign_key_request   {"key": "0x<32-byte Ed25519 public key>"}
      -> {"metadata": "0x...", "request_fid": 123, "request_signer": "0x...", "deadline": 190000000}
    GET  /api/sign_key_request
      -> {"request_fid": 123, "request_signer": "0x..."}

Settings (environment variables, never in code):
    PHUNK_APP_FID            the app account's fid
    PHUNK_APP_CUSTODY_KEY    its custody private key (0x...), or
    PHUNK_APP_MNEMONIC       its seed phrase (custody key on m/44'/60'/0'/0/0)

Deploys as a Vercel Python function (see signer/README.md). Needs only
eth-account.
"""
from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler

from eth_account import Account
from eth_account.messages import encode_typed_data

FARCASTER_EPOCH = 1609459200
TTL = 3600  # an approval is valid for an hour
KEY_DOMAIN = {"name": "Farcaster KeyAdd", "version": "1", "chainId": 1}
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
KEY_HEX = re.compile(r"^0x[0-9a-fA-F]{64}$")


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


def approve(key_hex: str, app_fid: int, account, now: float | None = None) -> dict:
    """Sign a SignedKeyRequest for one public key. Pure, apart from the clock."""
    if not isinstance(key_hex, str) or not KEY_HEX.match(key_hex):
        raise ValueError("key must be a 0x-prefixed 32-byte Ed25519 public key")
    key = bytes.fromhex(key_hex[2:])
    deadline = int(time.time() if now is None else now) - FARCASTER_EPOCH + TTL
    typed = {"types": TYPES, "primaryType": "SignedKeyRequest", "domain": KEY_DOMAIN,
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


class handler(BaseHTTPRequestHandler):  # Vercel's Python runtime looks for this name
    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802
        self._send(204, {})

    def do_GET(self):  # noqa: N802
        try:
            self._send(200, {"request_fid": int(os.environ["PHUNK_APP_FID"]),
                             "request_signer": load_account().address})
        except (KeyError, RuntimeError, ValueError):
            self._send(500, {"error": "signer is not configured"})

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1024:
                return self._send(413, {"error": "body too large"})
            data = json.loads(self.rfile.read(length) or b"{}")
            app_fid = int(os.environ["PHUNK_APP_FID"])
            account = load_account()
        except (KeyError, RuntimeError):
            return self._send(500, {"error": "signer is not configured"})
        except ValueError:
            return self._send(400, {"error": "send JSON: {\"key\": \"0x...\"}"})
        try:
            return self._send(200, approve(data.get("key") if isinstance(data, dict) else None, app_fid, account))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
