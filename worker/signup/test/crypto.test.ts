import { describe, expect, it } from "vitest";
import { open, seal, signToken, subscriptionId, verifyToken } from "../src/crypto";
import { parseFeed } from "../src/feed";

const KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=";

describe("tokens", () => {
  it("round-trips and binds the action", async () => {
    const t = await signToken("k", { subscription_id: "sub-1", action: "confirm", issued_at: 100 });
    expect(await verifyToken("k", t, "confirm")).toEqual({ subscription_id: "sub-1", action: "confirm", issued_at: 100 });
    expect(await verifyToken("k", t, "unsubscribe")).toBeNull();
    expect(await verifyToken("other", t, "confirm")).toBeNull();
  });

  it("refuses tampered and junk tokens", async () => {
    const t = await signToken("k", { subscription_id: "sub-1", action: "confirm", issued_at: 100 });
    const [p, s] = t.split(".");
    const forged = btoa(JSON.stringify({ subscription_id: "sub-2", action: "confirm", issued_at: 100 }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(await verifyToken("k", `${forged}.${s}`, "confirm")).toBeNull();
    expect(await verifyToken("k", `${p}.${"0".repeat(64)}`, "confirm")).toBeNull();
    for (const junk of ["", "a", "a.b.c", "x".repeat(2000)]) {
      expect(await verifyToken("k", junk, "confirm")).toBeNull();
    }
  });

  it("makes stable ids per address and city", async () => {
    const a = await subscriptionId("k", "a@b.org", "longmont");
    expect(a).toBe(await subscriptionId("k", "a@b.org", "longmont"));
    expect(a).not.toBe(await subscriptionId("k", "a@b.org", "oakland"));
    expect(a).toMatch(/^sub-[0-9a-f]{24}$/);
  });
});

describe("sealing", () => {
  it("round-trips and needs the same id", async () => {
    const s = await seal(KEY, "a@b.org", "sub-1");
    expect(s).not.toContain("a@b.org");
    expect(await open(KEY, s, "sub-1")).toBe("a@b.org");
    await expect(open(KEY, s, "sub-2")).rejects.toThrow();
  });
});

describe("feed", () => {
  it("reads items, decodes entities and CDATA, and skips bad ones", () => {
    const xml = `<rss><channel>
      <item><title><![CDATA[Council & budget]]></title><link>https://x.org/1/</link><guid isPermaLink="true">https://x.org/1/</guid><description>A &amp; B &#8212; C</description></item>
      <item><title>Plain http</title><guid>g2</guid><link>http://x.org/2/</link></item>
      <item><link>https://x.org/3/</link></item>
    </channel></rss>`;
    expect(parseFeed(xml)).toEqual([
      { guid: "https://x.org/1/", title: "Council & budget", link: "https://x.org/1/", description: "A & B — C" },
    ]);
  });
});
