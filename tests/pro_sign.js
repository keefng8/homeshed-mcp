"use strict";

/**
 * The Pro site's half of pro_proof.py (to-do #19, round 5, 2026-10-08): signs the membership statement whoami returns
 * and the "issued" watermark on each served pack, with Ed25519 (Node's crypto, no package). Used by test_pro_proof.py to check
 * that what the site signs verifies in Python; the private key comes from the vault (PEM, PKCS#8), never from a file in git.
 *
 *   whoami:  ctx.send({ client: {...}, proof: signMembership(key, kid, bearerToken, subscriberId) })
 *   pack:    content: signPack(key, kid, pack.content, subscriberId)   // also log sha256 of the result (the watermark scheme)
 *
 * subscriberId: an opaque id (the user's documentId), never an email.
 */

const crypto = require("crypto");

const MEMBERSHIP_TTL_S = 3600; // pro_proof.py refuses more than 24 h; short keeps a replayed proof short-lived

function b64u(buf) {
  return Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Same bytes as Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False). Integers and strings
// only: floats print differently in the two languages.
function canonical(value) {
  const sort = (v) =>
    Array.isArray(v) ? v.map(sort)
      : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sort(v[k])]))
        : v;
  return Buffer.from(JSON.stringify(sort(value)), "utf8");
}

function tokenHint(token) {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex").slice(0, 16);
}

function signMembership(privateKey, kid, token, subscriberId, now = Math.floor(Date.now() / 1000)) {
  const payload = { aud: "homeshed", tier: "pro", sub: String(subscriberId), th: tokenHint(token), iat: now, exp: now + MEMBERSHIP_TTL_S };
  const body = b64u(JSON.stringify(payload));
  return `${kid}.${body}.${b64u(crypto.sign(null, Buffer.from(body, "ascii"), privateKey))}`;
}

function signPack(privateKey, kid, pack, subscriberId, now = Math.floor(Date.now() / 1000)) {
  const issued = { to: String(subscriberId), at: now, kid };
  const sig = b64u(crypto.sign(null, canonical({ ...pack, issued }), privateKey));
  return { ...pack, issued: { ...issued, sig } };
}

module.exports = { canonical, tokenHint, signMembership, signPack };

// CLI for the cross-language test: node pro_sign.js <pkcs8-pem-file> <kid> membership <token> <sub> [now]
//                                   node pro_sign.js <pkcs8-pem-file> <kid> pack <pack-json-file> <sub> [now]
if (require.main === module) {
  const fs = require("fs");
  const [pemFile, kid, what, arg, sub, now] = process.argv.slice(2);
  const key = crypto.createPrivateKey(fs.readFileSync(pemFile, "utf8"));
  const t = now ? Number(now) : undefined;
  const out = what === "membership"
    ? signMembership(key, kid, arg, sub, t)
    : signPack(key, kid, JSON.parse(fs.readFileSync(arg, "utf8")), sub, t);
  process.stdout.write(typeof out === "string" ? out : JSON.stringify(out));
}
