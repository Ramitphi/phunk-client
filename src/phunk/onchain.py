"""Read a few facts from Base over JSON-RPC, with aiohttp only.

Used for one small badge in the people feed: who holds the "I voted for
Network School Farcaster" NFT (ERC-721 on Base). Holding one means the
account voted for it.

    async with aiohttp.ClientSession() as s:
        voted = await holders(s, ["0x187c7b0393ebe86378128f2653d0930e33218899"])
"""
from __future__ import annotations

import os

import aiohttp

BASE_RPC = "https://mainnet.base.org"
# "Network School Farcaster client" (symbol VOTE). Token metadata: "I voted
# for Network School Farcaster."
NS_VOTE = "0x4ecfed276c566aeb49da1f3a888e8dbb7263028d"
BALANCE_OF = "0x70a08231"  # balanceOf(address)
# Multicall3, at the same address on every EVM chain. One eth_call checks
# many wallets: public RPCs count each call in a JSON-RPC batch against their
# rate limit (mainnet.base.org: 25 a second), but a multicall is one.
MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
AGGREGATE3 = "0x82ad56cb"  # aggregate3((address,bool,bytes)[])
CHUNK = 200


def base_rpc_from_env() -> str | None:
    """PHUNK_BASE_RPC: the Base JSON-RPC URL. Unset: the public one. "off": no badge."""
    value = os.environ.get("PHUNK_BASE_RPC", "").strip()
    if value.lower() == "off":
        return None
    return value or BASE_RPC


def _is_address(a: str) -> bool:
    return isinstance(a, str) and len(a) == 42 and a.startswith("0x") and all(
        c in "0123456789abcdefABCDEF" for c in a[2:])


def _word(n: int) -> str:
    return f"{n:064x}"


def encode_aggregate3(contract: str, addresses: list[str]) -> str:
    """Calldata for Multicall3.aggregate3: `balanceOf(a)` on `contract` for each
    address, each allowed to fail."""
    head, tail = [], []
    size = 32 * 4 + 64  # target, allowFailure, offset, length, 36 bytes of calldata padded to 64
    for i, a in enumerate(addresses):
        head.append(_word(32 * len(addresses) + i * size))
        data = BALANCE_OF[2:] + a[2:].rjust(64, "0")
        tail.append(contract[2:].rjust(64, "0") + _word(1) + _word(0x60) + _word(36) + data.ljust(128, "0"))
    return AGGREGATE3 + _word(0x20) + _word(len(addresses)) + "".join(head) + "".join(tail)


def decode_aggregate3(result: str) -> list[int]:
    """aggregate3's (bool success, bytes returnData)[] -> each balance (0 on failure)."""
    raw = bytes.fromhex(result[2:] if result.startswith("0x") else result)

    def word(at: int) -> int:
        return int.from_bytes(raw[at:at + 32], "big")

    base = word(0)
    n = word(base)
    out = []
    for i in range(n):
        item = base + 32 + word(base + 32 + 32 * i)
        ok, data_at = word(item), item + word(item + 32)
        length = word(data_at)
        out.append(int.from_bytes(raw[data_at + 32:data_at + 32 + length], "big") if ok and length else 0)
    return out


async def holders(session: aiohttp.ClientSession, addresses, *, contract: str = NS_VOTE,
                  rpc: str = BASE_RPC, timeout: float = 15.0) -> set[str]:
    """The addresses (lowercase) that hold at least one token of `contract`.

    One `eth_call` to Multicall3 per 200 addresses. Raises on a network or
    RPC error, so a caller can tell "no" from "couldn't check"."""
    addrs = list(dict.fromkeys(a.lower() for a in addresses if _is_address(a)))
    out: set[str] = set()
    for i in range(0, len(addrs), CHUNK):
        chunk = addrs[i:i + CHUNK]
        body = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": MULTICALL3, "data": encode_aggregate3(contract.lower(), chunk)}, "latest"]}
        async with session.post(rpc, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            r.raise_for_status()
            reply = await r.json(content_type=None)
        if not isinstance(reply, dict) or "error" in reply or "result" not in reply:
            raise ValueError(f"RPC error: {str(reply)[:200]}")
        balances = decode_aggregate3(reply["result"])
        if len(balances) != len(chunk):
            raise ValueError("multicall returned the wrong number of results")
        out.update(a for a, n in zip(chunk, balances) if n > 0)
    return out
