// Checks src/phunk/static/fc.js against the official library's reference
// messages (tests/vectors/write.json). No dependencies: the hash and the
// signature come from the vectors, so this tests every byte fc.js writes.
// With NOBLE_DIR set to a folder holding @noble/ed25519 and @noble/hashes,
// it also signs for real and compares whole messages.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const V = JSON.parse(fs.readFileSync(path.join(root, "tests/vectors/write.json"), "utf8"));
const ctx = { TextEncoder, Date, Math, Uint8Array, Array, String, parseInt, Error };
ctx.globalThis = ctx;
vm.runInNewContext(fs.readFileSync(path.join(root, "src/phunk/static/fc.js"), "utf8"), ctx);
const M = ctx.phunkMsg;
const FID = 1234, TS = 183000000, AB = "ab".repeat(20);

const cases = {
  like: M.messageData(M.TYPE.REACTION_ADD, FID, TS, M.reactionBody(M.LIKE, 3, AB)),
  unrecast: M.messageData(M.TYPE.REACTION_REMOVE, FID, TS, M.reactionBody(2, 3, AB)),
  follow: M.messageData(M.TYPE.LINK_ADD, FID, TS, M.linkBody(3)),
  unfollow: M.messageData(M.TYPE.LINK_REMOVE, FID, TS, M.linkBody(3)),
};
let failed = 0;
const check = (name, got, want) => {
  if (got !== want) { failed++; console.error(`FAIL ${name}\n  got  ${got}\n  want ${want}`); }
  else console.log(`ok   ${name}`);
};
for (const [name, data] of Object.entries(cases)) {
  const v = V[name];
  check(`${name} data`, M.bytesToHex(data), v.data);
  const msg = M.message(data, M.hexToBytes(v.hash), M.hexToBytes(v.signature), M.hexToBytes(v.signer));
  check(`${name} message`, M.bytesToHex(msg), v.message);
}
// big numbers (fids and timestamps past 2^31) still encode right
check("varint 2^35", M.bytesToHex(M.linkBody(2 ** 35)), "0a06666f6c6c6f7718" + "8080808080" + "01");

if (process.env.NOBLE_DIR) {
  const imp = (p) => import(pathToFileURL(path.join(process.env.NOBLE_DIR, p)).href);
  const ed = await imp("node_modules/@noble/ed25519/index.js");
  const { blake3 } = await imp("node_modules/@noble/hashes/blake3.js");
  const priv = new Uint8Array(32).fill(0x22);
  const key = { priv, pub: await ed.getPublicKeyAsync(priv) };
  check("signer key", M.bytesToHex(key.pub), V.signer_key);
  const libs = { ed, blake3 };
  check("signed like", M.bytesToHex(await M.like(FID, 3, AB, key, libs, TS)), V.like.message);
  check("signed follow", M.bytesToHex(await M.follow(FID, 3, key, libs, TS)), V.follow.message);
  check("signed unfollow", M.bytesToHex(await M.unfollow(FID, 3, key, libs, TS)), V.unfollow.message);
}
if (failed) { console.error(`${failed} failed`); process.exit(1); }
