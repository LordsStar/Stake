"""Genera un reporte reproducible de calibración Elo sin tocar producción."""

import argparse
import json
import tempfile
from pathlib import Path

from blindado_core import (
    APP_VERSION,
    BRIER_MAX,
    BRIER_MIN,
    BRIER_SKILL_MIN,
    BRIER_WINDOW,
    ELO_K,
    ELO_MIN_GAMES,
    EloModel,
    TeamAliasRegistry,
    load_results,
    train_elo_from_results,
    utc_now,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="state/elo_backtest_report.md")
    args = parser.parse_args()
    results = load_results()
    with tempfile.TemporaryDirectory(prefix="blindado-backtest-") as tmp:
        elo = EloModel(Path(tmp) / "elo.json")
        train_elo_from_results(elo, TeamAliasRegistry(), results)
        coverage = elo.coverage()

    lines = [
        "# Backtest Elo Blindado",
        "",
        f"Generado: `{utc_now().isoformat()}`  ",
        f"Versión: `{APP_VERSION}`  ",
        f"Resultados: **{len(results)}**  ",
        f"Parámetros: K={ELO_K}, mínimo por participante={ELO_MIN_GAMES}, "
        f"muestras maduras={BRIER_MIN}, ventana={BRIER_WINDOW}, "
        f"skill mínimo={BRIER_SKILL_MIN:.1%}, tope Brier binario={BRIER_MAX:.3f}.",
        "",
        "| Namespace | Equipos calibrados | Muestras | Brier | Baseline | Skill | Activo | Motivo |",
        "|---|---:|---:|---:|---:|---:|:---:|---|",
    ]
    for namespace, info in sorted(coverage.items()):
        skill = info.get("brier_skill_score")
        lines.append(
            f"| {namespace} | {info.get('equipos_calibrados', 0)}/{info.get('equipos_totales', 0)} "
            f"| {info.get('predicciones_brier', 0)} | {info.get('brier')} "
            f"| {info.get('baseline_brier')} | {skill if skill is not None else 'N/D'} "
            f"| {'sí' if info.get('modelo_activo') else 'no'} | {info.get('motivo_calibracion', '')} |"
        )
    lines.extend([
        "",
        "## Datos estructurados",
        "",
        "```json",
        json.dumps(coverage, ensure_ascii=False, indent=2),
        "```",
        "",
        "Este reporte es diagnóstico: no cambia parámetros ni el estado Elo de producción.",
    ])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reporte guardado en {output} ({len(coverage)} namespaces).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
