# phunk-client

A small async Python client for reading and writing Farcaster straight
through a Snapchain / Hypersnap node over HTTP, plus a tutorial. MIT
licensed, public.

## Layout

| Path | What |
|---|---|
| `src/phunk/hub.py` | `Hub`: HTTP, rate limit, retries, the pager (`pages`), and the typed methods |
| `src/phunk/models.py` | Dataclasses (`User`, `Cast`, `Reaction`, `Follow`, `Key`); each keeps `.raw` |
| `src/phunk/proto.py` | Minimal protobuf encoder for the message types a client sends; matches ts-proto byte for byte |
| `src/phunk/write.py` | `AppKey`, `Custody`, `add_app_key` (gasless KEY_ADD), `revoke_key` / `revoke_key_with_custody` (KEY_REMOVE), `Writer` (cast, react, follow). Optional extra `[write]` |
| `src/phunk/keys.py` | `decode_key_request`: ABI-decodes `SignedKeyRequestMetadata` (which app asked for a key) |
| `src/phunk/onchain.py` | `holders`: which addresses hold an NFT on Base (Multicall3 `balanceOf`), for the "Voted NS" stamp on people cards |
| `src/phunk/notify.py` | Mini app notifications: signed webhook events (who added the app), the token store (`MemoryStore`, `RedisStore`) and the weekly digest |
| `src/phunk/timeutil.py` | Farcaster epoch (2021-01-01) conversions |
| `src/phunk/recommend.py` | People to follow: `score` (pure) and `suggest` (reads the node) |
| `src/phunk/people.py` | The people feed (main screen of `phunk ui`): reconnect / circle / bridge cards. `build()` is pure; `people_feed()` reads the node |
| `src/phunk/path.py` | Who could introduce two people: `paths()` finds the shortest mutual-follow paths, up to 3 steps, by reading one follow list per account either end follows (meet in the middle). `find()` and `rank()` are pure |
| `src/phunk/ui.py`, `src/phunk/static/` | `phunk ui`: local aiohttp web client and its plain HTML/CSS/JS. `static/fc.js` builds and signs follows and likes in the page (mini app) |
| `src/phunk/connect.py` | Follow and like in the mini app: `start` / `status` (a signed key request through `signer/` and Farcaster's API) and `check` (the relay passes only follows and likes) |
| `api/index.py`, `vercel.json`, `web/` | phunk.social: one Vercel project with two services (`web` static page, `api` read API), `phunk ui` in public (read-only) mode plus `/api/connect` and `/api/submit` for follow and like, also a Farcaster mini app at miniapp.phunk.social (manifest and images in `web/public/`), with OneDollarStats analytics on the hosted page only; see `web/README.md` |
| `src/phunk/__main__.py` | The `phunk` CLI |
| `docs/TUTORIAL.md` | Step-by-step tutorial; every snippet should run as written |
| `site/` | The microsite: `build.py` (stdlib only) turns `docs/` and `site/content/` into `site/_site/`; see `site/README.md` |
| `signer/` | The app's signing service, its own Vercel project at sign.phunk.social (ASGI `app`, entrypoint in `signer/pyproject.toml`): approves app keys so casts show "Sent from <app>". Holds the app's custody key in env vars only, kept out of the phunk.social project |
| `examples/` | Runnable scripts referenced from the README and tutorial |
| `scripts/` | `server.sh` (run on your own computer: `hetzner`, `digitalocean` or `aws` rents a server and 2 TB disk with that provider's CLI, `user@host` takes one you have; then runs `provision.sh` on it and mounts the data disk; each provider is a set of `<name>_check/_plan/_create/_ip/_destroy` functions), `hetzner.sh` (old name for `server.sh hetzner`), `provision.sh` (fresh Ubuntu server), `node.sh` (Snapchain read node, wraps the official `snapchain.sh`; `private` keeps its APIs on 127.0.0.1) and `gateway.sh` (nginx on 443 in front of it: a token per app, a rate limit per token, gzip, a short cache); keep them `shellcheck`-clean and safe to re-run |
| `tests/` | `test_hub.py`, `test_keys.py`, `test_write.py`, `test_ui.py`, `test_web.py`, `test_notify.py` run offline (fake node; `conftest.py` has the fake client and Redis); `test_live.py` hits the real node (writes are validate-only); `tests/js/fc_test.mjs` checks `fc.js` against the vectors (run by `test_web.py` when `node` is there) |
| `tests/vectors/` | Reference messages from the official TS library (`make_write_vectors.mjs`) |

## Commands

```sh
pip install -e '.[test,write]'
pytest                  # offline
PHUNK_LIVE=1 pytest     # also against https://haatz.quilibrium.com
```

CI (`.github/workflows/ci.yml`) runs the offline tests on Python 3.10 and 3.13.
`.github/workflows/pages.yml` builds the site (`python site/build.py`) and
deploys it to GitHub Pages from `main`.

## Rules

- **Public repo.** Never add personal data, private notes, plans, or code
  from other projects. Examples and docs use well-known public accounts
  (dwr, fid 3; v; vitalik.eth), not the maintainer's.
- One runtime dependency: `aiohttp`. Writing needs `pynacl`, `blake3` and
  `eth-account`, kept in the optional `[write]` extra and imported only by
  `phunk.write`. Don't add more without a strong reason.
- Never store, log or print a seed phrase or custody key. Never submit a
  message from tests; use `validate()` for live checks.
- **Hard safety rule:** never ask the maintainer (or anyone) for the seed
  phrase or custody key of a main or personal account, and never suggest
  testing with one or putting one in a server's environment. Only use
  throwaway test accounts, or an account created only for that job (for
  example a dedicated app account for `signer/`). If a step would need a
  main account's key, stop and propose a dedicated account instead.
- Any change to message encoding must keep `tests/test_write.py` passing
  against `tests/vectors/write.json`.
- Support Python 3.10+.
- Every list method goes through `Hub.pages()`, which dedupes by hash and
  stops on a page with nothing new. Cross-shard endpoints (`castsByParent`,
  `linksByTargetFid`, `castsByMention`) glue one sorted page per shard and
  their page token can loop, so never page them with a plain `while token:`.
- Message timestamps are Farcaster time; on-chain event timestamps are Unix.
- New behaviour gets an offline test against the fake node in `tests/test_hub.py`.
- When you change the API, update the README table and the tutorial, and run
  the tutorial snippets against the live node.
- Docs style: plain, short sentences; show the raw endpoint, then the
  library call.
