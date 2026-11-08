import { beforeEach, describe, expect, it } from "vitest";
import { handleFetch } from "../src/app";
import { form, get, harness, linkIn, tokenOf, type Harness } from "./helpers";

const T0 = Date.parse("2026-11-07T12:00:00Z");
let h: Harness;
beforeEach(async () => {
  h = await harness();
});

async function signUp(email = "Reader@Example.org", now = T0, ip?: string) {
  return handleFetch(form("/subscribe", { email, city: "longmont", website: "" }, ip), h.env, now);
}

async function statuses(): Promise<string[]> {
  const r = await h.env.DB.prepare("SELECT status FROM subscriptions").all<{ status: string }>();
  return r.results.map((x) => x.status);
}

describe("subscribe", () => {
  it("sends one confirmation email and keeps the token and address out of the page and the database", async () => {
    const res = await signUp();
    const html = await res.text();
    expect(res.status).toBe(200);
    expect(html).toContain("Check your email");
    expect(h.sent).toHaveLength(1);
    const mail = h.sent[0];
    expect(mail.to).toBe("reader@example.org");
    expect(mail.from).toEqual({ email: "brief@civiccastbrief.org", name: "CivicCast Brief: Longmont" });
    expect(mail.subject).toBe("Confirm your CivicCast Brief: Longmont emails");
    expect(mail.headers).toEqual({ "Auto-Submitted": "auto-generated" });
    const token = tokenOf(linkIn(mail, "/confirm"));
    expect(html).not.toContain(token);
    const rows = await h.env.DB.prepare("SELECT * FROM subscriptions").all();
    expect(JSON.stringify(rows.results)).not.toContain("example.org");
    expect(await statuses()).toEqual(["pending_confirmation"]);
    expect(res.headers.get("Content-Security-Policy")).toContain("default-src 'none'");
  });

  it("does nothing for a filled honeypot", async () => {
    const res = await handleFetch(
      form("/subscribe", { email: "bot@example.org", city: "longmont", website: "spam" }),
      h.env,
      T0,
    );
    expect(res.status).toBe(200);
    expect(h.sent).toHaveLength(0);
    expect(await statuses()).toEqual([]);
  });

  it("refuses a bad address and an unknown city", async () => {
    for (const email of ["", "no-at-sign", "a@b", "a b@example.org", `${"x".repeat(65)}@example.org`, "a@-bad.org"]) {
      expect((await signUp(email)).status, email).toBe(400);
    }
    for (const city of ["nowhere", "__proto__", "toString"]) {
      expect((await handleFetch(form("/subscribe", { email: "a@example.org", city }), h.env, T0)).status).toBe(400);
    }
    expect(h.sent).toHaveLength(0);
  });

  it("refuses a body that is too big", async () => {
    const res = await handleFetch(
      form("/subscribe", { email: "a@example.org", city: "longmont", pad: "x".repeat(5000) }),
      h.env,
      T0,
    );
    expect(res.status).toBe(413);
  });

  it("limits one client to 5 sign-ups an hour", async () => {
    for (let i = 0; i < 5; i++) expect((await signUp(`r${i}@example.org`, T0 + i)).status).toBe(200);
    expect((await signUp("r6@example.org", T0 + 10)).status).toBe(429);
    expect((await signUp("r6@example.org", T0 + 10, "198.51.100.9")).status).toBe(200);
    expect((await signUp("r7@example.org", T0 + 3_600_001)).status).toBe(200);
  });

  it("sends at most 3 confirmation emails a day to one address", async () => {
    for (let i = 0; i < 4; i++) await signUp("reader@example.org", T0 + i, `198.51.100.${i}`);
    expect(h.sent).toHaveLength(3);
    await signUp("reader@example.org", T0 + 86_400_001, "198.51.100.50");
    expect(h.sent).toHaveLength(4);
  });

  it("answers 503 when the email cannot be sent", async () => {
    h.failWith = { code: "E_RECIPIENT_NOT_ALLOWED" };
    expect((await signUp()).status).toBe(503);
  });
});

describe("confirm and unsubscribe", () => {
  async function confirmLink() {
    await signUp();
    return linkIn(h.sent[0], "/confirm");
  }

  it("does not confirm on GET, confirms on POST, then says already confirmed", async () => {
    const link = new URL(await confirmLink());
    const page = await handleFetch(get(link.pathname + link.search), h.env, T0 + 1000);
    expect(page.status).toBe(200);
    expect(await page.text()).toContain('<form method="post" action="/confirm">');
    expect(await statuses()).toEqual(["pending_confirmation"]);

    const t = tokenOf(link.href);
    const done = await handleFetch(form("/confirm", { t }), h.env, T0 + 2000);
    expect(await done.text()).toContain("You are signed up");
    expect(await statuses()).toEqual(["confirmed"]);
    const again = await handleFetch(form("/confirm", { t }), h.env, T0 + 3000);
    expect(await again.text()).toContain("Already confirmed");
  });

  it("sends nothing new to an address that is already confirmed", async () => {
    const t = tokenOf(await confirmLink());
    await handleFetch(form("/confirm", { t }), h.env, T0 + 1000);
    const res = await signUp("reader@example.org", T0 + 2000, "198.51.100.77");
    expect(await res.text()).toContain("Check your email");
    expect(h.sent).toHaveLength(1);
  });

  it("refuses tampered and expired links", async () => {
    const t = tokenOf(await confirmLink());
    const tampered = t.slice(0, -1) + (t.endsWith("0") ? "1" : "0");
    expect((await handleFetch(form("/confirm", { t: tampered }), h.env, T0)).status).toBe(400);
    const late = await handleFetch(form("/confirm", { t }), h.env, T0 + 8 * 86_400_000);
    expect(await late.text()).toContain("expired");
    expect(await statuses()).toEqual(["pending_confirmation"]);
  });

  it("refuses a confirm link sent before the reader unsubscribed", async () => {
    const t = tokenOf(await confirmLink());
    await handleFetch(form("/confirm", { t }), h.env, T0 + 1000);
    await h.env.DB.prepare("UPDATE subscriptions SET status = 'unsubscribed', unsubscribed_at = ?")
      .bind(new Date(T0 + 5000).toISOString())
      .run();
    const res = await handleFetch(form("/confirm", { t }), h.env, T0 + 6000);
    expect(res.status).toBe(400);
    expect(await statuses()).toEqual(["unsubscribed"]);
  });
});

describe("other routes", () => {
  it("serves the stylesheet, a home page, 404 and 405", async () => {
    const css = await handleFetch(get("/assets/signup.css"), h.env, T0);
    expect(css.headers.get("Content-Type")).toContain("text/css");
    expect((await handleFetch(get("/"), h.env, T0)).status).toBe(200);
    expect((await handleFetch(get("/nope"), h.env, T0)).status).toBe(404);
    const put = new Request("https://signup.civiccastbrief.org/subscribe", { method: "PUT" });
    expect((await handleFetch(put, h.env, T0)).status).toBe(405);
  });
});
