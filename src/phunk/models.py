"""Plain dataclasses for what the node returns. Each keeps the original
message in `raw`, so nothing the node sends is lost."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .timeutil import to_datetime

USER_DATA_FIELDS = {
    "USER_DATA_TYPE_USERNAME": "username",
    "USER_DATA_TYPE_DISPLAY": "display_name",
    "USER_DATA_TYPE_PFP": "pfp",
    "USER_DATA_TYPE_BIO": "bio",
    "USER_DATA_TYPE_URL": "url",
    "USER_DATA_TYPE_LOCATION": "location",
    "USER_DATA_TYPE_TWITTER": "twitter",
    "USER_DATA_TYPE_GITHUB": "github",
    "USER_DATA_TYPE_BANNER": "banner",
    "USER_DATA_PRIMARY_ADDRESS_ETHEREUM": "eth_address",
    "USER_DATA_PRIMARY_ADDRESS_SOLANA": "sol_address",
}


@dataclass
class User:
    fid: int
    username: str | None = None
    display_name: str | None = None
    pfp: str | None = None
    bio: str | None = None
    url: str | None = None
    location: str | None = None
    twitter: str | None = None
    github: str | None = None
    banner: str | None = None
    eth_address: str | None = None
    sol_address: str | None = None
    raw: list[dict] = field(default_factory=list, repr=False)

    @classmethod
    def from_messages(cls, fid: int, messages: list[dict]) -> "User":
        u = cls(fid=fid, raw=messages)
        for m in messages:
            body = m["data"].get("userDataBody") or {}
            name = USER_DATA_FIELDS.get(body.get("type"))
            if name:
                setattr(u, name, body.get("value"))
        return u


@dataclass
class Cast:
    hash: str
    fid: int
    timestamp: int  # Farcaster time
    text: str
    signer: str
    parent_fid: int | None = None
    parent_hash: str | None = None
    parent_url: str | None = None  # the channel, for top-level channel casts
    mentions: list[int] = field(default_factory=list)
    embeds: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def time(self) -> dt.datetime:
        return to_datetime(self.timestamp)

    @property
    def is_reply(self) -> bool:
        return self.parent_hash is not None

    @classmethod
    def from_message(cls, m: dict) -> "Cast":
        d = m["data"]
        b = d.get("castAddBody") or {}
        parent = b.get("parentCastId") or {}
        embeds = []
        for e in b.get("embeds") or []:
            if e.get("url"):
                embeds.append(e["url"])
            elif e.get("castId"):
                embeds.append(f"cast:{e['castId'].get('fid')}:{e['castId'].get('hash')}")
        return cls(
            hash=m["hash"],
            fid=d["fid"],
            timestamp=d["timestamp"],
            text=b.get("text") or "",
            signer=m.get("signer", ""),
            parent_fid=parent.get("fid"),
            parent_hash=parent.get("hash"),
            parent_url=b.get("parentUrl"),
            mentions=list(b.get("mentions") or []),
            embeds=embeds,
            raw=m,
        )


@dataclass
class Reaction:
    hash: str
    fid: int
    kind: str  # "like" or "recast"
    timestamp: int
    signer: str
    target_fid: int | None = None
    target_hash: str | None = None
    target_url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def time(self) -> dt.datetime:
        return to_datetime(self.timestamp)

    @classmethod
    def from_message(cls, m: dict) -> "Reaction":
        d = m["data"]
        b = d.get("reactionBody") or {}
        t = b.get("targetCastId") or {}
        return cls(
            hash=m["hash"],
            fid=d["fid"],
            kind=(b.get("type") or "").replace("REACTION_TYPE_", "").lower(),
            timestamp=d["timestamp"],
            signer=m.get("signer", ""),
            target_fid=t.get("fid"),
            target_hash=t.get("hash"),
            target_url=b.get("targetUrl"),
            raw=m,
        )


@dataclass
class Follow:
    hash: str
    fid: int  # who follows
    target_fid: int  # whom
    timestamp: int
    signer: str
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def time(self) -> dt.datetime:
        return to_datetime(self.timestamp)

    @classmethod
    def from_message(cls, m: dict) -> "Follow":
        d = m["data"]
        return cls(
            hash=m["hash"],
            fid=d["fid"],
            target_fid=(d.get("linkBody") or {}).get("targetFid"),
            timestamp=d["timestamp"],
            signer=m.get("signer", ""),
            raw=m,
        )


@dataclass
class Key:
    """One key an account added (or removed).

    `app_fid` is the account that asked for the key: the app. It is None
    when the event carries no metadata (REMOVE events do not). When an
    account requested its own key, `app_fid == fid`.
    """

    fid: int
    key: str
    event: str  # "add", "remove" or "admin_reset"
    app_fid: int | None
    timestamp: int  # Unix time (block time for on-chain keys)
    onchain: bool
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def time(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.timestamp, tz=dt.timezone.utc)

    @property
    def self_requested(self) -> bool:
        return self.app_fid == self.fid
