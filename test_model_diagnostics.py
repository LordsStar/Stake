import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import blindado_core as core


class ModelGateTests(unittest.TestCase):
    def _elo_with_binary_history(self, probabilities, outcomes):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        elo = core.EloModel(Path(tempdir.name) / "elo.json")
        elo.state["brier"]["cricket:all"] = [
            {"kind": "binary", "p": p, "y": y}
            for p, y in zip(probabilities, outcomes)
        ]
        return elo

    def test_absolute_brier_cannot_hide_negative_skill(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        elo = self._elo_with_binary_history([0.52] * 30, outcomes)
        metrics = elo.evaluation_metrics("cricket:all")
        self.assertLessEqual(metrics["brier"], core.BRIER_MAX)
        self.assertLess(metrics["skill"], 0.0)
        self.assertFalse(metrics["active"])
        self.assertEqual(metrics["gate_mode"], "all")

    def test_model_must_pass_brier_and_skill(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        probabilities = [0.70] * 20 + [0.50] * 10
        elo = self._elo_with_binary_history(probabilities, outcomes)
        metrics = elo.evaluation_metrics("cricket:all")
        self.assertLessEqual(metrics["brier"], core.BRIER_MAX)
        self.assertGreaterEqual(metrics["skill"], core.BRIER_SKILL_MIN)
        self.assertTrue(metrics["active"])

    def test_hysteresis_requires_three_bad_and_three_good_runs(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        good = [0.70] * 20 + [0.50] * 10
        bad = [0.52] * 30
        elo = self._elo_with_binary_history(good, outcomes)

        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])
        elo.state["brier"]["cricket:all"] = [
            {"kind": "binary", "p": p, "y": y}
            for p, y in zip(bad, outcomes)
        ]
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])
        self.assertFalse(elo.refresh_activation_state("cricket:all")["active"])

        elo.state["brier"]["cricket:all"] = [
            {"kind": "binary", "p": p, "y": y}
            for p, y in zip(good, outcomes)
        ]
        self.assertFalse(elo.refresh_activation_state("cricket:all")["active"])
        self.assertFalse(elo.refresh_activation_state("cricket:all")["active"])
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])


class AuditDiagnosticTests(unittest.TestCase):
    def _event(self):
        return core.NormalizedEvent(
            event_id="nba-without-history",
            source="stake",
            sport="basketball",
            league="NBA",
            home="Home Team",
            away="Away Team",
            start_time="2099-01-01T00:00:00Z",
            is_live=False,
            status="active",
            last_update="2098-12-31T23:55:00Z",
            markets=[core.NormalizedMarket(
                key="moneyline",
                name="Moneyline",
                outcomes=[
                    core.NormalizedOutcome("Home Team", 1.80),
                    core.NormalizedOutcome("Away Team", 1.90),
                ],
            )],
            raw={},
        )

    def test_sin_modelo_is_split_and_audit_reconciles(self):
        with patch.object(core, "append_movement_history", return_value={}):
            candidates, audit = core.prepare_candidates(
                [self._event()], [], [], 100.0,
            )
        self.assertEqual(candidates, [])
        self.assertEqual(audit["descartes"], {"historial_insuficiente": 1})
        self.assertNotIn("sin_modelo", audit["descartes"])
        self.assertEqual(
            audit["descartes_por_deporte"]["basketball"]["historial_insuficiente"], 1
        )
        self.assertTrue(audit["consistencia_auditoria"]["cuadra"])


if __name__ == "__main__":
    unittest.main()
