# Persib Bandung Match Center 2026/27 — V19 Fixed Schedule + Live Detail/Result Scraper

## Architecture
The **46-match master schedule is fixed** in `data/season-roster.json` and mirrored in `index.html` as `STATIC_MATCHES`.

Expected season roster:
- BRI Super League: 34
- ACL Two: 6
- ASEAN Club Championship / Shopee Cup: 6

The scraper **never changes schedule identity** (ID, competition, matchday, home/away, side). It enriches the fixed roster with mutable information: date/time (reschedules, see V22), venue, city, team URLs, source URL, status and score, plus the existing form/H2H, standings, player leaders and match statistics.

`data/fixtures.xml` is an enriched cache/output of the fixed roster; it is **not** used as the authoritative roster input. This prevents an empty/corrupt previous XML from causing `Baseline roster: 0` or season-count failures.

## Dashboard behavior
The dashboard now:
- displays all 46 fixed matches, including ACL Two;
- overlays kickoff/venue/city/status/score from local XML;
- displays a dedicated **Hasil Pertandingan** section;
- keeps upcoming fixtures separate from completed results;
- updates the BRI Super League standings from `data/standings.xml` after scraper execution;
- keeps Top Scorer / Assist / Yellow / Red Cards, 5 last matches, H2H and previous-match statistics.

## Reference sources
BRI Super League:
- Flashscore competition: https://www.flashscore.com/football/indonesia/super-league/#/A13mN8cC/live-standings/
- ILeague fixtures/results: https://ileague.id/fixtures/index/BRI_SUPER_LEAGUE_2026-27/2/0

ACL Two:
- Flashscore competition: https://www.flashscore.com/football/asia/afc-champions-league-2/
- Soccerway standings: https://ng.soccerway.com/asia/afc-champions-league-2/#/AHHbqZ0l/standings/overall/

ASEAN Club Championship / Shopee Cup:
- Flashscore competition: https://www.flashscore.com/football/asia/asean-club-championship/#/GGNnVGid/standings/overall/
- ASEAN United FC: https://aseanutdfc.com/id/asean-club-championship

## Important current schedule normalization
The current public competition references identify the Shopee Cup opening Persib fixture as **Port FC vs Persib Bandung**, not Buriram United vs Persib Bandung. The fixed roster has been normalized accordingly. The play-off opponent remains a placeholder until confirmed.

ACL Two uses the published opponent name **The Cong-Viettel FC** for the Viettel fixtures.

## GitHub Actions
The workflow remains daily at 23:00 WIB and supports manual execution. The scraper uses normal public rendered pages only; it does not call private feeds/APIs, bypass access controls, solve CAPTCHAs or rotate proxies.

## V21 fixes — 13 September 2026
- `results.xml` is now restricted to matches that have actually been played; future canonical fixtures cannot appear as finished results even if a stale XML contains scores/status.
- Result detail statistics are keyed by canonical match IDs (`SL-01`, `SL-02`, etc.), so clicking a result opens the correct statistics.
- Result modal now supports goals + minute + yellow/red cards in addition to match statistics.
- `last-update.xml` is loaded by the dashboard and the header now displays the actual last successful refresh timestamp.
- Failed scraper runs retain the previous successful refresh timestamp instead of erasing it.
- I.League player-leader parsing was hardened; empty scraper output no longer overwrites good player statistics.
- Yellow/red card leaders have a rendered-match fallback so card statistics can be rebuilt from completed I.League match timelines.
- Existing fixed 46-match master schedule, upcoming fixtures, calendar, filters, standings, form/H2H and Google Calendar features are preserved.

## V22 fixes — 5 October 2026

### Schedule changes (reschedules)
- `season-roster.json` / `STATIC_MATCHES` remain the **baseline** (identity + originally published date).
- **Automatic:** a Flashscore row may move a fixture's date/time only if it matches a roster fixture by competition + both club names (tolerant matching: `FC`, `The`, country tags, "Persebaya" = "Persebaya Surabaya") **and** its date is within `RESCHEDULE_WINDOW_DAYS` (default 45) of the baseline date. The nearest candidate wins; ties are rejected. Friendlies/Piala Presiden rows are never matched.
- **Manual:** edit `data/schedule-overrides.json` (`{"overrides": {"SL-07": {"date": "2026-10-25"}}}`). Overrides always win, are read directly by `index.html` (so they show immediately, without waiting for the scraper), and the scraper logs a `WARNING` if Flashscore later disagrees. Remove an entry once Flashscore shows the right date.
- Rescheduled matches carry `<originalDate>` in `fixtures.xml` and show a "Jadwal berubah · semula …" tag on the dashboard.
- Fixed: a match could be marked `finished` with a future date (pre-season rows glued onto later fixtures), and one Flashscore match URL could be reused by two fixtures. A match can no longer be `finished` before its kickoff.
- Every run writes `debug/scrape-report.txt` (uploaded as the `persib-scraper-debug-*` artifact) listing each accepted/rejected row with the reason, plus all reschedules and overrides.

### Match statistics from several sources
- Sources: **I.League** (Super League only; link resolved from the real I.League result links, name-built URL as fallback) and the **rendered Flashscore statistics tab** (all competitions, so ACL Two / Shopee Cup now get statistics). Controlled by `STATS_SOURCES` (default `ileague,flashscore`).
- `match-stats.xml` (v3) keeps existing data and merges per statistic with priority I.League > Flashscore > legacy seed; each `<stat>` records its `source`, and the modal shows "Sumber statistik". Flashscore also supplies saves and xG.
- Only matches that lack data (< 10 statistics) or finished within 3 days are fetched, at most `STATS_MAX_MATCHES` (default 8) per run.
- When a page yields nothing, its text is saved as `debug/stats-<match>-<source>.txt` for diagnosis.
- Not included: Sofascore. It has no public rendered statistics page that works without its private API and it blocks automated access, which conflicts with this project's "public rendered pages only" rule.

### Tests
`python -m unittest discover -s tests -v` (offline; no browser needed).
