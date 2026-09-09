"""Orquestador único para resultados, alias y Elo.

GitHub Actions lo ejecuta automáticamente. También puede lanzarse a mano con
``python results_pipeline.py``; no requiere credenciales ni datos personales.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PIPELINE_VERSION = 4
STATE_FILE = Path("state/results_pipeline_state.json")


def run(*args: str) -> None:
    command = [sys.executable, *args]
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(payload):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-backfill", action="store_true")
    parser.add_argument("--max-leagues", type=int, default=24)
    args = parser.parse_args()

    if not Path("snapshot.json").exists():
        print("No existe snapshot.json; generándolo antes de buscar resultados.")
        run("market_snapshot_job.py")

    state = load_state()
    initial = args.full_backfill or int(state.get("pipeline_version", 0)) < PIPELINE_VERSION
    if initial:
        print("Backfill inicial v3: temporadas principales y tenis ATP/WTA.")
        run("fetch_results_espn.py", "--sports", "basketball", "ice-hockey", "--days-back", "450")
        run("fetch_results_espn.py", "--sports", "baseball", "--days-back", "240")
        run("fetch_results_espn.py", "--sports", "american-football", "--days-back", "730")
        run("fetch_results_espn.py", "--sports", "tennis", "--days-back", "180")
    else:
        run("fetch_results_espn.py", "--days-back", "7")

    run(
        "fetch_results_free.py", "--snapshot", "snapshot.json",
        "--max-leagues", str(max(0, args.max_leagues)), "--opendota-pages", "5",
    )
    run("reconcile_aliases.py", "--snapshot", "snapshot.json")
    # Reconstruye ratings/Brier para eliminar dependencia del estado previo,
    # pero elo_trainer v7.7.2 conserva activation_state y solo avanza streaks
    # cuando cambia el fingerprint de match_id de la ventana evaluada.
    run("elo_trainer.py", "--reset")
    # MLB se entrena por separado: Elo es una variable, no el modelo final.
    run("mlb_pipeline.py", "--days-back", "240" if initial else "21")

    state.update({
        "pipeline_version": PIPELINE_VERSION,
        "initial_backfill_complete": True,
        "last_successful_run": datetime.now(timezone.utc).isoformat(),
    })
    save_state(state)
    print("Pipeline completo: resultados, alias y Elo actualizados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
