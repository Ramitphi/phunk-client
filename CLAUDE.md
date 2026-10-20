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
| `src/phunk/write.py` | `AppKey`, `Custody`, `add_app_key` (gasless KEY_ADD), `Writer` (cast, react, follow). Optional extra `[write]` |
| `src/phunk/keys.py` | `decode_key_request`: ABI-decodes `SignedKeyRequestMetadata` (which app asked for a key) |
| `src/phunk/timeutil.py` | Farcaster epoch (2021-01-01) conversions |
| `src/phunk/recommend.py` | People to follow: `score` (pure) and `suggest` (reads the node) |
| `src/phunk/people.py` | The people feed (main screen of `phunk ui`): reconnect / circle / bridge cards. `build()` is pure; `people_feed()` reads the node |
| `src/phunk/ui.py`, `src/phunk/static/` | `phunk ui`: local aiohttp web client and its plain HTML/CSS/JS |
| `api/index.py`, `vercel.json`, `web/` | phunk.social: `phunk ui` in public (read-only) mode on Vercel; see `web/README.md` |
| `src/phunk/__main__.py` | The `phunk` CLI |
| `docs/TUTORIAL.md` | Step-by-step tutorial; every snippet should run as written |
| `site/` | The microsite: `build.py` (stdlib only) turns `docs/` and `site/content/` into `site/_site/`; see `site/README.md` |
| `signer/` | The app's signing service (Vercel Python function): approves app keys so casts show "Sent from <app>". Holds the app's custody key in env vars only |
| `examples/` | Runnable scripts referenced from the README and tutorial |
| `scripts/` | `provision.sh` (fresh Ubuntu server) and `node.sh` (Snapchain read node, wraps the official `snapchain.sh`); keep them `shellcheck`-clean and safe to re-run |
| `tests/` | `test_hub.py`, `test_keys.py`, `test_write.py`, `test_ui.py`, `test_web.py` run offline (fake node); `test_live.py` hits the real node (writes are validate-only) |
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
