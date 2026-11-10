// SPDX-License-Identifier: Apache-2.0

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** The font files are served by the home page (civiccastbrief.org), which sends a CORS header for them. */
export const FONT_ORIGIN = "https://civiccastbrief.org";

/** Cloudflare Web Analytics: counts visits without cookies. Same site token as the city sites. */
export const ANALYTICS_TOKEN = "584d3422251846a3a68db1030f40f942";
export const ANALYTICS_SCRIPT = "https://static.cloudflareinsights.com/beacon.min.js";
const ANALYTICS_SCRIPT_ORIGIN = "https://static.cloudflareinsights.com";
const ANALYTICS_CONNECT_ORIGIN = "https://cloudflareinsights.com";

const font = (family: string, weight: number, file: string): string =>
  `@font-face { font-family: "${family}"; font-style: normal; font-weight: ${weight}; font-display: swap; src: url("${FONT_ORIGIN}/assets/fonts/${file}") format("woff2"); }`;

const LIGHT = `--paper: #f6f7fa; --surface: #fff; --ink: #161b2e; --muted: #586079; --rule: #dde1ea; --docket: #2340c4; --docket-soft: #e7ebfb;`;
const DARK = `--paper: #0e1120; --surface: #161a2c; --ink: #e8eaf3; --muted: #9aa1b8; --rule: #2a3048; --docket: #8ea2ff; --docket-soft: #1f2750;`;

export const STYLE = `
${font("Bricolage Grotesque", 700, "bricolage-grotesque-latin-700-normal.woff2")}
${font("Public Sans", 400, "public-sans-latin-400-normal.woff2")}
${font("Public Sans", 600, "public-sans-latin-600-normal.woff2")}
:root { ${LIGHT} color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ${DARK} } }
:root[data-theme="dark"] { ${DARK} }
* { box-sizing: border-box; }
body { margin: 0; font: 17px/1.6 "Public Sans", "Segoe UI", system-ui, sans-serif; color: var(--ink); background: var(--paper); }
main { max-width: 620px; margin: 0 auto; padding: 32px 16px 48px; }
.brand { display: flex; align-items: center; gap: 12px; color: var(--ink); text-decoration: none; }
.logo { flex: none; }
.logo-tile { fill: var(--docket); }
.logo-ink { fill: var(--surface); }
.logo-arc { stroke: var(--surface); }
.wordmark { font: 700 20px/1.1 "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif; letter-spacing: -0.01em; }
.wordmark b { color: var(--docket); }
.wordmark small { display: block; font: 600 13px/1.3 "Public Sans", "Segoe UI", system-ui, sans-serif; color: var(--muted); letter-spacing: 0; }
.card { margin-top: 28px; padding: 24px; background: var(--surface); border: 1px solid var(--rule); border-radius: 12px; }
h1 { font: 700 28px/1.2 "Bricolage Grotesque", "Segoe UI", system-ui, sans-serif; letter-spacing: -0.01em; margin: 0 0 12px; }
p, ul { margin: 0 0 12px; }
a { color: var(--docket); }
button { font: inherit; font-weight: 600; padding: 10px 20px; color: var(--surface); background: var(--docket); border: 0; border-radius: 6px; cursor: pointer; }
button:hover { opacity: 0.9; }
:focus-visible { outline: 3px solid var(--docket); outline-offset: 2px; }
.foot { margin-top: 20px; font-size: 13px; color: var(--muted); }
`;

const HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Content-Security-Policy":
    "default-src 'none'; style-src 'self'; " +
    `font-src ${FONT_ORIGIN}; script-src ${ANALYTICS_SCRIPT_ORIGIN}; connect-src ${ANALYTICS_CONNECT_ORIGIN}; ` +
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  // no-transform stops Cloudflare from adding its own copy of the analytics script.
  "Cache-Control": "no-store, no-transform",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

const LOGO =
  '<svg class="logo" viewBox="0 0 64 64" width="40" height="40" aria-hidden="true" focusable="false">' +
  '<rect class="logo-tile" width="64" height="64" rx="14"/><circle class="logo-ink" cx="19" cy="42" r="5.5"/>' +
  '<g class="logo-arc" fill="none" stroke-width="5" stroke-linecap="round"><path d="M19 29a13 13 0 0 1 13 13"/>' +
  '<path d="M19 18a24 24 0 0 1 24 24" opacity=".8"/><path d="M19 7.5a34.5 34.5 0 0 1 34.5 34.5" opacity=".55"/></g>' +
  '<rect class="logo-ink" x="11" y="51" width="42" height="4.5" rx="2.25"/></svg>';

/** The small city line under the wordmark: "CivicCast Brief: Longmont" shows "Longmont". */
function cityLine(brand: string): string {
  const prefix = "CivicCast Brief: ";
  return brand.startsWith(prefix) ? brand.slice(prefix.length) : "Email sign-up";
}

/** A small page. `bodyHtml` must already be escaped. */
export function page(title: string, bodyHtml: string, status = 200, brand = "CivicCast Brief"): Response {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="light dark">
<title>${escapeHtml(title)}</title>
<link rel="stylesheet" href="/assets/signup.css">
</head>
<body>
<main>
<header><a class="brand" href="${FONT_ORIGIN}/">${LOGO}<span class="wordmark">CivicCast <b>Brief</b><small>${escapeHtml(cityLine(brand))}</small></span></a></header>
<div class="card">
<h1>${escapeHtml(title)}</h1>
${bodyHtml}
</div>
<p class="foot">This page uses Cloudflare Web Analytics, which counts visits without cookies.</p>
</main>
<script defer src="${ANALYTICS_SCRIPT}" data-cf-beacon='{"token": "${ANALYTICS_TOKEN}"}'></script>
</body>
</html>
`;
  return new Response(html, { status, headers: HEADERS });
}
