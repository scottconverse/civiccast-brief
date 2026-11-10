# List a city show on Apple Podcasts and Spotify

The site build writes a feed at `<site>/feed.xml` that meets Apple's and Spotify's published rules. The build checks those rules and stops with a message when a setting breaks one. The rules and their sources are listed in `src/civiccast_brief/site/directories.py`.

Nobody has submitted a feed to Apple or Spotify yet. The feed was checked against test stand-ins for both directories (`tests/site/test_directories.py`), not against the real services.

## 1. Fill in `city.json`

The `podcast` block:

| Setting | What to put | Rule the build checks |
|---|---|---|
| `name` | The show name | Required |
| `author` | Who publishes it | Required (Spotify) |
| `owner_name` | A person or group name | Required |
| `owner_email` | An inbox someone reads | Must be an email address. Spotify sends its ownership code here. |
| `description` | One or two sentences about the show | At most 4,000 bytes (Apple) |
| `category` | `Government` (the default) or another Apple category | Must be spelled exactly as Apple lists it, for example `News` or `Society & Culture` |
| `subcategory` | Empty, or a subcategory of `category`, for example `Politics` under `News` | Must belong to `category`. `Government` has no subcategories. |
| `language` | `en-us` (the default) | Must be an ISO 639 code, such as `en` or `es-419` |
| `explicit` | `false` | true or false |

The full category list is `APPLE_CATEGORIES` in `directories.py`.

Show art: if `city.json` has no `artwork` setting, the build makes a 1400 x 1400 image. To use your own image, set `"artwork": "art.png"` (a file in the city folder). It must be:
- square, 1400 to 3000 pixels on each side;
- PNG or JPEG, with a file name ending that matches (`.png`, `.jpg` or `.jpeg`);
- RGB color with no transparency.

Episode audio must be MP3 (`audio/mpeg`) or AAC (`audio/x-m4a`). Spotify plays only MP3 from a feed, so use MP3.

Build the site and check that `<site>/feed.xml` opens in a browser before you submit it.

## 2. Apple Podcasts

1. Sign in at podcastsconnect.apple.com with an Apple Account.
2. Choose **+** then **New Show**, then **Add a show with an RSS feed**, and paste the feed address.
3. Fill in the parts that are not in the feed: content rights, contact information and where the show is available.
4. Submit for review. Apple emails the Apple Account when the review is done.

## 3. Spotify

1. Sign in at creators.spotify.com.
2. Choose **Add a new show**, then **Find an existing show**, then **Somewhere else**, and paste the feed address.
3. Spotify emails an 8-digit code to `owner_email`. Enter it to prove the show is yours.
4. Check the show details and submit.

## Not checked

- The real Apple and Spotify review steps (no accounts were used).
- The exact button names above can change; they were read from Apple's and Spotify's help pages on November 10, 2026.
- Apple's guide asks for chapter titles of 45 characters or fewer. The build does not refuse longer ones, because Apple's submission checks do not include that rule; the script writer is told the limit.
