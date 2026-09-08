"""Ingesta multideporte desde fuentes gratuitas.

Usa TheSportsDB, Cricsheet, OpenDota, históricos ATP/WTA y Oracle's Elixir.
Lee las ligas presentes en snapshot.json y rota entre ellas. Una coincidencia
débil se rechaza: ampliar cobertura nunca significa mezclar ligas.
"""

import argparse
import csv
import difflib
import gzip
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from blindado_core import merge_results

BASE = "https://www.thesportsdb.com/api/v1/json/123"
CRICSHEET_RECENT = "https://cricsheet.org/downloads/recently_played_30_json.zip"
OPENDOTA_PRO_MATCHES = "https://api.opendota.com/api/proMatches"
TENNIS_ATP = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
TENNIS_WTA = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"
LOL_DATA = (
    "https://oracleselixir-downloadable-match-data.s3.us-west-2.amazonaws.com/"
    "{year}_LoL_esports_match_data_from_OraclesElixir.gzip"
)
DEFAULT_STATE = Path("state/result_source_state.json")
_LAST_THESPORTSDB_CALL = 0.0
SPORT_NAMES = {
    "soccer": {"soccer"},
    "tennis": {"tennis"},
    "mma": {"fighting", "mma", "mixed martial arts"},
    "boxing": {"fighting", "boxing"},
    "cricket": {"cricket"},
    "rugby": {"rugby", "rugby union", "rugby league"},
    "volleyball": {"volleyball"},
    "table-tennis": {"table tennis"},
    "counter-strike": {"esports"},
    "dota-2": {"esports"},
    "league-of-legends": {"esports"},
    "valorant": {"esports"},
    "badminton": {"badminton"},
    "beach-volley": {"volleyball"},
    "aussie-rules": {"australian football"},
    "bandy": {"bandy"},
    "basketball-3x3": {"basketball"},
    "darts": {"darts"},
    "floorball": {"floorball"},
    "futsal": {"soccer", "futsal"},
    "handball": {"handball"},
    "gaelic-hurling": {"gaelic games", "hurling"},
    "pesapallo": {"pesapallo"},
    "snooker": {"snooker"},
    "squash": {"squash"},
    "waterpolo": {"water polo"},
    "padel": {"padel"},
}

# Juegos electrónicos que Stake presenta como deportes separados. El
# catálogo de TheSportsDB puede o no contener una liga equivalente; una
# coincidencia ausente se registra y jamás se sustituye por otro juego.
for _esport in (
    "counter-strike-2-duels", "ecricket", "efootball-bots", "etouchdown",
    "fifa", "kings-of-glory", "mobile-legends", "nba2k", "rainbow-six",
    "rocket-league", "dota-2-duels",
):
    SPORT_NAMES[_esport] = {"esports"}


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def score(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(seq, jac)


def get_json(path: str, **params):
    global _LAST_THESPORTSDB_CALL
    # El nivel gratuito publica 30 solicitudes/minuto. Mantener 2.1 s entre
    # llamadas evita que un catálogo grande convierta el workflow en 429.
    wait = 2.1 - (time.monotonic() - _LAST_THESPORTSDB_CALL)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        response = requests.get(f"{BASE}/{path}", params=params, timeout=25)
        _LAST_THESPORTSDB_CALL = time.monotonic()
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()
        if attempt < 2:
            time.sleep(60)
    response.raise_for_status()


def load_targets(snapshot_path: Path):
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    targets = set()
    for event in payload.get("stake_events", []):
        sport, league = event.get("sport", ""), event.get("league", "")
        if sport in SPORT_NAMES and league:
            targets.add((sport, league))
    return sorted(targets)


def load_state(path: Path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rotating_targets(all_targets, limit: int, state):
    """Rota por todo el catálogo; evita procesar siempre las primeras ligas."""
    if not all_targets or limit <= 0 or limit >= len(all_targets):
        state["league_cursor"] = 0
        return all_targets
    start = int(state.get("league_cursor", 0)) % len(all_targets)
    selected = [all_targets[(start + i) % len(all_targets)] for i in range(limit)]
    state["league_cursor"] = (start + len(selected)) % len(all_targets)
    return selected


def cricsheet_rows(targets):
    """Resultados recientes de cricket, publicados gratis en JSON por Cricsheet."""
    if not any(sport == "cricket" for sport, _ in targets):
        return []
    response = requests.get(CRICSHEET_RECENT, timeout=60)
    response.raise_for_status()
    rows = []
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for filename in archive.namelist():
            if not filename.endswith(".json"):
                continue
            match = json.loads(archive.read(filename))
            info = match.get("info") or {}
            teams = info.get("teams") or []
            outcome = info.get("outcome") or {}
            winner = outcome.get("winner")
            result = str(outcome.get("result") or "").lower()
            if len(teams) != 2 or (not winner and result not in {"tie", "draw"}):
                continue
            home, away = teams
            hs, aw = ((1, 0) if winner == home else ((0, 1) if winner == away else (1, 1)))
            dates = info.get("dates") or []
            if not dates:
                continue
            match_type = str(info.get("match_type") or "all").lower()
            event_name = str((info.get("event") or {}).get("name") or "cricket")
            rows.append({
                "sport": "cricket",
                "league": f"{event_name} {match_type}",
                "event_id": f"cricsheet:{Path(filename).stem}",
                "start_time": f"{dates[0]}T00:00:00Z",
                "home_name": home,
                "away_name": away,
                # Cricsheet declara el ganador; estos valores codifican el
                # resultado para Elo y no pretenden ser el total de carreras.
                "home_score": hs,
                "away_score": aw,
                "status": "final",
                "source": "cricsheet",
                "score_encoding": "winner_indicator",
            })
    return rows


def opendota_rows(targets, pages=5):
    """Partidos profesionales Dota 2 recientes desde la API pública OpenDota."""
    if not any(sport == "dota-2" for sport, _ in targets):
        return []
    rows = []
    before = None
    for _ in range(max(1, pages)):
        params = {"less_than_match_id": before} if before else {}
        response = requests.get(OPENDOTA_PRO_MATCHES, params=params, timeout=30)
        response.raise_for_status()
        batch = response.json() or []
        if not batch:
            break
        ids = []
        for match in batch:
            radiant, dire = match.get("radiant_name"), match.get("dire_name")
            match_id, started = match.get("match_id"), match.get("start_time")
            if match_id is not None:
                ids.append(int(match_id))
            if not radiant or not dire or match_id is None or started is None or match.get("radiant_win") is None:
                continue
            radiant_win = bool(match["radiant_win"])
            rows.append({
                "sport": "dota-2", "league": "professional",
                "event_id": f"opendota:{match_id}",
                "start_time": datetime.fromtimestamp(int(started), tz=timezone.utc).isoformat(),
                "home_name": radiant, "away_name": dire,
                "home_score": 1 if radiant_win else 0,
                "away_score": 0 if radiant_win else 1,
                "status": "final", "source": "opendota",
                "score_encoding": "winner_indicator",
            })
        if not ids:
            break
        before = min(ids)
    return rows


def tennis_rows(targets):
    """ATP/WTA actuales y del año anterior, sin inferir retiros ni walkovers."""
    if not any(sport == "tennis" for sport, _ in targets):
        return []
    rows = []
    now_year = datetime.now(timezone.utc).year
    for circuit, template in (("atp", TENNIS_ATP), ("wta", TENNIS_WTA)):
        for year in (now_year - 1, now_year):
            response = requests.get(template.format(year=year), timeout=60)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            for match in csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))):
                winner, loser = match.get("winner_name"), match.get("loser_name")
                event_id = f"tennis-{circuit}:{match.get('tourney_id')}:{match.get('match_num')}"
                date = str(match.get("tourney_date") or "")
                if not winner or not loser or not match.get("tourney_id") or len(date) != 8:
                    continue
                try:
                    started = datetime.strptime(date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
                rows.append({
                    "sport": "tennis", "league": circuit,
                    "event_id": event_id, "start_time": started,
                    "home_name": winner, "away_name": loser,
                    "home_score": 1, "away_score": 0, "status": "final",
                    "source": f"jeff-sackmann-{circuit}",
                    "score_encoding": "winner_indicator",
                })
    return rows


def lol_rows(targets):
    """Partidos profesionales de LoL desde el dataset gratuito Oracle's Elixir."""
    if not any(sport == "league-of-legends" for sport, _ in targets):
        return []
    now_year = datetime.now(timezone.utc).year
    rows = []
    for year in (now_year - 1, now_year):
        response = requests.get(LOL_DATA.format(year=year), timeout=120)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        try:
            decoded = gzip.decompress(response.content).decode("utf-8-sig", errors="replace")
        except (gzip.BadGzipFile, OSError):
            decoded = response.content.decode("utf-8-sig", errors="replace")
        games = {}
        for row in csv.DictReader(io.StringIO(decoded)):
            if str(row.get("position") or "").lower() != "team":
                continue
            game_id, team = row.get("gameid"), row.get("teamname")
            if game_id and team:
                games.setdefault(game_id, []).append(row)
        for game_id, teams in games.items():
            if len(teams) != 2:
                continue
            ordered = sorted(teams, key=lambda x: str(x.get("side") or ""))
            a, b = ordered
            try:
                ra, rb = int(float(a.get("result", ""))), int(float(b.get("result", "")))
            except (TypeError, ValueError):
                continue
            if ra == rb:
                continue
            started = a.get("date") or b.get("date")
            if not started:
                continue
            rows.append({
                "sport": "league-of-legends", "league": a.get("league") or "professional",
                "event_id": f"oracles-elixir:{game_id}", "start_time": started,
                "home_name": a["teamname"], "away_name": b["teamname"],
                "home_score": ra, "away_score": rb, "status": "final",
                "source": "oracles-elixir", "score_encoding": "winner_indicator",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default="snapshot.json")
    parser.add_argument(
        "--max-leagues", type=int, default=24,
        help="Ligas TheSportsDB por corrida; rota automáticamente. 0 procesa todas.",
    )
    parser.add_argument("--coverage-output", default="state/source_coverage.json")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--opendota-pages", type=int, default=5)
    args = parser.parse_args()
    snapshot = Path(args.snapshot)
    if not snapshot.exists():
        print("Sin snapshot.json: primero ejecuta el workflow de mercados.")
        return 0

    all_targets = load_targets(snapshot)
    state_path = Path(args.state)
    source_state = load_state(state_path)
    targets = rotating_targets(all_targets, args.max_leagues, source_state)
    rows, rejected, matched = [], [], []
    try:
        all_leagues = get_json("all_leagues.php").get("leagues") or []
        thesportsdb_available = True
    except Exception as exc:
        # TheSportsDB es solo una de varias fuentes. Una caída no debe impedir
        # que se incorporen tenis, cricket o esports en la misma corrida.
        all_leagues = []
        thesportsdb_available = False
        rejected.append(f"TheSportsDB no disponible: {exc}")
    # El endpoint general gratuito puede devolver solo el catálogo destacado.
    # Se amplía por deporte y se deduplica por id.
    by_id = {l.get("idLeague"): l for l in all_leagues if l.get("idLeague")}
    catalog_sports = (
        sorted({name for sport, _ in targets for name in SPORT_NAMES[sport]})
        if thesportsdb_available else []
    )
    for sport_name in catalog_sports:
        try:
            for league in get_json("search_all_leagues.php", s=sport_name.title()).get("countries") or []:
                if league.get("idLeague"):
                    by_id[league["idLeague"]] = league
        except Exception as exc:
            print(f"[aviso] catálogo {sport_name}: {exc}")
    all_leagues = list(by_id.values())
    for sport, stake_league in targets:
        allowed = SPORT_NAMES[sport]
        candidates = [l for l in all_leagues if norm(l.get("strSport", "")) in allowed]
        ranked = sorted(((score(stake_league, l.get("strLeague", "")), l) for l in candidates), reverse=True, key=lambda x: x[0])
        if not ranked or ranked[0][0] < 0.55:
            rejected.append(f"{sport}/{stake_league}: sin liga equivalente segura")
            continue
        similarity, league = ranked[0]
        matched.append({
            "sport": sport, "stake_league": stake_league,
            "source_league": league.get("strLeague"), "similarity": round(similarity, 3),
        })
        events = []
        try:
            detail = (get_json("lookupleague.php", id=league["idLeague"]).get("leagues") or [{}])[0]
            season = detail.get("strCurrentSeason")
            if season:
                events = get_json("eventsseason.php", id=league["idLeague"], s=season).get("events") or []
        except Exception:
            events = []
        if not events:
            events = get_json("eventspastleague.php", id=league["idLeague"]).get("events") or []
        accepted = 0
        for event in events:
            hs, aw = event.get("intHomeScore"), event.get("intAwayScore")
            home, away = event.get("strHomeTeam"), event.get("strAwayTeam")
            if hs in (None, "") or aw in (None, "") or not home or not away:
                continue
            start = event.get("strTimestamp") or f"{event.get('dateEvent', '')}T{event.get('strTime') or '00:00:00'}Z"
            if not event.get("idEvent"):
                continue
            rows.append({
                "sport": sport,
                "league": stake_league,
                "event_id": f"thesportsdb:{event['idEvent']}",
                "start_time": start,
                "home_name": home,
                "away_name": away,
                "home_score": hs,
                "away_score": aw,
                "status": "final",
                "source": "thesportsdb",
            })
            accepted += 1
        print(f"{sport}/{stake_league} -> {league['strLeague']} ({similarity:.2f}): {accepted}")

    auxiliary = (
        ("Cricsheet", lambda: cricsheet_rows(all_targets)),
        ("OpenDota", lambda: opendota_rows(all_targets, args.opendota_pages)),
        ("Tenis ATP/WTA", lambda: tennis_rows(all_targets)),
        ("League of Legends", lambda: lol_rows(all_targets)),
    )
    source_counts = {}
    for source_name, loader in auxiliary:
        try:
            source_rows = loader()
            rows.extend(source_rows)
            source_counts[source_name] = len(source_rows)
            print(f"{source_name}: {len(source_rows)} resultados normalizados")
        except Exception as exc:
            # Una fuente auxiliar caída no borra ni invalida lo recogido por
            # las otras. El evento solo pasará si finalmente existe cobertura.
            print(f"[aviso] {source_name} no disponible: {exc}")
            source_counts[source_name] = f"error: {exc}"

    added, errors = merge_results(rows)
    print(f"Resultados gratuitos nuevos: {added}; rechazados por esquema: {len(errors)}")
    for message in rejected:
        print(f"[sin cobertura] {message}")
    coverage_path = Path(args.coverage_output)
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "targets_in_snapshot": len(all_targets),
        "targets_processed": len(targets),
        "matched": matched,
        "unmatched": rejected,
        "rows_collected": len(rows),
        "rows_added": added,
        "schema_rejections": errors,
        "auxiliary_sources": source_counts,
        "next_league_cursor": source_state.get("league_cursor", 0),
        "note": "Cobertura de fuentes gratuitas; una liga sin match no se aproxima ni se fabrica.",
    }
    save_state(coverage_path, coverage_payload)
    source_state["last_successful_run"] = coverage_payload["generated_at"]
    source_state["targets_in_last_snapshot"] = len(all_targets)
    save_state(state_path, source_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
