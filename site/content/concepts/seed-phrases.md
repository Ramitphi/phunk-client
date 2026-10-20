---
title: Seed phrases are dangerous
summary: A seed phrase is the whole account. Use test accounts, trust the code, and prefer delegated signing.
order: 7
---

A seed phrase (the 12 or 24 words a wallet shows you once) generates the private key of a [custody address](custody.md). Anyone who has it owns the account. They can move the FID to their own address, add their own app keys, and take any ETH in that wallet. There is no undo and no support desk.

Some Farcaster scripts, including ones that create accounts or add keys, ask for a seed phrase or a custody private key. Before you give one to anything, read the rules below.

::: warning The short version
- Use a **test account** only.
- Only paste a seed phrase into software from **developers you trust**.
- Prefer **delegated signer authorization**, so the app never sees it.
- **Never commit it or log it.**
:::

## Use a test account

Make a new wallet for development and register a new FID with it. Fund it with only what the script needs (the Snapchain docs put registration and a storage unit at about $10 of ETH on OP Mainnet). Never point a script at the wallet that holds your real account or your savings.

If something goes wrong with a test account, you lose a test account.

## Only trust code you trust

A program that holds your seed phrase can send it anywhere. That includes a script from a tutorial, a package it installs, or a hosted tool with a friendly form.

- Prefer code you can read, from people with a public track record.
- Pin the version you checked. A later update can change what it does.
- Be suspicious of anything that asks for a seed phrase when it only needs to post. Posting needs an app key, not the custody key.

## Prefer delegated signer authorization

Most apps never need a seed phrase. The safe pattern is:

1. Your app generates its own Ed25519 [app key](app-keys.md).
2. The user approves that key in the wallet app they already use.
3. Your app signs messages with its key from then on.

The seed phrase stays in the wallet. A gasless key can also be limited to the message types your app needs, and it expires by itself if your app stops using it. If the key leaks, the user revokes it and the account is untouched.

## Never commit it or log it

- Read it from an environment variable or a secrets manager, never from a file in the repo.
- Add `.env` and similar files to `.gitignore` before you create them.
- Don't print it, and don't let error messages or debug logs include it.
- Don't paste it into issues, chats, screenshots or AI tools.

Git keeps history. Deleting a committed secret in a later commit does not remove it. If a seed phrase has ever been committed, pushed, logged or shared, treat it as public: move the FID to a new custody address and stop using the old wallet.

App key private keys deserve the same care. A leaked app key can post as the user until it is revoked, though it cannot take the account.
