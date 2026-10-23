---
title: Snapchain messages
summary: Every cast, like, follow and profile edit is a small signed message. Here is what one contains and where it goes.
order: 4
---

Everything a person does on Farcaster is a **message**: a small, signed piece of data. A cast is a message. So is a like, a follow, a profile field, and an unfollow.

## Anatomy

Here is dwr's latest cast, as the node returns it:

```sh
curl -s "$NODE/v1/castsByFid?fid=3&pageSize=1&reverse=true"
```

```json
{
  "data": {
    "type": "MESSAGE_TYPE_CAST_ADD",
    "fid": 3,
    "timestamp": 182745002,
    "network": "FARCASTER_NETWORK_MAINNET",
    "castAddBody": {"text": "...", "embeds": [...], "mentions": [], ...}
  },
  "hash": "0x0256ae66308d546514fa666cda44052abbfea066",
  "hashScheme": "HASH_SCHEME_BLAKE3",
  "signature": "S5E2Ixa8C0ny...Dg==",
  "signatureScheme": "SIGNATURE_SCHEME_ED25519",
  "signer": "0xc887f5bf385a4718eaee166481f1832198938cf33e98a82dc81a0b4b81ffe33d"
}
```

- `data` is the content: who, when, what kind, and a body for that kind.
- `hash` is a BLAKE3 hash of `data`, cut to 20 bytes. It is the message's ID. Replies and likes point at a cast by its author's FID and this hash.
- `signature` is an Ed25519 signature over the hash.
- `signer` is the public key that made the signature. It must be one of the account's [app keys](app-keys.md).

On the wire, messages are protobufs. The HTTP API shows them as JSON, with hashes and keys in hex and signatures in base64.

## Self-authenticating

Anyone can check a message without trusting the node that served it. Follow the chain: the message is signed by `signer`, that key was approved by the account's [custody address](custody.md), and the custody address owns the [FID](fids.md) on-chain.

## Kinds

| Kind | Types | Example |
|---|---|---|
| Casts | `CAST_ADD`, `CAST_REMOVE` | "gm" |
| Reactions | `REACTION_ADD`, `REACTION_REMOVE` | a like or recast |
| Links | `LINK_ADD`, `LINK_REMOVE` | a follow |
| Profile | `USER_DATA_ADD` | display name, bio, pfp |
| Verifications | `VERIFICATION_ADD_ETH_ADDRESS`, `VERIFICATION_REMOVE` | proof of owning an address |
| Usernames | `USERNAME_PROOF` | an ENS name |
| Keys | `KEY_ADD`, `KEY_REMOVE` | approving a gasless app key |

Each `_REMOVE` undoes its `_ADD`. Deleting a cast leaves a small tombstone in its place, and that tombstone still counts against the account's [storage](storage.md) until newer messages push it out.

## Timestamps

`timestamp` counts seconds from the **Farcaster epoch**, 2021-01-01 00:00 UTC. Add 1609459200 to get Unix time. A recent epoch keeps the numbers, and so the messages, small.

The author sets the timestamp, and nobody checks it. It can be backdated, like a blog post. It cannot be more than 10 minutes in the future; the network rejects those.

On-chain events (registrations, key adds on the old path, storage rent) use the chain's block time, which is ordinary Unix time.

```python
from phunk import to_datetime
to_datetime(182745002)            # what cast.time gives you: a UTC datetime
```

## Where a message goes

Snapchain splits accounts across **shards**. Each shard is its own chain of blocks. Today there are two message shards plus shard 0, which ties them together.

- Casts, reactions, links and profile data go to a shard picked from the FID: the first four bytes of the SHA-256 of the FID, modulo the number of message shards, plus one. All of one account's messages live on the same shard.
- Messages every shard needs to know about go to shard 0: `KEY_ADD`, `KEY_REMOVE`, storage lends, and a few others.

This is why queries about one account are simple, and queries across accounts (a channel feed, someone's followers) return one page per shard glued together. [Step 4 of the tutorial](../tutorial.html#4-paging-and-the-two-traps) shows how to page those safely.

To publish, a client posts the protobuf bytes to `/v1/submitMessage` on any node. [Read nodes and validators](nodes.md) covers what happens next.
