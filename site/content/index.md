---
title: Quickstart
summary: Run a Snapchain read node and read Farcaster from it with a small Python client. Three commands, then a tutorial.
alt_href: zh/index.html
alt_label: 中文
alt_lang: zh-CN
---

::: hero
# Read Farcaster from your own node

Every cast, follow and profile on Farcaster lives on a network of nodes called Snapchain. Run one, and you can read all of it over plain HTTP. No API key, no indexer, no paid plan.
:::

## Quickstart

Three commands: get a server, start a node on it, read from the node.

::: quickstart
::: step
### Get a server

```sh
# on your own computer; hetzner, digitalocean, aws, or root@<ip> for a server you have
curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/server.sh | bash -s -- hetzner
```

This rents an Ubuntu server with 16 GB of RAM and a 2 TB disk from your provider, then sets it up: Docker, a `snapchain` user with your SSH key, and the firewall. Already have a server? Pass its login instead, like `root@203.0.113.9`. It asks before it spends anything and prints the server's address. Ending the command with `hetzner destroy` instead deletes it all. You need your provider's command-line tool and an API token, for Hetzner `hcloud` and `export HCLOUD_TOKEN=<token>`. [Run your own node](tutorial.html#9-run-your-own-node) lists what each provider needs and what it costs.
:::
::: step
### Start a read node

```sh
ssh -t snapchain@<server-ip> ./node.sh start      # then ./node.sh status to watch it sync
```

The first start asks two things: type `Yes` to agree that a node earns no rewards, then press Enter to skip the FID question. It installs Snapchain under Docker, downloads a snapshot of the network (several hundred GB, a few hours), then syncs the blocks made since. **Don't restart the node while the snapshot downloads**: the shards not yet downloaded would sync from block 1 instead, which takes months. A read node follows the chain and serves the API. It does not vote on blocks. [Read nodes and validators](concepts/nodes.md) explains the difference.
:::
::: step
### Read from it

Once `./node.sh status` says every shard is in sync, log in (`ssh snapchain@<server-ip>`) and run:

```sh
pip install 'phunk-client[write] @ git+https://github.com/Ramitphi/phunk-client' && phunk --node http://localhost:3381 whois dwr
```

This installs `phunk-client` and reads dwr's profile from your node. To run it from your own computer instead, swap `localhost` for the server's address.

To write, add an app key for a **test account** and post through the same node:

```sh
export PHUNK_NODE=http://localhost:3381
phunk key add
phunk post "hello from my own node"
```

`key add` asks for the account's seed phrase, uses it once and keeps only the new app key. Your post goes to your node, which checks it and passes it on to the validators. [Writing to the network](writing.html) covers following, liking and filtering the feed by app.
:::
:::

::: warning Seed phrases are dangerous
A seed phrase is the whole account: whoever has it can take it. Try this with a **test account** only, never your main one. Only paste a seed phrase into software from developers you trust, and prefer apps that use delegated signer authorization, so they never see it. Read [Seed phrases are dangerous](concepts/seed-phrases.md) first.
:::

::: note No server yet?
Skip the first two steps. A public node answers the same API, and the client uses one by default:

```sh
pip install git+https://github.com/Ramitphi/phunk-client
phunk whois dwr
phunk feed dwr --hide warpcast
```

If `pip` says "externally-managed-environment" (macOS with Homebrew, Ubuntu 24.04, Debian 12), make a virtual environment first: `python3 -m venv ~/.phunk && . ~/.phunk/bin/activate`.
:::

## Learn it properly

<ul class="cards">
<li><a href="tutorial.html"><strong>Tutorial: reading Farcaster from a node</strong><span>From one curl to working out which app signed each cast, then running your own node. About 30 minutes to read.</span></a></li>
<li><a href="writing.html"><strong>Writing to the network</strong><span>Add a gasless app key, then post, like and follow through your own node. Build a feed filtered by app.</span></a></li>
<li><a href="concepts/index.html"><strong>Concepts</strong><span>FIDs, custody addresses, app keys, messages, nodes and storage rent. One short page each.</span></a></li>
<li><a href="concepts/seed-phrases.html"><strong>Seed phrases are dangerous</strong><span>Read this before any script asks for one.</span></a></li>
</ul>

## What the client does

`phunk-client` is a small async Python library. Reading needs one dependency, `aiohttp`; writing adds a few optional extras for signing. It wraps the node's HTTP API and handles the parts that are easy to get wrong:

- **Paging across shards.** Some queries return one sorted page per shard, glued together, and their page token can loop. The client dedupes by message hash and stops cleanly.
- **Farcaster time.** Message timestamps count seconds from 2021-01-01, not 1970. Every model has a `.time` in UTC.
- **Which app signed it.** Each message names the key that signed it. The client follows that key back to the app that asked for it.
- **Writing.** Gasless app keys, casts, likes and follows, signed byte for byte like the official TypeScript library.
- **Being polite.** A rate limit, a cap on requests in flight, and retries with backoff.

```python
import asyncio
from phunk import Hub

async def main():
    async with Hub("http://localhost:3381") as hub:
        fid = await hub.fid_by_name("dwr")
        async for cast in hub.casts(fid, limit=5):
            print(cast.time, cast.text[:60])

asyncio.run(main())
```

Leave out the URL to use the public node at `https://haatz.quilibrium.com`.
