# Reading and writing Farcaster through a node

Farcaster is a protocol, not an app. Every cast, like, follow and profile
lives on a network of nodes (Snapchain, and Hypersnap, a compatible build),
and any node will hand you all of it over plain HTTP. You don't need an API
key, an indexer or a paid plan.

This tutorial goes from a single `curl` up to working out which app someone
used to sign each cast, and then to posting through a node with your own
app key. Each step shows the raw endpoint first, then the same
thing with `phunk-client`.

**You'll need:** Python 3.10+ and a terminal. Allow about 30 minutes.

1. [Talk to a node](#1-talk-to-a-node)
2. [Names, fids and profiles](#2-names-fids-and-profiles)
3. [Casts, and Farcaster time](#3-casts-and-farcaster-time)
4. [Paging, and the two traps](#4-paging-and-the-two-traps)
5. [The social graph](#5-the-social-graph)
6. [Channels, replies, reactions](#6-channels-replies-reactions)
7. [Which app signed this?](#7-which-app-signed-this)
8. [Being a good guest, and running your own node](#8-being-a-good-guest-and-running-your-own-node)
9. [Run your own node](#9-run-your-own-node)
10. [Writing: app keys, casts, follows](#10-writing-app-keys-casts-follows)
11. [A web client: `phunk ui`](#11-a-web-client-phunk-ui)

---

## 1. Talk to a node

We'll use `https://haatz.quilibrium.com`, a public Hypersnap node that serves
HTTPS on port 443. A Snapchain node you run yourself answers on
`http://localhost:3381` with the same API.

```sh
curl -s https://haatz.quilibrium.com/v1/info
```

```json
{"dbStats": {"numMessages": 914272761, "numFidRegistrations": 3356123, ...},
 "numShards": 2, "shardInfos": [...]}
```

That's nearly a billion messages and 3.3M accounts, and every one of them
is readable. Note `numShards`: accounts are split across shards, and that
will matter in step 4.

Install the client:

```sh
pip install git+https://github.com/Ramitphi/phunk-client
```

```python
import asyncio
from phunk import Hub

async def main():
    async with Hub() as hub:          # or Hub("http://localhost:3381")
        info = await hub.info()
        print(info["dbStats"]["numMessages"])

asyncio.run(main())
```

The rest of the snippets run inside `async with Hub() as hub:`.

## 2. Names, fids and profiles

Every account is a number, its **fid**. Usernames are a separate layer: a
signed proof that ties a name to a fid.

```sh
curl -s "https://haatz.quilibrium.com/v1/userNameProofByName?name=dwr"
# {"name": "dwr", "fid": 3, "owner": "0x7423...", "type": "USERNAME_TYPE_FNAME", ...}
```

The profile is a set of `UserData` messages, one per field:

```sh
curl -s "https://haatz.quilibrium.com/v1/userDataByFid?fid=3"
```

```json
{"messages": [
  {"data": {"type": "MESSAGE_TYPE_USER_DATA_ADD", "fid": 3, "timestamp": 1234,
            "userDataBody": {"type": "USER_DATA_TYPE_USERNAME", "value": "dwr"}}, ...},
  {"data": {..., "userDataBody": {"type": "USER_DATA_TYPE_BIO", "value": "..."}}, ...}
]}
```

`phunk` folds those into one object:

```python
fid = await hub.fid_by_name("dwr")      # 3
user = await hub.user(fid)
print(user.username, user.display_name, user.bio, user.eth_address)
```

The fields are `username`, `display_name`, `pfp`, `bio`, `url`, `location`,
`twitter`, `github`, `banner`, `eth_address` and `sol_address`. Any field the
person never set is `None`.

## 3. Casts, and Farcaster time

```sh
curl -s "https://haatz.quilibrium.com/v1/castsByFid?fid=3&pageSize=1&reverse=true"
```

```json
{"messages": [{
  "data": {
    "type": "MESSAGE_TYPE_CAST_ADD", "fid": 3, "timestamp": 182745002,
    "castAddBody": {"text": "...", "mentions": [], "parentCastId": null,
                    "parentUrl": null, "embeds": [{"url": "https://..."}]}
  },
  "hash": "0x0256ae66...",
  "signer": "0xc887f5bf..."
}],
 "nextPageToken": "AwAAAAMBCuR3..."}
```

Three things to notice:

- `reverse=true` gives newest first. The default is oldest first.
- `timestamp` is **182,745,002**. That is not a Unix time. Farcaster counts
  seconds from **2021-01-01 00:00 UTC** (Unix 1609459200). Add that number
  to get Unix time. If you forget, every date comes out in 1975.
- `signer` is the key that signed this message. Step 7 uses it.

```python
async for cast in hub.casts(3, limit=5):
    print(cast.time, cast.text)        # .time is a UTC datetime
```

To get everything since a date, pass `since`:

```python
import datetime as dt
week_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
recent = [c async for c in hub.casts(3, since=week_ago)]
```

`phunk.to_datetime`, `from_datetime`, `to_unix` and `from_unix` convert by
hand.

## 4. Paging, and the two traps

Lists come back one page at a time (up to 1000 items). To get the next
page, pass `nextPageToken` back as `pageToken`. Simple enough, but on queries
that cross shards, the node has two habits that break naive loops.

**Trap 1: glued pages.** Some queries cover accounts on every shard:
`castsByParent` (channels, replies), `linksByTargetFid` (followers) and
`castsByMention`. The node answers them by asking each shard for a page and
**concatenating** the results. With two shards and `reverse=true`, you get
shard 0's newest casts followed by shard 1's newest casts. Each half is
sorted, but the page as a whole is not.

So "stop at the first cast older than my cutoff" is wrong: you'd stop
partway through shard 0 and never see shard 1.

**Trap 2: the token loops.** When one shard runs out before the other, the
token can send you **back to the start**. A `while token:` loop then runs
forever, yielding the same messages again and again.

The fix for both is to treat the message hash as an identity:

```python
async def channel_casts(hub, channel_url):
    seen, token = set(), None
    while True:
        params = {"url": channel_url, "reverse": "true", "pageSize": 1000}
        if token:
            params["pageToken"] = token
        page = await hub.get("/v1/castsByParent", params)
        new = [m for m in page.get("messages", []) if m["hash"] not in seen]
        seen.update(m["hash"] for m in new)
        for m in new:
            yield m
        token = page.get("nextPageToken")
        if not token or not new:                  # a page with nothing new = done
            break
```

For a date cutoff, skip old items rather than stopping at the first one, and
stop after a page in which *every new item* is too old. Each shard's part of
a page is sorted, so once a whole page is past the cutoff, every shard is.

That's what `hub.pages()` does, and every list method in `phunk` uses it.
You can use it directly for endpoints the library doesn't wrap:

```python
async for m in hub.pages("/v1/castsByParent", {"url": url, "reverse": "true"},
                         limit=500, stop=lambda m: m["data"]["timestamp"] < cutoff):
    ...
```

## 5. The social graph

A follow is a `LinkAdd` message with `type: "follow"`.

```sh
# who fid 3 follows
curl -s "https://haatz.quilibrium.com/v1/linksByFid?fid=3&link_type=follow"
# who follows fid 3 (spans shards, see step 4)
curl -s "https://haatz.quilibrium.com/v1/linksByTargetFid?target_fid=3&link_type=follow"
```

```python
following = {f.target_fid async for f in hub.following(3)}
followers = {f.fid async for f in hub.followers(3, limit=5000)}
mutuals = following & followers
```

Accounts with huge followings have hundreds of thousands of followers, so
use `limit=` on `followers` unless you mean it.

From here a graph is a few lines. For example, with
[networkx](https://networkx.org):

```python
import networkx as nx
G = nx.DiGraph()
for fid in [3, 2, 1]:
    async for f in hub.following(fid):
        G.add_edge(f.fid, f.target_fid)
print(nx.pagerank(G))
```

[`examples/mutuals.py`](../examples/mutuals.py) compares two people's follows.

## 6. Channels, replies, reactions

**Channels** aren't a separate object on the protocol. A channel cast is a
top-level cast whose `parentUrl` is the channel's URL. Older channels use a
`chain://` URL (from the NFT they started with). Newer ones use
`https://warpcast.com/~/channel/<id>`. To turn a channel name into its URL,
look it up in the main app's channel registry (that service is run by the
main app, not by a node):

```sh
curl -s "https://api.farcaster.xyz/v1/channel?channelId=dev" | jq -r .result.channel.url
# chain://eip155:1/erc721:0x7dd4e31f1530ac682c8ea4d8016e95773e08d8b0
```

```python
DEV = "chain://eip155:1/erc721:0x7dd4e31f1530ac682c8ea4d8016e95773e08d8b0"
async for cast in hub.channel(DEV, limit=20):
    print(cast.fid, cast.text[:80])
```

**Replies** use the same endpoint, keyed by the parent cast instead of a URL:

```python
async for reply in hub.replies(fid=3, hash="0xec0c8a22..."):
    print(reply.fid, reply.text)
```

**Reactions** are likes and recasts:

```python
async for like in hub.reactions(3, "like", limit=10):
    print(like.time, like.target_fid, like.target_hash)
```

**Mentions**: `hub.mentions(fid)`.

## 7. Which app signed this?

This is the part most APIs hide. Every message carries a `signer`, the
ed25519 public key that signed it. People don't sign with their wallet.
Each app they log into asks them to add a new key for that app, on-chain,
through Farcaster's KeyGateway contract on Optimism. The add event records
**which app asked**.

The node serves those events:

```sh
curl -s "https://haatz.quilibrium.com/v1/onChainEventsByFid?fid=3&event_type=EVENT_TYPE_SIGNER"
```

```json
{"events": [{
  "type": "EVENT_TYPE_SIGNER", "blockTimestamp": 1699395643, "fid": 3,
  "signerEventBody": {
    "key": "0xc887f5bf...",
    "eventType": "SIGNER_EVENT_TYPE_ADD",
    "metadata": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAA...",
    "metadataType": 1
  }
}]}
```

`metadata` is base64 of the ABI-encoded struct the app signed:

```solidity
struct SignedKeyRequestMetadata {
    uint256 requestFid;     // the app's own fid: apps are accounts too
    address requestSigner;  // the app's custody address
    bytes   signature;      // the app's signature over the key request
    uint256 deadline;
}
```

Decoded word by word (32 bytes each):

| Bytes | Value | Meaning |
|---|---|---|
| 0–31 | `0x20` | offset to the struct (it has a dynamic `bytes` field, so ABI puts it behind a pointer) |
| 32–63 | `9152` | **requestFid**: the app |
| 64–95 | `0x02ef…5596` | requestSigner, right-aligned in the word |
| 96–127 | `0x80` | offset to `signature`, from the start of the struct |
| 128–159 | `0x64ee798e` | deadline (Unix time) |
| 160–191 | `65` | signature length |
| 192–… | … | signature, padded to 32 bytes |

So the app is in bytes 32–63. Look up fid 9152's profile and you get
`warpcast`, the main app (its on-chain name hasn't changed). That key,
`0xc887f5bf…`, is the same `signer` as on the cast in step 3.

```python
from phunk import decode_key_request
decode_key_request(event["signerEventBody"]["metadata"]).request_fid   # 9152
```

Putting it together: message → `signer` → that key's ADD event →
`requestFid` → app name.

```python
async for cast in hub.casts(3, limit=20):
    app = await hub.app_of(cast)              # fetches and caches fid 3's keys
    print(await hub.app_name(app), cast.text[:50])
```

`hub.keys(fid)` gives the whole history, including removals. Some things to
know when reading it:

- **REMOVE events carry no metadata.** Match them to the ADD with the same key.
- **Off-chain ("gasless") keys** have no on-chain event at all. The node lists
  them in `/v1/signersByFid` with `source: SIGNER_SOURCE_OFFCHAIN` and the app
  in `requestFid`. `hub.keys()` includes them, with `onchain=False`.
- **Own keys.** If `requestFid` is the account's own fid, the person (or a
  tool acting for them) made the key themselves, so the app isn't named.
- **Key providers.** Some fids, like `neynar` and `privy`, request keys on
  behalf of many different apps, so their name tells you the provider, not
  the app.
- **Removing a key deletes everything it signed.** If someone signs out of an
  app that way, the node drops that app's messages for them, so the history
  you see is what's still signed by a live key.

[`examples/which_app.py`](../examples/which_app.py) prints someone's full key
history and which app signed their recent casts, likes and follows:

```
$ python examples/which_app.py dwr
Key history for @dwr (fid 3):
  2023-11-07  on-chain   add     0xc887f5bf38…  warpcast
  2023-11-07  on-chain   add     0x98f9740df6…  flink
  ...
  2023-11-27  on-chain   add     0xf7c6c32813…  super
  2024-02-06  on-chain   remove  0xf7c6c32813…
  ...
  2025-05-03  on-chain   add     0x8e59172f52…  baseapp.base.eth
  2026-01-24  on-chain   add     0x27386405c5…  neynar

Who signed their last 100 casts, likes and follows:
  cast    100  warpcast
  follow   79  warpcast
  like    100  warpcast
```

## 8. Being a good guest, and running your own node

Public nodes are shared. `Hub` defaults to at most 20 requests a second and
12 in flight, retries 429s, 5xx errors and timeouts with backoff, and raises
`NotFound` on 404 (and on the 400 some nodes send for an unknown fid). Turn
those down for big jobs:

```python
async with Hub(rate=5, concurrency=4) as hub:
    ...
print(hub.requests, hub.errors)   # what it cost
```

When you want the whole network (every fid, every follow), run your own
node ([step 9](#9-run-your-own-node)). A Snapchain node syncs the full
history, and the same code works unchanged:

```python
async with Hub("http://localhost:3381", rate=500, concurrency=64) as hub:
    ...
```

From the command line:

```sh
phunk --node http://localhost:3381 info
phunk whois dwr
phunk apps dwr
```

## 9. Run your own node

A public node is fine for learning. For real work, run your own. It is a
read node: it follows the chain, keeps a full copy of the data, and serves
the same HTTP API. It needs no API key and no account with anyone.

### What to rent

| | Minimum |
|---|---|
| CPU | 4 cores |
| RAM | 16 GB |
| Disk | 2 TB SSD or NVMe |
| Network | a public IP, ports 3381–3383 open |

The database is 500–800 GB today and grows, so don't skimp on disk. Any
provider works. Rough monthly prices (check before you rent):

- **Dedicated server** (Hetzner, OVH, Latitude and similar): about $60–150.
- **Cloud VM plus a 2 TB volume** (AWS, GCP, DigitalOcean and similar):
  about $200–400. Most of that is the disk.

Pick Ubuntu 22.04 and add your SSH key when you create the server.

### Three commands

Log in to the new server (as root, or a user with sudo) and prepare it:

```sh
curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/provision.sh | sudo bash
```

This updates the system, installs Docker, creates a `snapchain` user with
your SSH key, and turns on the firewall. If it says a reboot is needed,
reboot. Then log in as that user and start the node:

```sh
ssh snapchain@<ip>
./node.sh start
```

`node.sh` runs Farcaster's official
[`snapchain.sh`](https://snapchain.farcaster.xyz/guides/managing-a-node),
the same one the
[bootstrap one-liner](https://snapchain.farcaster.xyz/guides/running-a-node)
installs. Everything lives in `~/snapchain`. On the first start it asks two
things:

- To agree that you get no rewards for running a node. Type `Yes`.
- Your FID or username. It only picks the weekday for weekly auto-upgrades.
  Press Enter to skip; auto-upgrade is then off.

To run it without questions, set `SNAPCHAIN_AGREE_NO_REWARDS=yes` and
`SNAPCHAIN_OPERATOR_FID=<fid>` first.

The other commands:

```sh
./node.sh status    # sync progress, per shard
./node.sh logs      # follow the logs; Ctrl-C stops following, not the node
./node.sh stop
./node.sh upgrade   # latest release, then restart
```

### The snapshot, then sync

A new node doesn't replay history from the start. It downloads a snapshot
of several hundred GB, which takes a few hours. The API is down until that
finishes:

```
$ ./node.sh status
The node's API at http://localhost:3381 is not answering.
Downloading the snapshot: shard 1, chunk 42 of 2672.
This takes a few hours. The API comes up when it is done.
```

Then it syncs the blocks made since the snapshot. `status` reads
`/v1/info`:

```sh
curl -s localhost:3381/v1/info | jq '.shardInfos'
```

and says how far each shard is behind:

```
$ ./node.sh status
Snapchain 0.14.2 at http://localhost:3381
  914092103 messages, 3354008 fids, 549 GB on disk

  shard 0  height 47583086   1s behind      in sync
  shard 1  height 48256318   45s behind     nearly in sync
  shard 2  height 48091473   3h 7m behind   catching up

Still catching up. Reads work, but recent messages may be missing.
```

Shard 0 is the block shard; it orders the other two. Shards 1 and 2 hold
user messages, split by fid. "Behind" is `blockDelay`: how many seconds the
shard's newest block is behind the clock. Under 10 seconds on every shard
means in sync.

Don't restart or upgrade the node while the snapshot downloads. The
snapshot comes one shard at a time. After a restart, the shards not yet
downloaded sync from block 1 instead, which takes months. `status` spots
this and prints the fix:

```
  shard 0  height 190123     575d 5h behind snapshot interrupted
  shard 1  height 49200000   2s behind      in sync
  shard 2  height 190456     575d 5h behind snapshot interrupted

Shards 0 and 2 are far behind the others. The snapshot download for them was
interrupted, probably by a restart, so the node is syncing them from block 1.
Waiting would take months. Download the snapshot again instead (a few hours):

  ./node.sh stop
  sudo rm -rf /home/snapchain/snapchain/.rocks /home/snapchain/snapchain/.rocks.snapshot
  ./node.sh start
```

### Ports

| Port | | What |
|---|---|---|
| 3381 | TCP | HTTP API (what `Hub` talks to) |
| 3382 | UDP | gossip with other nodes |
| 3383 | TCP | gRPC API |

All three must be reachable from the internet. That also means anyone can
read from your node, like any public node.

Docker publishes ports around `ufw`, so a firewall rule alone won't hide a
container. `node.sh` binds Grafana (3000) and statsd (8125, 8126) to
`127.0.0.1` for that reason. To see the Grafana dashboard, tunnel to it:

```sh
ssh -L 3000:localhost:3000 snapchain@<ip>   # then open http://localhost:3000
```

### Point the client at it

```python
async with Hub("http://<ip>:3381", rate=500, concurrency=64) as hub:
    print(await hub.info())
```

```sh
phunk --node http://<ip>:3381 info
```

On the server itself, use `http://localhost:3381`.

### Read nodes and validators

Both run the same binary. A
[validator](https://snapchain.farcaster.xyz/node-types) proposes and votes
on blocks. Only nodes whose keys are in the validator set can do that. A
read node never votes. It follows the chain and serves the API, and anyone
can run one. Both end up with the same data. A read node is a moment
behind, because it fetches each block after validators decide it.

A read node still accepts writes. Send a signed message to
`/v1/submitMessage` and it checks the message, then
[gossips it to the validators](https://snapchain.farcaster.xyz/node-types#message-submission-and-the-mempool).
It keeps no copy. The message lands in a block once a validator picks it
up. If the gossip is lost, the message is gone, so check that it arrived
and resubmit if not. `phunk` only reads, so this matters only when you
start writing.

That's the whole stack: a node, an HTTP API, and a few traps to avoid.
Everything else (feeds, search, recommendations, graph analysis) is
something you can build on top.

---

## 10. Writing: app keys, casts, follows

Reading needs nothing. Writing needs two keys and one rule.

* The **custody key** owns the account. It is the Ethereum key behind the
  seed phrase, and the IdRegistry contract maps its address to the fid.
* An **app key** (also called a signer) is an Ed25519 key that signs every
  cast, like and follow. Each app you sign in to holds its own app key. That
  is how step 7 could tell apps apart.
* The rule: a node only accepts a message signed by an app key the account
  has added.

Adding an app key used to cost a transaction on Optimism. Snapchain now
accepts **gasless** app keys: you send a `KEY_ADD` message to a node,
authorized by a signature from the custody key. No gas, no wallet balance,
and it lands in about a second. A gasless key is:

* **scoped**: it may only sign the message types listed when it was added.
* **short-lived but sliding**: it expires after its TTL (at most 90 days),
  and every message it signs pushes the expiry out again.
* **unreplayable**: each `KEY_ADD` carries a nonce that must beat the fid's
  `currentUserNonce`.

The account also needs storage, which it rents on-chain. An account that
already posts has it.

> **Seed phrases are dangerous.** Whoever has one owns the account. Try
> this with a **test account**, never your main one. Only paste a seed
> phrase into software from developers you trust (this one is under 400
> lines: `src/phunk/write.py`). Apps that can should ask a wallet to sign
> the authorization instead, so the phrase never leaves it.

Install the writing extras (Ed25519, BLAKE3 and Ethereum signing):

```sh
pip install 'phunk-client[write]'
```

### Add an app key

From the command line. The phrase is read hidden, used once in memory, and
dropped. Only the new app key is saved, readable by you alone:

```sh
phunk --node http://<your-node>:3381 key add
```

In Python:

```python
import asyncio, getpass
from phunk import Hub
from phunk.write import Custody, add_app_key

async def main():
    custody = Custody.from_mnemonic(getpass.getpass("seed phrase: "))
    async with Hub("http://<your-node>:3381") as hub:
        fid, key = await add_app_key(hub, custody)
    print(fid, key.public_hex)
    # key.private_hex signs from now on. Store it like a password.

asyncio.run(main())
```

Under the hood, `add_app_key`:

1. looks up the fid that the custody address owns
   (`GET /v1/onChainIdRegistryEventByAddress?address=0x...`)
2. reads `currentUserNonce` (`GET /v1/signersByFid?fid=...`)
3. signs two EIP-712 messages with the custody key, both on the domain
   `{name: "Farcaster KeyAdd", version: "1", chainId: 1}`:
   a `SignedKeyRequest` (which app is asking for the key) and a `KeyAdd`
   (the fid, key, scopes, TTL, nonce and deadline)
4. wraps them in a `KEY_ADD` message signed by the **new** key, which proves
   you hold it, and posts it to `/v1/submitMessage`

Afterwards the key shows up in `GET /v1/signersByFid` with
`"source": "SIGNER_SOURCE_OFFCHAIN"`.

### Post, like, follow

```sh
phunk post "hello from my own node"
phunk post "gm" --channel https://warpcast.com/~/channel/politics
phunk like 3:0x<cast hash>
phunk follow dwr
```

```python
from phunk.write import AppKey, Writer

async with Hub("http://<your-node>:3381") as hub:
    me = Writer(hub, fid, AppKey.from_hex(private_hex))
    h = await me.cast("hello @dwr")            # "@dwr" becomes a real mention
    await me.cast("same here", reply_to=(3, "0x<cast hash>"))
    await me.like(3, "0x<cast hash>")
    await me.follow(3)
```

Each call builds one message, signs it and posts it to
`/v1/submitMessage`. You can post to your own **read node**: read nodes
accept writes and forward them to the validators, which put them in a block.

### What a message is

A message is protobuf. `MessageData` holds the type, fid, timestamp
(Farcaster time), network and the body. Its hash is the first 20 bytes of
BLAKE3 over those bytes, and the app key signs the hash:

```python
from phunk import proto
from phunk.write import AppKey

key = AppKey.generate()
msg = key.sign(proto.CAST_ADD, 3, proto.CastAddBody(text="just testing").encode())
```

To check a message without posting it, ask the node to validate it. This
checks the encoding, hash and signature only, so it works even for a key
nobody added. Nothing is submitted:

```python
async with Hub() as hub:
    print((await hub.validate(msg))["valid"])   # True
```

`phunk`'s messages match the official TypeScript library byte for byte; the
tests check this (`tests/test_write.py`).

### Your feed, from any app

A node has no feed endpoint. A client builds one: read the latest casts of
everyone you follow and merge them. Because every cast says which key signed
it, you can include or hide casts by app:

```sh
phunk feed                         # who your key's fid follows, last 24h
phunk feed dwr --hide warpcast     # anyone's, minus one app
phunk feed --only fireflyapp,"own key"
```

```python
casts = await hub.feed(fid)                    # newest first
apps = [await hub.app_of(c) for c in casts]    # who signed each
```

### "Sent from" your app

Clients show where a cast came from ("Sent from Warpcast") by reading the
key that signed it. Each key's `KEY_ADD` names the app that asked for it:
the `requestFid` in its `SignedKeyRequest` metadata, signed by that app
account's custody key. Its username is the name clients show.

`phunk key add` on its own makes the account its own app, so your casts
show your own username. To show a client's name instead:

1. **Make an account for the app** and give it a username (an fname).
2. **Run a signing service** that holds the app account's custody key and
   approves new keys. `signer/` in this repo is one, ready for Vercel. It
   only ever sees the new public key.
3. **Point `key add` at it:**

```sh
phunk key add --app-signer https://<your-signer>/api/sign-key-request
```

`phunk` checks each approval before using it: it must name the app's fid,
be signed over this exact key, not be expired, and come from the app
account's custody address on the node
(`/v1/onChainEventsByFid?event_type=EVENT_TYPE_ID_REGISTER`).

To set the app account's username, add a key to the app account itself
(with its own seed phrase, into a separate key file), then:

```sh
PHUNK_KEY_FILE=~/.config/phunk/app-account.json phunk profile --username yourapp
```

### When you're done: revoke the key

You don't have to revoke a key you stop using: its TTL runs out. Revoke it
sooner if a laptop is lost, a key file leaks, or you stop using an app.
The key stops working at once. Casts it already signed stay up.

See which keys an account has, and which app asked for each:

```sh
phunk key list dwr
# 2023-11-07  on-chain  0xc887f5bf...  warpcast
# 2026-01-24  on-chain  0x27386405...  neynar
```

Revoke the key saved on this machine. The key signs its own removal, so no
seed phrase is needed:

```sh
phunk key revoke
```

Revoke some other gasless key on the account, one you no longer hold. This
needs the custody key, so it asks for the seed phrase (test account only):

```sh
phunk key revoke --key 0x<public key from key list>
```

Both send a `KEY_REMOVE` message to `/v1/submitMessage`:

- **Self** (`signatureType: 2`): signed by the key being removed. Its nonce
  must beat the nonce of the app that asked for the key:
  `GET /v1/signersByFid?fid=...&requesterFids[0]=<app fid>` returns it in
  `requesterFidNonces`. Nodes that don't report it yet answer a wrong nonce
  with "not greater than stored nonce N"; phunk then retries once with N + 1.
- **Custody** (`signatureType: 1`): carries an EIP-712 `KeyRemove`
  `{fid, key, nonce, deadline}` signed by the custody key, on the same
  domain as `KeyAdd`. Its nonce must beat `currentUserNonce`.

```python
from phunk.write import revoke_key, revoke_key_with_custody

async with Hub("http://localhost:3381") as hub:
    await revoke_key(hub, fid, key)                       # the saved AppKey
    await revoke_key_with_custody(hub, custody, "0x...")  # any gasless key
```

Only gasless keys can be removed this way. On-chain keys, like the ones
in the list above, need a transaction to the Key Registry contract.

---

## 11. A web client: `phunk ui`

Everything above, in a browser. `phunk ui` serves a small web client on
your own machine:

```sh
phunk ui --as dwr          # look at anyone's people, read-only
phunk ui                   # as your app key's fid: follow, say hi, post
phunk ui --open            # and open it in your browser
phunk ui --public          # what phunk.social runs: read-only, actions open Farcaster
```

Then visit `http://127.0.0.1:8008`.

Its main screen is a feed of **people**, not posts. As people spread across
clients, the risk isn't that the network dies, it's that it splits into
islands and you lose the people you used to talk to. So each card is one
person to connect with: who they are, which app they post from now, one
plain reason, their latest casts, and **Follow**, **Say hi** (a reply to
their latest cast) and **Not now**. There are three kinds:

* **Reconnect**: people you replied to, mentioned, liked or recast in the
  last 90 days who now post from a different app, went quiet, or whom you
  never followed.
* **Your circle**: friends of friends with many mutual follows (4 or more).
* **Bridge**: only 2 or 3 mutual follows, but posting from a different app
  or in a channel you don't use. These keep you connected beyond your own
  island.

A line on top sums up your network: how many people you talk to, and how
many of them moved apps. The search box looks up anyone's people feed, so
you can see what the client would suggest to someone else.

The **Casts** tab has the classic feed:

* **Casts from everyone you follow**, newest first, each with the app that
  signed it. Pick a window (6 hours to 7 days) and load more.
* **An app filter**: a checkbox for every app seen, plus "own key" and
  "unknown". Untick one to hide its casts.
* **New cast**, to post, optionally to a channel.

**New cast** opens a composer that offers the channels it has seen as
one-tap picks. On a phone the page becomes four screens, switched from a
bottom bar: People, Casts, the composer (the **+**) and You (who you are,
and the search box to look up anyone's people).

Without an app key it is read-only: everything browses, and the write
buttons tell you to run `phunk key add`. The page never asks for a seed
phrase. The server binds to 127.0.0.1, answers only to a local Host, and
refuses POSTs from other sites. The app key stays in the server process.

It is plain HTML, CSS and JS served by `aiohttp`, with a small JSON API
(`/api/people`, `/api/feed`, `/api/suggestions`, `/api/cast`, `/api/react`,
`/api/follow`). Read `src/phunk/ui.py`, `src/phunk/people.py` and
`src/phunk/static/` to see how it works.

### Building the people feed

Every card comes from data any node serves:

* **Who you talk to**: your casts (`parentCastId` and `mentions`) and your
  likes and recasts (`/v1/reactionsByFid`) from the last 90 days.
* **Which app someone posts from now, and before**: the key that signed
  their newest cast versus their casts from more than two weeks ago
  (step 7).
* **Mutual follows and channels**: below.

```python
from phunk.people import people_feed

async with Hub() as hub:
    feed = await people_feed(hub, await hub.fid_by_name("dwr"))
    for card in feed["cards"][:5]:
        print(card.kind, card.fid, " ".join(card.why))
```

The first load reads about 150 things from the node, roughly ten seconds
on a public one. The ranking itself, `people.build()`, is a pure function
you can test without a node.

### People to follow

A node has no "who to follow" endpoint either. Two signals are easy to read
from it:

1. **Friends of friends.** Take the people you followed most recently, read
   whom each of them follows (newest first), and count. Someone followed by
   several people you follow is a good bet.
2. **Shared channels.** Find the channels you post in (the `parentUrl` on
   your latest top-level casts), then who else posted there lately.

```sh
# whom fid 3 follows, newest first (repeat for a sample of them)
curl "https://haatz.quilibrium.com/v1/linksByFid?fid=3&link_type=follow&reverse=true&pageSize=300"
# fid 3's latest casts: their parentUrl values are channels
curl "https://haatz.quilibrium.com/v1/castsByFid?fid=3&reverse=true&pageSize=100"
# who posts in /politics lately
curl "https://haatz.quilibrium.com/v1/castsByParent?url=https://warpcast.com/~/channel/politics&reverse=true&pageSize=100"
```

`phunk.recommend.suggest` does both and scores plainly: one point per
person you follow who follows them, plus two per shared channel. People you
already follow, and you, are left out. A friend of a friend needs at least
two people you follow behind them, unless you also share a channel.

```python
import asyncio
from phunk import Hub
from phunk.recommend import suggest

async def main():
    async with Hub() as hub:
        fid = await hub.fid_by_name("dwr")
        for s in await suggest(hub, fid, limit=5):
            name = (await hub.user(s.fid)).username
            print(f"{s.score:3}  @{name}  followed by {len(s.followed_by)} you follow, channels {s.channels}")

asyncio.run(main())
```

It reads only 40 follow lists (`sample=`), 300 follows each
(`per_account=`), and your top 3 channels (`channels=`): about 50 requests,
whatever the size of your graph. That keeps it polite on a shared node.
