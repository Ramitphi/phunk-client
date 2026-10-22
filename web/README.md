# phunk.social

The web client from `phunk ui`, hosted and read-only. Type a Farcaster
username, see that account's people and casts. Follow, Say hi, Like,
Reply and New cast open Farcaster, because the site holds no keys and
can't post for anyone. Inside a Farcaster client it runs as a mini app at
`miniapp.phunk.social` (see below).

One Vercel project with two [services](https://vercel.com/docs/services).
The same build answers on two domains: `phunk.social` (the site) and
`miniapp.phunk.social` (the mini app).

| Service | Root | Public paths | What |
|---|---|---|---|
| `api` | `.` | `/api/*` | The read API: ASGI `app` in `api/index.py`, through `phunk.ui` in public mode |
| `web` | `web/` | everything else | The static page, built by `web/build.sh` into `web/_site/` |

The page calls `api` from the browser, so there are no bindings. The app key
signer is a separate Vercel project at `sign.phunk.social`
(`signer/README.md`), so its custody key never sits in this project.

| Path | What |
|---|---|
| `vercel.json` (repo root) | The two services, their public routes, and the page's headers |
| `web/build.sh` | Copies `src/phunk/static/` into `web/_site/` with the head tags, images and manifest |
| `requirements.txt` (repo root) | The `api` service's one dependency, `aiohttp` |
| `web/head.html` | Link previews and the Farcaster mini app embed, put into the page at build |
| `web/public/` | Images and `/.well-known/farcaster.json`, the mini app manifest |
| `web/make_images.py` | Redraws the images (dev only: Playwright and Pillow) |

The page and its JS are the same files `phunk ui` serves. When `/api/me`
says `"public": true`, the page sends actions to Farcaster and remembers the
last username in the browser.

## Try it locally

```sh
phunk ui --public          # http://127.0.0.1:8008, the same mode as the site
vercel dev -L              # both services, routed as on Vercel, on :3000
```

## Deploy on Vercel

1. Vercel → **Add New → Project** → import `Ramitphi/phunk-client`.
2. Leave **Root Directory** empty (the repo root). Vercel reads the
   services from `vercel.json`; don't override build settings.
3. Environment variables (none are secret; never put the signer's key here):
   - Optional: `PHUNK_NODE` (node to read from, default
     `https://haatz.quilibrium.com`) and `PHUNK_RATE` (requests per second, default 20).
   - Optional: `SITE_DOMAIN` (default `phunk.social`) and `MINIAPP_DOMAIN`
     (default `miniapp.phunk.social`), only if you use other domains.
   - Later, for the mini app: `FARCASTER_HEADER`, `FARCASTER_PAYLOAD`,
     `FARCASTER_SIGNATURE` (below).
4. **Deploy**. Open the `*.vercel.app` URL and look someone up.

Vercel deploys `main` to production and every other branch as a preview.

## Point phunk.social and miniapp.phunk.social at it

1. In the project: **Settings → Domains → Add** `phunk.social`, then
   `www.phunk.social` (let Vercel redirect `www` to the bare domain).
2. **Add** `miniapp.phunk.social` too, connected to the same production
   deployment (no redirect). It serves the same page and API.
3. Vercel shows the DNS records to add. Add them where you bought the
   domain, usually:

   | Type | Name | Value |
   |---|---|---|
   | A | `@` | `76.76.21.21` |
   | CNAME | `www` | `cname.vercel-dns.com` |
   | CNAME | `miniapp` | `cname.vercel-dns.com` |

   Use the values Vercel shows if they differ. Remove any other A or AAAA
   record on `@`.
4. Wait for Vercel to show the domain as valid. HTTPS is automatic. Check
   `https://phunk.social` and `https://miniapp.phunk.social`.

## The mini app

The [Farcaster mini app](https://miniapps.farcaster.xyz/) lives at
`miniapp.phunk.social`. A mini app is tied to one domain by its signed
manifest, so the subdomain is the app and `phunk.social` stays the plain
site. Both serve the same page. Opened inside a Farcaster client, it:

- loads the mini app SDK (`@farcaster/miniapp-sdk`, pinned, from esm.sh;
  only inside a client, never on the plain site),
- shows the viewer's own people straight away, from the client's context,
- opens profiles, casts and the composer in the client: **Follow** opens
  the profile, **Say hi** and **Reply** open the composer as a reply,
  **New cast** opens the composer,
- hides the client's splash screen once the page is up.

A cast with a link to either domain shows a card with a **See your people**
button (the `fc:miniapp` tag in `web/head.html`). The button opens
`https://miniapp.phunk.social/`, so the client finds the signed manifest.

### Sign the manifest (once)

Clients trust `/.well-known/farcaster.json` only when a Farcaster account
signs it for the domain. Nothing secret is involved: you sign in the
Farcaster app, and the result is public.

1. Deploy, with miniapp.phunk.social pointing at Vercel (above).
2. Open Farcaster's manifest tool, https://farcaster.xyz/~/developers/new,
   and enter `miniapp.phunk.social` (exactly; every domain and subdomain is
   a different app).
3. Sign with the account that should own the app, for example the app
   account behind `signer/`. It gives you `header`, `payload` and
   `signature`.
4. In Vercel → **Settings → Environment Variables**, add them as
   `FARCASTER_HEADER`, `FARCASTER_PAYLOAD` and `FARCASTER_SIGNATURE`, then
   redeploy. `web/build.sh` puts them into the manifest.
5. Check `https://miniapp.phunk.social/.well-known/farcaster.json`: its
   `homeUrl` and `canonicalDomain` should name `miniapp.phunk.social`, and
   `accountAssociation` should be filled in. Then preview the app in
   Farcaster's developer tools and cast a link to `https://phunk.social` to
   see the card.

`phunk.social/.well-known/farcaster.json` serves the same file. It is signed
for `miniapp.phunk.social`, so clients don't treat `phunk.social` as an app.

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
