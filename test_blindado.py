import tempfile
import unittest
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

    def test_schema_three_records_only_mature_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            elo = core.EloModel(Path(tmp) / "elo.json")
            for i in range(8):
                elo.update("demo:league", "a", "b", float(i % 2 == 0), f"g{i}")
            self.assertEqual(elo.state["schema_version"], 3)
            self.assertEqual(len(elo.state["brier"]["demo:league"]), 3)

    def test_never_matches_different_esports_or_residual_sports(self):
        stake = self._event("stake", "fifa")
        reference = self._event("bovada", "counter-strike")
        self.assertIsNone(core.match_event(stake, [reference]))
        stake = self._event("stake", "darts")
        reference = self._event("bovada", "snooker")
        self.assertIsNone(core.match_event(stake, [reference]))


if __name__ == "__main__":
    unittest.main()
