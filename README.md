# Persib Bandung Match Center 2026/27 — V19 Fixed Schedule + Live Detail/Result Scraper

## Architecture
The **46-match master schedule is fixed** in `data/season-roster.json` and mirrored in `index.html` as `STATIC_MATCHES`.

Expected season roster:
- BRI Super League: 34
- ACL Two: 6
- ASEAN Club Championship / Shopee Cup: 6

The scraper **must not change schedule identity/date/home/away**. It enriches the fixed roster with mutable information: kickoff, venue, city, team URLs, source URL, status and score, plus the existing form/H2H, standings, player leaders and previous-match statistics.

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
