"""Descarga MLB gratis, construye features prepartido y entrena el modelo.

Fuente: servicios publicos de MLB (statsapi.mlb.com). Es incremental: los
boxscores ya normalizados en state/mlb/games.json no se descargan otra vez.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from mlb_model import MLB_DIR, MLB_GAMES_FILE, MLB_MODEL_FILE, MLB_PREGAME_FILE, train_model


BASE = "https://statsapi.mlb.com/api/v1"
UTC = timezone.utc


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_json(url: str, params: Optional[Dict[str, Any]] = None, attempts: int = 3) -> Any:
    last = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url, params=params, timeout=45,
                headers={"Accept": "application/json", "User-Agent": "Blindado-MLB/1.0"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 * (2 ** attempt))
    raise RuntimeError(f"MLB API fallo: {last}")


def dateranges(start: date, end: date, days: int = 14) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=days - 1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def schedule_games(start: date, end: date, hydrate: str = "probablePitcher,linescore") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for left, right in dateranges(start, end):
        payload = get_json(f"{BASE}/schedule", {
            "sportId": 1, "startDate": left.isoformat(), "endDate": right.isoformat(),
            "hydrate": hydrate,
        })
        rows.extend(game for block in payload.get("dates", []) for game in block.get("games", []))
    return rows


def pitching_row(player_id: Any, player: Dict[str, Any]) -> Dict[str, Any]:
    stats = (player.get("stats") or {}).get("pitching") or {}
    return {
        "id": str(player_id),
        "outs": int(stats.get("outs", 0) or 0),
        "hr": int(stats.get("homeRuns", 0) or 0),
        "bb": int(stats.get("baseOnBalls", 0) or 0),
        "hbp": int(stats.get("hitByPitch", 0) or 0),
        "k": int(stats.get("strikeOuts", 0) or 0),
        "pitches": int(stats.get("numberOfPitches", stats.get("pitchesThrown", 0)) or 0),
    }


def normalize_final_game(schedule_game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    game_pk = int(schedule_game["gamePk"])
    box = get_json(f"{BASE}/game/{game_pk}/boxscore")
    sides: Dict[str, Any] = {}
    for side in ("home", "away"):
        team = (box.get("teams") or {}).get(side) or {}
        pitcher_ids = team.get("pitchers") or []
        players = team.get("players") or {}
        pitchers = [pitching_row(pid, players.get(f"ID{pid}") or {}) for pid in pitcher_ids]
        schedule_side = (schedule_game.get("teams") or {}).get(side) or {}
        score = schedule_side.get("score")
        if score is None:
            score = (((schedule_game.get("linescore") or {}).get("teams") or {}).get(side) or {}).get("runs")
        if score is None or not pitchers:
            return None
        sides[side] = {
            "id": str((team.get("team") or {}).get("id") or (schedule_side.get("team") or {}).get("id")),
            "name": (team.get("team") or {}).get("name") or (schedule_side.get("team") or {}).get("name"),
            "score": int(score), "pitchers": pitchers,
        }
    return {
        "game_pk": str(game_pk), "start_time": schedule_game.get("gameDate"),
        "home_id": sides["home"]["id"], "home_name": sides["home"]["name"],
        "away_id": sides["away"]["id"], "away_name": sides["away"]["name"],
        "home_score": sides["home"]["score"], "away_score": sides["away"]["score"],
        "home_starter_id": sides["home"]["pitchers"][0]["id"],
        "away_starter_id": sides["away"]["pitchers"][0]["id"],
        "home_pitchers": sides["home"]["pitchers"], "away_pitchers": sides["away"]["pitchers"],
    }


def fetch_history(days_back: int, workers: int) -> Dict[str, Any]:
    existing_rows = load_json(MLB_GAMES_FILE, [])
    existing = {str(row.get("game_pk")): row for row in existing_rows if isinstance(row, dict)}
    today = datetime.now(UTC).date()
    scheduled = schedule_games(today - timedelta(days=max(1, days_back)), today)
    missing = [
        game for game in scheduled
        if str(game.get("gamePk")) not in existing
        and (game.get("status") or {}).get("abstractGameState") == "Final"
        and (game.get("gameType") in {"R", "F", "D", "L", "W"})
    ]
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
        jobs = {pool.submit(normalize_final_game, game): game for game in missing}
        for job in as_completed(jobs):
            game = jobs[job]
            try:
                row = job.result()
                if row:
                    existing[row["game_pk"]] = row
            except Exception as exc:
                errors.append(f"{game.get('gamePk')}: {exc}")
    rows = sorted(existing.values(), key=lambda row: row.get("start_time") or "")
    save_json(MLB_GAMES_FILE, rows)
    return {"games": rows, "added": len(missing) - len(errors), "errors": errors}


def lineup_confirmed(box: Dict[str, Any], side: str) -> bool:
    team = (box.get("teams") or {}).get(side) or {}
    players = team.get("players") or {}
    batting = [p for p in players.values() if int(p.get("battingOrder") or 0) > 0]
    return len(batting) >= 9


def fetch_pregame(days_forward: int = 3) -> List[Dict[str, Any]]:
    today = datetime.now(UTC).date()
    scheduled = schedule_games(today, today + timedelta(days=max(1, days_forward)))
    rows: List[Dict[str, Any]] = []
    now = datetime.now(UTC)
    for game in scheduled:
        if (game.get("status") or {}).get("abstractGameState") == "Final":
            continue
        teams = game.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        hp, ap = home.get("probablePitcher") or {}, away.get("probablePitcher") or {}
        lineups = False
        game_time = datetime.fromisoformat(str(game.get("gameDate", "")).replace("Z", "+00:00"))
        # Las alineaciones normalmente aparecen cerca del juego. Consultar el
        # boxscore de todos los partidos de tres dias solo agrega latencia.
        if -timedelta(minutes=30) <= game_time - now <= timedelta(hours=3):
            try:
                box = get_json(f"{BASE}/game/{game['gamePk']}/boxscore", attempts=1)
                lineups = lineup_confirmed(box, "home") and lineup_confirmed(box, "away")
            except Exception:
                pass
        rows.append({
            "game_pk": str(game.get("gamePk")), "start_time": game.get("gameDate"),
            "home_id": str((home.get("team") or {}).get("id")),
            "home_name": (home.get("team") or {}).get("name"),
            "away_id": str((away.get("team") or {}).get("id")),
            "away_name": (away.get("team") or {}).get("name"),
            "home_starter_id": str(hp.get("id") or ""), "home_starter_name": hp.get("fullName"),
            "away_starter_id": str(ap.get("id") or ""), "away_starter_name": ap.get("fullName"),
            "lineups_confirmed": lineups,
        })
    save_json(MLB_PREGAME_FILE, rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=21)
    parser.add_argument("--days-forward", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--pregame-only", action="store_true")
    args = parser.parse_args()
    MLB_DIR.mkdir(parents=True, exist_ok=True)
    if args.pregame_only:
        rows = fetch_pregame(args.days_forward)
        print(f"MLB pregame: {len(rows)} partidos")
        return 0
    history = fetch_history(args.days_back, args.workers)
    previous_model = load_json(MLB_MODEL_FILE, {})
    model = train_model(history["games"], previous_model=previous_model)
    save_json(MLB_MODEL_FILE, model)
    pregame = fetch_pregame(args.days_forward)
    print(
        f"MLB: {len(history['games'])} partidos guardados; {history['added']} nuevos; "
        f"{len(history['errors'])} errores; modelo activo={model.get('active')}; "
        f"pregame={len(pregame)}"
    )
    print(json.dumps(model.get("metrics", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
