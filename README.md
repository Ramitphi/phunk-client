# phunk-client

Read and write Farcaster straight from a Snapchain / Hypersnap node. No API
key, no Neynar, no indexer: just the node's HTTP API, with the paging quirks
handled for you, and gasless app keys for posting.

```python
import asyncio
from phunk import Hub

async def main():
    async with Hub() as hub:                      # public node by default
        fid = await hub.fid_by_name("dwr")
        print(await hub.user(fid))
        async for cast in hub.casts(fid, limit=5):
            app = await hub.app_of(cast)              # which app signed it
            print(cast.time, await hub.app_name(app), cast.text[:60])

asyncio.run(main())
```

New to reading from a node? **[Start with the tutorial](docs/TUTORIAL.md)**,
or read it on the [microsite](https://ramitphi.github.io/phunk-client/) with
the quickstart and short concept pages.

## Install

```sh
pip install git+https://github.com/Ramitphi/phunk-client
```

Python 3.10+. Reading needs only `aiohttp`. Writing needs three more
(Ed25519, BLAKE3 and Ethereum signing):

```sh
pip install 'phunk-client[write] @ git+https://github.com/Ramitphi/phunk-client'
```

## What you get

| Method | Endpoint | Returns |
|---|---|---|
| `fid_by_name(name)` | `/v1/userNameProofByName` | fid |
| `user(fid)`, `user_by_name(name)` | `/v1/userDataByFid` | `User`: username, display name, pfp, bio, primary addresses |
| `following(fid)` | `/v1/linksByFid` | `Follow`s |
| `followers(fid)` | `/v1/linksByTargetFid` | `Follow`s |
| `casts(fid, since=, limit=)` | `/v1/castsByFid` | `Cast`s, newest first |
| `cast(fid, hash)` | `/v1/castById` | one `Cast` |
| `replies(fid, hash)` | `/v1/castsByParent` | `Cast`s |
| `channel(url, since=, limit=)` | `/v1/castsByParent?url=` | `Cast`s in a channel |
| `mentions(fid)` | `/v1/castsByMention` | `Cast`s |
| `reactions(fid, "like" \| "recast")` | `/v1/reactionsByFid` | `Reaction`s |
| `keys(fid)` | `/v1/onChainEventsByFid`, `/v1/signersByFid` | `Key`s: every key add / remove, and the app that asked for each |
| `app_of(message)`, `app_name(app_fid)` | | which app signed a cast, like or follow |
| `feed(fid, since=, limit=)` | `/v1/linksByFid` + `/v1/castsByFid` | recent casts from everyone `fid` follows, newest first |
| `recommend.suggest(hub, fid)` | `/v1/linksByFid`, `/v1/castsByFid`, `/v1/castsByParent` | people to follow: friends of friends and shared channels, each with its reasons |
| `fid_by_address(address)` | `/v1/onChainIdRegistryEventByAddress` | the fid a custody address owns |
| `signers(fid)` | `/v1/signersByFid` | active keys and `currentUserNonce` |
| `submit(msg)`, `validate(msg)` | `POST /v1/submitMessage`, `/v1/validateMessage` | send a signed message, or just check it |
| `info()` | `/v1/info` | node stats |
| `get(path, params)`, `pages(...)`, `collect(...)` | any | raw JSON, for endpoints not wrapped here |

Everything that lists things is an async iterator, so you can stop early
(`limit=`) or stream through a whole history. Every dataclass keeps the
node's original message in `.raw`.

`decode_key_request(metadata)` decodes the ABI-encoded
`SignedKeyRequestMetadata` from a key ADD event on its own, if you're reading
on-chain events some other way.

## Writing

`phunk.write` adds an app key to an account and posts with it. The key is
**gasless**: a `KEY_ADD` message authorized by the custody key's EIP-712
signature. No transaction, no gas.

```python
from phunk.write import Custody, Writer, add_app_key

custody = Custody.from_mnemonic(phrase)          # test account; never saved
async with Hub("http://localhost:3381") as hub:
    fid, key = await add_app_key(hub, custody)   # key.private_hex: keep it secret
    me = Writer(hub, fid, key)
    h = await me.cast("hello from my own node")
    await me.like(3, "0x...")
    await me.follow(3)
```

| `Writer` method | Message |
|---|---|
| `cast(text, reply_to=, channel=, embeds=)` | `CAST_ADD`, with `@name` turned into mentions |
| `delete_cast(hash)` | `CAST_REMOVE` |
| `like`, `unlike`, `recast`, `unrecast` `(fid, hash)` | `REACTION_ADD` / `REACTION_REMOVE` |
| `follow(fid)`, `unfollow(fid)` | `LINK_ADD` / `LINK_REMOVE` |
| `set_profile(display=, bio=, pfp=, url=, username=)` | `USER_DATA_ADD` |

`add_app_key(..., app_signer=URL)` registers the key under an app account
through that app's signing service, so other clients show "Sent from
<app>". `signer/` is a ready-to-deploy service; see `signer/README.md`.

Messages match the official TypeScript library byte for byte
(`tests/test_write.py` checks against its output). Read the
[tutorial's writing chapter](docs/TUTORIAL.md#10-writing-app-keys-casts-follows)
first: it covers what a seed phrase can do and how to keep it safe.

## Node quirks it handles

- **Glued pages.** Queries that span shards (`castsByParent`,
  `linksByTargetFid`, `castsByMention`) return one sorted page per shard,
  joined. A page is not sorted as a whole.
- **Looping page tokens.** Once one shard runs out, `nextPageToken` can loop
  back to the start. The pager dedupes by message hash and stops when a page
  adds nothing new, so you never loop forever or see a message twice.
- **Cutoffs across shards.** `since=` skips older items and stops after a page
  with nothing new enough, which is safe because each shard's part of a page
  is sorted.
- **Farcaster time.** Message timestamps count from 2021-01-01, not 1970.
  `.time` on every model gives a UTC `datetime`; `to_datetime` and
  `from_datetime` convert by hand.
- **Rate limits.** A token bucket (`rate=`, default 20/s) and a cap on requests
  in flight (`concurrency=`). 429s, 5xx and timeouts retry with backoff.
  404s (and the 400s some nodes send for "no such fid") raise `NotFound`.
- **Private nodes.** `token=` sends `Authorization: Bearer <token>`. A 401
  or 403 raises `NodeError` saying whether the token was missing or wrong,
  and is not retried.

## Command line

```sh
phunk whois dwr
phunk casts dwr -n 5
phunk apps dwr          # which apps signed their last 200 casts
phunk --node http://localhost:3381 info

# writing (pip install 'phunk-client[write]')
phunk key add                      # seed phrase in (hidden), app key saved to ~/.config/phunk
phunk post "hello" --channel https://warpcast.com/~/channel/politics
phunk like 3:0x<hash>
phunk follow dwr
phunk feed --hide warpcast         # who you follow, filtered by app
phunk ui                           # local web client: people to connect with, then casts
```

## Web client

`phunk ui` serves a small web client on `http://127.0.0.1:8008`. Its main
screen is a feed of **people**, not posts: who to connect with, one plain
reason each, their latest casts, and Follow / Say hi / Not now.

* **Reconnect**: people you talked to who moved to another app, went quiet,
  or whom you never followed.
* **Your circle**: friends of friends with many mutual follows.
* **Bridge**: a few mutual follows, but posting from another app or in
  channels you don't use.

The **Casts** tab has the classic feed with the app that signed each cast,
a filter by app, and a **New cast** button. Anyone's people feed can be
looked up by name, read-only.

```sh
phunk ui --as dwr      # read-only: look at anyone's people
phunk ui               # with the key from `phunk key add`: follow, say hi, post
```

Without a key it is read-only and never asks for a seed phrase. It binds
to 127.0.0.1 and refuses cross-origin POSTs. Plain HTML, CSS and JS, no
build step. See [the tutorial](docs/TUTORIAL.md#11-a-web-client-phunk-ui).

A hosted, read-only copy runs at [phunk.social](https://phunk.social): type
a username, and Follow, Say hi and Reply open Farcaster. Inside a Farcaster
client it is a mini app that shows your own people right away and replies
in the client's composer. `phunk ui --public` runs the same mode locally. See [`web/README.md`](web/README.md) to deploy
it on Vercel.

## Which node

The default is `https://haatz.quilibrium.com`, a public Hypersnap node on
port 443 that sends CORS headers. Pass any other node's URL to `Hub(...)`,
for example your own Snapchain at `http://localhost:3381`. On your own
node, raise `rate=` and `concurrency=` (say 150 and 48): a feed is one
request per account followed, so this is what makes it fast.

To run your own read node on a fresh Ubuntu 22.04 server, use
[`scripts/provision.sh`](scripts/provision.sh) and
[`scripts/node.sh`](scripts/node.sh). To keep it for your own apps, put it
behind [`scripts/gateway.sh`](scripts/gateway.sh): HTTPS, a token per app,
a rate limit per token, gzip and a short cache. Then `./node.sh private`
closes the node's own ports. Clients pass the token:

```python
Hub("https://node.example.com", token=os.environ["PHUNK_NODE_TOKEN"], rate=150, concurrency=48)
```

The CLI reads `PHUNK_NODE`, `PHUNK_NODE_TOKEN`, `PHUNK_RATE` and
`PHUNK_CONCURRENCY`. See
[step 9 of the tutorial](docs/TUTORIAL.md#keep-it-for-your-own-apps).

## Examples

- [`examples/quickstart.py`](examples/quickstart.py): profile, casts, follows
- [`examples/which_app.py`](examples/which_app.py): which apps someone uses, from their key history
- [`examples/mutuals.py`](examples/mutuals.py): mutual follows between two people

## Tests

```sh
pip install -e '.[test,write]'
pytest                    # offline, against a fake node
PHUNK_LIVE=1 pytest       # also hits the real default node
```

## License

[MIT](LICENSE)
