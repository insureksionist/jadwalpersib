"""Offline tests for the schedule-change and multi-source stats logic.

Run from the repository root:  python -m unittest discover -s tests -v
No network or browser is needed.
"""
import sys, tempfile, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scraper as sc
from scraper import Match, apply_overlays, names_match

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("Asia/Jakarta"))


def scraped(home, away, d, t="15:30", comp="Super League", status="scheduled", hs="", as_="", url=""):
    return Match(id="x", competition=comp, competition_short=sc.slug_short(comp), matchday="", date=d, time=t,
                 timezone="Asia/Jakarta", home=home, away=away, status=status, home_score=hs, away_score=as_,
                 source_url=url)


class NameMatching(unittest.TestCase):
    def test_equivalent_names(self):
        for a, b in [("Garudayaksa FC", "Garudayaksa"), ("FC Seoul", "Seoul (Kor)"), ("Persebaya", "Persebaya Surabaya"),
                     ("Borneo FC Samarinda", "Borneo Samarinda"), ("The Cong-Viettel FC", "Viettel"),
                     ("Bhayangkara", "Bhayangkara Presisi Lampung FC"), ("Johor Darul Ta'zim", "Johor DT")]:
            self.assertTrue(names_match(a, b), (a, b))

    def test_different_clubs_do_not_match(self):
        for a, b in [("Persija Jakarta", "Persib Bandung"), ("Java United", "Madura United"),
                     ("Persik Kediri", "Persija Jakarta"), ("Bali United", "Madura United"), ("PSS Sleman", "PSIM Yogyakarta")]:
            self.assertFalse(names_match(a, b), (a, b))


class Overlays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = sc.read_canonical_roster()

    def run_overlay(self, rows, overrides=None):
        return apply_overlays(self.base, rows, overrides or {}, NOW)

    def test_reschedule_sl07_to_25_oct(self):
        upd, report, summ = self.run_overlay([scraped("Garudayaksa", "Persib Bandung", "2026-10-25")])
        self.assertEqual(upd["SL-07"].date, "2026-10-25")
        self.assertEqual(upd["SL-07"].original_date, "2026-10-26")
        self.assertEqual(summ["rescheduled"], 1)

    def test_unchanged_date_is_not_flagged(self):
        upd, _, summ = self.run_overlay([scraped("Garudayaksa FC", "Persib Bandung", "2026-10-26")])
        self.assertEqual(upd["SL-07"].original_date, "")
        self.assertEqual(summ["rescheduled"], 0)

    def test_old_friendly_row_cannot_become_future_result(self):
        # Regression: a July-Sept match against Bali United used to be glued onto SL-10 (22 Nov) as "finished".
        for comp in ("Super League", "Club Friendly", "Piala Presiden"):
            upd, report, summ = self.run_overlay([scraped("Persib Bandung", "Bali United", "2026-08-20", comp=comp, status="finished", hs="3", as_="2")])
            self.assertEqual(upd["SL-10"].status, "scheduled", comp)
            self.assertEqual(upd["SL-10"].date, "2026-11-22")
            self.assertEqual(summ["accepted"], 0, comp)

    def test_finished_status_rejected_before_kickoff(self):
        upd, report, _ = self.run_overlay([scraped("Persib Bandung", "Dewa United", "2026-10-11", t="19:00", status="finished", hs="1", as_="0")])
        self.assertEqual(upd["SL-04"].status, "scheduled")
        self.assertEqual(upd["SL-04"].home_score, "")

    def test_real_result_is_accepted(self):
        upd, _, _ = self.run_overlay([scraped("Persijap Jepara", "Persib Bandung", "2026-09-20", t="19:00", status="finished", hs="1", as_="2")])
        self.assertEqual((upd["SL-03"].status, upd["SL-03"].home_score, upd["SL-03"].away_score), ("finished", "1", "2"))

    def test_second_leg_does_not_steal_first_leg_url(self):
        url = "https://www.flashscore.com/match/football/persib-bandung-KpBjbPK1/persija-jakarta-dWeXDE5i/?mid=KpIFXYk2"
        rows = [scraped("Persija Jakarta", "Persib Bandung", "2026-09-12", t="15:30", status="finished", hs="2", as_="1", url=url),
                scraped("Persib Bandung", "Persija Jakarta", "2027-04-24", url=url)]
        upd, report, _ = self.run_overlay(rows)
        self.assertEqual(upd["SL-02"].source_url, url)
        self.assertEqual(upd["SL-27"].source_url, "")

    def test_move_beyond_window_is_rejected(self):
        upd, report, summ = self.run_overlay([scraped("Garudayaksa FC", "Persib Bandung", "2026-12-30")])
        self.assertEqual(upd["SL-07"].date, "2026-10-26")
        self.assertEqual(summ["accepted"], 0)

    def test_override_wins_and_conflict_is_reported(self):
        ov = {"SL-07": {"date": "2026-10-25", "time": ""}}
        upd, report, _ = self.run_overlay([], ov)
        self.assertEqual((upd["SL-07"].date, upd["SL-07"].original_date), ("2026-10-25", "2026-10-26"))
        upd, report, _ = self.run_overlay([scraped("Garudayaksa FC", "Persib Bandung", "2026-10-24")], ov)
        self.assertEqual(upd["SL-07"].date, "2026-10-25")
        self.assertTrue(any(line.startswith("WARNING override SL-07") for line in report))

    def test_identity_is_never_changed(self):
        upd, _, _ = self.run_overlay([scraped("Garudayaksa FC", "Persib Bandung", "2026-10-25", comp="Super League")])
        b = next(m for m in self.base if m.id == "SL-07")
        for f in ("id", "home", "away", "competition", "matchday", "side"):
            self.assertEqual(getattr(upd["SL-07"], f), getattr(b, f))

    def test_repo_overrides_file_is_valid(self):
        ov = sc.load_overrides()
        self.assertEqual(ov["SL-07"]["date"], "2026-10-25")
        upd, _, _ = self.run_overlay([], ov)
        self.assertEqual(upd["SL-07"].original_date, "2026-10-26")


class MatchStats(unittest.TestCase):
    FLASH = "Match statistics\n55%\nBall Possession\n45%\n12\nTotal shots\n7\n5\nShots on target\n2\n3\nCorner Kicks\n4\n1.85\nExpected goals (xG)\n0.62\n11\nFouls\n14\n2\nYellow Cards\n1\n0\nRed Cards\n0\n120 (85%)\nPasses\n300 (90%)\n"

    def test_flashscore_parser(self):
        st = sc.parse_flashscore_stats_text(self.FLASH)
        self.assertEqual(st["possession"], ("55", "45"))
        self.assertEqual(st["shots"], ("12", "7"))
        self.assertEqual(st["xg"], ("1.85", "0.62"))
        self.assertEqual(st["yellowCards"], ("2", "1"))
        self.assertNotIn("passes", st)

    def test_priority_and_gap_filling(self):
        m = Match(id="SL-03", competition="", competition_short="Super League", matchday="", date="2026-09-20", time="19:00",
                  timezone="Asia/Jakarta", home="Persijap Jepara", away="Persib Bandung")
        recs = {}
        self.assertTrue(sc.merge_match_stats(recs, m, "flashscore", {"possession": ("40", "60"), "xg": ("0.5", "1.9")}, [], "fs"))
        self.assertTrue(sc.merge_match_stats(recs, m, "ileague", {"possession": ("42", "58")}, [{"type": "goal", "minute": "10", "team": "away", "player": "A"}], "il"))
        self.assertEqual(recs["SL-03"]["stats"]["possession"], {"home": "42", "away": "58", "source": "ileague"})
        self.assertEqual(recs["SL-03"]["stats"]["xg"]["source"], "flashscore")
        # a lower-priority source must not overwrite iLeague
        sc.merge_match_stats(recs, m, "flashscore", {"possession": ("41", "59")}, [], "fs")
        self.assertEqual(recs["SL-03"]["stats"]["possession"]["home"], "42")

    def test_xml_roundtrip_preserves_legacy_seed(self):
        old = sc.MATCH_STATS_FILE
        with tempfile.TemporaryDirectory() as td:
            sc.MATCH_STATS_FILE = Path(td) / "ms.xml"
            try:
                recs = sc.read_match_stats_xml(Path("data/match-stats.xml"))
                self.assertEqual(len(recs["SL-01"]["stats"]), 12)
                self.assertEqual(recs["SL-01"]["stats"]["possession"]["source"], "legacy")
                m = Match(id="ACL2-01", competition="", competition_short="ACL 2", matchday="", date="2026-09-16", time="17:00",
                          timezone="Asia/Jakarta", home="FC Seoul", away="Persib Bandung")
                sc.merge_match_stats(recs, m, "flashscore", sc.parse_flashscore_stats_text(self.FLASH), [], "fs-url")
                sc.write_match_stats_xml(recs)
                back = sc.read_match_stats_xml(sc.MATCH_STATS_FILE)
            finally:
                sc.MATCH_STATS_FILE = old
        self.assertEqual(set(back), {"SL-01", "SL-02", "ACL2-01"})
        self.assertEqual(len(back["SL-01"]["events"]), 7)
        self.assertEqual(back["ACL2-01"]["stats"]["shotsOnTarget"]["source"], "flashscore")

    def test_targets_cover_missing_and_recent_only(self):
        def fm(i, d, comp):
            return Match(id=i, competition="", competition_short=comp, matchday="", date=d, time="19:00", timezone="Asia/Jakarta",
                         home="Persib Bandung", away="X", status="finished")
        results = [fm("SL-01", "2026-09-06", "Super League"), fm("SL-03", "2026-09-20", "Super League"), fm("ACL2-01", "2026-09-16", "ACL 2")]
        recs = sc.read_match_stats_xml(Path("data/match-stats.xml"))
        ids = [m.id for m in sc.stats_targets(results, recs, NOW)]
        self.assertEqual(sorted(ids), ["ACL2-01", "SL-03"])  # SL-01 already complete and old


class XmlRoundtrip(unittest.TestCase):
    def test_original_date_persisted(self):
        m = Match(id="SL-07", competition="c", competition_short="Super League", matchday="Matchday 7", date="2026-10-25", time="15:30",
                  timezone="Asia/Jakarta", home="Garudayaksa FC", away="Persib Bandung", original_date="2026-10-26")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.xml"
            sc.xml_write(p, "fixtures", [m])
            back = sc.read_existing(p, "fixtures")["SL-07"]
        self.assertEqual((back.date, back.original_date), ("2026-10-25", "2026-10-26"))


if __name__ == "__main__":
    unittest.main()
