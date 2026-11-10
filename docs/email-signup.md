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
2. Add `"signup_url": "https://signup.civiccastbrief.org/subscribe"` and `"email_signup": true` to the city's `city.json` and rebuild the site. The form, the "By email" band on the front page and the page's security policy follow from those two settings. `signup_url` alone shows nothing; `email_signup` without `signup_url` is refused.

## Look and privacy

The sign-up pages use the site's colours, logo and fonts. The fonts load from
`https://civiccastbrief.org/assets/fonts/`; the home page build sends a CORS header for them. The pages
load the Cloudflare Web Analytics beacon (no cookies), and their security policy allows only that
script, its endpoint and the font origin. Emails use inline styles only and carry no analytics.

## Deploy

From `worker/signup`:

```bash
npm ci
npm test
npx wrangler d1 migrations apply civiccast-brief-signup --remote
npx wrangler deploy
```

`TOKEN_KEY` (random text) and `EMAIL_KEY` (32 random bytes, base64) are Worker secrets. Changing `EMAIL_KEY` makes stored addresses unreadable. Changing `TOKEN_KEY` breaks every link already sent and every stored subscription id.

## Send through Brevo (free plan)

When the Worker secret `BREVO_API_KEY` is set, all mail goes through Brevo's transactional API (`worker/signup/src/brevo.ts`). Without it, the Worker uses Cloudflare's `send_email` binding. Brevo's free plan sends 300 emails a day and adds a "Sent with Brevo" line to each one.

Nobody has sent through a real Brevo account yet. The code was tested against a stand-in for Brevo's API (`worker/signup/test/brevo.test.ts`) that checks each request against Brevo's published format.

What a person fills in:

1. **Account.** Sign up for the free plan at brevo.com.
2. **Sender.** In Brevo, go to Senders, Domains & Dedicated IPs. Either add `brief@civiccastbrief.org` as a sender and enter the 6-digit code Brevo emails to it, or authenticate the whole domain: Brevo shows DNS records (a Brevo code TXT record, DKIM and DMARC) to add in Cloudflare DNS for `civiccastbrief.org`. Domain authentication gives better delivery. The sender must match `FROM_EMAIL` in `wrangler.jsonc`.
3. **API key.** Go to Settings, then SMTP & API, then API Keys (app.brevo.com/settings/keys/api), and create a key. Brevo shows it once.
4. **Allow the Worker to call Brevo.** Cloudflare Workers do not send from fixed IP addresses. In Brevo, go to Settings, then Security, then Authorized IPs, and turn off IP blocking for the API. Brevo can turn this blocking on by itself, and then every send fails with a 401.
5. **Give the key to the Worker.** From `worker/signup`:

   ```bash
   npx wrangler secret put BREVO_API_KEY
   ```

   Paste the key when asked. No deploy is needed for a secret change.

To go back to Cloudflare's sender, delete the secret with `npx wrangler secret delete BREVO_API_KEY`.

What changes with Brevo:
- Brevo's `headers` field takes only non-standard headers, so the Worker leaves out `List-Unsubscribe`, `List-Unsubscribe-Post`, `List-Id` and `Auto-Submitted`. The unsubscribe link in every email body still works. Brevo may add its own unsubscribe header (not checked).
- A failed send is stored in `deliveries.error` as Brevo's code and message, for example `not_enough_credits: Brevo 402: ...` or `unauthorized: Brevo 401: ...`.
- More than 300 sends in a day: Brevo refuses the rest. Each refused episode email is stored as failed and tried again by later runs, up to 3 attempts in all. Runs are 15 minutes apart, so all 3 can fall on the same day and that email is then not sent. A refused confirmation email shows the "could not send" page.

## Sending limits on the free plan

On the Workers Free plan, Cloudflare only delivers to addresses verified in the account's Email Routing destinations. Sending to the public needs Workers Paid, or Brevo (above). Until then, sign-ups from other addresses are refused with a "could not send" page.
