"""
elo_backtest.py
================
Backtest walk-forward OFFLINE de los parámetros del Elo (K y ventaja de
local), usando exclusivamente el histórico real que ya existe en
state/results/results.json.

NO toca state/elo_state.json (el Elo de producción que usa la app) ni los
umbrales Brier. Esto es deliberado: sirve para decidir CON EVIDENCIA si vale la
pena ajustar ELO_K / ELO_HOME_BY_SPORT en blindado_core.py antes de siquiera
considerar tocar el umbral del Brier — nunca para inventar un resultado
que justifique bajarlo.

Reutiliza elo_namespace() y TeamAliasRegistry del propio core para que la
agrupación por competencia y la resolución de alias sea IDÉNTICA a la que
usa el motor en producción. Lo único que cambia entre corridas del grid es
K y la ventaja de local: nada más se toca, y ningún resultado se inventa
o se completa — si un namespace no tiene resultados reales, simplemente
no aparece en el reporte.

Metodología (igual que EloModel.update(), reimplementada en memoria):
  1. Ordena TODOS los resultados de la competencia cronológicamente.
  2. Para cada partido: predice con los ratings ANTERIORES (nunca con
     información del futuro), registra el error cuadrático, y SOLO
     entonces actualiza los ratings con ese resultado real.
  3. Repite para cada combinación (K, home_adv) del grid.
  4. Reporta el Brier de cada combinación por namespace, comparado contra
     los parámetros actuales de producción (ELO_K y ventaja por deporte).

Uso:
    python elo_backtest.py
    python elo_backtest.py --namespace american-football:nfl baseball:mlb
    python elo_backtest.py --k-grid 10 15 20 25 30 40 --home-grid 0 25 50 75 100
    python elo_backtest.py --min-games 5 --brier-min 8
    python elo_backtest.py --output state/elo_backtest_report.md
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional

from blindado_core import (
    BRIER_MAX,
    BRIER_MIN,
    BRIER_MULTICLASS_MAX,
    BRIER_SKILL_MIN,
    BRIER_WINDOW,
    ELO_HOME_BY_SPORT,
    ELO_INITIAL,
    ELO_K,
    ELO_MIN_GAMES,
    TeamAliasRegistry,
    elo_namespace,
    load_results,
    parse_dt,
)


def _ordered_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        (r for r in results if parse_dt(r.get("start_time"))),
        key=lambda r: parse_dt(r["start_time"]),
    )


def _home_win(row: Dict[str, Any]) -> float:
    hs, aw = float(row["home_score"]), float(row["away_score"])
    return 1.0 if hs > aw else (0.5 if hs == aw else 0.0)


def backtest_namespace(
    rows: List[Dict[str, Any]],
    aliases: TeamAliasRegistry,
    namespace: str,
    k: float,
    home_adv: float,
    min_games: int,
    brier_min: int,
) -> Dict[str, Any]:
    """Replica EloModel.update()/probability() en memoria, sin tocar disco,
    para UNA sola combinación de parámetros y UN namespace. No comparte
    estado entre llamadas: cada combinación del grid arranca desde
    ELO_INITIAL, igual que arrancaría el Elo de producción desde cero."""
    ratings: Dict[str, Dict[str, float]] = {}
    all_errors: List[float] = []
    mature: List[Dict[str, Any]] = []
    draw_total = 0
    draw_count = 0

    def prob(h_elo: float, a_elo: float) -> float:
        return 1 / (1 + 10 ** ((a_elo - (h_elo + home_adv)) / 400))

    for row in rows:
        home_id = aliases.canonical_id(namespace, row["home_name"])
        away_id = aliases.canonical_id(namespace, row["away_name"])
        h = ratings.setdefault(home_id, {"elo": ELO_INITIAL, "games": 0})
        a = ratings.setdefault(away_id, {"elo": ELO_INITIAL, "games": 0})

        p = prob(h["elo"], a["elo"])
        y = _home_win(row)
        is_mature = h["games"] >= min_games and a["games"] >= min_games
        if namespace.startswith("soccer:"):
            p_draw = min(0.40, max(0.08, (draw_count + 2.6) / (draw_total + 10.0)))
            remaining = 1.0 - p_draw
            probs = {"home": p * remaining, "draw": p_draw, "away": (1.0 - p) * remaining}
            outcome = "home" if y > 0.5 else ("away" if y < 0.5 else "draw")
            err = sum((probs[label] - (1.0 if outcome == label else 0.0)) ** 2 for label in probs)
            if is_mature:
                mature.append({"kind": "multiclass", "error": err, "y": outcome})
        else:
            err = (p - y) ** 2
            if is_mature:
                mature.append({"kind": "binary", "error": err, "y": y})
        all_errors.append(err)

        h["elo"] += k * (y - p)
        a["elo"] += k * ((1 - y) - (1 - p))
        h["games"] += 1
        a["games"] += 1
        draw_total += 1
        if y == 0.5:
            draw_count += 1

    recent = mature[-BRIER_WINDOW:]
    model_brier = sum(x["error"] for x in recent) / len(recent) if recent else None
    if recent and recent[0]["kind"] == "multiclass":
        labels = ("home", "draw", "away")
        frequencies = {label: sum(x["y"] == label for x in recent) / len(recent) for label in labels}
        baseline = sum(
            sum((frequencies[label] - (1.0 if x["y"] == label else 0.0)) ** 2 for label in labels)
            for x in recent
        ) / len(recent)
        absolute_limit = BRIER_MULTICLASS_MAX
    elif recent:
        base_p = sum(float(x["y"]) for x in recent) / len(recent)
        baseline = sum((base_p - float(x["y"])) ** 2 for x in recent) / len(recent)
        absolute_limit = BRIER_MAX
    else:
        baseline = None
        absolute_limit = BRIER_MULTICLASS_MAX if namespace.startswith("soccer:") else BRIER_MAX
    skill = (1.0 - model_brier / baseline) if model_brier is not None and baseline and baseline > 0 else None
    enough = len(recent) >= brier_min
    active = bool(enough and (
        (model_brier is not None and model_brier <= absolute_limit)
        or (skill is not None and skill >= BRIER_SKILL_MIN)
    ))

    return {
        "partidos": len(rows),
        "equipos": len(ratings),
        "equipos_calibrados": sum(1 for r in ratings.values() if r["games"] >= min_games),
        "brier_todos": round(sum(all_errors) / len(all_errors), 4) if all_errors else None,
        "brier_solo_calibrados": round(model_brier, 4) if model_brier is not None else None,
        "baseline_brier": round(baseline, 4) if baseline is not None else None,
        "brier_skill_score": round(skill, 4) if skill is not None else None,
        "limite_brier_absoluto": absolute_limit,
        "modelo_activo_schema4": active,
        "muestras_calibradas": len(recent),
        "brier_min_requerido": brier_min,
    }


def _headline_brier(metrics: Dict[str, Any]) -> Optional[float]:
    """Prioriza el Brier medido solo sobre partidos ya calibrados (más
    representativo de lo que el gate en producción realmente evaluaría);
    si no hay suficientes muestras calibradas todavía, usa el Brier global
    como aproximación, dejándolo explícito en el reporte."""
    if metrics.get("muestras_calibradas", 0) < metrics.get("brier_min_requerido", BRIER_MIN):
        return None
    return metrics["brier_solo_calibrados"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--namespace", nargs="*", default=None,
        help="Namespaces Elo a probar (ej. american-football:nfl baseball:mlb). "
             "Por defecto: todos los que ya tengan >= --brier-min partidos.",
    )
    parser.add_argument("--k-grid", nargs="*", type=float, default=[10, 15, 20, 25, 30, 40])
    parser.add_argument("--home-grid", nargs="*", type=float, default=[0, 25, 50, 65, 75, 100])
    parser.add_argument("--min-games", type=int, default=ELO_MIN_GAMES)
    parser.add_argument("--brier-min", type=int, default=BRIER_MIN)
    parser.add_argument(
        "--output", default=None,
        help="Ruta de un .md donde volcar el mismo reporte (para que un "
             "workflow lo commitee, ej. state/elo_backtest_report.md, sin "
             "necesidad de dejar una computadora encendida ni de correrlo "
             "manualmente cada vez).",
    )
    args = parser.parse_args()
    lines: List[str] = []

    def emit(msg: str = "") -> None:
        print(msg)
        lines.append(msg)

    results = load_results()
    if not results:
        emit("state/results/results.json está vacío — nada que backtestear.")
        emit("Corre fetch_results_espn.py / fetch_results_free.py primero.")
        _write_report(args.output, lines)
        return 0

    aliases = TeamAliasRegistry()
    ordered = _ordered_results(results)

    by_namespace: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        by_namespace[elo_namespace(row["sport"], row.get("league", ""))].append(row)

    targets = args.namespace or [
        ns for ns, rows in by_namespace.items() if len(rows) >= args.brier_min
    ]
    if not targets:
        emit("Ningún namespace tiene todavía >= --brier-min partidos históricos.")
        _write_report(args.output, lines)
        return 0

    emit(f"# Backtest offline de Elo — {datetime.now(timezone.utc).isoformat()}")
    emit()
    emit(f"Grid: K en {args.k_grid} × ventaja_local en {args.home_grid} "
         f"({len(args.k_grid) * len(args.home_grid)} combinaciones por namespace)")
    emit()

    for namespace in sorted(targets):
        rows = by_namespace.get(namespace, [])
        if len(rows) < args.brier_min:
            emit(f"## {namespace} ({len(rows)} partidos — insuficiente para Brier, se omite)")
            emit()
            continue

        emit(f"## {namespace} ({len(rows)} partidos históricos)")

        production_home = ELO_HOME_BY_SPORT.get(namespace.split(":", 1)[0], 0.0)
        baseline = backtest_namespace(rows, aliases, namespace, ELO_K, production_home, args.min_games, args.brier_min)
        emit(
            f"- **Producción actual** K={ELO_K:g} home={production_home:g} -> "
            f"brier_todos={baseline['brier_todos']} "
            f"brier_solo_calibrados={baseline['brier_solo_calibrados']} "
            f"baseline={baseline['baseline_brier']} skill={baseline['brier_skill_score']} "
            f"activo_schema4={baseline['modelo_activo_schema4']} "
            f"(equipos_calibrados={baseline['equipos_calibrados']}/{baseline['equipos']}, "
            f"muestras_calibradas={baseline['muestras_calibradas']})"
        )

        best = None
        for k, home_adv in product(args.k_grid, args.home_grid):
            metrics = backtest_namespace(rows, aliases, namespace, k, home_adv, args.min_games, args.brier_min)
            b = _headline_brier(metrics)
            if b is None:
                continue
            if best is None or b < best[0]:
                best = (b, k, home_adv, metrics)

        if best:
            b, k, home_adv, metrics = best
            emit(
                f"- **Mejor del grid** K={k:g} home={home_adv:g} -> "
                f"brier_todos={metrics['brier_todos']} "
                f"brier_solo_calibrados={metrics['brier_solo_calibrados']} "
                f"baseline={metrics['baseline_brier']} skill={metrics['brier_skill_score']} "
                f"activo_schema4={metrics['modelo_activo_schema4']} "
                f"(equipos_calibrados={metrics['equipos_calibrados']}/{metrics['equipos']})"
            )
            base_b = _headline_brier(baseline)
            if base_b is not None:
                delta = base_b - b
                if delta > 0.003 and metrics.get("modelo_activo_schema4"):
                    veredicto = "mejora relevante y conserva/supera el gate; candidata a revisión manual"
                elif delta > 0.003:
                    veredicto = "mejora numérica, pero no activa el modelo; insuficiente para cambiar producción"
                else:
                    veredicto = "mejora marginal/ruido — no justifica cambiar producción"
                emit(f"- Delta vs. producción: {delta:+.4f} -> {veredicto}")
        else:
            emit("- No hay suficientes muestras calibradas para comparar el grid en este namespace.")
        emit()

    emit(
        "Este reporte NO modificó `state/elo_state.json` ni `BRIER_MAX`. Es solo "
        "evidencia numérica para decidir si conviene ajustar `ELO_K`/`ELO_HOME_BY_SPORT` "
        "en `blindado_core.py` — y con qué respaldo, en vez de tocar el umbral "
        "del Brier a ciegas cada vez que sale `NINGUNO`."
    )
    _write_report(args.output, lines)
    return 0


def _write_report(output: Optional[str], lines: List[str]) -> None:
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReporte también escrito en {path}")


if __name__ == "__main__":
    sys.exit(main())
