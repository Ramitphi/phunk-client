# phunk.social

The web client from `phunk ui`, hosted and read-only. Type a Farcaster
username, see that account's people and casts. Follow, Say hi, Like,
Reply and New cast open Farcaster, because the site holds no keys and
can't post for anyone.

| Path | What |
|---|---|
| `vercel.json` (repo root) | Build, routes, headers and the function's limits |
| `web/build.sh` | Copies `src/phunk/static/` into `web/_site/`, which Vercel serves |
| `api/index.py` | Vercel Python function: every `/api/*` read, through `phunk.ui` in public mode |
| `requirements.txt` (repo root) | The function's one dependency, `aiohttp` |

The page and its JS are the same files `phunk ui` serves. When `/api/me`
says `"public": true`, the page sends actions to Farcaster and remembers the
last username in the browser.

## Try it locally

```sh
phunk ui --public          # http://127.0.0.1:8008, the same mode as the site
```

## Deploy on Vercel

This is a second Vercel project, next to the signer's.

1. Vercel → **Add New → Project** → import `Ramitphi/phunk-client`.
2. **Framework Preset: Other**. Leave **Root Directory** empty (the repo
   root). Build and output come from `vercel.json`; don't override them.
3. Optional environment variables:
   - `PHUNK_NODE`: the node to read from (default `https://haatz.quilibrium.com`)
   - `PHUNK_RATE`: requests per second to it (default 20)
4. **Deploy**. Open the `*.vercel.app` URL and look someone up.

Vercel deploys `main` to production and every other branch as a preview.

## Point phunk.social at it

1. In the project: **Settings → Domains → Add** `phunk.social`, then
   `www.phunk.social` (let Vercel redirect `www` to the bare domain).
2. Vercel shows the DNS records to add. Add them where you bought the
   domain, usually:

   | Type | Name | Value |
   |---|---|---|
   | A | `@` | `76.76.21.21` |
   | CNAME | `www` | `cname.vercel-dns.com` |

   Use the values Vercel shows if they differ. Remove any other A or AAAA
   record on `@`.
3. Wait for Vercel to show the domain as valid. HTTPS is automatic.

## How it holds up

- The first look at an account reads the node for about 10 seconds
  (People). The Casts tab reads every account followed, so for someone who
  follows thousands it can take a couple of minutes.
- Successful reads are cached by Vercel's CDN (`s-maxage`), so the next
  visitor asking for the same account gets an instant answer. **Refresh**
  skips that cache.
- Each function instance keeps its own in-memory caches and talks to the
  node at `PHUNK_RATE` requests a second at most. If the public node starts
  refusing, point `PHUNK_NODE` at your own node (`scripts/node.sh`).
- Nothing is stored about visitors. The function doesn't log requests.
