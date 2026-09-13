import tempfile
import unittest
from pathlib import Path

import blindado_core as core
import fetch_results_free as free


class LeagueMappingTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "sport": "soccer", "league": "premier-league",
            "category": "england", "category_id": "10",
            "tournament_id": "20", "competition_key": "soccer/10/20",
        }

    def test_fuzzy_name_is_never_accepted(self):
        candidates = [{
            "idLeague": "4328", "strSport": "Soccer",
            "strLeague": "English Premier League", "strCountry": "England",
        }]
        league, reason = free.resolve_source_league(self.target, candidates, [])
        self.assertIsNone(league)
        self.assertEqual(reason, "no_exact_match")

    def test_exact_name_requires_compatible_category(self):
        candidates = [{
            "idLeague": "x", "strSport": "Soccer",
            "strLeague": "Premier League", "strCountry": "Nigeria",
        }]
        league, _ = free.resolve_source_league(self.target, candidates, [])
        self.assertIsNone(league)

    def test_verified_mapping_uses_source_id(self):
        candidates = [{
            "idLeague": "4328", "strSport": "Soccer",
            "strLeague": "English Premier League", "strCountry": "England",
        }]
        mappings = [{
            "verified": True, "sport": "soccer",
            "competition_key": "soccer/10/20", "source_league_id": "4328",
        }]
        league, reason = free.resolve_source_league(self.target, candidates, mappings)
        self.assertEqual(league["idLeague"], "4328")
        self.assertEqual(reason, "verified_mapping")

    def test_automatic_match_requires_stake_category(self):
        target = dict(self.target, category="", category_id="")
        candidates = [{
            "idLeague": "x", "strSport": "Soccer",
            "strLeague": "Premier League", "strCountry": "England",
        }]
        league, reason = free.resolve_source_league(target, candidates, [])
        self.assertIsNone(league)
        self.assertEqual(reason, "missing_stake_category_identity")


class ResultUpsertTests(unittest.TestCase):
    def test_thesportsdb_without_provenance_is_rejected(self):
        row = {
            "sport": "soccer", "league": "x", "event_id": "thesportsdb:1",
            "start_time": "2026-01-01T00:00:00Z", "home_name": "A", "away_name": "B",
            "home_score": 1, "away_score": 0, "status": "final", "source": "thesportsdb",
        }
        self.assertIn("procedencia segura", core.validate_result(row))

    def test_existing_result_can_be_corrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            old_path = core.RESULTS_FILE
            try:
                core.RESULTS_FILE = path
                base = {
                    "sport": "basketball", "league": "nba", "event_id": "espn:1",
                    "start_time": "2026-01-01T00:00:00Z", "home_name": "A", "away_name": "B",
                    "home_score": 1, "away_score": 0, "status": "final", "source": "espn",
                }
                self.assertEqual(core.merge_results([base])[0], 1)
                corrected = dict(base, home_score=2)
                self.assertEqual(core.merge_results([corrected])[0], 1)
                saved = core.load_results()[0]
                self.assertEqual(saved["home_score"], 2)
                self.assertEqual(saved["revision"], 2)
                self.assertTrue(saved["correction_history"])
            finally:
                core.RESULTS_FILE = old_path


if __name__ == "__main__":
    unittest.main()
