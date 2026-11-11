// SPDX-License-Identifier: Apache-2.0
// Email double opt-in and the new-episode sender. The sign-up flow is ported from
// civiccast-native civiccast/subscribe/service.py and rate_limit.py
// (Copyright (c) The CivicCast Authors, Apache-2.0): pending -> confirmed -> unsubscribed,
// signed links, a sliding-window rate limit, and no confirmation token in any response.

import { keyedHash, open, seal, signToken, subscriptionId, verifyToken } from "./crypto";
import type { AppEnv, CityConfig } from "./env";
import { parseFeed } from "./feed";
import { escapeHtml, page, STYLE } from "./html";
import { CONFIRM_DAYS, confirmMail, episodeMail } from "./mail";

export const LIMITS = {
  subscribesPerAddressPerHour: 5,
  confirmMailsPerSignupPerDay: 3,
  maxSendsPerRun: 25,
  maxAttempts: 3,
  deliverWindowDays: 7,
};

const MAX_BODY = 4096;
const DAY = 86_400;

type Status = "pending_confirmation" | "confirmed" | "unsubscribed";

interface Row {
  id: string;
  city: string;
  email_sealed: string;
  status: Status;
  unsubscribed_at: string | null;
}

function iso(nowMs: number): string {
  return new Date(nowMs).toISOString();
}

function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

const EMAIL_RE =
  /^[^\s@<>()[\]\\,;:"]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

export function isEmail(email: string): boolean {
  return email.length <= 254 && EMAIL_RE.test(email);
}

/** Sliding window: refuse when `limit` hits already fall inside the window. */
async function allow(db: D1Database, key: string, limit: number, windowSeconds: number, nowMs: number) {
  const since = nowMs - windowSeconds * 1000;
  const row = await db
    .prepare("SELECT COUNT(*) AS n FROM rate_hits WHERE key = ? AND at > ?")
    .bind(key, since)
    .first<{ n: number }>();
  if ((row?.n ?? 0) >= limit) return false;
  await db.prepare("INSERT INTO rate_hits (key, at) VALUES (?, ?)").bind(key, nowMs).run();
  return true;
}

function backLink(city: CityConfig): string {
  return `<p><a href="${escapeHtml(city.site)}">Back to ${escapeHtml(city.title)}</a></p>`;
}

function checkEmailPage(city: CityConfig): Response {
  return page(
    "Check your email",
    `<p>If that address can get email from us, a message with a confirmation link is on its way. Open it to finish signing up.</p>` +
      `<p>Nothing is sent to that address until it is confirmed.</p>${backLink(city)}`,
    200,
    city.title,
  );
}

function badLink(message: string, status = 400): Response {
  return page("This link does not work", `<p>${escapeHtml(message)}</p>`, status);
}

async function readForm(request: Request): Promise<FormData | null> {
  const length = Number(request.headers.get("Content-Length") ?? "0");
  if (length > MAX_BODY) return null;
  const type = request.headers.get("Content-Type") ?? "";
  if (!type.startsWith("application/x-www-form-urlencoded") && !type.startsWith("multipart/form-data")) {
    return new FormData();
  }
  const text = await request.text();
  if (text.length > MAX_BODY) return null;
  if (type.startsWith("multipart/form-data")) {
    return new Request(request.url, { method: "POST", headers: { "Content-Type": type }, body: text }).formData();
  }
  const form = new FormData();
  for (const [k, v] of new URLSearchParams(text)) form.append(k, v);
  return form;
}

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
}

async function subscribe(request: Request, env: AppEnv, nowMs: number): Promise<Response> {
  const form = await readForm(request);
  if (!form) return page("That was too much", "<p>The form sent more than we accept.</p>", 413);
  const slug = field(form, "city");
  const city = Object.hasOwn(env.CITIES, slug) ? env.CITIES[slug] : undefined;
  if (!city) return page("Unknown city", "<p>This sign-up form is not set up for that city.</p>", 400);
  // A bot that fills the hidden field gets the normal answer and nothing else happens.
  if (field(form, "website") !== "") return checkEmailPage(city);

  const email = normalizeEmail(field(form, "email"));
  if (!isEmail(email)) {
    return page(
      "That email address does not look right",
      `<p>Go back and check the address, then try again.</p>${backLink(city)}`,
      400,
      city.title,
    );
  }

  const client = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const clientKey = `subscribe:${await keyedHash(env.TOKEN_KEY, "client", client)}`;
  if (!(await allow(env.DB, clientKey, LIMITS.subscribesPerAddressPerHour, 3600, nowMs))) {
    return page("Too many tries", `<p>Please wait an hour and try again.</p>${backLink(city)}`, 429, city.title);
  }

  const id = await subscriptionId(env.TOKEN_KEY, email, slug);
  const row = await env.DB.prepare("SELECT status FROM subscriptions WHERE id = ?")
    .bind(id)
    .first<{ status: Status }>();
  // Same page for every case, so the form does not tell anyone who is signed up.
  if (row?.status === "confirmed") return checkEmailPage(city);
  if (!(await allow(env.DB, `confirm-mail:${id}`, LIMITS.confirmMailsPerSignupPerDay, DAY, nowMs))) {
    return checkEmailPage(city);
  }

  const sealed = await seal(env.EMAIL_KEY, email, id);
  const now = iso(nowMs);
  if (row) {
    await env.DB.prepare(
      "UPDATE subscriptions SET status = 'pending_confirmation', email_sealed = ?, confirm_sent_at = ? WHERE id = ?",
    )
      .bind(sealed, now, id)
      .run();
  } else {
    await env.DB.prepare(
      "INSERT INTO subscriptions (id, city, email_sealed, status, created_at, confirm_sent_at) VALUES (?, ?, ?, 'pending_confirmation', ?, ?)",
    )
      .bind(id, slug, sealed, now, now)
      .run();
  }

  const token = await signToken(env.TOKEN_KEY, {
    subscription_id: id,
    action: "confirm",
    issued_at: Math.floor(nowMs / 1000),
  });
  const mail = confirmMail(city, `${env.PUBLIC_URL}/confirm?t=${encodeURIComponent(token)}`);
  try {
    await env.EMAIL.send({
      to: email,
      from: { email: env.FROM_EMAIL, name: city.title },
      ...mail,
      headers: { "Auto-Submitted": "auto-generated" },
    });
  } catch (err) {
    const e = err as { code?: string; message?: string };
    console.error("confirmation email failed", e.code, e.message);
    return page(
      "We could not send the email",
      `<p>Something went wrong on our side. Please try again later.</p>${backLink(city)}`,
      503,
      city.title,
    );
  }
  return checkEmailPage(city);
}

async function loadRow(env: AppEnv, id: string): Promise<Row | null> {
  return env.DB.prepare("SELECT id, city, email_sealed, status, unsubscribed_at FROM subscriptions WHERE id = ?")
    .bind(id)
    .first<Row>();
}

function cityOf(env: AppEnv, row: Row): CityConfig | undefined {
  return Object.hasOwn(env.CITIES, row.city) ? env.CITIES[row.city] : undefined;
}

function tokenFrom(url: URL, form: FormData | null): string {
  return (form && field(form, "t")) || url.searchParams.get("t") || "";
}

async function confirm(request: Request, env: AppEnv, nowMs: number, url: URL): Promise<Response> {
  const form = request.method === "POST" ? await readForm(request) : null;
  const token = tokenFrom(url, form);
  const body = await verifyToken(env.TOKEN_KEY, token, "confirm");
  if (!body) return badLink("This confirmation link is not valid. Sign up again from the site.");
  if (nowMs / 1000 - body.issued_at > CONFIRM_DAYS * DAY) {
    return badLink("This confirmation link has expired. Sign up again from the site.");
  }
  const row = await loadRow(env, body.subscription_id);
  const city = row ? cityOf(env, row) : undefined;
  if (!row || !city) return badLink("This confirmation link is not valid. Sign up again from the site.");
  if (row.status === "unsubscribed" && row.unsubscribed_at && Date.parse(row.unsubscribed_at) >= body.issued_at * 1000) {
    return badLink("You unsubscribed after this link was sent. Sign up again from the site.");
  }
  if (row.status === "confirmed") {
    return page("Already confirmed", `<p>This sign-up was already confirmed.</p>${backLink(city)}`, 200, city.title);
  }
  if (request.method !== "POST") {
    // Mail scanners open links. Only a button press confirms.
    return page(
      "Confirm your sign-up",
      `<p>Press the button to get an email from ${escapeHtml(city.title)} each time a new episode is posted.</p>` +
        `<form method="post" action="/confirm"><input type="hidden" name="t" value="${escapeHtml(token)}">` +
        `<button type="submit">Confirm my sign-up</button></form>`,
      200,
      city.title,
    );
  }
  await env.DB.prepare(
    "UPDATE subscriptions SET status = 'confirmed', confirmed_at = ?, unsubscribed_at = NULL WHERE id = ?",
  )
    .bind(iso(nowMs), row.id)
    .run();
  return page(
    "You are signed up",
    `<p>You will get an email each time a new episode is posted. Every email has an unsubscribe link.</p>${backLink(city)}`,
    200,
    city.title,
  );
}

async function unsubscribe(request: Request, env: AppEnv, nowMs: number, url: URL): Promise<Response> {
  const form = request.method === "POST" ? await readForm(request) : null;
  const token = tokenFrom(url, form);
  const body = await verifyToken(env.TOKEN_KEY, token, "unsubscribe");
  if (!body) return badLink("This unsubscribe link is not valid.");
  const row = await loadRow(env, body.subscription_id);
  const city = row ? cityOf(env, row) : undefined;
  if (!row || !city) return badLink("This unsubscribe link is not valid.");
  if (row.status === "unsubscribed") {
    return page("Already unsubscribed", `<p>You will not get more emails.</p>${backLink(city)}`, 200, city.title);
  }
  if (request.method !== "POST") {
    return page(
      "Unsubscribe",
      `<p>Press the button to stop getting emails from ${escapeHtml(city.title)}.</p>` +
        `<form method="post" action="/unsubscribe"><input type="hidden" name="t" value="${escapeHtml(token)}">` +
        `<button type="submit">Unsubscribe</button></form>`,
      200,
      city.title,
    );
  }
  // POST covers the button and one-click unsubscribe from mail apps (RFC 8058).
  await env.DB.prepare("UPDATE subscriptions SET status = 'unsubscribed', unsubscribed_at = ? WHERE id = ?")
    .bind(iso(nowMs), row.id)
    .run();
  return page("You are unsubscribed", `<p>You will not get more emails.</p>${backLink(city)}`, 200, city.title);
}

export async function handleFetch(request: Request, env: AppEnv, nowMs = Date.now()): Promise<Response> {
  const url = new URL(request.url);
  const method = request.method;
  if (url.pathname === "/assets/signup.css" && method === "GET") {
    return new Response(STYLE, {
      headers: { "Content-Type": "text/css; charset=utf-8", "Cache-Control": "public, max-age=3600" },
    });
  }
  if (method !== "GET" && method !== "POST") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, POST" } });
  }
  if (url.pathname === "/subscribe" && method === "POST") return subscribe(request, env, nowMs);
  if (url.pathname === "/confirm") return confirm(request, env, nowMs, url);
  if (url.pathname === "/unsubscribe") return unsubscribe(request, env, nowMs, url);
  if (url.pathname === "/" && method === "GET") {
    const links = Object.values(env.CITIES)
      .map((c) => `<li><a href="${escapeHtml(c.site)}">${escapeHtml(c.title)}</a></li>`)
      .join("");
    return page("Email sign-up", `<p>This address handles email sign-ups. Sign up from a city site:</p><ul>${links}</ul>`);
  }
  return page("Page not found", "<p>There is nothing here.</p>", 404);
}

export interface SenderReport {
  newItems: number;
  sent: number;
  failed: number;
  feedErrors: string[];
}

interface Due {
  city: string;
  guid: string;
  title: string;
  link: string;
  description: string;
  sid: string;
  email_sealed: string;
}

/** Reads each city feed and emails confirmed subscribers about episodes that appeared after they confirmed. */
export async function runSender(
  env: AppEnv,
  fetcher: (url: string, init?: RequestInit) => Promise<Response> = fetch,
  nowMs = Date.now(),
): Promise<SenderReport> {
  const db = env.DB;
  const now = iso(nowMs);
  const report: SenderReport = { newItems: 0, sent: 0, failed: 0, feedErrors: [] };
  await db.prepare("DELETE FROM rate_hits WHERE at < ?").bind(nowMs - 2 * DAY * 1000).run();

  for (const [slug, city] of Object.entries(env.CITIES)) {
    let items;
    try {
      const res = await fetcher(city.feed);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      items = parseFeed(await res.text());
    } catch (err) {
      report.feedErrors.push(`${slug}: ${(err as Error).message}`);
      continue;
    }
    const state = await db.prepare("SELECT city FROM feed_state WHERE city = ?").bind(slug).first();
    const inserts = items.map((i) =>
      db
        .prepare(
          "INSERT OR IGNORE INTO feed_items (city, guid, title, link, description, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
        )
        .bind(slug, i.guid, i.title, i.link, i.description, now),
    );
    if (!state) inserts.push(db.prepare("INSERT INTO feed_state (city, baseline_at) VALUES (?, ?)").bind(slug, now));
    if (inserts.length) {
      const results = await db.batch(inserts);
      if (state) report.newItems += results.reduce((n, r) => n + (r.meta.changes ?? 0), 0);
    }
  }

  const due = await db
    .prepare(
      `SELECT f.city, f.guid, f.title, f.link, f.description, s.id AS sid, s.email_sealed
       FROM feed_items f
       JOIN feed_state st ON st.city = f.city AND f.seen_at > st.baseline_at
       JOIN subscriptions s ON s.city = f.city AND s.status = 'confirmed' AND s.confirmed_at < f.seen_at
       LEFT JOIN deliveries d ON d.city = f.city AND d.guid = f.guid AND d.subscription_id = s.id
       WHERE f.seen_at > ? AND (d.status IS NULL OR (d.status = 'failed' AND d.attempts < ?))
       ORDER BY f.seen_at, s.id
       LIMIT ?`,
    )
    .bind(iso(nowMs - LIMITS.deliverWindowDays * DAY * 1000), LIMITS.maxAttempts, LIMITS.maxSendsPerRun)
    .all<Due>();

  for (const d of due.results) {
    const city = Object.hasOwn(env.CITIES, d.city) ? env.CITIES[d.city] : undefined;
    if (!city) continue;
    let status: "sent" | "failed" = "sent";
    let messageId: string | null = null;
    let error: string | null = null;
    try {
      const email = await open(env.EMAIL_KEY, d.email_sealed, d.sid);
      const token = await signToken(env.TOKEN_KEY, {
        subscription_id: d.sid,
        action: "unsubscribe",
        issued_at: Math.floor(nowMs / 1000),
      });
      const unsubscribeUrl = `${env.PUBLIC_URL}/unsubscribe?t=${encodeURIComponent(token)}`;
      const mail = episodeMail(city, d, unsubscribeUrl);
      const sent = await env.EMAIL.send({
        to: email,
        from: { email: env.FROM_EMAIL, name: city.title },
        ...mail,
        headers: {
          "List-Unsubscribe": `<${unsubscribeUrl}>`,
          "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
          "List-Id": `${city.title.replace(/[<>"]/g, "")} <${d.city}.${new URL(env.PUBLIC_URL).hostname}>`,
          "Auto-Submitted": "auto-generated",
        },
      });
      messageId = sent.messageId;
      report.sent += 1;
    } catch (err) {
      const e = err as { code?: string; message?: string };
      status = "failed";
      error = `${e.code ?? "error"}: ${e.message ?? ""}`.slice(0, 500);
      report.failed += 1;
      console.error("episode email failed", d.city, d.guid, e.code, e.message);
    }
    await db
      .prepare(
        `INSERT INTO deliveries (city, guid, subscription_id, status, message_id, error, at, attempts)
         VALUES (?, ?, ?, ?, ?, ?, ?, 1)
         ON CONFLICT (city, guid, subscription_id) DO UPDATE SET
           status = excluded.status, message_id = excluded.message_id, error = excluded.error,
           at = excluded.at, attempts = deliveries.attempts + 1`,
      )
      .bind(d.city, d.guid, d.sid, status, messageId, error, now)
      .run();
  }
  return report;
}
