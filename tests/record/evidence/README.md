# Recorded fixtures (copied from TownRecord's oversight repository)

Captured 2026-09-27 about 2:06 to 2:07 PM MT by the coordinator from the home connection, with paced requests.
User-Agent for PrimeGov: "TownRecord/0.0 (fixture capture; github.com/scottconverse/townrecord)".

- primegov/: Longmont PrimeGov ListArchivedMeetings?year=2026 (241 meetings), ListUpcomingMeetings,
  HTML agendas for templateId 16805 (Sept 8 2026 regular session, meeting 3709, video 3qfQAkAAC9U),
  16095 (June 2 2026 study session, meeting 3560, video Y82AJ7hmmyk), 16821 (Sept 22 2026 regular session,
  meeting 3712, video jhsFsEz0P5A), and the compiled Agenda PDF for 16805 (via the signed-link redirect).
  16805 carries data-videolocation values including placeholders 585015 and 585021 seconds (162:30:15+).
- youtube/rss-*.xml: channel RSS for City of Longmont (UCH5_wkpLrKYb1JuUk6-UdNg) and Longmont Public Media
  (UCXFW3IRzfCc6q_XM-uutAmw), 15 entries each.
- youtube/captions/: yt-dlp 2026.08.19 caption capture (spec 8.3 command) of 3qfQAkAAC9U: .en.srv3 and .info.json
  (video duration 14407 s). The info.json holds signed media URLs, so it is not in this copy (no test reads it).

- youtube/captions-vtt/: auto-caption VTT of 3qfQAkAAC9U (2:12 PM). YouTube rolling format.
- youtube/flat-UCH5-streams.json: yt-dlp flat listing of the City channel /streams, 50 entries (2:13 PM).

| File | Bytes | SHA-256 (first 16) |
|---|---|---|
| primegov/longmont-agenda-16805.pdf | 1,469,670 | 1cca62438cd75b23 |
| primegov/longmont-html-agenda-16095.html | 174,344 | 41bb4cd6b371d1e2 |
| primegov/longmont-html-agenda-16805.html | 263,687 | b4f2a398a1a903f3 |
| primegov/longmont-html-agenda-16821.html | 256,525 | d3830fdf0a10a16e |
| primegov/longmont-ListArchivedMeetings-2026.json | 269,866 | 69aa2472ecfd447e |
| primegov/longmont-ListUpcomingMeetings.json | 7,648 | 472bf9a0a8e90cf4 |
| youtube/captions/3qfQAkAAC9U.en.srv3 | 1,429,554 | b3199ecde3fe0244 |
| youtube/rss-UCH5_wkpLrKYb1JuUk6-UdNg.xml | 20,474 | 1aab49dcb7cc60bb |
| youtube/rss-UCXFW3IRzfCc6q_XM-uutAmw.xml | 25,681 | d855c881212edca8 |
| youtube/captions-vtt/3qfQAkAAC9U.en.vtt | 1,914,067 | fb8048ccfdc9060d |
| youtube/flat-UCH5-streams.json | 41,654 | a53867c1adf6b2c4 |
- `primegov/longmont-packet-16823-minutes-sep08-p17-38.pdf`: pages 17 to 38 of the Sept 22, 2026 City Council Regular Session packet (meeting 3712, template 16823, 864 pages, 109,733,479 bytes, SHA-256 9642d37c178b8043...), cut with pypdf 6.19.0 at 4:44 PM 9/27/2026. Holds the draft minutes of the Sept 8, 2026 regular session (packet pages 17 to 37). 1,876,961 bytes, SHA-256 f00ece287947963c0b977e04ad6f830bf3132b0ae4530557303e6e29de9caa5b. File page 1 = packet page 17.
