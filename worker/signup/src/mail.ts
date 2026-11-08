// SPDX-License-Identifier: Apache-2.0
// Mail texts. Adapted from civiccast-native civiccast/subscribe/delivery.py
// (Copyright (c) The CivicCast Authors, Apache-2.0).

import type { CityConfig } from "./env";
import type { FeedItem } from "./feed";
import { escapeHtml } from "./html";

export const CONFIRM_DAYS = 7;

export interface MailBody {
  subject: string;
  text: string;
  html: string;
}

function wrap(inner: string): string {
  return `<!DOCTYPE html><html lang="en"><body style="margin:0;padding:24px 16px;font:16px/1.55 Calibri,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a2e;background:#ffffff"><div style="max-width:600px;margin:0 auto">${inner}</div></body></html>`;
}

export function confirmMail(city: CityConfig, confirmUrl: string): MailBody {
  const subject = `Confirm your ${city.title} emails`;
  const text = [
    `Someone asked to get an email from ${city.title} each time a new episode is posted.`,
    "",
    "If that was you, confirm here:",
    confirmUrl,
    "",
    `The link works for ${CONFIRM_DAYS} days. If you did not ask for this, ignore this email. Nothing more will be sent.`,
    "",
    city.title,
    city.site,
  ].join("\n");
  const html = wrap(
    `<p>Someone asked to get an email from ${escapeHtml(city.title)} each time a new episode is posted.</p>` +
      `<p>If that was you, confirm here:</p>` +
      `<p><a href="${escapeHtml(confirmUrl)}" style="display:inline-block;padding:10px 20px;color:#ffffff;background:#0d4f5c;border-radius:6px;font-weight:600;text-decoration:none">Confirm my sign-up</a></p>` +
      `<p style="font-size:14px;color:#4a5568">Or copy this link into your browser:<br>${escapeHtml(confirmUrl)}</p>` +
      `<p>The link works for ${CONFIRM_DAYS} days. If you did not ask for this, ignore this email. Nothing more will be sent.</p>` +
      `<p style="font-size:14px;color:#4a5568">${escapeHtml(city.title)}<br><a href="${escapeHtml(city.site)}" style="color:#0b7383">${escapeHtml(city.site)}</a></p>`,
  );
  return { subject, text, html };
}

function cut(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}…`;
}

export function episodeMail(city: CityConfig, item: FeedItem, unsubscribeUrl: string): MailBody {
  const subject = cut(`New on ${city.title}: ${item.title}`, 180);
  const text = [
    item.title,
    "",
    item.description,
    "",
    `Listen and read the cited recap: ${item.link}`,
    "",
    "--",
    `You get this email because you signed up at ${city.site}.`,
    `Unsubscribe: ${unsubscribeUrl}`,
  ].join("\n");
  const html = wrap(
    `<p style="font-size:13px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:#0b7383">${escapeHtml(city.title)}</p>` +
      `<h1 style="font-family:Georgia,'Times New Roman',serif;font-size:24px;line-height:1.25;color:#0d4f5c;margin:0 0 12px">${escapeHtml(item.title)}</h1>` +
      `<p>${escapeHtml(item.description)}</p>` +
      `<p><a href="${escapeHtml(item.link)}" style="display:inline-block;padding:10px 20px;color:#ffffff;background:#0d4f5c;border-radius:6px;font-weight:600;text-decoration:none">Listen and read the cited recap</a></p>` +
      `<hr style="border:0;border-top:1px solid #d1d5db;margin:24px 0">` +
      `<p style="font-size:14px;color:#4a5568">You get this email because you signed up at <a href="${escapeHtml(city.site)}" style="color:#0b7383">${escapeHtml(city.site)}</a>. <a href="${escapeHtml(unsubscribeUrl)}" style="color:#0b7383">Unsubscribe</a>.</p>`,
  );
  return { subject, text, html };
}
