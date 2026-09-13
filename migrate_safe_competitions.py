"""Retira resultados inseguros y reinicia estados derivados.

La primera migración elimina todas las filas TheSportsDB heredadas porque no
guardaban source_league_id, país ni el método de mapping. No es posible saber
retroactivamente cuáles de ellas provinieron de una coincidencia correcta.
Los resultados de ESPN, Cricsheet, OpenDota y Oracle's Elixir se conservan.
"""

import argparse
import json
from pathlib import Path


RESULTS = Path("state/results/results.json")
AUTO_ALIASES = Path("state/auto_team_aliases.json")
UNRESOLVED = Path("state/unresolved_aliases.json")


def load(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Escribe la migración; sin esto solo reporta.")
    args = parser.parse_args()

    rows = load(RESULTS, [])
    unsafe = [row for row in rows if str(row.get("source", "")).lower() == "thesportsdb"]
    clean = [row for row in rows if str(row.get("source", "")).lower() != "thesportsdb"]
    print(f"Resultados totales={len(rows)}; TheSportsDB heredados inseguros={len(unsafe)}; limpios={len(clean)}")
    if not args.apply:
        print("Simulación: usa --apply para escribir.")
        return 0

    save(RESULTS, clean)
    aliases = load(AUTO_ALIASES, {})
    aliases = {key: value for key, value in aliases.items() if not str(key).startswith("soccer:")}
    save(AUTO_ALIASES, aliases)
    save(UNRESOLVED, {
        "generated_at": None,
        "aliases_created": 0,
        "unresolved_count": 0,
        "unresolved": [],
        "policy": "Pendiente de snapshot schema 6 con identidad estable de competición",
    })
    print("Migración aplicada. Ejecuta: python elo_trainer.py --reset-activation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
