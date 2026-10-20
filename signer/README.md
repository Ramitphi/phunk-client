# phunk signer

A tiny service that approves app keys for the phunk app account, so casts
posted with phunk show "Sent from <app username>" in other clients.

## Why it exists

Every app key's `KEY_ADD` carries a `SignedKeyRequest`: the **app** account's
custody key signs (app fid, new public key, deadline). Clients read that app
fid to show where a cast came from. An open-source CLI can't ship the app's
custody key, so this service holds it and signs requests.

It only ever sees the new **public** key. It never sees a user's seed phrase
or custody key, and its approval alone can't add a key to anyone's account:
the user's own custody key still has to sign the `KEY_ADD`. `phunk` checks
every approval against the app account's custody address on the node before
using it.

## API

```sh
curl -s https://<your-signer>/api/sign-key-request                     # which app, which signer
curl -s -X POST https://<your-signer>/api/sign-key-request \
     -H 'Content-Type: application/json' -d '{"key": "0x<64 hex chars>"}'
# {"metadata": "0x...", "request_fid": 123, "request_signer": "0x...", "deadline": 190000000}
```

An approval is valid for one hour.

## Deploy on Vercel

It deploys as the `signer` service of the phunk.social project (repo root
`vercel.json`, see `web/README.md`), at `/api/sign-key-request` on the same
domain. Set the variables below in that project.

Or on its own, as before:

1. New project from this repo, **Root Directory: `signer`**. No build step.
2. Environment variables (Settings → Environment Variables), marked sensitive:
   - `PHUNK_APP_FID`: the app account's fid
   - `PHUNK_APP_CUSTODY_KEY`: its custody private key (`0x...`), **or**
     `PHUNK_APP_MNEMONIC`: its seed phrase
3. Deploy, then optionally add a domain such as `sign.example.com`.
4. Check it: `GET /api/sign-key-request` should show the app fid and the
   custody address the node has for it.

Then:

```sh
phunk key add --app-signer https://<your-signer>/api/sign-key-request
```

**Never use your main or personal account as the app account.** Its
custody key would sit on a server. Create a dedicated app account from a
fresh wallet that holds nothing else, and use only that one here. Anyone can
get an approval from a public signer, which is the point: any phunk user
can add a key under the app. Add rate limiting (for example Vercel's
firewall rules) if it gets abused.
