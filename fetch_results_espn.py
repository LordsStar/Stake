"""
fetch_results_espn.py
======================
Fase 1 del plan de fuentes por fases: descarga resultados FINALIZADOS de
ESPN (endpoint público de scoreboard, sin API key) para NBA, NFL, NHL y
MLB, y los agrega al histórico de resultados que usa elo_trainer.py.

Uso:
    python fetch_results_espn.py --days-back 3
    python fetch_results_espn.py --sports basketball baseball --days-back 1

No inventa nada: si ESPN no devuelve un partido como STATUS_FINAL, se
ignora. Los errores de red por deporte/fecha se reportan y se continúa
con el resto (una fecha caída no debe tumbar toda la corrida).
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

import requests

from blindado_core import merge_results, safe_get

# Deportes con conector ESPN fiable en esta fase. El resto de deportes
# (fútbol, tenis, MMA, boxeo, esports) queda para fases posteriores o
# para ingesta manual vía CSV — no existe una única API pública gratuita
# que los cubra todos de forma confiable.
ESPN_TARGETS = {
    "basketball": "basketball/nba",
    "american-football": "football/nfl",
    "ice-hockey": "hockey/nhl",
    "baseball": "baseball/mlb",
    # Tenis usa una estructura distinta (torneo > groupings > competitions)
    # y se procesa con fetch_espn_tennis_scoreboard().
    "tennis": "tennis",
}


def fetch_espn_scoreboard(espn_path: str, date_str: str) -> list:
    url = f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/scoreboard"
    resp = requests.get(url, params={"dates": date_str}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for event in data.get("events", []):
        status_name = safe_get(event, "status", "type", "name") or ""
        if status_name != "STATUS_FINAL":
            continue
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        home_score = home.get("score")
        away_score = away.get("score")
        if home_score is None or away_score is None:
            continue
        rows.append({
            "event_id": f"espn:{event.get('id')}",
            "start_time": event.get("date"),
            "home_name": safe_get(home, "team", "displayName") or "",
            "away_name": safe_get(away, "team", "displayName") or "",
            "home_score": home_score,
            "away_score": away_score,
            "status": "final",
            "source": "espn",
        })
    return rows


def _competitor_name(competitor: dict) -> str:
    return (
        safe_get(competitor, "athlete", "displayName")
        or safe_get(competitor, "team", "displayName")
        or ""
    )


def fetch_espn_tennis_scoreboard(circuit: str, date_str: str) -> list:
    """Normaliza las competencias finalizadas ATP/WTA de ESPN."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{circuit}/scoreboard"
    resp = requests.get(url, params={"dates": date_str}, timeout=25)
    resp.raise_for_status()
    rows = []
    seen = set()
    for tournament in resp.json().get("events", []):
        for grouping in tournament.get("groupings", []):
            for comp in grouping.get("competitions", []):
                status_name = safe_get(comp, "status", "type", "name") or ""
                if status_name != "STATUS_FINAL" or comp.get("id") in seen:
                    continue
                competitors = comp.get("competitors") or []
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home or not away or home.get("winner") == away.get("winner"):
                    continue
                home_name, away_name = _competitor_name(home), _competitor_name(away)
                if not home_name or not away_name:
                    continue
                event_id = f"espn:tennis:{circuit}:{comp.get('id')}"
                rows.append({
                    "sport": "tennis", "league": circuit,
                    "event_id": event_id,
                    "start_time": comp.get("date") or tournament.get("date"),
                    "home_name": home_name, "away_name": away_name,
                    "home_score": 1 if home.get("winner") else 0,
                    "away_score": 1 if away.get("winner") else 0,
                    "status": "final", "source": "espn",
                    "score_encoding": "winner_indicator",
                })
                seen.add(comp.get("id"))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=2, help="Cuántos días hacia atrás revisar (incluye hoy).")
    parser.add_argument("--sports", nargs="*", default=list(ESPN_TARGETS.keys()), help="Slugs de deporte (los mismos que usa Stake).")
    args = parser.parse_args()

    all_rows = []
    fallos = []

    for sport in args.sports:
        espn_path = ESPN_TARGETS.get(sport)
        if not espn_path:
            print(f"[aviso] sin mapeo ESPN para '{sport}', se omite (deportes disponibles: {list(ESPN_TARGETS)})")
            continue
        league = espn_path.split("/")[-1]
        for d in range(max(1, args.days_back)):
            date = (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y%m%d")
            try:
                if sport == "tennis":
                    rows = []
                    for circuit in ("atp", "wta"):
                        rows.extend(fetch_espn_tennis_scoreboard(circuit, date))
                else:
                    rows = fetch_espn_scoreboard(espn_path, date)
            except Exception as exc:
                fallos.append(f"{sport} {date}: {exc}")
                continue
            for row in rows:
                row.setdefault("sport", sport)
                row.setdefault("league", league)
            all_rows.extend(rows)
            print(f"  {sport} {date}: {len(rows)} finalizado(s) encontrados")

    added, errors = merge_results(all_rows)
    print(f"\nResultados nuevos agregados al histórico: {added}")
    if errors:
        print(f"Filas rechazadas ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    if fallos:
        print(f"\nConsultas fallidas ({len(fallos)}) — no detienen la corrida:")
        for f in fallos:
            print(f"  - {f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
