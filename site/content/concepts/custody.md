---
title: Custody addresses
summary: The Ethereum address that owns a FID. It is the master key, so apps should never ask for it.
order: 2
---

The **custody address** is the Ethereum address that owns a [FID](fids.md). Whoever holds its private key (or the seed phrase behind it) controls the account.

## What it can do

- **Transfer** the FID to another address.
- **Set a recovery address**, which can also move the FID to a new address.
- **Authorize app keys.** It signs the approval that lets an app's key post for the account. See [App keys](app-keys.md).

What it does not do is sign casts. Day-to-day messages are signed by app keys. The custody key only comes out for account-level changes.

## Recovery

Wallets get lost. A recovery address can move the account to a fresh custody address. It can be set when the account is created or at any time after.

Accounts made in the Farcaster app use Farcaster's recovery service, which recovers them by email. If the owner changes the recovery address away from it, Farcaster can no longer recover the account.

## Reading it from the node

The register and transfer events live on the node:

```sh
curl -s "$NODE/v1/onChainEventsByFid?fid=3&event_type=EVENT_TYPE_ID_REGISTER"
```

```json
{"events": [{
  "type": "EVENT_TYPE_ID_REGISTER", "fid": 3, "blockTimestamp": 1699386171,
  "idRegisterEventBody": {
    "to": "0x6b0bda3f2ffed5efc83fa8c024acff1dd45793f1",
    "eventType": "Register",
    "from": "0x",
    "recoveryAddress": "0x00000000fcb080a4d6c39a9354da9eb9bc104cd7"
  }
}]}
```

`to` is the custody address. If the account was later transferred, a newer event names the new owner. `blockTimestamp` is Unix time, because this happened on a chain. Messages use [Farcaster time](messages.md#timestamps).

To go the other way, from an address to its FID:

```sh
curl -s "$NODE/v1/onChainIdRegistryEventByAddress?address=0x6b0bda3f2ffed5efc83fa8c024acff1dd45793f1"
```

## Why it matters when you build

An app that asks for a custody key or seed phrase is asking for the whole account. A well-behaved app asks for an app key instead, and the user approves it in their wallet. Read [Seed phrases are dangerous](seed-phrases.md) before you write a script that touches one.
