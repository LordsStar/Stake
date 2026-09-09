import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import blindado_core as core

UTC = timezone.utc


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


class MarketFreshnessTests(unittest.TestCase):
    def _event(self, now, fetched_minutes=5, carried=False, flags=None, fetch_status="success"):
        return core.NormalizedEvent(
            event_id="market-1", source="stake", sport="basketball", league="NBA",
            home="Home", away="Away",
            start_time=(now + timedelta(hours=3)).isoformat(),
            is_live=False, status="active", last_update=(now - timedelta(hours=5)).isoformat(),
            markets=[core.NormalizedMarket("moneyline", "Moneyline", [
                core.NormalizedOutcome("Home", 1.80), core.NormalizedOutcome("Away", 1.95),
            ])], raw={},
            market_fetched_at=(now - timedelta(minutes=fetched_minutes)).isoformat(),
            source_last_update_at=(now - timedelta(hours=5)).isoformat(),
            fetch_status=fetch_status, carried_forward=carried,
            audit_flags=list(flags or []),
        )

    def test_relative_market_limits_are_explicit(self):
        self.assertEqual(core.market_limit_minutes(30), 10.0)
        self.assertEqual(core.market_limit_minutes(180), 30.0)
        self.assertEqual(core.market_limit_minutes(1440), 120.0)

    def test_fresh_stable_line_passes(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        ok, status, _ = core.market_freshness_status(
            self._event(now, flags=["linea_estable"]), now
        )
        self.assertTrue(ok)
        self.assertEqual(status, "fresh_stable")

    def test_carried_forward_stale_is_not_collapsed(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        ok, status, _ = core.market_freshness_status(
            self._event(now, fetched_minutes=40, carried=True, fetch_status="failed"), now
        )
        self.assertFalse(ok)
        self.assertEqual(status, "carried_forward_stale")

    def test_successful_but_expired_observation_is_distinct(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        ok, status, _ = core.market_freshness_status(
            self._event(now, fetched_minutes=40), now
        )
        self.assertFalse(ok)
        self.assertEqual(status, "market_observation_expired")

    def test_failed_fetch_with_recent_observation_can_continue(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        ok, status, _ = core.market_freshness_status(
            self._event(now, fetched_minutes=5, carried=True, fetch_status="failed"), now
        )
        self.assertTrue(ok)
        self.assertEqual(status, "fetch_failed_recent_fallback")

    def test_possible_cache_is_warning_not_blocker(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        ok, status, _ = core.market_freshness_status(
            self._event(now, flags=["possible_cache_upstream"]), now
        )
        self.assertTrue(ok)
        self.assertEqual(status, "possible_cache_upstream")


class MarketCohortTests(unittest.TestCase):
    def _event(self, event_id, now, source_time):
        return core.NormalizedEvent(
            event_id=event_id, source="stake", sport="basketball", league="NBA",
            home=f"Home {event_id}", away=f"Away {event_id}",
            start_time=(now + timedelta(hours=12)).isoformat(),
            is_live=False, status="active", last_update=source_time.isoformat(),
            markets=[core.NormalizedMarket("moneyline", "Moneyline", [
                core.NormalizedOutcome(f"Home {event_id}", 1.80),
                core.NormalizedOutcome(f"Away {event_id}", 1.95),
            ])], raw={}, market_fetched_at=now.isoformat(),
            source_last_update_at=source_time.isoformat(),
        )

    def test_cache_detector_requires_long_stability_and_active_cohort(self):
        now = datetime(2026, 9, 9, 12, tzinfo=UTC)
        old_source = now - timedelta(minutes=120)
        previous_events = []
        current_events = []
        for index in range(9):
            previous = self._event(str(index), now - timedelta(minutes=30), old_source)
            previous.unchanged_fetches = 3
            previous.market_last_changed_at = (now - timedelta(hours=3)).isoformat()
            previous_events.append(core.event_to_dict_pick_markets(previous))
            current_source = old_source if index == 0 else now - timedelta(minutes=5)
            current_events.append(self._event(str(index), now, current_source))

        diagnostic = core.annotate_market_observations(
            current_events, previous_events, now, expected_interval_minutes=30,
        )
        self.assertIn("possible_cache_upstream", current_events[0].audit_flags)
        self.assertEqual(current_events[0].unchanged_fetches, 4)
        self.assertEqual(diagnostic["possible_cache_upstream"], 1)


class SnapshotSchemaCompatibilityTests(unittest.TestCase):
    def _serialized_event(self):
        return {
            "event_id": "legacy-1", "source": "stake", "sport": "basketball",
            "league": "NBA", "home": "Home", "away": "Away",
            "start_time": "2099-01-01T00:00:00Z", "is_live": False,
            "status": "active", "last_update": "2026-09-09T12:00:00Z",
            "markets": [{
                "key": "moneyline", "name": "Moneyline",
                "outcomes": [
                    {"selection": "Home", "odds": 1.8, "active": True, "point": None},
                    {"selection": "Away", "odds": 1.95, "active": True, "point": None},
                ],
            }],
        }

    def test_schema4_uses_legacy_timestamp_as_compatibility_fallback(self):
        from cloud_snapshot_reader import snapshot_a_normalized_events
        stake, _ = snapshot_a_normalized_events({
            "schema_version": 4, "stake_events": [self._serialized_event()], "bovada_events": [],
        })
        self.assertEqual(stake[0].market_fetched_at, "2026-09-09T12:00:00Z")
        self.assertEqual(stake[0].source_last_update_at, "2026-09-09T12:00:00Z")

    def test_schema5_does_not_invent_missing_upstream_timestamp(self):
        from cloud_snapshot_reader import snapshot_a_normalized_events
        event = self._serialized_event()
        event["market_fetched_at"] = "2026-09-09T12:30:00Z"
        event["source_last_update_at"] = None
        stake, _ = snapshot_a_normalized_events({
            "schema_version": 5, "stake_events": [event], "bovada_events": [],
        })
        self.assertEqual(stake[0].market_fetched_at, "2026-09-09T12:30:00Z")
        self.assertIsNone(stake[0].source_last_update_at)


if __name__ == "__main__":
    unittest.main()
