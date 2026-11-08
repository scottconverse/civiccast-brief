import { env as workerEnv } from "cloudflare:workers";
import type { AppEnv, OutgoingMail } from "../src/env";

export const base = workerEnv as unknown as AppEnv;

export interface Harness {
  env: AppEnv;
  sent: OutgoingMail[];
  failWith: { code: string } | null;
}

export async function harness(): Promise<Harness> {
  for (const t of ["subscriptions", "rate_hits", "feed_state", "feed_items", "deliveries"]) {
    await base.DB.prepare(`DELETE FROM ${t}`).run();
  }
  const h: Harness = { env: base, sent: [], failWith: null };
  h.env = {
    ...base,
    EMAIL: {
      async send(m) {
        if (h.failWith) throw Object.assign(new Error("send failed"), h.failWith);
        h.sent.push(m);
        return { messageId: `msg-${h.sent.length}` };
      },
    },
  };
  return h;
}

export function form(path: string, fields: Record<string, string>, ip = "203.0.113.5"): Request {
  return new Request(`https://signup.civiccastbrief.org${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": ip },
    body: new URLSearchParams(fields).toString(),
  });
}

export function get(pathAndQuery: string): Request {
  return new Request(`https://signup.civiccastbrief.org${pathAndQuery}`);
}

export function linkIn(mail: OutgoingMail, path: string): string {
  const m = mail.text.match(new RegExp(`https://signup\\.civiccastbrief\\.org${path}\\?t=\\S+`));
  if (!m) throw new Error(`no ${path} link in mail`);
  return m[0];
}

export function tokenOf(link: string): string {
  return new URL(link).searchParams.get("t") ?? "";
}

export function feedXml(items: { guid: string; title: string }[]): string {
  const body = items
    .map(
      (i) =>
        `<item><title>${i.title}</title><link>https://longmont.civiccastbrief.org/e/${i.guid}/</link>` +
        `<guid isPermaLink="true">https://longmont.civiccastbrief.org/e/${i.guid}/</guid>` +
        `<pubDate>Fri, 06 Nov 2026 12:00:00 GMT</pubDate><description>About ${i.title} &amp; more</description></item>`,
    )
    .join("");
  return `<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>${body}</channel></rss>`;
}

export function fetcherFor(xml: () => string) {
  return async () => new Response(xml(), { headers: { "Content-Type": "application/rss+xml" } });
}
