import { describe, expect, it } from "vitest";
import { ANALYTICS_TOKEN, page, STYLE } from "../src/html";
import { confirmMail, episodeMail } from "../src/mail";
import type { FeedItem } from "../src/feed";

const CITY = {
  title: "CivicCast Brief: Longmont",
  site: "https://longmont.civiccastbrief.org",
  feed: "https://longmont.civiccastbrief.org/feed.xml",
};

describe("sign-up pages", () => {
  it("carry the logo, the wordmark with the city line, and the analytics beacon", async () => {
    const res = page("Check your email", "<p>ok</p>", 200, CITY.title);
    const html = await res.text();
    expect(html).toContain('<rect class="logo-tile" width="64" height="64" rx="14"/>');
    expect(html).toContain("CivicCast <b>Brief</b><small>Longmont</small>");
    expect(html).toContain(
      `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "${ANALYTICS_TOKEN}"}'></script>`,
    );
    expect(ANALYTICS_TOKEN).toBe("584d3422251846a3a68db1030f40f942");
    expect(html).toContain("Cloudflare Web Analytics, which counts visits without cookies");
    expect(html).not.toMatch(/style="/);
  });

  it("allow only the beacon, its endpoint and the home page fonts in the CSP", () => {
    const res = page("x", "");
    const csp = res.headers.get("Content-Security-Policy") ?? "";
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("script-src https://static.cloudflareinsights.com;");
    expect(csp).toContain("connect-src https://cloudflareinsights.com;");
    expect(csp).toContain("font-src https://civiccastbrief.org;");
    expect(csp).toContain("style-src 'self';");
    expect(res.headers.get("Cache-Control")).toContain("no-transform");
  });

  it("use the site colour tokens in light and dark mode", () => {
    expect(STYLE).toContain("--docket: #2340c4;");
    expect(STYLE).toContain('@media (prefers-color-scheme: dark) { :root:not([data-theme="light"])');
    expect(STYLE).toContain(':root[data-theme="dark"]');
    expect(STYLE).toContain("--docket: #8ea2ff;");
    expect(STYLE).toContain('url("https://civiccastbrief.org/assets/fonts/public-sans-latin-400-normal.woff2")');
  });

  it("escape the city line", async () => {
    const html = await page("x", "", 200, "CivicCast Brief: <b>Town</b>").text();
    expect(html).toContain("<small>&lt;b&gt;Town&lt;/b&gt;</small>");
  });
});

describe("emails", () => {
  const item: FeedItem = {
    title: "Council approves <annexation>",
    description: "A 6-1 vote.",
    link: "https://longmont.civiccastbrief.org/episodes/x/",
    guid: "x",
  };

  it("use inline styles only, the new colours, and no script or analytics", () => {
    for (const mail of [
      confirmMail(CITY, "https://signup.civiccastbrief.org/confirm?t=abc"),
      episodeMail(CITY, item, "https://signup.civiccastbrief.org/unsubscribe?t=abc"),
    ]) {
      expect(mail.html).not.toContain("<style");
      expect(mail.html).not.toContain("<link");
      expect(mail.html).not.toContain("<script");
      expect(mail.html).not.toContain("cloudflareinsights");
      expect(mail.html).toContain("background:#2340c4");
      expect(mail.html).toContain('CivicCast <span style="color:#2340c4">Brief</span>');
      expect(mail.html).toContain(">Longmont</span>");
      expect(mail.html).not.toMatch(/#0d4f5c|#0b7383/);
    }
  });

  it("escape the episode title", () => {
    const mail = episodeMail(CITY, item, "https://signup.civiccastbrief.org/unsubscribe?t=abc");
    expect(mail.html).toContain("Council approves &lt;annexation&gt;");
  });
});
