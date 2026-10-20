---
title: Storage rent
summary: Accounts rent storage by the year. When it fills up, the oldest messages fall off.
order: 6
---

Nodes store every message for every account. To keep that bounded and to make spam cost something, each account pays **rent** for its storage. It is a yearly fee, not a fee per post.

## How it works

- An account rents **storage units** from the StorageRegistry contract on OP Mainnet. The price is set in US dollars and paid in ETH. A unit lasts one year.
- Each unit adds a fixed number of slots per kind of message: casts, reactions, links, profile fields, verifications and username proofs.
- An account needs at least one unit before the network accepts its messages. Signing up through an app usually includes one.

## When it fills up

Nothing breaks. When an account goes over its limit for a kind, the node drops that kind's **oldest** message to make room. A new cast pushes out the oldest cast. This is called pruning.

For a timeline that's usually fine. It is less fine for follows, since pruning a very old follow silently unfollows someone. People who want to keep more can rent more units.

Deleted casts leave a tombstone that still takes a slot until it is pushed out.

## When it expires

If an account lets all its storage expire, it has a 30-day grace period to renew. After that it can lose its messages.

## Reading it from the node

The exact slots per unit have changed over time, and the node tracks units by the year they were bought. Ask the node rather than hard-coding numbers:

```sh
curl -s "$NODE/v1/storageLimitsByFid?fid=3"
```

```json
{"limits": [
   {"name": "CASTS",     "limit": 108000, "used": 51340, ...},
   {"name": "LINKS",     "limit": 54000,  "used": 4660,  ...},
   {"name": "REACTIONS", "limit": 54000,  "used": 53162, ...},
   {"name": "USER_DATA", "limit": 1500,   "used": 10,    ...},
   ...
 ],
 "units": 30,
 "unitDetails": [{"unitType": "UnitTypeLegacy", "unitSize": 16},
                 {"unitType": "UnitType2024",   "unitSize": 14},
                 {"unitType": "UnitType2025",   "unitSize": 0}]}
```

When we checked, dwr had 30 units and was close to the reaction limit. Past it, each new like pushes out an old one.

## What this means for readers

A node holds what is currently stored, not everything that was ever posted. If an old cast is missing, it may have been pruned, deleted, or signed by a key that was later removed. Archive what you need yourself.

Source: [Messages](https://docs.farcaster.xyz/learn/what-is-farcaster/messages) and [Contracts](https://docs.farcaster.xyz/learn/architecture/contracts) on docs.farcaster.xyz.
