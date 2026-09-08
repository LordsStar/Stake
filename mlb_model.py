"""Modelo MLB prepartido, gratuito y sin fuga de informacion.

La version 2 usa solo dos senales predefinidas: Elo y abridor. Las pruebas de
v1 mostraron que descanso/forma introducian inestabilidad temporal y que
bullpen/carga no mejoraban de forma robusta. Alineaciones y lesiones no se usan para
entrenar porque no existe en este proyecto un historico fiable con la hora en
que esa informacion se conocio; solo se registran prospectivamente como gates.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
MLB_DIR = BASE_DIR / "state" / "mlb"
MLB_GAMES_FILE = MLB_DIR / "games.json"
MLB_MODEL_FILE = MLB_DIR / "model.json"
MLB_PREGAME_FILE = MLB_DIR / "pregame.json"
MODEL_SCHEMA_VERSION = 2

FEATURE_NAMES = [
    "elo_diff",
    "starter_fip_diff",
]


def _norm(value: str) -> str:
    value = re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower())
    aliases = {
        "d backs": "diamondbacks", "dbacks": "diamondbacks",
        "chi cubs": "chicago cubs", "chi white sox": "chicago white sox",
        "la angels": "los angeles angels", "la dodgers": "los angeles dodgers",
        "ny mets": "new york mets", "ny yankees": "new york yankees",
        "sf giants": "san francisco giants", "tb rays": "tampa bay rays",
        "kc royals": "kansas city royals", "sd padres": "san diego padres",
    }
    value = re.sub(r"\s+", " ", value).strip()
    return aliases.get(value, value)


def _dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def _ip(outs: float) -> float:
    return max(0.0, float(outs)) / 3.0


def _fip(stats: Dict[str, float], prior_ip: float, prior_fip: float = 4.20) -> float:
    """FIP reducido hacia 4.20 para evitar extremos por muestras pequenas."""
    innings = _ip(stats.get("outs", 0.0))
    numerator = (
        13.0 * stats.get("hr", 0.0)
        + 3.0 * (stats.get("bb", 0.0) + stats.get("hbp", 0.0))
        - 2.0 * stats.get("k", 0.0)
    )
    observed = (numerator / innings + 3.10) if innings >= 1.0 else prior_fip
    return (observed * innings + prior_fip * prior_ip) / (innings + prior_ip)


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


@dataclass
class TeamState:
    elo: float = 1500.0
    last_game: Optional[str] = None
    recent: deque = field(default_factory=lambda: deque(maxlen=10))
    bullpen: deque = field(default_factory=lambda: deque(maxlen=120))


class FeatureState:
    def __init__(self) -> None:
        self.teams: Dict[str, TeamState] = defaultdict(TeamState)
        self.starters: Dict[str, deque] = defaultdict(lambda: deque(maxlen=12))

    @staticmethod
    def _sum_pitching(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        out = {key: 0.0 for key in ("outs", "hr", "bb", "hbp", "k", "pitches")}
        for row in rows:
            for key in out:
                out[key] += float(row.get(key, 0) or 0)
        return out

    def _starter_fip(self, pitcher_id: str) -> Optional[float]:
        rows = list(self.starters.get(str(pitcher_id), []))
        if len(rows) < 3:
            return None
        return _fip(self._sum_pitching(rows), prior_ip=20.0)

    def _bullpen_metrics(self, team_id: str, when: datetime) -> Tuple[float, float]:
        rows = list(self.teams[team_id].bullpen)
        quality_rows = [r for r in rows if (when - _dt(r["date"])).days <= 30]
        work_rows = [r for r in rows if (when - _dt(r["date"])).total_seconds() <= 72 * 3600]
        quality = _fip(self._sum_pitching(quality_rows), prior_ip=50.0)
        workload = sum(float(r.get("pitches", 0)) for r in work_rows) / 100.0
        return quality, workload

    def features(self, game: Dict[str, Any]) -> Optional[List[float]]:
        when = _dt(game.get("start_time"))
        if not when:
            return None
        home, away = str(game["home_id"]), str(game["away_id"])
        hp, ap = str(game.get("home_starter_id") or ""), str(game.get("away_starter_id") or "")
        home_starter, away_starter = self._starter_fip(hp), self._starter_fip(ap)
        if home_starter is None or away_starter is None:
            return None
        hs, aws = self.teams[home], self.teams[away]
        return [
            (hs.elo + 20.0 - aws.elo) / 400.0,
            away_starter - home_starter,
        ]

    def update(self, game: Dict[str, Any]) -> None:
        when = _dt(game.get("start_time"))
        if not when:
            return
        home, away = str(game["home_id"]), str(game["away_id"])
        hs, aws = self.teams[home], self.teams[away]
        home_runs, away_runs = float(game["home_score"]), float(game["away_score"])
        outcome = 1.0 if home_runs > away_runs else 0.0
        p = 1.0 / (1.0 + 10 ** ((aws.elo - (hs.elo + 20.0)) / 400.0))
        hs.elo += 20.0 * (outcome - p)
        aws.elo += 20.0 * ((1.0 - outcome) - (1.0 - p))
        run_signal = max(-2.0, min(2.0, (home_runs - away_runs) / 5.0))
        hs.recent.append(run_signal)
        aws.recent.append(-run_signal)
        hs.last_game = aws.last_game = when.isoformat()
        for side, team_id in (("home", home), ("away", away)):
            pitchers = list(game.get(f"{side}_pitchers") or [])
            if pitchers:
                starter = dict(pitchers[0])
                starter["date"] = when.isoformat()
                self.starters[str(starter.get("id") or game.get(f"{side}_starter_id") or "")].append(starter)
            for reliever in pitchers[1:]:
                item = dict(reliever)
                item["date"] = when.isoformat()
                self.teams[team_id].bullpen.append(item)

    def export(self) -> Dict[str, Any]:
        return {
            "teams": {
                key: {"elo": s.elo, "last_game": s.last_game, "recent": list(s.recent), "bullpen": list(s.bullpen)}
                for key, s in self.teams.items()
            },
            "starters": {key: list(rows) for key, rows in self.starters.items()},
        }

    @classmethod
    def restore(cls, payload: Dict[str, Any]) -> "FeatureState":
        obj = cls()
        for key, row in (payload.get("teams") or {}).items():
            state = TeamState(elo=float(row.get("elo", 1500)), last_game=row.get("last_game"))
            state.recent.extend(row.get("recent") or [])
            state.bullpen.extend(row.get("bullpen") or [])
            obj.teams[str(key)] = state
        for key, rows in (payload.get("starters") or {}).items():
            obj.starters[str(key)].extend(rows or [])
        return obj


def build_observations(games: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], FeatureState]:
    state = FeatureState()
    observations: List[Dict[str, Any]] = []
    ordered = sorted((g for g in games if _dt(g.get("start_time"))), key=lambda g: _dt(g["start_time"]))
    for game in ordered:
        features = state.features(game)
        if features is not None:
            observations.append({
                "game_pk": str(game.get("game_pk")),
                "start_time": game.get("start_time"),
                "x": features,
                "y": 1.0 if float(game["home_score"]) > float(game["away_score"]) else 0.0,
                "elo_p": 1.0 / (1.0 + 10 ** (-features[0])),
            })
        state.update(game)
    return observations, state


def build_dataset(games: Sequence[Dict[str, Any]]) -> Tuple[List[List[float]], List[float], List[float], FeatureState]:
    observations, state = build_observations(games)
    xs = [row["x"] for row in observations]
    ys = [row["y"] for row in observations]
    elo_probs = [row["elo_p"] for row in observations]
    return xs, ys, elo_probs, state


def _standardize_fit(xs: Sequence[Sequence[float]]) -> Tuple[List[float], List[float]]:
    means, scales = [], []
    for col in range(len(FEATURE_NAMES)):
        values = [float(row[col]) for row in xs]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
        means.append(mean)
        scales.append(max(1e-6, math.sqrt(variance)))
    return means, scales


def _transform(xs: Sequence[Sequence[float]], means: Sequence[float], scales: Sequence[float]) -> List[List[float]]:
    return [[(float(v) - means[i]) / scales[i] for i, v in enumerate(row)] for row in xs]


def _fit_logistic(xs: Sequence[Sequence[float]], ys: Sequence[float], l2: float) -> List[float]:
    weights = [0.0] * (len(FEATURE_NAMES) + 1)
    n = max(1, len(xs))
    for step in range(2500):
        grad = [0.0] * len(weights)
        for row, y in zip(xs, ys):
            p = _sigmoid(weights[0] + sum(w * x for w, x in zip(weights[1:], row)))
            err = p - y
            grad[0] += err
            for i, value in enumerate(row, 1):
                grad[i] += err * value
        rate = 0.08 / (1.0 + step / 800.0)
        weights[0] -= rate * grad[0] / n
        for i in range(1, len(weights)):
            weights[i] -= rate * (grad[i] / n + l2 * weights[i] / n)
    return weights


def _predict(weights: Sequence[float], rows: Sequence[Sequence[float]]) -> List[float]:
    return [_sigmoid(weights[0] + sum(w * x for w, x in zip(weights[1:], row))) for row in rows]


def _brier(probs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    return sum((p - y) ** 2 for p, y in zip(probs, ys)) / len(ys) if ys else None


def train_model(games: Sequence[Dict[str, Any]], previous_model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    observations, final_state = build_observations(games)
    xs = [row["x"] for row in observations]
    ys = [row["y"] for row in observations]
    if len(xs) < 500:
        return {
            "schema_version": MODEL_SCHEMA_VERSION, "active": False,
            "reason": f"solo {len(xs)}/500 partidos con abridores y estado previo suficientes",
            "samples": len(xs), "feature_names": FEATURE_NAMES,
            "feature_state": final_state.export(),
        }
    previous = previous_model if isinstance(previous_model, dict) else {}
    if previous.get("schema_version") == MODEL_SCHEMA_VERSION and previous.get("weights"):
        means = list(previous["means"])
        scales = list(previous["scales"])
        weights = list(previous["weights"])
        selected_l2 = float(previous.get("l2", 1.0))
        marker = previous.get("prospective_start_after")
        trained_at = previous.get("trained_at")
        baseline_probability = float(previous.get("baseline_probability", 0.5))
        prospective = list(previous.get("prospective_predictions") or [])
    else:
        # La arquitectura Elo+abridor queda congelada ahora. El ultimo bloque
        # historico solo selecciona regularizacion; NO activa el modelo. La
        # activacion se decide con partidos posteriores a marker.
        validation_n = min(200, max(100, len(xs) // 5))
        train_end = len(xs) - validation_n
        means, scales = _standardize_fit(xs[:train_end])
        z = _transform(xs, means, scales)
        best: Optional[Tuple[float, float, List[float]]] = None
        for l2 in (0.1, 1.0, 10.0, 50.0):
            candidate = _fit_logistic(z[:train_end], ys[:train_end], l2)
            score = _brier(_predict(candidate, z[train_end:]), ys[train_end:])
            if score is not None and (best is None or score < best[0]):
                best = (score, l2, candidate)
        assert best is not None
        selected_l2 = best[1]
        means, scales = _standardize_fit(xs)
        z = _transform(xs, means, scales)
        weights = _fit_logistic(z, ys, selected_l2)
        marker = observations[-1]["start_time"]
        trained_at = datetime.now(UTC).isoformat()
        baseline_probability = sum(ys) / len(ys)
        prospective = []

    seen = {str(row.get("game_pk")) for row in prospective}
    marker_dt = _dt(marker)
    for row in observations:
        if str(row["game_pk"]) in seen or not marker_dt or _dt(row["start_time"]) <= marker_dt:
            continue
        zrow = [(value - means[i]) / scales[i] for i, value in enumerate(row["x"])]
        prospective.append({
            "game_pk": row["game_pk"], "start_time": row["start_time"],
            "p": _predict(weights, [zrow])[0], "elo_p": row["elo_p"], "y": row["y"],
        })
        seen.add(str(row["game_pk"]))
    # Se conservan todos los IDs para mantener idempotencia. Las métricas usan
    # como máximo los 200 más recientes, igual que el gate Elo general.
    evaluation = prospective[-200:]
    test_y = [float(row["y"]) for row in evaluation]
    test_probs = [float(row["p"]) for row in evaluation]
    elo_probs = [float(row["elo_p"]) for row in evaluation]
    model_brier = _brier(test_probs, test_y)
    baseline_brier = _brier([baseline_probability] * len(test_y), test_y)
    elo_brier = _brier(elo_probs, test_y)
    skill = (1.0 - model_brier / baseline_brier) if model_brier is not None and baseline_brier else None
    elo_gain = (elo_brier - model_brier) if model_brier is not None and elo_brier is not None else None
    enough = len(prospective) >= 100
    active = bool(
        enough and model_brier is not None and model_brier <= 0.245
        and skill is not None and skill >= 0.02
        and elo_gain is not None and elo_gain >= 0.001
    )
    if not enough:
        reason = f"validacion prospectiva: {len(prospective)}/100 partidos nuevos"
    elif active:
        reason = "aprobado prospectivamente: supera limite absoluto, baseline y Elo"
    else:
        reason = (
            f"prospectivo requiere Brier<=0.245, skill>=2% y mejora Elo>=0.001; "
            f"obtuvo {model_brier:.4f}, {skill:.2%}, {elo_gain:.4f}"
        )
    return {
        "schema_version": MODEL_SCHEMA_VERSION,
        "trained_at": trained_at,
        "last_evaluated_at": datetime.now(UTC).isoformat(),
        "prospective_start_after": marker,
        "prospective_predictions": prospective,
        "active": active, "reason": reason, "samples": len(xs),
        "test_samples": len(evaluation), "prospective_samples": len(prospective),
        "validation_kind": "prospective_frozen_v2",
        "feature_names": FEATURE_NAMES, "means": means, "scales": scales,
        "weights": weights, "l2": selected_l2, "baseline_probability": baseline_probability,
        "metrics": {
            "brier": model_brier, "baseline_brier": baseline_brier,
            "elo_brier": elo_brier, "skill": skill, "elo_gain": elo_gain,
        },
        "feature_state": final_state.export(),
    }


def _legacy_train_model_removed() -> None:
    """Marcador intencional: v1 no puede reactivarse por accidente."""
    return None


class MLBPregameModel:
    def __init__(self, model: Optional[Dict[str, Any]] = None):
        self.model = model or self._load(MLB_MODEL_FILE, {})

    @staticmethod
    def _load(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def coverage(self) -> Dict[str, Any]:
        return {
            "modelo_activo": bool(
                self.model.get("schema_version") == MODEL_SCHEMA_VERSION
                and self.model.get("active")
            ),
            "muestras": self.model.get("samples", 0),
            "muestras_test": self.model.get("test_samples", 0),
            "muestras_prospectivas_totales": self.model.get("prospective_samples", 0),
            "metricas": self.model.get("metrics", {}),
            "motivo": self.model.get("reason", "modelo MLB no entrenado"),
            "variables": self.model.get("feature_names", FEATURE_NAMES),
            "validacion": self.model.get("validation_kind"),
            "inicio_prospectivo": self.model.get("prospective_start_after"),
        }

    def probability_for_event(self, event: Any, pregame: Sequence[Dict[str, Any]]) -> Tuple[Optional[float], str]:
        if self.model.get("schema_version") != MODEL_SCHEMA_VERSION:
            return None, "modelo MLB anterior; falta inicializar validacion prospectiva v2"
        if not self.model.get("active"):
            return None, str(self.model.get("reason", "modelo MLB no entrenado"))
        start = _dt(getattr(event, "start_time", None))
        if not start:
            return None, "evento MLB sin hora valida"
        best, best_score = None, -1.0
        for game in pregame or []:
            game_start = _dt(game.get("start_time"))
            if not game_start or abs((game_start - start).total_seconds()) > 12 * 3600:
                continue
            score = int(_norm(getattr(event, "home", "")) == _norm(game.get("home_name", "")))
            score += int(_norm(getattr(event, "away", "")) == _norm(game.get("away_name", "")))
            if score > best_score:
                best, best_score = game, score
        if not best or best_score < 2:
            return None, "sin coincidencia con calendario MLB oficial"
        if not best.get("home_starter_id") or not best.get("away_starter_id"):
            return None, "abridores probables no confirmados"
        minutes = (start - datetime.now(UTC)).total_seconds() / 60.0
        if minutes <= 90 and not best.get("lineups_confirmed", False):
            return None, "alineaciones no confirmadas a menos de 90 minutos"
        state = FeatureState.restore(self.model.get("feature_state") or {})
        features = state.features(best)
        if features is None:
            return None, "abridor con menos de tres aperturas previas"
        z = [(v - self.model["means"][i]) / self.model["scales"][i] for i, v in enumerate(features)]
        p = _sigmoid(self.model["weights"][0] + sum(w * x for w, x in zip(self.model["weights"][1:], z)))
        return p, "modelo MLB especializado calibrado"
