// SPDX-License-Identifier: Apache-2.0

export interface CityConfig {
  /** Shown in emails and pages, for example "CivicCast Brief: Longmont". */
  title: string;
  /** The city site, for example https://longmont.civiccastbrief.org */
  site: string;
  /** The city podcast feed the sender reads for new episodes. */
  feed: string;
}

export interface OutgoingMail {
  to: string;
  from: { email: string; name: string };
  subject: string;
  text: string;
  html: string;
  headers?: Record<string, string>;
}

/** The part of the send_email binding this Worker uses. */
export interface Mailer {
  send(message: OutgoingMail): Promise<{ messageId: string }>;
}

export interface AppEnv {
  DB: D1Database;
  /** Cloudflare's send_email binding. Used when BREVO_API_KEY is not set. */
  EMAIL: Mailer;
  /** Brevo API key (app.brevo.com/settings/keys/api). Secret. When set, mail goes through Brevo. */
  BREVO_API_KEY?: string;
  /** HMAC key for links, ids and rate-limit keys. Secret. */
  TOKEN_KEY: string;
  /** 32 random bytes, base64, for sealing email addresses. Secret. */
  EMAIL_KEY: string;
  FROM_EMAIL: string;
  /** This Worker's own address, for example https://signup.civiccastbrief.org */
  PUBLIC_URL: string;
  CITIES: Record<string, CityConfig>;
}
