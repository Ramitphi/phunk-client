# phunk.social

The web client from `phunk ui`, hosted and read-only. Type a Farcaster
username, see that account's people and casts. Follow, Say hi, Like,
Reply and New cast open Farcaster, because the site holds no keys and
can't post for anyone. Inside a Farcaster client it runs as a mini app
(see below).

| Path | What |
|---|---|
| `vercel.json` (repo root) | Build, routes, headers and the function's limits |
| `web/build.sh` | Copies `src/phunk/static/` into `web/_site/`, which Vercel serves |
| `api/index.py` | Vercel Python function: every `/api/*` read, through `phunk.ui` in public mode |
| `requirements.txt` (repo root) | The function's one dependency, `aiohttp` |
| `web/head.html` | Link previews and the Farcaster mini app embed, put into the page at build |
| `web/public/` | Images and `/.well-known/farcaster.json`, the mini app manifest |
| `web/make_images.py` | Redraws the images (dev only: Playwright and Pillow) |

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

## The mini app

phunk.social is also a [Farcaster mini app](https://miniapps.farcaster.xyz/).
Opened inside a Farcaster client, the page:

- loads the mini app SDK (`@farcaster/miniapp-sdk`, pinned, from esm.sh;
  only inside a client, never on the plain site),
- shows the viewer's own people straight away, from the client's context,
- opens profiles, casts and the composer in the client: **Follow** opens
  the profile, **Say hi** and **Reply** open the composer as a reply,
  **New cast** opens the composer,
- hides the client's splash screen once the page is up.

A cast with a phunk.social link shows a card with a **See your people**
button (the `fc:miniapp` tag in `web/head.html`).

### Sign the manifest (once)

Clients trust `/.well-known/farcaster.json` only when a Farcaster account
signs it for the domain. Nothing secret is involved: you sign in the
Farcaster app, and the result is public.

1. Deploy, with phunk.social pointing at Vercel (above).
2. Open Farcaster's manifest tool, https://farcaster.xyz/~/developers/new,
   and enter `phunk.social` (exactly; `www.phunk.social` is a different app).
3. Sign with the account that should own the app, for example the app
   account behind `signer/`. It gives you `header`, `payload` and
   `signature`.
4. In Vercel → **Settings → Environment Variables**, add them as
   `FARCASTER_HEADER`, `FARCASTER_PAYLOAD` and `FARCASTER_SIGNATURE`, then
   redeploy. `web/build.sh` puts them into the manifest.
5. Check `https://phunk.social/.well-known/farcaster.json`, then preview the
   app in Farcaster's developer tools.

To change the name, text or images, edit `web/public/.well-known/farcaster.json`
or `web/make_images.py` (then run it). Image sizes: icon 1024×1024 with no
transparency, splash 200×200, embed 3:2, share image 1200×630.

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
