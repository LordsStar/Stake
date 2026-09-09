import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import blindado_core as core
import elo_trainer

UTC = timezone.utc


class ModelGateTests(unittest.TestCase):
    def _elo_with_binary_history(self, probabilities, outcomes):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        elo = core.EloModel(Path(tempdir.name) / "elo.json")
        elo.state["brier"]["cricket:all"] = [
            {"kind": "binary", "p": p, "y": y, "match_id": f"match-{index}"}
            for index, (p, y) in enumerate(zip(probabilities, outcomes))
        ]
        return elo

    @staticmethod
    def _history(probabilities, outcomes, prefix):
        return [
            {"kind": "binary", "p": p, "y": y, "match_id": f"{prefix}-{index}"}
            for index, (p, y) in enumerate(zip(probabilities, outcomes))
        ]

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
        elo.state["activation_state"]["cricket:all"] = {
            "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION - 1,
            "active": True, "pass_streak": 3, "fail_streak": 0,
        }
        # Primera corrida v2: migra y fija el fingerprint, sin inventar señal.
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])
        bad_history = self._history(bad, outcomes, "bad")
        for expected_streak in (1, 2):
            elo.state["brier"]["cricket:all"] = list(bad_history) + [{
                "kind": "binary", "p": 0.52, "y": 1.0,
                "match_id": f"bad-new-{expected_streak}",
            }]
            state = elo.refresh_activation_state("cricket:all")
            self.assertTrue(state["active"])
            self.assertEqual(state["fail_streak"], expected_streak)
            bad_history = list(elo.state["brier"]["cricket:all"])
        elo.state["brier"]["cricket:all"] = bad_history + [{
            "kind": "binary", "p": 0.52, "y": 1.0, "match_id": "bad-new-3",
        }]
        self.assertFalse(elo.refresh_activation_state("cricket:all")["active"])

        good_history = self._history(good, outcomes, "good")
        for expected_streak in (1, 2):
            elo.state["brier"]["cricket:all"] = list(good_history) + [{
                "kind": "binary", "p": 0.70, "y": 1.0,
                "match_id": f"good-new-{expected_streak}",
            }]
            state = elo.refresh_activation_state("cricket:all")
            self.assertFalse(state["active"])
            self.assertEqual(state["pass_streak"], expected_streak)
            good_history = list(elo.state["brier"]["cricket:all"])
        elo.state["brier"]["cricket:all"] = good_history + [{
            "kind": "binary", "p": 0.70, "y": 1.0, "match_id": "good-new-3",
        }]
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])

    def test_same_match_ids_do_not_advance_streak(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        bad = [0.52] * 30
        elo = self._elo_with_binary_history(bad, outcomes)
        elo.state["activation_state"]["cricket:all"] = {
            "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION,
            "active": True, "pass_streak": 0, "fail_streak": 1,
            "last_evidence_fingerprint": elo.evidence_fingerprint("cricket:all"),
        }
        first = elo.refresh_activation_state("cricket:all")
        second = elo.refresh_activation_state("cricket:all")
        self.assertEqual(first["fail_streak"], 1)
        self.assertEqual(second["fail_streak"], 1)
        self.assertFalse(second["evidence_changed"])

    def test_new_namespace_starts_inactive_with_zero_streak(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        good = [0.70] * 20 + [0.50] * 10
        elo = self._elo_with_binary_history(good, outcomes)
        state = elo.refresh_activation_state("cricket:all")
        self.assertFalse(state["active"])
        self.assertEqual(state["pass_streak"], 0)
        self.assertEqual(state["fail_streak"], 0)
        self.assertTrue(state["migration_baseline"])

    def test_brier_observation_contains_match_id(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        elo = core.EloModel(Path(tempdir.name) / "elo.json")
        for index in range(core.ELO_MIN_GAMES + 1):
            elo.update(
                "basketball:test", "home", "away", 1.0,
                f"game-{index}",
            )
        history = elo.state["brier"]["basketball:test"]
        self.assertEqual(history[-1]["match_id"], f"game-{core.ELO_MIN_GAMES}")

    def test_hysteresis_state_persists_between_process_instances(self):
        outcomes = [1.0] * 20 + [0.0] * 10
        good = [0.70] * 20 + [0.50] * 10
        bad = [0.52] * 30
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "elo.json"

        elo = core.EloModel(path)
        elo.state["brier"]["cricket:all"] = self._history(good, outcomes, "good")
        elo.state["activation_state"]["cricket:all"] = {
            "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION - 1,
            "active": True, "pass_streak": 3, "fail_streak": 0,
        }
        self.assertTrue(elo.refresh_activation_state("cricket:all")["active"])
        elo.state["brier"]["cricket:all"] = self._history(bad, outcomes, "bad")
        self.assertEqual(elo.refresh_activation_state("cricket:all")["fail_streak"], 1)
        elo.save()

        elo = core.EloModel(path)
        elo.state["brier"]["cricket:all"].append({
            "kind": "binary", "p": 0.52, "y": 1.0, "match_id": "bad-extra-2",
        })
        self.assertEqual(elo.refresh_activation_state("cricket:all")["fail_streak"], 2)
        elo.save()
        elo = core.EloModel(path)
        elo.state["brier"]["cricket:all"].append({
            "kind": "binary", "p": 0.52, "y": 1.0, "match_id": "bad-extra-3",
        })
        final_state = elo.refresh_activation_state("cricket:all")
        self.assertFalse(final_state["active"])
        self.assertEqual(final_state["fail_streak"], 0)


class TrainerMigrationTests(unittest.TestCase):
    def test_rebuild_preserves_only_transition_state_not_old_metrics(self):
        rebuilt = core.fresh_elo_state({
            "basketball:nba": {
                "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION,
                "active": True, "pass_streak": 0, "fail_streak": 1,
                "last_evidence_fingerprint": "abc",
                "last_brier": 0.1, "last_skill": 0.9,
            }
        })
        state = rebuilt["activation_state"]["basketball:nba"]
        self.assertEqual(state["last_evidence_fingerprint"], "abc")
        self.assertNotIn("last_brier", state)
        self.assertNotIn("last_skill", state)

    def test_schema_migration_without_manual_reset_preserves_activation_state(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        elo = core.EloModel(Path(tempdir.name) / "elo.json")
        elo.state["schema_version"] = core.ELO_SCHEMA_VERSION - 1
        preserved = {
            "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION - 1,
            "active": True, "pass_streak": 3, "fail_streak": 0,
        }
        elo.state["activation_state"] = {"basketball:nba": dict(preserved)}
        observed = {}

        def inspect_rebuilt_state(rebuilt, aliases, results):
            observed.update(rebuilt.state.get("activation_state", {}))
            return 0

        with (
            patch.object(elo_trainer, "EloModel", return_value=elo),
            patch.object(elo_trainer, "load_results", return_value=[{"event_id": "one"}]),
            patch.object(elo_trainer, "train_elo_from_results", side_effect=inspect_rebuilt_state),
            patch("sys.argv", ["elo_trainer.py"]),
        ):
            self.assertEqual(elo_trainer.main(), 0)

        self.assertEqual(observed["basketball:nba"], preserved)
        self.assertEqual(elo.state["schema_version"], core.ELO_SCHEMA_VERSION)

    def test_full_rebuild_with_same_matches_does_not_advance_hysteresis(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        aliases = core.TeamAliasRegistry(
            root / "aliases.json", root / "automatic_aliases.json"
        )
        results = [{
            "sport": "basketball", "league": "nba", "event_id": f"game-{index}",
            "start_time": f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00Z",
            "home_name": "Home", "away_name": "Away",
            "home_score": 100 + (index % 3), "away_score": 95,
            "status": "final",
        } for index in range(40)]

        first = core.EloModel(root / "first.json")
        core.train_elo_from_results(first, aliases, results)
        namespace = "basketball:nba"
        fingerprint = first.evidence_fingerprint(namespace)
        preserved = {
            "policy_version": core.BRIER_ACTIVATION_POLICY_VERSION,
            "active": True, "pass_streak": 0, "fail_streak": 1,
            "last_evidence_fingerprint": fingerprint,
        }

        rebuilt = core.EloModel(root / "rebuilt.json")
        rebuilt.state["activation_state"] = {namespace: preserved}
        core.train_elo_from_results(rebuilt, aliases, results)
        state = rebuilt.state["activation_state"][namespace]
        self.assertEqual(state["fail_streak"], 1)
        self.assertFalse(state["evidence_changed"])
        self.assertEqual(state["last_evidence_fingerprint"], fingerprint)


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


class LiquiditySplitTests(unittest.TestCase):
    def _event(self, source="stake", league="NBA", home="Alpha United", away="Beta City",
               start_time=None, fetched_minutes=1, selections=None):
        now = datetime.now(UTC)
        return core.NormalizedEvent(
            event_id=f"{source}:{home}:{away}", source=source,
            sport="basketball", league=league, home=home, away=away,
            start_time=start_time, is_live=False, status="active",
            last_update=now.isoformat(), market_fetched_at=(now - timedelta(minutes=fetched_minutes)).isoformat(),
            markets=[core.NormalizedMarket("moneyline", "Moneyline", [
                core.NormalizedOutcome(name, odds)
                for name, odds in (selections or [(home, 1.80), (away, 1.95)])
            ])], raw={},
        )

    def test_liquidity_cascade_distinguishes_missing_league_and_event(self):
        stake = self._event()
        _, _, code, _, _ = core.classify_bovada_liquidity(stake, [])
        self.assertEqual(code, "bovada_sin_eventos_para_liga")

        unrelated = self._event(
            source="bovada", league="basketball_nba", home="Gamma", away="Delta"
        )
        _, _, code, _, diagnostic = core.classify_bovada_liquidity(stake, [unrelated])
        self.assertEqual(code, "bovada_evento_no_emparejado")
        self.assertEqual(diagnostic["mejores_candidatos"][0]["componente_nombres"], 0.0)

    def test_exact_point_25_is_time_bonus_not_fallback(self):
        now = datetime.now(UTC)
        start = (now + timedelta(hours=5)).isoformat()
        stake = self._event(start_time=start)
        unrelated = self._event(
            source="bovada", league="basketball_nba", home="Gamma", away="Delta",
            start_time=start,
        )
        _, score, code, _, diagnostic = core.classify_bovada_liquidity(stake, [unrelated])
        components = diagnostic["mejores_candidatos"][0]
        self.assertEqual(code, "bovada_evento_no_emparejado")
        self.assertEqual(score, 0.25)
        self.assertEqual(components["componente_nombres"], 0.0)
        self.assertEqual(components["componente_horario"], 0.25)

    def test_liquidity_cascade_exposes_low_score_and_selection_failure(self):
        stake = self._event(start_time=None)
        weak = self._event(
            source="bovada", league="basketball_nba",
            home="Alpha", away="Beta North Town", start_time=None,
        )
        _, score, code, _, diagnostic = core.classify_bovada_liquidity(stake, [weak])
        self.assertGreaterEqual(score, 0.35)
        self.assertLess(score, core.LIQUIDITY_MIN_MATCH_SCORE)
        self.assertEqual(code, "bovada_match_score_bajo")
        self.assertIn("stake_home_normalizado", diagnostic["mejores_candidatos"][0])
        self.assertIn("bovada_home_normalizado", diagnostic["mejores_candidatos"][0])

        matched_names_wrong_selections = self._event(
            source="bovada", league="basketball_nba",
            selections=[("Choice One", 1.9), ("Choice Two", 1.9)],
        )
        _, _, code, _, diagnostic = core.classify_bovada_liquidity(
            stake, [matched_names_wrong_selections]
        )
        self.assertEqual(code, "bovada_seleccion_no_emparejada")
        self.assertEqual(diagnostic["stake_selecciones_crudas"], ["Alpha United", "Beta City"])
        self.assertEqual(diagnostic["bovada_selecciones_crudas"], ["Choice One", "Choice Two"])

    def test_exact_event_without_bovada_moneyline_has_own_reason(self):
        stake = self._event(start_time=None)
        bovada = self._event(source="bovada", league="basketball_nba", start_time=None)
        bovada.markets = [core.NormalizedMarket("spread", "Spread", [
            core.NormalizedOutcome("Alpha United", 1.9, point=-2.5),
            core.NormalizedOutcome("Beta City", 1.9, point=2.5),
        ])]
        _, score, code, _, diagnostic = core.classify_bovada_liquidity(stake, [bovada])
        self.assertEqual(score, 1.0)
        self.assertEqual(code, "bovada_sin_mercado_principal")
        self.assertEqual(diagnostic["bovada_selecciones_crudas"], [])

    def test_bovada_expiration_reuses_v76_freshness_state(self):
        now = datetime.now(UTC)
        start = (now + timedelta(hours=3)).isoformat()
        stake = self._event(start_time=start)
        stale = self._event(
            source="bovada", league="basketball_nba", start_time=start, fetched_minutes=40
        )
        _, _, code, detail, _ = core.classify_bovada_liquidity(stake, [stale])
        self.assertEqual(code, "bovada_observacion_vencida")
        self.assertIn("market_observation_expired", detail)


class FinalGateSplitTests(unittest.TestCase):
    def _event_pair(self):
        now = datetime.now(UTC)
        start = (now + timedelta(hours=3)).isoformat()
        common = dict(
            sport="basketball", league="NBA", home="Home Team", away="Away Team",
            start_time=start, is_live=False, status="active", last_update=now.isoformat(),
            market_fetched_at=now.isoformat(), raw={},
        )
        stake = core.NormalizedEvent(
            event_id="stake-final", source="stake", markets=[core.NormalizedMarket(
                "moneyline", "Moneyline", [
                    core.NormalizedOutcome("Home Team", 1.80),
                    core.NormalizedOutcome("Away Team", 1.80),
                ],
            )], **common,
        )
        bovada = core.NormalizedEvent(
            event_id="bovada-final", source="bovada", markets=[core.NormalizedMarket(
                "moneyline", "Moneyline", [
                    core.NormalizedOutcome("Home Team", 1.90),
                    core.NormalizedOutcome("Away Team", 1.90),
                ],
            )], **common,
        )
        return stake, bovada

    def test_confidence_failure_keeps_raw_ev_divergence_and_confidence(self):
        stake, bovada = self._event_pair()
        engine = core.BlindadoEngine()
        candidates, reasons, metrics = engine.evaluate_event(
            stake, {"Home Team": 0.58, "Away Team": 0.42}, True,
            bovada, 1.0, [], core.PhysicalStatusRegistry(), {},
        )
        self.assertEqual(candidates, [])
        self.assertEqual(list(reasons), ["confianza_menor_8"])
        self.assertTrue(metrics)
        self.assertTrue(all(row["motivo"] == "confianza_menor_8" for row in metrics))
        self.assertIsNotNone(metrics[0]["ev_calculado"])
        self.assertIsNotNone(metrics[0]["divergencia_calculada"])
        self.assertIsNotNone(metrics[0]["confianza_calculada"])

    def test_ev_failure_precedes_composite_confidence(self):
        stake, bovada = self._event_pair()
        engine = core.BlindadoEngine()
        _, reasons, metrics = engine.evaluate_event(
            stake, {"Home Team": 0.54, "Away Team": 0.46}, True,
            bovada, 1.0, [], core.PhysicalStatusRegistry(), {},
        )
        self.assertEqual(list(reasons), ["ev_menor_4"])
        self.assertTrue(all(row["confianza_calculada"] is None for row in metrics))

    def test_confidence_breakdown_exposes_existing_formula_without_changing_it(self):
        engine = core.BlindadoEngine()
        breakdown = engine._confidence_breakdown(0.081, 0.52, 0.60, {"verified": False})
        self.assertEqual(breakdown, {
            "base": 5.0,
            "componente_ev": 2.0,
            "componente_divergencia": -1.0,
            "componente_movimiento": 0.0,
            "total": 6.0,
        })


if __name__ == "__main__":
    unittest.main()
