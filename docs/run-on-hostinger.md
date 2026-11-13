# Run CivicCast Brief on Hostinger

> **Not tested on Linux.** CivicCast Brief is built and tested on Windows, and on Cloudflare. A
> Hostinger VPS is a Linux server. This guide has not been run there. Expect to fix small things.

Cloudflare is the default host (see [set-up-a-city.md](set-up-a-city.md)). This guide is for
running the same parts on Hostinger instead.

## Which Hostinger plan

| Plan | What it can run |
|---|---|
| Web or shared hosting | The public site and its media only (plain files). The station cannot run there, because it is a program that stays on. |
| VPS (a Linux server) | Everything the cloud runs: the station, the desk at `/desk`, the weekly job and the public site. |

On either plan, a Windows PC still fetches the YouTube captions and makes the audio
([set-up-a-city.md, section 10](set-up-a-city.md#10-a-cloud-station-with-a-windows-pc-worker)).

## Shared hosting: the site only

Run the station on a Windows PC as usual, and give its `publish` step the folder target instead
of Cloudflare:

```
["{python}", "-m", "civiccast_brief.station", "deploy", "folder", "--site", "live/site", "--media", "live/media", "--out", "upload/site", "--media-out", "upload/media"]
```

Then copy `upload/site` into the domain's `public_html` folder and `upload/media` into the media
address's folder, with Hostinger's File Manager or SFTP. The copy is a step you do by hand: the
folder target only writes the files on the PC. The desk stays on the PC.

## VPS: the whole station

The commands below are for Ubuntu, Hostinger's usual VPS system. Run them as a user that is not
root, with `sudo` where shown.

### 1. Install

```
sudo apt update
sudo apt install -y git nginx certbot python3-certbot-nginx
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/scottconverse/civiccast-brief.git ~/civiccast-brief
cd ~/civiccast-brief
uv sync
```

`uv sync` brings Python 3.12 when the server has none. The server does not need ffmpeg or a GPU:
the Windows PC makes the audio.

### 2. station.json

Make `~/station/station.json`. Use the `"worker"` folder so YouTube captions and audio come from
the PC, and publish with the folder target into the web server's folders:

```
{
  "db": "desk.db",
  "city": "/home/YOU/civiccast-brief/cities/yourcity",
  "schedule": {"day": "monday", "time": "06:00", "timezone": "America/Denver"},
  "worker": "worker",
  "job": [["{python}", "-m", "civiccast_brief.weekly", "run", "--worker", "worker", "--city", "/home/YOU/civiccast-brief/cities/yourcity", "..."]],
  "publish": [
    ["{python}", "-m", "civiccast_brief.weekly", "publish", "--worker", "worker", "--city", "/home/YOU/civiccast-brief/cities/yourcity", "..."],
    ["{python}", "-m", "civiccast_brief.station", "deploy", "folder", "--site", "live/site", "--media", "live/media", "--out", "/var/www/yourcity", "--media-out", "/var/www/yourcity-media"]
  ]
}
```

Fill in the `...` the same way as on Windows (set-up-a-city.md, section 9). Give `--base-url` the
site's address and `--media-base-url` the media address. Make the two web folders yours:

```
sudo mkdir -p /var/www/yourcity /var/www/yourcity-media
sudo chown $USER /var/www/yourcity /var/www/yourcity-media
```

### 3. The worker token

Make a long random token and keep it in a file only you can read:

```
umask 077
echo "CIVICCAST_WORKER_TOKEN=$(openssl rand -hex 32)" > ~/station/worker.env
```

Set the same token on the Windows PC (set-up-a-city.md, section 10).

### 4. Run the station as a service

Make `/etc/systemd/system/civiccast-station.service` (change `YOU` to your user name):

```
[Unit]
Description=CivicCast Brief station
After=network-online.target

[Service]
User=YOU
WorkingDirectory=/home/YOU/station
EnvironmentFile=/home/YOU/station/worker.env
ExecStart=/home/YOU/civiccast-brief/.venv/bin/python -m civiccast_brief.station serve --config /home/YOU/station/station.json
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now civiccast-station
journalctl -u civiccast-station -f
```

The station listens on `127.0.0.1:8765` only. The web server in front of it is what the internet
reaches.

### 5. The web server in front

Make `/etc/nginx/sites-available/civiccast` with three addresses: the desk, the site and the
media.

```
server {
    server_name desk.example.org;
    # The PC worker sends episode audio; the station takes files up to 300 MB.
    client_max_body_size 300m;
    proxy_request_buffering off;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
}

server {
    server_name yourcity.example.org;
    root /var/www/yourcity;
    error_page 404 /404.html;
    add_header Cache-Control "public, max-age=0, must-revalidate, no-transform";
    location / { try_files $uri $uri/ =404; }
}

server {
    server_name media.example.org;
    root /var/www/yourcity-media;
}
```

Turn it on and add HTTPS. The desk's sign-in cookie only works over HTTPS:

```
sudo ln -s /etc/nginx/sites-available/civiccast /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d desk.example.org -d yourcity.example.org -d media.example.org
```

Point the three names at the VPS's address in Hostinger's DNS first.

The site's `_headers` and `_redirects` files are for Cloudflare, and nginx does not read them.
The `add_header` line above does the work of `_headers`. `_redirects` sends old `/stories/...`
links to their episode pages; to keep those links working, add each line of it as a `location`
with `return 301`.

### 6. First sign-in and the PC

1. Open `https://desk.example.org/desk/`. The first account made on a desk owns it; after that,
   sign-up is closed.
2. On the Windows PC, run the worker against the desk's address:

   ```
   python -m civiccast_brief.station worker --station https://desk.example.org
   ```

### Updating

```
cd ~/civiccast-brief && git pull && uv sync
sudo systemctl restart civiccast-station
```
