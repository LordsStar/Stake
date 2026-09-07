"""Genera snapshot.json desde un job cloud, sin login ni datos de usuario.

Descubre todos los deportes habilitados y recorre categorías/torneos para
evitar el endpoint limitado a 10 fixtures. Si Bovada bloquea al runner, lo
registra explícitamente; no fabrica referencias.
"""

import argparse
import json
from pathlib import Path

from blindado_core import (
    BovadaCollector,
    StakeSportsDataCollector,
    append_movement_history,
    bovada_key_for_event,
    dedupe_events,
    event_to_dict_pick_markets,
    load_json,
    merge_movement_history,
    utc_now,
)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="snapshot.json")
    parser.add_argument(
        "--sports", nargs="*", default=None,
        help="Slugs concretos; si se omite, descubre todos los habilitados en /sports.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    previous = load_json(output, {})
    previous_history = previous.get("movement_history") if isinstance(previous, dict) else {}
    if isinstance(previous_history, dict):
        merge_movement_history(previous_history)

    stake = StakeSportsDataCollector(max_workers=6)
    stake_events = stake.fetch_all(args.sports)
    movement = append_movement_history(stake_events)

    keys = sorted({key for event in stake_events if (key := bovada_key_for_event(event))})
    bovada_events, unavailable = BovadaCollector().fetch_all(keys)

    payload = {
        "schema_version": 3,
        "generado_utc": utc_now().isoformat(),
        "stake_sports": stake.sports_catalog,
        "stake_coverage": stake.audit,
        # Solo moneyline/draw_no_bet: son las únicas claves que el motor de
        # picks evalúa (ver market_odds()/build_elo_model()). El snapshot
        # es respaldo de liquidez para el motor, no una copia de inspección
        # de totales/hándicaps/props — esas líneas sobraban y hacían que el
        # archivo superara el límite de seguridad de 25 MB en
        # cloud_snapshot_reader.py, tumbando el fallback de Bovada COMPLETO
        # (incluido soccer, que no tenía nada que ver con el fallo original).
        "stake_events": [event_to_dict_pick_markets(e) for e in dedupe_events(stake_events)],
        "bovada_events": [event_to_dict_pick_markets(e) for e in dedupe_events(bovada_events)],
        "bovada_no_disponible": unavailable,
        "stake_errors": stake.errors,
        "movement_history": movement,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    output.write_text(raw, encoding="utf-8")
    size_mb = len(raw.encode("utf-8")) / 1_000_000
    print(
        f"Snapshot: {len(stake_events)} Stake, {len(bovada_events)} Bovada; "
        f"Bovada no disponible: {unavailable or 'ninguno'}; "
        f"tamaño: {size_mb:.2f} MB (límite de seguridad de lectura: 25 MB)"
    )
    print(f"Cobertura Stake: {json.dumps(stake.audit, ensure_ascii=False)}")
    if stake.audit.get("limited_fallback_sports"):
        print(
            "[aviso] cobertura incompleta: falló el árbol jerárquico para "
            + ", ".join(stake.audit["limited_fallback_sports"])
        )
    if size_mb > 20:
        print(
            "[aviso] el snapshot está cerca del límite de 25 MB pese al filtro "
            "a moneyline/draw_no_bet. Si esto se repite, revisar volumen de "
            "eventos por deporte y el historial de movimiento antes de tocar el límite."
        )
    return 0 if stake_events else 1


if __name__ == "__main__":
    raise SystemExit(main())
