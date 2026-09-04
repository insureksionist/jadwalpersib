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
FIXTURES_URL = os.getenv("FLASHSCORE_FIXTURES_URL", TEAM_URL.rstrip("/") + "/fixtures/")
RESULTS_URL = os.getenv("FLASHSCORE_RESULTS_URL", TEAM_URL.rstrip("/") + "/results/")
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
        # Keep snake_case aliases for the dashboard's current parser, while
        # retaining camelCase fields for readability/compatibility.
        ("lastSuccess", now if success else ""),
        ("last_success", now if success else ""),
        ("status", "ok" if success else "error"),
        ("message", message),
        ("fixtureCount", str(fixture_count)),
        ("resultCount", str(result_count)),
        ("sourceUrl", TEAM_URL),
    ]:
        ET.SubElement(root, key).text = value
    pretty = minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    LAST_UPDATE_FILE.write_bytes(pretty)


async def dismiss_consent(page) -> None:
    """Dismiss common cookie/consent dialogs without bypassing access controls."""
    selectors = [
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('I agree')",
        "button:has-text('Agree')",
        "button:has-text('Consent')",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector)
            for i in range(min(await loc.count(), 2)):
                if await loc.nth(i).is_visible():
                    await loc.nth(i).click(timeout=1500)
                    await page.wait_for_timeout(300)
        except Exception:
            pass


async def click_show_more(page) -> None:
    selectors = [
        "button:has-text('Show more matches')",
        "span:has-text('Show more matches')",
        "a:has-text('Show more matches')",
        "button:has-text('Show more')",
        "a:has-text('Show more')",
    ]
    for _ in range(MAX_PAGES):
        clicked = False
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for i in range(min(count, 3)):
                    if await loc.nth(i).is_visible():
                        await loc.nth(i).click(timeout=2500)
                        clicked = True
                        await page.wait_for_timeout(PAGE_DELAY_MS)
            except Exception:
                pass
        if not clicked:
            break


async def extract_rows(page) -> list[dict]:
    """Extract matches from Flashscore.

    Flashscore currently exposes the fixture data in the rendered page text,
    while the CSS event-row classes are not necessarily present in the DOM
    seen by a GitHub Actions browser. We therefore use two strategies:

    1. DOM selectors when available.
    2. A conservative text parser as a fallback. The fallback only accepts a
       record when Persib is one of the two teams and a date/time is present.
    """
    rows = await page.evaluate(
        """
        () => {
          const out = [];
          const nodes = document.querySelectorAll('.event__match');
          for (const node of nodes) {
            const home = node.querySelector('.event__participant--home, .event__homeParticipant');
            const away = node.querySelector('.event__participant--away, .event__awayParticipant');
            const time = node.querySelector('.event__time');
            if (!home || !away || !time) continue;
            const scoreHome = node.querySelector('.event__score--home');
            const scoreAway = node.querySelector('.event__score--away');
            const scores = node.querySelector('.event__scores');
            const link = node.querySelector('a.eventRowLink') || node.querySelector('a[href*="/match/"]') || node.querySelector('a');
            const titleBox = node.parentElement?.querySelector('.event__titleBox, .event__title');
            out.push({
              id: node.id || '',
              classes: typeof node.className === 'string' ? node.className : '',
              competition: titleBox ? (titleBox.innerText || '').replace(/\\s+/g, ' ').trim() : '',
              time: (time.innerText || time.textContent || '').replace(/\\s+/g, ' ').trim(),
              home: (home.innerText || home.textContent || '').replace(/\\s+/g, ' ').trim(),
              away: (away.innerText || away.textContent || '').replace(/\\s+/g, ' ').trim(),
              homeScore: scoreHome ? (scoreHome.innerText || scoreHome.textContent || '').trim() : '',
              awayScore: scoreAway ? (scoreAway.innerText || scoreAway.textContent || '').trim() : '',
              score: scores ? (scores.innerText || scores.textContent || '').replace(/\\s+/g, ' ').trim() : '',
              href: link ? link.href : ''
            });
          }
          return out;
        }
        """
    )
    if rows:
        return rows

    # Fallback: parse the rendered accessibility/body text. This is the path
    # currently needed by the GitHub Actions runner.
    body = await page.locator("body").inner_text(timeout=10000)
    return parse_text_rows(body)


def normalize_team_text(value: str) -> str:
    value = clean(value)
    value = re.sub(r"\s*\((?:Ina|Kor|Vie|IDN|KOR|VIE)\)\s*$", "", value, flags=re.I)
    return clean(value)


def strip_match_noise(value: str) -> str:
    value = clean(value)
    # Remove common competition/footer labels that can leak into a text slice.
    value = re.sub(r"\b(?:Standings|Show more matches|Scheduled|Latest Scores)\b", " ", value, flags=re.I)
    return clean(value.strip(" -–—|:"))


def parse_text_rows(body: str) -> list[dict]:
    """Parse Flashscore's current rendered text representation.

    Example currently observed representation:
      Super League INDONESIA: Standings
      06.09. 19:00 Persib Bandung PSM Makassar - -
      12.09. 15:30 Persija Jakarta Persib Bandung - -
      AFC Champions League 2 ASIA: Standings
      16.09. 17:00 Seoul (Kor) Persib Bandung (Ina) - -

    The parser deliberately uses Persib as the anchor instead of trying to
    split arbitrary club names into tokens.
    """
    text = clean(body)
    date_re = re.compile(r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})")
    matches = list(date_re.finditer(text))
    out: list[dict] = []
    current_competition = ""

    for idx, m in enumerate(matches):
        # Ignore old/current unrelated dates outside the actual list if the
        # text parser reaches the page footer.
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        prefix = text[max(0, m.start() - 600):m.start()]
        segment = clean(text[m.end():next_start])

        # Competition headers are rendered as e.g. "Super League INDONESIA:
        # Standings" or "AFC Champions League 2 ASIA: Standings". Use the
        # portion after the previous match when possible so navigation text
        # and the previous opponent cannot become the competition name.
        header_matches = list(re.finditer(
            r"(?P<comp>[A-Za-z0-9][A-Za-z0-9 &.\'-]{1,80}?)\s+(?:INDONESIA|ASIA):\s*Standings\b",
            prefix
        ))
        if header_matches:
            hm = header_matches[-1]
            candidate = clean(hm.group("comp"))
            # Only retain text after the last known UI/row boundary.
            boundaries = [
                candidate.upper().rfind(x) + len(x)
                for x in ("SQUAD", "TRANSFERS", "FIXTURES", "RESULTS", "NEWS", "ODDS", "SUMMARY")
                if x in candidate.upper()
            ]
            if boundaries:
                candidate = candidate[max(boundaries):].strip()
            # If a prior scheduled/result row leaked into the candidate, keep
            # the text after its final row separator.
            sep_positions = [candidate.rfind("- -"), candidate.rfind(" 2 1"), candidate.rfind(" 1 0"), candidate.rfind(" 0 0")]
            sep = max(sep_positions)
            if sep >= 0:
                candidate = candidate[sep + 3:].strip()
            candidate = re.sub(r"^\d{1,2}\s+\d{1,2}\s+", "", candidate).strip()
            if candidate:
                current_competition = candidate

        persib_match = re.search(r"Persib\s+Bandung(?:\s+\((?:Ina|IDN)\))?", segment, flags=re.I)
        if not persib_match:
            continue

        before = strip_match_noise(segment[:persib_match.start()])
        after = strip_match_noise(segment[persib_match.end():])

        # The match row ends with either "- -" for a scheduled fixture or
        # two score numbers for a completed result. This lets us discard any
        # following competition header before the next date.
        score_h = score_a = ""
        scheduled_marker = re.search(r"\s-\s-", after)
        score_marker = re.search(r"(?:^|\s)(\d{1,2})\s+(\d{1,2})(?:\s|$)", after)
        if scheduled_marker:
            after = clean(after[:scheduled_marker.start()])
        elif score_marker:
            score_h, score_a = score_marker.group(1), score_marker.group(2)
            after = clean(after[:score_marker.start()])

        before = normalize_team_text(before)
        after = normalize_team_text(after)
        persib = TEAM_NAME

        # Persib is the anchor. If text exists before Persib, it is the home
        # opponent and Persib is away. If text exists after Persib, Persib is
        # home and that text is the away opponent.
        if before:
            home, away = before, persib
        elif after:
            home, away = persib, after
        else:
            continue

        # If the row is Persib home, the opponent is after Persib. If Persib is
        # away, the opponent is before Persib. The branch above already covers
        # that; this extra guard removes obvious leaked labels.
        home = strip_match_noise(home)
        away = strip_match_noise(away)
        if not home or not away:
            continue
        if TEAM_NAME.lower() not in {normalize_team_text(home).lower(), normalize_team_text(away).lower()}:
            continue

        d = f"{infer_year(int(m.group('day')), int(m.group('month'))):04d}-{int(m.group('month')):02d}-{int(m.group('day')):02d}"
        tm = f"{int(m.group('hour')):02d}:{int(m.group('minute')):02d}"
        stable_id = re.sub(r"[^A-Za-z0-9]+", "-", f"{d}-{home}-{away}").strip("-").lower()
        out.append({
            "id": stable_id,
            "classes": "",
            "competition": current_competition,
            "time": f"{m.group('day')}.{m.group('month')}. {tm}",
            "home": normalize_team_text(home),
            "away": normalize_team_text(away),
            "homeScore": score_h,
            "awayScore": score_a,
            "score": f"{score_h} {score_a}".strip(),
            "href": "",
        })

    # De-duplicate rows from repeated Flashscore sections.
    unique: dict[str, dict] = {}
    for row in out:
        key = (row["id"], row["home"].lower(), row["away"].lower())
        unique[str(key)] = row
    return list(unique.values())


async def dump_debug(page, label: str) -> None:
    """Save diagnostics so a future DOM/access change is immediately visible in Actions."""
    try:
        debug_dir = BASE_DIR / "debug"
        debug_dir.mkdir(exist_ok=True)
        await page.screenshot(path=str(debug_dir / f"{label}.png"), full_page=True)
        (debug_dir / f"{label}.html").write_text(await page.content(), encoding="utf-8")
        body = await page.locator("body").inner_text(timeout=5000)
        (debug_dir / f"{label}.txt").write_text(body[:100000], encoding="utf-8")
        print(f"DEBUG: saved debug/{label}.png, .html and .txt")
    except Exception as exc:
        print(f"DEBUG: could not save diagnostics: {exc}", file=sys.stderr)


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


async def scrape_page(page, url: str, page_kind: str) -> list[Match]:
    print(f"Opening {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    await dismiss_consent(page)
    await page.wait_for_timeout(500)
    await click_show_more(page)

    # Wait for a real event row. If none appears, capture diagnostics instead
    # of silently producing an empty XML.
    try:
        await page.wait_for_selector('.event__match, [id^="g_1_"]', timeout=15000)
    except PlaywrightTimeoutError:
        await dump_debug(page, page_kind)

    rows = await extract_rows(page)
    print(f"{page_kind}: DOM match rows = {len(rows)}")
    if not rows:
        title = await page.title()
        body = clean((await page.locator('body').inner_text())[:1000])
        raise RuntimeError(
            f"No match rows found on {page_kind}; page title={title!r}; body preview={body!r}"
        )

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
            if page_kind == "results" and status == "scheduled":
                status = "finished"

            hs, as_ = clean(row.get("homeScore")), clean(row.get("awayScore"))
            if not hs and not as_:
                # Some finished matches (especially penalty-shootout games)
                # do not expose the score through .event__scores. Parse the
                # complete rendered row text instead. Prefer the regular
                # score (e.g. "1 - 1") before a later "PEN 5 - 6" marker.
                raw_score_text = row.get("rowText", "") or row.get("score", "")
                score_match = re.search(r"(?:^|\s)(\d{1,2})\s*[-–—:]\s*(\d{1,2})(?:\s|$)", raw_score_text)
                if score_match:
                    hs, as_ = score_match.group(1), score_match.group(2)
                else:
                    hs, as_ = extract_score(raw_score_text)

            fs_id = clean(row["id"]).replace("g_1_", "") or re.sub(
                r"[^A-Za-z0-9_-]", "", row["home"] + "_" + row["away"] + "_" + d
            )
            competition = clean(row.get("competition")) or "Unknown"
            matches.append(Match(
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
            ))
        except ValueError as exc:
            print(f"Skipping row: {exc}", file=sys.stderr)

    return matches


async def scrape() -> tuple[list[Match], list[Match]]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            locale="en-US",
            timezone_id=TIMEZONE,
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            fixture_matches = await scrape_page(page, FIXTURES_URL, "fixtures")
            await page.wait_for_timeout(PAGE_DELAY_MS)
            result_matches = await scrape_page(page, RESULTS_URL, "results")

            matches = fixture_matches + result_matches
            unique: dict[str, Match] = {}
            for m in matches:
                old = unique.get(m.id)
                unique[m.id] = m if not old else merge_metadata(m, old)
            matches = list(unique.values())

            if len(matches) < 3:
                raise RuntimeError(f"Validation failed: only {len(matches)} valid Persib matches found.")

            existing_all = list(read_existing(FIXTURES_FILE, "fixtures").values()) + list(read_existing(RESULTS_FILE, "results").values())
            existing_by_key = {
                (x.date, normalize_team_text(x.home).lower(), normalize_team_text(x.away).lower()): x
                for x in existing_all
            }
            detail_page = await context.new_page()
            try:
                normalized: dict[tuple[str, str, str], Match] = {}
                for m in sorted(matches, key=lambda x: x.dt):
                    key = (m.date, normalize_team_text(m.home).lower(), normalize_team_text(m.away).lower())
                    old = existing_by_key.get(key)
                    m = merge_metadata(m, old)
                    if not m.venue and m.source_url:
                        meta = await detail_metadata(detail_page, m.source_url)
                        m.venue = clean(meta.get("venue"))
                        m.city = clean(meta.get("city"))
                        await detail_page.wait_for_timeout(DETAIL_DELAY_MS)
                    normalized[key] = m
            finally:
                await detail_page.close()

            # Flashscore is authoritative for the current scrape. Do not keep
            # stale baseline records merely because their old IDs differ from
            # the stable IDs generated by the text fallback.
            all_matches = [m for m in normalized.values() if m.date and date.fromisoformat(m.date) >= SEASON_START]
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
            # A penalty-shootout result can occasionally be represented by a
            # special Flashscore row. Do not silently publish a blank score:
            # fail with the complete record so the offending row is diagnosable.
            raise RuntimeError(
                f"Finished match has no score: {m.id} | {m.date} {m.time} "
                f"{m.home} - {m.away} | source={m.source_url}"
            )


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
