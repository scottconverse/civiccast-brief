// SPDX-License-Identifier: Apache-2.0
// Ported from civiccast-native civiccast/subscribe/crypto.py and service.py
// (Copyright (c) The CivicCast Authors, Apache-2.0). Changes: Web Crypto instead of Python hmac;
// the subscription id is a keyed HMAC instead of a plain SHA-256; email is sealed with AES-GCM.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64urlEncode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(text: string): Uint8Array {
  const plain = text.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  if (!/^[A-Za-z0-9_-]*$/.test(plain)) throw new Error("not base64");
  const padded = plain.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((plain.length + 3) % 4);
  const bin = atob(padded);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  if (!secret) throw new Error("TOKEN_KEY is not set");
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

/** Keyed hash for lookups and rate-limit keys. */
export async function keyedHash(secret: string, label: string, value: string): Promise<string> {
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(`${label}:${value}`));
  return hex(sig);
}

/** Same email and city always give the same id, so a second sign-up finds the first row. */
export async function subscriptionId(secret: string, email: string, city: string): Promise<string> {
  return `sub-${(await keyedHash(secret, "id", `${email}:${city}`)).slice(0, 24)}`;
}

export type TokenAction = "confirm" | "unsubscribe";

export interface TokenBody {
  subscription_id: string;
  action: TokenAction;
  issued_at: number; // Unix seconds
}

/** base64url(JSON body) + "." + hex HMAC-SHA256 of that text, as in civiccast-native. */
export async function signToken(secret: string, body: TokenBody): Promise<string> {
  const payload = b64urlEncode(enc.encode(JSON.stringify(body)));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(payload));
  return `${payload}.${hex(sig)}`;
}

/** Returns the body only when the signature is good and the action matches. */
export async function verifyToken(
  secret: string,
  token: unknown,
  action: TokenAction,
): Promise<TokenBody | null> {
  if (typeof token !== "string" || token.length > 1024) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [payload, sigHex] = parts as [string, string];
  if (!/^[0-9a-f]{64}$/.test(sigHex)) return null;
  const sig = Uint8Array.from(sigHex.match(/../g) ?? [], (h) => parseInt(h, 16));
  // subtle.verify compares in constant time.
  const ok = await crypto.subtle.verify("HMAC", await hmacKey(secret), sig, enc.encode(payload));
  if (!ok) return null;
  let body: unknown;
  try {
    body = JSON.parse(dec.decode(b64urlDecode(payload)));
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  const b = body as Record<string, unknown>;
  if (b.action !== action || typeof b.subscription_id !== "string" || typeof b.issued_at !== "number") {
    return null;
  }
  return { subscription_id: b.subscription_id, action, issued_at: b.issued_at };
}

async function aesKey(keyB64: string): Promise<CryptoKey> {
  const raw = b64urlDecode(keyB64 ?? "");
  if (raw.length !== 32) throw new Error("EMAIL_KEY must be 32 random bytes, base64");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function seal(keyB64: string, plaintext: string, aad: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: enc.encode(aad) },
    await aesKey(keyB64),
    enc.encode(plaintext),
  );
  const out = new Uint8Array(12 + ct.byteLength);
  out.set(iv);
  out.set(new Uint8Array(ct), 12);
  return b64urlEncode(out);
}

export async function open(keyB64: string, sealed: string, aad: string): Promise<string> {
  const raw = b64urlDecode(sealed);
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: raw.slice(0, 12), additionalData: enc.encode(aad) },
    await aesKey(keyB64),
    raw.slice(12),
  );
  return dec.decode(pt);
}
