# phunk.social

The web client from `phunk ui`, hosted and read-only. Type a Farcaster
username, see that account's people and casts. Follow opens the profile
and Like opens the cast in Farcaster, because the site holds no keys and
can't act for anyone. Casting from phunk is coming soon. Inside a Farcaster client it runs as a mini app
(see below).

One Vercel project with two [services](https://vercel.com/docs/services)
on one domain:

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
     `https://haatz.quilibrium.com`), `PHUNK_RATE` (requests per second, default 20)
     and `PHUNK_TIP_FID` (the fid "Buy me a coffee" tips; unset, there is no button).
     `PHUNK_BASE_RPC` (Base JSON-RPC for the "Voted NS" stamp, default
     `https://mainnet.base.org`; `off` hides it).
   - Later, for the mini app: `FARCASTER_HEADER`, `FARCASTER_PAYLOAD`,
     `FARCASTER_SIGNATURE` (below).
4. **Deploy**. Open the `*.vercel.app` URL and look someone up.

Vercel deploys `main` to production and every other branch as a preview.

## Point phunk.social at it

1. In the project: **Settings → Domains → Add** `phunk.social`, then
   `www.phunk.social` (let Vercel redirect `www` to the bare domain), then
   `miniapp.phunk.social` (the mini app; same site, its own domain).
2. Vercel shows the DNS records to add. Add them where you bought the
   domain, usually:

   | Type | Name | Value |
   |---|---|---|
   | A | `@` | `76.76.21.21` |
   | CNAME | `www` | `cname.vercel-dns.com` |
   | CNAME | `miniapp` | `cname.vercel-dns.com` |

   Use the values Vercel shows if they differ. Remove any other A or AAAA
   record on `@`.
3. Wait for Vercel to show the domain as valid. HTTPS is automatic.

## The mini app

The same site is also a [Farcaster mini app](https://miniapps.farcaster.xyz/)
at `miniapp.phunk.social`. It gets its own domain so its traffic shows
apart from the website's in analytics. Opened inside a Farcaster client,
the page:

- loads the mini app SDK (`@farcaster/miniapp-sdk`, pinned, from esm.sh;
  only inside a client, never on the plain site),
- shows the viewer's own people straight away, from the client's context,
- opens profiles and casts in the client: **Follow** opens the profile,
  **Like** opens the cast (Say hi, Reply and New cast come back with
  casting),
- with `PHUNK_TIP_FID` set, shows **Buy me a coffee** in its own Coffee tab
  (the people list ends with a small card whose button leads there): $1, $37, $69, $420,
  or $10 to $1000 on a slider (round steps, finer at the low end). It calls `sdk.actions.sendToken`, which opens the
  viewer's own wallet with USDC on Base to that fid filled in; they confirm
  or change it there. Outside a client the button opens that profile,
- hides the client's splash screen once the page is up.

A cast with a phunk.social link shows a card with a **See your people**
button that opens `miniapp.phunk.social` (the `fc:miniapp` tag in
`web/head.html`). `MINIAPP_DOMAIN` in `web/build.sh` sets that domain.

### Sign the manifest (once)

Clients trust `/.well-known/farcaster.json` only when a Farcaster account
signs it for the domain. Nothing secret is involved: you sign in the
Farcaster app, and the result is public.

1. Deploy, with `miniapp.phunk.social` pointing at Vercel (above).
2. Open Farcaster's manifest tool, https://farcaster.xyz/~/developers/new,
   and enter `miniapp.phunk.social` (exactly; every domain is a different app).
3. Sign with the account that should own the app, for example the app
   account behind `signer/`. It gives you `header`, `payload` and
   `signature`.
4. In Vercel → **Settings → Environment Variables**, add them as
   `FARCASTER_HEADER`, `FARCASTER_PAYLOAD` and `FARCASTER_SIGNATURE`, then
   redeploy. `web/build.sh` puts them into the manifest.
5. Check `https://miniapp.phunk.social/.well-known/farcaster.json`, then preview the
   app in Farcaster's developer tools.

To change the name, text or images, edit `web/public/.well-known/farcaster.json`
or `web/make_images.py` (then run it). Image sizes: icon 1024×1024 with no
transparency, splash 200×200, embed 3:2, share image 1200×630.

## Analytics

[OneDollarStats](https://onedollarstats.com) counts visits, one dashboard
per domain: `phunk.social` and `miniapp.phunk.social` (add both as sites
there). `web/head.html` loads its script on the hosted site only; the
local `phunk ui` never loads it, and it ignores localhost anyway.

- No cookies. Query strings are dropped before sending, so the usernames
  people look up (`?as=dwr`) are never sent.
- The page also counts actions, by name only, each with `where=web` or
  `where=miniapp` (inside a Farcaster client): `Look up` and `Follow` now;
  `Say hi`, `Reply`, `Like`, `Recast` and `New cast` once casting is back.
- The page's CSP allows `assets.onedollarstats.com` (the script) and
  `collector.onedollarstats.com` (where it sends events), in `vercel.json`.

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
