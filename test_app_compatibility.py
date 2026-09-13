import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app


class AppCoreCompatibilityTests(unittest.TestCase):
    def test_uses_event_namespace_resolver_when_available(self):
        event = SimpleNamespace(sport="soccer", league="Premier League")
        compatible_core = SimpleNamespace(
            event_elo_namespace=lambda current: f"safe:{current.league}",
            elo_namespace=lambda sport, league: "legacy",
        )

        with patch.object(app, "core", compatible_core):
            self.assertEqual(
                app.resolve_event_elo_namespace(event),
                "safe:Premier League",
            )

    def test_falls_back_when_cloud_has_previous_core_loaded(self):
        event = SimpleNamespace(sport="baseball", league="MLB")
        previous_core = SimpleNamespace(
            elo_namespace=lambda sport, league: f"{sport}:{league.lower()}",
        )

        with patch.object(app, "core", previous_core):
            self.assertEqual(
                app.resolve_event_elo_namespace(event),
                "baseball:mlb",
            )


if __name__ == "__main__":
    unittest.main()
