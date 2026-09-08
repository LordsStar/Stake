"""
blindado_core.py
=================
Lógica pura de Blindado v7.1 / Elo schema 4 — SIN dependencia de Streamlit.

Se separó del archivo de la app para que tanto la UI (Streamlit) como los
scripts de línea de comandos (elo_trainer.py, fetch_results_espn.py, y el
workflow de GitHub Actions) puedan importar exactamente la misma lógica,
sin tener que instalar streamlit en el runner de CI.

Cambios de arquitectura vs. la versión anterior (v5.1), resumidos:

1. Elo real: se agrega un pipeline de ingesta de resultados + entrenamiento
   cronológico (train_elo_from_results). Antes EloModel.update() existía
   pero nadie lo llamaba nunca.
2. Se elimina la circularidad Stake+Bovada: la función que promediaba
   ambas cuotas para "inventar" un modelo (antes model_from_market_consensus)
   ya NO se usa para calcular EV. Ahora existe market_consensus_info(),
   que sirve solo como dato informativo, nunca como modelo.
3. Gates obligatorios explícitos en vez de un score de confianza que podía
   compensar datos faltantes: modelo calibrado, frescura, liquidez, y
   estado físico (tenis/MMA/boxeo) se verifican ANTES de calcular
   confianza. Si falta alguno, se descarta sin importar el EV.
4. Movimiento de línea real: se guarda un historial acumulado por
   evento+selección (no solo el snapshot anterior) y nunca se premia
   movimiento con una sola observación.
5. Frescura aplicada también a la API en vivo (antes solo al snapshot
   remoto).
6. Liquidez real: ya no es "existe Bovada" (bool), exige coincidencia de
   evento con score mínimo y timestamp reciente de esa referencia.
7. Promociones: validan sport/market además de event_id/selection, y
   requieren "confirmed_eligible" explícito (nunca se asume elegibilidad).
8. Esports: sport_family() reconoce los slugs reales de Stake
   (counter-strike, valorant, dota-2, league-of-legends) — antes caían en
   "other" porque el chequeo buscaba un prefijo "esports" que no existe
   en STAKE_SPORT_SLUGS. Además hay un mapa de fuentes por videojuego.
9. IDs de equipo: TeamAliasRegistry normaliza nombres y permite overrides
   manuales versionables (team_aliases.json) para evitar que "NY Yankees"
   y "New York Yankees" se traten como equipos distintos en el Elo. No
   existe una base de datos gratuita completa de IDs — este mecanismo es
   incremental y honesto sobre esa limitación.
10. HTTP: sesión por hilo (thread-local), caché TTL simple, reintentos
    solo para 429/5xx respetando Retry-After, semáforo global de
    concurrencia, y nunca se reintenta 400/401/403/404.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

UTC = timezone.utc
APP_VERSION = "7.1-schema4-full-stake-catalog"

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
RESULTS_DIR = STATE_DIR / "results"
STATE_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

PROMOTIONS_FILE = STATE_DIR / "promotions.json"
PUBLIC_PROMOTIONS_FILE = STATE_DIR / "public_promotions.json"
ELO_FILE = STATE_DIR / "elo_state.json"
PHYSICAL_STATUS_FILE = STATE_DIR / "physical_status.json"
TEAM_ALIASES_FILE = STATE_DIR / "team_aliases.json"
MOVEMENT_HISTORY_FILE = STATE_DIR / "movement_history.json"
RESULTS_FILE = RESULTS_DIR / "results.json"
ELO_SCHEMA_VERSION = 4

STAKE_ODDS_DATA_URL = "https://odds-data.stake.com"
DEFAULT_TIMEOUT = 20

# Respaldo únicamente para cuando /sports no esté disponible. En ejecución
# normal los deportes se descubren dinámicamente; esta lista no limita la API.
STAKE_SPORT_SLUGS = [
    "soccer", "basketball", "baseball", "ice-hockey", "tennis",
    "american-football", "mma", "boxing", "cricket", "rugby",
    "volleyball", "table-tennis", "counter-strike", "dota-2",
    "league-of-legends", "valorant", "badminton", "beach-volley",
    "aussie-rules", "bandy", "basketball-3x3", "counter-strike-2-duels",
    "darts", "ecricket", "floorball", "futsal", "handball",
    "gaelic-hurling", "kings-of-glory", "mobile-legends", "nba2k",
    "pesapallo", "rainbow-six", "rocket-league", "snooker", "squash",
    "waterpolo", "dota-2-duels", "fifa", "padel", "efootball-bots",
    "etouchdown",
]

# Slugs de esports reales usados por Stake. El chequeo anterior
# (s.startswith("esports")) nunca hacía match con ninguno de estos —
# todo esport caía silenciosamente en la familia "other".
ESPORTS_SLUGS = {
    "age-of-empires", "arena-of-valor", "call-of-duty", "counter-strike",
    "counter-strike-2-duels", "crossfire", "dota-2", "dota-2-duels",
    "ecricket", "efootball-bots", "etouchdown", "fifa", "kings-of-glory",
    "league-of-legends", "madden", "mobile-legends", "nba2k", "overwatch-2",
    "rainbow-six", "rocket-league", "starcraft-2", "valorant",
}

BOVADA_ESPORT_SLUG_ALIASES = {
    "counter-strike-2": "counter-strike",
    "cs2": "counter-strike",
    "league-of-legends": "league-of-legends",
    "dota-2": "dota-2",
}

ESPORT_SOURCES: Dict[str, List[str]] = {
    "counter-strike": ["https://www.hltv.org/", "https://liquipedia.net/counterstrike/"],
    "valorant": ["https://www.vlr.gg/", "https://liquipedia.net/valorant/"],
    "league-of-legends": ["https://oracleselixir.com/", "https://liquipedia.net/leagueoflegends/"],
    "dota-2": ["https://www.opendota.com/", "https://liquipedia.net/dota2/"],
    "rocket-league": ["https://octane.gg/", "https://liquipedia.net/rocketleague/"],
    "mobile-legends": ["https://liquipedia.net/mobilelegends/"],
    "rainbow-six": ["https://liquipedia.net/rainbowsix/"],
    "counter-strike-2-duels": ["https://www.hltv.org/", "https://liquipedia.net/counterstrike/"],
}

BOVADA_ENDPOINTS = {
    "baseball_mlb": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/baseball/mlb",
    "basketball_nba": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/basketball/nba",
    "icehockey_nhl": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/hockey/nhl",
    "americanfootball_nfl": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/football/nfl",
    "americanfootball_ncaaf": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/football/college-football",
    "baseball_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/baseball",
    "basketball_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/basketball",
    "ice-hockey_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/hockey",
    "american-football_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/football",
    "soccer_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/soccer",
    "tennis_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/tennis",
    "mma_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/ufc-mma",
    "boxing_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/boxing",
    "cricket_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/cricket",
    "rugby_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/rugby-union",
    "volleyball_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/volleyball",
    "table-tennis_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/table-tennis",
    "esports_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/esports",
    "badminton_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/badminton",
    "beach-volley_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/beach-volleyball",
    "darts_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/darts",
    "futsal_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/futsal",
    "handball_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/handball",
    "snooker_all": "https://www.bovada.lv/services/sports/event/coupon/events/A/description/snooker",
}

BOVADA_KEY_SPORT = {
    "baseball_all": "baseball", "basketball_all": "basketball",
    "ice-hockey_all": "ice-hockey", "american-football_all": "american-football",
    "soccer_all": "soccer", "tennis_all": "tennis", "mma_all": "mma",
    "boxing_all": "boxing", "cricket_all": "cricket", "rugby_all": "rugby",
    "volleyball_all": "volleyball", "table-tennis_all": "table-tennis",
    "esports_all": "esports",
    "badminton_all": "badminton", "beach-volley_all": "beach-volley",
    "darts_all": "darts", "futsal_all": "futsal",
    "handball_all": "handball", "snooker_all": "snooker",
}

KNOWN_LEAGUE_CODES = {
    "mlb": "mlb", "major-league-baseball": "mlb",
    "nba": "nba", "national-basketball-association": "nba",
    "nhl": "nhl", "national-hockey-league": "nhl",
    "nfl": "nfl", "national-football-league": "nfl",
    "ncaaf": "ncaaf", "college-football": "ncaaf",
}

FREE_SOURCES: Dict[str, List[str]] = {
    "mlb": [
        "https://baseballsavant.mlb.com/statcast_search",
        "https://www.fangraphs.com/",
        "https://www.baseball-reference.com/",
    ],
    "nba": [
        "https://www.basketball-reference.com/",
        "https://www.nba.com/stats/",
    ],
    "wnba": [
        "https://www.basketball-reference.com/wnba/",
        "https://stats.wnba.com/",
    ],
    "nfl": [
        "https://www.pro-football-reference.com/",
        "https://github.com/nflverse/nflfastR-data",
    ],
    "nhl": [
        "https://www.hockey-reference.com/",
        "https://www.nhl.com/stats/",
    ],
    "soccer": [
        "https://fbref.com/en/",
        "https://github.com/statsbomb/open-data",
    ],
    "tennis": [
        "https://www.tennisabstract.com/",
        "https://www.atptour.com/",
        "https://www.wtatennis.com/",
    ],
    "mma": [
        "http://ufcstats.com/",
        "https://www.sherdog.com/",
    ],
    "boxing": [
        "https://boxrec.com/",
    ],
    "f1": [
        "https://www.formula1.com/",
        "https://github.com/f1db/f1db",
    ],
    "cricket": [
        "https://cricsheet.org/",
        "https://www.espncricinfo.com/",
    ],
    "esports": [
        "https://liquipedia.net/",
        "https://www.hltv.org/",
        "https://www.vlr.gg/",
        "https://www.opendota.com/",
        "https://oracleselixir.com/",
        "https://octane.gg/",
    ],
    "other": [
        "https://www.espn.com/",
    ],
}

PHYSICAL_STATUS_SPORTS = {"tennis", "mma", "boxing"}
PHYSICAL_STATUS_MAX_AGE_HOURS = 72
PHYSICAL_STATUS_OK_VALUES = {"verified_ok"}

MOVEMENT_MIN_OBSERVATIONS = 2
MOVEMENT_MAX_HISTORY = 20

LIQUIDITY_MIN_MATCH_SCORE = 0.5
LIQUIDITY_MAX_AGE_MIN = 120.0

ELO_INITIAL = 1500.0
ELO_K = 20.0
ELO_MIN_GAMES = 5
# Alias retrocompatible para herramientas antiguas; producción usa el mapa
# por deporte definido debajo.
ELO_HOME = 50.0
BRIER_MAX = 0.245
BRIER_MULTICLASS_MAX = 0.60
BRIER_MIN = 30
BRIER_WINDOW = 200
BRIER_SKILL_MIN = 0.02
BRIER_GATE_MODE = "either"
ELO_HOME_BY_SPORT = {
    "american-football": 50.0,
    "basketball": 60.0,
    "soccer": 55.0,
    "baseball": 20.0,
    "ice-hockey": 25.0,
    "rugby": 35.0,
    "volleyball": 35.0,
    # En tenis, combate, cricket y esports el primer participante no es
    # necesariamente un local real; aplicar +50 introducía sesgo artificial.
    "tennis": 0.0,
    "mma": 0.0,
    "boxing": 0.0,
    "cricket": 0.0,
    "table-tennis": 0.0,
    "counter-strike": 0.0,
    "dota-2": 0.0,
    "league-of-legends": 0.0,
    "valorant": 0.0,
    "rocket-league": 0.0,
}

BLINDADO_PROMPT = """
PROMPT — Analista Cuantitativo de Apuesta Única (Blindado v7.1 — Stake First, Gated)

OBJETIVO:
Seleccionar COMO MÁXIMO UNA sola apuesta ejecutable en Stake.com.
Un resultado de 0 picks es válido y preferido cuando no existe evidencia suficiente.

REGLAS:
1. STAKE ES LA ANCLA DE EJECUCIÓN.
   La cuota final y el mercado recomendado deben existir realmente en Stake.

2. FUENTES DE MERCADO:
   Stake = mercado principal (ejecutable).
   Bovada = referencia secundaria de-vigged (para divergencia y liquidez).
   El consenso Stake+Bovada puede mostrarse como dato informativo, pero
   NUNCA se usa para calcular una probabilidad de modelo ni un EV.

3. SEGUNDO MODELO — ÚNICA FUENTE VÁLIDA:
   Elo interno, entrenado cronológicamente con resultados reales
   (>= 5 partidos por participante y >= 30 predicciones maduras). La
   calibración usa una ventana reciente de 200. Con suficientes muestras,
   se acepta calidad absoluta (Brier binario <= 0.245 / 1X2 <= 0.60) O
   Brier Skill Score >= 2% frente al baseline de la competencia.
   Si el Elo no está calibrado para ambos equipos: DESCARTAR ese evento.
   En mercados 1X2 se usa Brier multiclase y una tasa de empate aprendida.

4. GATES OBLIGATORIOS (todos deben cumplirse; ninguno se compensa con EV
   alto ni con confianza alta):
   a) modelo_calibrado (Elo con datos suficientes)
   b) frescura_mercado (timestamp de Stake reciente según tiempo al inicio)
   c) liquidez_verificada (evento emparejado en Bovada, con similitud y
      antigüedad de cuota dentro de límites)
   d) estado_físico_verificado — SOLO para tenis/MMA/boxeo: registro
      manual con fuente, timestamp <72h y confirmación explícita.
   Si falta cualquiera de los gates aplicables: DESCARTAR.

5. EV:
   EV = P_modelo * cuota_efectiva - 1. Umbral mínimo: 4%.

6. DIVERGENCIA:
   |P_modelo - P_Bovada| > 9 puntos porcentuales => DESCARTAR.

7. CONFIANZA (solo se calcula si los gates ya se cumplieron):
   Debe ser >= 8/10. No sustituye ningún gate obligatorio.

8. FÚTBOL:
   Si P(draw) >= 30%, no recomendar ML puro salvo mercado DNB real en Stake.

9. PROMOCIONES:
   Solo se aplican si el usuario las marcó como "confirmed_eligible" para
   ese evento/mercado exacto. Nunca se asume elegibilidad universal.

10. ANTI-FABRICACIÓN:
    No inventar cuotas, resultados, lesiones, alineaciones ni
    probabilidades. Si falta información crítica: DESCARTAR.

SALIDA:
PICK DEL DÍA: NINGUNO, o una única selección.
Mostrar: evento, mercado, Stake odds, probabilidad modelo, EV, confianza,
gates superados, fuentes y motivo.
"""


# ============================================================
# Utilidades genéricas
# ============================================================
def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            v = float(value)
            if v > 10_000_000_000:
                v /= 1000.0
            return datetime.fromtimestamp(v, UTC)
        except Exception:
            return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        pass
    try:
        v = float(s)
        if v > 10_000_000_000:
            v /= 1000.0
        return datetime.fromtimestamp(v, UTC)
    except Exception:
        return None


def decimal_from_any(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", ".")
    if not s:
        return None
    # American odds SIEMPRE llevan signo explícito (+150, -200). Se detectan
    # ANTES de intentar float(s) directo porque Python acepta "+150" como
    # 150.0 válido, lo que confundiría una americana con una decimal absurda.
    m = re.fullmatch(r"([+-])\s*(\d+(?:\.\d+)?)", s)
    if m:
        n = float(m.group(2))
        if m.group(1) == "+":
            return 1.0 + n / 100.0
        if n:
            return 1.0 + 100.0 / n
        return None
    try:
        x = float(s)
        if x > 1.0:
            return x
    except Exception:
        pass
    return None


def devig(odds: Dict[str, float]) -> Dict[str, float]:
    clean = {k: float(v) for k, v in odds.items() if v and v > 1.0}
    if not clean:
        return {}
    raw = {k: 1.0 / v for k, v in clean.items()}
    total = sum(raw.values())
    return {k: p / total for k, p in raw.items()} if total else {}


def ev_decimal(prob: float, odds: float) -> float:
    return prob * odds - 1.0


def kelly_fraction(prob: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    return max(0.0, (b * prob - q) / b)


def stake_amount(
    bankroll: float,
    prob: float,
    odds: float,
    fraction: float = 0.25,
    min_pct: float = 0.005,
    max_pct: float = 0.05,
) -> float:
    k = kelly_fraction(prob, odds) * fraction
    k = min(max(k, min_pct), max_pct)
    return round(bankroll * k, 2)


def safe_get(d: Dict[str, Any], *keys: str, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


PRIVATE_STATE_FILES = {
    "promotions": (PROMOTIONS_FILE, list),
    "physical_status": (PHYSICAL_STATUS_FILE, dict),
    "team_aliases": (TEAM_ALIASES_FILE, dict),
    "movement_history": (MOVEMENT_HISTORY_FILE, dict),
}


def export_private_state() -> Dict[str, Any]:
    """Crea un respaldo descargable sin incluir Elo, resultados ni credenciales."""
    return {
        "schema_version": 1,
        "exported_at": utc_now().isoformat(),
        "state": {key: load_json(path, expected()) for key, (path, expected) in PRIVATE_STATE_FILES.items()},
    }


def import_private_state(payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("respaldo privado inválido o versión no compatible")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("el respaldo no contiene 'state'")
    for key, (path, expected) in PRIVATE_STATE_FILES.items():
        value = state.get(key, expected())
        if not isinstance(value, expected):
            raise ValueError(f"sección inválida: {key}")
        save_json(path, value)


def config_value_env(name: str, default: str = "") -> str:
    import os
    return str(os.environ.get(name, default) or default)


# ============================================================
# HTTP: sesión por hilo, caché TTL, reintentos inteligentes
# ============================================================
_GLOBAL_HTTP_SEMAPHORE = threading.Semaphore(8)


class HttpClient:
    """Cliente HTTP con sesión propia POR HILO (antes se compartía una sola
    requests.Session entre todos los hilos del ThreadPoolExecutor, lo cual
    no está garantizado como thread-safe por urllib3 bajo concurrencia).

    Reintenta solo 429 y 5xx (respetando Retry-After si viene). Nunca
    reintenta 400/401/403/404 — esos no se arreglan reintentando.
    Incluye una caché TTL simple para no golpear la misma URL dos veces
    en la misma corrida si varias partes del código la piden.
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, cache_ttl: float = 15.0):
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        # Debe ser por instancia. Si fuera atributo de clase, Stake y Bovada
        # podrían reutilizar la misma sesión del hilo y perder sus headers.
        self._thread_local = threading.local()
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._extra_headers: Dict[str, str] = {}

    @staticmethod
    def _default_headers() -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
        }

    def set_extra_headers(self, headers: Dict[str, str]) -> None:
        """Headers adicionales aplicados a toda sesión nueva creada a partir
        de este punto (p. ej. X-API-KEY). Las sesiones de hilos ya creados
        NO se actualizan retroactivamente — llamar antes de lanzar hilos."""
        self._extra_headers.update(headers)

    def _session(self) -> requests.Session:
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update(self._default_headers())
            sess.headers.update(self._extra_headers)
            self._thread_local.session = sess
        return sess

    def _cache_get(self, key: str):
        with self._cache_lock:
            item = self._cache.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > self.cache_ttl:
            return None
        return value

    def _cache_set(self, key: str, value: Any):
        with self._cache_lock:
            self._cache[key] = (time.time(), value)

    def get_json(self, url: str, params: Optional[dict] = None, use_cache: bool = True) -> Any:
        cache_key = url + "?" + json.dumps(params or {}, sort_keys=True)
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        sess = self._session()
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                with _GLOBAL_HTTP_SEMAPHORE:
                    r = sess.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < 3:
                        retry_after = r.headers.get("Retry-After")
                        wait = float(retry_after) if retry_after else 0.6 * (2 ** attempt)
                        time.sleep(wait)
                        continue
                r.raise_for_status()
                data = r.json()
                if use_cache:
                    self._cache_set(cache_key, data)
                return data
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (400, 401, 403, 404):
                    raise  # errores de cliente reales: no se arreglan reintentando
                last_exc = exc
            except requests.exceptions.RequestException as exc:
                last_exc = exc
        raise RuntimeError(f"GET falló para {url}: {last_exc}")

    def post_json(self, url: str, payload: dict, headers: Optional[dict] = None) -> Any:
        sess = self._session()
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        with _GLOBAL_HTTP_SEMAPHORE:
            r = sess.post(url, json=payload, headers=h, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


# ============================================================
# Modelos normalizados
# ============================================================
@dataclass
class NormalizedOutcome:
    selection: str
    odds: float
    active: bool = True
    point: Optional[float] = None


@dataclass
class NormalizedMarket:
    key: str
    name: str
    outcomes: List[NormalizedOutcome]


@dataclass
class NormalizedEvent:
    event_id: str
    source: str
    sport: str
    league: str
    home: str
    away: str
    start_time: Optional[str]
    is_live: bool
    status: str
    last_update: str
    markets: List[NormalizedMarket]
    raw: Dict[str, Any]


def stake_market_key(name: str) -> str:
    n = (name or "").lower()
    # "Map 1 Winner", "1st Set Winner", etc. no son el ganador del
    # partido. Clasificarlos como moneyline hacía que el motor pudiera tomar
    # accidentalmente el primer mercado parcial devuelto por la API.
    partial_winner = bool(re.search(
        r"\b(map|set|game|round|frame|inning|quarter|half|period|race)\s*\d*\b|"
        r"\b\d+(?:st|nd|rd|th)\s+(?:map|set|game|round|frame|inning|quarter|half|period)\b",
        n,
    ))
    if partial_winner and any(x in n for x in ["winner", "moneyline", "1x2", "3-way"]):
        return re.sub(r"[^a-z0-9]+", "_", n).strip("_") or "market"
    if any(x in n for x in ["winner", "moneyline", "match winner", "1x2", "3-way"]):
        return "moneyline"
    if "draw no bet" in n or n == "dnb":
        return "draw_no_bet"
    if "spread" in n or "handicap" in n:
        return "spread"
    if "total" in n or "over/under" in n:
        return "totals"
    return re.sub(r"[^a-z0-9]+", "_", n).strip("_") or "market"


def event_to_dict(e: NormalizedEvent, markets_filter: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    markets = e.markets
    if markets_filter is not None:
        allowed = set(markets_filter)
        markets = [market for market in markets if market.key in allowed]
    return {
        "event_id": e.event_id,
        "source": e.source,
        "sport": e.sport,
        "league": e.league,
        "home": e.home,
        "away": e.away,
        "start_time": e.start_time,
        "is_live": e.is_live,
        "status": e.status,
        "last_update": e.last_update,
        "markets": [
            {"key": m.key, "name": m.name, "outcomes": [asdict(o) for o in m.outcomes]}
            for m in markets
        ],
    }


PICK_ENGINE_MARKET_KEYS = ("moneyline", "draw_no_bet")


def event_to_dict_pick_markets(e: NormalizedEvent) -> Dict[str, Any]:
    """Formato compacto del snapshot: conserva todo lo que usa el motor."""
    return event_to_dict(e, markets_filter=PICK_ENGINE_MARKET_KEYS)


def dedupe_events(events: List[NormalizedEvent]) -> List[NormalizedEvent]:
    seen = {}
    for e in events:
        key = (e.source, e.event_id) if e.event_id else (
            e.source, e.sport, e.home, e.away, e.start_time
        )
        seen[key] = e
    return list(seen.values())


def market_odds(event: Optional[NormalizedEvent], preferred=("moneyline", "draw_no_bet")) -> Dict[str, float]:
    if not event:
        return {}
    for key in preferred:
        for m in event.markets:
            if m.key == key:
                return {o.selection: o.odds for o in m.outcomes if o.active and o.odds > 1}
    return {}


# ============================================================
# Stake Sports Data API oficial
# ============================================================
class StakeSportsDataCollector:
    """Descarga fixtures y cuotas sin login, cookies ni sesión de usuario.

    NOTA: se eliminó el comentario/headers heredados de una implementación
    GraphQL anterior (x-apollo-operation-name, Origin/Referer stake.com,
    sec-ch-ua, etc.). Esta clase pega a odds-data.stake.com con endpoints
    REST; esos headers eran de otra versión del collector y podían inducir
    a error al debuggear un 403 aquí (pensando que el problema era
    Cloudflare/GraphQL cuando en realidad es esta ruta REST).
    """

    def __init__(
        self,
        client: Optional[HttpClient] = None,
        delay: float = 0.0,
        max_workers: int = 6,
        api_key: Optional[str] = None,
    ):
        self.client = client or HttpClient()
        self.delay = max(0.0, float(delay))
        self.max_workers = max(1, min(int(max_workers), 10))
        key = api_key or config_value_env("STAKE_ODDS_API_KEY")
        if key:
            self.client.set_extra_headers({"X-API-KEY": key})
        self.errors: List[str] = []
        self.sports_catalog: List[Dict[str, Any]] = []
        self.audit: Dict[str, Any] = {
            "sports_discovered": 0,
            "sports_requested": 0,
            "categories": 0,
            "tournaments": 0,
            "fixtures_listed": 0,
            "fixtures_prematch": 0,
            "fixtures_detailed": 0,
            "fixtures_with_markets": 0,
            "excluded_live_or_started": 0,
            "excluded_disabled_or_outright": 0,
            "detail_failures": 0,
            "limited_fallback_sports": [],
        }

    def _get(self, path: str) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                data = self.client.get_json(f"{STAKE_ODDS_DATA_URL}{path}")
                if self.delay:
                    time.sleep(self.delay)
                return data
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in (400, 401, 403, 404):
                    raise RuntimeError(f"Stake Sports Data API respondió {status} en {path}") from exc
                if attempt < 2:
                    time.sleep(0.6 * (2 ** attempt))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.6 * (2 ** attempt))
        raise RuntimeError(f"Stake Sports Data API falló en {path}: {last_error}")

    @staticmethod
    def _slug(value: Any) -> str:
        return quote(str(value or "").strip(), safe="-")

    def discover_sports(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """Obtiene el catálogo completo oficial de `/sports`.

        Stake puede añadir o retirar categorías sin actualizar este código;
        por eso STAKE_SPORT_SLUGS es solo un respaldo de emergencia.
        """
        payload = self._get("/sports")
        rows = payload if isinstance(payload, list) else []
        sports = [row for row in rows if isinstance(row, dict) and row.get("slug")]
        if enabled_only:
            sports = [row for row in sports if row.get("enabled", True)]
        self.sports_catalog = sports
        self.audit["sports_discovered"] = len(sports)
        return sports

    def available_sport_slugs(self) -> List[str]:
        try:
            return [str(row["slug"]) for row in self.discover_sports()]
        except Exception as exc:
            self.errors.append(f"/sports: {exc}")
            return list(STAKE_SPORT_SLUGS)

    def _parallel_get(self, paths: Iterable[Tuple[str, Any]]) -> List[Tuple[Any, Any]]:
        jobs_input = list(paths)
        if not jobs_input:
            return []
        completed: List[Tuple[Any, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(jobs_input))) as pool:
            jobs = {pool.submit(self._get, path): context for path, context in jobs_input}
            for job in as_completed(jobs):
                context = jobs[job]
                try:
                    completed.append((context, job.result()))
                except Exception as exc:
                    self.errors.append(f"{context}: {exc}")
        return completed

    def list_all_fixtures(self, sport_slug: str) -> List[Dict[str, Any]]:
        """Recorre el árbol completo documentado deporte/categoría/torneo.

        `/sport/{sport}/fixture` devuelve deliberadamente una lista limitada
        (normalmente 10). El árbol `/sports/.../fixtures` es el endpoint que
        la documentación define como lista completa por torneo.
        """
        sport = self._slug(sport_slug)
        try:
            category_payload = self._get(f"/sports/{sport}/categories")
            categories = category_payload.get("categories", []) if isinstance(category_payload, dict) else []
            categories = [c for c in categories if isinstance(c, dict) and c.get("slug") and c.get("enabled", True)]
        except Exception as exc:
            self.errors.append(f"{sport_slug}/categories: {exc}")
            categories = []

        self.audit["categories"] += len(categories)
        tournament_calls = [
            (f"/sports/{sport}/{self._slug(category['slug'])}/tournaments", category)
            for category in categories
        ]
        tournament_contexts: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for category, payload in self._parallel_get(tournament_calls):
            tournaments = payload.get("tournaments", []) if isinstance(payload, dict) else []
            for tournament in tournaments:
                if isinstance(tournament, dict) and tournament.get("slug") and tournament.get("enabled", True):
                    tournament_contexts.append((category, tournament))

        if not categories or not tournament_contexts:
            # Respaldo explícitamente marcado como incompleto; nunca se anuncia
            # como cobertura total si la navegación jerárquica falla.
            listing = self._get(f"/sport/{sport}/fixture")
            fixtures = listing.get("fixture", []) if isinstance(listing, dict) else []
            clean = [f for f in fixtures if isinstance(f, dict) and f.get("slug")]
            if clean:
                self.audit["limited_fallback_sports"].append(sport_slug)
            self.audit["fixtures_listed"] += len(clean)
            return clean

        self.audit["tournaments"] += len(tournament_contexts)
        fixture_calls = [
            (
                f"/sports/{sport}/{self._slug(category['slug'])}/{self._slug(tournament['slug'])}/fixtures",
                (category, tournament),
            )
            for category, tournament in tournament_contexts
        ]
        fixtures_by_key: Dict[str, Dict[str, Any]] = {}
        for context, payload in self._parallel_get(fixture_calls):
            category, tournament = context
            rows = payload.get("fixtures", []) if isinstance(payload, dict) else []
            for fixture in rows:
                if not isinstance(fixture, dict) or not fixture.get("slug"):
                    continue
                item = dict(fixture)
                item.setdefault("category", category.get("slug") or category.get("name"))
                item.setdefault("tournament", tournament.get("slug") or tournament.get("name"))
                key = str(item.get("id") or item["slug"])
                fixtures_by_key[key] = item
        fixtures = list(fixtures_by_key.values())
        self.audit["fixtures_listed"] += len(fixtures)
        return fixtures

    def fetch_sport(self, sport_slug: str, first: Optional[int] = None) -> List[NormalizedEvent]:
        fixtures = self.list_all_fixtures(sport_slug)
        now = utc_now()
        eligible: List[Dict[str, Any]] = []
        for fixture in fixtures:
            start = parse_dt(fixture.get("startTime") or fixture.get("date"))
            status = str(fixture.get("status") or "").lower()
            disabled = (
                not fixture.get("enabled", True)
                or fixture.get("blacklisted", False)
                or str(fixture.get("type") or "match").lower() != "match"
                or not fixture.get("preMatchEnabled", True)
            )
            if disabled:
                self.audit["excluded_disabled_or_outright"] += 1
                continue
            if status in {"live", "ended", "inactive"} or not start or start <= now:
                self.audit["excluded_live_or_started"] += 1
                continue
            eligible.append(fixture)
        eligible.sort(key=lambda f: parse_dt(f.get("startTime") or f.get("date")) or datetime.max.replace(tzinfo=UTC))
        if first is not None:
            eligible = eligible[:max(0, int(first))]
        self.audit["fixtures_prematch"] += len(eligible)
        fixtures = eligible
        if not fixtures:
            return []

        events: List[NormalizedEvent] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(fixtures))) as pool:
            jobs = {
                pool.submit(self._get, f"/fixtures/{fixture['slug']}"): fixture
                for fixture in fixtures
            }
            for job in as_completed(jobs):
                fixture = jobs[job]
                try:
                    detail = job.result()
                    event = self.normalize_api_event(sport_slug, fixture, detail)
                    self.audit["fixtures_detailed"] += 1
                    if event.event_id and event.markets:
                        events.append(event)
                        self.audit["fixtures_with_markets"] += 1
                except Exception as exc:
                    self.audit["detail_failures"] += 1
                    self.errors.append(f"{sport_slug}/{fixture.get('slug', '?')}: {exc}")
        return events

    def fetch_all(self, slugs: Optional[Iterable[str]] = None) -> List[NormalizedEvent]:
        all_events: List[NormalizedEvent] = []
        if slugs is not None:
            requested = list(slugs)
            if not self.sports_catalog:
                try:
                    self.discover_sports()
                except Exception as exc:
                    self.errors.append(f"/sports: {exc}")
        else:
            requested = self.available_sport_slugs()
        self.audit["sports_requested"] = len(requested)
        for slug in requested:
            try:
                all_events.extend(self.fetch_sport(slug))
            except Exception as exc:
                self.errors.append(f"{slug}: {exc}")
        return dedupe_events(all_events)

    @staticmethod
    def _flatten_markets(groups: Any) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        seen = set()
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            for bundle in group.get("markets") or []:
                candidates = bundle if isinstance(bundle, list) else [bundle]
                for market in candidates:
                    if not isinstance(market, dict):
                        continue
                    marker = market.get("id") or (market.get("name"), market.get("specifiers", ""))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    found.append(market)
        return found

    @classmethod
    def normalize_api_event(
        cls, sport_slug: str, listing_fixture: Dict[str, Any], detail: Dict[str, Any]
    ) -> NormalizedEvent:
        node = detail.get("fixture", {}) if isinstance(detail, dict) else {}
        if not isinstance(node, dict):
            node = {}
        competitors = listing_fixture.get("competitors") or []
        names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in competitors]
        if len(names) < 2:
            names = re.split(r"\s+-\s+", str(node.get("name") or listing_fixture.get("name", "")), maxsplit=1)
        home = names[0] if names else ""
        away = names[1] if len(names) > 1 else ""
        markets: List[NormalizedMarket] = []
        newest_update = parse_dt(node.get("updatedAt") or listing_fixture.get("updatedAt"))
        for m in cls._flatten_markets(detail.get("groups", [])):
            if str(m.get("status", "active")).lower() not in ("active", "open"):
                continue
            outs: List[NormalizedOutcome] = []
            for o in m.get("outcomes") or []:
                if not isinstance(o, dict):
                    continue
                odd = decimal_from_any(o.get("odds"))
                active = bool(o.get("active", True))
                if odd and odd > 1 and active:
                    outs.append(NormalizedOutcome(
                        selection=str(o.get("name", "")),
                        odds=odd, active=active,
                        point=decimal_from_any(o.get("point")),
                    ))
            if outs:
                markets.append(NormalizedMarket(
                    key=stake_market_key(str(m.get("name", ""))),
                    name=str(m.get("name", "")), outcomes=outs,
                ))
                market_update = parse_dt(m.get("updatedAt"))
                if market_update and (not newest_update or market_update > newest_update):
                    newest_update = market_update

        tournament = listing_fixture.get("tournament") or {}
        league = tournament.get("slug") or tournament.get("name") if isinstance(tournament, dict) else str(tournament)
        league = league or listing_fixture.get("tournamentId", "")
        start = parse_dt(node.get("startTime") or listing_fixture.get("startTime"))
        raw_status = str(node.get("status") or listing_fixture.get("status") or "unknown")
        is_live = raw_status.lower() in {"live", "inplay", "in-play", "in_play"}
        return NormalizedEvent(
            event_id=str(node.get("id") or listing_fixture.get("id", "")),
            source="stake", sport=str(sport_slug), league=str(league or ""),
            home=home, away=away,
            start_time=start.isoformat() if start else None,
            is_live=is_live, status=raw_status,
            last_update=(newest_update or utc_now()).isoformat(),
            markets=markets,
            raw={"fixture": node, "listing": listing_fixture, "groups": detail.get("groups", [])},
        )


StakeCollector = StakeSportsDataCollector  # alias retrocompatible


# ============================================================
# Bovada collector
# ============================================================
def _walk_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v)


def _find_event_dicts(payload: Any) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    if isinstance(payload, dict):
        candidates = payload.get("events")
        if isinstance(candidates, list):
            for e in candidates:
                if isinstance(e, dict):
                    out.append(e)
    for d in _walk_dicts(payload):
        if "competitors" in d and ("startTime" in d or "lastModified" in d) and (
            "displayGroups" in d or "markets" in d or "description" in d
        ):
            ident = str(d.get("id", id(d)))
            if ident not in seen:
                seen.add(ident)
                out.append(d)
    return out


def _competitor_names(event: Dict[str, Any]) -> Tuple[str, str]:
    comps = event.get("competitors") or []
    names = []
    for c in comps:
        if isinstance(c, dict):
            names.append(c.get("name") or c.get("description") or c.get("shortName") or "")
    if len(names) >= 2:
        home = next((c.get("name") or c.get("description") or c.get("shortName") for c in comps if c.get("home") is True), None)
        away = next((c.get("name") or c.get("description") or c.get("shortName") for c in comps if c.get("home") is False), None)
        if home and away:
            return str(home), str(away)
        return str(names[0]), str(names[1])
    desc = str(event.get("description", ""))
    parts = re.split(r"\s+@\s+|\s+vs\.?\s+|\s+-\s+", desc, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return desc, ""


def _extract_bovada_markets(event: Dict[str, Any]) -> List[NormalizedMarket]:
    containers = []
    if isinstance(event.get("markets"), list):
        containers.extend(event["markets"])
    for group in event.get("displayGroups") or []:
        if not isinstance(group, dict):
            continue
        for key in ("markets", "itemList", "items"):
            val = group.get(key)
            if isinstance(val, list):
                containers.extend(val)
            elif isinstance(val, dict):
                items = val.get("items")
                if isinstance(items, list):
                    containers.extend(items)

    markets = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        market_name = str(c.get("name") or c.get("description") or c.get("key") or "market")
        outcomes_raw = c.get("outcomes") or c.get("selections") or c.get("outcomeList")
        if isinstance(outcomes_raw, dict):
            outcomes_raw = outcomes_raw.get("items", [])
        if not isinstance(outcomes_raw, list):
            if any(k in c for k in ("price", "odds", "americanOdds")):
                outcomes_raw = [c]
            else:
                continue
        outs = []
        for o in outcomes_raw:
            if not isinstance(o, dict):
                continue
            name = str(o.get("name") or o.get("description") or o.get("label") or o.get("shortName") or "")
            # Bovada entrega actualmente la cuota dentro de price.decimal /
            # price.american. Versiones anteriores podían traer price como
            # valor escalar, por eso conservamos ambos formatos.
            price = o.get("price")
            nested_decimal = price.get("decimal") if isinstance(price, dict) else None
            nested_american = price.get("american") if isinstance(price, dict) else None
            scalar_price = price if not isinstance(price, dict) else None
            odd = decimal_from_any(
                o.get("odds") or nested_decimal or nested_american or scalar_price
                or o.get("decimalOdds") or o.get("americanOdds") or o.get("american")
            )
            if name and odd and odd > 1:
                status = str(o.get("status", "O")).upper()
                active = bool(o.get("active", True)) and status not in {"C", "S", "H"}
                outs.append(NormalizedOutcome(selection=name, odds=odd, active=active))
        if outs:
            markets.append(NormalizedMarket(key=stake_market_key(market_name), name=market_name, outcomes=outs))
    return markets


class BovadaCollector:
    def __init__(self, client: Optional[HttpClient] = None, base_delay: float = 1.2, max_retries: int = 3):
        self.client = client or HttpClient()
        self.client.set_extra_headers({
            "Accept": "application/json",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": "https://www.bovada.lv/sports",
        })
        self.base_delay = base_delay
        self.max_retries = max_retries

    def fetch_league_checked(self, sport_key: str) -> Tuple[List[NormalizedEvent], bool]:
        """Devuelve (eventos, ok). ok=False solo si la fuente falló
        (403/429/timeout), NO simplemente porque no haya eventos ahora."""
        url = BOVADA_ENDPOINTS.get(sport_key)
        if not url:
            return [], True

        params = {"preMatchOnly": "true", "lang": "en"}
        payload = None
        for intento in range(self.max_retries):
            try:
                payload = self.client.get_json(url, params=params, use_cache=False)
                break
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (403, 429):
                    return [], False
                if intento < self.max_retries - 1:
                    time.sleep(self.base_delay * (2 ** intento))
                else:
                    return [], False
            except requests.exceptions.RequestException:
                if intento < self.max_retries - 1:
                    time.sleep(self.base_delay * (2 ** intento))
                else:
                    return [], False
            except RuntimeError:
                return [], False

        if payload is None:
            return [], False

        events = []
        for e in _find_event_dicts(payload):
            home, away = _competitor_names(e)
            if not home or not away:
                continue
            start = parse_dt(e.get("startTime"))
            markets = _extract_bovada_markets(e)
            if not markets:
                continue
            normalized_sport = BOVADA_KEY_SPORT.get(sport_key, {
                "baseball": "baseball", "basketball": "basketball",
                "icehockey": "ice-hockey", "americanfootball": "american-football",
            }.get(sport_key.split("_")[0], sport_key.split("_")[0]))
            if sport_key == "esports_all":
                link_parts = [part for part in str(e.get("link") or "").split("/") if part]
                if len(link_parts) >= 2 and link_parts[0] == "esports":
                    raw_game = link_parts[1].lower()
                    normalized_sport = BOVADA_ESPORT_SLUG_ALIASES.get(raw_game, raw_game)
            events.append(NormalizedEvent(
                event_id=f"bovada:{e.get('id', '')}", source="bovada",
                sport=normalized_sport,
                league=sport_key,
                home=home, away=away,
                start_time=start.isoformat() if start else None,
                is_live=bool(e.get("live", False)), status=str(e.get("status", "scheduled")),
                last_update=(parse_dt(e.get("lastModified")) or utc_now()).isoformat(),
                markets=markets, raw=e,
            ))
        time.sleep(self.base_delay)
        return dedupe_events(events), True

    def fetch_all(self, sport_keys: Optional[Iterable[str]] = None) -> Tuple[List[NormalizedEvent], List[str]]:
        keys = list(sport_keys or BOVADA_ENDPOINTS.keys())
        events: List[NormalizedEvent] = []
        no_disponibles: List[str] = []
        for key in keys:
            found, ok = self.fetch_league_checked(key)
            if not ok:
                no_disponibles.append(key)
            events.extend(found)
        return dedupe_events(events), no_disponibles


# ============================================================
# Familias de deporte / fuentes gratuitas (CORREGIDO: esports)
# ============================================================
def is_esport_slug(slug: str) -> bool:
    s = (slug or "").lower()
    return (
        s in ESPORTS_SLUGS
        or s.startswith("esports")
        or s.endswith("-duels")
        or s.startswith(("efootball", "ecricket", "etouchdown"))
    )


def sport_family(event: NormalizedEvent) -> str:
    s = (event.sport or "").lower()
    if is_esport_slug(s):
        return "esports"
    if s in ("baseball",):
        return "mlb"
    if s in ("basketball",):
        return "nba"
    if s in ("american-football", "americanfootball"):
        return "nfl"
    if s in ("ice-hockey", "hockey"):
        return "nhl"
    if s in ("football", "soccer"):
        return "soccer"
    if s in ("tennis",):
        return "tennis"
    if s in ("mma",):
        return "mma"
    if s in ("boxing",):
        return "boxing"
    if s in ("cricket",):
        return "cricket"
    if s in ("rugby", "rugby-union", "rugby-league"):
        return "rugby"
    if s in ("volleyball",):
        return "volleyball"
    if s in ("table-tennis", "tabletennis"):
        return "table-tennis"
    if s in ("formula-1", "f1"):
        return "f1"
    return "other"


def league_code(raw_league: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (raw_league or "").lower()).strip("-")
    if value in KNOWN_LEAGUE_CODES:
        return KNOWN_LEAGUE_CODES[value]
    for token, code in KNOWN_LEAGUE_CODES.items():
        if re.search(rf"(^|-){re.escape(token)}(-|$)", value):
            return code
    return value


def elo_namespace(sport: str, league: str) -> str:
    """Agrupa resultados en una competencia estable y comparable.

    Stake puede usar el nombre del torneo puntual como liga. Eso sirve para
    fútbol/ligas de equipo, pero fragmentaría para siempre el historial de
    ATP/WTA, deportes de combate, cricket y esports. En esos casos usamos un
    circuito o formato estable, sin mezclar juegos de esports entre sí.
    """
    sport = (sport or "other").lower()
    code = league_code(league)
    if sport == "tennis":
        circuit = "wta" if "wta" in code else ("atp" if "atp" in code else "all")
        return f"tennis:{circuit}"
    if sport in {"mma", "boxing", "table-tennis"}:
        return f"{sport}:all"
    if sport == "cricket":
        cricket_format = next((x for x in ("t20", "odi", "test") if x in code), "all")
        return f"cricket:{cricket_format}"
    if is_esport_slug(sport):
        return f"{sport}:all"
    return f"{sport}:{code or 'all'}"


def pick_capability(event: NormalizedEvent) -> Tuple[bool, str]:
    if not bovada_key_for_event(event):
        return False, "sin conector gratuito de cuota secundaria"
    return True, "conector gratuito disponible; todavía debe superar cobertura, calibración y demás gates"


def bovada_key_for_event(event: NormalizedEvent) -> Optional[str]:
    family = sport_family(event)
    league = league_code(event.league)
    prefixes = {"mlb": "baseball", "nba": "basketball", "nhl": "icehockey", "nfl": "americanfootball"}
    key = f"{prefixes.get(family, family)}_{league}"
    if key in BOVADA_ENDPOINTS:
        return key
    generic = f"{event.sport}_all"
    if generic in BOVADA_ENDPOINTS:
        return generic
    if is_esport_slug(event.sport):
        return "esports_all"
    return None


class FreeSourceRegistry:
    def sources_for(self, event: NormalizedEvent) -> List[str]:
        s = (event.sport or "").lower()
        if s in ESPORT_SOURCES:
            return ESPORT_SOURCES[s]
        return FREE_SOURCES.get(sport_family(event), FREE_SOURCES["other"])


# ============================================================
# Nombres / matching Stake vs Bovada
# ============================================================
def normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(fc|cf|sc|bc|club|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_similarity(a: str, b: str) -> float:
    ta, tb = set(normalize_name(a).split()), set(normalize_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def match_event_scored(
    stake_event: NormalizedEvent, references: List[NormalizedEvent]
) -> Tuple[Optional[NormalizedEvent], float]:
    best, best_score = None, 0.0
    for r in references:
        if sport_family(stake_event) != sport_family(r):
            continue
        family = sport_family(stake_event)
        if family in {"esports", "other"} and stake_event.sport != r.sport:
            # Esports y la familia residual mezclan categorías distintas.
            # Nombres parecidos y horarios cercanos nunca bastan para cruzarlas.
            continue
        direct = (name_similarity(stake_event.home, r.home) + name_similarity(stake_event.away, r.away)) / 2.0
        swapped = (name_similarity(stake_event.home, r.away) + name_similarity(stake_event.away, r.home)) / 2.0
        score = max(direct, swapped)
        if stake_event.start_time and r.start_time:
            a, b = parse_dt(stake_event.start_time), parse_dt(r.start_time)
            if a and b:
                minutes = abs((a - b).total_seconds()) / 60
                if minutes <= 90:
                    score += 0.25
                elif minutes > 720:
                    score -= 0.25
        if score > best_score:
            best_score, best = score, r
    if best_score >= 0.35:
        return best, best_score
    return None, best_score


def match_event(stake_event: NormalizedEvent, references: List[NormalizedEvent]) -> Optional[NormalizedEvent]:
    return match_event_scored(stake_event, references)[0]


# ============================================================
# Alias de equipos -> ID canónico
# ============================================================
def normalize_team_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"[^a-z0-9\s]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


class TeamAliasRegistry:
    """Resuelve nombres a un ID canónico estable por namespace deporte:liga.

    No existe una base de datos gratuita y completa de IDs de equipo para
    todas las ligas del mundo, así que esto se resuelve de forma
    incremental y honesta: por defecto el ID canónico es el propio nombre
    normalizado (funciona mientras Stake sea consistente con los nombres),
    y se agregan overrides manuales en team_aliases.json según se detecten
    variantes (ej. "ny yankees" -> "new_york_yankees").
    """

    def __init__(self, path: Path = TEAM_ALIASES_FILE):
        self.path = path
        self.data: Dict[str, Dict[str, str]] = load_json(path, {})

    def canonical_id(self, sport: str, name: str) -> str:
        norm = normalize_team_name(name)
        overrides = self.data.get(sport, {})
        return overrides.get(norm, norm)

    def add_alias(self, sport: str, name: str, canonical: str) -> None:
        norm = normalize_team_name(name)
        self.data.setdefault(sport, {})[norm] = normalize_team_name(canonical)
        save_json(self.path, self.data)

    def all_aliases(self, sport: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        if sport:
            return {sport: self.data.get(sport, {})}
        return self.data


# ============================================================
# Elo interno — modelo + pipeline de entrenamiento cronológico
# ============================================================
class EloModel:
    def __init__(self, path: Path = ELO_FILE):
        self.path = path
        self.state = load_json(path, {
            "schema_version": ELO_SCHEMA_VERSION,
            "ratings": {}, "brier": {}, "processed": {}, "draw_stats": {},
        })

    def schema_is_current(self) -> bool:
        return self.state.get("schema_version") == ELO_SCHEMA_VERSION

    @staticmethod
    def home_advantage(namespace: str) -> float:
        return ELO_HOME_BY_SPORT.get((namespace or "").split(":", 1)[0], 0.0)

    @staticmethod
    def probability(home_elo: float, away_elo: float, home_advantage: float = 0.0) -> float:
        return 1 / (1 + 10 ** ((away_elo - (home_elo + home_advantage)) / 400))

    @staticmethod
    def _draw_probability(draw_stats: Dict[str, Any]) -> float:
        total, draws = int(draw_stats.get("total", 0)), int(draw_stats.get("draws", 0))
        # Prior beta equivalente a 10 partidos con 26% de empates. La liga
        # domina progresivamente y los extremos quedan acotados.
        return min(0.40, max(0.08, (draws + 2.6) / (total + 10.0)))

    def evaluation_metrics(self, namespace: str) -> Dict[str, Any]:
        """Métricas walk-forward sobre una ventana reciente y un baseline.

        Solo entran observaciones generadas cuando ambos participantes ya
        tenían ELO_MIN_GAMES antes del partido. Para fútbol se usa Brier
        multiclase 1X2; para los demás, Brier binario.
        """
        full_hist = self.state.get("brier", {}).get(namespace, [])
        hist = full_hist[-BRIER_WINDOW:]
        if not hist:
            return {
                "kind": "multiclass" if namespace.startswith("soccer:") else "binary",
                "samples": 0, "total_samples": len(full_hist), "brier": None,
                "baseline_brier": None, "skill": None, "active": False,
                "reason": f"sin predicciones maduras (mínimo {BRIER_MIN})",
            }

        kind = "multiclass" if hist[0].get("kind") == "multiclass" else "binary"
        if kind == "multiclass":
            labels = ("home", "draw", "away")
            counts = {label: sum(1 for item in hist if item.get("y") == label) for label in labels}
            frequencies = {label: counts[label] / len(hist) for label in labels}
            model_scores, baseline_scores = [], []
            for item in hist:
                probs = item.get("probs") or {}
                outcome = item.get("y")
                model_scores.append(sum((float(probs.get(label, 0.0)) - (1.0 if outcome == label else 0.0)) ** 2 for label in labels))
                baseline_scores.append(sum((frequencies[label] - (1.0 if outcome == label else 0.0)) ** 2 for label in labels))
            brier = sum(model_scores) / len(model_scores)
            baseline = sum(baseline_scores) / len(baseline_scores)
        else:
            ys = [float(item["y"]) for item in hist]
            base_probability = sum(ys) / len(ys)
            brier = sum((float(item["p"]) - float(item["y"])) ** 2 for item in hist) / len(hist)
            baseline = sum((base_probability - y) ** 2 for y in ys) / len(ys)

        skill = (1.0 - brier / baseline) if baseline > 0 else None
        enough = len(hist) >= BRIER_MIN
        beats_baseline = skill is not None and skill >= BRIER_SKILL_MIN
        absolute_limit = BRIER_MULTICLASS_MAX if kind == "multiclass" else BRIER_MAX
        absolute_ok = brier <= absolute_limit
        # Schema 4: los dos caminos son alternativos. Esto evita rechazar un
        # Brier absoluto excelente solo porque el baseline de una competición
        # muy desigual también sea excepcionalmente bajo.
        active = enough and (beats_baseline or absolute_ok)
        if not enough:
            reason = f"{len(hist)}/{BRIER_MIN} predicciones maduras"
        elif absolute_ok and beats_baseline:
            reason = "calibración aprobada por Brier absoluto y skill"
        elif absolute_ok:
            reason = f"calibración aprobada por Brier absoluto <= {absolute_limit:.3f}"
        elif beats_baseline:
            reason = f"calibración aprobada por skill >= {BRIER_SKILL_MIN:.0%}"
        else:
            reason = (
                f"Brier {brier:.4f} > {absolute_limit:.3f} y skill "
                f"{skill if skill is not None else 'N/D'} < {BRIER_SKILL_MIN:.0%}"
            )
        return {
            "kind": kind, "samples": len(hist), "total_samples": len(full_hist),
            "brier": brier, "baseline_brier": baseline, "skill": skill,
            "absolute_limit": absolute_limit, "absolute_ok": absolute_ok,
            "beats_baseline": beats_baseline, "gate_mode": BRIER_GATE_MODE,
            "active": active, "reason": reason,
        }

    def probability_for(self, sport: str, home_id: str, away_id: str) -> Optional[float]:
        ratings = self.state.get("ratings", {}).get(sport, {})
        h, a = ratings.get(home_id), ratings.get(away_id)
        if not h or not a:
            return None
        if h.get("games", 0) < ELO_MIN_GAMES or a.get("games", 0) < ELO_MIN_GAMES:
            return None
        if not self.evaluation_metrics(sport)["active"]:
            return None
        return self.probability(float(h["elo"]), float(a["elo"]), self.home_advantage(sport))

    def probabilities_three_way(self, namespace: str, home_id: str, away_id: str) -> Optional[Dict[str, float]]:
        """Elo 1X2: fuerza relativa Elo + tasa de empate aprendida por liga."""
        p_home_binary = self.probability_for(namespace, home_id, away_id)
        if p_home_binary is None:
            return None
        stats = self.state.get("draw_stats", {}).get(namespace, {})
        if int(stats.get("total", 0)) < BRIER_MIN:
            return None
        p_draw = self._draw_probability(stats)
        remaining = 1.0 - p_draw
        return {
            "home": p_home_binary * remaining,
            "draw": p_draw,
            "away": (1.0 - p_home_binary) * remaining,
        }

    def update(self, sport: str, home_id: str, away_id: str, home_win: float, game_id: str) -> bool:
        """Genera la predicción con los ratings PREVIOS antes de actualizar
        (anti-fuga de información) y solo entonces mueve el Elo. Devuelve
        False si el game_id ya fue procesado (idempotente)."""
        processed = self.state.setdefault("processed", {}).setdefault(sport, [])
        if game_id in processed:
            return False
        ratings = self.state.setdefault("ratings", {}).setdefault(sport, {})
        h = ratings.setdefault(home_id, {"elo": ELO_INITIAL, "games": 0})
        a = ratings.setdefault(away_id, {"elo": ELO_INITIAL, "games": 0})
        mature_before_game = h.get("games", 0) >= ELO_MIN_GAMES and a.get("games", 0) >= ELO_MIN_GAMES
        p = self.probability(h["elo"], a["elo"], self.home_advantage(sport))
        draw_stats = self.state.setdefault("draw_stats", {}).setdefault(sport, {"total": 0, "draws": 0})
        if mature_before_game:
            history = self.state.setdefault("brier", {}).setdefault(sport, [])
            if sport.startswith("soccer:"):
                p_draw = self._draw_probability(draw_stats)
                remaining = 1.0 - p_draw
                outcome = "home" if home_win > 0.5 else ("away" if home_win < 0.5 else "draw")
                history.append({
                    "kind": "multiclass",
                    "probs": {"home": p * remaining, "draw": p_draw, "away": (1.0 - p) * remaining},
                    "y": outcome,
                })
            else:
                history.append({"kind": "binary", "p": p, "y": home_win})
            del history[: max(0, len(history) - 1000)]
        h["elo"] += ELO_K * (home_win - p)
        a["elo"] += ELO_K * ((1 - home_win) - (1 - p))
        h["games"] += 1
        a["games"] += 1
        draw_stats["total"] += 1
        if home_win == 0.5:
            draw_stats["draws"] += 1
        processed.append(game_id)
        return True

    def save(self):
        save_json(self.path, self.state)

    def coverage(self) -> Dict[str, Dict[str, Any]]:
        """Para el panel de salud: cuántos equipos por deporte ya tienen
        >= ELO_MIN_GAMES partidos (es decir, cuántos PODRÍAN usarse ya)."""
        out = {}
        for sport, ratings in self.state.get("ratings", {}).items():
            total = len(ratings)
            calibrados = sum(1 for r in ratings.values() if r.get("games", 0) >= ELO_MIN_GAMES)
            metrics = self.evaluation_metrics(sport)
            calibration_reason = metrics["reason"]
            if calibrados < 2:
                calibration_reason = f"solo {calibrados} participantes con >= {ELO_MIN_GAMES} partidos"
            out[sport] = {
                "equipos_totales": total,
                "equipos_calibrados": calibrados,
                "predicciones_brier": metrics["samples"],
                "predicciones_brier_totales": metrics["total_samples"],
                "ventana_brier": BRIER_WINDOW,
                "tipo_brier": metrics["kind"],
                "limite_brier_absoluto": metrics.get("absolute_limit"),
                "modo_gate_brier": BRIER_GATE_MODE,
                "brier": None if metrics["brier"] is None else round(metrics["brier"], 4),
                "baseline_brier": None if metrics["baseline_brier"] is None else round(metrics["baseline_brier"], 4),
                "brier_skill_score": None if metrics["skill"] is None else round(metrics["skill"], 4),
                "ventaja_local_elo": self.home_advantage(sport),
                "motivo_calibracion": calibration_reason,
                "modelo_activo": bool(calibrados >= 2 and metrics["active"]),
            }
        return out


def build_elo_model(event: NormalizedEvent, elo: EloModel, aliases: TeamAliasRegistry) -> Tuple[Dict[str, float], bool]:
    """Único punto de entrada para obtener 'el modelo' de un evento.
    A propósito NO tiene fallback a consenso de mercado: si el Elo no
    aplica o no está calibrado, se devuelve vacío y el evento se descarta
    aguas arriba (Regla 3 del prompt)."""
    namespace = elo_namespace(event.sport, event.league)
    if not elo.schema_is_current():
        return {}, False
    home_id = aliases.canonical_id(namespace, event.home)
    away_id = aliases.canonical_id(namespace, event.away)
    principal = next((m for m in event.markets if m.key in ("moneyline", "draw_no_bet")), None)
    has_draw = bool(principal and any(normalize_name(o.selection) in {"draw", "tie", "empate"} for o in principal.outcomes))
    if has_draw:
        probs = elo.probabilities_three_way(namespace, home_id, away_id)
        if not probs:
            return {}, False
        return {event.home: probs["home"], "Draw": probs["draw"], "Empate": probs["draw"], event.away: probs["away"]}, True
    p_home = elo.probability_for(namespace, home_id, away_id)
    if p_home is None:
        return {}, False
    return {event.home: p_home, event.away: 1 - p_home}, True


def market_consensus_info(stake_event: NormalizedEvent, bovada_event: Optional[NormalizedEvent]) -> Dict[str, float]:
    """SOLO informativo. Antes esta función (model_from_market_consensus)
    se usaba como 'segundo modelo' de respaldo cuando no había Elo — eso
    generaba un EV parcialmente circular (comparabas la cuota de Stake
    contra una probabilidad que ya incluía la propia cuota de Stake).
    Ahora se muestra en el reporte como contexto, pero build_elo_model()
    es la única fuente de P_modelo."""
    stake_p = devig(market_odds(stake_event))
    if not bovada_event:
        return stake_p
    bovada_p = devig(market_odds(bovada_event))
    combined = {}
    for n, sp in stake_p.items():
        ref_name = max(bovada_p, key=lambda x: name_similarity(n, x), default="")
        rp = bovada_p.get(ref_name) if name_similarity(n, ref_name) >= 0.5 else None
        vals = [v for v in (sp, rp) if v is not None]
        if vals:
            combined[n] = sum(vals) / len(vals)
    total = sum(combined.values())
    return {k: v / total for k, v in combined.items()} if total else stake_p


# ============================================================
# Resultados normalizados + entrenamiento del Elo
# ============================================================
REQUIRED_RESULT_FIELDS = ["sport", "league", "event_id", "start_time", "home_name", "away_name", "home_score", "away_score", "status"]


def validate_result(row: Dict[str, Any]) -> Optional[str]:
    for f in REQUIRED_RESULT_FIELDS:
        if row.get(f) in (None, ""):
            return f"falta '{f}'"
    if str(row.get("status")).lower() != "final":
        return "status no es 'final'"
    try:
        float(row["home_score"])
        float(row["away_score"])
    except Exception:
        return "marcador no numérico"
    if not parse_dt(row.get("start_time")):
        return "start_time inválido"
    return None


def load_results() -> List[Dict[str, Any]]:
    return load_json(RESULTS_FILE, [])


def save_results(rows: List[Dict[str, Any]]) -> None:
    save_json(RESULTS_FILE, rows)


def merge_results(new_rows: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Agrega resultados evitando duplicados por event_id. Nunca rellena
    campos faltantes: las filas inválidas se rechazan con motivo explícito
    (anti-fabricación aplicado también a los datos de entrenamiento)."""
    existing = load_results()
    seen_ids = {r.get("event_id") for r in existing}
    added = 0
    errors: List[str] = []
    for row in new_rows:
        err = validate_result(row)
        if err:
            errors.append(f"{row.get('event_id', '?')}: {err}")
            continue
        if row["event_id"] in seen_ids:
            continue
        existing.append(row)
        seen_ids.add(row["event_id"])
        added += 1
    save_results(existing)
    return added, errors


def parse_results_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def train_elo_from_results(elo: EloModel, aliases: TeamAliasRegistry, results: List[Dict[str, Any]]) -> int:
    """Entrena EN ORDEN CRONOLÓGICO (crítico: entrenar fuera de orden
    invalidaría tanto el Elo como el Brier histórico, porque se estarían
    generando 'predicciones' con ratings que en la realidad todavía no
    existían en ese momento — fuga de información)."""
    ordered = sorted(
        (r for r in results if parse_dt(r.get("start_time"))),
        key=lambda r: parse_dt(r["start_time"]),
    )
    updated = 0
    for r in ordered:
        sport = r["sport"]
        namespace = elo_namespace(sport, r.get("league", ""))
        home_id = aliases.canonical_id(namespace, r["home_name"])
        away_id = aliases.canonical_id(namespace, r["away_name"])
        hs, aw = float(r["home_score"]), float(r["away_score"])
        home_win = 1.0 if hs > aw else (0.5 if hs == aw else 0.0)
        if elo.update(namespace, home_id, away_id, home_win, r["event_id"]):
            updated += 1
    elo.save()
    return updated


# ============================================================
# Estado físico (tenis / MMA / boxeo) — gate obligatorio
# ============================================================
class PhysicalStatusRegistry:
    """No existe forma gratuita y fiable de automatizar dentro de la app la
    verificación de lesiones/forma reciente sin otra API de pago o scraping
    frágil de noticias. La solución honesta: la app muestra las fuentes
    relevantes y una persona registra la evidencia con fuente + timestamp.
    Sin un registro 'verified_ok' vigente (<72h) y confirmado, el evento
    se descarta SIEMPRE — sin importar el EV o la confianza."""

    def __init__(self, path: Path = PHYSICAL_STATUS_FILE):
        self.path = path
        self.data: Dict[str, Dict[str, Any]] = load_json(path, {})

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        return self.data.get(event_id)

    def is_verified_ok(self, event_id: str) -> Tuple[bool, str]:
        record = self.data.get(event_id)
        if not record:
            return False, "sin registro de estado físico"
        if record.get("status") not in PHYSICAL_STATUS_OK_VALUES:
            return False, f"estado registrado: {record.get('status')}"
        if not record.get("source_url"):
            return False, "registro sin fuente"
        if not record.get("confirmed_by_user"):
            return False, "registro no confirmado por el usuario"
        checked = parse_dt(record.get("checked_at"))
        if not checked:
            return False, "registro sin timestamp"
        age_h = (utc_now() - checked).total_seconds() / 3600.0
        if age_h > PHYSICAL_STATUS_MAX_AGE_HOURS:
            return False, f"registro vencido ({age_h:.0f}h, límite {PHYSICAL_STATUS_MAX_AGE_HOURS}h)"
        return True, "ok"

    def upsert(self, event_id: str, status: str, source_url: str, notes: str, confirmed_by_user: bool) -> None:
        self.data[event_id] = {
            "event_id": event_id,
            "checked_at": utc_now().isoformat(),
            "status": status,
            "source_url": source_url,
            "notes": notes,
            "confirmed_by_user": confirmed_by_user,
        }
        save_json(self.path, self.data)


# ============================================================
# Movimiento de línea real (historial acumulado, no solo el snapshot previo)
# ============================================================
def merge_movement_history(incoming: Dict[str, Any], path: Path = MOVEMENT_HISTORY_FILE) -> Dict[str, Any]:
    """Une historial remoto y local, deduplicando observaciones por tiempo/cuota."""
    current = load_json(path, {})
    if not isinstance(incoming, dict):
        return current
    for event_id, remote in incoming.items():
        if not isinstance(remote, dict):
            continue
        local = current.setdefault(event_id, {
            "home": remote.get("home", ""), "away": remote.get("away", ""), "observations": {}
        })
        local["home"] = remote.get("home") or local.get("home", "")
        local["away"] = remote.get("away") or local.get("away", "")
        for selection, observations in (remote.get("observations") or {}).items():
            if not isinstance(observations, list):
                continue
            merged = (local.setdefault("observations", {}).get(selection) or []) + observations
            unique = {(str(o.get("t")), o.get("odds")): o for o in merged if isinstance(o, dict)}
            local["observations"][selection] = sorted(unique.values(), key=lambda o: str(o.get("t", "")))[-MOVEMENT_MAX_HISTORY:]
    save_json(path, current)
    return current


def append_movement_history(events: List[NormalizedEvent], path: Path = MOVEMENT_HISTORY_FILE) -> Dict[str, Any]:
    history = load_json(path, {})
    now_iso = utc_now().isoformat()
    for e in events:
        odds = market_odds(e)
        if not odds:
            continue
        entry = history.setdefault(e.event_id, {"home": e.home, "away": e.away, "observations": {}})
        entry["home"], entry["away"] = e.home, e.away
        for sel, price in odds.items():
            obs = entry["observations"].setdefault(sel, [])
            if not obs or obs[-1]["odds"] != price:
                obs.append({"t": now_iso, "odds": price})
                del obs[: max(0, len(obs) - MOVEMENT_MAX_HISTORY)]
    save_json(path, history)
    return history


def movement_status(event: NormalizedEvent, selection: str, history: Dict[str, Any]) -> Dict[str, Any]:
    """Nunca premia movimiento con una sola observación. La interpretación
    de dirección (¿"sube" es señal de dinero sharp en contra, o del público
    alejándose?) es ambigua sin más contexto de flujo de apuestas, así que
    aquí solo se certifica que el movimiento está VERIFICADO (>=2
    observaciones, con la última lo bastante reciente según la cercanía al
    inicio) — no se asume que una dirección sea "buena" o "mala"."""
    entry = history.get(event.event_id, {})
    obs = (entry.get("observations") or {}).get(selection, [])
    if len(obs) < MOVEMENT_MIN_OBSERVATIONS:
        return {"verified": False, "reason": "insuficientes observaciones (<2)", "direction": None, "pct": None}

    first, last = obs[0], obs[-1]
    old, new = first["odds"], last["odds"]
    if not old:
        return {"verified": False, "reason": "cuota inicial inválida", "direction": None, "pct": None}
    pct = round((new / old - 1) * 100, 2)
    direction = "up" if new > old else ("down" if new < old else "flat")

    start = parse_dt(event.start_time)
    minutes_to_start = (start - utc_now()).total_seconds() / 60.0 if start else None
    last_t = parse_dt(last["t"])
    last_obs_age_min = (utc_now() - last_t).total_seconds() / 60.0 if last_t else None
    freshness_limit = 15.0 if (minutes_to_start is not None and minutes_to_start <= 120) else 180.0
    fresh_enough = last_obs_age_min is not None and last_obs_age_min <= freshness_limit

    return {
        "verified": fresh_enough,
        "reason": "ok" if fresh_enough else (
            f"última observación con {last_obs_age_min:.0f} min de antigüedad (límite {freshness_limit:.0f})"
            if last_obs_age_min is not None else "sin timestamp de observación"
        ),
        "direction": direction,
        "pct": pct,
        "observaciones": len(obs),
    }


def movements_summary(history: Dict[str, Any]) -> Dict[str, Any]:
    """Reemplaza a la antigua snapshot_market_movements(): antes comparaba
    solo contra el snapshot inmediatamente anterior (un archivo aparte,
    stake_snapshots.json); ahora se deriva del mismo historial acumulado
    que ya usa movement_status(), evitando mantener dos sistemas de
    snapshot en paralelo."""
    movements = []
    for event_id, entry in history.items():
        for sel, obs in (entry.get("observations") or {}).items():
            if len(obs) < 2:
                continue
            old, new = obs[0]["odds"], obs[-1]["odds"]
            if old == new:
                continue
            movements.append({
                "event_id": event_id,
                "match": f"{entry.get('home', '')} vs {entry.get('away', '')}",
                "selection": sel, "old": old, "new": new,
                "pct": round((new / old - 1) * 100, 2),
                "direction": "up" if new > old else "down",
            })
    return {"timestamp": utc_now().isoformat(), "movements": movements}


# ============================================================
# Frescura de mercado (aplicada también a la API en vivo)
# ============================================================
def market_freshness_ok(event: NormalizedEvent) -> Tuple[bool, str]:
    """Antes este principio (Regla 4) solo se aplicaba al snapshot remoto
    de respaldo (>60 min = vencido). La consulta directa a la API no tenía
    ningún filtro de frescura. Ahora aplica siempre, con un límite que se
    endurece mientras más cerca esté el evento de comenzar."""
    last = parse_dt(event.last_update)
    if not last:
        return False, "sin timestamp de actualización"
    age_min = (utc_now() - last).total_seconds() / 60.0
    start = parse_dt(event.start_time)
    if start:
        minutes_to_start = (start - utc_now()).total_seconds() / 60.0
        limit = 10.0 if minutes_to_start <= 60 else (30.0 if minutes_to_start <= 360 else 120.0)
    else:
        limit = 60.0
    if age_min > limit:
        return False, f"cuota con {age_min:.0f} min de antigüedad (límite {limit:.0f} min a esta distancia del inicio)"
    return True, "ok"


# ============================================================
# Liquidez real (antes: bool(bovada_event) nada más)
# ============================================================
def liquidity_status(bovada_event: Optional[NormalizedEvent], match_score: float) -> Tuple[bool, str]:
    if not bovada_event:
        return False, "sin referencia secundaria disponible (Bovada)"
    if match_score < LIQUIDITY_MIN_MATCH_SCORE:
        return False, f"coincidencia de evento débil ({match_score:.2f} < {LIQUIDITY_MIN_MATCH_SCORE})"
    last = parse_dt(bovada_event.last_update)
    if not last:
        return False, "referencia secundaria sin timestamp"
    age_min = (utc_now() - last).total_seconds() / 60.0
    if age_min > LIQUIDITY_MAX_AGE_MIN:
        return False, f"referencia secundaria desactualizada ({age_min:.0f} min)"
    return True, "ok"


# ============================================================
# Promociones
# ============================================================
def load_promotions() -> List[Dict[str, Any]]:
    return load_json(PROMOTIONS_FILE, [])


def load_public_promotions(active_only: bool = False) -> List[Dict[str, Any]]:
    """Carga el catálogo versionado sin asumir elegibilidad ni alterar el EV."""
    items = load_json(PUBLIC_PROMOTIONS_FILE, [])
    if not isinstance(items, list):
        return []
    valid = [p for p in items if isinstance(p, dict) and p.get("id") and p.get("name")]
    return [p for p in valid if public_promotion_status(p) == "activa"] if active_only else valid


def public_promotion_status(promotion: Dict[str, Any]) -> str:
    today = utc_now().date()
    try:
        starts = datetime.fromisoformat(str(promotion.get("starts_on", ""))).date()
    except ValueError:
        starts = None
    try:
        ends = datetime.fromisoformat(str(promotion.get("ends_on", ""))).date()
    except ValueError:
        ends = None
    if starts and today < starts:
        return "próxima"
    if ends and today > ends:
        return "vencida"
    return "activa"


def save_promotions(items: List[Dict[str, Any]]) -> None:
    save_json(PROMOTIONS_FILE, items)


def effective_odds(base_odds: float, promotion: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    if not promotion:
        return base_odds, "sin promoción"
    if promotion.get("type") == "odds_boost":
        boost = float(promotion.get("boost_percent", 0))
        return round(1.0 + (base_odds - 1.0) * (1 + boost / 100), 4), f"Odds Boost +{boost:.1f}%"
    return base_odds, str(promotion.get("name", "promoción no modelada"))


def promo_expected_value(prob: float, base_odds: float, promotion: Optional[Dict[str, Any]]) -> Tuple[float, float, str]:
    if not promotion:
        return base_odds, ev_decimal(prob, base_odds), "sin promoción"
    kind = promotion.get("type")
    if kind == "odds_boost":
        odds, label = effective_odds(base_odds, promotion)
        return odds, ev_decimal(prob, odds), label
    if kind in ("refund", "insurance"):
        refund_fraction = float(promotion.get("refund_fraction", 1.0))
        trigger_prob = float(promotion.get("trigger_probability", 0.0))
        p_loss = 1 - prob
        ev = prob * (base_odds - 1) + p_loss * trigger_prob * refund_fraction - p_loss
        return base_odds, ev, f"{promotion.get('name', 'Refund/Insurance')}"
    return base_odds, ev_decimal(prob, base_odds), "promoción no modelada; EV base"


def select_promotion(
    event: NormalizedEvent, selection: str, market_key: str, promotions: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Ahora valida sport y mercado, además de event_id/selection, y exige
    'confirmed_eligible' explícito — antes bastaba con que existiera la
    promoción y coincidiera event_id/selection (opcionales), lo que
    permitía aplicarla casi globalmente sin que el usuario confirmara
    elegibilidad real para su cuenta."""
    now = utc_now()
    for p in promotions:
        if not p.get("enabled", True):
            continue
        if p.get("informational_only") or p.get("ev_enabled") is False:
            continue  # el catálogo público nunca altera el EV por sí solo
        if not p.get("confirmed_eligible", False):
            continue  # Regla 9 del prompt: nunca asumir elegibilidad universal
        exp = parse_dt(p.get("expires_at"))
        if exp and exp < now:
            continue
        if p.get("event_id") and p["event_id"] != event.event_id:
            continue
        if p.get("sport") and p["sport"] != event.sport:
            continue
        if p.get("market") and p["market"] != market_key:
            continue
        if p.get("selection") and normalize_name(p["selection"]) != normalize_name(selection):
            continue
        if p.get("bet_type", "single") != "single":
            continue  # Blindado solo opera apuestas simples
        return p
    return None


# ============================================================
# Motor Blindado con gates obligatorios
# ============================================================
@dataclass
class Candidate:
    event: NormalizedEvent
    selection: str
    stake_odds: float
    model_prob: float
    reference_prob: Optional[float]
    ev: float
    confidence: float
    promotion: Optional[Dict[str, Any]]
    effective_odds: float
    reason: str
    movement: Dict[str, Any] = field(default_factory=dict)


class BlindadoEngine:
    MIN_ODDS = 1.40
    MAX_ODDS = 2.00
    MIN_EV = 0.04
    MAX_DIVERGENCE = 0.09
    MIN_CONFIDENCE = 8.0

    def __init__(self, bankroll: float = 100.0):
        self.bankroll = bankroll

    def _confidence(self, ev: float, ref_p: float, model_prob: float, movement: Dict[str, Any]) -> float:
        """La confianza YA NO compensa gates faltantes — esos se verifican
        antes en evaluate_event() y descartan el evento sin llegar aquí.
        Este score solo sirve para ORDENAR entre candidatos que ya
        pasaron todos los gates obligatorios."""
        score = 5.0
        if ev >= 0.08:
            score += 2
        elif ev >= 0.04:
            score += 1
        div = abs(model_prob - ref_p)
        if div <= 0.03:
            score += 1
        elif div > 0.07:
            score -= 1
        if movement.get("verified"):
            score += 0.5
        return max(0.0, min(10.0, score))

    def evaluate_event(
        self,
        event: NormalizedEvent,
        model_probs: Dict[str, float],
        model_active: bool,
        bovada_event: Optional[NormalizedEvent],
        bovada_score: float,
        promotions: List[Dict[str, Any]],
        physical_registry: PhysicalStatusRegistry,
        movement_history: Dict[str, Any],
    ) -> Tuple[List[Candidate], Dict[str, str]]:
        reasons: Dict[str, str] = {}
        market = next((m for m in event.markets if m.key in ("moneyline", "draw_no_bet")), None)
        if not market:
            reasons["sin_mercado"] = "sin mercado moneyline/DNB"
            return [], reasons

        # --- Gate 1: modelo calibrado (Elo, única fuente válida) ---
        if not model_active or not model_probs:
            reasons["sin_modelo"] = "sin modelo estadístico independiente calibrado (Elo no disponible)"
            return [], reasons

        # --- Gate 2: frescura de mercado ---
        fresh_ok, fresh_reason = market_freshness_ok(event)
        if not fresh_ok:
            reasons["frescura"] = fresh_reason
            return [], reasons

        # --- Gate 3: liquidez real (evento emparejado en Bovada, fresco) ---
        liquid_ok, liquid_reason = liquidity_status(bovada_event, bovada_score)
        if not liquid_ok:
            reasons["liquidez"] = liquid_reason
            return [], reasons

        # --- Gate 4: estado físico (solo tenis/MMA/boxeo) ---
        if event.sport in PHYSICAL_STATUS_SPORTS:
            physical_ok, physical_reason = physical_registry.is_verified_ok(event.event_id)
            if not physical_ok:
                reasons["estado_fisico"] = physical_reason
                return [], reasons

        ref_probs = devig(market_odds(bovada_event))

        out: List[Candidate] = []
        for o in market.outcomes:
            if not o.active or not (self.MIN_ODDS <= o.odds <= self.MAX_ODDS):
                continue
            p = model_probs.get(o.selection)
            if p is None:
                if normalize_name(o.selection) in {"draw", "tie", "empate", "x"}:
                    p = model_probs.get("Draw") or model_probs.get("Empate")
            if p is None:
                model_name = max(model_probs, key=lambda n: name_similarity(o.selection, n), default="")
                p = model_probs.get(model_name) if name_similarity(o.selection, model_name) >= 0.5 else None
            if p is None or not (0 < p < 1):
                continue

            promo = select_promotion(event, o.selection, market.key, promotions)
            eff_odds, ev, promo_label = promo_expected_value(p, o.odds, promo)
            if not (self.MIN_ODDS <= eff_odds <= self.MAX_ODDS):
                continue
            if ev < self.MIN_EV:
                continue

            ref_name = max(ref_probs, key=lambda x: name_similarity(o.selection, x), default="")
            ref_p = ref_probs.get(ref_name) if name_similarity(o.selection, ref_name) >= 0.5 else None
            if ref_p is None:
                # El evento pasó liquidez (existe Bovada emparejado), pero
                # esta selección puntual no tiene contraparte identificable
                # -> no se puede validar divergencia (Regla 6): se descarta.
                continue
            if abs(p - ref_p) > self.MAX_DIVERGENCE:
                continue

            mv = movement_status(event, o.selection, movement_history)
            conf = self._confidence(ev, ref_p, p, mv)
            if conf < self.MIN_CONFIDENCE:
                continue

            out.append(Candidate(
                event=event, selection=o.selection, stake_odds=o.odds,
                model_prob=p, reference_prob=ref_p, ev=ev, confidence=conf,
                promotion=promo, effective_odds=eff_odds, reason=promo_label,
                movement=mv,
            ))
        if not out:
            reasons["ev_confianza_divergencia"] = "ningún outcome superó EV/confianza/divergencia"
        return out, reasons

    def choose_one(self, candidates: List[Candidate]) -> Optional[Candidate]:
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: (c.confidence, c.ev), reverse=True)[0]


def prepare_candidates(
    stake_events: List[NormalizedEvent],
    bovada_events: List[NormalizedEvent],
    promotions: List[Dict[str, Any]],
    bankroll: float,
) -> Tuple[List[Candidate], Dict[str, Any]]:
    engine = BlindadoEngine(bankroll)
    elo = EloModel()
    aliases = TeamAliasRegistry()
    physical_registry = PhysicalStatusRegistry()
    movement_history = append_movement_history(stake_events)

    candidates: List[Candidate] = []
    audit: Dict[str, Any] = {"stake_events": len(stake_events), "descartes": {}, "qualified": 0}

    def bump(reason: str):
        audit["descartes"][reason] = audit["descartes"].get(reason, 0) + 1

    for e in stake_events:
        start = parse_dt(e.start_time)
        if not start or start <= utc_now() or e.is_live:
            bump("en_vivo_o_sin_hora_valida")
            continue

        capable, _ = pick_capability(e)
        if not capable:
            bump("deporte_o_liga_no_compatible")
            continue

        if sport_family(e) == "soccer":
            odds = market_odds(e)
            p_draw = next((p for n, p in devig(odds).items() if normalize_name(n) == "draw"), None)
            if p_draw is not None and p_draw >= 0.30 and not any(m.key == "draw_no_bet" for m in e.markets):
                bump("riesgo_empate_sin_dnb")
                continue

        model, model_active = build_elo_model(e, elo, aliases)
        bov, score = match_event_scored(e, bovada_events)

        cs, reasons = engine.evaluate_event(
            e, model, model_active, bov, score, promotions, physical_registry, movement_history,
        )
        for reason_key in reasons:
            bump(reason_key)
        candidates.extend(cs)

    audit["qualified"] = len(candidates)
    audit["elo_coverage"] = elo.coverage()
    return candidates, audit


def candidate_report(c: Candidate, bankroll: float) -> Dict[str, Any]:
    stake = stake_amount(bankroll, c.model_prob, c.effective_odds)
    return {
        "PICK": f"{c.event.home} vs {c.event.away}",
        "Mercado": c.selection,
        "Stake odds": round(c.stake_odds, 3),
        "Odds efectivas": round(c.effective_odds, 3),
        "Prob. modelo (Elo)": round(c.model_prob * 100, 2),
        "Prob. referencia (Bovada)": None if c.reference_prob is None else round(c.reference_prob * 100, 2),
        "EV %": round(c.ev * 100, 2),
        "Confianza": round(c.confidence, 1),
        "Movimiento verificado": c.movement.get("verified", False),
        "Stake sugerido": stake,
        "Promoción": c.reason,
        "Fuente mercado": "Stake",
        "Modelo estadístico": "Elo interno calibrado (única fuente válida)",
        "Referencia": "Bovada de-vigged",
    }


def build_prompt(events: List[NormalizedEvent], movements: Dict[str, Any]) -> str:
    payload = [event_to_dict(e) for e in events]
    return (
        BLINDADO_PROMPT
        + "\n\nDATOS DE STAKE:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\nMOVIMIENTOS:\n" + json.dumps(movements, ensure_ascii=False, indent=2)
    )


def init_promotions_file():
    if not PROMOTIONS_FILE.exists():
        save_promotions([])
