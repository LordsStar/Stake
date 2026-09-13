import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import blindado_core as core


class FakeStakeClient:
    def set_extra_headers(self, headers):
        pass

    def get_json(self, url, params=None, use_cache=True):
        path = url.replace(core.STAKE_ODDS_DATA_URL, "")
        if path == "/sports":
            return [{"slug": "demo", "enabled": True}, {"slug": "off", "enabled": False}]
        if path == "/sports/demo/categories":
            return {"categories": [{"slug": "world", "enabled": True}]}
        if path == "/sports/demo/world/tournaments":
            return {"tournaments": [{"slug": "league", "enabled": True}]}
        if path == "/sports/demo/world/league/fixtures":
            return {"fixtures": [
                {
                    "id": str(i), "slug": f"event-{i}", "name": f"A{i} - B{i}",
                    "competitors": [f"A{i}", f"B{i}"], "enabled": True,
                    "preMatchEnabled": True, "status": "active",
                    "startTime": 4102444800000,
                }
                for i in range(14)
            ]}
        if path.startswith("/fixtures/event-"):
            i = path.rsplit("-", 1)[-1]
            return {
                "fixture": {"id": i, "name": f"A{i} - B{i}", "status": "active", "startTime": 4102444800000},
                "groups": [{"markets": [{
                    "id": f"m{i}", "name": "Winner", "status": "active",
                    "outcomes": [{"name": f"A{i}", "odds": 1.8}, {"name": f"B{i}", "odds": 2.1}],
                }]}],
            }
        raise AssertionError(path)


class CollectorTests(unittest.TestCase):
    @staticmethod
    def _event(source, sport):
        return core.NormalizedEvent(
            event_id=f"{source}:{sport}", source=source, sport=sport, league="x",
            home="Same Team", away="Other Team", start_time="2099-01-01T00:00:00+00:00",
            is_live=False, status="active", last_update="2099-01-01T00:00:00+00:00",
            markets=[], raw={},
        )

    def test_dynamic_hierarchy_is_not_limited_to_ten(self):
        collector = core.StakeSportsDataCollector(client=FakeStakeClient(), max_workers=4)
        self.assertEqual(collector.available_sport_slugs(), ["demo"])
        events = collector.fetch_all(["demo"])
        self.assertEqual(len(events), 14)
        self.assertEqual(collector.audit["fixtures_listed"], 14)
        self.assertEqual(collector.audit["limited_fallback_sports"], [])

    def test_compact_snapshot_keeps_only_pick_markets(self):
        event = core.NormalizedEvent(
            event_id="1", source="stake", sport="demo", league="x",
            home="A", away="B", start_time=None, is_live=False,
            status="active", last_update="", raw={}, markets=[
                core.NormalizedMarket("moneyline", "Winner", [core.NormalizedOutcome("A", 1.8)]),
                core.NormalizedMarket("totals", "Total", [core.NormalizedOutcome("Over", 1.9)]),
            ],
        )
        payload = core.event_to_dict_pick_markets(event)
        self.assertEqual([m["key"] for m in payload["markets"]], ["moneyline"])

    def test_partial_winner_is_not_match_moneyline(self):
        self.assertEqual(core.stake_market_key("Winner (Incl. Overtime)"), "moneyline")
        self.assertNotEqual(core.stake_market_key("Map 1 Winner"), "moneyline")
        self.assertNotEqual(core.stake_market_key("1st Set Winner"), "moneyline")

    def test_current_schema_records_only_mature_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            elo = core.EloModel(Path(tmp) / "elo.json")
            for i in range(8):
                elo.update("demo:league", "a", "b", float(i % 2 == 0), f"g{i}")
            self.assertEqual(elo.state["schema_version"], core.ELO_SCHEMA_VERSION)
            self.assertEqual(len(elo.state["brier"]["demo:league"]), 3)

    def test_schema_four_uses_sport_home_advantage(self):
        self.assertEqual(core.EloModel.home_advantage("basketball:nba"), 60.0)
        self.assertEqual(core.EloModel.home_advantage("tennis:atp"), 0.0)

    def test_current_schema_requires_absolute_brier_and_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            elo = core.EloModel(Path(tmp) / "elo.json")
            # Excelente en términos absolutos, aunque ligeramente peor que
            # el baseline de una muestra extremadamente desequilibrada.
            elo.state["brier"] = {"demo:league": [
                {"kind": "binary", "p": 0.1, "y": 0.0} for _ in range(29)
            ] + [{"kind": "binary", "p": 0.1, "y": 1.0}]}
            metrics = elo.evaluation_metrics("demo:league")
            self.assertLess(metrics["skill"], 0.0)
            self.assertTrue(metrics["absolute_ok"])
            self.assertFalse(metrics["active"])

    def test_competition_identity_separates_same_league_slug(self):
        england = core.elo_namespace(
            "soccer", "premier-league", category="england", tournament_id="100"
        )
        nigeria = core.elo_namespace(
            "soccer", "premier-league", category="nigeria", tournament_id="200"
        )
        self.assertNotEqual(england, nigeria)

    def test_stake_event_serializes_competition_identity(self):
        listing = {
            "id": "1", "name": "A - B", "competitors": ["A", "B"],
            "startTime": 4102444800000,
            "_stake_category": {"id": "10", "slug": "england"},
            "_stake_tournament": {"id": "20", "slug": "premier-league"},
        }
        detail = {
            "fixture": {"id": "1", "name": "A - B", "status": "active", "startTime": 4102444800000},
            "groups": [{"markets": [{
                "id": "m", "name": "Winner", "status": "active",
                "outcomes": [{"name": "A", "odds": 1.8}, {"name": "B", "odds": 2.1}],
            }]}],
        }
        event = core.StakeSportsDataCollector.normalize_api_event("soccer", listing, detail)
        payload = core.event_to_dict_pick_markets(event)
        self.assertEqual(payload["category"], "england")
        self.assertEqual(payload["category_id"], "10")
        self.assertEqual(payload["tournament_id"], "20")
        self.assertEqual(payload["competition_key"], "soccer/10/20")

    def test_snapshot_delay_does_not_change_scheduled_interval(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        timing = core.snapshot_timing(
            {"generado_utc": "2026-01-01T09:00:00Z"}, now
        )
        self.assertEqual(timing["expected_interval_minutes"], 30.0)
        self.assertEqual(timing["actual_gap_minutes"], 180.0)
        self.assertTrue(timing["schedule_delayed"])

    def test_never_matches_different_esports_or_residual_sports(self):
        stake = self._event("stake", "fifa")
        reference = self._event("bovada", "counter-strike")
        self.assertIsNone(core.match_event(stake, [reference]))
        stake = self._event("stake", "darts")
        reference = self._event("bovada", "snooker")
        self.assertIsNone(core.match_event(stake, [reference]))


if __name__ == "__main__":
    unittest.main()
