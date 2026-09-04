# Persib Dashboard — Flashscore XML updater

This folder adds a scheduled collector for the static GitHub Pages dashboard.
The collector opens the public Persib team page in a normal Chromium session,
extracts the rendered match rows, normalizes them, validates the records, and
writes local XML files used by `index.html`.

## Files

```text
scraper.py
requirements.txt
.github/workflows/update-persib-data.yml
data/
  fixtures.xml
  results.xml
  last-update.xml
```

## Data flow

```text
Flashscore rendered team page
          |
          v
      scraper.py
          |
   normalize + validate
       /         \
      v           v
fixtures.xml  results.xml
       \         /
        v       v
     GitHub commit
          |
          v
 GitHub Pages dashboard
```

## What the scraper does

- Uses Playwright + Chromium so JavaScript-rendered match rows can be read.
- Uses the public team page rather than Flashscore's private/internal feeds.
- Filters to Persib Bandung and the configured season (`SEASON_START`).
- Normalizes date/time to `YYYY-MM-DD` and `HH:MM` in `Asia/Jakarta`.
- Detects home/away, scheduled/live/finished/postponed/cancelled status and scores.
- Captures the competition heading and match-detail URL when available.
- Optionally opens the public match-detail page to fill venue/city metadata.
- Deduplicates match IDs.
- Refuses to overwrite good data when scraping returns an unexpectedly tiny or invalid dataset.
- Writes `last-update.xml` with status, timestamp and counts.

## GitHub Actions

The workflow runs:

- every 6 hours, and
- manually via **Actions → Update Persib XML data → Run workflow**.

The workflow requires repository contents write permission. The workflow file
sets `permissions: contents: write`, so no personal access token is needed for
a normal repository.

## Local test

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python scraper.py
```

For a visible browser during local debugging:

```bash
HEADLESS=false python scraper.py
```

## Important operational note

Flashscore can change its frontend markup, rate-limit automated traffic, or
restrict access. This implementation deliberately does **not** bypass CAPTCHAs,
private APIs/feeds, authentication, or access controls. If the page structure
changes, the scraper should fail safely rather than publish an incomplete XML.

Check Flashscore's current terms and robots/access rules before enabling the
scheduled collector for a public repository.
