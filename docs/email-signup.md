# Email sign-up

Readers can get one email per new episode. The sign-up runs on a small Cloudflare Worker in `worker/signup`, separate from the static city site.

## How it works

1. A reader types an address into the form at the bottom of every city page and presses **Sign up**.
2. The Worker sends one confirmation email. Nothing else goes to that address until it is confirmed.
3. The link in that email opens a page with a **Confirm my sign-up** button. Opening the link alone confirms nothing, because mail scanners open links. The link works for 7 days.
4. Every 15 minutes the Worker reads each city's podcast feed. When an episode appears that was not in the feed before, each confirmed reader gets one email about it.
5. Every episode email has an unsubscribe link, and mail apps can unsubscribe in one click (RFC 8058).

Episodes already in the feed when a city is first added are never emailed. A reader only gets episodes that appear after they confirm.

## What is stored

- Addresses are encrypted (AES-GCM) before they are stored. The key is a Worker secret.
- The subscription id is a keyed hash of the address and city, so the same address can be found again without storing it in plain text.
- Rate-limit records use a keyed hash of the visitor's IP address, and are deleted after 2 days.

## Limits

- 5 sign-ups an hour from one IP address.
- 3 confirmation emails a day to one address.
- Up to 25 episode emails per 15-minute run, and up to 3 tries per email.

## Set up a city

1. Add the city to `CITIES` in `worker/signup/wrangler.jsonc` (title, site, feed) and deploy the Worker.
2. Add `"signup_url": "https://signup.civiccastbrief.org/subscribe"` to the city's `city.json` and rebuild the site. The form and the page's security policy follow from that one setting.

## Deploy

From `worker/signup`:

```bash
npm ci
npm test
npx wrangler d1 migrations apply civiccast-brief-signup --remote
npx wrangler deploy
```

`TOKEN_KEY` (random text) and `EMAIL_KEY` (32 random bytes, base64) are Worker secrets. Changing `EMAIL_KEY` makes stored addresses unreadable. Changing `TOKEN_KEY` breaks every link already sent and every stored subscription id.

## Sending limits on the free plan

On the Workers Free plan, Cloudflare only delivers to addresses verified in the account's Email Routing destinations. Sending to the public needs Workers Paid. Until then, sign-ups from other addresses are refused with a "could not send" page.
