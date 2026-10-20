# phunk-client

Read Farcaster straight from a Snapchain / Hypersnap node. No API key, no
Neynar, no indexer: just the node's HTTP API, with the paging quirks handled
for you.

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

New to reading from a node? **[Start with the tutorial](docs/TUTORIAL.md).**

## Install

```sh
pip install git+https://github.com/Ramitphi/phunk-client
```

Python 3.10+. The only dependency is `aiohttp`.

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
| `info()` | `/v1/info` | node stats |
| `get(path, params)`, `pages(...)`, `collect(...)` | any | raw JSON, for endpoints not wrapped here |

Everything that lists things is an async iterator, so you can stop early
(`limit=`) or stream through a whole history. Every dataclass keeps the
node's original message in `.raw`.

`decode_key_request(metadata)` decodes the ABI-encoded
`SignedKeyRequestMetadata` from a key ADD event on its own, if you're reading
on-chain events some other way.

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

## Command line

```sh
phunk whois dwr
phunk casts dwr -n 5
phunk apps dwr          # which apps signed their last 200 casts
phunk --node http://localhost:3381 info
```

## Which node

The default is `https://haatz.quilibrium.com`, a public Hypersnap node on
port 443 that sends CORS headers. Pass any other node's URL to `Hub(...)`,
for example your own Snapchain at `http://localhost:3381`.

To run your own read node on a fresh Ubuntu 22.04 server, use
[`scripts/provision.sh`](scripts/provision.sh) and
[`scripts/node.sh`](scripts/node.sh). See
[step 9 of the tutorial](docs/TUTORIAL.md#9-run-your-own-node).

## Examples

- [`examples/quickstart.py`](examples/quickstart.py): profile, casts, follows
- [`examples/which_app.py`](examples/which_app.py): which apps someone uses, from their key history
- [`examples/mutuals.py`](examples/mutuals.py): mutual follows between two people

## Tests

```sh
pip install -e '.[test]'
pytest                    # offline, against a fake node
PHUNK_LIVE=1 pytest       # also hits the real default node
```

## License

[MIT](LICENSE)
