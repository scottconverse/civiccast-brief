import { beforeEach, describe, expect, it } from "vitest";
import { handleFetch, runSender } from "../src/app";
import { BREVO_URL, brevoMailer, withMailer } from "../src/brevo";
import type { AppEnv, OutgoingMail } from "../src/env";
import { feedXml, form, harness, type Harness } from "./helpers";

const T0 = Date.parse("2026-11-07T12:00:00Z");
const KEY = "test-brevo-key-not-real";

type Json = Record<string, any>;

/**
 * A stand-in for Brevo's POST /v3/smtp/email. It checks each request against Brevo's
 * published contract on its own terms (developers.brevo.com/reference/sendtransacemail)
 * and answers the way Brevo does.
 */
function mockBrevo(opts: { verifiedSenders?: string[]; status?: number; reply?: unknown } = {}) {
  const calls: { headers: Headers; body: Json }[] = [];
  const problems: string[] = [];
  const verified = opts.verifiedSenders ?? ["brief@civiccastbrief.org"];
  const fetcher = async (url: string, init?: RequestInit): Promise<Response> => {
    if (url !== BREVO_URL) return new Response("not mocked", { status: 404 });
    const headers = new Headers(init?.headers);
    const body = JSON.parse(String(init?.body)) as Json;
    calls.push({ headers, body });
    const json = (status: number, data: unknown) =>
      new Response(data === undefined ? "Internal error" : JSON.stringify(data), {
        status,
        headers: { "content-type": "application/json" },
      });
    if (init?.method !== "POST") problems.push("method is not POST");
    if (headers.get("api-key") !== KEY) return json(401, { code: "unauthorized", message: "Key not found" });
    if (headers.get("content-type") !== "application/json")
      return json(406, { code: "not_acceptable", message: "Content-Type must be application/json" });
    if (opts.status) return json(opts.status, opts.reply);
    if (!verified.includes(body.sender?.email))
      return json(400, { code: "invalid_parameter", message: "Sender is not valid" });
    if (!Array.isArray(body.to) || !body.to.length || !body.to.every((t: Json) => typeof t.email === "string"))
      problems.push("to must be a non-empty list of {email}");
    if (typeof body.subject !== "string" || !body.subject) problems.push("subject missing");
    if (typeof body.htmlContent !== "string" && typeof body.textContent !== "string")
      problems.push("htmlContent or textContent required");
    for (const name of Object.keys(body.headers ?? {})) {
      if (/^(list-|auto-submitted$|from$|to$|subject$|reply-to$|date$|message-id$)/i.test(name))
        problems.push(`standard header ${name} sent in headers`);
    }
    const allowed = new Set(["sender", "to", "subject", "htmlContent", "textContent", "headers", "replyTo", "tags"]);
    for (const k of Object.keys(body)) if (!allowed.has(k)) problems.push(`unknown field ${k}`);
    if (problems.length) return json(400, { code: "invalid_parameter", message: problems.join("; ") });
    return json(201, { messageId: `<${calls.length}.mock@smtp-relay.mailin.fr>` });
  };
  return { fetcher, calls, problems };
}

const MAIL: OutgoingMail = {
  to: "reader@example.org",
  from: { email: "brief@civiccastbrief.org", name: "CivicCast Brief: Longmont" },
  subject: "Hello",
  text: "plain",
  html: "<p>html</p>",
  headers: { "List-Unsubscribe": "<https://x/u>", "Auto-Submitted": "auto-generated", "X-Brief-City": "longmont" },
};

describe("Brevo mailer", () => {
  it("sends a request Brevo accepts and returns its messageId", async () => {
    const brevo = mockBrevo();
    const sent = await brevoMailer(KEY, brevo.fetcher).send(MAIL);
    expect(brevo.problems).toEqual([]);
    expect(sent.messageId).toBe("<1.mock@smtp-relay.mailin.fr>");
    const { body, headers } = brevo.calls[0];
    expect(headers.get("api-key")).toBe(KEY);
    expect(body).toEqual({
      sender: { email: "brief@civiccastbrief.org", name: "CivicCast Brief: Longmont" },
      to: [{ email: "reader@example.org" }],
      subject: "Hello",
      htmlContent: "<p>html</p>",
      textContent: "plain",
      // Only the custom header goes to Brevo; standard ones are not supported there.
      headers: { "X-Brief-City": "longmont" },
    });
  });

  it("leaves headers out when only standard ones were set", async () => {
    const brevo = mockBrevo();
    await brevoMailer(KEY, brevo.fetcher).send({ ...MAIL, headers: { "Auto-Submitted": "auto-generated" } });
    expect(brevo.calls[0].body).not.toHaveProperty("headers");
  });

  it("reads messageIds when Brevo answers with a list", async () => {
    const brevo = mockBrevo({ status: 201, reply: { messageIds: ["<a@x>", "<b@x>"] } });
    expect((await brevoMailer(KEY, brevo.fetcher).send(MAIL)).messageId).toBe("<a@x>");
  });

  it.each([
    ["a wrong key", { key: "wrong" }, "unauthorized", 401],
    ["an unverified sender", { from: "someone@else.org" }, "invalid_parameter", 400],
    ["no credits left", { status: 402, reply: { code: "not_enough_credits", message: "No credits" } }, "not_enough_credits", 402],
    ["the rate limit", { status: 429, reply: { code: "too_many_requests", message: "Slow down" } }, "too_many_requests", 429],
    ["a non-JSON 500", { status: 500 }, "http_500", 500],
  ] as [string, Json, string, number][])("throws code and status on %s", async (_name, o, code, status) => {
    const brevo = mockBrevo(o.status ? { status: o.status, reply: o.reply } : {});
    const mail = o.from ? { ...MAIL, from: { ...MAIL.from, email: o.from } } : MAIL;
    const err = await brevoMailer(o.key ?? KEY, brevo.fetcher)
      .send(mail)
      .catch((e) => e);
    expect(err).toMatchObject({ code, status });
    expect(err.message).toContain(`Brevo ${status}`);
  });

  it("refuses a 2xx with no messageId", async () => {
    const brevo = mockBrevo({ status: 202, reply: {} });
    await expect(brevoMailer(KEY, brevo.fetcher).send(MAIL)).rejects.toMatchObject({ code: "no_message_id" });
  });
});

describe("mailer choice", () => {
  let h: Harness;
  beforeEach(async () => {
    h = await harness();
  });

  it("keeps the send_email binding when no Brevo key is set", () => {
    expect(withMailer(h.env).EMAIL).toBe(h.env.EMAIL);
  });

  it("says what to set when there is no mailer at all", () => {
    const env = { ...h.env, EMAIL: undefined } as unknown as AppEnv;
    expect(() => withMailer(env)).toThrow(/BREVO_API_KEY/);
  });

  it("runs sign-up, confirm and an episode email end to end through mock Brevo", async () => {
    const brevo = mockBrevo();
    let items = [{ guid: "old", title: "Old meeting" }];
    const fetcher = async (url: string, init?: RequestInit) =>
      url === BREVO_URL ? brevo.fetcher(url, init) : new Response(feedXml(items));
    const env = withMailer({ ...h.env, BREVO_API_KEY: KEY }, fetcher);

    const res = await handleFetch(form("/subscribe", { email: "reader@example.org", city: "longmont" }), env, T0);
    expect(res.status).toBe(200);
    expect(brevo.calls).toHaveLength(1);
    const confirm = String(brevo.calls[0].body.textContent).match(/https:\/\/signup\.civiccastbrief\.org\/confirm\?t=\S+/);
    expect(confirm).not.toBeNull();
    const t = new URL((confirm as RegExpMatchArray)[0]).searchParams.get("t") ?? "";
    expect((await handleFetch(form("/confirm", { t }), env, T0 + 1)).status).toBe(200);

    await runSender(env, fetcher, T0 + 60_000);
    items = [{ guid: "new", title: "Council: budget" }, ...items];
    expect(await runSender(env, fetcher, T0 + 16 * 60_000)).toMatchObject({ sent: 1, failed: 0 });
    expect(brevo.problems).toEqual([]);
    expect(brevo.calls).toHaveLength(2);
    const episode = brevo.calls[1].body;
    expect(episode.to).toEqual([{ email: "reader@example.org" }]);
    expect(episode.textContent).toContain("https://signup.civiccastbrief.org/unsubscribe?t=");
    const row = await h.env.DB.prepare("SELECT status, message_id FROM deliveries").first();
    expect(row).toMatchObject({ status: "sent", message_id: "<2.mock@smtp-relay.mailin.fr>" });
  });

  it("records Brevo's error on a failed episode email", async () => {
    const brevo = mockBrevo({ status: 402, reply: { code: "not_enough_credits", message: "No credits" } });
    let items = [{ guid: "old", title: "Old meeting" }];
    const fetcher = async (url: string, init?: RequestInit) =>
      url === BREVO_URL ? brevo.fetcher(url, init) : new Response(feedXml(items));
    // Sign up with the binding, then switch to a Brevo account that is out of credits.
    await handleFetch(form("/subscribe", { email: "reader@example.org", city: "longmont" }), h.env, T0);
    const t = new URL(String(h.sent[0].text.match(/https:\/\/\S+\/confirm\?t=\S+/)?.[0])).searchParams.get("t") ?? "";
    await handleFetch(form("/confirm", { t }), h.env, T0 + 1);
    const env = withMailer({ ...h.env, BREVO_API_KEY: KEY }, fetcher);
    await runSender(env, fetcher, T0 + 60_000);
    items = [{ guid: "new", title: "Council: budget" }, ...items];
    expect(await runSender(env, fetcher, T0 + 16 * 60_000)).toMatchObject({ sent: 0, failed: 1 });
    const row = await h.env.DB.prepare("SELECT status, error FROM deliveries").first();
    expect(row).toEqual({ status: "failed", error: "not_enough_credits: Brevo 402: No credits" });
  });
});
