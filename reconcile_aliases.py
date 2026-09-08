"""Reconcilia nombres de Stake con participantes del histórico Elo.

Solo crea un alias automático cuando la coincidencia es fuerte y claramente
mejor que la segunda opción. Los casos ambiguos se guardan para revisión; no se
adivinan porque una asociación incorrecta contaminaría el entrenamiento.
"""

import argparse
import difflib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from blindado_core import TEAM_ALIASES_FILE, elo_namespace, load_results, normalize_team_name


def similarity(a: str, b: str) -> float:
    a, b = normalize_team_name(a), normalize_team_name(b)
    if not a or not b:
        return 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    token = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    compact = 1.0 if a.replace(" ", "") == b.replace(" ", "") else 0.0
    return max(seq, token, compact)


def load_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default="snapshot.json")
    parser.add_argument("--aliases", default=str(TEAM_ALIASES_FILE))
    parser.add_argument("--report", default="state/unresolved_aliases.json")
    parser.add_argument("--threshold", type=float, default=0.86)
    parser.add_argument("--margin", type=float, default=0.08)
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print("Sin snapshot.json; no hay nombres de Stake que reconciliar.")
        return 0

    history_names = defaultdict(set)
    for row in load_results():
        namespace = elo_namespace(row.get("sport", ""), row.get("league", ""))
        for field in ("home_name", "away_name"):
            name = str(row.get(field) or "").strip()
            if name:
                history_names[namespace].add(name)

    snapshot = load_json(snapshot_path, {})
    aliases_path = Path(args.aliases)
    aliases = load_json(aliases_path, {})
    if not isinstance(aliases, dict):
        aliases = {}
    unresolved, created = [], 0

    for event in snapshot.get("stake_events", []):
        namespace = elo_namespace(event.get("sport", ""), event.get("league", ""))
        candidates = history_names.get(namespace, set())
        if not candidates:
            continue
        canonical_norms = {normalize_team_name(candidate): candidate for candidate in candidates}
        for field in ("home", "away"):
            stake_name = str(event.get(field) or "").strip()
            stake_norm = normalize_team_name(stake_name)
            if not stake_norm or stake_norm in canonical_norms or stake_norm in aliases.get(namespace, {}):
                continue
            ranked = sorted(
                ((similarity(stake_name, candidate), candidate) for candidate in candidates),
                reverse=True,
            )
            best_score, best_name = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= args.threshold and best_score - second_score >= args.margin:
                aliases.setdefault(namespace, {})[stake_norm] = normalize_team_name(best_name)
                created += 1
            else:
                unresolved.append({
                    "namespace": namespace,
                    "stake_name": stake_name,
                    "best_candidate": best_name,
                    "best_score": round(best_score, 3),
                    "second_score": round(second_score, 3),
                })

    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    aliases_path.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aliases_created": created,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "policy": f"score >= {args.threshold:.2f} y margen >= {args.margin:.2f}",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Alias automáticos seguros creados: {created}; pendientes: {len(unresolved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
