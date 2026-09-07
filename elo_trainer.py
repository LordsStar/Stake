"""
elo_trainer.py
==============
Entrena el Elo interno de forma CRONOLÓGICA a partir de
state/results/results.json (alimentado por fetch_results_espn.py y/o por
cargas manuales CSV desde la app).

Es idempotente: cada resultado tiene un event_id, y EloModel.update()
ignora los que ya fueron procesados, así que correr esto varias veces
con el mismo histórico no duplica actualizaciones.

Uso:
    python elo_trainer.py
    python elo_trainer.py --reset   # vuelve a entrenar desde cero
"""

import argparse
import sys

from blindado_core import ELO_SCHEMA_VERSION, EloModel, TeamAliasRegistry, load_results, train_elo_from_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Ignora el estado guardado y reentrena desde cero.")
    args = parser.parse_args()

    elo = EloModel()
    if args.reset or not elo.schema_is_current():
        reason = "solicitud --reset" if args.reset else "migración a Elo separado por liga"
        print(f"Reentrenamiento completo: {reason}.")
        elo.state = {"schema_version": ELO_SCHEMA_VERSION, "ratings": {}, "brier": {}, "processed": {}}

    aliases = TeamAliasRegistry()
    results = load_results()
    if not results:
        print("No hay resultados en state/results/results.json — nada que entrenar.")
        print("Corre fetch_results_espn.py primero, o sube un CSV desde la app.")
        return 0

    updated = train_elo_from_results(elo, aliases, results)
    print(f"Partidos aplicados al Elo en esta corrida: {updated}")
    print(f"Total de resultados en histórico: {len(results)}")

    coverage = elo.coverage()
    if not coverage:
        print("Todavía no hay ratings guardados (¿historial vacío?).")
    for sport, info in coverage.items():
        print(f"  {sport}: {info['equipos_calibrados']}/{info['equipos_totales']} equipos calibrados")

    return 0


if __name__ == "__main__":
    sys.exit(main())
