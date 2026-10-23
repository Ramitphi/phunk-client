# phunk.social

The web client from `phunk ui`, hosted. Type a Farcaster username, see that
account's people and casts. The site holds no keys: on the plain site,
Follow opens the profile and Open opens the cast in Farcaster. Inside a
Farcaster client it runs as a mini app (see below), where people can
approve phunk once and then follow and like from it. Casting from phunk is
coming soon.

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
| `web/make_screenshots.py` | Retakes the three store screenshots from the running app (dev only) |

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
     and `PHUNK_TIP_FID` (the fid the money gun in the Tip tab tips; unset, there is no button).
     `PHUNK_BASE_RPC` (Base JSON-RPC for the "Voted NS" stamp, default
     `https://mainnet.base.org`; `off` hides it).
   - For your own node behind `scripts/gateway.sh`: `PHUNK_NODE=https://<gateway-domain>`,
     `PHUNK_NODE_TOKEN` (from `sudo ./gateway.sh token add phunk-social`; mark it
     Sensitive), `PHUNK_RATE=150` and `PHUNK_CONCURRENCY=48`.
   - For follow and like in the mini app: `PHUNK_SIGNER_URL`
     ([below](#follow-and-like-optional)).
   - Later, for the mini app: `FARCASTER_HEADER`, `FARCASTER_PAYLOAD`,
     `FARCASTER_SIGNATURE` (below), and for notifications a connected
     Upstash Redis (`KV_REST_API_URL`, `KV_REST_API_TOKEN`).
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
- opens profiles and casts in the client: **Follow** opens the profile
  and **Open** opens the cast, until the viewer connects phunk (below);
  Say hi, Reply and New cast come back with casting,
- with `PHUNK_SIGNER_URL` set, lets the viewer **follow and like** from
  phunk after approving it once in their Farcaster app,
- with `PHUNK_TIP_FID` set, shows a **money gun** in its own Tip tab
  (the people list and the You screen end with a small card whose button leads there): $1, $37, $69, $420,
  or $10 to $1000 on a slider (round steps, finer at the low end). It calls `sdk.actions.sendToken`, which opens the
  viewer's own wallet with USDC on Base to that fid filled in; they confirm
  or change it there. Outside a client the button opens that profile,
- once someone's people are on screen, offers **Add phunk**, which keeps it
  in their apps and turns on the weekly note (asked at most twice, never on
  the plain site),
- gives taps the launch video's cues: a ring where you tap, confetti on a
  follow, hearts on a like, bills out of the money gun, tabs that slide
  in. Each one has a haptic too: the client's own (`sdk.haptics`, where
  it has them), or the phone's vibration on the plain site. "Reduce
  motion" turns the movement off and keeps the haptics,
- hides the client's splash screen once the page is up.

A cast with a phunk.social link shows a card with a **See your people**
button that opens `miniapp.phunk.social` (the `fc:miniapp` tag in
`web/head.html`). `MINIAPP_DOMAIN` in `web/build.sh` sets that domain.

### Follow and like (optional)

With a signer set up, people can follow and like right in the mini app.
Their first Follow or Like asks them to approve phunk once in their
Farcaster app. After that the page signs follows and likes itself, and the
site passes them to the node.

| Piece | What |
|---|---|
| The page | Makes an Ed25519 key in the browser and keeps it there (localStorage). The private key never leaves the phone |
| `src/phunk/static/fc.js` | Builds and signs follow, unfollow, like and unlike in the page; `tests/js/fc_test.mjs` checks it byte for byte against the official library's messages |
| `POST /api/connect` | Asks the signer (`signer/`) to approve the public key for the app account, then opens a signed key request with Farcaster. Answers the approval link |
| `GET /api/connect?token=` | Where that request stands: `completed` once the key is on their account |
| `POST /api/submit` | Passes a signed message to the node. Only follows, unfollows, likes and unlikes get through; the node checks the signature and that the key belongs to the account |
| `src/phunk/connect.py` | All of the above, with offline tests in `tests/test_web.py` |

The Farcaster endpoints behind `/api/connect`:

```sh
POST https://api.farcaster.xyz/v2/signed-key-requests   {key, requestFid, signature, deadline}
GET  https://api.farcaster.xyz/v2/signed-key-request?token=0x...
```

No seed phrase and no custody key: people approve in their own Farcaster
app. Other clients show these follows and likes as sent from the app
account. People can remove the key any time in Farcaster's settings;
**Forget** on the You screen drops it from the phone.

To turn it on:

1. Make a **dedicated app account** for phunk, from a fresh wallet that
   holds nothing else. Never a main or personal account: its key will sit
   on a server.
2. Deploy the signer as its own Vercel project, with
   `PHUNK_APP_FID=<app account fid>` and the app account's key
   (`signer/README.md`). Type the key into Vercel yourself and mark it
   sensitive. It never goes in this project, the repo or a chat.
3. In this project, set
   `PHUNK_SIGNER_URL=https://sign.phunk.social/api/sign-key-request`
   and redeploy.
4. Check `GET /api/me` shows `"can_connect": true`. Then open the mini app
   on a phone, tap Follow on someone, approve, and look for the follow in
   Farcaster.

Without `PHUNK_SIGNER_URL` the site works as before: `/api/connect` and
`/api/submit` answer 503, and Follow opens the profile.

Each instance allows, per IP, 10 approvals per 10 minutes and 60 submits a
minute. For a hard cap, add a Vercel firewall rule on `/api/connect` and
`/api/submit`.

Cost: the signer and the relay cost nothing beyond Vercel. Approving puts
the key on chain from the person's account. Unless the request is
sponsored, their Farcaster app may charge them a small fee for that; check
it on your first approval.

### Notifications (optional)

People who add the mini app can get one short note a week: who they talk to
but don't follow, or who moved to another app. Nothing is sent to anyone
who hasn't added it.

How it fits together:

| Piece | What |
|---|---|
| `webhookUrl` in the manifest | Where a Farcaster client POSTs its signed add / remove events |
| `POST /api/webhook` | Checks the signature, checks the key is one of that account's live keys on the node, then keeps or drops their token |
| Upstash Redis | Holds `{fid: {url, token}}` and when each account last got a note |
| `GET /api/cron/digest` | Tuesdays at 16:00 UTC (`crons` in `vercel.json`); Vercel sends `CRON_SECRET`, and nothing else may call it |
| `src/phunk/notify.py` | All of the above, with `tests/test_notify.py` offline |

To turn it on:

1. In the Vercel project: **Storage → Create → Upstash Redis** (the free
   tier is plenty; one small hash per account). Connect it to the project,
   which sets `KV_REST_API_URL` and `KV_REST_API_TOKEN`.
2. Redeploy. The manifest already points at `/api/webhook`.
3. Check `POST /api/webhook` answers 400 (not 503) for a bad body: 503
   means it can't see the Redis.

Without the Redis the site works exactly as before; the webhook answers
503 and no one is subscribed. To stop notifications, remove the Redis, or
drop `crons` from `vercel.json` to keep subscriptions but send nothing.

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

The three screenshots in the app listing come from the real app:

```sh
phunk ui --public               # in another terminal
python web/make_screenshots.py  # -> web/public/img/screen-*.png
```

## Analytics

[OneDollarStats](https://onedollarstats.com) counts visits, one dashboard
per domain: `phunk.social` and `miniapp.phunk.social` (add both as sites
there). `web/head.html` loads its script on the hosted site only; the
local `phunk ui` never loads it, and it ignores localhost anyway.

- No cookies. Query strings are dropped before sending, so the usernames
  people look up (`?as=dwr`) are never sent.
- The page sends the product events below (`track()` in
  `src/phunk/static/app.js`). Every event has `where`: `web`, or `miniapp`
  inside a Farcaster client. Props are labels and coarse buckets only:
  never a username, fid or cast text.
- The page's CSP allows `assets.onedollarstats.com` (the script) and
  `collector.onedollarstats.com` (where it sends events), in `vercel.json`.
- The script drops a visit as a headless browser when it says "Chrome" but
  has no `window.chrome`. Android WebViews are like that, and Farcaster
  clients open the mini app in one. So `web/head.html` sets an empty
  `window.chrome` on Android before the script loads; without it, the
  `miniapp.phunk.social` dashboard misses every Android visit. Other bot
  checks (webdriver, headless user agents) still apply.

### Product events

Buckets: `took` is `<5s`, `5-15s`, `15-60s` or `60s+`; `cards` and `casts`
are `0`, `1-5`, `6-15` or `16+`; `slot` is a card's place on the page,
`1-3`, `4-10` or `11+`.

| Event | When | Props |
|---|---|---|
| `Visit` | The page is up | `start`: `miniapp` (signed in to a client), `link` (a `?as=` link), `returning` (a remembered username) or `new` |
| `Look up` | The username box is sent | `by`: `username`, `fid` or `empty` |
| `People loaded` | The people feed arrives | `took`, `cards`, `self` (the viewer's own people: `yes`/`no`), `refresh` |
| `People failed` | It doesn't | `reason`: `not found`, `bad lookup`, `server`, `network` or a status; `took` |
| `Follow` | Follow on a card (opens the profile, or in a connected mini app, follows) | `kind` (`reconnect`, `circle`, `bridge`), `slot` |
| `Followed` | The follow went through (mini app, connected) | `kind`, `slot` |
| `Say hi` | Say hi on a card | `kind`, `slot` |
| `Not now` | A card is dismissed | `kind`, `slot` |
| `Casts opened` | The Casts tab | |
| `Casts loaded` | The feed arrives | `took`, `casts`, `window` (`6h` to `168h`) |
| `Casts failed` | It doesn't | `reason`, `took` |
| `Window` | A time window is picked | `window` |
| `Load more` | More casts | `mode`: `next page` or `wider window` |
| `App filter` | An app is shown or hidden | `change`: `show`, `hide`, `show all`, `hide all` |
| `Open` | Open on a cast (opens it in Farcaster) | |
| `Liked`, `Unliked` | Like / unlike went through (mini app, connected) | |
| `Connect shown` | The one-time approval sheet opens (Follow or Like before phunk is connected) | |
| `Connect skipped` | Not now on that sheet | |
| `Connect done` | The person approved phunk's key in Farcaster | |
| `Connect failed` | Starting or finishing the approval failed | `reason` |
| `Disconnect` | Forget on the You screen (drops the key from this phone) | |
| `Reply` | Reply on a cast (opens it in Farcaster) | |
| `New cast` | The composer opens | |
| `Casting soon` | New cast while casting is off: demand for it | |
| `Add phunk` | Add phunk, in the mini app (adds it to the viewer's apps) | |
| `Tip tapped` | A tip amount (the Tip tab or card) | `amount` in dollars: a button (`1`, `37`, `69`, `420`) or the slider (`10` to `1000`) |
| `Tip sent` | The wallet confirms | `amount` |
| `Tip failed` | Cancelled or failed | `amount`, `reason`: `cancelled`, `send failed`, `no wallet` |
| `Tip profile` | The tip button outside a client (opens the profile) | |
| `Refresh` | A refresh button | `pane`: `people` or `casts` |
| `Back to me` | Back to the viewer's own people | |
| `Theme` | The theme toggle | `theme` |

Useful funnels: `Visit` → `Look up` → `People loaded` → `Follow`;
`Connect shown` → `Connect done` → `Followed`; and
`Follow` / `Not now` split by `kind` and `slot`, to see which suggestions
work. `People failed` and `Casts failed` with `took` show when the node is
slow or down.

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
- **Refresh** re-reads the node only for results over a minute old, so a
  script sending `refresh=1` in a loop gets the cached answer. `/api/me`
  doesn't say which node the site reads from.

## Your own node, fast and private

The site holds a token for your node, so the node itself can refuse
everyone else (`scripts/gateway.sh`, then `./node.sh private`; see
[the tutorial](../docs/TUTORIAL.md#keep-it-for-your-own-apps)). The
gateway caps the token at 300 requests a second, so even a flood of
visitors can't take the node down; they get slower answers instead.

To make it fast:

1. Set `PHUNK_RATE=150` and `PHUNK_CONCURRENCY=48`. A feed reads every
   account followed, one request each, so this is what makes Casts quick.
2. Run the function next to the node: in Vercel, **Settings → Functions →
   Function Region**, pick the region closest to the server (for example
   `fra1` for a server in Germany, `iad1` for US East).
3. In Vercel → **Firewall**, add a rate limit rule for `/api/*` (say 60
   requests a minute per IP). Each lookup of a new account costs the node
   about 150 requests, so this keeps one visitor from asking for thousands.
- Nothing is stored about visitors. The function doesn't log requests.
