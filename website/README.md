# Landing page

The project's public page, published to https://scottconverse.github.io/civiccast-brief/
by `.github/workflows/pages.yml` on every push to `main` that changes `website/`.

Plain HTML and CSS. No build step and no JavaScript.

- `index.html` is the whole page.
- `styles.css` holds the colors, fonts and layout. The colors and fonts match the
  published city site (`src/civiccast_brief/site/static/site.css`).
- `assets/fonts/` holds the self-hosted fonts and their SIL Open Font License files.
- `assets/shots/` holds the screenshots: the live Longmont site and a local test desk.

## Run it locally

```powershell
.\.venv\Scripts\python.exe -m http.server 8790 --bind 127.0.0.1 --directory website
```

Then open http://127.0.0.1:8790/. All paths are relative, so the page works the same
at the root and under `/civiccast-brief/`.

## Keep it true

Every claim on the page should match the code and `docs/user-manual.md`. When a
feature changes, update the "Works today" and "Not yet" lists in the Status section.
