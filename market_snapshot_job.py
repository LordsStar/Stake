"""Genera snapshot.json desde un job cloud, sin login ni datos de usuario.

Descubre todos los deportes habilitados y recorre categorías/torneos para
evitar el endpoint limitado a 10 fixtures. Si Bovada bloquea al runner, lo
registra explícitamente; no fabrica referencias.
"""

import argparse
import json
from pathlib import Path

from blindado_core import (
    annotate_market_observations,
    BovadaCollector,
    StakeSportsDataCollector,
    append_movement_history,
    bovada_key_for_event,
    dedupe_events,
    event_to_dict_pick_markets,
    load_json,
    merge_movement_history,
    normalized_event_from_dict,
    parse_dt,
    snapshot_timing,
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

    generated_at = utc_now()
    timing = snapshot_timing(previous, generated_at)
    expected_interval = timing["expected_interval_minutes"]
    stake_observability = annotate_market_observations(
        stake_events,
        previous.get("stake_events", []) if isinstance(previous, dict) else [],
        generated_at,
        expected_interval,
    )
    # Stake permite identificar de forma segura algunos fallos de detalle por
    # event_id. Solo esos eventos exactos se arrastran; nunca se rellena una
    # liga completa por aproximación de nombres.
    carried_stake = []
    current_stake_ids = {event.event_id for event in stake_events}
    if stake.failed_event_ids and isinstance(previous, dict):
        for item in previous.get("stake_events", []):
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id", ""))
            if event_id not in stake.failed_event_ids or event_id in current_stake_ids:
                continue
            start = parse_dt(item.get("start_time"))
            if not start or start <= generated_at:
                continue
            event = normalized_event_from_dict(item, int(previous.get("schema_version", 4) or 4))
            event.fetch_status = "failed"
            event.carried_forward = True
            event.audit_flags = [
                flag for flag in event.audit_flags
                if flag not in {"linea_estable", "linea_modificada", "possible_cache_upstream"}
            ]
            event.audit_flags.append("fetch_failed_current_run")
            carried_stake.append(event)
    stake_events = dedupe_events(stake_events + carried_stake)
    stake_observability["carried_forward"] = len(carried_stake)
    bovada_observability = annotate_market_observations(
        bovada_events,
        previous.get("bovada_events", []) if isinstance(previous, dict) else [],
        generated_at,
        expected_interval,
    )
    # Bovada informa qué ligas fallaron. Solo para esas ligas conservamos
    # eventos previos y, crucialmente, NO actualizamos market_fetched_at.
    # El motor decidirá si la última observación válida todavía es utilizable.
    carried_bovada = []
    current_bovada_ids = {event.event_id for event in bovada_events}
    if unavailable and isinstance(previous, dict):
        for item in previous.get("bovada_events", []):
            if not isinstance(item, dict) or item.get("league") not in unavailable:
                continue
            if str(item.get("event_id", "")) in current_bovada_ids:
                continue
            start = parse_dt(item.get("start_time"))
            if not start or start <= generated_at:
                continue
            event = normalized_event_from_dict(item, int(previous.get("schema_version", 4) or 4))
            event.fetch_status = "failed"
            event.carried_forward = True
            event.audit_flags = [
                flag for flag in event.audit_flags
                if flag not in {"linea_estable", "linea_modificada", "possible_cache_upstream"}
            ]
            event.audit_flags.append("fetch_failed_current_run")
            carried_bovada.append(event)
    bovada_events = dedupe_events(bovada_events + carried_bovada)
    bovada_observability["carried_forward"] = len(carried_bovada)

    payload = {
        "schema_version": 5,
        "generado_utc": generated_at.isoformat(),
        "snapshot_generated_at": generated_at.isoformat(),
        "snapshot_history": timing["snapshot_history"],
        "expected_fetch_interval_minutes": expected_interval,
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
        "market_observability": {
            "stake": stake_observability,
            "bovada": bovada_observability,
        },
        "movement_history": movement,
        # El modelo y las variables MLB viajan junto al snapshot para que
        # Streamlit no dependa de que un redeploy coincida con el entrenamiento.
        "mlb_model": load_json(Path("state/mlb/model.json"), {}),
        "mlb_pregame": load_json(Path("state/mlb/pregame.json"), []),
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
