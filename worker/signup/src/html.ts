// SPDX-License-Identifier: Apache-2.0

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export const STYLE = `
body { margin: 0; font: 17px/1.55 Calibri, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a2e; background: #ffffff; }
.bar { height: 4px; background: #e8a838; }
main { max-width: 620px; margin: 0 auto; padding: 32px 16px 48px; }
.brand { font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #0b7383; font-size: 14px; }
h1 { font-family: Georgia, "Times New Roman", serif; color: #0d4f5c; font-size: 30px; line-height: 1.2; margin: 10px 0 14px; }
a { color: #0b7383; }
button { font: inherit; font-weight: 600; padding: 10px 20px; color: #ffffff; background: #0d4f5c; border: 0; border-radius: 6px; cursor: pointer; }
button:hover { background: #0b7383; }
:focus-visible { outline: 3px solid #0d4f5c; outline-offset: 2px; }
`;

const HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Content-Security-Policy":
    "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

/** A small page. `bodyHtml` must already be escaped. */
export function page(title: string, bodyHtml: string, status = 200, brand = "CivicCast Brief"): Response {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escapeHtml(title)}</title>
<link rel="stylesheet" href="/assets/signup.css">
</head>
<body>
<div class="bar" aria-hidden="true"></div>
<main>
<p class="brand">${escapeHtml(brand)}</p>
<h1>${escapeHtml(title)}</h1>
${bodyHtml}
</main>
</body>
</html>
`;
  return new Response(html, { status, headers: HEADERS });
}
