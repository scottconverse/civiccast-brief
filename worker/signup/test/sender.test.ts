import { beforeEach, describe, expect, it } from "vitest";
import { handleFetch, runSender } from "../src/app";
import { feedXml, fetcherFor, form, harness, linkIn, tokenOf, type Harness } from "./helpers";

const T0 = Date.parse("2026-11-07T12:00:00Z");
const MIN = 60_000;
let h: Harness;
let items: { guid: string; title: string }[];
const fetcher = () => fetcherFor(() => feedXml(items));

beforeEach(async () => {
  h = await harness();
  items = [{ guid: "old", title: "Old meeting" }];
});

async function subscriber(email: string, now: number, ip: string) {
  await handleFetch(form("/subscribe", { email, city: "longmont" }, ip), h.env, now);
  const t = tokenOf(linkIn(h.sent[h.sent.length - 1], "/confirm"));
  await handleFetch(form("/confirm", { t }), h.env, now + 1);
  h.sent.length = 0;
}

describe("sender", () => {
  it("sends nothing on the first run, then emails each new episode once", async () => {
    await subscriber("reader@example.org", T0, "198.51.100.1");
    expect(await runSender(h.env, fetcher(), T0 + MIN)).toMatchObject({ newItems: 0, sent: 0 });
    expect(h.sent).toHaveLength(0);

    items = [{ guid: "new", title: "Council: budget" }, ...items];
    expect(await runSender(h.env, fetcher(), T0 + 15 * MIN)).toMatchObject({ newItems: 1, sent: 1, failed: 0 });
    const mail = h.sent[0];
    expect(mail.to).toBe("reader@example.org");
    expect(mail.subject).toBe("New on CivicCast Brief: Longmont: Council: budget");
    expect(mail.text).toContain("About Council: budget & more");
    expect(mail.text).toContain("https://longmont.civiccastbrief.org/e/new/");
    const unsub = linkIn(mail, "/unsubscribe");
    expect(mail.headers).toMatchObject({
      "List-Unsubscribe": `<${unsub}>`,
      "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
      "Auto-Submitted": "auto-generated",
      "List-Id": "CivicCast Brief: Longmont <longmont.signup.civiccastbrief.org>",
    });

    expect(await runSender(h.env, fetcher(), T0 + 30 * MIN)).toMatchObject({ newItems: 0, sent: 0 });
    expect(h.sent).toHaveLength(1);
  });

  it("does not send older episodes to someone who confirmed later", async () => {
    await runSender(h.env, fetcher(), T0);
    items = [{ guid: "new", title: "New" }, ...items];
    await runSender(h.env, fetcher(), T0 + 15 * MIN);
    await subscriber("late@example.org", T0 + 20 * MIN, "198.51.100.2");
    expect(await runSender(h.env, fetcher(), T0 + 30 * MIN)).toMatchObject({ sent: 0 });
  });

  it("retries a failed send up to 3 times", async () => {
    await subscriber("reader@example.org", T0, "198.51.100.1");
    await runSender(h.env, fetcher(), T0 + MIN);
    items = [{ guid: "new", title: "New" }, ...items];
    h.failWith = { code: "E_RATE_LIMIT_EXCEEDED" };
    for (let i = 1; i <= 4; i++) await runSender(h.env, fetcher(), T0 + i * 15 * MIN);
    const d = await h.env.DB.prepare("SELECT status, attempts, error FROM deliveries").first<{
      status: string;
      attempts: number;
      error: string;
    }>();
    expect(d).toMatchObject({ status: "failed", attempts: 3 });
    expect(d?.error).toContain("E_RATE_LIMIT_EXCEEDED");
  });

  it("skips readers who unsubscribed, including by one-click POST", async () => {
    await subscriber("reader@example.org", T0, "198.51.100.1");
    await runSender(h.env, fetcher(), T0 + MIN);
    items = [{ guid: "a", title: "A" }, ...items];
    await runSender(h.env, fetcher(), T0 + 15 * MIN);
    const unsub = linkIn(h.sent[0], "/unsubscribe");
    const oneClick = () =>
      new Request(unsub, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "List-Unsubscribe=One-Click",
      });
    expect(await (await handleFetch(oneClick(), h.env, T0 + 16 * MIN)).text()).toContain("You are unsubscribed");
    expect(await (await handleFetch(oneClick(), h.env, T0 + 17 * MIN)).text()).toContain("Already unsubscribed");
    items = [{ guid: "b", title: "B" }, ...items];
    expect(await runSender(h.env, fetcher(), T0 + 30 * MIN)).toMatchObject({ newItems: 1, sent: 0 });
  });

  it("reports a feed that cannot be read and keeps going", async () => {
    const r = await runSender(h.env, async () => new Response("no", { status: 500 }), T0);
    expect(r.feedErrors).toEqual(["longmont: HTTP 500"]);
  });
});
