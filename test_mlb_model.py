import unittest
from datetime import datetime, timedelta, timezone

import blindado_core as core
from mlb_model import FEATURE_NAMES, FeatureState, MLBPregameModel, build_dataset


UTC = timezone.utc


def pitcher(pid, runs=2, pitches=90):
    return {"id": str(pid), "outs": 18, "hr": 1, "bb": 2, "hbp": 0, "k": 6, "pitches": pitches, "er": runs}


def game(index, home_score=5, away_score=3):
    when = datetime(2025, 4, 1, tzinfo=UTC) + timedelta(days=index)
    return {
        "game_pk": str(index), "start_time": when.isoformat(),
        "home_id": "1", "home_name": "Home", "away_id": "2", "away_name": "Away",
        "home_score": home_score, "away_score": away_score,
        "home_starter_id": "11", "away_starter_id": "22",
        "home_pitchers": [pitcher(11), pitcher(12, pitches=20)],
        "away_pitchers": [pitcher(22), pitcher(23, pitches=25)],
    }


class MLBModelTests(unittest.TestCase):
    def test_only_six_aggregated_features(self):
        self.assertEqual(len(FEATURE_NAMES), 6)

    def test_starter_requires_three_previous_games(self):
        xs, ys, elo, _ = build_dataset([game(i) for i in range(6)])
        self.assertEqual(len(xs), 3)
        self.assertTrue(all(len(row) == 6 for row in xs))
        self.assertEqual(len(xs), len(ys))
        self.assertEqual(len(xs), len(elo))

    def test_current_result_cannot_change_pregame_features(self):
        state = FeatureState()
        for i in range(3):
            state.update(game(i))
        a = game(4, 1, 0)
        b = game(4, 0, 20)
        self.assertEqual(state.features(a), state.features(b))

    def test_blindado_uses_specialized_model_for_mlb(self):
        state = FeatureState()
        for i in range(3):
            state.update(game(i))
        payload = {
            "active": True, "means": [0.0] * 6, "scales": [1.0] * 6,
            "weights": [0.0] * 7, "feature_names": FEATURE_NAMES,
            "feature_state": state.export(), "samples": 600,
        }
        current = game(10)
        current["lineups_confirmed"] = True
        event = core.NormalizedEvent(
            event_id="stake-mlb", source="stake", sport="baseball", league="MLB",
            home="Home", away="Away", start_time=current["start_time"], is_live=False,
            status="active", last_update=current["start_time"], markets=[], raw={},
        )
        probs, active = core.build_elo_model(
            event, core.EloModel(), core.TeamAliasRegistry(), MLBPregameModel(payload), [current]
        )
        self.assertTrue(active)
        self.assertAlmostEqual(probs["Home"], 0.5)
        self.assertAlmostEqual(probs["Away"], 0.5)


if __name__ == "__main__":
    unittest.main()
