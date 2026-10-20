// Reference messages from the official TypeScript library, for tests/test_write.py.
//
//   npm i @farcaster/hub-nodejs@0.17.0 viem
//   node make_write_vectors.mjs > write.json
//
// The keys below are fixed TEST keys, not real accounts. Ed25519 and
// RFC 6979 ECDSA signatures are deterministic, so phunk must reproduce
// every byte.
import * as h from "@farcaster/hub-nodejs";
import { privateKeyToAccount } from "viem/accounts";

const CUSTODY = "0x" + "11".repeat(32);
const ED = new Uint8Array(32).fill(0x22);
const FID = 1234;
const TS = 183000000; // fixed past Farcaster time
const DEADLINE = 190003600; // fixed future deadline
const opts = { fid: FID, network: h.FarcasterNetwork.MAINNET, timestamp: TS };
const signer = new h.NobleEd25519Signer(ED);
const eip = new h.ViemLocalEip712Signer(privateKeyToAccount(CUSTODY));
const hex = (b) => Buffer.from(b).toString("hex");
const out = {};
const dump = (name, r) => {
  if (r.isErr()) { out[name] = { error: String(r.error) }; return; }
  const m = r.value;
  out[name] = { data: hex(h.MessageData.encode(m.data).finish()), hash: hex(m.hash), signature: hex(m.signature), signer: hex(m.signer), message: hex(h.Message.encode(m).finish()) };
};
const key = (await signer.getSignerKey())._unsafeUnwrap();
out.signer_key = hex(key);
out.custody_address = privateKeyToAccount(CUSTODY).address;
const scopes = [1, 2, 3, 4, 5, 6, 11];
const body = await h.makeKeyAddBody({ fid: BigInt(FID), key, scopes, ttl: 2592000, nonce: 5, deadline: DEADLINE }, eip);
if (body.isErr()) { out.key_add_body_error = String(body.error); } else {
  out.key_add_body = { custody_signature: hex(body.value.custodySignature), metadata: hex(body.value.metadata), metadata_type: body.value.metadataType, key_type: body.value.keyType, deadline: body.value.deadline, nonce: body.value.nonce, scopes: body.value.scopes, ttl: body.value.ttl };
  dump("key_add", await h.makeKeyAdd(body.value, opts, signer));
}
dump("cast_simple", await h.makeCastAdd({ text: "hello from phunk", embeds: [], embedsDeprecated: [], mentions: [], mentionsPositions: [], type: h.CastType.CAST }, opts, signer));
dump("cast_full", await h.makeCastAdd({ text: "hi  and see", embeds: [{ url: "https://example.com" }, { castId: { fid: 3, hash: new Uint8Array(20).fill(0xab) } }], embedsDeprecated: [], mentions: [3], mentionsPositions: [3], parentCastId: { fid: 2, hash: new Uint8Array(20).fill(0xcd) }, type: h.CastType.CAST }, opts, signer));
dump("cast_channel", await h.makeCastAdd({ text: "gm", embeds: [], embedsDeprecated: [], mentions: [], mentionsPositions: [], parentUrl: "https://farcaster.xyz/~/channel/dev", type: h.CastType.CAST }, opts, signer));
dump("cast_remove", await h.makeCastRemove({ targetHash: new Uint8Array(20).fill(0xef) }, opts, signer));
dump("like", await h.makeReactionAdd({ type: h.ReactionType.LIKE, targetCastId: { fid: 3, hash: new Uint8Array(20).fill(0xab) } }, opts, signer));
dump("unrecast", await h.makeReactionRemove({ type: h.ReactionType.RECAST, targetCastId: { fid: 3, hash: new Uint8Array(20).fill(0xab) } }, opts, signer));
dump("follow", await h.makeLinkAdd({ type: "follow", targetFid: 3 }, opts, signer));
dump("unfollow", await h.makeLinkRemove({ type: "follow", targetFid: 3 }, opts, signer));
console.log(JSON.stringify(out, null, 1));
