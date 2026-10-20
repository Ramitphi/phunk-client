# phunk.social

The web client from `phunk ui`, hosted and read-only. Type a Farcaster
username, see that account's people and casts. Follow, Say hi, Like,
Reply and New cast open Farcaster, because the site holds no keys and
can't post for anyone. Inside a Farcaster client it runs as a mini app
(see below).

One Vercel project, three [services](https://vercel.com/docs/services),
all on one domain:

| Service | Root | Public paths | What |
|---|---|---|---|
| `signer` | `signer/` | `/api/sign-key-request` | Approves app keys (`signer/README.md`). ASGI `app` in `signer/api/sign_key_request.py` |
| `api` | `.` | `/api/*` (everything else under it) | The read API: ASGI `app` in `api/index.py`, through `phunk.ui` in public mode |
| `web` | `web/` | everything else | The static page, built by `web/build.sh` into `web/_site/` |

No service calls another, so there are no bindings: the browser calls
`api`, and the `phunk` CLI calls `signer`, both over the public URL.

| Path | What |
|---|---|
| `vercel.json` (repo root) | The services, the public routes (most specific first), the page's headers |
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
vercel dev -L              # all three services, routed as on Vercel, on :3000
```

## Deploy on Vercel

1. Vercel → **Add New → Project** → import `Ramitphi/phunk-client`.
2. Leave **Root Directory** empty (the repo root). Vercel reads the
   services from `vercel.json`; don't override build settings.
3. Environment variables (shared by all services):
   - `PHUNK_APP_FID`, and `PHUNK_APP_CUSTODY_KEY` or `PHUNK_APP_MNEMONIC`:
     the **dedicated app account** the signer approves keys for (never a
     main or personal account; see `signer/README.md`). Mark them sensitive.
   - Optional: `PHUNK_NODE` (node to read from, default
     `https://haatz.quilibrium.com`) and `PHUNK_RATE` (requests per second, default 20).
   - Later, for the mini app: `FARCASTER_HEADER`, `FARCASTER_PAYLOAD`,
     `FARCASTER_SIGNATURE` (below).
4. **Deploy**. Open the `*.vercel.app` URL and look someone up, and check
   `GET /api/sign-key-request` shows the app fid.

Then keys can be approved through the same domain:

```sh
phunk key add --app-signer https://phunk.social/api/sign-key-request
```

If the signer already runs as its own Vercel project, retire it once this
one works (and update any `PHUNK_APP_SIGNER` that points at it).

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
