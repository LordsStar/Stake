"""Recalcula y publica la validacion cronologica del modelo MLB."""

import argparse
import json
from pathlib import Path

from mlb_model import MLB_GAMES_FILE, MLB_MODEL_FILE, train_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="state/mlb/backtest_report.json")
    args = parser.parse_args()
    try:
        games = json.loads(MLB_GAMES_FILE.read_text(encoding="utf-8"))
    except Exception:
        games = []
    try:
        previous = json.loads(MLB_MODEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    model = train_model(games, previous_model=previous)
    report = {
        "active": model.get("active", False), "reason": model.get("reason"),
        "samples": model.get("samples", 0), "test_samples": model.get("test_samples", 0),
        "features": model.get("feature_names", []), "selected_l2": model.get("l2"),
        "metrics": model.get("metrics", {}),
        "validation": model.get("validation_kind", "prospectiva pendiente"),
        "prospective_start_after": model.get("prospective_start_after"),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
