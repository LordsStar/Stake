"""Genera snapshot.json desde un job cloud, sin login ni datos de usuario.

Consulta todos los deportes configurados. Si Bovada bloquea al runner, lo
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
    event_to_dict,
    load_json,
    merge_movement_history,
    utc_now,
)

DEFAULT_SPORTS = [
    "soccer", "basketball", "baseball", "ice-hockey", "tennis",
    "american-football", "mma", "boxing", "cricket", "rugby",
    "volleyball", "table-tennis", "counter-strike", "dota-2",
    "league-of-legends", "valorant",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="snapshot.json")
    parser.add_argument("--sports", nargs="*", default=DEFAULT_SPORTS)
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
        "schema_version": 2,
        "generado_utc": utc_now().isoformat(),
        "stake_events": [event_to_dict(e) for e in dedupe_events(stake_events)],
        "bovada_events": [event_to_dict(e) for e in dedupe_events(bovada_events)],
        "bovada_no_disponible": unavailable,
        "stake_errors": stake.errors,
        "movement_history": movement,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Snapshot: {len(stake_events)} Stake, {len(bovada_events)} Bovada; "
        f"Bovada no disponible: {unavailable or 'ninguno'}"
    )
    return 0 if stake_events else 1


if __name__ == "__main__":
    raise SystemExit(main())
