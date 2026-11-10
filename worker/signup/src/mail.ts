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

// Inline styles only: mail clients drop style sheets. Colours are the site's light tokens.
const INK = "#161b2e";
const MUTED = "#586079";
const RULE = "#dde1ea";
const DOCKET = "#2340c4";
const PAPER = "#f6f7fa";
const SANS = "'Public Sans','Segoe UI',Helvetica,Arial,sans-serif";
const DISPLAY = "'Bricolage Grotesque','Segoe UI',Helvetica,Arial,sans-serif";
const BUTTON = `display:inline-block;padding:11px 20px;color:#ffffff;background:${DOCKET};border-radius:6px;font-weight:600;text-decoration:none`;
const SMALL = `font-size:14px;color:${MUTED}`;
const LINK = `color:${DOCKET}`;

/** Wordmark "CivicCast Brief" with the city line, in text so it shows when images are blocked. */
function masthead(city: CityConfig): string {
  const prefix = "CivicCast Brief: ";
  const line = city.title.startsWith(prefix) ? city.title.slice(prefix.length) : city.title;
  return (
    `<p style="margin:0 0 20px;font:700 20px/1.15 ${DISPLAY};color:${INK}">CivicCast <span style="color:${DOCKET}">Brief</span>` +
    `<br><span style="font:600 13px/1.3 ${SANS};color:${MUTED}">${escapeHtml(line)}</span></p>`
  );
}

function wrap(city: CityConfig, inner: string): string {
  return (
    `<!DOCTYPE html><html lang="en"><body style="margin:0;padding:24px 16px;font:16px/1.6 ${SANS};color:${INK};background:${PAPER}">` +
    `<div style="max-width:600px;margin:0 auto">${masthead(city)}` +
    `<div style="background:#ffffff;border:1px solid ${RULE};border-radius:12px;padding:24px">${inner}</div></div></body></html>`
  );
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
    city,
    `<p>Someone asked to get an email from ${escapeHtml(city.title)} each time a new episode is posted.</p>` +
      `<p>If that was you, confirm here:</p>` +
      `<p><a href="${escapeHtml(confirmUrl)}" style="${BUTTON}">Confirm my sign-up</a></p>` +
      `<p style="${SMALL}">Or copy this link into your browser:<br>${escapeHtml(confirmUrl)}</p>` +
      `<p>The link works for ${CONFIRM_DAYS} days. If you did not ask for this, ignore this email. Nothing more will be sent.</p>` +
      `<p style="${SMALL};margin-bottom:0">${escapeHtml(city.title)}<br><a href="${escapeHtml(city.site)}" style="${LINK}">${escapeHtml(city.site)}</a></p>`,
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
    city,
    `<p style="margin:0 0 6px;font:600 12px/1.3 ${SANS};letter-spacing:0.06em;text-transform:uppercase;color:${MUTED}">New episode</p>` +
      `<h1 style="font:700 24px/1.25 ${DISPLAY};color:${INK};margin:0 0 12px">${escapeHtml(item.title)}</h1>` +
      `<p>${escapeHtml(item.description)}</p>` +
      `<p><a href="${escapeHtml(item.link)}" style="${BUTTON}">Listen and read the cited recap</a></p>` +
      `<hr style="border:0;border-top:1px solid ${RULE};margin:24px 0">` +
      `<p style="${SMALL};margin-bottom:0">You get this email because you signed up at <a href="${escapeHtml(city.site)}" style="${LINK}">${escapeHtml(city.site)}</a>. <a href="${escapeHtml(unsubscribeUrl)}" style="${LINK}">Unsubscribe</a>.</p>`,
  );
  return { subject, text, html };
}
