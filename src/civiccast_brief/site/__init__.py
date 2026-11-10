"""Phase 6: the static city site, the podcast feed and the search index.

Build: ``python -m civiccast_brief.site build --episodes DIR --out DIR --base-url URL``.

NOTICE: where each part came from. Licences were checked before any copying.

- templates/base.html, templates/episode.html and the other page templates: adapted from
  CivicNewspaper's templates/index.html, post.html, styles.css and print.css. CivicNewspaper is
  MIT licensed, Copyright (c) 2026 Scott Converse. The copyright line and the MIT permission
  notice are reproduced in the comment at the top of templates/base.html, as the licence requires
  for substantial portions. The CivicNewspaper header (nav, masthead, CSP meta) is adapted; the CSP
  is changed to allow the two scripts this site ships.
- static/site.css: new code for the 2026-11-08 look (colour tokens, layout and player styles from
  the CivicCast Brief mockup). print.css is adapted from CivicNewspaper's print.css.
- static/fonts/: Bricolage Grotesque, Public Sans, Source Serif 4 and IBM Plex Mono, latin subsets,
  self-hosted. All four are under the SIL Open Font License 1.1; each licence is in static/fonts/.
- templates/_logo.html, static/favicon.svg: logo A (the cast mark), new for this project.
- static/player.js: behaviour (speed steps 1 to 2x, resume within the browser tab, chapter seek)
  follows the Craft kit's app/src/client/meeting.js. It is rewritten here, not copied. Resume uses
  sessionStorage: it stays in the browser and is never sent anywhere.
- static/search.js: client-side search over search-index.json. New code.
- feed.py: RSS 2.0 layout and the enclosure and item rules from civiccast-native
  civiccast/podcast/rss.py (Apache-2.0, SPDX header in that file). Channel layout, itunes:duration
  in seconds and the podcast:guid rule from the Craft kit's app/src/lib/feeds.mjs (Scott's project).
  content:encoded, podcast:chapters and podcast:transcript are added here.
- the RSS date format: CivicNewspaper src-tauri/src/core/compiler.rs (MIT).
- images.py: the share image, podcast artwork and favicon PNG draw logo A with the standard
  library. No image is copied from any source.
- Analytics: every page loads the Cloudflare Web Analytics beacon
  (static.cloudflareinsights.com). It sets no cookies. The CSP allows only that script and its
  endpoint. Emails carry no analytics.

Limits: share and artwork images carry no text (no font rendering without a dependency).
"""
