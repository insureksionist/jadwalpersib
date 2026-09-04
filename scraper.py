#!/usr/bin/env python3
"""Collect Persib Bandung fixtures/results from the rendered Flashscore team page.

This scraper intentionally uses the public, rendered HTML exposed by a normal
Playwright browser session. It does not call Flashscore's private feeds/APIs,
rotate proxies, solve CAPTCHAs, or bypass access controls.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from xml.dom import minidom

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FIXTURES_FILE = DATA_DIR / "fixtures.xml"
RESULTS_FILE = DATA_DIR / "results.xml"
LAST_UPDATE_FILE = DATA_DIR / "last-update.xml"

TEAM_NAME = os.getenv("TEAM_NAME", "Persib Bandung")
TEAM_ID = os.getenv("TEAM_ID", "KpBjbPK1")
BASE_URL = os.getenv("FLASHSCORE_BASE_URL", "https://www.flashscore.com")
TEAM_URL = os.getenv(
    "FLASHSCORE_TEAM_URL",
    f"{BASE_URL}/team/persib-bandung/{TEAM_ID}/",
)
SEASON_START = date.fromisoformat(os.getenv("SEASON_START", "2026-07-01"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Jakarta")
MAX_PAGES = int(os.getenv("MAX_PAGES", "30"))
DETAIL_DELAY_MS = int(os.getenv("DETAIL_DELAY_MS", "1200"))
PAGE_DELAY_MS = int(os.getenv("PAGE_DELAY_MS", "1200"))
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"0", "false", "no"}


@dataclass
class Match:
    id: str
    competition: str
    competition_short: str
    matchday: str
    date: str
    time: str
    timezone: str
    home: str
    away: str
    venue: str = ""
    city: str = ""
    country: str = "Indonesia"
    side: str = ""
    status: str = "scheduled"
    home_score: str = ""
    away_score: str = ""
    source_url: str = ""

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(f"{self.date}T{self.time}:00")


def clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def slug_short(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    # Flashscore competition headers often include country + competition + season.
    parts = [p.strip() for p in re.split(r"\s*[|•]\s*", value) if p.strip()]
    value = parts[-1] if parts else value
    value = re.sub(r"\b\d{4}/\d{2,4}\b", "", value).strip(" -")
    return value


def infer_year(day: int, month: int) -> int:
    # The project tracks the 2026/27 season. A July-Dec date belongs to the
    # season start year; Jan-Jun belongs to the following calendar year.
    return SEASON_START.year if month >= SEASON_START.month else SEASON_START.year + 1


def parse_flashscore_date(raw: str) -> tuple[str, str]:
    raw = clean(raw)
    # Typical values: "06.09. 19:00", "06.09.19:00", "06.09. 2026 19:00"
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.?\s*(?:(\d{4})\s*)?(\d{1,2}):(\d{2})", raw)
    if not m:
        raise ValueError(f"Unrecognised Flashscore date/time: {raw!r}")
    day, month, explicit_year, hour, minute = m.groups()
    year = int(explicit_year) if explicit_year else infer_year(int(day), int(month))
    return f"{year:04d}-{int(month):02d}-{int(day):02d}", f"{int(hour):02d}:{int(minute):02d}"


def status_from_classes(classes: str) -> str:
    classes = classes or ""
    if "event__match--finished" in classes:
        return "finished"
    if "event__match--live" in classes:
        return "live"
    if "event__match--postponed" in classes:
        return "postponed"
    if "event__match--canceled" in classes or "event__match--cancelled" in classes:
        return "cancelled"
    return "scheduled"


def extract_score(text: str) -> tuple[str, str]:
    text = clean(text)
    nums = re.findall(r"\d+", text)
    return (nums[0], nums[1]) if len(nums) >= 2 else ("", "")


def read_existing(path: Path, root_name: str) -> dict[str, Match]:
    if not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    result: dict[str, Match] = {}
    for node in root.findall("match"):
        def t(name: str) -> str:
            return clean(node.findtext(name, default=""))
        mid = t("id")
        if not mid:
            continue
        result[mid] = Match(
            id=mid,
            competition=t("competition"),
            competition_short=t("competitionShort"),
            matchday=t("matchday"),
            date=t("date"),
            time=t("time"),
            timezone=t("timezone") or TIMEZONE,
            home=t("home"), away=t("away"), venue=t("venue"), city=t("city"),
            country=t("country") or "Indonesia", side=t("side"), status=t("status") or "scheduled",
            home_score=t("homeScore"), away_score=t("awayScore"), source_url=t("sourceUrl"),
        )
    return result


def merge_metadata(new: Match, old: Match | None) -> Match:
    if not old:
        return new
    # Keep useful metadata if Flashscore's list view does not expose it.
    for field in ("venue", "city", "country", "matchday", "competition_short"):
        if not getattr(new, field):
            setattr(new, field, getattr(old, field))
    if not new.source_url:
        new.source_url = old.source_url
    return new


def xml_write(path: Path, root_name: str, matches: Iterable[Match], source: str = "flashscore-rendered") -> None:
    root = ET.Element(root_name, {"version": "1.1", "source": source})
    for m in sorted(matches, key=lambda x: (x.date, x.time, x.id)):
        node = ET.SubElement(root, "match")
        fields = [
            ("id", m.id), ("competition", m.competition), ("competitionShort", m.competition_short),
            ("matchday", m.matchday), ("date", m.date), ("time", m.time), ("timezone", m.timezone),
            ("home", m.home), ("away", m.away), ("venue", m.venue), ("city", m.city),
            ("country", m.country), ("side", m.side), ("status", m.status),
            ("homeScore", m.home_score), ("awayScore", m.away_score), ("sourceUrl", m.source_url),
        ]
        for name, value in fields:
            if value != "":
                ET.SubElement(node, name).text = value
    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    path.write_bytes(pretty)


def write_last_update(success: bool, message: str, fixture_count: int, result_count: int) -> None:
    root = ET.Element("lastUpdate", {"version": "1.1", "source": "flashscore-rendered"})
    now = datetime.now(timezone.utc).astimezone(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")
    for key, value in [
        ("lastSuccess", now if success else ""),
        ("status", "ok" if success else "error"),
        ("message", message),
        ("fixtureCount", str(fixture_count)),
        ("resultCount", str(result_count)),
        ("sourceUrl", TEAM_URL),
    ]:
        ET.SubElement(root, key).text = value
    pretty = minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    LAST_UPDATE_FILE.write_bytes(pretty)


async def click_show_more(page) -> None:
    selectors = [
        "button:has-text('Show more matches')",
        "span:has-text('Show more matches')",
        "button:has-text('Show more')",
    ]
    for _ in range(MAX_PAGES):
        clicked = False
        for selector in selectors:
            loc = page.locator(selector)
            count = await loc.count()
            if not count:
                continue
            for i in range(min(count, 3)):
                try:
                    if await loc.nth(i).is_visible():
                        await loc.nth(i).click(timeout=2500)
                        clicked = True
                        await page.wait_for_timeout(PAGE_DELAY_MS)
                except Exception:
                    pass
        if not clicked:
            break


async def extract_rows(page) -> list[dict]:
    # The class names below are stable enough to use as primary selectors, but
    # participant data also has data-testid attributes on newer Flashscore DOMs.
    return await page.evaluate(
        """
        () => {
          const out = [];
          let currentCompetition = '';
          const nodes = document.querySelectorAll('.event__title, .event__match');
          for (const node of nodes) {
            if (node.classList.contains('event__title')) {
              currentCompetition = (node.innerText || '').replace(/\\s+/g, ' ').trim();
              continue;
            }
            const home = node.querySelector('.event__participant--home, .event__homeParticipant');
            const away = node.querySelector('.event__participant--away, .event__awayParticipant');
            const time = node.querySelector('.event__time');
            if (!home || !away || !time) continue;
            const scoreHome = node.querySelector('.event__score--home');
            const scoreAway = node.querySelector('.event__score--away');
            const link = node.querySelector('a.eventRowLink') || node.querySelector('a');
            out.push({
              id: node.id || '',
              classes: node.className || '',
              competition: currentCompetition,
              time: (time.innerText || '').replace(/\\s+/g, ' ').trim(),
              home: (home.innerText || home.textContent || '').replace(/\\s+/g, ' ').trim(),
              away: (away.innerText || away.textContent || '').replace(/\\s+/g, ' ').trim(),
              homeScore: scoreHome ? (scoreHome.innerText || '').trim() : '',
              awayScore: scoreAway ? (scoreAway.innerText || '').trim() : '',
              href: link ? link.href : ''
            });
          }
          return out;
        }
        """
    )


async def detail_metadata(page, url: str) -> dict[str, str]:
    if not url:
        return {}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(700)
        return await page.evaluate(
            """
            () => {
              const body = document.body?.innerText || '';
              const venueMatch = body.match(/Venue:\\s*([^\\n]+)/i);
              const venue = venueMatch ? venueMatch[1].trim() : '';
              let city = '';
              const cm = venue.match(/\\(([^)]+)\\)$/);
              if (cm) city = cm[1].trim();
              return { venue, city };
            }
            """
        )
    except Exception:
        return {}


async def scrape() -> tuple[list[Match], list[Match]]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            locale="en-US",
            timezone_id=TIMEZONE,
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            print(f"Opening {TEAM_URL}")
            await page.goto(TEAM_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1500)
            await click_show_more(page)
            rows = await extract_rows(page)
            if not rows:
                raise RuntimeError("No match rows found; Flashscore DOM may have changed or access was denied.")

            matches: list[Match] = []
            for row in rows:
                try:
                    d, tm = parse_flashscore_date(row["time"])
                    if date.fromisoformat(d) < SEASON_START:
                        continue
                    home = clean(row["home"])
                    away = clean(row["away"])
                    if TEAM_NAME.lower() not in {home.lower(), away.lower()}:
                        continue
                    status = status_from_classes(row["classes"])
                    hs, as_ = clean(row.get("homeScore")), clean(row.get("awayScore"))
                    if not hs and not as_:
                        # Some newer DOM variants put the score in a generic score container.
                        hs, as_ = extract_score(row.get("score", ""))
                    fs_id = clean(row["id"]).replace("g_1_", "") or re.sub(r"[^A-Za-z0-9_-]", "", row["home"] + "_" + row["away"] + "_" + d)
                    competition = clean(row.get("competition")) or "Unknown"
                    m = Match(
                        id=fs_id,
                        competition=competition,
                        competition_short=slug_short(competition),
                        matchday="",
                        date=d,
                        time=tm,
                        timezone=TIMEZONE,
                        home=home,
                        away=away,
                        side="Home" if home.lower() == TEAM_NAME.lower() else "Away",
                        status=status,
                        home_score=hs,
                        away_score=as_,
                        source_url=urljoin(BASE_URL, row.get("href", "")),
                    )
                    matches.append(m)
                except ValueError as exc:
                    print(f"Skipping row: {exc}", file=sys.stderr)

            # Deduplicate by match ID.
            unique: dict[str, Match] = {m.id: m for m in matches}
            matches = list(unique.values())
            if len(matches) < 3:
                raise RuntimeError(f"Validation failed: only {len(matches)} valid Persib matches found.")

            existing = read_existing(FIXTURES_FILE, "fixtures") | read_existing(RESULTS_FILE, "results")
            detail_page = await context.new_page()
            try:
                for m in sorted(matches, key=lambda x: x.dt):
                    old = existing.get(m.id)
                    m = merge_metadata(m, old)
                    if not m.venue and m.source_url:
                        meta = await detail_metadata(detail_page, m.source_url)
                        m.venue = clean(meta.get("venue"))
                        m.city = clean(meta.get("city"))
                        await detail_page.wait_for_timeout(DETAIL_DELAY_MS)
                    existing[m.id] = m
            finally:
                await detail_page.close()

            # Keep only the requested season. A successful scrape replaces the
            # source set, but preserves venue/city metadata via the merge above.
            all_matches = [m for m in existing.values() if m.date and date.fromisoformat(m.date) >= SEASON_START]
            all_matches.sort(key=lambda x: x.dt)
            fixtures = [m for m in all_matches if m.status != "finished"]
            results = [m for m in all_matches if m.status == "finished"]
            return fixtures, results
        finally:
            await browser.close()


def validate(fixtures: list[Match], results: list[Match]) -> None:
    all_matches = fixtures + results
    if len(all_matches) < 3:
        raise RuntimeError("Refusing to write XML: fewer than 3 matches after normalization.")
    ids = [m.id for m in all_matches]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Refusing to write XML: duplicate match IDs detected.")
    for m in all_matches:
        if not m.home or not m.away or not m.date or not m.time:
            raise RuntimeError(f"Invalid match record: {asdict(m)}")
        if TEAM_NAME.lower() not in {m.home.lower(), m.away.lower()}:
            raise RuntimeError(f"Non-Persib record slipped through: {m.home} - {m.away}")
        if m.status == "finished" and (m.home_score == "" or m.away_score == ""):
            raise RuntimeError(f"Finished match has no score: {m.id}")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fixtures, results = asyncio.run(scrape())
        validate(fixtures, results)
        xml_write(FIXTURES_FILE, "fixtures", fixtures)
        xml_write(RESULTS_FILE, "results", results)
        write_last_update(True, "Scrape and validation succeeded", len(fixtures), len(results))
        print(f"OK: {len(fixtures)} fixtures, {len(results)} results")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # Do not destroy good fixture/result files on a failed scrape.
        try:
            old_f = len(read_existing(FIXTURES_FILE, "fixtures"))
            old_r = len(read_existing(RESULTS_FILE, "results"))
            write_last_update(False, str(exc), old_f, old_r)
        except Exception as update_exc:
            print(f"Could not write last-update.xml: {update_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
