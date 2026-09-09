"""
elo_trainer.py
==============
Entrena el Elo cronológicamente desde state/results/results.json.

`--reset` reconstruye ratings, Brier y procesados, pero conserva el estado
operativo de histéresis. Las métricas nunca se restauran: se recalculan desde
los resultados. `--reset-activation` es la única opción que borra también los
estados de activación de forma explícita.
"""

import argparse
import sys

from blindado_core import (
    EloModel,
    TeamAliasRegistry,
    fresh_elo_state,
    load_results,
    train_elo_from_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true",
        help="Reconstruye Elo/Brier desde cero conservando la histéresis.",
    )
    parser.add_argument(
        "--reset-activation", action="store_true",
        help="Borra también la histéresis; usar solo para una reinicialización deliberada.",
    )
    args = parser.parse_args()

    results = load_results()
    if not results:
        print("No hay resultados en state/results/results.json — nada que entrenar.")
        print("Corre fetch_results_espn.py primero, o sube un CSV desde la app.")
        return 0

    elo = EloModel()
    previous_activation = elo.state.get("activation_state", {})
    rebuild = args.reset or args.reset_activation or not elo.schema_is_current()
    if rebuild:
        if args.reset_activation:
            previous_activation = {}
            reason = "solicitud explícita --reset-activation"
        elif args.reset:
            reason = "solicitud --reset con histéresis preservada"
        else:
            reason = "migración del esquema de calibración Elo"
        print(f"Reentrenamiento completo: {reason}.")
        elo.state = fresh_elo_state(previous_activation)
        print(f"Namespaces de histéresis preservados: {len(previous_activation)}")

    aliases = TeamAliasRegistry()
    updated = train_elo_from_results(elo, aliases, results)
    print(f"Partidos aplicados al Elo en esta corrida: {updated}")
    print(f"Total de resultados en histórico: {len(results)}")

    coverage = elo.coverage()
    if not coverage:
        print("Todavía no hay ratings guardados (¿historial vacío?).")
    for sport, info in coverage.items():
        state = info.get("estado_histeresis") or {}
        print(
            f"  {sport}: {info['equipos_calibrados']}/{info['equipos_totales']} "
            f"equipos calibrados · activo={info['modelo_activo']} · "
            f"fase={state.get('fase', 'sin_estado')} · "
            f"evidencia_nueva={state.get('evidence_changed', False)}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
