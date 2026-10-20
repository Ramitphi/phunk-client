---
title: Writing to the network
summary: Sign a message with an app key and submit it through your read node.
---

<!-- ==================================================================
     SLOT: "Writing to the network" chapter.
     The main session fills this in. Either replace this file, or add
     docs/WRITING.md: if that file exists, the build uses it instead of
     this one. Keep the front matter (title, summary) at the top.
     ================================================================== -->

::: placeholder Chapter in progress
PLACEHOLDER: the step-by-step chapter on writing with `phunk-client` goes here. Until it lands, this page lays out the path a write takes, with links to the pieces.
:::

## The path of a write

Reading needs nothing but a node. Writing also needs an account, storage, and a key that is allowed to sign for it. Then you sign, submit and confirm.

1. **An account.** A [FID](concepts/fids.md), owned by a [custody address](concepts/custody.md).
2. **Storage.** At least one [storage unit](concepts/storage.md), or the network refuses the account's messages.
3. **An app key.** An Ed25519 key approved by the custody address with a gasless `KEY_ADD`. See [App keys](concepts/app-keys.md).
4. **A signed message.** Build the message `data`, hash it with BLAKE3, and sign the hash with the app key. See [Snapchain messages](concepts/messages.md).
5. **Submit it.** Post the protobuf bytes to `/v1/submitMessage` on your read node:

   ```sh
   curl -X POST "http://localhost:3381/v1/submitMessage" \
        -H "Content-Type: application/octet-stream" \
        --data-binary "@message.bin"
   ```

6. **Confirm it.** A read node checks the message and gossips it to the validators, but does not keep it. Read it back by hash to be sure it landed. See [Read nodes and validators](concepts/nodes.md#writes-go-through-read-nodes-too).

::: warning Before you run any write script
Writing means handling keys. Use a test account, and never paste your main account's seed phrase into a script. Read [Seed phrases are dangerous](concepts/seed-phrases.md) first.
:::
