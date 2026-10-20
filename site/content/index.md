---
title: Quickstart
summary: Run a Snapchain read node and read Farcaster from it with a small Python client. Three commands, then a tutorial.
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
# on a fresh Ubuntu server, as root
curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/provision.sh | sudo bash
```

A Linux machine with 16 GB of RAM, 4 CPU cores, 2 TB of disk and ports 3381 to 3383 open. Those are Snapchain's stated minimums.
:::
::: step
### Start a read node

```sh
ssh snapchain@<server-ip>
./node.sh start      # then ./node.sh status to watch it sync
```

This installs Snapchain under Docker, downloads a snapshot of the network (several hundred GB, a few hours), then syncs the blocks made since. A read node follows the chain and serves the API. It does not vote on blocks. [Read nodes and validators](concepts/nodes.md) explains the difference.
:::
::: step
### Read from it

```sh
pip install 'phunk-client[write] @ git+https://github.com/Ramitphi/phunk-client' && phunk --node http://localhost:3381 whois dwr
```

This installs `phunk-client` and reads dwr's profile from your node. Run it on the server, or swap `localhost` for the server's address. The node is ready when every shard's `blockDelay` in `/v1/info` is in single digits.

To write, add an app key for a **test account** and post through the same node:

```sh
phunk --node http://localhost:3381 key add
phunk --node http://localhost:3381 post "hello from my own node"
```

`key add` asks for the account's seed phrase, uses it once and keeps only the new app key. Read [Seed phrases are dangerous](concepts/seed-phrases.md) first, and [Writing to the network](writing.html) for what happens underneath.
:::
:::

::: note No server yet?
Skip the first two steps. A public node answers the same API, and the client uses one by default:

```sh
pip install git+https://github.com/Ramitphi/phunk-client
phunk whois dwr
```
:::

## Learn it properly

<ul class="cards">
<li><a href="tutorial.html"><strong>Tutorial: reading Farcaster from a node</strong><span>Eight short steps, from one curl to working out which app signed each cast. About 30 minutes.</span></a></li>
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
