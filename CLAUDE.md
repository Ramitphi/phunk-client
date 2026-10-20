# phunk-client

A small async Python client for reading Farcaster straight from a Snapchain /
Hypersnap node over HTTP, plus a tutorial. MIT licensed, public.

## Layout

| Path | What |
|---|---|
| `src/phunk/hub.py` | `Hub`: HTTP, rate limit, retries, the pager (`pages`), and the typed methods |
| `src/phunk/models.py` | Dataclasses (`User`, `Cast`, `Reaction`, `Follow`, `Key`); each keeps `.raw` |
| `src/phunk/keys.py` | `decode_key_request`: ABI-decodes `SignedKeyRequestMetadata` (which app asked for a key) |
| `src/phunk/timeutil.py` | Farcaster epoch (2021-01-01) conversions |
| `src/phunk/__main__.py` | The `phunk` CLI |
| `docs/TUTORIAL.md` | Step-by-step tutorial; every snippet should run as written |
| `examples/` | Runnable scripts referenced from the README and tutorial |
| `scripts/` | `provision.sh` (fresh Ubuntu server) and `node.sh` (Snapchain read node, wraps the official `snapchain.sh`); keep them `shellcheck`-clean and safe to re-run |
| `tests/` | `test_hub.py` and `test_keys.py` run offline (fake node); `test_live.py` hits the real node |

## Commands

```sh
pip install -e '.[test]'
pytest                  # offline
PHUNK_LIVE=1 pytest     # also against https://haatz.quilibrium.com
```

CI (`.github/workflows/ci.yml`) runs the offline tests on Python 3.10 and 3.13.

## Rules

- **Public repo.** Never add personal data, private notes, plans, or code
  from other projects. Examples and docs use well-known public accounts
  (dwr, fid 3; v; vitalik.eth), not the maintainer's.
- One runtime dependency: `aiohttp`. Don't add more without a strong reason.
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
