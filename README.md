# Persib Bandung Match Center 2026/27 — V18 Fixed Schedule + Live Detail Scraper

The 46-match season roster is a **fixed master schedule** supplied by the project owner:
- BRI Super League: 34
- ACL Two: 6
- Shopee Cup: 6

The scraper is intentionally **not authoritative for schedule/date**. It may scrape public rendered pages only to enrich the fixed roster with mutable details such as kickoff time, venue, team URLs, match status/score, match-detail URLs, form/H2H data, standings, player leaders and previous-match statistics.

## Fixed schedule rule
`data/fixtures.xml` is the canonical 46-match roster. The scraper validates exactly 46 records and never adds, removes, reschedules, or changes:
- match ID
- competition
- matchday
- date
- home
- away
- home/away side

If an external source reports a different date, the canonical date remains unchanged.

## Scraped detail panels
GitHub Actions still runs `scraper.py` to update:
- match status and score
- kickoff time / venue / team URLs when available
- 5 last matches for Persib and the next opponent
- 5 H2H
- BRI Super League standings
- Top Scorer
- Top Assist
- Top Yellow Cards
- Top Red Cards
- previous-match statistics

The dashboard reads the **fixed `STATIC_MATCHES` array in `index.html`** for the schedule and local XML files for those detail/statistics panels.

## GitHub Actions
The workflow remains scheduled daily at 23:00 WIB and can also be run manually. The scraper uses the public rendered Flashscore team pages and public I.League pages for detail/statistics collection; it does not use private feeds/APIs or bypass access controls.
