// Farcaster messages, built in the page: follow, unfollow, like, unlike.
// The same encoding as src/phunk/proto.py, which matches the official
// TypeScript library byte for byte; tests/js/fc_test.mjs checks this file
// against tests/vectors/write.json. Signing needs two small libraries the
// page loads on demand (Ed25519 and BLAKE3, see app.js); they are passed in.
"use strict";

(() => {
  const FARCASTER_EPOCH = 1609459200;
  const MAINNET = 1;
  const TYPE = { REACTION_ADD: 3, REACTION_REMOVE: 4, LINK_ADD: 5, LINK_REMOVE: 6 };
  const LIKE = 1;
  const BODY_FIELD = { 3: 7, 4: 7, 5: 14, 6: 14 }; // MessageData oneof: reaction_body, link_body

  function concat(...parts) {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let i = 0;
    for (const p of parts) { out.set(p, i); i += p.length; }
    return out;
  }
  // Numbers here (fids, timestamps) stay well under 2^53, so plain arithmetic is exact.
  function varint(n) {
    const out = [];
    while (n > 127) { out.push((n % 128) | 128); n = Math.floor(n / 128); }
    out.push(n);
    return Uint8Array.from(out);
  }
  const tag = (num, wire) => varint(num * 8 + wire);
  const fInt = (num, v) => (v ? concat(tag(num, 0), varint(v)) : new Uint8Array(0));
  const fBytes = (num, b) => (b.length ? concat(tag(num, 2), varint(b.length), b) : new Uint8Array(0));
  const fMsg = (num, b) => concat(tag(num, 2), varint(b.length), b); // a set submessage, even if empty
  const fStr = (num, s) => fBytes(num, new TextEncoder().encode(s));

  function hexToBytes(hex) {
    const h = String(hex).replace(/^0x/, "");
    if (h.length % 2 || /[^0-9a-f]/i.test(h)) throw new Error("bad hex");
    const out = new Uint8Array(h.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(2 * i, 2 * i + 2), 16);
    return out;
  }
  const bytesToHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");

  const castId = (fid, hash) => concat(fInt(1, fid), fBytes(2, hexToBytes(hash)));
  const reactionBody = (type, fid, hash) => concat(fInt(1, type), fMsg(2, castId(fid, hash)));
  const linkBody = (targetFid) => concat(fStr(1, "follow"), tag(3, 0), varint(targetFid));

  function messageData(type, fid, timestamp, body) {
    return concat(fInt(1, type), fInt(2, fid), fInt(3, timestamp), fInt(4, MAINNET), fMsg(BODY_FIELD[type], body));
  }
  // `data` goes in twice, as data and as data_bytes, like the official library.
  function message(data, hash, signature, signer) {
    return concat(fMsg(1, data), fBytes(2, hash), fInt(3, 1), fBytes(4, signature), fInt(5, 1), fBytes(6, signer), fBytes(7, data));
  }
  const now = () => Math.floor(Date.now() / 1000) - FARCASTER_EPOCH;

  // key: { priv, pub } as Uint8Arrays; libs: { ed (noble ed25519), blake3 }
  async function sign(type, fid, body, key, libs, timestamp) {
    const data = messageData(type, fid, timestamp === undefined ? now() : timestamp, body);
    const hash = libs.blake3(data).slice(0, 20);
    const signature = await libs.ed.signAsync(hash, key.priv);
    return message(data, hash, signature, key.pub);
  }

  globalThis.phunkMsg = {
    TYPE, LIKE, now, hexToBytes, bytesToHex, messageData, message, reactionBody, linkBody, sign,
    follow: (fid, target, key, libs, ts) => sign(TYPE.LINK_ADD, fid, linkBody(target), key, libs, ts),
    unfollow: (fid, target, key, libs, ts) => sign(TYPE.LINK_REMOVE, fid, linkBody(target), key, libs, ts),
    like: (fid, castFid, castHash, key, libs, ts) => sign(TYPE.REACTION_ADD, fid, reactionBody(LIKE, castFid, castHash), key, libs, ts),
    unlike: (fid, castFid, castHash, key, libs, ts) => sign(TYPE.REACTION_REMOVE, fid, reactionBody(LIKE, castFid, castHash), key, libs, ts),
  };
})();
