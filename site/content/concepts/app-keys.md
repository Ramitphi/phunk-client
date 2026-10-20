---
title: App keys
summary: The Ed25519 keys that sign messages. Adding one is now gasless, scoped and expires on its own.
order: 3
---

People don't sign casts with their wallet. Each app they use gets its own **app key** (also called a signer): an Ed25519 key pair. The app keeps the private key and signs messages with it. The account's [custody address](custody.md) approves the public key once.

An app key can post, like, follow and edit the profile, as far as its scopes allow. It cannot transfer the account or add other keys. If an app misbehaves, the user removes its key.

## Two ways to add a key

**Gasless (the current way).** The app submits a `KEY_ADD` message to a node. No transaction, no gas, and it lands in about a second. It carries two EIP-712 signatures:

1. A *signed key request* from the app that wants the key. It binds the app's own FID (`requestFid`) to the key, so one app can't pose as another.
2. A *key add* signature from the account's custody address. This is the user's approval. It covers the key's scopes, TTL and nonce.

The message itself is signed by the new key, which proves the submitter holds it.

Gasless keys have three rules that on-chain keys don't:

- **Scopes.** Each key lists the message types it may sign. A key scoped to `CAST_ADD` cannot follow anyone. No key can ever add or remove another key.
- **Sliding TTL.** Each key has a time to live of up to 90 days. Every message it signs resets the clock, so a key in use stays alive and an abandoned one expires by itself.
- **Nonces.** Each FID has a counter, and each `KEY_ADD` must use a higher number. A captured approval can't be replayed.

An account can hold up to 1,000 active gasless keys.

**On-chain (the old way).** The app calls the KeyGateway contract on OP Mainnet. This costs gas and needs a funded wallet. Existing on-chain keys keep working with no scopes and no expiry, but new code should use `KEY_ADD`.

Details and code: [Writing messages](https://snapchain.farcaster.xyz/guides/writing-messages) in the Snapchain docs.

## Removing a key

A gasless key is removed with a `KEY_REMOVE` message. Either the custody address signs it, or the key revokes itself. An app that has been compromised can revoke all the keys it requested in one go.

You don't have to remove a key you've stopped using. Its TTL runs out.

## Reading keys from the node

```sh
curl -s "$NODE/v1/signersByFid?fid=3"
```

```json
{"signers": [{
   "source": "SIGNER_SOURCE_ONCHAIN",
   "key": "0xc887f5bf385a4718eaee166481f1832198938cf33e98a82dc81a0b4b81ffe33d",
   "fid": 3, "addedAt": 1699395643,
   "onChainEvent": {...}
 }, ...],
 "gaslessSignerCount": 0, "gaslessSignerLimit": 1000, "currentUserNonce": 0}
```

This one list has both kinds. Gasless keys show `source: SIGNER_SOURCE_OFFCHAIN`. `currentUserNonce` is what the next `KEY_ADD` must beat.

Every message names the key that signed it in `signer`. Match that against this list and you know which app sent it:

```python
async for cast in hub.casts(3, limit=5):
    app = await hub.app_of(cast)
    print(await hub.app_name(app), cast.text[:50])
```

[Step 7 of the tutorial](../tutorial.html#7-which-app-signed-this) decodes the key request byte by byte.

## Delegated authorization

This split is what makes Farcaster apps safe to try. Your app generates a key pair. The user approves the public key in the wallet app they already trust. Your app never sees their seed phrase, and the key it gets can be limited to what it needs. Prefer this to any flow that asks the user for a seed phrase. See [Seed phrases are dangerous](seed-phrases.md).
