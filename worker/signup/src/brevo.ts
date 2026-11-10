// SPDX-License-Identifier: Apache-2.0
// Sends mail through Brevo's transactional API instead of Cloudflare's send_email binding.
// Contract: developers.brevo.com/reference/sendtransacemail (read November 10, 2026).
//   POST https://api.brevo.com/v3/smtp/email, header "api-key", JSON body
//   { sender {email,name}, to [{email}], subject, htmlContent, textContent, headers? }.
//   201 -> { messageId }; errors -> { code, message } with 400/401/402/403/404/406/429.

import type { AppEnv, Mailer, OutgoingMail } from "./env";

export const BREVO_URL = "https://api.brevo.com/v3/smtp/email";

/**
 * Brevo's `headers` field takes "custom email headers (non-standard headers)" only;
 * "standard email headers are not supported". These are the standard ones this Worker
 * sets, so they are left out. Brevo handles unsubscribe headers itself; the unsubscribe
 * link in the mail body still works.
 */
const STANDARD_HEADERS = new Set(["list-unsubscribe", "list-unsubscribe-post", "list-id", "auto-submitted"]);

export class BrevoError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

export function brevoBody(mail: OutgoingMail): Record<string, unknown> {
  const body: Record<string, unknown> = {
    sender: { email: mail.from.email, name: mail.from.name },
    to: [{ email: mail.to }],
    subject: mail.subject,
    htmlContent: mail.html,
    textContent: mail.text,
  };
  const custom = Object.entries(mail.headers ?? {}).filter(([name]) => !STANDARD_HEADERS.has(name.toLowerCase()));
  if (custom.length) body.headers = Object.fromEntries(custom);
  return body;
}

export function brevoMailer(apiKey: string, fetcher: Fetcher = fetch): Mailer {
  return {
    async send(mail) {
      const res = await fetcher(BREVO_URL, {
        method: "POST",
        headers: { "api-key": apiKey, "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify(brevoBody(mail)),
      });
      const raw = await res.text();
      let data: { messageId?: string; messageIds?: string[]; code?: string; message?: string } = {};
      try {
        data = raw ? JSON.parse(raw) : {};
      } catch {
        // Not JSON; the status says what happened.
      }
      if (!res.ok) {
        throw new BrevoError(data.code ?? `http_${res.status}`, `Brevo ${res.status}: ${data.message ?? raw.slice(0, 200)}`, res.status);
      }
      const messageId = data.messageId ?? data.messageIds?.[0];
      if (!messageId) throw new BrevoError("no_message_id", `Brevo ${res.status} gave no messageId`, res.status);
      return { messageId };
    },
  };
}

/** Brevo when BREVO_API_KEY is set, else the send_email binding. */
export function withMailer(env: AppEnv, fetcher: Fetcher = fetch): AppEnv {
  if (env.BREVO_API_KEY) return { ...env, EMAIL: brevoMailer(env.BREVO_API_KEY, fetcher) };
  if (!env.EMAIL) throw new Error("No mailer: set the BREVO_API_KEY secret or the send_email binding EMAIL");
  return env;
}
