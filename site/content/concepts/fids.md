---
title: FIDs
summary: Every account is a number. Names are a separate layer on top.
order: 1
---

A Farcaster account is a number called a **FID** (Farcaster ID). dwr is 3. v is 2. vitalik.eth is 5650.

## Where it comes from

An Ethereum address calls the IdGateway contract on OP Mainnet. The IdRegistry contract gives that address the next free number. That address is now the account's [custody address](custody.md).

- One address owns at most one FID at a time.
- FIDs are handed out in order, so a lower FID is an older account.
- The account can move to another address later. The number stays the same.

The node watches these contracts and stores the events, so you never need to call the chain yourself to read an account.

## Everything points at the FID

Casts, likes, follows and profile fields all carry a `fid`. A follow points at a target FID, not a name. A reply points at a parent cast by FID and hash.

That is why someone can change their username without re-signing anything. The name was never in the data.

## Names are proofs

A username is a signed proof that ties a name to an address, and so to a FID. There are two kinds:

- **fnames** like `dwr`, issued off-chain by Farcaster's name server.
- **ENS names** like `vitalik.eth`, checked against Ethereum.

The node stores the proofs:

```sh
curl -s "$NODE/v1/userNameProofByName?name=vitalik.eth"
```

```json
{"name": "vitalik.eth", "fid": 5650, "type": "USERNAME_TYPE_ENS_L1",
 "owner": "0xd8da6bf26964af9d7eed9e03e53415d37aa96045", "timestamp": 1691063015, ...}
```

With the client:

```python
fid = await hub.fid_by_name("vitalik.eth")    # 5650
```

The `username` on a profile is a separate `USER_DATA_TYPE_USERNAME` message. The node only accepts it if the account holds a proof for that name.

## Listing every FID

Accounts are spread across shards. `/v1/fids` lists the FIDs on one shard at a time:

```sh
curl -s "$NODE/v1/fids?shard_id=1&pageSize=5"
```

`/v1/info` reports the total in `numFidRegistrations`. It is over 3.3 million.
